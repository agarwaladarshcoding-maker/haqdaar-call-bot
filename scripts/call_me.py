"""Make Haqdaar phone YOU, instead of you phoning Haqdaar.

The Twilio number is American (+1424...), so dialling it from an Indian
handset needs international roaming or ISD - which is exactly the thing
that isn't available on a hackathon floor. An OUTBOUND call inverts the
problem: Twilio dials the Indian mobile, the handset simply receives, and
in India an incoming international call costs the receiver nothing and
needs no roaming pack. The caller pays, and here the caller is Twilio.

Nothing about the call itself differs. Twilio POSTs the same
/twilio/voice webhook the moment the callee picks up, the same engine
runs, the same TwiML comes back - `Direction` is the only field that
changes, and no code reads it. So this is an honest demo, not a mock: it
is the identical code path a real caller hits.

TRIAL-ACCOUNT NOTE: on a trial account Twilio can only dial numbers on
its verified-caller-ID list, and it prepends a short "you have a trial
account, press any key" message. That keypress is consumed by Twilio's
own prompt, NOT by our opening question - so the call still starts at
Q002_INTENT exactly as it would for a real caller.

Usage:
    python -m scripts.call_me                    # dial DEMO_PHONE, auto-detect ngrok
    python -m scripts.call_me --to +91XXXXXXXXXX
    python -m scripts.call_me --url https://abc.ngrok-free.dev
    python -m scripts.call_me --dry-run          # show what it would do
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

from haqdaar import config

NGROK_API = "http://127.0.0.1:4040/api/tunnels"
TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"


def detect_public_url() -> str | None:
    """Ask the local ngrok agent for its current HTTPS tunnel.

    Auto-detection rather than a pasted constant on purpose: a free ngrok
    URL changes every restart, and a stale one fails as a call that rings,
    connects, and then dies silently with an application error - the worst
    thing to debug live, because the handset gives you no clue."""
    try:
        tunnels = httpx.get(NGROK_API, timeout=3.0).json().get("tunnels", [])
    except (httpx.HTTPError, ValueError):
        return None
    for t in tunnels:
        url = t.get("public_url", "")
        if url.startswith("https"):
            return url
    return None


def verified_numbers() -> list[str]:
    """Trial accounts refuse any destination that isn't on this list, with
    a 21210/21215-style error that doesn't say so in plain words."""
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{config.TWILIO_ACCOUNT_SID}/OutgoingCallerIds.json"
    )
    try:
        resp = httpx.get(url, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), timeout=10.0)
        resp.raise_for_status()
        return [n["phone_number"] for n in resp.json().get("outgoing_caller_ids", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Have Haqdaar call your phone.")
    ap.add_argument("--to", default=os.getenv("DEMO_PHONE"), help="number to call, E.164 (+91...)")
    ap.add_argument("--url", default=None, help="public base URL (default: ask the local ngrok agent)")
    ap.add_argument("--dry-run", action="store_true", help="print the request, place no call")
    args = ap.parse_args()

    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_PHONE_NUMBER):
        print("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER must be set in .env",
              file=sys.stderr)
        return 1

    verified = verified_numbers()
    to = args.to or (verified[0] if len(verified) == 1 else None)
    if not to:
        print("No destination. Pass --to +91XXXXXXXXXX, or set DEMO_PHONE in .env.", file=sys.stderr)
        if verified:
            print(f"Verified on this account: {', '.join(verified)}", file=sys.stderr)
        return 1
    if verified and to not in verified:
        print(f"{to} is not a verified caller ID on this trial account - the call will be "
              f"rejected.\nVerified: {', '.join(verified)}", file=sys.stderr)
        return 1

    base = args.url or detect_public_url()
    if not base:
        print("No public URL. Start the tunnel (./run.sh --tunnel) or pass --url.", file=sys.stderr)
        return 1
    base = base.rstrip("/")

    # Fail here rather than on the handset: an unreachable server produces
    # a call that connects to silence and then drops, which looks like a
    # voice bug and is actually a dead process.
    try:
        health = httpx.get(f"{base}/health", timeout=8.0).json()
        if not (health.get("db_loaded") and health.get("bank_loaded")):
            print(f"Server is up but not ready: {health}", file=sys.stderr)
            return 1
    except (httpx.HTTPError, ValueError) as e:
        print(f"{base}/health did not answer ({type(e).__name__}) - is ./run.sh running?", file=sys.stderr)
        return 1

    voice_url = f"{base}/twilio/voice"
    print(f"calling {to} from {config.TWILIO_PHONE_NUMBER}")
    print(f"  webhook: {voice_url}")
    if args.dry_run:
        print("  (dry run - no call placed)")
        return 0

    resp = httpx.post(
        TWILIO_API.format(sid=config.TWILIO_ACCOUNT_SID),
        auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
        data={
            "To": to,
            "From": config.TWILIO_PHONE_NUMBER,
            "Url": voice_url,
            "Method": "POST",
            "StatusCallback": f"{base}/twilio/status",
            "StatusCallbackMethod": "POST",
        },
        timeout=20.0,
    )
    if resp.status_code >= 400:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        print(f"Twilio refused the call ({resp.status_code}): "
              f"{body.get('message', resp.text[:300])}", file=sys.stderr)
        if body.get("more_info"):
            print(f"  {body['more_info']}", file=sys.stderr)
        return 1

    sid = resp.json().get("sid", "?")
    print(f"  ringing - CallSid {sid}")
    print(f"  transcript will be at calls/<today>/{sid}.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
