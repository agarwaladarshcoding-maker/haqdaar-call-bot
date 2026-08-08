"""Shared pytest fixtures. Tests run against a fresh copy of the 20-scheme
demo DB (scripts/seed_demo.py), never the real 100-scheme catalogue, so
narrowing behaviour is asserted against known, controlled rules."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


@pytest.fixture(autouse=True)
def _no_real_llm_calls_by_default(monkeypatch):
    """The whole point of select.py/present.py's fallback discipline is
    that the system is correct with NO LLM key present - but a real .env
    with a real LLM_API_KEY (needed for scripts/extract_rules.py and any
    manual demo run) would otherwise make every test that reaches
    engine.py's presenting phase fire a real, uncontrolled network call
    the moment .env exists on disk. Tests must be hermetic regardless of
    what's in .env: blank the key by default for every test; a test that
    specifically wants to exercise the LLM path re-patches it to a fake
    key AND passes an injected llm_caller, same pattern test_select.py
    already established before this fixture existed."""
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")


@pytest.fixture(autouse=True)
def _no_real_sarvam_calls_by_default(monkeypatch):
    """Same hermeticity issue as the LLM key, discovered the same way (a
    real .env for a live demo broke tests that assumed no key): once
    .env carries a real SARVAM_API_KEY, twilio_adapter.py's <Say>-vs-
    <Play> choice and voice.py's tts()/stt() start making real network
    calls and returning real audio instead of the documented no-key
    fallback, which is what most tests here actually want to exercise."""
    monkeypatch.setattr("haqdaar.config.SARVAM_API_KEY", "")


@pytest.fixture(autouse=True)
def _no_real_twilio_signature_check_by_default(monkeypatch):
    """Same hermeticity issue as the LLM key above, discovered the same
    way (a real .env for a live demo broke the test suite): once .env
    carries a real TWILIO_AUTH_TOKEN, twilio_adapter.py's signature check
    starts rejecting every test webhook that doesn't carry a real,
    correctly-computed X-Twilio-Signature header, which none of the
    ordinary functional tests do (that's test_twilio_adapter.py's own
    signature-specific tests' job, and they explicitly monkeypatch the
    token back in for exactly that purpose)."""
    monkeypatch.setattr("haqdaar.config.TWILIO_AUTH_TOKEN", "")


@pytest.fixture()
def demo_db(tmp_path):
    db_path = str(tmp_path / "test_haqdaar.db")
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py"), db_path],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return db_path
