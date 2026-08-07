"""Question selector: pick_question(answers, candidates, bank) -> Question. Step 4.

Two layers, in order:

1. Deterministic information-gain ranking over bank.askable(answers). For
   each askable question, simulate every DTMF option's effect on the
   candidate set via narrow() and take the WORST case (largest surviving
   set) - a question is only as good as its least-informative answer.
   Lower worst-case survivor count wins; ties break on the question's
   static `gain` field (declared 1-10 in question_bank.yaml), then on
   question id for full determinism (J-series implies same input -> same
   output; nothing here reads a clock or random source).

2. Optional LLM tie-break over the top 5 by that ranking. The LLM sees
   ONLY question ids + `why` text - never scheme names, never SQL, never
   candidate contents (PRD/SYSTEM_DESIGN.md: the LLM must not be able to
   leak eligibility logic or scheme identity). It must return exactly one
   of the 5 ids. Every failure mode falls back to rank 1 (index 0 of the
   top-5 list), never raises:
     J1 no API key            -> rank 1
     J2 slower than budget_ms -> rank 1
     J3 id not in shortlist   -> rank 1
     J4 malformed response    -> rank 1
     J5 already-asked id      -> rank 1 ("already asked" can't happen from
                                  askable()'s own filtering, but the LLM
                                  is untrusted output and is re-checked
                                  anyway - defense in depth)
     J6 disabled by caller    -> rank 1, LLM not called at all

J6 (disable the LLM for the rest of a call after 3 errors) is per-call
state that belongs to engine.py's state, not to this pure function. The
`llm_enabled` parameter lets the caller (engine.py, Step 5) pass that
decision in without select.py needing to track anything itself.

J7 (GATE): the LLM's raw output is only ever compared against a fixed set
of known-safe ids and then discarded - it is never returned, logged as
user-facing text, or placed into any `actions` payload. Only a `Question`
already present in the bank is ever returned.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from haqdaar import config
from haqdaar.bank import Bank, Question
from haqdaar.narrow import narrow

logger = logging.getLogger("haqdaar.select")

LLMCaller = Callable[[str, list[str]], str]


def _worst_case_survivors(question: Question, answers: dict[str, Any], db_path: str | None) -> int:
    """Largest candidate-set size across every DTMF option this question
    could produce. A question is only as informative as its least
    informative answer, so we rank on the worst case, not the average."""
    options = question.get("dtmf") or {}
    if not options:
        # Speech-only question: nothing to simulate branch-by-branch, so it
        # cannot be scored by worst-case narrowing. Treat as maximally
        # uninformative (never eliminates via this path) so DTMF questions
        # with real branches are preferred when any exist.
        return len(narrow(answers, db_path))

    writes = question.raw["writes"]
    worst = 0
    for opt in options.values():
        value = (opt.get("set") or {}).get(writes)
        if value is None:
            # A malformed/atypical option with no `set` for this question's
            # own attribute can't be simulated - skip it rather than crash;
            # _validate() in bank.py is the place that should catch this at
            # load time, not the selector at call time.
            continue
        simulated = {**answers, writes: value}
        worst = max(worst, len(narrow(simulated, db_path)))
    return worst


def _rank(
    askable: list[Question], answers: dict[str, Any], db_path: str | None
) -> list[Question]:
    scored = [
        (_worst_case_survivors(q, answers, db_path), -q.get("gain", 0), q.id, q)
        for q in askable
    ]
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [q for _, _, _, q in scored]


def _ask_llm(caller: LLMCaller, shortlist: list[Question], answers: dict[str, Any]) -> str | None:
    """Calls `caller(prompt, ids)` and returns the raw text response, or
    None on any exception. The caller never receives scheme names, SQL, or
    candidate contents - only ids and each question's `why`."""
    payload = {
        "answers_so_far": answers,
        "options": [{"id": q.id, "why": q.get("why", "")} for q in shortlist],
    }
    try:
        return caller(json.dumps(payload), [q.id for q in shortlist])
    except Exception as e:  # noqa: BLE001 - any LLM failure is a fallback, never a crash
        logger.info("select: LLM call raised %s, falling back to rank 1", type(e).__name__)
        return None


def pick_question(
    answers: dict[str, Any],
    candidates: list[Any],
    bank: Bank,
    db_path: str | None = None,
    llm_enabled: bool = True,
    llm_caller: LLMCaller | None = None,
) -> Question | None:
    """Returns the next Question to ask, or None if nothing is askable
    (caller must handle this - I6: never assume askable() is non-empty)."""
    askable = bank.askable(answers)
    if not askable:
        return None

    ranked = _rank(askable, answers, db_path)
    shortlist = ranked[:5]
    rank1 = shortlist[0]

    if not llm_enabled or not config.LLM_API_KEY:
        logger.info("select: LLM disabled or no API key, using rank 1 (%s)", rank1.id)
        return rank1

    caller = llm_caller or _default_llm_caller
    budget_s = config.LLM_TIMEOUT_MS / 1000.0
    already_asked = set(answers.keys())

    start = time.monotonic()
    raw = _ask_llm(caller, shortlist, answers)
    elapsed = time.monotonic() - start

    if raw is None:
        return rank1
    if elapsed > budget_s:
        logger.info("select: LLM took %.3fs > %.3fs budget, using rank 1", elapsed, budget_s)
        return rank1

    chosen_id = _parse_llm_response(raw)
    if chosen_id is None:
        logger.info("select: LLM response malformed, using rank 1")
        return rank1

    shortlist_ids = {q.id for q in shortlist}
    if chosen_id not in shortlist_ids:
        logger.info("select: LLM chose id %r not in shortlist, using rank 1", chosen_id)
        return rank1

    writes = bank.question(chosen_id).raw["writes"]
    if writes in already_asked:
        logger.info("select: LLM chose already-answered id %r, using rank 1", chosen_id)
        return rank1

    return bank.question(chosen_id)


def _parse_llm_response(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        qid = data.get("id")
        return qid if isinstance(qid, str) else None
    return None


def _default_llm_caller(prompt: str, ids: list[str]) -> str:
    """No LLM wired up yet (Step 4 scope is the selector's fallback
    discipline, not a specific provider integration) - always behaves like
    a missing key. Real callers pass `llm_caller` explicitly."""
    raise RuntimeError("no LLM caller configured")
