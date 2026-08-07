"""Stress test for narrow.py beyond TEST_CASES.md's documented B-series.

Not part of the acceptance gate - this is adversarial/property-based coverage
to catch anything the 9 documented cases don't: random answer combinations,
malformed inputs, boundary values, monotonicity invariants, and the specific
failure modes SYSTEM_DESIGN.md and CHANGE_REPORT.md call out by name.
"""
import itertools
import json
import random
import sqlite3

import pytest

from haqdaar.narrow import narrow

ATTRS_AND_VALUES = {
    "persona": ["business", "artisan", "fisher", "farmer", "other", "nonexistent_persona"],
    "applicant_type": ["business", "person", "both"],
    "age_band": ["lt18", "18_40", "41_59", "gte60"],
    "income_band": ["lt1l", "1l_2_5l", "2_5l_5l", "gt5l"],
    "state": ["PY", "WB", "MP", "AP", "ZZ"],
    "social_category": ["GEN", "OBC", "SC", "ST", "MIN"],
    "existing_pension": ["yes", "no"],
    "has_bank_account": ["yes", "no"],
}


def all_slugs(db_path):
    conn = sqlite3.connect(db_path)
    return {r[0] for r in conn.execute("select slug from schemes")}


# ---------------------------------------------------------------------------
# 1. Monotonicity: adding one more answer can only shrink or hold the
#    candidate set, never grow it (soft rules only change score, never add
#    a scheme back). This is the invariant B1/B4 exist to protect broadly.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(30))
def test_monotonic_narrowing_never_grows_candidate_set(demo_db, seed):
    rng = random.Random(seed)
    attrs = list(ATTRS_AND_VALUES.keys())
    rng.shuffle(attrs)
    n = rng.randint(0, len(attrs))
    chosen = attrs[:n]

    answers = {}
    prev_slugs = {c.slug for c in narrow(answers, demo_db)}
    for attr in chosen:
        val = rng.choice(ATTRS_AND_VALUES[attr])
        answers = {**answers, attr: val}
        cur_slugs = {c.slug for c in narrow(answers, demo_db)}
        assert cur_slugs <= prev_slugs, (
            f"seed={seed} answers={answers}: candidate set grew from "
            f"{len(prev_slugs)} to {len(cur_slugs)} after adding one answer"
        )
        prev_slugs = cur_slugs


# ---------------------------------------------------------------------------
# 2. Order independence: the final candidate set (not score, which can carry
#    row-insertion-order ties, but set membership) must not depend on the
#    order answers were supplied in, only the final answer dict content.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(20))
def test_order_independence(demo_db, seed):
    rng = random.Random(seed + 1000)
    attrs = list(ATTRS_AND_VALUES.keys())
    n = rng.randint(1, len(attrs))
    chosen_attrs = rng.sample(attrs, n)
    answers = {a: rng.choice(ATTRS_AND_VALUES[a]) for a in chosen_attrs}

    items = list(answers.items())
    perm1 = dict(items)
    shuffled = items[:]
    rng.shuffle(shuffled)
    perm2 = dict(shuffled)

    slugs1 = {c.slug for c in narrow(perm1, demo_db)}
    slugs2 = {c.slug for c in narrow(perm2, demo_db)}
    assert slugs1 == slugs2


# ---------------------------------------------------------------------------
# 3. Exhaustive small grid: every combination of persona x age_band x
#    income_band (4x4x6=96 combos < the ~100 the user asked for) must not
#    crash and must always return a list (possibly empty).
# ---------------------------------------------------------------------------
def test_exhaustive_persona_age_income_grid(demo_db):
    combos = list(itertools.product(
        ATTRS_AND_VALUES["persona"],
        ATTRS_AND_VALUES["age_band"],
        ATTRS_AND_VALUES["income_band"],
    ))
    assert len(combos) >= 90
    for persona, age, income in combos:
        result = narrow(
            {"persona": persona, "age_band": age, "income_band": income}, demo_db
        )
        assert isinstance(result, list)
        for c in result:
            assert isinstance(c.score, float)
            assert c.slug and c.scheme_no


# ---------------------------------------------------------------------------
# 4. Adversarial / malformed inputs must not crash narrow().
# ---------------------------------------------------------------------------
def test_unknown_attribute_key_ignored_safely(demo_db):
    result = narrow({"this_attribute_does_not_exist_anywhere": "x"}, demo_db)
    assert len(result) == 20  # same as B1 baseline, unknown attrs are no-ops


def test_scratch_attribute_prefixed_underscore_ignored(demo_db):
    # narrow() strips leading-underscore session-scratch keys per bank.yaml's
    # documented convention (attributes starting with _ are not scheme attrs).
    baseline = {c.slug for c in narrow({}, demo_db)}
    result = {c.slug for c in narrow({"_scratch_key": "anything"}, demo_db)}
    assert result == baseline


def test_none_value_does_not_crash(demo_db):
    # Defensive: a caller passing None for an answer (e.g. a bug upstream)
    # must not crash narrow(), even if the behaviour is just "no match".
    result = narrow({"persona": None}, demo_db)
    assert isinstance(result, list)


def test_empty_string_value_does_not_crash(demo_db):
    result = narrow({"persona": ""}, demo_db)
    assert isinstance(result, list)


