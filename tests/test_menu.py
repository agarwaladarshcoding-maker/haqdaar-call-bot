"""TEST_CASES.md section H - menus and dial codes (Step 6).

H8 is a GATE: section_text() must return the DB column byte-identical,
minus only the H9 deadline-sentence strip for verified=0 rows.
"""
import sqlite3

import pytest

from haqdaar import menu


@pytest.fixture(autouse=True)
def _menu_db(demo_db):
    menu._set_db_path_for_testing(demo_db)
    yield
    menu._set_db_path_for_testing(None)


# ---------------------------------------------------------------------------
# H1/H2: option count and reserved keys
# ---------------------------------------------------------------------------
def test_h1_main_menu_never_exceeds_six_options(demo_db):
    assert len(menu.main_menu()) <= 6


def test_h1_need_menu_never_exceeds_six_options(demo_db):
    assert len(menu.need_menu("1")) <= 6


def test_h1_schemes_for_never_exceeds_six_per_page(demo_db):
    listings, _ = menu.schemes_for("1")
    assert len(listings) <= 6


def test_h2_reserved_keys_never_appear_as_menu_options(demo_db):
    for opt in menu.main_menu():
        assert opt.key not in menu.RESERVED_KEYS
    for opt in menu.need_menu("1"):
        assert opt.key not in menu.RESERVED_KEYS


# ---------------------------------------------------------------------------
# H3/H4: level-2 only for business, everything else goes straight to a list
# ---------------------------------------------------------------------------
def test_h3_level1_option_one_offers_need_groups(demo_db):
    needs = menu.need_menu("1")
    assert len(needs) == 6
    assert {n.key for n in needs} == {"1", "2", "3", "4", "5", "6"}


def test_h4_other_level1_options_have_no_need_menu(demo_db):
    for ps_key in ["2", "3", "4", "5", "6"]:
        assert menu.need_menu(ps_key) == []


def test_h4_other_level1_options_go_straight_to_scheme_list(demo_db):
    listings, _ = menu.schemes_for("2")  # craft
    assert len(listings) >= 1
    for l in listings:
        assert l.slug.startswith("demo-")


# ---------------------------------------------------------------------------
# H5: paging
# ---------------------------------------------------------------------------
def test_h5_paging_covers_every_scheme_with_no_gaps_or_dupes(demo_db):
    conn = sqlite3.connect(demo_db)
    conn.executemany(
        "INSERT INTO schemes (slug, scheme_no, scheme_name, theme, need_group, verified) VALUES (?,?,?,?,?,1)",
        [(f"page-test-{i}", 900 + i, f"Page Test Scheme {i}", "business", "machine_setup") for i in range(15)],
    )
    conn.commit()
    conn.close()

    seen_slugs = []
    page = 0
    while True:
        listings, has_more = menu.schemes_for("1", need_key="1", page=page)
        seen_slugs.extend(l.slug for l in listings)
        if not has_more:
            break
        page += 1
        assert page < 50, "paging did not terminate - possible infinite loop"

    page_test_seen = [s for s in seen_slugs if s.startswith("page-test-")]
    assert len(page_test_seen) == 15
    assert len(set(page_test_seen)) == 15  # no duplicates across pages


# ---------------------------------------------------------------------------
# H6/H7: dial code resolution
# ---------------------------------------------------------------------------
def test_h6_valid_dial_code_resolves_to_right_scheme_and_section(demo_db):
    result = menu.resolve_code("011")  # scheme_no=1, section=1 (benefits)
    assert result is not None
    slug, sec_key = result
    conn = sqlite3.connect(demo_db)
    row = conn.execute("SELECT slug, benefits FROM schemes WHERE scheme_no=1").fetchone()
    assert slug == row[0]
    text = menu.section_text(slug, sec_key)
    assert text == row[1]


def test_h7_invalid_dial_code_returns_none_gracefully(demo_db):
    assert menu.resolve_code("999") is None  # no such scheme_no
    assert menu.resolve_code("019") is None  # scheme exists, section 9 doesn't
    assert menu.resolve_code("ab1") is None
    assert menu.resolve_code("1") is None
    assert menu.resolve_code("") is None
    assert menu.resolve_code(None) is None


# ---------------------------------------------------------------------------
# H8 - GATE: byte-identical section text
# ---------------------------------------------------------------------------
def test_h8_section_text_byte_identical_to_db_column(demo_db):
    conn = sqlite3.connect(demo_db)
    row = conn.execute("SELECT slug, eligibility, verified FROM schemes WHERE scheme_no=1").fetchone()
    slug, eligibility, verified = row
    assert verified == 1  # demo-001 is a verified scheme, no stripping applies
    text = menu.section_text(slug, "2")  # 2 = eligibility
    assert text == eligibility


