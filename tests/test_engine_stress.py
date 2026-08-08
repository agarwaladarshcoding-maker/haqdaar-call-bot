"""Stress test for engine.py beyond TEST_CASES.md's documented C/D/E/F/G/I
series. Adversarial/property-based coverage: random event sequences,
interleaved undo/invalid/silence, and the ~100-scenario fuzz pass the user
asked for at every step boundary.
"""
import random

import pytest

from haqdaar.bank import load_bank
from haqdaar.engine import CallState, step

BANK = load_bank()


def all_dtmf_keys_for(state):
    if state.current_question is None:
        return []
    q = BANK.question(state.current_question)
    return list((q.get("dtmf") or {}).keys())


# ---------------------------------------------------------------------------
# 1. Fully random event sequences must never raise and must always leave
#    the engine in a valid, well-formed state.
# ---------------------------------------------------------------------------
def random_event(rng, state):
    kind = rng.choice(["dtmf", "dtmf", "dtmf", "global", "speech", "silence", "hangup"])
    if kind == "dtmf":
        return {"dtmf": rng.choice("0123456789*#")}
    if kind == "global":
        return {"dtmf": rng.choice(["0", "*", "#"])}
    if kind == "speech":
        return {"speech": rng.choice(["", "haan", "kuch bhi bol raha hoon", None]),
                "confidence": rng.choice([0.0, 0.3, 0.59, 0.6, 0.9, 1.0])}
    if kind == "silence":
        return {"timeout": rng.choice([1, 5, 10, 15, 20, 25, 30, 45])}
    return {"hangup": True}


@pytest.mark.parametrize("seed", range(60))
def test_random_event_sequences_never_crash(demo_db, seed):
    rng = random.Random(seed)
    state = CallState()
    for _ in range(40):
        event = random_event(rng, state)
        state, actions = step(state, event, BANK, demo_db)
        assert isinstance(actions, list)
        assert state.phase in ("asking", "presenting", "ended")
        assert len(state.candidates) <= 20
        assert len(state.candidates) >= 0
        if state.phase == "ended":
            break


# ---------------------------------------------------------------------------
# 2. Repeated undo/redo cycles: answer N questions, undo N+2 times (more
#    than were asked), confirm it lands cleanly at the start every time,
#    never a negative index, never a crash - generalizes C6/C7.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(15))
def test_over_undo_always_lands_cleanly_at_start(demo_db, seed):
    rng = random.Random(seed + 100)
    state = CallState()
    state, _ = step(state, {}, BANK, demo_db)
    depth = rng.randint(0, 5)
    for _ in range(depth):
        keys = all_dtmf_keys_for(state)
        if not keys or state.phase != "asking":
            break
        state, _ = step(state, {"dtmf": keys[0]}, BANK, demo_db)

    for _ in range(depth + 5):
        state, actions = step(state, {"dtmf": "#"}, BANK, demo_db)
        assert isinstance(actions, list)

    assert state.answers == {} or state.current_question == "Q001_LANGUAGE"


# ---------------------------------------------------------------------------
# 3. Undo must always restore the EXACT prior candidate count (C5,
#    generalized across many random walk depths, not just one fixed path).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(20))
def test_undo_restores_exact_prior_candidate_count(demo_db, seed):
    rng = random.Random(seed + 200)
    state = CallState()
    state, _ = step(state, {}, BANK, demo_db)

    history_counts = [len(state.candidates)]
    steps_taken = 0
    for _ in range(rng.randint(1, 6)):
        if state.phase != "asking":
            break
        keys = all_dtmf_keys_for(state)
        if not keys:
            break
        key = rng.choice(keys)
        state, _ = step(state, {"dtmf": key}, BANK, demo_db)
        history_counts.append(len(state.candidates))
        steps_taken += 1

    if steps_taken == 0:
        return

    before_undo = len(state.candidates)
    state, _ = step(state, {"dtmf": "#"}, BANK, demo_db)
    assert len(state.candidates) == history_counts[-2]
    assert len(state.candidates) >= before_undo


