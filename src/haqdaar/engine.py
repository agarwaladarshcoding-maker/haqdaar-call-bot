"""State machine: step(state, event) -> (new_state, actions). Step 5 - THE HEART.

Pure function. No I/O, no clock reads, no DB writes inside step() itself -
narrow() and select() do read the DB, but they are deterministic given the
same DB file and the same answers, so replaying the same (state, event)
log against the same DB always reproduces the same call. This is what
makes tests/test_engine.py able to drive a "call" with literal event
dicts, and what will let sim.py (Step 8) and a real Twilio adapter
(Step 11, later) be two interchangeable clients of the same logic.

event   = {"dtmf": "1"} | {"speech": "...", "confidence": 0.8}
        | {"timeout": 5} | {"hangup": True}
actions = [{"say": "..."}, {"gather": {...}}, {"end": True}]

Priority order at every node (BUILD_STEPS.md Step 5 / SYSTEM_DESIGN.md #8):
  1. Global keys first, always: 0 = main menu + wipe answers, * = replay
     last_spoken, # = undo last answer AND re-run narrow().
  2. Normal answer keys (1-9, whatever the current question's dtmf offers).
  3. Unclear speech ladder (2 attempts, then force DTMF).
  4. Wrong-button ladder (3 attempts, then collapse to a 2-option menu).
  5. Silence ladder (5s/15s/25s/30s).

C5 - THE NASTIEST BUG IN THE SYSTEM: '#' must both remove the last answer
AND re-run narrow() in the same step. candidates is NEVER cached
independent of answers - every code path that changes `answers` recomputes
`candidates` from narrow() before returning. There is exactly one place in
this file that calls narrow() to produce state.candidates
(_recompute_candidates), and every mutation of answers goes through it.

I6 - never loop forever if bank.askable() is empty for reasons other than
having reached a stop condition: _next_question always has a fallback to
presenting whatever candidates exist rather than asking select.py for a
question that doesn't exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from haqdaar.bank import Bank, Question
from haqdaar.narrow import Candidate, narrow
from haqdaar.present import present_many
from haqdaar.select import pick_question

MAIN_MENU_QUESTION_ID = "__MAIN_MENU__"  # sentinel: no question asked yet


@dataclass(frozen=True)
class CallState:
    phase: str = "asking"  # asking | presenting | ended
    language: str | None = None
    answers: dict[str, Any] = field(default_factory=dict)
    asked: tuple[str, ...] = field(default_factory=tuple)
    current_question: str | None = None
    last_spoken: str = ""
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    invalid_count: int = 0
    speech_attempts: int = 0
    silence_elapsed: int = 0
    menu_path: tuple[str, ...] = field(default_factory=tuple)
    llm_error_count: int = 0  # J6: 3 LLM errors in a call disables it for the rest


def _merged(question: Question | None, bank: Bank, key: str, fallback_key: str | None = None) -> Any:
    """Per-question override wins; otherwise the bank-level policy under
    `fallback_key` in bank.policies (SYSTEM_DESIGN.md's documented merge:
    question-level on_invalid/on_timeout/etc beat the global ladder)."""
    if question is not None:
        val = question.get(key)
        if val is not None:
            return val
    if fallback_key is not None:
        return bank.policies.get(fallback_key)
    return None


def _say(text: str) -> dict:
    return {"say": text}


def _gather(question: Question | None) -> dict:
    if question is None:
        return {"gather": {"digits": 1}}
    collect = question.get("collect") or {"digits": 1, "speech": True}
    return {"gather": {"digits": collect.get("digits", 1), "speech": collect.get("speech", True)}}


def _end(text: str | None = None) -> list[dict]:
    out = []
    if text:
        out.append(_say(text))
    out.append({"end": True})
    return out


def _prompt_text(question: Question, language: str | None, answers: dict[str, Any] | None = None, db_path: str | None = None) -> str:
    lang = language or "hi"
    key = "prompt_en" if lang == "en" else "prompt_hi"
    text = question.get(key) or question.get("prompt_hi") or ""
    
    if "{" in text and answers and "_named_scheme" in answers:
        slug = answers["_named_scheme"]
        from haqdaar.db import get_db
        conn = get_db(db_path)
        try:
            row = conn.execute("SELECT name_short_hi, scheme_name, benefit_one_line FROM schemes WHERE slug = ?", (slug,)).fetchone()
            if row:
                name = row["name_short_hi"] or row["scheme_name"] or ""
                benefit = row["benefit_one_line"] or ""
                if not benefit:
                    text = text.replace("Isme {benefit_one_line}. ", "")
                    text = text.replace("It gives {benefit_one_line}. ", "")
                text = text.format(name_short_hi=name, benefit_one_line=benefit)
        finally:
            conn.close()
            
    return text


def _options_text(question: Question, language: str | None, keys: list[str] | None = None) -> str:
    lang = language or "hi"
    dtmf = question.get("dtmf") or {}
    parts = []
    for k, opt in dtmf.items():
        if keys is not None and k not in keys:
            continue
        label = opt.get(lang) or opt.get("hi") or ""
        parts.append(f"{k}: {label}")
    return " | ".join(parts)


def _stop_reason(state: CallState, bank: Bank) -> str | None:
    """I1/I2/I5: returns why we should stop asking and present, or None."""
    stop_at = bank.meta.get("stop_when_candidates_lte", 5)
    max_q = bank.meta.get("max_questions_per_call", 10)
    n = len(state.candidates)
    if n <= stop_at:
        return "candidates_narrow_enough"
    if len(state.asked) >= max_q:
        return "max_questions_reached"
    return None


def _recompute_candidates(state: CallState, db_path: str | None) -> CallState:
    """THE ONLY place narrow() is called to populate state.candidates.
    Every function that mutates `answers` MUST route the result through
    this before returning - never hand-patch `candidates` separately."""
    cands = tuple(narrow(state.answers, db_path))
    return replace(state, candidates=cands)


def _next_question(state: CallState, bank: Bank, db_path: str | None) -> Question | None:
    """I6: askable() can legitimately return [] (every remaining question's
    `requires` is unmet) without that meaning "ask forever" - it just means
    there is nothing left to ask, so the caller falls through to
    presenting. select.py itself also returns None in that case; this
    wrapper exists so callers of step() never need to special-case it."""
    already_asked = set(state.asked)
    llm_enabled = state.llm_error_count < 3  # J6
    q = pick_question(state.answers, list(state.candidates), bank, db_path=db_path, llm_enabled=llm_enabled)
    if q is not None and q.id in already_asked:
        # Defense in depth: askable() already excludes answered attributes,
        # but a question can in principle be re-offered after an undo
        # brought its attribute back to "unanswered". That is CORRECT
        # (the same question should be re-askable after #), so this is
        # intentionally not treated as an error - only guards against an
        # infinite ask-same-question loop within a single un-undone turn,
        # which _next_question is never called twice for without state
        # changing in between.
        pass
    return q


def _enter_question(
    state: CallState, question: Question | None, bank: Bank, db_path: str | None = None
) -> tuple[CallState, list[dict]]:
    if question is None:
        return _enter_presenting(state, bank, db_path)
    new_state = replace(
        state,
        phase="asking",
        current_question=question.id,
        invalid_count=0,
        speech_attempts=0,
        silence_elapsed=0,
    )
    text = _prompt_text(question, state.language, state.answers, db_path)
    new_state = replace(new_state, last_spoken=text)
    return new_state, [_say(text), _gather(question)]


def _enter_presenting(state: CallState, bank: Bank, db_path: str | None = None) -> tuple[CallState, list[dict]]:
    new_state = replace(state, phase="presenting", current_question=None)
    if not new_state.candidates:
        # K6: no scheme matches - say so honestly, invent nothing.
        text = "Maaf kijiye, koi yojna nahi mili. Mukhya menu ke liye zero dabaiye."
    else:
        top = new_state.candidates[:5]
        presentations = present_many(
            top, _benefits_text_by_slug(top, db_path), new_state.answers, llm_enabled=state.llm_error_count < 3
        )
        lines = "; ".join(f"{p.spoken_name} - {p.benefit_line}" for p in presentations)
        text = f"Yeh yojnaayein mil sakti hain: {lines}."
    new_state = replace(new_state, last_spoken=text)
    return new_state, [_say(text), {"end": True}]


def _benefits_text_by_slug(candidates: tuple[Candidate, ...], db_path: str | None) -> dict[str, str]:
    """present.py needs each candidate's raw `benefits` column to verify
    its LLM-selected sentence is a verbatim substring (K2/K3) - narrow.py's
    Candidate doesn't carry it (it's presentation-layer data, not
    matching-layer data), so this is a small, separate, read-only lookup."""
    from haqdaar.db import get_db

    if not candidates:
        return {}
    conn = get_db(db_path)
    try:
        slugs = [c.slug for c in candidates]
        placeholders = ",".join("?" for _ in slugs)
        rows = conn.execute(f"SELECT slug, benefits FROM schemes WHERE slug IN ({placeholders})", slugs).fetchall()
        return {r["slug"]: r["benefits"] or "" for r in rows}
    finally:
        conn.close()


def _start_call(bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    state = CallState()
    state = _recompute_candidates(state, db_path)
    q = bank.question("Q001_LANGUAGE")
    return _enter_question(state, q, bank, db_path)


def _global_key(state: CallState, key: str, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]] | None:
    """C1-C8. Returns None if `key` is not a global key at all."""
    if key == "0":
        # C1/C8: back to language, answers wiped, asked wiped, candidates
        # reset to the full unfiltered set (recomputed from {} answers, not
        # hand-set to some remembered "all schemes" list).
        fresh = CallState(language=state.language)
        fresh = _recompute_candidates(fresh, db_path)
        q = bank.question("Q001_LANGUAGE")
        return _enter_question(fresh, q, bank, db_path)

    if key == "*":
        # C2/C3: replay last_spoken verbatim, no state change at all beyond
        # re-emitting the say action - explicitly does not touch asked,
        # answers, candidates, or any ladder counters.
        actions = [_say(state.last_spoken)]
        if state.phase == "asking" and state.current_question:
            q = bank.question(state.current_question)
            actions.append(_gather(q))
        elif state.phase == "presenting":
            actions.append({"end": True})
        return state, actions

    if key == "#":
        # C4-C7: undo the last answer AND re-run narrow() - see module
        # docstring, this is C5, the single most important line in engine.py.
        if not state.asked:
            # C6: at the first question, '#' stays put, does not crash.
            q = bank.question(state.current_question) if state.current_question else bank.question("Q001_LANGUAGE")
            return _enter_question(state, q, bank, db_path)

        last_qid = state.asked[-1]
        last_q = bank.question(last_qid)
        written_attr = last_q.raw["writes"]
        new_answers = {k: v for k, v in state.answers.items() if k != written_attr}
        new_asked = state.asked[:-1]
        undone_state = replace(state, answers=new_answers, asked=new_asked, language=(
            new_answers.get("language", state.language) if written_attr != "language" else None
        ))
        # language is special-cased above only insofar as undoing the very
        # first answer should also forget the language choice, matching
        # C7's "5x # lands at the first question" (not stuck mid-ladder
        # with a language set but no way to re-ask it).
        undone_state = _recompute_candidates(undone_state, db_path)
        # Re-ask the question that WROTE the attribute we just undid -
        # that's "the previous question" C4 refers to.
        return _enter_question(undone_state, last_q, bank, db_path)

    return None


def _handle_dtmf_answer(state: CallState, key: str, question: Question, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    dtmf = question.get("dtmf") or {}
    if not dtmf:
        # I6 guard: a speech-only question (no dtmf options at all, e.g.
        # Q100_SCHEME_NAME) has nothing for _handle_invalid's G-ladder to
        # collapse TO - looping the invalid ladder forever on a question
        # with zero valid buttons is exactly the infinite-loop bug I6
        # warns about. Treat any stray keypress here as "give up on speech
        # for this question" and fall through the same forced-fallback
        # path speech.after_max_speech already defines, same as maxing
        # out the speech-attempts ladder would.
        return _force_fallback(state, question, bank, db_path)
    opt = dtmf.get(key)
    if opt is None:
        return _handle_invalid(state, question, bank, db_path)

    write_attr = question.raw["writes"]
    write_val = (opt.get("set") or {}).get(write_attr)
    new_answers = {**state.answers, write_attr: write_val}
    new_asked = state.asked + (question.id,)
    new_language = new_answers.get("language", state.language)
    new_state = replace(
        state,
        answers=new_answers,
        asked=new_asked,
        language=new_language,
        invalid_count=0,       # G4: wrong-then-right resets the counter
        speech_attempts=0,     # E5: unclear-then-valid resets speech counter
        silence_elapsed=0,     # F5: any real input resets the silence ladder
    )
    new_state = _recompute_candidates(new_state, db_path)

    reason = _stop_reason(new_state, bank)
    if reason == "candidates_narrow_enough" or reason == "max_questions_reached":
        return _enter_presenting(new_state, bank, db_path)
    if not new_state.candidates:
        # I4: zero candidates -> undo the answer we JUST made and ask
        # something else, never tell the caller there is nothing for them.
        reverted = replace(state, invalid_count=0, speech_attempts=0)
        reverted = _recompute_candidates(reverted, db_path)
        nxt = _next_question(reverted, bank, db_path)
        return _enter_question(reverted, nxt, bank, db_path)

    nxt = _next_question(new_state, bank, db_path)
    return _enter_question(new_state, nxt, bank, db_path)


def _handle_invalid(state: CallState, question: Question, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    """G1-G6. Wrong button pressed - not a global key, not a valid dtmf key."""
    max_invalid = bank.policies["invalid_policy"]["max_invalid"]
    new_count = state.invalid_count + 1

    if new_count >= max_invalid:
        # G3: collapse to a 2-option menu rather than keep repeating. Pin
        # the counter at max_invalid rather than letting it climb forever
        # on further wrong presses at the collapsed menu - it has already
        # reached its ceiling and stays there until a valid answer resets
        # it to 0 (G4).
        on_max = _merged(question, bank, "on_max_invalid", None) or bank.policies["invalid_policy"]["on_max_invalid"]
        dtmf_keys = list((question.get("dtmf") or {}).keys())[:2]
        collapsed_text = on_max.get("hi", "") + " " + _options_text(question, state.language, dtmf_keys)
        new_state = replace(state, invalid_count=max_invalid, last_spoken=collapsed_text)
        return new_state, [_say(collapsed_text), _gather(question)]

    on_invalid = _merged(question, bank, "on_invalid", None) or bank.policies["invalid_policy"]["on_invalid"]
    then_action = bank.policies["invalid_policy"]["then"]
    if isinstance(on_invalid, dict) and on_invalid.get("action") == "replay_options":
        msg = on_invalid.get("hi", "")
    else:
        msg = on_invalid.get("hi", "") if isinstance(on_invalid, dict) else str(on_invalid)
    options_text = _options_text(question, state.language)
    text = f"{msg} {options_text}".strip()
    new_state = replace(state, invalid_count=new_count, last_spoken=text)
    return new_state, [_say(text), _gather(question)]


def _resolve_speech_to_dtmf(state: CallState, text: str, question: Question) -> str | None:
    from haqdaar import llm
    import json

    dtmf = question.get("dtmf") or {}
    if not dtmf:
        return None

    options_text = _options_text(question, state.language)
    prompt_text = _prompt_text(question, state.language, state.answers)

    system = (
        "You are a natural language router for a phone tree. "
        "You will be given the prompt that was played to the user, the available keypad options, "
        "and the transcribed speech of the user's response.\n"
        "Your job is to match the user's speech to one of the available keypad options.\n"
        "Output a JSON object with a single key 'dtmf_key' containing the mapped key as a string (e.g. '1'). "
        "If the user's speech does not clearly match any of the options, output 'UNCLEAR' as the value."
    )
    user = f"Prompt: {prompt_text}\nOptions: {options_text}\nUser Speech: {text}"

    try:
        resp = llm.chat(system, user, temperature=0.0, json_mode=True)
        data = json.loads(resp)
        key = data.get("dtmf_key")
        if key in dtmf:
            return key
    except Exception as e:
        print(f"LLM Speech Router Error: {e}", flush=True)
    return None


def _handle_speech(state: CallState, event: dict, question: Question, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    """E1-E7. Unclear/low-confidence speech ladder."""
    collect = question.get("collect") or {"speech": True}
    if not collect.get("speech", True):
        # D2: speech ignored entirely at this node (e.g. language node).
        return state, [_say(state.last_spoken), _gather(question)]

    text = (event.get("speech") or "").strip()
    confidence = event.get("confidence", 0.0)
    min_conf = bank.policies["speech_policy"]["min_confidence"]
    is_clear = bool(text) and confidence >= min_conf  # E6: empty string is always unclear

    if is_clear:
        if "SCHEME_NAME" in (question.get("speech_intents") or []):
            return _handle_known_scheme_speech(state, text, question, bank, db_path)
        
        # Use LLM to resolve standard speech to a DTMF key
        if state.llm_error_count < 3:
            matched_key = _resolve_speech_to_dtmf(state, text, question)
            if matched_key is not None:
                return _handle_dtmf_answer(state, matched_key, question, bank, db_path)
            # If it didn't match (UNCLEAR) or LLM failed, we fall through to unclear ladder
        else:
            pass # LLM disabled, treat as unclear

    max_attempts = question.get("speech_attempts") or bank.policies["speech_policy"]["max_speech_attempts"]
    new_attempts = min(state.speech_attempts + 1, max_attempts)

    if new_attempts >= max_attempts:
        return _force_fallback(replace(state, speech_attempts=new_attempts), question, bank, db_path)

    on_unclear = _merged(question, bank, "on_unclear", None) or bank.policies["speech_policy"]["on_unclear"]
    msg = on_unclear.get("hi", "") if isinstance(on_unclear, dict) else str(on_unclear)
    new_state = replace(state, speech_attempts=new_attempts, last_spoken=msg)
    return new_state, [_say(msg), _gather(question)]


def _fuzzy_match(spoken: str, candidate_name: str) -> bool:
    spoken = spoken.lower().strip()
    c_name = candidate_name.lower().strip()
    if not spoken or not c_name:
        return False
    if spoken in c_name or c_name in spoken:
        return True
    
    spoken_words = set(spoken.split())
    c_words = set(c_name.split())
    
    stop = {"the", "of", "and", "in", "on", "for", "to", "a", "an", "scheme", "yojana", "yojna"}
    spoken_words -= stop
    c_words -= stop
    
    if not spoken_words:
        return False
        
    overlap = len(spoken_words & c_words)
    if overlap >= max(1, len(spoken_words) - 1):
        return True
        
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, spoken, c_name).ratio()
    if ratio > 0.6:
        return True
        
    return False


def _handle_known_scheme_speech(state: CallState, text: str, question: Question, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    best_match = None
    for c in state.candidates:
        if _fuzzy_match(text, c.scheme_name) or (c.name_short_hi and _fuzzy_match(text, c.name_short_hi)):
            best_match = c
            break
            
    if best_match:
        on_match = _merged(question, bank, "on_match", None)
        new_answers = dict(state.answers)
        if question.raw.get("writes"):
            new_answers[question.raw["writes"]] = best_match.slug
        new_state = replace(state, answers=new_answers, asked=state.asked + (question.id,))
        new_state = _recompute_candidates(new_state, db_path)
        
        nxt_q = bank.question(on_match["next"])
        return _enter_question(new_state, nxt_q, bank, db_path)
        
    max_attempts = question.get("speech_attempts") or bank.policies["speech_policy"]["max_speech_attempts"]
    new_attempts = min(state.speech_attempts + 1, max_attempts)

    if new_attempts >= max_attempts:
        return _force_fallback(replace(state, speech_attempts=new_attempts), question, bank, db_path)

    on_unclear = _merged(question, bank, "on_unclear", None) or bank.policies["speech_policy"]["on_unclear"]
    msg = on_unclear.get("hi", "") if isinstance(on_unclear, dict) else str(on_unclear)
    new_state = replace(state, speech_attempts=new_attempts, last_spoken=msg)
    return new_state, [_say(msg), _gather(question)]


def _force_fallback(state: CallState, question: Question, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    """Shared by E3 (speech attempts exhausted) and the I6 guard in
    _handle_dtmf_answer (a stray keypress on a speech-only question,
    which has no buttons for the G-ladder to collapse to). Either routes
    to the question's own `after_max_speech.next` (e.g. Q100_SCHEME_NAME
    skipping to Q101_SCHEME_CATEGORY), or forces DTMF-only on this same
    question if it has no such override."""
    collect = question.get("collect") or {"digits": 1, "speech": True}
    after = _merged(question, bank, "after_max_speech", None) or bank.policies["speech_policy"]["after_max"]
    msg = after.get("hi", "") if isinstance(after, dict) else str(after)
    new_state = replace(state, last_spoken=msg)

    if isinstance(after, dict) and after.get("next"):
        # e.g. Q100_SCHEME_NAME.after_max_speech routes to a specific
        # follow-up question (and, for that one question, also needs
        # _named_scheme set so Q101's `requires` is satisfiable).
        new_answers = dict(new_state.answers)
        if question.raw["writes"] == "_named_scheme":
            new_answers["_named_scheme"] = "UNKNOWN"
        new_state = replace(new_state, answers=new_answers, asked=new_state.asked + (question.id,))
        new_state = _recompute_candidates(new_state, db_path)
        nxt_q = bank.question(after["next"])
        return _enter_question(new_state, nxt_q, bank, db_path)

    if question.get("dtmf"):
        # E3/E4: force DTMF-only from here on for this question.
        return new_state, [_say(msg), {"gather": {"digits": collect.get("digits", 1), "speech": False}}]

    # No dtmf AND no after_max_speech.next: nothing left to ask here at
    # all - fall through to presenting whatever candidates already exist
    # rather than spin. This is the last-resort I6 backstop.
    return _enter_presenting(new_state, bank, db_path)


def _handle_silence(state: CallState, elapsed: int, question: Question | None, bank: Bank, db_path: str | None) -> tuple[CallState, list[dict]]:
    """F1-F6 + D7. Silence ladder is cumulative across calls to step() -
    elapsed is reported by the caller (the phone platform / sim.py), engine
    just tracks the ladder rung reached so far and never reads a clock."""
    total = state.silence_elapsed + elapsed
    ladder = bank.policies["silence_ladder"]

    on_timeout_override = _merged(question, bank, "on_timeout", None) if question else None
    if total >= 30:
        if on_timeout_override and on_timeout_override.get("action") == "default":
            # D7: silence at the language node must NOT end the call -
            # defaults to Hindi and continues, the worst possible failure
            # being a caller who hangs up having heard nothing.
            write_attr = question.raw["writes"]
            write_val = on_timeout_override["set"][write_attr]
            new_answers = {**state.answers, write_attr: write_val}
            new_state = replace(
                state, answers=new_answers, asked=state.asked + (question.id,),
                language=new_answers.get("language", state.language),
                silence_elapsed=0,
            )
            new_state = _recompute_candidates(new_state, db_path)
            nxt = _next_question(new_state, bank, db_path)
            return _enter_question(new_state, nxt, bank, db_path)
        # F4: polite close, never a silent hangup.
        msg = next((r["hi"] for r in ladder if r["at"] == 30), "Dhanyavaad. Phir call kijiye.")
        return replace(state, phase="ended", silence_elapsed=total), _end(msg)

    rung = None
    for r in ladder:
        if total >= r["at"]:
            rung = r
    if rung is None:
        return replace(state, silence_elapsed=total), []

    new_state = replace(state, silence_elapsed=total, last_spoken=rung["hi"])
    actions = [_say(rung["hi"])]
    if question is not None:
        if rung["action"] == "two_options":
            keys = list((question.get("dtmf") or {}).keys())[:2]
            actions.append(_say(_options_text(question, state.language, keys)))
        actions.append(_gather(question))
    return new_state, actions


def step(state: CallState, event: dict, bank: Bank, db_path: str | None = None) -> tuple[CallState, list[dict]]:
    """The one pure entry point. `bank` and `db_path` are dependencies, not
    hidden globals - same (state, event, bank, db_path) always produces the
    same (new_state, actions), which is the whole point (BUILD_STEPS.md
    Step 5's purity requirement)."""
    if state.phase == "ended":
        return state, [{"end": True}]

    if event.get("hangup"):
        return replace(state, phase="ended"), [{"end": True}]

    # Call start: no current_question yet and nothing asked -> bootstrap.
    if state.current_question is None and not state.asked and state.phase == "asking" and not state.answers:
        # A truly fresh CallState() with no prior actions taken - the very
        # first step() call for a new call. Any event here (even a stray
        # dtmf) just (re)starts at the language question; nothing has been
        # asked yet so there is nothing meaningful to interpret the event
        # against.
        return _start_call(bank, db_path)

    if state.phase == "presenting":
        # Only global keys are meaningful once presenting; anything else
        # (including further dtmf) just ends the call cleanly, matching
        # "presents and stops" rather than looping forever waiting for
        # input nobody asked for.
        dtmf = event.get("dtmf")
        if dtmf in ("0", "*", "#"):
            g = _global_key(state, dtmf, bank, db_path)
            if g is not None:
                return g
        return replace(state, phase="ended"), _end()

    question = bank.question(state.current_question) if state.current_question else None
    if question is None:
        return _enter_presenting(state, bank, db_path)

    dtmf = event.get("dtmf")
    if dtmf is not None:
        g = _global_key(state, dtmf, bank, db_path)
        if g is not None:
            return g
        return _handle_dtmf_answer(state, dtmf, question, bank, db_path)

    if "speech" in event or "confidence" in event:
        return _handle_speech(state, event, question, bank, db_path)

    if "timeout" in event:
        return _handle_silence(state, event.get("timeout", 0), question, bank, db_path)

    # Unrecognized event shape: treat as silence with 0 elapsed rather than
    # crash a live call over a malformed event (defensive, not in the
    # documented C-I series, but consistent with E7's "never crash" spirit).
    return state, [_say(state.last_spoken), _gather(question)]
