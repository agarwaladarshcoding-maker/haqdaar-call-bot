"""Terminal client of the HTTP API for demoing a call end to end. Step 8 -
THE DEMO. Talks to api.py over real HTTP (starts uvicorn itself if nothing
is already listening on the configured port), same as a real Twilio
adapter eventually would - sim.py adds zero engine logic of its own.

Usage:
    python -m haqdaar.sim                       interactive
    python -m haqdaar.sim --script 1,2,3,#,1     scripted replay
    python -m haqdaar.sim --script 1,2,!silence,1 --trace
    python -m haqdaar.sim --dial 011             direct dial-code lookup
                                                  (menu.py path, M10 -
                                                  see module docstring)
    python -m haqdaar.sim --seed 42 --script ...

--trace prints candidate count and the current question after every turn -
watching that count drop turn by turn is the single most convincing thing
to show a judge, per BUILD_STEPS.md Step 8.

M10 note: dial codes (menu.py's resolve_code) are a browsing-vs-addressing
system deliberately decoupled from the question flow (CHANGE_REPORT #7) -
every question in question_bank.yaml collects exactly one DTMF digit per
turn, so there is no multi-digit buffer mid-call to intercept a 3-digit
code with. --dial demonstrates the real, live menu.py/resolve_code path
directly, exactly as a caller who already knows a code from a leaflet
would use it (skip the questions entirely, jump straight to the section).
"""
from __future__ import annotations

import argparse
import os
import random
import socket
import subprocess
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8791"

# bank.py/config.py resolve question_bank.yaml, attribute_seed.sql, and the
# demo DB relative to the process CWD, not this file's location - the
# standing contract across the whole codebase (conftest.py's seed_demo.py
# subprocess already runs with cwd=REPO_ROOT for the same reason). The
# uvicorn subprocess this module spawns must honor it too.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SimError(Exception):
    pass


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _ensure_server(base_url: str) -> subprocess.Popen | None:
    """Starts uvicorn if nothing is already answering at base_url. Returns
    the Popen handle to terminate later, or None if a server was already
    running (in which case sim.py must not kill someone else's process)."""
    host_port = base_url.split("://")[1]
    host, port_s = host_port.split(":")
    port = int(port_s)
    if _port_open(host, port):
        return None
    env = {**os.environ, "PYTHONPATH": os.path.join(REPO_ROOT, "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "haqdaar.api:app", "--host", host, "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if _port_open(host, port):
            return proc
        time.sleep(0.1)
    proc.terminate()
    raise SimError(f"server did not start within 10s on {base_url}")


def _print_actions(actions: list[dict], quiet: bool = False) -> None:
    if quiet:
        return
    for a in actions:
        if "say" in a:
            print(f"  \033[36mIVR:\033[0m {a['say']}")
        elif "gather" in a:
            g = a["gather"]
            mode = "digits" + (" + speech" if g.get("speech") else "") if g.get("digits") else "speech only"
            print(f"  \033[90m[waiting for input: {mode}]\033[0m")
        elif a.get("end"):
            print("  \033[90m[call ended]\033[0m")


def _print_trace(state: dict) -> None:
    print(
        f"  \033[33m[trace] candidates={state['candidate_count']:>3} "
        f"question={state.get('current_question') or '-':<20} "
        f"asked={len(state['asked'])}\033[0m"
    )


def run_dial(base_url: str, code: str, quiet: bool = False) -> int:
    """M10: direct dial-code lookup via menu.py, no question flow at all."""
    from haqdaar import menu

    result = menu.resolve_code(code)
    if result is None:
        print(f"'{code}' is not a valid code (need 3 digits: 2-digit scheme number + 1-digit section).")
        return 1
    slug, sec_key = result
    text = menu.section_text(slug, sec_key)
    spoken_code = menu.speak_digits_hi(code)
    if not quiet:
        print(f"  \033[36mIVR:\033[0m Aapne dabaya: {spoken_code}")
    print(f"  \033[36mIVR:\033[0m {text}")
    return 0


def run_call(
    base_url: str,
    script: list[str] | None,
    trace: bool,
    seed: int | None,
    quiet: bool = False,
) -> dict[str, Any]:
    rng = random.Random(seed)
    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        resp = client.post("/call/start")
        resp.raise_for_status()
        body = resp.json()
        call_id = body["call_id"]
        _print_actions(body["actions"], quiet)
        if trace:
            _print_trace(body["state"])

        step_i = 0
        while body["state"]["phase"] != "ended":
            if script is not None:
                if step_i >= len(script):
                    break
                key = script[step_i]
                step_i += 1
            else:
                try:
                    key = input("  press a key (0-9, *, #, !silence, !hangup): ").strip()
                except EOFError:
                    break

            if key == "!silence":
                event = {"timeout": 30}
            elif key == "!hangup":
                event = {"hangup": True}
            elif key.startswith("!speech:"):
                event = {"speech": key.split(":", 1)[1], "confidence": rng.uniform(0.3, 0.95)}
            else:
                event = {"dtmf": key}

            resp = client.post("/call/event", json={"call_id": call_id, "event": event})
            resp.raise_for_status()
            body = resp.json()
            _print_actions(body["actions"], quiet)
            if trace:
                _print_trace(body["state"])

        client.delete(f"/call/{call_id}")
        return body["state"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Haqdaar Voice terminal simulator")
    parser.add_argument("--script", type=str, default=None, help="comma-separated key sequence, e.g. 1,2,3,#,1")
    parser.add_argument("--trace", action="store_true", help="show candidate count + question after each turn")
    parser.add_argument("--seed", type=int, default=None, help="reproducible randomness for speech confidence")
    parser.add_argument("--dial", type=str, default=None, help="direct dial-code lookup, e.g. --dial 011 (M10)")
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL)
    parser.add_argument("--quiet", action="store_true", help="suppress say/gather printing (used by tests)")
    args = parser.parse_args(argv)

    if args.dial:
        return run_dial(args.base_url, args.dial, quiet=args.quiet)

    script = args.script.split(",") if args.script else None

    proc = _ensure_server(args.base_url)
    try:
        state = run_call(args.base_url, script, args.trace, args.seed, quiet=args.quiet)
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)

    if state["phase"] != "ended":
        print("Call did not reach an ended state.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
