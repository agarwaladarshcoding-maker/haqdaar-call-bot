#!/usr/bin/env bash
# One command to get set up for recording the demo video.
#
#   ./demos/record.sh terminal    the engine running in the terminal
#   ./demos/record.sh phone       a real call to your handset
#   ./demos/record.sh check       rehearse: prints the keys, dials nobody
#
# Both modes run the SAME engine and produce the same 100 -> 6 -> 4. Only
# the keypresses are decided in advance; every line and every number comes
# back from the running system.
#
# Record the TERMINAL mode if you want one clean take - no room noise, no
# network leg, and it paces itself for video. Record the PHONE mode if you
# want a handset on camera; point it at the run.sh terminal, which prints
# the same thing live.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-terminal}"
PYTHON="${PYTHON:-.venv/bin/python}"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

keys() {
  printf '\n'
  printf '  \033[1mPRESS, IN ORDER\033[0m\n\n'
  printf '    \033[1m1\033[0m   "haan, naam pata hai"\n'
  printf '    \033[1m1\033[0m   browse by category instead\n'
  printf '    \033[1m5\033[0m   kheti / farming              \033[32m100 -> 6\033[0m\n'
  printf '    \033[1m1\033[0m   dhaan / paddy                \033[32m  6 -> 4\033[0m\n\n'
  printf '  \033[90mNo talking. Four keys, then it reads out the schemes.\033[0m\n\n'
}

case "$MODE" in
  terminal)
    # --speed slow is the video pace: fast enough not to drag, slow
    # enough that a viewer can read a line before the next one lands.
    clear
    exec "$PYTHON" -m demos.demo --speed slow
    ;;

  phone)
    # The backend must already be up in another window - that terminal
    # IS the shot, so this must not start its own hidden copy.
    if ! curl -s -m 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
      echo "Backend is not running. Start it in the window you are filming:" >&2
      echo "    ./run.sh" >&2
      exit 1
    fi
    keys
    echo "  dialling..."
    exec "$PYTHON" -m scripts.call_me
    ;;

  check)
    keys
    "$PYTHON" -m demos.demo --speed instant
    ;;

  *)
    echo "usage: $0 [terminal|phone|check]" >&2
    exit 2
    ;;
esac
