"""TEST_CASES.md section K - content safety, integration-level: drives a
real call all the way to engine.py's presenting phase and checks what
actually gets spoken, not just present.py in isolation (tests/test_present.py
already covers present.py's own guardrail unit-by-unit).

K1 is THE ship gate but is fundamentally a human-transcript-review test
(10 real calls, reviewed by a person) - not something to automate away.
What CAN be automated and is covered here: K2/K3 (every benefit/amount
spoken traces back to a real DB row), K4 (no deadline stated as fact for
verified=0 schemes), K6 (zero candidates says so honestly).
"""
import re

import pytest

from haqdaar.bank import load_bank
from haqdaar.engine import CallState, step

BANK = load_bank()


def _run_to_presenting(db_path, script):
    """Stops at the FIRST turn that reaches phase == 'presenting' - the
    next step() call after that transitions straight to 'ended' (engine.py:
    only global keys are meaningful once presenting), so driving the whole
    script past that point would overwrite the presenting actions we
    actually want to inspect."""
    state = CallState()
    state, actions = step(state, {}, BANK, db_path)
    for key in script:
        if state.phase != "asking":
            break
        state, actions = step(state, {"dtmf": key}, BANK, db_path)
    return state, actions


# ---------------------------------------------------------------------------
# K2/K3: every benefit/amount actually spoken in the final presenting
# message must trace back to a real scheme's DB row, not be invented.
# ---------------------------------------------------------------------------
def test_k2_k3_presented_amounts_trace_to_real_scheme_rows(demo_db):
    import sqlite3

    state, actions = _run_to_presenting(demo_db, ["1", "2", "1"] + ["1"] * 12)
    assert state.phase == "presenting"

    spoken_text = next(a["say"] for a in actions if "say" in a)

    conn = sqlite3.connect(demo_db)
    conn.row_factory = sqlite3.Row
    all_benefits = [r["benefits"] for r in conn.execute("SELECT benefits FROM schemes")]

    # Every rupee amount spoken in the final message must appear in AT
    # LEAST one real scheme's benefits column - an amount present in the
    # spoken text but nowhere in the DB would be a fabricated number.
    spoken_amounts = re.findall(r"Rs\s*[\d,]+", spoken_text)
    for amount in spoken_amounts:
        assert any(amount in b for b in all_benefits if b), (
            f"spoken amount {amount!r} does not appear in any real scheme's benefits text"
        )


def test_k2_benefit_line_never_invented_across_many_call_paths(demo_db):
    """Runs several different scripted paths to presenting and checks each
    one's spoken benefit content against the DB, not just one happy path."""
    import sqlite3

    conn = sqlite3.connect(demo_db)
    conn.row_factory = sqlite3.Row
    all_benefits_text = " ".join(r["benefits"] or "" for r in conn.execute("SELECT benefits FROM schemes"))

    scripts = [
        ["1", "2", "1"] + ["1"] * 12,
        ["1", "2", "2"] + ["2"] * 12,
        ["1", "1"] + ["1"] * 12,
    ]
    for script in scripts:
        state, actions = _run_to_presenting(demo_db, script)
        if state.phase != "presenting":
            continue
        spoken_text = next((a["say"] for a in actions if "say" in a), "")
        spoken_amounts = re.findall(r"Rs\s*[\d,]+", spoken_text)
        for amount in spoken_amounts:
            assert amount in all_benefits_text, f"invented amount {amount!r} in script {script}"


# ---------------------------------------------------------------------------
# K4: verified=0 schemes never state a deadline as fact in what's spoken.
# ---------------------------------------------------------------------------
def test_k4_unverified_scheme_deadline_never_spoken_as_fact(demo_db):
    """seed_demo.py marks every 5th scheme unverified with a real deadline
    sentence in its application text (H9's test fixture) - if such a
    scheme is ever presented, the deadline language must not appear in
    what's spoken, mirroring menu.py's own H9 stripping for the number-
    tree path applied here to the Q&A presenting path."""
    import sqlite3

    conn = sqlite3.connect(demo_db)
    conn.row_factory = sqlite3.Row
    unverified_slugs = {
        r["slug"] for r in conn.execute("SELECT slug FROM schemes WHERE verified = 0")
    }
    assert unverified_slugs, "fixture sanity: demo DB should have at least one verified=0 scheme"

    # Drive several paths and check every one that reaches presenting.
    scripts = [
        ["1", "2", "1"] + ["1"] * 12,
        ["1", "2", "2"] + ["2"] * 12,
        ["1", "1"] + ["1"] * 12,
        ["1", "3"] + ["1"] * 12,
    ]
    for script in scripts:
        state, actions = _run_to_presenting(demo_db, script)
        if state.phase != "presenting":
            continue
        spoken_text = next((a["say"] for a in actions if "say" in a), "")
        for phrase in ("last date", "deadline", "due date", "closing date"):
            assert phrase not in spoken_text.lower(), (
                f"deadline language {phrase!r} spoken for a possibly-unverified scheme: {spoken_text!r}"
            )


# ---------------------------------------------------------------------------
# K6: zero candidates says so honestly, invents nothing.
# ---------------------------------------------------------------------------
def test_k6_zero_candidates_presents_honest_no_match_message(demo_db):
    from haqdaar.engine import _enter_presenting
    from dataclasses import replace

    empty_state = CallState(candidates=())
    new_state, actions = _enter_presenting(empty_state, BANK, demo_db)
    spoken_text = next(a["say"] for a in actions if "say" in a)
    assert "nahi mili" in spoken_text or "koi yojna" in spoken_text.lower()
    # No scheme name should appear when there are zero candidates.
    assert "yeh yojnaayein mil sakti hain" not in spoken_text.lower()


# ---------------------------------------------------------------------------
# Presenting output is deterministic and safe with no LLM key at all -
# same discipline as L1 (system works fully offline), applied specifically
# to the new present.py-backed presenting phase.
# ---------------------------------------------------------------------------
def test_presenting_works_identically_with_no_llm_key(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    state, actions = _run_to_presenting(demo_db, ["1", "2", "1"] + ["1"] * 12)
    assert state.phase == "presenting"
    spoken_text = next(a["say"] for a in actions if "say" in a)
    assert isinstance(spoken_text, str)
    assert len(spoken_text) > 0
