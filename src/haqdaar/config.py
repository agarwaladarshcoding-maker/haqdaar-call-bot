"""Loads .env if present. Every setting has a working default - the app must
run with no .env at all (BUILD_STEPS.md Step 0 acceptance test)."""
import os

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")
LLM_TIMEOUT_MS = int(os.getenv("LLM_TIMEOUT_MS", "700"))

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_TTS_LANG = os.getenv("SARVAM_TTS_LANG", "hi-IN")
SARVAM_STT_LANG = os.getenv("SARVAM_STT_LANG", "hi-IN")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

DB_PATH = os.getenv("DB_PATH", "haqdaar.db")
SCHEME_SOURCE_JSON = os.getenv("SCHEME_SOURCE_JSON", "optimized_schemes.json")
QUESTION_BANK_PATH = os.getenv("QUESTION_BANK_PATH", "question_bank.yaml")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
