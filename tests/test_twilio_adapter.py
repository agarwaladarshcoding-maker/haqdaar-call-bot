"""Step 11 - twilio_adapter.py. Drives the real FastAPI app (same app.py
mount used in production) with a real TestClient so we're testing the
actual webhook contract, not a reimplementation of it. No real Twilio
account or network reached anywhere - TWILIO_AUTH_TOKEN stays unset in
every test unless a test is specifically exercising signature checking."""
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from haqdaar import config
from haqdaar.api import app
from haqdaar.twilio_adapter import _sessions, _call_sid_to_call_id, verify_twilio_signature


@pytest.fixture(autouse=True)
def _isolated_sessions():
    """twilio_adapter.py's session dicts are module-level, like api.py's -
    clear them before and after every test so tests can't see each
    other's calls."""
    _sessions.clear()
    _call_sid_to_call_id.clear()
    yield
    _sessions.clear()
    _call_sid_to_call_id.clear()


@pytest.fixture()
def client(monkeypatch, demo_db, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", demo_db)
    monkeypatch.setattr("haqdaar.twilio_adapter.config.DB_PATH", demo_db)
    # Isolate from the real cache/tts/ dir - voice.tts() checks the disk
    # cache BEFORE checking for a key, so a real cached clip from manual
    # testing would otherwise leak real audio into tests that expect the
    # no-key <Say> fallback.
    monkeypatch.setattr("haqdaar.voice.CACHE_DIR", str(tmp_path / "tts"))
    return TestClient(app)


def _twiml_root(response):
    from xml.etree import ElementTree as ET

    assert response.status_code == 200, response.text
    return ET.fromstring(response.content)

def _post_gather(client, url: str, data: dict):
    resp = client.post(url, data=data)
    assert resp.status_code == 200
    root = _twiml_root(resp)
    redirect = root.find(".//Redirect")
    if redirect is not None:
        process_url = redirect.text
        resp = client.post(process_url, data=data)
    return resp


def test_voice_webhook_starts_a_call_and_returns_twiml(client):
    """No SARVAM_API_KEY (blanked by conftest's hermeticity fixture) ->
    the documented fallback: <Say>, not <Play> of a nonexistent clip."""
    resp = client.post("/twilio/voice", data={"CallSid": "CA001", "From": "+911111111111", "To": "+912222222222"})
    assert resp.status_code == 200
    root = _twiml_root(resp)
    assert root.tag == "Response"
    says = root.findall(".//Say")
    assert len(says) >= 1
    assert says[0].text  # real prompt text, not empty
    gather = root.find(".//Gather")
    assert gather is not None
    assert gather.get("action")


def test_voice_webhook_missing_callsid_is_rejected(client):
    resp = client.post("/twilio/voice", data={"From": "+911111111111"})
    assert resp.status_code == 400


def test_gather_unknown_call_sid_404s(client):
    resp = client.post("/twilio/gather/CA_never_started", data={"CallSid": "CA_never_started", "Digits": "1"})
    assert resp.status_code == 404


def test_full_call_completes_via_twiml_digits(client):
    """Mirrors sim.py's own smoke test but through the Twilio surface:
    drives a whole call via <Gather>/Digits and must reach <Hangup/>."""
    call_sid = "CA_full"
    resp = client.post("/twilio/voice", data={"CallSid": call_sid, "From": "+91", "To": "+91"})
    root = _twiml_root(resp)
    assert root.find(".//Gather") is not None

    # language=Hindi, path=find schemes for me, then answer a long run of
    # question 1s - demo DB's 20 schemes narrow down within the 10-question
    # cap regardless of exact answers, same assumption test_sim.py makes.
    script = ["1", "2"] + ["1"] * 12
    ended = False
    for key in script:
        resp = _post_gather(client, f"/twilio/gather/{call_sid}", data={"CallSid": call_sid, "Digits": key})
        assert resp.status_code == 200
        root = _twiml_root(resp)
        if root.find(".//Hangup") is not None:
            ended = True
            break
    assert ended, "call never reached Hangup within the scripted turns"


def test_silence_produces_timeout_event_not_error(client):
    call_sid = "CA_silence"
    client.post("/twilio/voice", data={"CallSid": call_sid, "From": "+91", "To": "+91"})
    resp = _post_gather(client, f"/twilio/gather/{call_sid}?silence=1", data={"CallSid": call_sid})
    assert resp.status_code == 200
    root = _twiml_root(resp)
    # silence ladder's first rung is a nudge, not an immediate hangup -
    # some spoken content must be present, as <Say> (no key) or <Play>
    assert root.find(".//Say") is not None or root.find(".//Play") is not None


def test_voice_webhook_uses_play_when_sarvam_key_present(client, monkeypatch, tmp_path):
    """With a real SARVAM_API_KEY and a cache hit, the adapter must emit
    <Play> of a real clip URL, not Twilio's own <Say> engine - this is
    the actual bug fix (Twilio's hi-IN <Say> was found to mispronounce
    mixed Hindi/English prompts on the first live call)."""
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key-for-cache-hit")
    monkeypatch.setattr("haqdaar.voice.CACHE_DIR", str(tmp_path / "tts"))

    from haqdaar import voice as voice_module

    prompt = "Namaste. Hindi ke liye 1 dabaiye. For English, press 2. Dono ke liye 3 dabaiye."
    path = voice_module._cache_path(prompt, "hi-IN")
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF-fake-cached-clip")

    resp = client.post("/twilio/voice", data={"CallSid": "CA_play", "From": "+91", "To": "+91"})
    assert resp.status_code == 200
    root = _twiml_root(resp)
    play = root.find(".//Play")
    assert play is not None, "expected <Play> when a cached Sarvam clip exists"
    assert "/twilio/audio/" in play.text
    assert root.find(".//Say") is None


def test_audio_endpoint_serves_cached_clip(client, monkeypatch, tmp_path):
    monkeypatch.setattr("haqdaar.voice.CACHE_DIR", str(tmp_path / "tts"))
    import os

    os.makedirs(tmp_path / "tts", exist_ok=True)
    with open(tmp_path / "tts" / "abc123.wav", "wb") as f:
        f.write(b"RIFF-test-audio-bytes")

    resp = client.get("/twilio/audio/abc123.wav")
    assert resp.status_code == 200
    assert resp.content == b"RIFF-test-audio-bytes"
    assert resp.headers["content-type"] == "audio/wav"


def test_audio_endpoint_rejects_path_traversal(client):
    resp = client.get("/twilio/audio/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_audio_endpoint_rejects_non_wav(client):
    resp = client.get("/twilio/audio/not-a-wav-file.txt")
    assert resp.status_code == 400


def test_audio_endpoint_404s_on_missing_clip(client, monkeypatch, tmp_path):
    monkeypatch.setattr("haqdaar.voice.CACHE_DIR", str(tmp_path / "tts"))
    resp = client.get("/twilio/audio/does-not-exist.wav")
    assert resp.status_code == 404


def test_speech_result_is_translated_to_speech_event(client):
    call_sid = "CA_speech"
    client.post("/twilio/voice", data={"CallSid": call_sid, "From": "+91", "To": "+91"})
    resp = _post_gather(
        client,
        f"/twilio/gather/{call_sid}",
        data={"CallSid": call_sid, "SpeechResult": "hindi"},
    )
    assert resp.status_code == 200
    _twiml_root(resp)  # must be valid XML either way


def test_status_webhook_cleans_up_session(client):
    """No {call_sid} in the URL - Twilio always sends CallSid as a form
    field, which is what lets this same StatusCallback URL work for
    outbound calls too (no CallSid exists yet when the URL is configured)."""
    call_sid = "CA_status"
    client.post("/twilio/voice", data={"CallSid": call_sid, "From": "+91", "To": "+91"})
    assert call_sid in _call_sid_to_call_id
    resp = client.post("/twilio/status", data={"CallSid": call_sid, "CallStatus": "completed"})
    assert resp.status_code == 200
    assert resp.json()["cleaned_up"] is True
    assert call_sid not in _call_sid_to_call_id


def test_status_webhook_on_unknown_call_sid_is_a_noop_success(client):
    resp = client.post("/twilio/status", data={"CallSid": "CA_never_existed", "CallStatus": "completed"})
    assert resp.status_code == 200
    assert resp.json()["cleaned_up"] is False


def test_status_webhook_missing_callsid_is_a_noop_success(client):
    resp = client.post("/twilio/status", data={"CallStatus": "completed"})
    assert resp.status_code == 200
    assert resp.json()["cleaned_up"] is False


# ---------------------------------------------------------------------------
# Signature verification (M15-adjacent: don't trust unsigned webhooks once
# a real auth token is configured).
# ---------------------------------------------------------------------------
def test_signature_verification_passes_when_no_token_configured(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    assert verify_twilio_signature("https://example.com/twilio/voice", {"CallSid": "CA1"}, None) is True


def test_signature_verification_fails_on_missing_signature_when_token_set(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "real-token")
    assert verify_twilio_signature("https://example.com/twilio/voice", {"CallSid": "CA1"}, None) is False


def test_signature_verification_fails_on_wrong_signature(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "real-token")
    assert verify_twilio_signature("https://example.com/twilio/voice", {"CallSid": "CA1"}, "bogus") is False


def test_signature_verification_passes_with_correctly_computed_signature(monkeypatch):
    import base64
    import hashlib
    import hmac

    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "real-token")
    url = "https://example.com/twilio/voice"
    params = {"CallSid": "CA1", "From": "+911"}
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(b"real-token", data.encode("utf-8"), hashlib.sha1).digest()
    sig = base64.b64encode(digest).decode("utf-8")
    assert verify_twilio_signature(url, params, sig) is True


def test_voice_webhook_rejects_bad_signature_when_token_configured(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "real-token")
    monkeypatch.setattr("haqdaar.twilio_adapter.config.TWILIO_AUTH_TOKEN", "real-token")
    resp = client.post(
        "/twilio/voice",
        data={"CallSid": "CA_bad_sig", "From": "+91", "To": "+91"},
        headers={"X-Twilio-Signature": "not-a-real-signature"},
    )
    assert resp.status_code == 403
