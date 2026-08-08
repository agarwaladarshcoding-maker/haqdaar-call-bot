"""Step 10 - voice.py. SETUP_KEYS.md's fallback ladder: no key -> tts()
returns None (caller prints instead), stt() returns ("", 0.0) which
engine.py's existing unclear-speech ladder already treats correctly."""
import os

import httpx
import pytest

from haqdaar import voice


def test_tts_no_key_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "")
    monkeypatch.setattr(voice, "CACHE_DIR", str(tmp_path / "tts"))
    assert voice.tts("Namaste") is None


def test_tts_empty_text_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")
    monkeypatch.setattr(voice, "CACHE_DIR", str(tmp_path / "tts"))
    assert voice.tts("") is None


def test_stt_no_key_returns_empty_zero_confidence(monkeypatch):
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "")
    text, confidence = voice.stt(b"fake-audio-bytes")
    assert text == ""
    assert confidence == 0.0


def test_stt_empty_audio_returns_empty_zero_confidence(monkeypatch):
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")
    text, confidence = voice.stt(b"")
    assert text == ""
    assert confidence == 0.0


def test_tts_cache_hit_skips_network(monkeypatch, tmp_path):
    """Once a clip is cached, tts() must never call httpx again - proves
    the disk cache actually short-circuits the network path rather than
    just being written to and ignored."""
    cache_dir = tmp_path / "tts"
    monkeypatch.setattr(voice, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    path = voice._cache_path("Namaste", "hi-IN")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF-fake-wav-bytes")

    def _explode(*a, **kw):
        raise AssertionError("tts() hit the network despite a cache hit")

    monkeypatch.setattr(httpx, "post", _explode)
    result = voice.tts("Namaste", "hi-IN")
    assert result == b"RIFF-fake-wav-bytes"


def test_tts_writes_cache_on_success(monkeypatch, tmp_path):
    cache_dir = tmp_path / "tts"
    monkeypatch.setattr(voice, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    import base64

    fake_audio = base64.b64encode(b"RIFF-generated").decode("utf-8")

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"audios": [fake_audio]}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResp())
    result = voice.tts("Kuch bhi", "hi-IN")
    assert result == b"RIFF-generated"

    path = voice._cache_path("Kuch bhi", "hi-IN")
    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == b"RIFF-generated"


def test_tts_network_failure_returns_none_not_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(voice, "CACHE_DIR", str(tmp_path / "tts"))
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    def _raise(*a, **kw):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "post", _raise)
    assert voice.tts("Namaste") is None


def test_tts_malformed_response_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(voice, "CACHE_DIR", str(tmp_path / "tts"))
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"audios": []}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResp())
    assert voice.tts("Namaste") is None


def test_stt_success_returns_transcript_with_full_confidence(monkeypatch):
    """Sarvam's saarika:v2.5 response has no confidence field (confirmed
    against the live API) - a successful call with a non-empty transcript
    reports confidence 1.0; engine.py's own vocabulary matching is the
    real quality gate, not this number."""
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"transcript": "haan", "language_code": "hi-IN"}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResp())
    text, confidence = voice.stt(b"real-audio-bytes")
    assert text == "haan"
    assert confidence == pytest.approx(1.0)


def test_stt_empty_transcript_returns_zero_confidence(monkeypatch):
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"transcript": "", "language_code": "hi-IN"}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResp())
    text, confidence = voice.stt(b"real-audio-bytes")
    assert text == ""
    assert confidence == 0.0


def test_stt_network_failure_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "fake-key")

    def _raise(*a, **kw):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", _raise)
    text, confidence = voice.stt(b"audio")
    assert text == ""
    assert confidence == 0.0


def test_cache_path_differs_by_language():
    p1 = voice._cache_path("Namaste", "hi-IN")
    p2 = voice._cache_path("Namaste", "en-IN")
    assert p1 != p2


def test_cache_path_is_deterministic():
    assert voice._cache_path("same text", "hi-IN") == voice._cache_path("same text", "hi-IN")
