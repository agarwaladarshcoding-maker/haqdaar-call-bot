"""Live presentation wording: given schemes that ALREADY survived narrow()
(deterministic SQL matching, unchanged), optionally ask an LLM to pick the
best short name and the single most relevant sentence to speak for each
one, given the caller's actual answers as context. Step 9 (wording half).

This module never decides WHICH schemes qualify - narrow.py already did
that before anything here runs. This module only decides HOW to say the
name and benefit for schemes that already qualified, same separation of
concerns as select.py picking a QUESTION (never eligibility) via LLM.

K2/K3 discipline (TEST_CASES.md: any benefit/amount spoken must match the
DB verbatim): the LLM is not asked to summarize or paraphrase benefit
text. It is asked to select which existing sentence(s) of the scheme's own
`benefits` column are most relevant to this caller, returned character-
for-character. Every returned "benefit_line" is verified as an exact
substring of the scheme's own DB text before use - if it is not, that
scheme's presentation falls back to a safe deterministic default (never
the LLM's words, never blocking the call).

Fallback discipline mirrors select.py exactly: no API key, timeout,
malformed response, or a failed verbatim check all fall back to a
deterministic default (name_short_hi/scheme_name for the name, the first
sentence of `benefits` for the line) - the call always completes, this
module can never make presenting worse than doing nothing.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from haqdaar import config
from haqdaar.narrow import Candidate

logger = logging.getLogger("haqdaar.present")

PresentCaller = Callable[[str, str], str]

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Presentation:
    slug: str
    spoken_name: str
    benefit_line: str
    source: str  # "llm" or "fallback" - for tests/observability only, never spoken


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return parts[0] if parts else text.strip()


def _fallback_presentation(candidate: Candidate, benefits_text: str) -> Presentation:
    name = candidate.name_short_hi or candidate.scheme_name
    line = candidate.benefit_one_line or _first_sentence(benefits_text)
    return Presentation(slug=candidate.slug, spoken_name=name, benefit_line=line, source="fallback")


def _default_caller(system: str, user: str) -> str:
    from haqdaar import llm as llm_module

    return llm_module.chat(system, user, json_mode=True)


_SYSTEM_PROMPT = """You help present already-matched Indian government scheme results to a caller on a phone call. You are NOT deciding eligibility - that is already done. Your only job: pick a short spoken name and select ONE existing sentence from the scheme's own benefits text that is most relevant to this caller.

Rules:
1. "spoken_name": a short name for the scheme (under 8 words), based on the scheme's real name. You may shorten/simplify wording, but do not invent a different scheme.
2. "benefit_line": you MUST copy an existing sentence (or short verbatim span) FROM THE GIVEN BENEFITS TEXT, character for character. Do not summarize, do not paraphrase, do not add or remove numbers. If multiple sentences are relevant, pick the single best one, not a blend.
3. If the benefits text has no sentence that clearly stands alone, copy the whole first sentence verbatim instead of inventing a shorter one.

Return JSON: {"spoken_name": "...", "benefit_line": "..."}"""


def _build_user_prompt(candidate: Candidate, benefits_text: str, answers: dict[str, Any]) -> str:
    return (
        f"Scheme name: {candidate.scheme_name}\n\n"
        f"Benefits text (verbatim source - your benefit_line MUST be an exact substring of this):\n{benefits_text}\n\n"
        f"Caller's answers so far (for relevance only, do not mention these in your output): "
        f"{json.dumps({k: v for k, v in answers.items() if not k.startswith('_')})}"
    )


def _parse_response(raw: str) -> tuple[str, str] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("spoken_name")
    line = data.get("benefit_line")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(line, str) or not line.strip():
        return None
    return name.strip(), line.strip()


def present_one(
    candidate: Candidate,
    benefits_text: str,
    answers: dict[str, Any],
    llm_enabled: bool = True,
    llm_caller: PresentCaller | None = None,
) -> Presentation:
    """Returns a Presentation for one already-matched scheme. Never raises -
    every failure mode (disabled, no key, timeout, malformed, verbatim
    check failed) falls back to a deterministic, always-safe default."""
    if not llm_enabled or not config.LLM_API_KEY:
        return _fallback_presentation(candidate, benefits_text)

    caller = llm_caller or _default_caller
    budget_s = config.LLM_TIMEOUT_MS / 1000.0
    system = _SYSTEM_PROMPT
    user = _build_user_prompt(candidate, benefits_text, answers)

    start = time.monotonic()
    try:
        raw = caller(system, user)
    except Exception as e:  # noqa: BLE001 - any LLM failure is a fallback, never a crash
        logger.info("present: LLM call raised %s for %s, falling back", type(e).__name__, candidate.slug)
        return _fallback_presentation(candidate, benefits_text)
    elapsed = time.monotonic() - start

    if elapsed > budget_s:
        logger.info("present: LLM took %.3fs > %.3fs budget for %s, falling back", elapsed, budget_s, candidate.slug)
        return _fallback_presentation(candidate, benefits_text)

    parsed = _parse_response(raw)
    if parsed is None:
        logger.info("present: malformed LLM response for %s, falling back", candidate.slug)
        return _fallback_presentation(candidate, benefits_text)

    name, line = parsed

    # K2/K3 GATE: benefit_line must be a verbatim substring of the real
    # benefits text. Any deviation - paraphrase, invented amount, dropped
    # number - fails this check and falls back. This is the mechanical
    # guardrail, not a trust exercise.
    if line not in benefits_text:
        logger.info("present: benefit_line failed verbatim check for %s, falling back", candidate.slug)
        return _fallback_presentation(candidate, benefits_text)

    return Presentation(slug=candidate.slug, spoken_name=name, benefit_line=line, source="llm")


def present_many(
    candidates: list[Candidate],
    benefits_by_slug: dict[str, str],
    answers: dict[str, Any],
    llm_enabled: bool = True,
    llm_caller: PresentCaller | None = None,
    budget_s: float | None = None,
) -> list[Presentation]:
    """Presents each candidate independently - one candidate's LLM failure
    never affects another's presentation.

    WALL-CLOCK BUDGET (`budget_s`, default config.PRESENT_BUDGET_MS): this
    runs inside a Twilio webhook, and Twilio hangs up if we don't answer
    within 15 seconds. Five candidates x a 3s LLM timeout each is 15s of
    LLM time alone, before any TTS - i.e. the results turn, the one the
    whole call builds up to, was the single most likely turn to drop the
    call. Once the budget is spent, the remaining candidates skip the LLM
    entirely and use the deterministic fallback, which is always safe (it
    reads name + first benefit sentence straight from the DB).

    The budget is checked BEFORE each call rather than enforced across
    them, so worst case is budget + one LLM timeout - bounded and well
    inside Twilio's limit, without needing to cancel an in-flight call."""
    if budget_s is None:
        budget_s = config.PRESENT_BUDGET_MS / 1000.0

    out: list[Presentation] = []
    start = time.monotonic()
    for c in candidates:
        benefits_text = benefits_by_slug.get(c.slug, "")
        if llm_enabled and (time.monotonic() - start) >= budget_s:
            logger.info("present: %.1fs budget spent, falling back for %s and the rest", budget_s, c.slug)
            out.append(_fallback_presentation(c, benefits_text))
            continue
        out.append(
            present_one(c, benefits_text, answers, llm_enabled=llm_enabled, llm_caller=llm_caller)
        )
    return out
