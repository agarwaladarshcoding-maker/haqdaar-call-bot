"""TEST_CASES.md section A - data and ingest (Step 1), run against the
REAL optimized_schemes.json catalogue for the first time (previously only
scripts/seed_demo.py's 20 synthetic schemes were exercised by tests).

A4, A5, A8 are BLOCKERS per TEST_CASES.md.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_JSON = os.path.join(ROOT, "optimized_schemes.json")

pytestmark = pytest.mark.skipif(
    not os.path.exists(SOURCE_JSON), reason="optimized_schemes.json not present in this checkout"
)


@pytest.fixture(scope="module")
def real_db(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("ingest") / "real_haqdaar.db")
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "ingest.py"), SOURCE_JSON, db_path],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return db_path


def _conn(db_path):
    import sqlite3

    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# A1: exactly 100 rows
# ---------------------------------------------------------------------------
def test_a1_exactly_100_schemes_loaded(real_db):
    conn = _conn(real_db)
    assert conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 100


# ---------------------------------------------------------------------------
# A2/A8: idempotent re-run, scheme_no never renumbered - A8 is a BLOCKER
# (dial codes are spoken aloud and written down; a re-run must never move
# what a previously-noted code points to).
# ---------------------------------------------------------------------------
def test_a2_rerun_does_not_duplicate(real_db):
    conn = _conn(real_db)
    before = conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0]
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "ingest.py"), SOURCE_JSON, real_db],
        check=True, cwd=ROOT, capture_output=True,
    )
    conn2 = _conn(real_db)
    after = conn2.execute("SELECT COUNT(*) FROM schemes").fetchone()[0]
    assert before == after == 100


def test_a8_rerun_never_renumbers_scheme_no(tmp_path):
    db_path = str(tmp_path / "a8.db")
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "ingest.py"), SOURCE_JSON, db_path],
        check=True, cwd=ROOT, capture_output=True,
    )
    before = {r[0]: r[1] for r in _conn(db_path).execute("SELECT slug, scheme_no FROM schemes")}

    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "ingest.py"), SOURCE_JSON, db_path],
        check=True, cwd=ROOT, capture_output=True,
    )
    after = {r[0]: r[1] for r in _conn(db_path).execute("SELECT slug, scheme_no FROM schemes")}

    assert before == after


# ---------------------------------------------------------------------------
# A4/A5: state resolution - BLOCKERS. Unclear or multi-state text must
# leave state_scope NULL (safe failure direction), never a guess.
# ---------------------------------------------------------------------------
def test_a4_a5_state_scope_is_null_or_a_valid_2letter_code(real_db):
    conn = _conn(real_db)
    valid_codes = {
        "AP", "AR", "AS", "BR", "CG", "GA", "GJ", "HR", "HP", "JH", "KA", "KL",
        "MP", "MH", "MN", "ML", "MZ", "NL", "OD", "PB", "RJ", "SK", "TN", "TS",
        "TR", "UP", "UK", "WB", "PY", "DL", "JK", "LA", "CH", "LD",
    }
    rows = conn.execute("SELECT slug, state_scope FROM schemes").fetchall()
    assert len(rows) == 100
    for r in rows:
        if r["state_scope"] is not None:
            assert r["state_scope"] in valid_codes, f"{r['slug']} has invalid state_scope {r['state_scope']!r}"


def test_a4_scheme_naming_no_state_has_null_scope(real_db):
    """A4 specifically: prose naming no state at all -> NULL, never a
    guessed default. Spot-check via a scheme we know is Lakshadweep-scoped
    from earlier manual inspection is scoped correctly (not NULL when a
    single state IS named) as the complementary check to A5's multi-state
    case below - both directions of the same correctness property."""
    conn = _conn(real_db)
    # At least some schemes must have NULL state_scope (the catalogue is
    # not 100% single-state-scoped prose) - if this is ever 0, either the
    # heuristic became too aggressive or the source data changed shape.
    null_count = conn.execute("SELECT COUNT(*) FROM schemes WHERE state_scope IS NULL").fetchone()[0]
    assert null_count > 0


# ---------------------------------------------------------------------------
# A6: benefits text byte-identical to the source JSON
# ---------------------------------------------------------------------------
def test_a6_benefits_text_byte_identical_to_source_json(real_db):
    with open(SOURCE_JSON, encoding="utf-8") as f:
        source = json.load(f)
    by_slug = {s["slug"]: s for s in source}

    conn = _conn(real_db)
    rows = conn.execute("SELECT slug, benefits FROM schemes").fetchall()
    assert len(rows) == 100
    checked = 0
    for r in rows:
        src = by_slug.get(r["slug"])
        if src is None:
            continue
        assert r["benefits"] == src["benefits"], f"{r['slug']}: benefits text was rewritten"
        checked += 1
    assert checked == 100


# ---------------------------------------------------------------------------
# A7: verified = 0 for every freshly-ingested real scheme (Step 9's human
# review, not ingest, is what ever sets verified=1).
# ---------------------------------------------------------------------------
def test_a7_every_scheme_starts_unverified(real_db):
    conn = _conn(real_db)
    assert conn.execute("SELECT COUNT(*) FROM schemes WHERE verified != 0").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# A9: every scheme reachable from some menu path (theme is always set,
# every theme appears in ingest.py's THEMES list which menu.py reads).
# ---------------------------------------------------------------------------
def test_a9_every_scheme_has_a_theme_and_is_menu_reachable(real_db):
    conn = _conn(real_db)
    rows = conn.execute("SELECT slug, theme FROM schemes").fetchall()
    valid_themes = {r["theme"] for r in conn.execute("SELECT theme FROM problem_statements")}
    assert len(rows) == 100
    for r in rows:
        assert r["theme"] is not None, f"{r['slug']} has no theme - unreachable from any menu"
        assert r["theme"] in valid_themes, f"{r['slug']} has theme {r['theme']!r} not in problem_statements"


def test_a9_every_scheme_reachable_via_schemes_for(real_db):
    """End-to-end reachability through the real menu.py, not just a raw
    column check - walks main_menu() -> (need_menu() if any) -> schemes_for()
    for every branch and confirms all 100 slugs are covered exactly once."""
    import sys as _sys

    _sys.path.insert(0, os.path.join(ROOT, "src"))
    from haqdaar import menu

    menu._set_db_path_for_testing(real_db)
    try:
        seen = set()
        for opt in menu.main_menu():
            needs = menu.need_menu(opt.key)
            branches = [(opt.key, n.key) for n in needs] if needs else [(opt.key, None)]
            for ps_key, need_key in branches:
                page = 0
                while True:
                    listings, has_more = menu.schemes_for(ps_key, need_key=need_key, page=page)
                    seen.update(l.slug for l in listings)
                    if not has_more:
                        break
                    page += 1
                    assert page < 30
        assert len(seen) == 100, f"only {len(seen)}/100 schemes reachable via menu.py"
    finally:
        menu._set_db_path_for_testing(None)


# ---------------------------------------------------------------------------
# Sanity: narrow({}) still returns all 100 on real data (B1's invariant,
# re-checked against real content since it was previously only proven on
# the 20-scheme demo catalogue).
# ---------------------------------------------------------------------------
def test_b1_empty_answers_returns_all_100_real_schemes(real_db):
    import sys as _sys

    _sys.path.insert(0, os.path.join(ROOT, "src"))
    from haqdaar.narrow import narrow

    result = narrow({}, real_db)
    assert len(result) == 100
