#!/usr/bin/env bash
# Start the Haqdaar voice server so you can WATCH a call happen.
#
# The point of this script is the terminal output. twilio_adapter.py prints
# every webhook hit, the event it was translated to and every line spoken
# back, as it happens - but only if something is actually showing stdout.
# Redirecting uvicorn into a file (which is what left an empty uvicorn.log
# in the repo root) hides exactly the thing you need during a demo, so this
# runs in the FOREGROUND and uses `tee`: on screen and on disk, not either/or.
#
# Usage:
#   ./run.sh              server only (use for local/simulator testing)
#   ./run.sh --tunnel     also start ngrok and print the webhook URLs
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
PYTHON="${PYTHON:-.venv/bin/python}"
mkdir -p logs
LOG="logs/server-$(date +%Y%m%d-%H%M%S).log"

if [ ! -x "$PYTHON" ]; then
  echo "No interpreter at $PYTHON - create it with: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

cleanup() { [ -n "${NGROK_PID:-}" ] && kill "$NGROK_PID" 2>/dev/null || true; }
trap cleanup EXIT

if [ "${1:-}" = "--tunnel" ]; then
  if ! command -v ngrok >/dev/null 2>&1; then
    echo "ngrok not installed - run without --tunnel, or: brew install ngrok" >&2
    exit 1
  fi
  ngrok http "$PORT" --log=stdout > logs/ngrok.log 2>&1 &
  NGROK_PID=$!
  # ngrok's local API only answers once the tunnel is actually up.
  for _ in $(seq 1 20); do
    URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
      | "$PYTHON" -c 'import json,sys;d=json.load(sys.stdin);print(next((t["public_url"] for t in d.get("tunnels",[]) if t["public_url"].startswith("https")),""))' 2>/dev/null || true)
    [ -n "${URL:-}" ] && break
    sleep 0.5
  done
  if [ -z "${URL:-}" ]; then
    echo "ngrok started but no HTTPS tunnel appeared - check logs/ngrok.log" >&2
  else
    echo ""
    echo "======================================================================"
    echo "  Paste these into the Twilio console for your number:"
    echo "    A CALL COMES IN   ->  POST  $URL/twilio/voice"
    echo "    CALL STATUS       ->  POST  $URL/twilio/status"
    echo "======================================================================"
    echo ""
  fi
fi

echo "logging to $LOG   (call transcripts land in calls/$(date +%Y-%m-%d)/)"
echo ""

# PYTHONPATH=src because the package is deliberately not pip-installed -
# the whole repo runs from source (conftest.py injects the same path, and
# sim.py sets exactly this for the uvicorn subprocess it spawns).
#
# PYTHONUNBUFFERED keeps output flowing through the pipe: without it `tee`
# shows the call in 4KB bursts instead of turn by turn, which defeats the
# whole purpose. (stdbuf would also work but isn't on every macOS.)
#
# --reload so edits land without restarting mid-hack.
exec env PYTHONUNBUFFERED=1 PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON" -m uvicorn haqdaar.api:app \
  --host 0.0.0.0 --port "$PORT" --reload --log-level info 2>&1 | tee "$LOG"
