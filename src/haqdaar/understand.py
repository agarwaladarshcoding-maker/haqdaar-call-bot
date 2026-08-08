"""Turning what a caller SAID into what the engine can USE. Step 3.

Two jobs, both "free speech in, closed vocabulary out", both gated the
same way:

  extract_answers()  "main kisan hoon, thodi zameen hai"
                     -> {"persona": "farmer", "owns_land": "yes"}
                     so the question loop can start from what they already
                     told us instead of asking it back.

  match_scheme()     "PMEGP ke bare mein bataiye"
                     -> a scheme slug, or None meaning "not in our
                     catalogue" - which is a real answer, not a retry.

THE GATE (the reason this module is safe to put an LLM behind)
Neither function returns anything the LLM wrote. Both return values
chosen from a fixed set built at call time from the question bank and the
live candidate list, and anything outside that set is dropped, not
repaired. So:

  - extract_answers can only ever produce an (attribute, value) pair that
    some keypress on some question could ALSO have produced, because the
    vocabulary is built from the questions' own `dtmf` option `set:`
    blocks (45 of the bank's 46 questions declare theirs there). A
    hallucinated attribute, a plausible-but-undeclared value, a value
    belonging to a different attribute - all dropped.
  - match_scheme can only ever return a slug that was already in the
    candidate list handed to it, or None.

This is select.py's J7 discipline applied to a second call site: LLM
output is only ever compared against a known-safe set and then discarded.
Nothing here decides eligibility - narrow() still does that, from the
answers this produces.

FALLBACK DISCIPLINE, same as select.py and present.py: no API key, a
timeout, a malformed response, or an empty result all return "I learned
nothing" ({} / None). The caller then just asks its questions the normal
way, so an LLM outage degrades the call to the plain DTMF menus rather
than breaking it.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from haqdaar import config
from haqdaar.bank import Bank

logger = logging.getLogger("haqdaar.understand")

LLMCaller = Callable[[str, str], str]

# Attributes the caller can never usefully state in an opening sentence,
# excluded from the vocabulary so the LLM is not invited to guess at them.
# `intent` is decided by the menu they just answered; `_`-prefixed ones are
# internal bookkeeping, not facts about the caller.
_NOT_EXTRACTABLE = {"intent", "language", "on_behalf"}


def _default_caller(system: str, user: str) -> str:
    from haqdaar import llm as llm_module

    return llm_module.chat(system, user, json_mode=True)


# ---------------------------------------------------------------------------
# Vocabulary: built from the bank, so it can never drift from the engine.
# ---------------------------------------------------------------------------
def build_vocabulary(bank: Bank) -> dict[str, dict[str, str]]:
    """{attribute: {value: human label}} for every attribute a question can
    write via a keypress.

    Derived from the bank rather than hardcoded in a prompt string: a new
    question, a renamed value, or a deleted option changes what the
    extractor is allowed to say WITHOUT anyone remembering to edit a
    prompt. The labels come from the same `dtmf` entries the caller would
    have heard read aloud, which is exactly the wording that makes sense
    to match their speech against."""
    vocab: dict[str, dict[str, str]] = {}
    for qid in bank._order:
        q = bank.question(qid)
        attr = q.raw.get("writes")
        if not attr or attr.startswith("_") or attr in _NOT_EXTRACTABLE:
            continue
        for opt in (q.get("dtmf") or {}).values():
            value = (opt.get("set") or {}).get(attr)
            if value is None:
                continue
            vocab.setdefault(attr, {})[str(value)] = opt.get("en") or opt.get("hi") or str(value)
    return vocab


_EXTRACT_SYSTEM = """You extract structured facts from what a caller said to an Indian government-scheme helpline.

The caller spoke one sentence about what they need. Your job is to fill in ONLY the attributes they clearly stated or unambiguously implied, using ONLY the exact attribute names and values listed below.

RULES
1. Use only attribute names and values from the ALLOWED list. Never invent either. If something they said has no matching value, leave that attribute out.
2. Only include an attribute if the caller actually indicated it. Do NOT guess, do NOT infer from stereotypes, do NOT fill in a "likely" default. Leaving an attribute out is always correct and costs nothing - the helpline will simply ask about it.
3. The caller may speak Hindi, English, or a mix, in Roman script. "main kisan hoon" = farmer. "meri dukaan hai" = business. "mujhe loan chahiye" is a need, not an occupation - do not turn it into one.
4. Do not decide whether they qualify for anything. You are only recording what they said.

