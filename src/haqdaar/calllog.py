"""Append-only per-call transcript on disk. Step 1 (demo/diagnostics).

One JSONL file per call at calls/<YYYY-MM-DD>/<CallSid>.jsonl, one record per
turn, carrying BOTH sides: what the caller pressed or said (including the raw
transcript before any matching ran) and what we said back, plus the engine
state that produced it and how long each stage took.

Why this exists: twilio_adapter.py already builds exactly this data for its
live terminal output (CallTurn/_record_turn), but threw it away when the call
ended - so a call that misbehaved could only be diagnosed by watching it
happen. Writing the same records to disk is what makes "why did it mishear
that?" answerable after the fact, from both sides at once.

DELIBERATE OVERRIDE OF PRD M13. The PRD (and api.py's own module docstring)
said caller answers are NEVER written to disk. That was the right default for
a privacy-first design and the wrong one for a system nobody can debug. The
override is scoped: transcripts and engine state only, never audio, and
calls/ is gitignored so no caller data reaches the repo.

Never raises. A logging failure must never take down a live call, so every
write is wrapped - a lost log line is always preferable to a dropped call.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

LOG_DIR = "calls"

_lock = threading.Lock()


def _path(call_sid: str) -> str:
    day = time.strftime("%Y-%m-%d")
    safe = "".join(c for c in call_sid if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(LOG_DIR, day, f"{safe}.jsonl")


def append(call_sid: str, record: dict[str, Any]) -> None:
    """Appends one record to this call's JSONL file. Silent on any failure."""
    try:
        path = _path(call_sid)
        payload = {"ts": time.time(), "iso": time.strftime("%H:%M:%S"), **record}
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - logging must never break a live call
        pass


def log_turn(
    call_sid: str,
    *,
    turn: int,
    event: dict[str, Any],
    said: list[str],
    phase: str,
    question_id: str | None,
    candidate_count: int,
    answers: dict[str, Any] | None = None,
    timings: dict[str, float] | None = None,
    error: str | None = None,
) -> None:
    """One full turn: the caller's side (event, including any raw transcript)
    and ours (every line spoken back), plus the state that produced it."""
    append(
        call_sid,
        {
            "kind": "turn",
            "turn": turn,
            "caller": event,
            "system_said": said,
            "phase": phase,
            "question_id": question_id,
            "candidate_count": candidate_count,
            "answers": answers or {},
            "timings_ms": timings or {},
            "error": error,
        },
    )


def log_event(call_sid: str, kind: str, **fields: Any) -> None:
    """Anything that isn't a full turn - call start/end, an STT result, an
    LLM failure. Kept separate so `kind == "turn"` stays a clean filter."""
    append(call_sid, {"kind": kind, **fields})
