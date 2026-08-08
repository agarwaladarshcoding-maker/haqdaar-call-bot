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


# ---------------------------------------------------------------------------
# Step 2: speech now goes through <Record> + Sarvam, not Twilio's recognizer.
# ---------------------------------------------------------------------------
def _start_call(client, sid):
    client.post("/twilio/voice", data={"CallSid": sid, "From": "+911", "To": "+912"})
    # Answer the language question so the next one accepts speech.
    client.post(f"/twilio/gather/{sid}", data={"CallSid": sid, "Digits": "1"})
    return client.post(f"/twilio/process/{sid}", data={"CallSid": sid})


def test_speech_question_emits_record_pointing_at_the_sarvam_endpoint(client):
    resp = _start_call(client, "CA400")
    assert "<Record" in resp.text, "speech question must record for Sarvam to transcribe"
    assert "/twilio/recording/CA400" in resp.text
    assert 'finishOnKey="0123456789*#"' in resp.text, "a keypress must still end the recording"
    # The barge-in Gather must be short - it is only the window before the
    # Record takes over, not the old 15s wait that made callers feel unheard.
    assert 'timeout="2"' in resp.text


def test_speech_disabled_gather_has_no_record_and_keeps_the_silence_redirect():
    """A buttons-only question must keep the plain Gather path: no
    <Record>, and the silence <Redirect> that the Record would otherwise
    replace.

    Driven through _actions_to_twiml directly rather than through a bank
    question, because no question in the bank disables speech any more -
    Q001_LANGUAGE was the only one and it has been removed. The code path
    is still live and still needs covering; binding the test to a question
    that no longer exists would only have deleted the coverage."""
    from haqdaar.twilio_adapter import _actions_to_twiml

    twiml = _actions_to_twiml(
        [{"say": "buttons only"}, {"gather": {"digits": 1, "speech": False}}],
        "https://x/twilio/gather/CA1", "https://x", "hi", "https://x/twilio/recording/CA1",
    )
    assert "<Record" not in twiml
    assert "silence=1" in twiml
    assert 'timeout="15"' in twiml, "no Record to fall through to, so keep the long wait"


def test_recording_webhook_transcribes_with_sarvam_and_feeds_the_engine(client, monkeypatch):
    """The whole point of Step 2: voice.stt() was dead code, and Twilio's
    own recognizer (which cannot handle mixed Hindi/English) was doing the
    transcription. This asserts our transcript reaches the engine."""
    _start_call(client, "CA402")
    monkeypatch.setattr("haqdaar.twilio_adapter._fetch_recording", lambda url: b"FAKEWAV")
    monkeypatch.setattr("haqdaar.voice.stt", lambda audio, lang=None: ("main kisan hoon", 0.93))

    resp = client.post(
        "/twilio/recording/CA402",
        data={"CallSid": "CA402", "RecordingUrl": "https://api.twilio.com/rec1", "RecordingDuration": "3"},
    )
    assert resp.status_code == 200
    # The recording turn plays the wait message and bounces to /process.
    assert "/twilio/process/CA402" in resp.text

    from haqdaar.twilio_adapter import _call_sid_to_call_id, _pending_events
    event = _pending_events[_call_sid_to_call_id["CA402"]]
    assert event["speech"] == "main kisan hoon"
    assert event["confidence"] == 0.93


def test_recording_webhook_treats_a_keypress_as_dtmf_not_speech(client, monkeypatch):
    """finishOnKey means a caller can press a button mid-recording. That
    must become a dtmf event, and must not cost a Sarvam call."""
    _start_call(client, "CA403")
    called = []
    monkeypatch.setattr("haqdaar.voice.stt", lambda *a, **k: called.append(1) or ("", 0.0))

    client.post(
        "/twilio/recording/CA403",
        data={"CallSid": "CA403", "Digits": "2", "RecordingUrl": "https://api.twilio.com/r", "RecordingDuration": "1"},
    )
    from haqdaar.twilio_adapter import _call_sid_to_call_id, _pending_events
    assert _pending_events[_call_sid_to_call_id["CA403"]] == {"dtmf": "2"}
    assert not called, "a keypress must not trigger transcription"


def test_empty_recording_reports_real_seconds_not_the_gather_timeout(client):
    """engine.py's silence ladder counts REAL cumulative seconds (5/15/25/
    30). An empty <Record> is ~4s of silence, not 15 - reporting 15 would
    end the call after two thoughtful pauses."""
    _start_call(client, "CA404")
    client.post(
        "/twilio/recording/CA404",
        data={"CallSid": "CA404", "RecordingUrl": "", "RecordingDuration": "0"},
    )
    from haqdaar.twilio_adapter import SILENCE_AFTER_RECORD, _call_sid_to_call_id, _pending_events
    assert _pending_events[_call_sid_to_call_id["CA404"]] == {"timeout": SILENCE_AFTER_RECORD}
    assert SILENCE_AFTER_RECORD < 15


def test_hindi_benefit_is_preferred_when_translated(monkeypatch):
    """scripts/translate_benefits.py fills benefit_one_line_hi. The call's
    convention is English scheme name + Hindi benefit, so the Hindi line
    must win over the English source sentence when it exists."""
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    c = Candidate(
        slug="s1", scheme_no=1, scheme_name="Coir Training Scheme", name_short_hi="Coir Training",
        benefit_one_line=None, theme="craft", verified=1, score=0.0,
        benefit_one_line_hi="Trainees ko Rs. 2,500 per month ka stipend milta hai.",
    )
    p = present.present_many([c], {"s1": "Trainees get a stipend of Rs. 2,500 per month."}, {})[0]
    assert p.spoken_name == "Coir Training", "the name stays English"
    assert p.benefit_line.startswith("Trainees ko"), "the benefit must be the Hindi line"


def test_english_benefit_used_when_no_translation_exists(monkeypatch):
    """A row whose translation failed the number-survival check has an
    empty benefit_one_line_hi. Speaking the English source is always safe;
    speaking a wrong amount is not."""
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    c = _candidate("s2")  # benefit_one_line_hi defaults to None
    p = present.present_many([c], {"s2": "Stipend of Rs 7,000 per month."}, {})[0]
    assert p.benefit_line == "Stipend of Rs 7,000 per month."


def test_failed_recording_fetch_degrades_to_unclear_not_a_crash(client, monkeypatch):
    """Twilio unreachable, or no credentials: must behave exactly like an
    unclear utterance, which engine.py's ladder already handles."""
    _start_call(client, "CA405")
    monkeypatch.setattr("haqdaar.twilio_adapter._fetch_recording", lambda url: None)

    resp = client.post(
        "/twilio/recording/CA405",
        data={"CallSid": "CA405", "RecordingUrl": "https://api.twilio.com/r", "RecordingDuration": "3"},
    )
    assert resp.status_code == 200
    from haqdaar.twilio_adapter import _call_sid_to_call_id, _pending_events
    event = _pending_events[_call_sid_to_call_id["CA405"]]
    assert event["speech"] == "" and event["confidence"] == 0.0
