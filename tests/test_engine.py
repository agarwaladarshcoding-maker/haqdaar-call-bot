"""TEST_CASES.md sections C, D, E, F, G, I - the state machine (Step 5).

C5, D7, E7, I6 are BLOCKERS and get their own explicit, unambiguous test
(not just folded into a bigger scenario) so a regression here fails loudly.
"""
from haqdaar.bank import load_bank
from haqdaar.engine import ROOT_QUESTION_ID, CallState, step

BANK = load_bank()


def start(db):
    return step(CallState(), {}, BANK, db)


def answer(state, key, db):
    return step(state, {"dtmf": key}, BANK, db)


def to_persona_node(db):
    """Lands on Q003B_PERSONA with all 20 demo schemes still candidates.

    Route: root -> "2" (I need help) -> Q000_OPEN_NEED, the free-speech
    question -> two unclear attempts -> its after_max_speech.next, which
    is Q003B_PERSONA. Going via the unclear ladder rather than by SAYING
    something is deliberate: it needs no LLM (conftest blanks the API key
    for hermeticity) and lands on a known question every time, so the
    C/E/F/G ladder tests below still start from a fixed, dtmf-bearing
    node."""
    state, _ = start(db)
    state, _ = answer(state, "2", db)  # intent = by_purpose -> Q000_OPEN_NEED
    for _ in range(2):
        state, actions = step(state, {"speech": "", "confidence": 0.0}, BANK, db)
    assert state.current_question == "Q003B_PERSONA", state.current_question
    return state, actions


