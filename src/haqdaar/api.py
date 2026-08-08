"""FastAPI HTTP surface over engine.step(). Step 7.

This layer does nothing but translate HTTP to step() and back - all logic
stays in engine.py. When the Twilio adapter arrives later (Step 11), it
becomes a second client of these same endpoints and nothing in the core
changes.

Sessions live in an in-memory dict keyed by call_id, and nothing in THIS
file writes to disk - CallState has no serialize-to-disk path.

PRD M13 ("caller answers are NEVER written to disk") no longer holds for
the Twilio path, and this docstring used to claim otherwise. calllog.py
persists a per-call transcript (both sides, plus engine state and stage
timings) under calls/, because a live call that misbehaves is otherwise
undiagnosable once it ends. The override is deliberate and scoped:
transcripts only, never audio, and calls/ is gitignored. See calllog.py's
own docstring and PRD.md.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from haqdaar import config
from haqdaar.bank import Bank, BankError, load_bank
from haqdaar.engine import CallState, step

app = FastAPI(title="Haqdaar Voice API")

# Step 11: Twilio becomes a second client of the same engine, mounted as
# its own router rather than folded into the endpoints above - it has its
# own session store (keyed by Twilio CallSid, not our call_id) and its own
# request/response shapes (form-encoded webhooks, TwiML), so sharing a
# router would blur two different transport contracts for no benefit.
from haqdaar.twilio_adapter import router as twilio_router  # noqa: E402

app.include_router(twilio_router)

# L5: two calls at once must never mix sessions - a single process-wide
# lock around dict access is enough here (in-memory dict, not a DB), since
# FastAPI's default sync-endpoint threadpool can run requests concurrently.
_lock = threading.Lock()
_sessions: dict[str, CallState] = {}

_bank: Bank | None = None
_bank_error: str | None = None


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


class EventIn(BaseModel):
    call_id: str
    event: dict[str, Any]


def _state_to_dict(state: CallState) -> dict[str, Any]:
    return {
        "phase": state.phase,
        "language": state.language,
        "answers": state.answers,
        "asked": list(state.asked),
        "current_question": state.current_question,
        "last_spoken": state.last_spoken,
        "candidate_count": len(state.candidates),
        "candidate_names": [c.scheme_name for c in state.candidates[:5]],
        "invalid_count": state.invalid_count,
        "speech_attempts": state.speech_attempts,
        "silence_elapsed": state.silence_elapsed,
        "menu_path": list(state.menu_path),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    """Reports whether the DB loaded and the bank validated - never raises
    itself, so a judge/monitor always gets a real answer, not a 500."""
    db_ok = True
    db_error = None
    try:
        from haqdaar.db import get_db
        conn = get_db(config.DB_PATH)
        conn.execute("SELECT 1 FROM schemes LIMIT 1")
        conn.close()
    except Exception as e:  # noqa: BLE001 - health check must never crash itself
        db_ok = False
        db_error = str(e)

    bank_ok = True
    bank_error = None
    try:
        _get_bank()
    except HTTPException as e:
        bank_ok = False
        bank_error = e.detail

    return {
        "status": "ok" if (db_ok and bank_ok) else "degraded",
        "db_loaded": db_ok,
        "db_error": db_error,
        "bank_loaded": bank_ok,
        "bank_error": bank_error,
        "active_calls": len(_sessions),
    }


@app.post("/call/start")
def call_start() -> dict[str, Any]:
    bank = _get_bank()
    call_id = str(uuid.uuid4())
    state = CallState()
    new_state, actions = step(state, {}, bank, config.DB_PATH)
    with _lock:
        _sessions[call_id] = new_state
    return {"call_id": call_id, "actions": actions, "state": _state_to_dict(new_state)}


@app.post("/call/event")
def call_event(body: EventIn) -> dict[str, Any]:
    bank = _get_bank()
    with _lock:
        state = _sessions.get(body.call_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown call_id: {body.call_id}")
        # L7: idempotent duplicate posts - a call already ended just keeps
        # returning the same terminal actions rather than erroring, since
        # step() itself already treats phase == "ended" as a no-op that
        # returns {"end": True} for any further event.
        new_state, actions = step(state, body.event, bank, config.DB_PATH)
        _sessions[body.call_id] = new_state
    return {"actions": actions, "state": _state_to_dict(new_state)}


@app.get("/call/{call_id}/state")
def call_state(call_id: str) -> dict[str, Any]:
    with _lock:
        state = _sessions.get(call_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown call_id: {call_id}")
    return _state_to_dict(state)


@app.delete("/call/{call_id}")
def call_end(call_id: str) -> dict[str, Any]:
    """L6: caller hangs up mid-question - session cleaned up, no crash.
    Idempotent: ending an already-gone or never-existed call_id is a no-op
    success, not a 404, since "make sure it's gone" should never itself
    fail on a call that's already gone."""
    with _lock:
        _sessions.pop(call_id, None)
    return {"ended": True}
