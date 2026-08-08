"""Twilio voice webhook adapter. Step 11.

Translates Twilio's form-encoded webhooks into engine events and engine
actions into TwiML. Adds nothing to the engine - this module is a second
client of api.py's session store and step()-calling logic, exactly the way
sim.py is a client of the same engine via HTTP (BUILD_STEPS.md: "If the
adapter needs new engine behaviour, the engine is wrong").

No `twilio` SDK dependency: TwiML is a small, stable XML dialect and the
inbound webhook is just a standard form POST, so hand-rolling both sides
avoids a dependency that would otherwise sit unused without a real Twilio
account configured (SETUP_KEYS.md: keys are never required to build or run
this repo).

Call flow:
  Twilio POSTs to /twilio/voice on an incoming call (no call_id yet - we
  start one and remember it against Twilio's own CallSid).
  Twilio POSTs to /twilio/gather/{call_sid} with Digits or SpeechResult
  after every <Gather>, which we translate to a step() event.
  Silence (a <Gather> that times out with nothing entered) POSTs to the
  same endpoint with neither Digits nor SpeechResult - translated to a
  {"timeout": N} event, matching sim.py's !silence and engine.py's own
  silence ladder.

Speech: every {"say"} action is rendered as <Play> of a real Sarvam TTS
clip (voice.py's disk cache, same cache scripts/warm_cache.py fills), not
Twilio's own built-in <Say> engine - Twilio's hi-IN voice was found (first
live call) to mispronounce mixed Hindi/English prompts (reading "1" as the
English word "one" mid-Hindi-sentence). <Say> is kept only as a last-resort
fallback for a single turn when voice.tts() itself returns None (no
SARVAM_API_KEY, or a live call failure) - same fallback-ladder discipline
as every other optional service in this codebase.

Verification (M15/security): every inbound request's X-Twilio-Signature
is checked against TWILIO_AUTH_TOKEN before anything else runs, UNLESS no
auth token is configured at all (local/simulator testing without a real
Twilio account) - a configured token that fails verification is always
rejected, never silently accepted.

Live terminal visibility: every webhook hit, the event it was translated
to, and the TwiML action it produced are printed to stdout as they happen
(not just logged to a file), so a person driving a manual test call can
watch the call unfold turn by turn in the same terminal running uvicorn.
A full per-call diagnostic report (every turn, candidate counts, final
outcome) is printed when the call ends (Hangup action or Twilio's own
status-callback), the terminal/voice equivalent of sim.py's --trace.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response

def _get_external_url(request: Request) -> str:
    url_str = str(request.url)
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host")
    if proto and proto.lower() == "https" and url_str.startswith("http://"):
        url_str = url_str.replace("http://", "https://", 1)
    if host:
        # replace the netloc with the forwarded host
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url_str)
        url_str = urlunparse(parsed._replace(netloc=host))
    return url_str

from haqdaar import config, voice
from haqdaar.bank import Bank, BankError, load_bank
from haqdaar.engine import CallState, step

router = APIRouter(prefix="/twilio", tags=["twilio"])

_lock = threading.Lock()
_sessions: dict[str, CallState] = {}          # call_id -> state
_call_sid_to_call_id: dict[str, str] = {}     # Twilio CallSid -> our call_id
_pending_events: dict[str, dict[str, Any]] = {}     # Twilio CallSid -> event
_call_logs: dict[str, "CallLog"] = {}         # call_id -> diagnostic log

_bank: Bank | None = None
_bank_error: str | None = None


@dataclass
class CallTurn:
    event: dict
    actions: list[dict]
    candidate_count: int
    question_id: str | None
    phase: str


@dataclass
class CallLog:
    call_sid: str
    started_at: float = field(default_factory=time.time)
    turns: list[CallTurn] = field(default_factory=list)


def _get_bank() -> Bank:
    global _bank, _bank_error
    if _bank is None and _bank_error is None:
        try:
            _bank = load_bank()
        except BankError as e:
            _bank_error = str(e)
    if _bank is None:
        raise HTTPException(status_code=503, detail=f"question bank failed to load: {_bank_error}")
    return _bank


def verify_twilio_signature(url: str, params: dict[str, str], signature: str | None) -> bool:
    """Twilio's documented HMAC-SHA1 request-signing scheme. Returns True
    when no auth token is configured (nothing to verify against - the
    simulator/no-key path), False on any mismatch or missing signature
    when a token IS configured."""
    if not config.TWILIO_AUTH_TOKEN:
        return True
    if not signature:
        return False
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(config.TWILIO_AUTH_TOKEN.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _log_line(call_sid: str, msg: str) -> None:
    print(f"  \033[36m[{call_sid[:12]}]\033[0m {msg}", flush=True)


def _print_call_report(log: "CallLog") -> None:
    duration = time.time() - log.started_at
    print("", flush=True)
    print("=" * 70, flush=True)
    print(f"CALL REPORT  {log.call_sid}  ({duration:.1f}s, {len(log.turns)} turns)", flush=True)
    print("=" * 70, flush=True)
    for i, turn in enumerate(log.turns, 1):
        event_desc = _describe_event(turn.event)
        print(f"  turn {i:>2}: {event_desc:<28} phase={turn.phase:<11} "
              f"candidates={turn.candidate_count:>3}  question={turn.question_id or '-'}", flush=True)
        for a in turn.actions:
            if "say" in a:
                preview = a["say"][:90] + ("..." if len(a["say"]) > 90 else "")
                print(f"           say: {preview}", flush=True)
    final = log.turns[-1] if log.turns else None
    outcome = "ENDED" if final and final.phase == "ended" else f"INCOMPLETE (stuck at {final.phase if final else 'start'})"
    print(f"  outcome: {outcome}", flush=True)
    print("=" * 70, flush=True)
    print("", flush=True)


def _describe_event(event: dict) -> str:
    if "dtmf" in event:
        return f"dtmf={event['dtmf']!r}"
    if "speech" in event:
        return f"speech={event['speech']!r} conf={event.get('confidence')}"
    if "timeout" in event:
        return f"silence(timeout={event['timeout']})"
    if event.get("hangup"):
        return "hangup"
    return "start" if not event else str(event)


def _sarvam_lang(engine_language: str | None) -> str:
    """CallState.language is "hi"/"en"/None (engine.py) - Sarvam and
    Twilio's own <Gather>/<Say> both want a BCP-47-ish code."""
    return "en-IN" if engine_language == "en" else "hi-IN"