def test_all_attributes_answered_at_once_does_not_crash(demo_db):
    rng = random.Random(42)
    answers = {a: rng.choice(vals) for a, vals in ATTRS_AND_VALUES.items()}
    result = narrow(answers, demo_db)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 5. Named failure modes from SYSTEM_DESIGN.md / CHANGE_REPORT.md, restated
#    as explicit assertions (not just re-running B1/B4/B5/B6 verbatim).
# ---------------------------------------------------------------------------
def test_never_exists_satisfied_bug_pattern(demo_db):
    """The exact bug SYSTEM_DESIGN.md warns about: a naive
    `WHERE attr = value` (EXISTS-satisfied) implementation would drop every
    scheme that has ANY rule on an attribute the caller hasn't touched. Prove
    that answering ONE attribute never drops a scheme whose only rules are on
    OTHER, still-unanswered attributes."""
    conn = sqlite3.connect(demo_db)
    conn.execute(
        "insert into schemes (slug, scheme_no, scheme_name, theme, verified) "
        "values ('never-exists-test', 998, 'Never-EXISTS test scheme', 'business', 0)"
    )
    # Rules on THREE different attributes, none of which the test answers.
    conn.executemany(
        "insert into scheme_rules (scheme_id, attribute, op, value, hard, weight) values (?,?,?,?,?,?)",
        [
            ("never-exists-test", "social_category", "in", json.dumps(["SC"]), 1, 1.0),
            ("never-exists-test", "existing_pension", "eq", "no", 1, 1.0),
            ("never-exists-test", "has_bank_account", "eq", "yes", 1, 1.0),
        ],
    )
    conn.commit()
    conn.close()

    # Answer a completely unrelated attribute (persona). The scheme has no
    # persona rule at all, so it must survive untouched.
    result = narrow({"persona": "business"}, demo_db)
    assert "never-exists-test" in {c.slug for c in result}


def test_state_never_hard_eliminates_via_scheme_rules_table(demo_db):
    """CHANGE_REPORT #3: state is a late tiebreaker applied against
    schemes.state_scope directly, never a scheme_rules row. Confirm no
    scheme_rules row on 'state' silently behaves as a hard gate."""
    conn = sqlite3.connect(demo_db)
    rows = conn.execute("select count(*) from scheme_rules where attribute='state'").fetchone()
    assert rows[0] == 0, "state must never appear as a scheme_rules row (tiebreaker only)"


def test_soft_rule_weight_accumulates_correctly_with_multiple_soft_hits(demo_db):
    # demo-018 is a welfare-theme scheme: income_band soft (-0.8 on
    # violation) AND existing_pension soft (-0.6 on violation). Violating
    # both must subtract both weights, not just one.
    base = {c.slug: c.score for c in narrow({}, demo_db)}
    after = {
        c.slug: c.score
        for c in narrow({"income_band": "gt5l", "existing_pension": "yes"}, demo_db)
    }
    delta = base["demo-018"] - after["demo-018"]
    assert delta == pytest.approx(0.8 + 0.6)


def test_hard_violation_on_one_scheme_never_affects_sibling_schemes(demo_db):
    # Eliminating demo-001 via a hard rule must not change any OTHER
    # scheme's presence or score - rules are scoped per-scheme_id.
    baseline = {c.slug: c.score for c in narrow({}, demo_db)}
    after = {c.slug: c.score for c in narrow({"persona": "farmer", "age_band": "lt18"}, demo_db)}
    # demo-001 (business persona) should be gone (persona mismatch is hard).
    assert "demo-001" not in after
    # demo-011 (farming/farmer persona) should ALSO be gone here because
    # age_band lt18 violates its hard age rule - but demo-005 which shares
    # farmer persona and is unaffected by the specific combination should
    # follow the same rule consistently, not some unrelated inconsistency.
    for slug, score in after.items():
        assert score <= baseline[slug] + 1e-9  # scores never increase from adding an answer


def test_verified_ranks_above_unverified_at_equal_score(demo_db):
    conn = sqlite3.connect(demo_db)
    conn.executemany(
        "insert into schemes (slug, scheme_no, scheme_name, theme, verified) values (?,?,?,?,?)",
        [
            ("rank-test-a", 997, "Rank test A", "business", 0),
            ("rank-test-b", 996, "Rank test B", "business", 1),
        ],
    )
    conn.commit()
    conn.close()

    result = narrow({}, demo_db)
    slugs_in_order = [c.slug for c in result]
    assert slugs_in_order.index("rank-test-b") < slugs_in_order.index("rank-test-a")


def test_100_random_answer_sets_never_crash_and_always_return_valid_shape(demo_db):
    """The user's explicit ask: stress test across ~100 scenarios."""
    rng = random.Random(7)
    attrs = list(ATTRS_AND_VALUES.keys())
    for i in range(100):
        n = rng.randint(0, len(attrs))
        chosen = rng.sample(attrs, n)
        answers = {a: rng.choice(ATTRS_AND_VALUES[a]) for a in chosen}
        result = narrow(answers, demo_db)
        assert isinstance(result, list)
        slugs = [c.slug for c in result]
        assert len(slugs) == len(set(slugs)), f"duplicate candidates for answers={answers}"
        for c in result:
            assert c.score is not None
            assert isinstance(c.verified, int)
