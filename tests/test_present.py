"""Tests for present.py - live presentation wording (Step 9, wording half).

Mirrors select.py's J-series discipline: every LLM failure mode falls back
to a deterministic default, never crashes, never blocks the call. The
extra guardrail here (not present in select.py) is the K2/K3 verbatim
check - present.py must reject any LLM output whose benefit_line is not
an exact substring of the scheme's own DB text, since a benefit spoken to
a caller must match the database exactly (TEST_CASES.md K2/K3).
"""
import pytest

from haqdaar.narrow import Candidate
from haqdaar.present import present_many, present_one


def _candidate(slug="demo-001", name="Demo Scheme One", short_name=None, benefit_line=None) -> Candidate:
    return Candidate(
        slug=slug,
        scheme_no=1,
        scheme_name=name,
        name_short_hi=short_name,
        benefit_one_line=benefit_line,
        theme="business",
        verified=1,
        score=0.0,
    )


BENEFITS_TEXT = (
    "Rs 10000 one-time assistance for eligible business applicants. "
    "Additional Rs 5000 for women applicants. Apply within 60 days of approval."
)


# ---------------------------------------------------------------------------
# Fallback discipline: no key / disabled / timeout / malformed / verbatim
# check failure - every one of these must return a safe, non-LLM result.
# ---------------------------------------------------------------------------
def test_no_api_key_uses_fallback(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    c = _candidate(short_name="Demo Yojna", benefit_line="Rs 10000 one-time.")
    p = present_one(c, BENEFITS_TEXT, {})
    assert p.source == "fallback"
    assert p.spoken_name == "Demo Yojna"
    assert p.benefit_line == "Rs 10000 one-time."


def test_llm_disabled_uses_fallback_without_calling(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    calls = []

    def spy_caller(system, user):
        calls.append((system, user))
        return '{"spoken_name": "x", "benefit_line": "y"}'

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_enabled=False, llm_caller=spy_caller)
    assert p.source == "fallback"
    assert calls == []


def test_llm_timeout_uses_fallback(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    monkeypatch.setattr("haqdaar.config.LLM_TIMEOUT_MS", 50)
    import time

    def slow_caller(system, user):
        time.sleep(0.2)
        return '{"spoken_name": "Late", "benefit_line": "Rs 10000 one-time assistance for eligible business applicants."}'

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=slow_caller)
    assert p.source == "fallback"


def test_llm_raises_exception_uses_fallback(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def exploding_caller(system, user):
        raise RuntimeError("network exploded")

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=exploding_caller)
    assert p.source == "fallback"


@pytest.mark.parametrize(
    "bad_response",
    [
        "not json at all",
        "{}",
        '{"spoken_name": "x"}',  # missing benefit_line
        '{"benefit_line": "y"}',  # missing spoken_name
        '{"spoken_name": "", "benefit_line": "Rs 10000"}',  # empty name
        '{"spoken_name": "x", "benefit_line": ""}',  # empty line
        '["not", "a", "dict"]',
        "null",
        "42",
    ],
)
def test_malformed_llm_response_uses_fallback(monkeypatch, bad_response):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=lambda s, u: bad_response)
    assert p.source == "fallback"


# ---------------------------------------------------------------------------
# K2/K3 GATE: benefit_line must be a VERBATIM substring of the source text.
# Any paraphrase, invented amount, or dropped detail must be rejected.
# ---------------------------------------------------------------------------
def test_paraphrased_benefit_line_rejected(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def paraphrasing_caller(system, user):
        # A plausible-sounding but NOT verbatim rewording - this is exactly
        # the failure mode K2/K3 exist to catch.
        return '{"spoken_name": "Demo Yojna", "benefit_line": "You get about ten thousand rupees as one-time help."}'

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=paraphrasing_caller)
    assert p.source == "fallback"


def test_invented_amount_rejected(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def hallucinating_caller(system, user):
        # A different, larger number than what's actually in benefits text.
        return '{"spoken_name": "Demo Yojna", "benefit_line": "Rs 50000 one-time assistance for eligible business applicants."}'

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=hallucinating_caller)
    assert p.source == "fallback"
    assert "50000" not in p.benefit_line


def test_valid_verbatim_substring_accepted(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def good_caller(system, user):
        return '{"spoken_name": "Demo Yojna", "benefit_line": "Rs 10000 one-time assistance for eligible business applicants."}'

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=good_caller)
    assert p.source == "llm"
    assert p.benefit_line in BENEFITS_TEXT
    assert p.spoken_name == "Demo Yojna"