def _actions_to_twiml(actions: list[dict], gather_action_url: str, audio_base_url: str, language: str | None) -> str:
    """{"say"} -> <Play> of a real Sarvam clip when available, <Say>
    fallback otherwise. {"gather"} -> <Gather> wrapping any preceding
    audio. {"end"} -> <Hangup>.

    `language` is the call's actual selected language (state.language,
    "hi"/"en"/None-before-chosen) - previously this whole function
    silently assumed hi-IN everywhere, which meant an English-speaking
    caller would still get audio generated/cached under the wrong
    language key and Twilio's <Gather> would listen for Hindi speech
    regardless of what the caller actually chose."""
    lang_code = _sarvam_lang(language)
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<Response>"]
    pending_says: list[str] = []

    def flush_says():
        for text in pending_says:
            audio = voice.tts(text, lang_code)
            if audio:
                digest = voice._cache_path(text, lang_code)
                clip_id = digest.rsplit("/", 1)[-1]
                parts.append(f'<Play>{escape(audio_base_url)}/twilio/audio/{clip_id}</Play>')
            else:
                parts.append(f'<Say language="{lang_code}">{escape(text)}</Say>')
        pending_says.clear()

    for a in actions:
        if "say" in a:
            pending_says.append(a["say"])
        elif "gather" in a:
            g = a["gather"]
            digits = g.get("digits", 1)
            speech = g.get("speech", True)
            input_types = "dtmf speech" if speech else "dtmf"
            # Bug fix (first live call): no language attribute here meant
            # Twilio's OWN speech recognizer defaulted to en-US and tried
            # to parse Hindi speech, which is why it seemed to "not hear"
            # the caller at all - it was mishearing everything.
            parts.append(
                f'<Gather input="{input_types}" numDigits="{digits}" timeout="15" '
                f'language="{lang_code}" '
                f'action="{escape(gather_action_url)}" method="POST">'
            )
            flush_says()
            parts.append("</Gather>")
            # Twilio only fires the webhook if the caller enters something;
            # falling through here (nothing entered) replays the same
            # <Gather> as a silence retry, matching engine.py's own ladder.
            parts.append(f'<Redirect method="POST">{escape(gather_action_url)}?silence=1</Redirect>')
        elif a.get("end"):
            flush_says()
            parts.append("<Hangup/>")

    flush_says()
    parts.append("</Response>")
    return "".join(parts)