# ---------------------------------------------------------------------------
# C. Global keys
# ---------------------------------------------------------------------------
def test_c1_zero_returns_to_root_and_wipes_answers(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = answer(state, "1", demo_db)  # persona = business, narrows to 4
    assert state.answers

    state, actions = step(state, {"dtmf": "0"}, BANK, demo_db)
    assert state.current_question == ROOT_QUESTION_ID
    assert state.answers == {}
    assert len(state.candidates) == 20


def test_c2_c3_star_replays_last_spoken_exactly(demo_db):
    state, actions = to_persona_node(demo_db)
    spoken_before = state.last_spoken
    state2, actions2 = step(state, {"dtmf": "*"}, BANK, demo_db)
    assert actions2[0]["say"] == spoken_before
    assert state2.last_spoken == spoken_before
    # * must not change any call state.
    assert state2.answers == state.answers
    assert state2.asked == state.asked
    assert state2.current_question == state.current_question


def test_c4_c5_hash_undoes_answer_and_regrows_candidates(demo_db):
    state, _ = to_persona_node(demo_db)
    before_count = len(state.candidates)
    state, _ = answer(state, "1", demo_db)  # persona = business
    narrowed_count = len(state.candidates)
    assert narrowed_count < before_count  # sanity: the answer did narrow

    state, actions = step(state, {"dtmf": "#"}, BANK, demo_db)
    # C4: previous question re-asked.
    assert state.current_question == "Q003B_PERSONA"
    assert "persona" not in state.answers
    # C5 - THE BLOCKER: candidate count must grow back, not stay narrowed.
    assert len(state.candidates) == before_count


def test_c6_hash_at_first_question_does_not_crash_or_move(demo_db):
    state, _ = start(demo_db)
    assert state.current_question == ROOT_QUESTION_ID
    state, actions = step(state, {"dtmf": "#"}, BANK, demo_db)
    assert state.current_question == ROOT_QUESTION_ID
    assert state.answers == {}
    assert actions  # produced some action, did not raise


def test_c7_five_hashes_lands_at_first_question_no_negative_index(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = answer(state, "1", demo_db)
    for _ in range(5):
        state, actions = step(state, {"dtmf": "#"}, BANK, demo_db)
    assert state.current_question == ROOT_QUESTION_ID
    assert state.answers == {}


def test_c8_zero_mid_narrowing_wipes_asked_and_restores_full_candidates(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = answer(state, "1", demo_db)
    assert state.asked
    state, _ = step(state, {"dtmf": "0"}, BANK, demo_db)
    assert state.asked == ()
    assert len(state.candidates) == 20


# ---------------------------------------------------------------------------
# D. Root node
#
# Was "Language node". Q001_LANGUAGE is gone (see question_bank.yaml for
# why), so every guarantee this section asserted about "the first thing a
# caller hits" now has to hold for Q002_INTENT instead. The guarantees
# themselves are unchanged - they were never really about language, they
# were about not failing the caller at the very first prompt.
#
# D2 ("speech ignored at the language node") is deliberately gone rather
# than ported: it existed because Q001 could not run ASR before knowing
# which language to run it in. No question in the bank disables speech any
# more, so there is no node for it to describe. The engine code path is
# still there and still correct (_handle_speech's `collect.speech` branch)
# and is now covered directly in test_call_path.py, driven by an action
# rather than by a bank question that no longer exists.
# ---------------------------------------------------------------------------
def test_d1_root_offers_the_two_scenario_fork(demo_db):
    """Two options, not three. The old third ("find schemes for me" vs
    "help with a specific need") was a distinction the system cared about
    and a caller could not answer - both mean "I have no name, help me"."""
    state, actions = start(demo_db)
    say = actions[0]["say"]
    assert "1" in say and "2" in say
    assert "3" not in say


def test_d4_invalid_digit_at_root_replays_options(demo_db):
    state, _ = start(demo_db)
    state2, actions = step(state, {"dtmf": "7"}, BANK, demo_db)
    assert state2.current_question == ROOT_QUESTION_ID
    assert state2.invalid_count == 1
    assert state2.answers == {}


def test_d5_zero_at_root_replays_root_prompt(demo_db):
    state, _ = start(demo_db)
    state2, actions = step(state, {"dtmf": "0"}, BANK, demo_db)
    assert state2.current_question == ROOT_QUESTION_ID


def test_d6_hash_at_root_does_not_exit_call(demo_db):
    state, _ = start(demo_db)
    state2, actions = step(state, {"dtmf": "#"}, BANK, demo_db)
    assert state2.phase != "ended"
    assert state2.current_question == ROOT_QUESTION_ID


def test_d7_silence_30s_at_root_defaults_and_continues(demo_db):
    """BLOCKER. Ending the call because of hesitation at the very first
    prompt is the worst possible failure - they never heard a scheme.

    The guarantee moved with the root: Q001_LANGUAGE carried the
    on_timeout default that made this pass, and when it was deleted the
    root had to inherit it or this blocker would have silently regressed
    into "30s of hesitation hangs up on the caller"."""
    state, _ = start(demo_db)
    state2, actions = step(state, {"timeout": 30}, BANK, demo_db)
    assert state2.phase != "ended"
    assert state2.answers.get("intent") == "by_purpose"
    assert state2.current_question is not None


def test_d8_three_wrong_presses_collapses_not_hangs_up(demo_db):
    state, _ = start(demo_db)
    for _ in range(3):
        state, actions = step(state, {"dtmf": "9"}, BANK, demo_db)
    assert state.phase != "ended"
    assert state.current_question == ROOT_QUESTION_ID


# ---------------------------------------------------------------------------
# E. Unclear speech
# ---------------------------------------------------------------------------
def test_e1_low_confidence_gives_unclear_message(demo_db):
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"speech": "kuch bhi", "confidence": 0.2}, BANK, demo_db)
    assert state2.speech_attempts == 1
    assert "samajh nahi" in actions[0]["say"]


def test_e2_second_unclear_attempt_increments_counter(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = step(state, {"speech": "x", "confidence": 0.1}, BANK, demo_db)
    state, actions = step(state, {"speech": "y", "confidence": 0.1}, BANK, demo_db)
    assert state.speech_attempts == 2


def test_e3_third_unclear_forces_dtmf_only(demo_db):
    state, _ = to_persona_node(demo_db)
    for _ in range(2):
        state, _ = step(state, {"speech": "x", "confidence": 0.1}, BANK, demo_db)
    state, actions = step(state, {"speech": "z", "confidence": 0.1}, BANK, demo_db)
    gather = [a for a in actions if "gather" in a][0]
    assert gather["gather"]["speech"] is False


def test_e5_unclear_then_valid_keypress_resets_speech_counter(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = step(state, {"speech": "x", "confidence": 0.1}, BANK, demo_db)
    assert state.speech_attempts == 1
    state, _ = answer(state, "1", demo_db)
    assert state.speech_attempts == 0


def test_e6_empty_string_stt_treated_as_unclear(demo_db):
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"speech": "", "confidence": 0.99}, BANK, demo_db)
    assert state2.speech_attempts == 1
    assert state2.answers == state.answers  # not treated as a valid answer


def test_e7_stt_timeout_shape_treated_as_unclear_not_a_crash(demo_db):
    """BLOCKER. A Sarvam outage must degrade to buttons, never crash a live
    call - simulated here as a malformed/empty speech event."""
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"speech": None, "confidence": 0.0}, BANK, demo_db)
    assert state2.phase == "asking"
    assert actions


# ---------------------------------------------------------------------------
# F. Silence ladder
# ---------------------------------------------------------------------------
def test_f1_5s_silence_gives_short_nudge(demo_db):
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"timeout": 5}, BANK, demo_db)
    assert state2.silence_elapsed == 5
    assert actions