# ---------------------------------------------------------------------------
# 4. Candidate count is monotonic non-increasing across a pure forward walk
#    (no undo) - mirrors narrow.py's own monotonicity property, but through
#    the engine's own bookkeeping this time, not narrow() called directly.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(15))
def test_forward_walk_candidate_count_never_increases(demo_db, seed):
    rng = random.Random(seed + 300)
    state = CallState()
    state, _ = step(state, {}, BANK, demo_db)
    prev = len(state.candidates)
    for _ in range(10):
        if state.phase != "asking":
            break
        keys = all_dtmf_keys_for(state)
        if not keys:
            break
        state, _ = step(state, {"dtmf": rng.choice(keys)}, BANK, demo_db)
        assert len(state.candidates) <= prev
        prev = len(state.candidates)


# ---------------------------------------------------------------------------
# 5. Silence ladder never ends a call before 30s cumulative, and always
#    ends by/at 30s (F4), across many interleavings of small increments.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(10))
def test_silence_ladder_ends_at_30_never_before(demo_db, seed):
    rng = random.Random(seed + 400)
    state = CallState()
    state, _ = step(state, {}, BANK, demo_db)
    # Answer language first so we're off the D7-special-cased node.
    state, _ = step(state, {"dtmf": "1"}, BANK, demo_db)

    total = 0
    for _ in range(20):
        if state.phase == "ended":
            break
        chunk = rng.choice([2, 3, 5])
        total += chunk
        state, actions = step(state, {"timeout": chunk}, BANK, demo_db)
        if total < 30:
            assert state.phase != "ended", f"call ended early at total={total}"
    if total >= 30:
        assert state.phase == "ended"


# ---------------------------------------------------------------------------
# 6. Invalid-button ladder never exceeds max_invalid before collapsing, and
#    never crashes regardless of which bogus key is pressed repeatedly.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bogus_key", list("0123456789"))
def test_invalid_ladder_bounded_for_every_bogus_key(demo_db, bogus_key):
    state = CallState()
    state, _ = step(state, {}, BANK, demo_db)
    state, _ = step(state, {"dtmf": "1"}, BANK, demo_db)  # language
    state, _ = step(state, {"dtmf": "2"}, BANK, demo_db)  # intent=find_for_me -> persona node, keys 1-5

    valid_keys = set(all_dtmf_keys_for(state))
    if bogus_key in valid_keys or bogus_key in {"0", "*", "#"}:
        return  # not actually bogus at this node, skip

    for _ in range(6):
        state, actions = step(state, {"dtmf": bogus_key}, BANK, demo_db)
        assert state.invalid_count <= BANK.policies["invalid_policy"]["max_invalid"]
        assert isinstance(actions, list) and actions


# ---------------------------------------------------------------------------
# 7. Every reachable "asking" state has a current_question that is
#    actually present in the bank (no stale/invalid question ids ever
#    surface in state).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(15))
def test_current_question_always_resolvable_in_bank(demo_db, seed):
    rng = random.Random(seed + 500)
    state = CallState()
    for _ in range(25):
        event = random_event(rng, state)
        state, _ = step(state, event, BANK, demo_db)
        if state.phase == "asking" and state.current_question:
            # Must not raise KeyError.
            BANK.question(state.current_question)
        if state.phase == "ended":
            break


# ---------------------------------------------------------------------------
# 8. The user's explicit ask: ~100 scenarios, each a fresh random call,
#    driven to completion (ended or a bounded number of turns), never a
#    crash, never an unbounded loop.
# ---------------------------------------------------------------------------
def test_100_random_full_calls_never_crash_or_hang(demo_db):
    rng = random.Random(999)
    for i in range(100):
        state = CallState()
        turns = 0
        while state.phase != "ended" and turns < 60:
            event = random_event(rng, state)
            state, actions = step(state, event, BANK, demo_db)
            assert isinstance(actions, list)
            turns += 1
        assert turns < 60, f"scenario {i} did not terminate within 60 turns"
