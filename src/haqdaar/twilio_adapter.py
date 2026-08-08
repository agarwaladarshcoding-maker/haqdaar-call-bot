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
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

import httpx
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

from haqdaar import calllog, config, voice
from haqdaar.bank import Bank, BankError, load_bank
from haqdaar.engine import CallState, step

# Played while the engine works (STT + LLM + narrow can take a couple of
# seconds). Kept as a module constant so scripts/warm_cache.py can
# pre-generate it - it is spoken on EVERY turn, so a cache miss here would
# be the most expensive one in the system.
WAIT_TEXT = "Ek minute, main dekh raha hoon."
TTS_LANG = "hi-IN"

# Real seconds of silence a completed-but-empty <Record> represents: the
# barge-in <Gather> plus the <Record> silence timeout, rounded up. Fed to
# engine.py's silence ladder, which counts real seconds cumulatively.
# DERIVED, never hardcoded: the ladder hangs up after enough accumulated
# silence, so a number that drifts below the truth would drop callers who
# had merely paused twice.
SILENCE_AFTER_RECORD = config.GATHER_BARGE_IN_SECONDS + config.RECORD_SILENCE_SECONDS + 1

router = APIRouter(prefix="/twilio", tags=["twilio"])

_lock = threading.Lock()
_sessions: dict[str, CallState] = {}          # call_id -> state
_call_sid_to_call_id: dict[str, str] = {}     # Twilio CallSid -> our call_id
_pending_events: dict[str, dict[str, Any]] = {}     # Twilio CallSid -> event
_pending_timings: dict[str, dict[str, float]] = {}  # STT timings, carried into the turn's log
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
    Twilio's own <Gather>/<Say> both want a BCP-47-ish code.

    Note this ALWAYS returns a valid BCP-47 code, never the raw engine
    value. Rendering `state.language` straight into a TwiML attribute is
    the bug this function exists to prevent: it produced
    <Say language="None"> on every single turn, which Twilio rejects."""
    return "en-IN" if engine_language == "en" else "hi-IN"


def _say_twiml(text: str, lang_code: str, audio_base_url: str, deadline: float | None = None) -> str:
    """One spoken line as TwiML: <Play> of a real Sarvam clip when we have
    one, <Say> only as a last resort (no SARVAM_API_KEY, a live TTS
    failure, or the per-turn TTS budget already spent). Every place that
    speaks goes through here, so no call site can reintroduce a
    hand-written <Say> with an unvalidated language.

    `deadline` caps synthesis across a whole turn. A cache hit is free and
    always taken; only an actual Sarvam call is skipped once the budget is
    gone, so a warmed cache (scripts/warm_cache.py) never hits this path."""
    cached = os.path.exists(voice._cache_path(text, lang_code))
    if not cached and deadline is not None and time.monotonic() > deadline:
        print(f"  \033[33mTTS budget spent, falling back to <Say> for: {text[:50]}\033[0m", flush=True)
        return f'<Say language="{lang_code}">{escape(text)}</Say>'

    audio = voice.tts(text, lang_code)
    if audio:
        clip_id = os.path.basename(voice._cache_path(text, lang_code))
        return f"<Play>{escape(audio_base_url)}/twilio/audio/{clip_id}</Play>"
    return f'<Say language="{lang_code}">{escape(text)}</Say>'


def _actions_to_twiml(
    actions: list[dict],
    gather_action_url: str,
    audio_base_url: str,
    language: str | None,
    recording_action_url: str | None = None,
) -> str:
    """{"say"} -> <Play> of a real Sarvam clip when available, <Say>
    fallback otherwise. {"gather"} -> <Gather> (buttons) and, when the
    question accepts speech, a following <Record> so SARVAM does the
    transcription. {"end"} -> <Hangup>.

    WHY <Record> AND NOT <Gather input="speech">: Twilio's own recognizer
    was doing the transcription, and it cannot handle a caller who says
    "main kisan hoon" and an English scheme name in the same sentence.
    <Gather> gives you Twilio's text and no way to reach the audio, so
    the only way to put Sarvam in the loop is to record and transcribe
    ourselves. voice.stt() existed for exactly this and was never called
    by anything - it was dead code until now.

    The <Gather>/<Record> pair, in order, is what keeps BOTH inputs live:
      <Gather input="dtmf" timeout="2"> wrapping the prompt audio - a
      keypress interrupts the prompt (barge-in) and skips the recording
      entirely, so button-only callers are as fast as they ever were.
      <Record finishOnKey="0123456789*#"> catches speech if no key came,
      and still reports Digits if the caller presses one mid-recording.
    A speech-disabled question (Q001-style, collect.speech false) emits
    the <Gather> alone, exactly as before."""
    lang_code = _sarvam_lang(language)
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<Response>"]
    pending_says: list[str] = []
    deadline = time.monotonic() + config.TTS_BUDGET_MS / 1000.0

    def flush_says():
        for text in pending_says:
            parts.append(_say_twiml(text, lang_code, audio_base_url, deadline))
        pending_says.clear()

    for a in actions:
        if "say" in a:
            pending_says.append(a["say"])
        elif "gather" in a:
            g = a["gather"]
            digits = g.get("digits", 1)
            speech = g.get("speech", True) and recording_action_url is not None

            # numDigits=0 is invalid TwiML; a speech-only question (e.g.
            # Q100_SCHEME_NAME, collect.digits 0) still needs the <Gather>
            # to play the prompt and allow a global key (0/*/#).
            num_digits = max(1, digits)
            # A short timeout when speech follows: this <Gather> is only
            # the barge-in window before the <Record> takes over. Without
            # a <Record> to fall through to, it stays the long wait.
            timeout = config.GATHER_BARGE_IN_SECONDS if speech else 15
            # actionOnEmptyResult="false" is load-bearing whenever a
            # <Record> follows, and its absence is why a real caller was
            # asked "Yojna ka naam boliye", spoke, and was never recorded:
            # Twilio POSTed the Gather action with empty Digits the
            # instant the barge-in window lapsed, so the <Record> below
            # was never reached and the turn became a bare silence event.
            # False means "no input -> fall through to the next verb",
            # which is the entire premise of the Gather/Record pair.
            empty_result = "" if not speech else ' actionOnEmptyResult="false"'
            parts.append(
                f'<Gather input="dtmf" numDigits="{num_digits}" timeout="{timeout}"'
                f'{empty_result} '
                f'action="{escape(gather_action_url)}" method="POST">'
            )
            flush_says()
            parts.append("</Gather>")

            if speech:
                # trim-silence so a caller who pauses doesn't send us
                # seconds of dead air to transcribe. The silence timeout
                # is the delicate number: too long and the caller waits
                # after finishing, too short and we cut them off before
                # they begin. Two real calls proved 2s was far too short -
                # it clipped a caller at 2.0s of audio ("Aa") and then
                # recorded 4.0s of nothing - so it now lives in config
                # where it can be tuned without touching TwiML.
                parts.append(
                    f'<Record action="{escape(recording_action_url)}" method="POST" '
                    f'maxLength="{config.RECORD_MAX_SECONDS}" '
                    f'timeout="{config.RECORD_SILENCE_SECONDS}" playBeep="true" '
                    f'trim="trim-silence" finishOnKey="0123456789*#" />'
                )
            else:
                # No recording to fall through to: nothing entered replays
                # the same prompt as a silence retry, matching engine.py's
                # own silence ladder.
                parts.append(f'<Redirect method="POST">{escape(gather_action_url)}?silence=1</Redirect>')
        elif a.get("end"):
            flush_says()
            parts.append("<Hangup/>")

    flush_says()
    parts.append("</Response>")
    return "".join(parts)


def _record_turn(
    call_id: str,
    call_sid: str,
    event: dict,
    new_state: CallState,
    actions: list[dict],
    timings: dict[str, float] | None = None,
) -> None:
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
    said = [a["say"] for a in actions if "say" in a]

    _log_line(call_sid, f"{_describe_event(event)} -> phase={new_state.phase} candidates={len(new_state.candidates)}")
    for text in said:
        preview = text[:80] + ("..." if len(text) > 80 else "")
        _log_line(call_sid, f'  SAY: "{preview}"')

    # Same data the terminal just printed, persisted so the call can still
    # be diagnosed after it ends (calllog.py never raises).
    calllog.log_turn(
        call_sid,
        turn=len(log.turns),
        event=event,
        said=said,
        phase=new_state.phase,
        question_id=new_state.current_question,
        candidate_count=len(new_state.candidates),
        answers=dict(new_state.answers),
        timings=timings,
    )

    if new_state.phase == "ended":
        _print_call_report(log)


@dataclass(frozen=True)
class CallUrls:
    """The four absolute URLs a turn's TwiML needs. Built in one place
    because they were previously recomputed inline in four endpoints, and
    a fifth (recording) would have made that five chances to drift."""
    base: str
    gather: str
    recording: str


def _urls(request: Request, call_sid: str) -> CallUrls:
    from urllib.parse import urlparse

    parsed = urlparse(_get_external_url(request))
    base = f"{parsed.scheme}://{parsed.netloc}"
    return CallUrls(
        base=base,
        gather=f"{base}/twilio/gather/{call_sid}",
        recording=f"{base}/twilio/recording/{call_sid}",
    )


def _step_and_render(call_id: str, call_sid: str, event: dict, urls: CallUrls, extra_timings: dict | None = None) -> Response:
    bank = _get_bank()
    t0 = time.monotonic()
    with _lock:
        state = _sessions.get(call_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown call_id: {call_id}")
        new_state, actions = step(state, event, bank, config.DB_PATH)
        _sessions[call_id] = new_state
    t_step = time.monotonic()
    twiml = _actions_to_twiml(actions, urls.gather, urls.base, new_state.language, urls.recording)
    t_tts = time.monotonic()

    # Timed separately because these are the two stages that can blow past
    # Twilio's 15s webhook timeout, and knowing WHICH one did is the whole
    # point of logging them.
    timings = {
        **(extra_timings or {}),
        "engine_ms": round((t_step - t0) * 1000, 1),
        "tts_ms": round((t_tts - t_step) * 1000, 1),
        "total_ms": round((t_tts - t0) * 1000, 1),
    }
    _record_turn(call_id, call_sid, event, new_state, actions, timings)
    if timings["total_ms"] > 10_000:
        _log_line(call_sid, f"\033[31mSLOW TURN {timings['total_ms']}ms - Twilio drops at 15000ms\033[0m")
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
    calllog.log_event(call_sid, "call_start", from_=params.get("From"), to=params.get("To"))

    bank = _get_bank()
    call_id = str(uuid.uuid4())
    state = CallState()
    new_state, actions = step(state, {}, bank, config.DB_PATH)
    with _lock:
        _sessions[call_id] = new_state
        _call_sid_to_call_id[call_sid] = call_id
        _call_logs[call_id] = CallLog(call_sid=call_sid)
    _record_turn(call_id, call_sid, {}, new_state, actions)

    urls = _urls(request, call_sid)
    twiml = _actions_to_twiml(actions, urls.gather, urls.base, new_state.language, urls.recording)
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

    urls = _urls(request, call_sid)

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

    calllog.log_event(call_sid, "input", event=event, phase=state.phase)
    return _wait_then_process(call_sid, urls.base, language)


def _wait_then_process(call_sid: str, base_url: str, language: str | None) -> Response:
    """Plays "ek minute" and bounces to /process, which does the slow work
    (STT, LLM, narrowing). Splitting the turn in two is what keeps a
    caller from sitting in silence while we think.

    Was <Say language="{state.language}"> here, which rendered
    language="None"/"hi" - neither is a value Twilio accepts, so this verb
    was invalid on EVERY turn of every call. It now goes through the same
    Sarvam <Play> path as every other spoken line, which also means the
    wait message is in the call's own voice, not Twilio's built-in one."""
    process_url = f"{base_url}/twilio/process/{call_sid}"
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"{_say_twiml(WAIT_TEXT, _sarvam_lang(language), base_url)}"
        f'<Redirect method="POST">{escape(process_url)}</Redirect>'
        "</Response>"
    )
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

    with _lock:
        event = _pending_events.pop(call_id, {"timeout": 15})
        extra_timings = _pending_timings.pop(call_id, {})

    return _step_and_render(call_id, call_sid, event, _urls(request, call_sid), extra_timings)


def _fetch_recording(recording_url: str) -> bytes | None:
    """Downloads a Twilio recording as 8kHz WAV. Twilio serves the raw
    RecordingUrl with no extension; appending .wav is the documented way
    to get WAV rather than MP3, and Sarvam wants WAV.

    Authenticated with the account SID/token we already have - recordings
    are private to the account, so an unauthenticated GET returns 401.
    Returns None on any failure; the caller treats that exactly like an
    unclear utterance, which engine.py's existing ladder already handles."""
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        return None
    try:
        resp = httpx.get(
            f"{recording_url}.wav",
            auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
            timeout=config.RECORDING_FETCH_TIMEOUT_MS / 1000.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPError:
        return None


@router.post("/recording/{call_sid}")
async def twilio_recording(
    call_sid: str,
    request: Request,
    RecordingUrl: str | None = Form(default=None),
    RecordingDuration: str | None = Form(default=None),
    Digits: str | None = Form(default=None),
) -> Response:
    """Fires when a <Record> ends. This is where SARVAM replaces Twilio's
    own recognizer: we fetch the audio Twilio just captured and transcribe
    it ourselves, because Twilio's hi-IN model cannot handle a caller who
    mixes Hindi and English - which is how people actually talk.

    Three outcomes, all of which the engine already knows how to handle:
      Digits set        -> the caller pressed a key during the recording
                           (finishOnKey), so it's a plain dtmf event and
                           no transcription is needed at all.
      no/short audio    -> a timeout event, same as any other silence.
      audio             -> Sarvam transcript as a speech event.

    Transcription happens HERE rather than in /process so the raw
    transcript is logged even when the engine later rejects it - "what did
    Sarvam actually hear?" is the first question worth answering when a
    call goes wrong, and it must be answerable separately from "what did
    the engine do with it"."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    if not verify_twilio_signature(_get_external_url(request), params, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

    with _lock:
        call_id = _call_sid_to_call_id.get(call_sid)
    if call_id is None:
        raise HTTPException(status_code=404, detail=f"unknown CallSid: {call_sid}")

    urls = _urls(request, call_sid)
    try:
        duration = float(RecordingDuration or 0)
    except ValueError:
        duration = 0.0

    timings: dict[str, float] = {}
    if Digits:
        event: dict[str, Any] = {"dtmf": Digits}
        _log_line(call_sid, f"recording ended on keypress {Digits!r}")
    elif not RecordingUrl or duration < 1:
        # Under a second is a cough or a hangup artifact, not speech.
        #
        # SILENCE_AFTER_RECORD, not the 15 the <Gather> path reports:
        # engine.py's silence ladder is cumulative and calibrated in real
        # seconds (5 nudge / 15 two-options / 25 are-you-there / 30 end).
        # This path's actual silence is the barge-in Gather plus the
        # Record timeout, so reporting 15 would have hung up on a caller
        # who simply paused to think - two thoughtful pauses would hit 30
        # and end the call after about ten real seconds.
        event = {"timeout": SILENCE_AFTER_RECORD}
        _log_line(call_sid, f"no usable recording (duration={duration}s) -> silence")
        # Logged as an `stt` record with an explicit reason, NOT left to
        # the terminal alone. A real call reached this branch and the
        # JSONL showed only a bare `{"dtmf": "timeout"}`, which is
        # indistinguishable from the caller never speaking - so the
        # transcript could not answer whether Twilio recorded nothing,
        # recorded silence, or recorded speech we then threw away. That
        # is the exact question the call log exists to answer.
        calllog.log_event(
            call_sid, "stt",
            transcript="", confidence=0.0, audio_seconds=duration, fetched=False,
            skipped_reason=("no RecordingUrl" if not RecordingUrl else "duration under 1s"),
            timings_ms={},
        )
    else:
        t0 = time.monotonic()
        audio = _fetch_recording(RecordingUrl)
        t_fetch = time.monotonic()
        transcript, confidence = voice.stt(audio) if audio else ("", 0.0)
        t_stt = time.monotonic()
        timings = {
            "recording_fetch_ms": round((t_fetch - t0) * 1000, 1),
            "stt_ms": round((t_stt - t_fetch) * 1000, 1),
        }
        event = {"speech": transcript, "confidence": confidence}
        _log_line(
            call_sid,
            f'\033[35mSARVAM heard: "{transcript}"\033[0m (conf={confidence:.2f}, '
            f"{duration}s audio, {timings['stt_ms']:.0f}ms)",
        )
        calllog.log_event(
            call_sid, "stt",
            transcript=transcript, confidence=confidence,
            audio_seconds=duration, fetched=bool(audio), timings_ms=timings,
        )

    with _lock:
        _pending_events[call_id] = event
        _pending_timings[call_id] = timings
        language = _sessions[call_id].language

    calllog.log_event(call_sid, "input", event=event)
    # Same two-hop as /gather: play "ek minute" first, then do the slow
    # engine work in /process, so the caller isn't left in silence.
    return _wait_then_process(call_sid, urls.base, language)


@router.get("/audio/{clip_id}")
async def twilio_audio(clip_id: str) -> Response:
    """Serves a cached Sarvam TTS clip by its cache filename - Twilio's
    <Play> verb needs a fetchable URL, it cannot take audio bytes inline.
    clip_id is validated as a bare filename (no path separators) before
    ever touching the filesystem, since it comes straight from a URL path
    segment Twilio echoes back from a <Play> tag we ourselves generated."""
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
    calllog.log_event(
        call_sid,
        "call_end",
        status=call_status,
        turns=len(log.turns) if log else 0,
        reached_end_phase=bool(log and log.turns and log.turns[-1].phase == "ended"),
    )
    if log is not None and (not log.turns or log.turns[-1].phase != "ended"):
        _log_line(call_sid, f"call ended (status={call_status}) before reaching engine's own 'ended' phase")
        _print_call_report(log)
    return {"cleaned_up": call_id is not None}
