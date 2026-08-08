"""Step 3 - the two scenarios the call is actually built around.

  A. The caller knows a scheme name -> match, ALWAYS confirm it back, and
     say plainly when we don't have it.
  B. The caller has a need -> they say it in their own words, and the
     question loop starts from what they already told us.

Every LLM here is injected (the `llm_caller` parameter that select.py and
present.py already established), so these run offline and deterministically
- conftest blanks the real API key for every test.
"""
import pytest

from haqdaar import config, present, understand
from haqdaar.bank import load_bank
from haqdaar.engine import CallState, step
from haqdaar.narrow import Candidate

BANK = load_bank()


def _candidate(slug, name):
    return Candidate(
        slug=slug, scheme_no=1, scheme_name=name, name_short_hi=name,
        benefit_one_line=None, theme="business", verified=1, score=0.0,
    )


def _at_scheme_name_question(db):
    state, _ = step(CallState(), {}, BANK, db)
    state, _ = step(state, {"dtmf": "1"}, BANK, db)  # "I know the name"
    assert state.current_question == "Q100_SCHEME_NAME"
    return state


def _at_open_need_question(db):
    state, _ = step(CallState(), {}, BANK, db)
    state, _ = step(state, {"dtmf": "2"}, BANK, db)  # "I need help"
    assert state.current_question == "Q000_OPEN_NEED"
    return state


# ---------------------------------------------------------------------------
# The gate: nothing the LLM writes is ever used directly.
# ---------------------------------------------------------------------------
def test_extract_drops_attributes_and_values_the_bank_never_declared(monkeypatch):
    """The LLM can only produce an (attribute, value) pair some keypress
    could also have produced. Everything else is dropped, not repaired."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    payload = (
        '{"attributes": {'
        '"persona": "farmer",'          # real attribute, real value  -> kept
        '"persona_type": "farmer",'     # invented attribute          -> dropped
        '"theme": "agriculture",'       # real attribute, wrong value -> dropped
        '"gender": "farmer"'            # value from another attribute-> dropped
        '}}'
    )
    out = understand.extract_answers("main kisan hoon", BANK, llm_caller=lambda s, u: payload)
    assert out == {"persona": "farmer"}


def test_extract_returns_nothing_when_the_llm_fails(monkeypatch):
    """An LLM outage must degrade the call to the plain DTMF menus, not
    break it."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")

    def boom(system, user):
        raise RuntimeError("groq is down")

    assert understand.extract_answers("main kisan hoon", BANK, llm_caller=boom) == {}


def test_extract_returns_nothing_without_an_api_key():
    assert understand.extract_answers("main kisan hoon", BANK) == {}


def test_vocabulary_comes_from_the_bank_not_a_hardcoded_list():
    """Built from the questions' own dtmf `set:` blocks, so a renamed
    value can never leave the extractor allowing something the engine
    would reject."""
    vocab = understand.build_vocabulary(BANK)
    assert "farmer" in vocab["persona"]
    assert "farming" in vocab["theme"]
    # Session bookkeeping is deliberately not extractable.
    assert "intent" not in vocab
    assert not any(a.startswith("_") for a in vocab)


# ---------------------------------------------------------------------------
# Scenario A: the caller knows a name.
# ---------------------------------------------------------------------------
def test_match_returns_none_when_the_llm_says_none(monkeypatch):
    """NONE is a real answer. Reading back the wrong scheme is far worse
    than admitting we don't have it - the caller acts on what they hear."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    cands = [_candidate("a", "Coir Training Scheme"), _candidate("b", "Coir Loan Scheme")]
    got = understand.match_scheme("coir something", cands, llm_caller=lambda s, u: '{"choice": "NONE"}')
    assert got is None


def test_match_only_ever_returns_a_shortlisted_scheme(monkeypatch):
    """An out-of-range or garbage choice falls back to the deterministic
    rank 1 - it can never produce a scheme we didn't offer."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    cands = [_candidate("a", "Coir Training Scheme"), _candidate("b", "Coir Loan Scheme")]
    for bad in ('{"choice": 99}', '{"choice": "Some Other Scheme"}', "not json at all"):
        got = understand.match_scheme("coir training", cands, llm_caller=lambda s, u: bad)
        assert got is not None and got.slug in {"a", "b"}


def test_unknown_scheme_is_reported_not_retried(demo_db):
    """THE BUG THIS FIXES: a scheme we don't hold used to be treated as
    unclear speech - retried twice, then the caller was silently dropped
    into a category menu, never once told we don't have it."""
    state = _at_scheme_name_question(demo_db)
    state2, actions = step(
        state, {"speech": "Pradhan Mantri Awas Yojana", "confidence": 0.95}, BANK, demo_db
    )
    spoken = " ".join(a["say"] for a in actions if "say" in a)
    assert "hamare paas nahi hai" in spoken, f"never said we don't have it: {spoken!r}"
    assert "samajh nahi aaya" not in spoken, "must not claim we misheard them"
    assert state2.speech_attempts == 0, "an answer must not consume a retry"
    assert state2.current_question == "Q101_SCHEME_CATEGORY"