def test_h8_all_five_sections_map_to_correct_columns(demo_db):
    conn = sqlite3.connect(demo_db)
    row = conn.execute(
        "SELECT slug, benefits, eligibility, documents, application, details "
        "FROM schemes WHERE scheme_no=1"
    ).fetchone()
    slug, benefits, eligibility, documents, application, details = row
    assert menu.section_text(slug, "1") == benefits
    assert menu.section_text(slug, "2") == eligibility
    assert menu.section_text(slug, "3") == documents
    # section 4 (application) on scheme_no=1 has no deadline text (only
    # every-5th demo scheme does), so this is still a byte-identical check.
    assert menu.section_text(slug, "4") == application
    assert menu.section_text(slug, "5") == details


# ---------------------------------------------------------------------------
# H9: deadline stripped only for verified=0
# ---------------------------------------------------------------------------
def test_h9_deadline_sentence_stripped_for_unverified_scheme(demo_db):
    conn = sqlite3.connect(demo_db)
    row = conn.execute("SELECT slug, application FROM schemes WHERE scheme_no=5").fetchone()
    slug, raw_application = row
    assert "last date" in raw_application.lower()  # sanity: fixture actually has one

    spoken = menu.section_text(slug, "4")
    assert "last date" not in spoken.lower()
    assert "deadline" not in spoken.lower()
    # The non-deadline part of the sentence must survive untouched.
    assert "Apply at the nearest block office" in spoken


def test_h9_verified_scheme_keeps_full_text_even_if_it_mentioned_a_date(demo_db):
    conn = sqlite3.connect(demo_db)
    conn.execute(
        "INSERT INTO schemes (slug, scheme_no, scheme_name, theme, application, verified) "
        "VALUES ('verified-with-date', 950, 'Verified Date Scheme', 'business', "
        "'Apply before the last date of 1 January.', 1)"
    )
    conn.commit()
    conn.close()
    text = menu.section_text("verified-with-date", "4")
    assert "last date" in text.lower()  # verified=1 -> never stripped


def test_h9_strip_deadline_sentence_leaves_other_sentences_alone():
    text = "Bring your documents. Apply before the last date of 31 March. Visit the office."
    stripped = menu.strip_deadline_sentence(text)
    assert "Bring your documents." in stripped
    assert "Visit the office." in stripped
    assert "last date" not in stripped.lower()


def test_h9_strip_deadline_sentence_handles_no_deadline_present():
    text = "This scheme has no expiry mentioned at all."
    assert menu.strip_deadline_sentence(text) == text


def test_h9_strip_deadline_sentence_handles_empty_and_none():
    assert menu.strip_deadline_sentence("") == ""
    assert menu.strip_deadline_sentence(None) == ""


# ---------------------------------------------------------------------------
# H10: digits spoken as digits
# ---------------------------------------------------------------------------
def test_h10_dial_code_spoken_as_individual_digits():
    assert menu.speak_digits_hi("231") == "do teen ek"
    assert menu.speak_digits_hi("000") == "shunya shunya shunya"
    assert menu.speak_digits_hi("905") == "nau shunya paanch"


# ---------------------------------------------------------------------------
# H11: dial codes survive a menu reshuffle (decoupled from menu structure)
# ---------------------------------------------------------------------------
def test_h11_dial_code_independent_of_problem_statement_order(demo_db):
    before = menu.resolve_code("011")
    conn = sqlite3.connect(demo_db)
    # Reshuffle the LEVEL-1 menu order (ord column) - this must have zero
    # effect on dial code resolution, since resolve_code never touches
    # problem_statements at all.
    conn.execute("UPDATE problem_statements SET ord = ord + 100 WHERE ps_key = '1'")
    conn.commit()
    conn.close()
    after = menu.resolve_code("011")
    assert before == after


def test_h11_rerun_ingest_style_reseed_keeps_scheme_no_stable(demo_db):
    # scripts/seed_demo.py assigns scheme_no in sorted-slug order (A8's
    # guarantee, exercised here at the menu layer) - re-running it against
    # the same source data must not change what a previously-noted code
    # resolves to.
    import subprocess
    import sys
    import os

    before = menu.resolve_code("011")
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py"), demo_db], check=True, capture_output=True)
    after = menu.resolve_code("011")
    assert before == after