Return JSON: {"attributes": {"attribute_name": "value", ...}}
Return {"attributes": {}} if they stated nothing matchable."""


def extract_answers(
    utterance: str,
    bank: Bank,
    llm_enabled: bool = True,
    llm_caller: LLMCaller | None = None,
) -> dict[str, Any]:
    """Free speech -> engine answers. Returns {} when it learns nothing,
    which is always a safe outcome: the question loop then simply asks."""
    if not utterance or not utterance.strip():
        return {}
    if not llm_enabled or not config.LLM_API_KEY:
        return {}

    vocab = build_vocabulary(bank)
    if not vocab:
        return {}

    allowed = "\n".join(
        f"- {attr}: " + ", ".join(f'"{v}" ({label})' for v, label in values.items())
        for attr, values in sorted(vocab.items())
    )
    user = f"ALLOWED attributes and values:\n{allowed}\n\nThe caller said: \"{utterance.strip()}\""

    caller = llm_caller or _default_caller
    budget_s = config.UNDERSTAND_TIMEOUT_MS / 1000.0
    start = time.monotonic()
    try:
        raw = caller(_EXTRACT_SYSTEM, user)
    except Exception as e:  # noqa: BLE001 - any LLM failure is a fallback, never a crash
        logger.info("understand: extract raised %s, learning nothing", type(e).__name__)
        return {}
    if time.monotonic() - start > budget_s:
        logger.info("understand: extract exceeded %.1fs budget, learning nothing", budget_s)
        return {}

    try:
        data = json.loads(raw)
        proposed = data.get("attributes") if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        logger.info("understand: extract returned malformed JSON, learning nothing")
        return {}
    if not isinstance(proposed, dict):
        return {}

    # THE GATE. Every pair must exist in the bank's own vocabulary; nothing
    # is coerced, corrected, or case-fixed into validity.
    out: dict[str, Any] = {}
    for attr, value in proposed.items():
        if attr in vocab and isinstance(value, str) and value in vocab[attr]:
            out[attr] = value
        else:
            logger.info("understand: dropped un-declared pair %r=%r", attr, value)
    return out


# ---------------------------------------------------------------------------
# Scheme-name matching.
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "the", "of", "and", "in", "on", "for", "to", "a", "an", "under", "scheme",
    "schemes", "yojana", "yojna", "ke", "ki", "ka", "mein", "bare", "batao",
    "bataiye", "chahiye", "hai", "hoon", "mujhe", "about", "tell", "me",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOPWORDS}


def shortlist_schemes(spoken: str, candidates: list[Any], limit: int = 6) -> list[Any]:
    """Cheap, deterministic first pass: rank candidates by how many
    meaningful words they share with what the caller said.

    Deliberately generous - its job is to not LOSE the right scheme, not
    to pick it. Picking is the LLM's job, over a list short enough that it
    cannot wander. Runs with no API key at all, which is what makes the
    no-LLM path still able to match an obvious name."""
    spoken_tokens = _tokens(spoken)
    if not spoken_tokens:
        return []
    scored = []
    for c in candidates:
        name_tokens = _tokens(c.scheme_name or "")
        overlap = len(spoken_tokens & name_tokens)
        if overlap:
            # Normalize by the scheme's own length so a 30-word official
            # title doesn't outrank a short exact match on raw overlap.
            scored.append((overlap, overlap / max(1, len(name_tokens)), c))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2].scheme_name))
    return [c for _, _, c in scored[:limit]]


_MATCH_SYSTEM = """You match what a caller said to a government scheme, for an Indian helpline.

You are given what the caller said and a numbered shortlist of real scheme names. Decide which ONE the caller meant.

RULES
1. Answer with the number of a scheme from the shortlist, or the string "NONE".
2. "NONE" is a correct and expected answer. If the caller named a scheme that is not on the shortlist, or said something too vague to identify one, answer "NONE". The helpline will tell them plainly that it does not have that scheme and offer to search by need instead.
3. NEVER pick the closest-looking option just to give an answer. Reading back the wrong scheme is far worse than admitting we do not have it - the caller may act on what they hear.
4. The caller may speak Hindi, English, or a mix, in Roman script, and may use an abbreviation or a partial name.

Return JSON: {"choice": 2} or {"choice": "NONE"}"""


def match_scheme(
    spoken: str,
    candidates: list[Any],
    llm_enabled: bool = True,
    llm_caller: LLMCaller | None = None,
) -> Any | None:
    """Returns the matched candidate, or None meaning "not in the
    catalogue" - a real answer the caller is told plainly, NOT a
    "please repeat".

    Replaces a SequenceMatcher ratio over 100 English scheme names, which
    both missed real names and returned confidently wrong ones. Wrong is
    the expensive direction here: the caller acts on what we read back."""
    short = shortlist_schemes(spoken, candidates)
    if not short:
        return None

    # An unambiguous single hit needs no model - and this is the path that
    # still works with no API key.
    if len(short) == 1:
        return short[0]

    if not llm_enabled or not config.LLM_API_KEY:
        return short[0]

    listing = "\n".join(f"{i}. {c.scheme_name}" for i, c in enumerate(short, 1))
    user = f"The caller said: \"{spoken.strip()}\"\n\nShortlist:\n{listing}"

    caller = llm_caller or _default_caller
    try:
        raw = caller(_MATCH_SYSTEM, user)
        data = json.loads(raw)
        choice = data.get("choice") if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.info("understand: match raised %s, using shortlist rank 1", type(e).__name__)
        return short[0]

    if isinstance(choice, str) and choice.strip().upper() == "NONE":
        return None
    # THE GATE: an index into the shortlist WE built, never a name the
    # model wrote. Anything else falls back to the deterministic rank 1.
    if isinstance(choice, int) and 1 <= choice <= len(short):
        return short[choice - 1]
    if isinstance(choice, str) and choice.strip().isdigit():
        idx = int(choice.strip())
        if 1 <= idx <= len(short):
            return short[idx - 1]
    logger.info("understand: match returned unusable choice %r, using shortlist rank 1", choice)
    return short[0]
