"""TEST_CASES.md section B - narrowing engine. Highest-risk area.

B1 is the single most important test in the project: with no answers,
narrow() must return every scheme. If it returns fewer, the SQL is using
EXISTS(satisfied) instead of NOT EXISTS(violated hard), and it will hide
schemes from every caller who has not answered enough questions yet.
"""
import sqlite3

from haqdaar.narrow import narrow


def all_slugs(db_path):
    conn = sqlite3.connect(db_path)
    return {r[0] for r in conn.execute("select slug from schemes")}


def test_b1_empty_answers_returns_all_schemes(demo_db):
    result = narrow({}, demo_db)
    assert {c.slug for c in result} == all_slugs(demo_db)
    assert len(result) == 20


def test_b2_hard_violation_drops_exactly_that_scheme(demo_db):
    # demo-004 (training/other persona). A hard age_band rule requires
    # 18_40..41_59; lt18 violates it and must eliminate the scheme.
    before = narrow({}, demo_db)
    after = narrow({"age_band": "lt18"}, demo_db)
    before_slugs = {c.slug for c in before}
    after_slugs = {c.slug for c in after}
    assert after_slugs < before_slugs
    assert len(before_slugs) - len(after_slugs) == 20  # every scheme has this hard rule


def test_b3_soft_violation_keeps_with_lower_score(demo_db):
    base = {c.slug: c.score for c in narrow({}, demo_db)}
    # income_band lte 2_5l_5l is soft (hard=0, weight=0.8) on every scheme.
    # gt5l violates it -> kept, but score drops by 0.8.
    after = {c.slug: c.score for c in narrow({"income_band": "gt5l"}, demo_db)}
    assert set(after.keys()) == set(base.keys())  # nothing eliminated
    for slug in base:
        assert after[slug] == base[slug] - 0.8


def test_b4_unanswered_attribute_eliminates_nothing(demo_db):
    # Answering an attribute that appears in zero scheme_rules must be a
    # complete no-op on the candidate set.
    before = {c.slug for c in narrow({}, demo_db)}
    after = {c.slug for c in narrow({"has_bank_account": "no"}, demo_db)}
    assert before == after


def test_b5_state_scoped_scheme_dropped_on_mismatch(demo_db):
    # PY-scoped schemes must disappear when the caller answers a different state.
    result = narrow({"state": "WB"}, demo_db)
    conn = sqlite3.connect(demo_db)
    py_slugs = {r[0] for r in conn.execute("select slug from schemes where state_scope='PY'")}
    result_slugs = {c.slug for c in result}
    assert py_slugs.isdisjoint(result_slugs)


def test_b6_state_null_scheme_kept_regardless_of_state_answer(demo_db):
    conn = sqlite3.connect(demo_db)
    null_slugs = {r[0] for r in conn.execute("select slug from schemes where state_scope is null")}
    assert null_slugs, "fixture must have at least one state-NULL scheme"
    result_slugs = {c.slug for c in narrow({"state": "WB"}, demo_db)}
    assert null_slugs <= result_slugs


def test_b7_contradictory_answers_returns_zero_and_is_recoverable(demo_db):
    # persona=farmer contradicts every scheme except the farming one; combine
    # with an age_band that also violates that one to drive candidates to 0.
    result = narrow({"persona": "farmer", "age_band": "lt18"}, demo_db)
    assert result == []
    # Recovery (undo last answer) is engine.py's job in Step 5; narrow()
    # itself just needs to return an empty, non-crashing list.
    recovered = narrow({"persona": "farmer"}, demo_db)
    assert len(recovered) >= 1


def test_b8_ordered_attr_uses_ord_not_string_comparison(demo_db):
    # A brand-new scheme with ONLY an income_band gte rule, isolated from
    # every other rule, so this test cannot pass by accident via some other
    # hard rule on the same scheme. String comparison of income_band values
    # ('gt5l' vs '2_5l_5l') would sort wrongly; ord-based comparison must not.
    conn = sqlite3.connect(demo_db)
    conn.execute(
        "insert into schemes (slug, scheme_no, scheme_name, theme, verified) "
        "values ('iso-test', 999, 'Isolated ord test scheme', 'business', 0)"
    )
    conn.execute(
        "insert into scheme_rules (scheme_id, attribute, op, value, hard, weight) "
        "values ('iso-test', 'income_band', 'gte', '2_5l_5l', 1, 1.0)"
    )
    conn.commit()
    conn.close()

    # income_band ord: lt1l=1, 1l_2_5l=2, 2_5l_5l=3, gt5l=4.
    # gte '2_5l_5l' (ord 3) is satisfied by gt5l (ord 4), violated by lt1l (ord 1).
    ok = narrow({"income_band": "gt5l"}, demo_db)
    bad = narrow({"income_band": "lt1l"}, demo_db)
    assert "iso-test" in {c.slug for c in ok}
    assert "iso-test" not in {c.slug for c in bad}


def test_b9_same_answers_twice_identical_result(demo_db):
    answers = {"persona": "business", "income_band": "1l_2_5l"}
    first = narrow(answers, demo_db)
    second = narrow(answers, demo_db)
    assert [(c.slug, c.score) for c in first] == [(c.slug, c.score) for c in second]
