"""Loads .env if present. Every setting has a working default - the app must
run with no .env at all (BUILD_STEPS.md Step 0 acceptance test)."""
import os

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # empty -> provider's own default
LLM_TIMEOUT_MS = int(os.getenv("LLM_TIMEOUT_MS", "700"))

# Wall-clock caps for the two stages that run inside a Twilio webhook.
# Twilio hangs up if we don't answer in 15s, so these must sum to well
# under that with room for the HTTP round trip itself.
PRESENT_BUDGET_MS = int(os.getenv("PRESENT_BUDGET_MS", "4000"))   # present.py LLM total
TTS_BUDGET_MS = int(os.getenv("TTS_BUDGET_MS", "5000"))           # per-turn TTS total
TTS_TIMEOUT_MS = int(os.getenv("TTS_TIMEOUT_MS", "4000"))         # one Sarvam TTS call

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_TTS_LANG = os.getenv("SARVAM_TTS_LANG", "hi-IN")
# "unknown" = let Sarvam detect per utterance. Callers mix Hindi and
# English freely, so pinning one language is worse than detecting it.
SARVAM_STT_LANG = os.getenv("SARVAM_STT_LANG", "unknown")
# "translit" = Roman script out. See voice.stt()'s docstring: everything
# downstream matches against Roman text, so Devanagari breaks matching.
SARVAM_STT_MODE = os.getenv("SARVAM_STT_MODE", "translit")
STT_TIMEOUT_MS = int(os.getenv("STT_TIMEOUT_MS", "6000"))
# Cap on one recorded caller utterance. Long enough for "main kisan hoon
# aur mujhe kheti ke liye loan chahiye", short enough that a caller who
# forgets to stop talking doesn't stall the call.
RECORD_MAX_SECONDS = int(os.getenv("RECORD_MAX_SECONDS", "12"))
RECORDING_FETCH_TIMEOUT_MS = int(os.getenv("RECORDING_FETCH_TIMEOUT_MS", "5000"))
# understand.py runs once per call on the opening utterance and saves
# several turns when it works, so it gets a longer budget than the
# per-turn LLM calls.
UNDERSTAND_TIMEOUT_MS = int(os.getenv("UNDERSTAND_TIMEOUT_MS", "6000"))

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

DB_PATH = os.getenv("DB_PATH", "haqdaar.db")
SCHEME_SOURCE_JSON = os.getenv("SCHEME_SOURCE_JSON", "optimized_schemes.json")
QUESTION_BANK_PATH = os.getenv("QUESTION_BANK_PATH", "question_bank.yaml")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
