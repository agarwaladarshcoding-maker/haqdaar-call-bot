"""Stress test for select.py beyond TEST_CASES.md's documented J-series.

Adversarial/property-based coverage: random answer walks, malformed LLM
payloads of every shape, determinism, and the offline guarantee PRD's M14
depends on (system runs with zero external API keys).
"""
import json
import random

import pytest

from haqdaar.bank import load_bank, Question
from haqdaar.narrow import narrow
from haqdaar.select import pick_question

BANK = load_bank()


def walk_to_random_depth(rng, max_steps, db_path):
    """Drives pick_question -> answer -> pick_question for up to max_steps
    turns, always taking the FIRST dtmf option (deterministic walk driven
    only by rng's choice of when to stop), or stopping early on a
    speech-only question or no-more-askable. Returns the final answers."""
    answers = {}
    for _ in range(max_steps):
        q = pick_question(answers, [], BANK, db_path=db_path, llm_enabled=False)
        if q is None:
            break
        opts = q.get("dtmf") or {}
        if not opts:
            break
        key = rng.choice(list(opts.keys()))
        val = (opts[key].get("set") or {}).get(q.raw["writes"])
        if val is None:
            break
        answers = {**answers, q.raw["writes"]: val}
    return answers


# ---------------------------------------------------------------------------
# 1. Offline guarantee (M14 / J1 generalized): with no API key, 40 random
#    walks of varying depth never crash and always return a real Question
#    or a clean None.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(40))
def test_offline_random_walks_never_crash(demo_db, seed, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    rng = random.Random(seed)
    answers = {}
    for step in range(12):
        q = pick_question(answers, [], BANK, db_path=demo_db)
        if q is None:
            break
        assert isinstance(q, Question)
        opts = q.get("dtmf") or {}
        if not opts:
            break
        key = rng.choice(list(opts.keys()))
        val = (opts[key].get("set") or {}).get(q.raw["writes"])
        if val is None:
            break
        answers = {**answers, q.raw["writes"]: val}


# ---------------------------------------------------------------------------
# 2. Determinism: same answers, same DB, LLM disabled -> same question,
#    every time, no matter how many times asked.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(15))
def test_deterministic_rank1_for_same_answers(demo_db, seed):
    rng = random.Random(seed + 500)
    answers = walk_to_random_depth(rng, rng.randint(0, 5), demo_db)
    results = {
        pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False).id
        if pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False) else None
        for _ in range(5)
    }
    assert len(results) == 1


# ---------------------------------------------------------------------------
# 3. Never re-asks an already-answered attribute, across many random depths.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(15))
def test_never_reasks_answered_attribute(demo_db, seed):
    rng = random.Random(seed + 2000)
    answers = walk_to_random_depth(rng, rng.randint(1, 6), demo_db)
    q = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    if q is not None:
        assert q.raw["writes"] not in answers


# ---------------------------------------------------------------------------
# 4. Adversarial LLM responses: every shape of "not a clean id" must fall
#    back to rank 1 without raising.
# ---------------------------------------------------------------------------
ADVERSARIAL_PAYLOADS = [
    "",
    "null",
    "42",
    "[]",
    "{}",
    '{"id": null}',
    '{"id": 123}',
    '{"id": ["Q001_LANGUAGE"]}',
    '{"wrong_key": "Q001_LANGUAGE"}',
    "{'id': 'Q001_LANGUAGE'}",  # python-repr, not valid JSON
    '{"id": "Q001_LANGUAGE", "extra": ' + "x" * 200 + "}",  # oversized/malformed tail
    "<script>alert(1)</script>",
    "SELECT * FROM schemes;",
    "\x00\x01\x02",
    "   \n\t  ",
]


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_adversarial_llm_payloads_fall_back_to_rank1(demo_db, payload, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    answers = {"language": "hi", "intent": "find_for_me"}

    def caller(prompt, ids):
        return payload

    expected = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=caller)
    assert got.id == expected.id


def test_llm_caller_raises_exception_falls_back_to_rank1(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    answers = {"language": "hi", "intent": "find_for_me"}

    def exploding_caller(prompt, ids):
        raise ConnectionError("network unreachable")

    expected = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    got = pick_question(answers, [], BANK, db_path=demo_db, llm_caller=exploding_caller)
    assert got.id == expected.id


# ---------------------------------------------------------------------------
# 5. LLM never sees scheme names or SQL - only ids + why text + answers.
# ---------------------------------------------------------------------------
def test_llm_prompt_payload_never_contains_scheme_identifiers(demo_db, monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    answers = {"language": "hi", "intent": "find_for_me"}
    candidates = narrow(answers, demo_db)
    scheme_names = {c.scheme_name for c in candidates}
    scheme_slugs = {c.slug for c in candidates}

    captured = {}

    def capturing_caller(prompt, ids):
        captured["prompt"] = prompt
        return json.dumps({"id": ids[0]})

    pick_question(answers, candidates, BANK, db_path=demo_db, llm_caller=capturing_caller)
    prompt = captured["prompt"]
    for name in scheme_names:
        assert name not in prompt
    for slug in scheme_slugs:
        assert slug not in prompt
    assert "SELECT" not in prompt.upper()


# ---------------------------------------------------------------------------
# 6. Worst-case information-gain ranking sanity: the chosen rank-1 question
#    must never have a worse (larger) worst-case survivor count than any
#    other askable question with a real dtmf branch, across many states.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(20))
def test_rank1_is_never_worse_than_alternatives(demo_db, seed):
    from haqdaar.select import _worst_case_survivors

    rng = random.Random(seed + 9000)
    answers = walk_to_random_depth(rng, rng.randint(0, 4), demo_db)
    askable = BANK.askable(answers)
    if len(askable) < 2:
        return
    picked = pick_question(answers, [], BANK, db_path=demo_db, llm_enabled=False)
    picked_worst = _worst_case_survivors(picked, answers, demo_db)
    for q in askable:
        other_worst = _worst_case_survivors(q, answers, demo_db)
        assert picked_worst <= other_worst


# ---------------------------------------------------------------------------
# 7. The user's explicit ask: ~100 scenarios exercised end to end.
# ---------------------------------------------------------------------------
def test_100_random_scenarios_never_crash(demo_db):
    rng = random.Random(123)
    for i in range(100):
        depth = rng.randint(0, 8)
        llm_on = rng.choice([True, False])
        answers = walk_to_random_depth(rng, depth, demo_db)

        def flaky_caller(prompt, ids, _i=i):
            choice = _i % 5
            if choice == 0:
                return json.dumps({"id": ids[0]})
            if choice == 1:
                return "not json"
            if choice == 2:
                return json.dumps({"id": "NOPE"})
            if choice == 3:
                raise RuntimeError("boom")
            return json.dumps({"id": ids[-1]})

        q = pick_question(
            answers, [], BANK, db_path=demo_db,
            llm_enabled=llm_on, llm_caller=flaky_caller,
        )
        assert q is None or isinstance(q, Question)