def test_partial_verbatim_substring_accepted(monkeypatch):
    """A shorter verbatim span (not necessarily a full sentence) is fine -
    the check is substring containment, not sentence-boundary matching."""
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def good_caller(system, user):
        return '{"spoken_name": "Demo Yojna", "benefit_line": "Additional Rs 5000 for women applicants."}'

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=good_caller)
    assert p.source == "llm"


def test_whitespace_only_difference_rejected(monkeypatch):
    """Even a trivial reformatting (extra space, newline) fails the strict
    substring check - this is intentional conservatism, not a bug: the
    fallback path is always safe, so there's no reason to relax the check
    to allow near-misses."""
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")

    def near_miss_caller(system, user):
        return '{"spoken_name": "Demo Yojna", "benefit_line": "Rs 10000  one-time assistance for eligible business applicants."}'  # double space

    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=near_miss_caller)
    assert p.source == "fallback"


# ---------------------------------------------------------------------------
# Fallback content itself: sane defaults with no DB fields set at all.
# ---------------------------------------------------------------------------
def test_fallback_uses_scheme_name_when_no_short_name(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    c = _candidate(name="Full Long Scheme Name", short_name=None)
    p = present_one(c, BENEFITS_TEXT, {})
    assert p.spoken_name == "Full Long Scheme Name"


def test_fallback_uses_first_sentence_when_no_benefit_one_line(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    c = _candidate(benefit_line=None)
    p = present_one(c, BENEFITS_TEXT, {})
    assert p.benefit_line == "Rs 10000 one-time assistance for eligible business applicants."


def test_fallback_handles_empty_benefits_text(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    c = _candidate(benefit_line=None)
    p = present_one(c, "", {})
    assert p.benefit_line == ""


def test_fallback_handles_none_benefits_text_gracefully(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "")
    c = _candidate(benefit_line=None)
    p = present_one(c, None, {})  # type: ignore[arg-type]
    assert isinstance(p.benefit_line, str)


# ---------------------------------------------------------------------------
# present_many: one candidate's failure never affects another's result.
# ---------------------------------------------------------------------------
def test_present_many_independent_per_candidate(monkeypatch):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    candidates = [_candidate("demo-001", "Scheme A"), _candidate("demo-002", "Scheme B")]
    benefits = {
        "demo-001": "Rs 1000 for scheme A applicants.",
        "demo-002": "Rs 2000 for scheme B applicants.",
    }

    def per_scheme_caller(system, user):
        if "Scheme A" in user:
            raise RuntimeError("scheme A's call fails")
        return '{"spoken_name": "B Short", "benefit_line": "Rs 2000 for scheme B applicants."}'

    results = present_many(candidates, benefits, {}, llm_caller=per_scheme_caller)
    assert results[0].source == "fallback"  # A's failure -> fallback
    assert results[1].source == "llm"  # B still succeeds independently


def test_present_many_empty_candidates_returns_empty():
    assert present_many([], {}, {}) == []


# ---------------------------------------------------------------------------
# Adversarial fuzz: many malformed/weird LLM responses in a row, never
# raises, always returns a valid Presentation for every candidate.
# ---------------------------------------------------------------------------
ADVERSARIAL_RESPONSES = [
    "",
    " ",
    "{",
    "}}",
    '{"spoken_name": null, "benefit_line": "Rs 10000 one-time assistance for eligible business applicants."}',
    '{"spoken_name": 123, "benefit_line": "Rs 10000 one-time assistance for eligible business applicants."}',
    '{"spoken_name": "x", "benefit_line": null}',
    '{"spoken_name": "x", "benefit_line": 123}',
    "```json\n{\"spoken_name\": \"x\", \"benefit_line\": \"y\"}\n```",  # markdown-fenced
    '{"spoken_name": "' + "x" * 5000 + '", "benefit_line": "y"}',  # huge name
]


@pytest.mark.parametrize("bad_response", ADVERSARIAL_RESPONSES)
def test_adversarial_responses_never_raise(monkeypatch, bad_response):
    monkeypatch.setattr("haqdaar.config.LLM_API_KEY", "fake-key")
    c = _candidate()
    p = present_one(c, BENEFITS_TEXT, {}, llm_caller=lambda s, u: bad_response)
    assert p is not None
    assert isinstance(p.spoken_name, str)
    assert isinstance(p.benefit_line, str)
