"""Step 1 - the live call path. These are regression tests for three bugs
that only showed up on a real phone call and were invisible to the 599
tests that came before, because every one of those tests exercised the
ENGINE and none of them checked the TwiML the engine's actions turn into.

1. <Say language="None"> on every turn (invalid TwiML, Twilio rejects it).
2. The results turn exceeding Twilio's 15s webhook timeout, hanging up on
   the caller at the one moment the whole call was building towards.
3. Nothing persisted, so a call that misbehaved could not be diagnosed
   once it ended.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from haqdaar import calllog, config, present
from haqdaar.api import app
from haqdaar.narrow import Candidate
from haqdaar.twilio_adapter import _call_sid_to_call_id, _sessions

# Every language attribute Twilio actually accepts is a BCP-47 code. These
# are the values the buggy code emitted straight from CallState.language.
ILLEGAL_LANG_ATTRS = ['language="None"', 'language="hi"', 'language="en"', 'language="hinglish"']


@pytest.fixture(autouse=True)
def _isolated_sessions():
    _sessions.clear()
    _call_sid_to_call_id.clear()
    yield
    _sessions.clear()
    _call_sid_to_call_id.clear()


@pytest.fixture()
def client(monkeypatch, demo_db, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", demo_db)
    monkeypatch.setattr("haqdaar.twilio_adapter.config.DB_PATH", demo_db)
    monkeypatch.setattr("haqdaar.voice.CACHE_DIR", str(tmp_path / "tts"))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Bug 1: the wait message emitted an invalid language attribute every turn.
# ---------------------------------------------------------------------------
def test_wait_message_never_emits_an_invalid_language_attribute(client):
    """The regression: twilio_gather rendered CallState.language ("hi" /
    "en" / None) directly into <Say language="...">, so every single turn
    of every call carried a value Twilio rejects."""
    client.post("/twilio/voice", data={"CallSid": "CA100", "From": "+911", "To": "+912"})
    resp = client.post("/twilio/gather/CA100", data={"CallSid": "CA100", "Digits": "1"})

    assert resp.status_code == 200
    for illegal in ILLEGAL_LANG_ATTRS:
        assert illegal not in resp.text, f"invalid TwiML language attribute: {illegal}"


def test_no_turn_of_a_whole_call_emits_an_invalid_language_attribute(client):
    """Walks a real call and checks every response, not just the first -
    the bug was per-turn, so a single-turn assertion would have missed a
    reintroduction on any later turn."""
    responses = [client.post("/twilio/voice", data={"CallSid": "CA101", "From": "+911", "To": "+912"})]
    for digit in ["1", "2", "1", "1"]:
        responses.append(client.post("/twilio/gather/CA101", data={"CallSid": "CA101", "Digits": digit}))
        responses.append(client.post("/twilio/process/CA101", data={"CallSid": "CA101"}))

    for i, resp in enumerate(responses):
        for illegal in ILLEGAL_LANG_ATTRS:
            assert illegal not in resp.text, f"response {i} carried {illegal}"


# ---------------------------------------------------------------------------
# Bug 2: the results turn could take ~55s against Twilio's 15s timeout.
# ---------------------------------------------------------------------------
def _candidate(slug: str) -> Candidate:
    return Candidate(
        slug=slug, scheme_no=1, scheme_name=f"Scheme {slug}", name_short_hi=None,
        benefit_one_line=None, theme="business", verified=1, score=0.0,
    )


def test_present_many_stops_calling_the_llm_once_its_budget_is_spent(monkeypatch):
    """Five candidates x a slow LLM used to mean five sequential waits.
    With a budget, the first call is made, the budget is then found spent,
    and every remaining candidate falls back deterministically instead."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    calls = []

    def slow_caller(system, user):
        calls.append(user)
        time.sleep(0.3)
        return '{"spoken_name": "X", "benefit_line": "Rs 1000 for applicants."}'

    candidates = [_candidate(f"s{i}") for i in range(5)]
    benefits = {c.slug: "Rs 1000 for applicants." for c in candidates}

    start = time.monotonic()
    results = present.present_many(candidates, benefits, {}, llm_caller=slow_caller, budget_s=0.2)
    elapsed = time.monotonic() - start

    assert len(results) == 5, "every candidate must still get a presentation"
    assert len(calls) == 1, f"budget should have stopped after the first call, made {len(calls)}"
    # Worst case is budget + one in-flight call, nowhere near 5 x 0.3s.
    assert elapsed < 0.9, f"took {elapsed:.2f}s, budget was not enforced"
    assert all(r.benefit_line for r in results), "fallbacks must still say something real"


def test_present_many_with_no_budget_pressure_still_uses_the_llm(monkeypatch):
    """The budget must not disable the LLM path outright - a fast LLM
    should still get to shape the wording for every candidate."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    candidates = [_candidate(f"s{i}") for i in range(3)]
    benefits = {c.slug: "Rs 1000 for applicants." for c in candidates}

    results = present.present_many(
        candidates, benefits, {},
        llm_caller=lambda s, u: '{"spoken_name": "X", "benefit_line": "Rs 1000 for applicants."}',
        budget_s=10.0,
    )
    assert [r.source for r in results] == ["llm", "llm", "llm"]


# ---------------------------------------------------------------------------
# Bug 3: nothing was persisted, so a bad call could not be diagnosed later.
# ---------------------------------------------------------------------------
def test_call_transcript_records_both_sides(client, monkeypatch, tmp_path):
    monkeypatch.setattr(calllog, "LOG_DIR", str(tmp_path / "calls"))

    client.post("/twilio/voice", data={"CallSid": "CA200", "From": "+919", "To": "+918"})
    client.post("/twilio/gather/CA200", data={"CallSid": "CA200", "Digits": "1"})
    client.post("/twilio/process/CA200", data={"CallSid": "CA200"})

    written = list((tmp_path / "calls").rglob("CA200.jsonl"))
    assert written, "no transcript written for the call"
    records = [json.loads(line) for line in written[0].read_text(encoding="utf-8").splitlines()]

    assert records[0]["kind"] == "call_start"
    turns = [r for r in records if r["kind"] == "turn"]
    assert len(turns) >= 2

    # Our side: the exact lines spoken. Their side: the event we received.
    assert any(t["system_said"] for t in turns), "system's own words not recorded"
    assert any(t["caller"].get("dtmf") == "1" for t in turns), "caller's input not recorded"
    # Engine state, so a wrong answer can be traced to the state that caused it.
    assert all("candidate_count" in t and "phase" in t for t in turns)
    assert any(t["timings_ms"].get("total_ms") is not None for t in turns)


def test_calllog_never_raises_even_when_the_path_is_unwritable(monkeypatch):
    """A logging failure must never take down a live call."""
    monkeypatch.setattr(calllog, "LOG_DIR", "/proc/nonexistent-and-unwritable")
    calllog.log_turn(
        "CA300", turn=1, event={"dtmf": "1"}, said=["hi"], phase="asking",
        question_id="Q001", candidate_count=5,
    )  # must not raise