def _record_turn(call_id: str, call_sid: str, event: dict, new_state: CallState, actions: list[dict]) -> None:
    log = _call_logs.get(call_id)
    if log is None:
        return
    log.turns.append(CallTurn(
        event=event,
        actions=actions,
        candidate_count=len(new_state.candidates),
        question_id=new_state.current_question,
        phase=new_state.phase,
    ))
    _log_line(call_sid, f"{_describe_event(event)} -> phase={new_state.phase} candidates={len(new_state.candidates)}")
    for a in actions:
        if "say" in a:
            preview = a["say"][:80] + ("..." if len(a["say"]) > 80 else "")
            _log_line(call_sid, f'  SAY: "{preview}"')
    if new_state.phase == "ended":
        _print_call_report(log)


def _step_and_render(call_id: str, call_sid: str, event: dict, gather_url: str, audio_base_url: str) -> Response:
    bank = _get_bank()
    with _lock:
        state = _sessions.get(call_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown call_id: {call_id}")
        new_state, actions = step(state, event, bank, config.DB_PATH)
        _sessions[call_id] = new_state
    _record_turn(call_id, call_sid, event, new_state, actions)
    twiml = _actions_to_twiml(actions, gather_url, audio_base_url, new_state.language)
    return Response(content=twiml, media_type="application/xml")


@router.post("/voice")
async def twilio_voice(request: Request) -> Response:
    """Entry point for a new inbound call. Twilio sends CallSid, From, To
    as form fields; we mint our own call_id and remember the mapping."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    external_url = _get_external_url(request)
    if not verify_twilio_signature(external_url, params, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

    call_sid = params.get("CallSid")
    if not call_sid:
        raise HTTPException(status_code=400, detail="missing CallSid")

    _log_line(call_sid, f"NEW CALL from={params.get('From')} to={params.get('To')}")

    bank = _get_bank()
    call_id = str(uuid.uuid4())
    state = CallState()
    new_state, actions = step(state, {}, bank, config.DB_PATH)
    with _lock:
        _sessions[call_id] = new_state
        _call_sid_to_call_id[call_sid] = call_id
        _call_logs[call_id] = CallLog(call_sid=call_sid)
    _record_turn(call_id, call_sid, {}, new_state, actions)

    # Build gather_url and audio_base_url using the external URL's origin
    from urllib.parse import urlparse
    parsed = urlparse(external_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    gather_url = f"{base_url}/twilio/gather/{call_sid}"
    audio_base_url = base_url
    twiml = _actions_to_twiml(actions, gather_url, audio_base_url, new_state.language)
    return Response(content=twiml, media_type="application/xml")


@router.post("/gather/{call_sid}")
async def twilio_gather(
    call_sid: str,
    request: Request,
    Digits: str | None = Form(default=None),
    SpeechResult: str | None = Form(default=None),
    Confidence: float | None = Form(default=None),
) -> Response:
    """Fires after every <Gather> completes, and (via the <Redirect> in
    _actions_to_twiml) also on silence, distinguished by the ?silence=1
    query param sim.py's !silence event mirrors."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    external_url = _get_external_url(request)
    if not verify_twilio_signature(external_url, params, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

    with _lock:
        call_id = _call_sid_to_call_id.get(call_sid)
    if call_id is None:
        raise HTTPException(status_code=404, detail=f"unknown CallSid: {call_sid}")

    from urllib.parse import urlparse
    parsed = urlparse(_get_external_url(request))
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    gather_url = f"{base_url}/twilio/gather/{call_sid}"
    audio_base_url = base_url

    if request.query_params.get("silence"):
        event: dict[str, Any] = {"timeout": 15}
    elif Digits:
        event = {"dtmf": Digits}
    elif SpeechResult:
        event = {"speech": SpeechResult, "confidence": Confidence if Confidence is not None else 0.5}
    else:
        event = {"timeout": 15}

    with _lock:
        _pending_events[call_id] = event
        state = _sessions[call_id]
        language = state.language

    process_url = f"{base_url}/twilio/process/{call_sid}"
    
    # "Please wait" depends on the current language
    if language == "en-IN":
        wait_text = "Please wait..."
    else:
        wait_text = "Kripaya pratiksha karein..."

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="{language}">{wait_text}</Say>
    <Redirect method="POST">{process_url}</Redirect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/process/{call_sid}")
async def twilio_process(
    call_sid: str,
    request: Request
) -> Response:
    """Consumes the pending event after playing the wait message, and executes the heavy LLM logic."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    external_url = _get_external_url(request)
    if not verify_twilio_signature(external_url, params, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

    with _lock:
        call_id = _call_sid_to_call_id.get(call_sid)
    if call_id is None:
        raise HTTPException(status_code=404, detail=f"unknown CallSid: {call_sid}")

    from urllib.parse import urlparse
    parsed = urlparse(_get_external_url(request))
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    gather_url = f"{base_url}/twilio/gather/{call_sid}"
    audio_base_url = base_url

    with _lock:
        event = _pending_events.pop(call_id, {"timeout": 15})

    return _step_and_render(call_id, call_sid, event, gather_url, audio_base_url)


@router.get("/audio/{clip_id}")
async def twilio_audio(clip_id: str) -> Response:
    """Serves a cached Sarvam TTS clip by its cache filename - Twilio's
    <Play> verb needs a fetchable URL, it cannot take audio bytes inline.
    clip_id is validated as a bare filename (no path separators) before
    ever touching the filesystem, since it comes straight from a URL path
    segment Twilio echoes back from a <Play> tag we ourselves generated."""
    import os

    if "/" in clip_id or ".." in clip_id or not clip_id.endswith(".wav"):
        raise HTTPException(status_code=400, detail="invalid clip id")
    path = os.path.join(voice.CACHE_DIR, clip_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="clip not found")
    with open(path, "rb") as f:
        audio = f.read()
    return Response(content=audio, media_type="audio/wav")


@router.post("/status")
async def twilio_status(request: Request) -> dict[str, Any]:
    """Twilio's call-status-changes webhook (e.g. "completed" on hangup) -
    cleans up the session so long-idle entries don't accumulate, and
    prints the diagnostic report if the call ended before ever reaching
    engine.py's own "ended" phase (e.g. the caller just hung up mid-call).

    No {call_sid} path parameter: Twilio always sends CallSid as a POST
    form field regardless of what StatusCallback URL was configured, so
    reading it from the body (rather than requiring the caller who sets
    up StatusCallback to already know the not-yet-created CallSid and
    bake it into the URL - impossible for an outbound call, which is the
    bug this replaced) is both simpler and the only way that actually
    works for outbound calls placed before a CallSid exists."""
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    if not call_sid:
        return {"cleaned_up": False}

    with _lock:
        call_id = _call_sid_to_call_id.pop(call_sid, None)
        log = _call_logs.pop(call_id, None) if call_id else None
        if call_id is not None:
            _sessions.pop(call_id, None)
    if log is not None and (not log.turns or log.turns[-1].phase != "ended"):
        _log_line(call_sid, f"call ended (status={call_status}) before reaching engine's own 'ended' phase")
        _print_call_report(log)
    return {"cleaned_up": call_id is not None}
