"""Sarvam TTS/STT with disk cache and no-key fallback. Step 10.

Every prompt in question_bank.yaml is static text, so TTS output is cached
to disk by content hash on first generation - after one warm-up run
(scripts/warm_cache.py) the system makes almost no live TTS calls, which
also removes Sarvam's rate limit as a demo risk (SETUP_KEYS.md).

With no SARVAM_API_KEY: tts() returns None (caller prints the text instead
of playing audio) and stt() returns ("", 0.0) - a confidence of 0.0 is
already below any real threshold, so engine.py's existing "unclear speech"
ladder handles it exactly like a low-confidence real STT result, with zero
new engine code (BUILD_STEPS.md Step 11's rule - "add nothing to the
engine" - applies here too, even though this is Step 10).

TTS_MODEL/TTS_SPEAKER/8kHz sample rate: the first real Twilio call surfaced
two real bugs traced back to this being wrong. (1) Audio was generated at
Sarvam's default 22050 Hz - Twilio telephony audio is 8kHz narrowband
(SETUP_KEYS.md's own Twilio section already warned "TTS that sounds clean
on a laptop can turn to mush on a handset"), so Twilio was downsampling on
the fly for every <Play>, and a caller heard "dabaiye" mangled into
something like "dabii". Now requesting speech_sample_rate=8000 directly
from Sarvam avoids that resampling entirely, and produces much smaller
files as a side effect (faster to fetch/play - also helps the reported
latency). (2) Bumped to bulbul:v3 (the latest model, each Sarvam TTS model
version has its own speaker roster - "anushka" isn't on v3's, "priya" is).
"""
from __future__ import annotations

import hashlib
import os

import httpx

from haqdaar import config

CACHE_DIR = os.path.join("cache", "tts")
_BASE_URL = "https://api.sarvam.ai"
TTS_MODEL = "bulbul:v3"
TTS_SPEAKER = "priya"
TTS_SAMPLE_RATE = 8000  # telephony narrowband, not Sarvam's 22050 default
TTS_PACE = 0.85  # slower than Sarvam's default 1.0 - first live call felt rushed


class VoiceError(Exception):
    pass


def _cache_path(text: str, lang: str) -> str:
    # Cache key includes model/speaker/rate so switching any of them (as
    # happened once already) can't silently serve a stale clip generated
    # under different settings - it just misses and regenerates.
    key = f"{TTS_MODEL}:{TTS_SPEAKER}:{TTS_SAMPLE_RATE}:{lang}:{text}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.wav")


def tts(text: str, lang: str | None = None) -> bytes | None:
    """Returns WAV audio bytes, or None if no key is configured or the call
    fails - callers must treat None as "print the text instead", never as
    an error to propagate (SETUP_KEYS.md's documented fallback ladder)."""
    if not text:
        return None
    lang = lang or config.SARVAM_TTS_LANG

    path = _cache_path(text, lang)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()

    if not config.SARVAM_API_KEY:
        return None

    try:
        resp = httpx.post(
            f"{_BASE_URL}/text-to-speech",
            headers={"api-subscription-key": config.SARVAM_API_KEY},
            json={
                "inputs": [text],
                "target_language_code": lang,
                "speaker": TTS_SPEAKER,
                "model": TTS_MODEL,
                "speech_sample_rate": TTS_SAMPLE_RATE,
                "pace": TTS_PACE,
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        body = resp.json()
        audio_b64 = (body.get("audios") or [None])[0]
        if not audio_b64:
            return None
        import base64

        audio = base64.b64decode(audio_b64)
    except (httpx.HTTPError, ValueError, KeyError):
        return None

    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "wb") as f:
        f.write(audio)
    os.replace(tmp_path, path)
    return audio


def stt(audio: bytes, lang: str | None = None) -> tuple[str, float]:
    """Returns (transcript, confidence). No key, empty audio, or any call
    failure -> ("", 0.0), which engine.py's existing unclear-speech ladder
    (E1-E4) already routes to a retry then a drop to DTMF-only - the same
    path a real low-confidence Sarvam result would take.

    Sarvam's saarika:v2.5 response has no confidence field at all (just
    transcript/language_code/request_id, confirmed against the live API,
    not assumed) - a successful call with a non-empty transcript is
    reported as confidence 1.0, and engine.py's own text-matching against
    each question's valid answer vocabulary is the real quality gate
    (already proven correct: an out-of-vocabulary transcript like "haan"
    on a question that doesn't accept it routes to "unclear" regardless
    of the confidence value passed in)."""
    if not audio or not config.SARVAM_API_KEY:
        return "", 0.0
    lang = lang or config.SARVAM_STT_LANG

    try:
        resp = httpx.post(
            f"{_BASE_URL}/speech-to-text",
            headers={"api-subscription-key": config.SARVAM_API_KEY},
            files={"file": ("audio.wav", audio, "audio/wav")},
            data={"language_code": lang, "model": "saarika:v2.5"},
            timeout=5.0,
        )
        resp.raise_for_status()
        body = resp.json()
        transcript = body.get("transcript") or ""
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return "", 0.0

    return transcript, (1.0 if transcript else 0.0)