def test_a_matched_scheme_is_always_confirmed_back(demo_db):
    """Never act on a spoken name without reading it back - the single
    guard against announcing a scheme the caller never asked for."""
    state = _at_scheme_name_question(demo_db)
    target = state.candidates[0]
    state2, actions = step(state, {"speech": target.scheme_name, "confidence": 0.95}, BANK, demo_db)
    assert state2.current_question == "Q100B_CONFIRM_SCHEME"
    assert state2.phase == "asking", "must not present before confirming"


def test_saying_no_at_the_confirm_step_re_asks_for_the_name(demo_db):
    """question_bank.yaml has always declared next: Q100_SCHEME_NAME on
    Q100B's "2: Nahi" option, and _handle_dtmf_answer ignored it - so a
    caller saying "no, that's not my scheme" was NOT asked again, they
    were dropped wherever the ranker pointed."""
    state = _at_scheme_name_question(demo_db)
    target = state.candidates[0]
    state, _ = step(state, {"speech": target.scheme_name, "confidence": 0.95}, BANK, demo_db)
    assert state.current_question == "Q100B_CONFIRM_SCHEME"

    state2, _ = step(state, {"dtmf": "2"}, BANK, demo_db)  # "Nahi, wrong scheme"
    assert state2.current_question == "Q100_SCHEME_NAME"


# ---------------------------------------------------------------------------
# Scenario B: the caller has a need.
# ---------------------------------------------------------------------------
def test_the_call_opens_on_the_two_way_fork(demo_db):
    state, actions = step(CallState(), {}, BANK, demo_db)
    assert state.current_question == "Q002_INTENT"
    say = actions[0]["say"]
    assert "naam pata hai" in say.lower()


def test_free_query_seeds_answers_and_the_next_question_follows_from_them(demo_db, monkeypatch):
    """The heart of scenario B: "main kisan hoon" must produce a FARMING
    question next, not a generic demographic one. No new selection logic -
    pick_question already ranks by real information gain; it just never
    had a seeded state to work from."""
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    monkeypatch.setattr(
        understand, "extract_answers",
        lambda text, bank, **kw: {"persona": "farmer", "theme": "farming"},
    )
    state = _at_open_need_question(demo_db)
    before = len(state.candidates)

    state2, _ = step(
        state, {"speech": "main kisan hoon, mujhe kheti ke liye madad chahiye", "confidence": 0.95},
        BANK, demo_db,
    )
    assert state2.answers["persona"] == "farmer"
    assert state2.answers["theme"] == "farming"
    assert len(state2.candidates) < before, "the seeded answers must actually narrow"
    # Seeded attributes cost no turn, so they are not charged to `asked`.
    assert "Q003B_PERSONA" not in state2.asked
    # ...and must never be asked again.
    assert state2.current_question != "Q003B_PERSONA"


def test_free_query_that_yields_nothing_still_continues(demo_db):
    """No key, a timeout, or an unusable sentence: the caller just gets
    asked the normal questions rather than hitting a dead end."""
    state = _at_open_need_question(demo_db)
    state2, actions = step(state, {"speech": "hello hello", "confidence": 0.95}, BANK, demo_db)
    assert state2.phase == "asking"
    assert state2.current_question is not None
    assert any("gather" in a for a in actions)


# ---------------------------------------------------------------------------
# The question budget.
# ---------------------------------------------------------------------------
def test_target_questions_per_call_is_actually_enforced(demo_db):
    """meta.target_questions_per_call was declared from the start and
    nothing ever read it - only the hard cap of 10 was enforced, so a call
    that never narrowed asked ten questions. Nobody stays on a helpline
    for ten questions."""
    target = BANK.meta["target_questions_per_call"]
    state, _ = step(CallState(), {}, BANK, demo_db)
    for _ in range(20):
        if state.phase != "asking":
            break
        state, _ = step(state, {"dtmf": "1"}, BANK, demo_db)

    narrowing = [q for q in state.asked if BANK.question(q).get("layer") != "session"]
    assert len(narrowing) <= target, f"asked {len(narrowing)} narrowing questions, target is {target}"


def test_session_questions_do_not_eat_the_question_budget(demo_db):
    """The intent fork and the open-need question route the call rather
    than narrow it, so charging them to the budget would leave only four
    real questions."""
    from haqdaar.engine import _narrowing_questions_asked

    state = CallState(asked=("Q002_INTENT", "Q000_OPEN_NEED", "Q003B_PERSONA"))
    assert _narrowing_questions_asked(state, BANK) == 1


# ---------------------------------------------------------------------------
# Spoken text quality.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "benefits",
    [
        "1. The beneficiary will get financial assistance for the purchase of equipment.",
        "Under the scheme, Rs. 5,000 is paid to each worker every month.",
        "100 kgs. of rice is provided to each fisherman's family during the ban.",
    ],
)
def test_spoken_benefit_is_never_a_bare_fragment(benefits):
    """A plain [.!?] split announced six real schemes as "1.", "Rs." and
    "100 kgs." - which is what callers actually heard read aloud."""
    got = present._first_sentence(benefits)
    assert len(got) >= 25, f"fragment would be spoken: {got!r}"
    assert got.rstrip(".").strip() not in {"1", "Rs", "100 kgs"}
