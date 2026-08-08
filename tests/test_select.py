"""TEST_CASES.md section J - LLM selector fallback discipline.

J7 is a GATE: whatever the LLM returns, the only thing that ever comes
back out of pick_question() is a Question already present in the bank -
never raw LLM text, and never anything that reaches a caller as speech.
"""
import json
import time

import pytest

from haqdaar.bank import load_bank
from haqdaar.select import pick_question

BANK = load_bank()


def find_for_me_answers():
    return {"language": "hi", "intent": "find_for_me"}


def test_j1_no_api_key_uses_rank1_and_call_proceeds(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    q = pick_question(find_for_me_answers(), [], BANK, db_path=demo_db)
    assert q is not None
    assert q.id in {qq.id for qq in BANK.askable(find_for_me_answers())}


def test_j2_llm_slower_than_budget_uses_rank1(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    monkeypatch.setattr("haqdaar.config.LLM_TIMEOUT_MS", 10)

    def slow_caller(prompt, ids):
        time.sleep(0.05)
        return json.dumps({"id": ids[0]})

    answers = find_for_me_answers()
    expected_rank1 = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=slow_caller)
    assert got.id == expected_rank1.id


def test_j3_id_not_in_shortlist_uses_rank1(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def bad_id_caller(prompt, ids):
        return json.dumps({"id": "Q999_NOT_REAL"})

    answers = find_for_me_answers()
    expected_rank1 = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=bad_id_caller)
    assert got.id == expected_rank1.id


def test_j4_malformed_response_uses_rank1(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def malformed_caller(prompt, ids):
        return "not json at all {{{"

    answers = find_for_me_answers()
    expected_rank1 = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=malformed_caller)
    assert got.id == expected_rank1.id


def test_j5_already_asked_id_rejected_uses_rank1(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    answers = find_for_me_answers()  # already answered: language, intent

    def already_asked_caller(prompt, ids):
        # Q001_LANGUAGE writes `language`, which is already in `answers`.
        return json.dumps({"id": "Q001_LANGUAGE"})

    expected_rank1 = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=already_asked_caller)
    assert got.id == expected_rank1.id


def test_j6_disabled_flag_skips_llm_entirely(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    calls = []

    def spy_caller(prompt, ids):
        calls.append(1)
        return json.dumps({"id": ids[0]})

    pick_question(
        find_for_me_answers(), [], BANK, db_path=demo_db,
        llm_enabled=False, llm_caller=spy_caller,
    )
    assert calls == [], "llm_enabled=False must skip the LLM call entirely (J6 wiring point)"


def test_j7_llm_output_never_returned_as_text(demo_db, monkeypatch):
    """GATE. Whatever the LLM returns, pick_question must only ever return
    a Question object (or None) - never a str, never the raw LLM payload."""
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def chatty_caller(prompt, ids):
        # A "helpful" LLM that ignores instructions and chats instead of
        # returning a clean id - must be treated as malformed, not crash,
        # and definitely never be handed back to the caller as text.
        return "Sure! I think you should ask about their income next."

    from haqdaar.bank import Question
    got = pick_question(find_for_me_answers(), [], BANK, db_path=demo_db, llm_caller=chatty_caller)
    assert isinstance(got, Question)
    assert not isinstance(got, str)


def test_llm_valid_choice_from_shortlist_is_honored(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    answers = find_for_me_answers()
    askable_ids = {q.id for q in BANK.askable(answers)}

    picked_id = {}

    def echo_caller(prompt, ids):
        picked_id["shortlist"] = ids
        # Deliberately choose the LAST id in the shortlist, not rank 1, to
        # prove the LLM's actual choice (when valid) is honored rather than
        # always silently falling back.
        return json.dumps({"id": ids[-1]})

    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=echo_caller)
    assert got.id == picked_id["shortlist"][-1]
    assert got.id in askable_ids