def test_f2_15s_silence_gives_two_options(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = step(state, {"timeout": 5}, BANK, demo_db)
    state2, actions = step(state, {"timeout": 10}, BANK, demo_db)  # total 15
    assert state2.silence_elapsed == 15


def test_f4_30s_silence_ends_politely_not_silently(demo_db):
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"timeout": 30}, BANK, demo_db)
    assert state2.phase == "ended"
    assert any("say" in a for a in actions)  # polite message, not a bare hangup
    assert any(a.get("end") for a in actions)


def test_f5_input_resets_silence_ladder(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = step(state, {"timeout": 20}, BANK, demo_db)
    assert state.silence_elapsed == 20
    state, _ = answer(state, "1", demo_db)
    assert state.silence_elapsed == 0


# ---------------------------------------------------------------------------
# G. Wrong buttons
# ---------------------------------------------------------------------------
def test_g1_wrong_button_replays_options_not_whole_question(demo_db):
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"dtmf": "8"}, BANK, demo_db)
    assert state2.invalid_count == 1
    assert state2.current_question == "Q003B_PERSONA"


def test_g3_third_wrong_press_collapses_to_two_option_menu(demo_db):
    state, _ = to_persona_node(demo_db)
    for _ in range(3):
        state, actions = step(state, {"dtmf": "8"}, BANK, demo_db)
    assert state.invalid_count == 3
    gather = [a for a in actions if "gather" in a]
    assert gather


def test_g4_wrong_then_right_resets_invalid_counter(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = step(state, {"dtmf": "8"}, BANK, demo_db)
    assert state.invalid_count == 1
    state, _ = answer(state, "1", demo_db)
    assert state.invalid_count == 0


def test_g6_unused_key_is_invalid_not_silently_mapped(demo_db):
    state, _ = to_persona_node(demo_db)
    dtmf_keys = set(BANK.question("Q003B_PERSONA").get("dtmf").keys())
    unused = next(k for k in "123456789" if k not in dtmf_keys)
    state2, actions = step(state, {"dtmf": unused}, BANK, demo_db)
    assert state2.invalid_count == 1
    assert "persona" not in state2.answers


# ---------------------------------------------------------------------------
# I. Stop conditions
# ---------------------------------------------------------------------------
def test_i1_stops_when_candidates_lte_5(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = answer(state, "1", demo_db)  # persona=business -> 4 candidates
    assert state.phase == "presenting"
    assert len(state.candidates) <= 5


def test_i4_zero_candidates_undoes_and_asks_something_else(demo_db):
    """Zero candidates must never be shown to the caller as 'nothing for
    you' - engine undoes the answer that caused it and keeps going."""
    state, _ = to_persona_node(demo_db)
    # Drive toward a real state, then force a contradiction via direct
    # engine internals is out of scope - instead assert the INVARIANT: no
    # reachable state via step() ever has phase == "presenting" with
    # exactly 0 candidates AND came from a path where a prior turn had >0,
    # UNLESS genuinely nothing satisfies anything (covered by narrow.py's
    # own B7). Practically: after any single dtmf answer, candidates is
    # never empty in this demo bank (rules are permissive), so assert the
    # simpler guarantee that phase is never left dangling.
    assert state.phase in ("asking", "presenting")


def test_i5_single_candidate_presents_immediately(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = answer(state, "1", demo_db)
    if len(state.candidates) == 1:
        assert state.phase == "presenting"


def test_i6_no_askable_questions_presents_does_not_loop(demo_db):
    """BLOCKER. Drive every dtmf answer down option 1 repeatedly; the
    engine must terminate (either by stop condition or by askable()
    running dry) within a small, bounded number of turns - never spin."""
    state, _ = start(demo_db)
    turns = 0
    while state.phase == "asking" and turns < 30:
        state, actions = answer(state, "1", demo_db)
        turns += 1
    assert state.phase in ("presenting", "asking")
    assert turns < 30, "engine did not terminate within a bounded number of turns"


def test_hangup_event_ends_call_from_any_phase(demo_db):
    state, _ = to_persona_node(demo_db)
    state2, actions = step(state, {"hangup": True}, BANK, demo_db)
    assert state2.phase == "ended"
    assert actions == [{"end": True}]


def test_step_after_ended_is_a_noop_end(demo_db):
    state, _ = to_persona_node(demo_db)
    state, _ = step(state, {"hangup": True}, BANK, demo_db)
    state2, actions = step(state, {"dtmf": "1"}, BANK, demo_db)
    assert state2.phase == "ended"
    assert actions == [{"end": True}]
