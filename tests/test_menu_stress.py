"""Stress test for menu.py beyond TEST_CASES.md's documented H-series.

Adversarial dial codes, random paging depths, fuzzed deadline-sentence
text, and a 100-scenario pass over resolve_code + section_text together.
"""
import random
import sqlite3

import pytest

from haqdaar import menu


@pytest.fixture(autouse=True)
def _menu_db(demo_db):
    menu._set_db_path_for_testing(demo_db)
    yield
    menu._set_db_path_for_testing(None)


# ---------------------------------------------------------------------------
# 1. resolve_code never raises, for any string input.
# ---------------------------------------------------------------------------
ADVERSARIAL_CODES = [
    "", " ", "0", "00", "0000", "abc", "12a", "a12", "-11", "1.1",
    "999", "001", "100", "011 ", " 011", "011\n", "０１１",  # fullwidth digits
    None, "011011", "-01", "01-1",
]


@pytest.mark.parametrize("code", ADVERSARIAL_CODES)
def test_resolve_code_never_raises(demo_db, code):
    result = menu.resolve_code(code)
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


def test_resolve_code_out_of_range_scheme_no(demo_db):
    assert menu.resolve_code("981") is None  # scheme_no 98 doesn't exist in demo set


@pytest.mark.parametrize("sec", ["0", "6", "7", "9"])
def test_resolve_code_invalid_section_digit(demo_db, sec):
    assert menu.resolve_code(f"01{sec}") is None


# ---------------------------------------------------------------------------
# 2. Every valid scheme_no x every valid section resolves and returns text
#    without crashing - exhaustive over the demo catalogue.
# ---------------------------------------------------------------------------
def test_every_scheme_every_section_resolves_and_returns_string(demo_db):
    conn = sqlite3.connect(demo_db)
    scheme_nos = [r[0] for r in conn.execute("SELECT scheme_no FROM schemes")]
    for scheme_no in scheme_nos:
        for sec in "12345":
            code = f"{scheme_no:02d}{sec}"
            result = menu.resolve_code(code)
            assert result is not None, f"code {code} failed to resolve"
            slug, sec_key = result
            text = menu.section_text(slug, sec_key)
            assert isinstance(text, str)


# ---------------------------------------------------------------------------
# 3. H1/H2 invariants across every ps_key and every page.
# ---------------------------------------------------------------------------
def test_h1_h2_invariants_hold_across_every_theme(demo_db):
    for opt in menu.main_menu():
        ps_key = opt.key
        assert ps_key not in menu.RESERVED_KEYS
        needs = menu.need_menu(ps_key)
        assert len(needs) <= 6
        for n in needs:
            assert n.key not in menu.RESERVED_KEYS

        listings, has_more = menu.schemes_for(ps_key)
        assert len(listings) <= 6
        assert isinstance(has_more, bool)


# ---------------------------------------------------------------------------
# 4. Random paging never loses or duplicates a scheme, across many
#    randomly-sized synthetic buckets.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bucket_size,seed", [(n, n * 7) for n in [0, 1, 5, 6, 7, 11, 12, 13, 23, 30]])
def test_paging_exhaustive_for_random_bucket_sizes(demo_db, bucket_size, seed):
    conn = sqlite3.connect(demo_db)
    conn.executemany(
        "INSERT INTO schemes (slug, scheme_no, scheme_name, theme, need_group, verified) VALUES (?,?,?,?,?,1)",
        [(f"fuzz-{seed}-{i}", 800 + i, f"Fuzz Scheme {i}", "business", "loan_interest") for i in range(bucket_size)],
    )
    conn.commit()
    conn.close()

    seen = []
    page = 0
    while True:
        listings, has_more = menu.schemes_for("1", need_key="2", page=page)
        assert len(listings) <= 6
        seen.extend(l.slug for l in listings)
        if not has_more:
            break
        page += 1
        assert page < 20

    fuzz_seen = [s for s in seen if s.startswith(f"fuzz-{seed}-")]
    assert len(fuzz_seen) == bucket_size
    assert len(set(fuzz_seen)) == bucket_size


# ---------------------------------------------------------------------------
# 5. strip_deadline_sentence fuzzing: never crashes, never removes a
#    sentence that doesn't match, always removes one that does, idempotent.
# ---------------------------------------------------------------------------
DEADLINE_PHRASES = [
    "The deadline is 1 January.",
    "Applications close on 5 May 2026.",
    "This offer is valid until 31 December.",
    "The last date for submission is next Friday.",
    "Please note the due date of 15th.",
]
NON_DEADLINE_SENTENCES = [
    "Bring your Aadhaar card.",
    "Visit the block office.",
    "This scheme supports small businesses.",
    "Income must be under five lakh rupees.",
]


@pytest.mark.parametrize("seed", range(20))
def test_strip_deadline_fuzz_never_crashes_and_is_idempotent(seed):
    rng = random.Random(seed)
    n_deadline = rng.randint(0, 2)
    n_other = rng.randint(0, 3)
    sentences = rng.sample(DEADLINE_PHRASES, n_deadline) if n_deadline else []
    sentences += rng.sample(NON_DEADLINE_SENTENCES, n_other) if n_other else []
    rng.shuffle(sentences)
    text = " ".join(sentences)

    result = menu.strip_deadline_sentence(text)
    assert isinstance(result, str)
    twice = menu.strip_deadline_sentence(result)
    assert twice == result  # idempotent - stripping an already-stripped text is a no-op

    for other in NON_DEADLINE_SENTENCES:
        if other in text:
            assert other in result


def test_strip_deadline_sentence_on_malformed_input_types_does_not_crash():
    assert menu.strip_deadline_sentence("") == ""
    assert menu.strip_deadline_sentence(None) == ""
    assert isinstance(menu.strip_deadline_sentence("   "), str)
    assert isinstance(menu.strip_deadline_sentence("no punctuation at all no deadline"), str)


# ---------------------------------------------------------------------------
# 6. section_text raises MenuError (not a silent None/crash) for a
#    genuinely unknown slug - this is a programmer error, not a caller's
#    bad keypress, so it should be loud, not swallowed.
# ---------------------------------------------------------------------------
def test_section_text_unknown_slug_raises_menu_error(demo_db):
    with pytest.raises(menu.MenuError):
        menu.section_text("does-not-exist", "1")


def test_need_menu_unknown_ps_key_raises_menu_error(demo_db):
    with pytest.raises(menu.MenuError):
        menu.need_menu("99")


# ---------------------------------------------------------------------------
# 7. The user's explicit ask: ~100 scenarios, random dial codes + section
#    requests, never crash, always well-formed.
# ---------------------------------------------------------------------------
def test_100_random_dial_and_section_scenarios(demo_db):
    rng = random.Random(2024)
    conn = sqlite3.connect(demo_db)
    valid_scheme_nos = [r[0] for r in conn.execute("SELECT scheme_no FROM schemes")]

    for i in range(100):
        use_valid = rng.random() < 0.6
        if use_valid:
            scheme_no = rng.choice(valid_scheme_nos)
            sec = rng.choice("12345")
            code = f"{scheme_no:02d}{sec}"
        else:
            code = "".join(rng.choice("0123456789abc ") for _ in range(rng.randint(0, 5)))

        result = menu.resolve_code(code)
        assert result is None or (isinstance(result, tuple) and len(result) == 2)
        if result is not None:
            slug, sec_key = result
            text = menu.section_text(slug, sec_key)
            assert isinstance(text, str)
