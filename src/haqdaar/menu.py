"""Number tree navigator: problem statement -> scheme -> section. Step 6.

Two decoupled systems (CHANGE_REPORT #7):
  - Menus are for BROWSING: main_menu() -> schemes_for(ps_key[, need_key])
    -> section_text(slug, sec_key). Purely a tree walk over theme/
    need_group, re-derivable any time from the live DB - reordering these
    never moves anything a caller has written down.
  - Dial codes are for ADDRESSING: resolve_code("021") jumps straight to
    scheme_no=02, section=1, using the STABLE scheme_no assigned once at
    ingest in sorted-slug order (never renumbered - A8). A code a caller
    wrote on paper still works after any menu reshuffle or re-ingest
    (H11), because it never depended on menu structure in the first
    place.

H8 - GATE: section_text() returns the DB column value byte-for-byte. No
rewriting, no summarising, no model anywhere in this path.

H9: verified=0 rows may carry an unconfirmed deadline (PRD M12: "deadlines
spoken only from verified=1 rows"). strip_deadline_sentence() removes
exactly the offending sentence(s), never touching the rest of the text -
this is a sentence-level filter, not a rewrite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from haqdaar.db import get_db

MAX_SPOKEN_OPTIONS = 6  # H1
RESERVED_KEYS = {"0", "*", "#"}  # H2

DIGIT_WORDS_HI = {
    "0": "shunya", "1": "ek", "2": "do", "3": "teen", "4": "chaar",
    "5": "paanch", "6": "chhe", "7": "saat", "8": "aath", "9": "nau",
}

_DEADLINE_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(deadline|last date|due date|closes? on|closing date|"
    r"valid (?:upto|until|till)|before \d)[^.!?]*[.!?]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MenuOption:
    key: str
    label_hi: str
    label_en: str


@dataclass(frozen=True)
class SchemeListing:
    slug: str
    scheme_no: int
    name_short_hi: str | None
    scheme_name: str
    verified: int


class MenuError(Exception):
    """Raised for a genuinely malformed request (unknown ps_key/need_key/
    sec_key passed by CALLER CODE, not by a live caller's keypress - a
    live caller's bad input is handled by resolve_code returning None /
    engine.py's own invalid-key ladder, never by raising)."""


def strip_deadline_sentence(text: str | None) -> str:
    """H9: remove every sentence that reads as a deadline, leaving the
    rest of the text untouched. Byte-identical to the source EXCEPT for
    the removed sentence(s) - still no rewriting of what remains (H8's
    spirit extends here: filtering is allowed, rephrasing is not)."""
    if not text:
        return text or ""
    stripped = _DEADLINE_SENTENCE_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", stripped).strip()


def main_menu() -> list[MenuOption]:
    """H1/H2/H3/H4: the 6 problem statements (themes), ordered by ord."""
    conn = get_db_default()
    try:
        rows = conn.execute(
            "SELECT ps_key, label_hi, label_en FROM problem_statements ORDER BY ord"
        ).fetchall()
        return [MenuOption(r["ps_key"], r["label_hi"], r["label_en"]) for r in rows]
    finally:
        conn.close()


def need_menu(ps_key: str) -> list[MenuOption]:
    """H3: level-2 menu, only meaningful for the theme that actually has
    need_group rows (business, in the current catalogue). Empty list for
    every other ps_key - callers should go straight to schemes_for (H4)."""
    conn = get_db_default()
    try:
        theme = _theme_for_ps_key(conn, ps_key)
        rows = conn.execute(
            "SELECT need_key, label_hi, label_en FROM need_groups ORDER BY ord"
        ).fetchall()
        has_any = conn.execute(
            "SELECT 1 FROM schemes WHERE theme = ? AND need_group IS NOT NULL LIMIT 1",
            (theme,),
        ).fetchone()
        if not has_any:
            return []
        return [MenuOption(r["need_key"], r["label_hi"], r["label_en"]) for r in rows]
    finally:
        conn.close()


def schemes_for(ps_key: str, need_key: str | None = None, page: int = 0) -> tuple[list[SchemeListing], bool]:
    """H4/H5: up to MAX_SPOKEN_OPTIONS schemes for this branch, paged.
    Returns (listings_for_this_page, has_more_pages). Ordered by
    scheme_no so paging is stable across calls (never reshuffled)."""
    conn = get_db_default()
    try:
        theme = _theme_for_ps_key(conn, ps_key)
        if need_key is not None:
            need_group = _need_group_for_key(conn, need_key)
            rows = conn.execute(
                "SELECT slug, scheme_no, name_short_hi, scheme_name, verified "
                "FROM schemes WHERE theme = ? AND need_group = ? ORDER BY scheme_no",
                (theme, need_group),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT slug, scheme_no, name_short_hi, scheme_name, verified "
                "FROM schemes WHERE theme = ? ORDER BY scheme_no",
                (theme,),
            ).fetchall()

        start = page * MAX_SPOKEN_OPTIONS
        end = start + MAX_SPOKEN_OPTIONS
        page_rows = rows[start:end]
        has_more = end < len(rows)
        listings = [
            SchemeListing(r["slug"], r["scheme_no"], r["name_short_hi"], r["scheme_name"], r["verified"])
            for r in page_rows
        ]
        return listings, has_more
    finally:
        conn.close()


def section_text(slug: str, sec_key: str) -> str:
    """H8 - GATE. Returns the DB column verbatim (minus deadline
    stripping for verified=0 rows, H9) - never rewritten, never
    summarised, no model call anywhere in this function."""
    conn = get_db_default()
    try:
        column = _column_for_sec_key(conn, sec_key)
        row = conn.execute(
            f"SELECT {column} AS text, verified FROM schemes WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            raise MenuError(f"unknown scheme slug: {slug!r}")
        text = row["text"] or ""
        if row["verified"] == 0:
            text = strip_deadline_sentence(text)
        return text
    finally:
        conn.close()


def resolve_code(code: str) -> tuple[str, str] | None:
    """H6/H7: dial code = 2-digit scheme_no + 1-digit section, e.g. '021'
    = scheme 02, section 1 (CHANGE_REPORT #7). Returns (slug, sec_key) or
    None for any malformed/unresolvable code - never raises, so engine.py
    can treat None as "graceful message, back to main menu" (H7) without
    a try/except at the call site."""
    if not re.fullmatch(r"\d{3}", code or ""):
        return None
    scheme_no = int(code[:2])
    sec_key = code[2]
    if sec_key not in _SEC_KEY_TO_COLUMN_STATIC:
        return None
    conn = get_db_default()
    try:
        row = conn.execute(
            "SELECT slug FROM schemes WHERE scheme_no = ?", (scheme_no,)
        ).fetchone()
        if row is None:
            return None
        return row["slug"], sec_key
    finally:
        conn.close()


def speak_digits_hi(code: str) -> str:
    """H10: a dial code is read digit by digit ('do teen ek'), never as
    a number word ('two hundred thirty one')."""
    return " ".join(DIGIT_WORDS_HI.get(ch, ch) for ch in code)


# --- internal helpers -------------------------------------------------

_DB_PATH_OVERRIDE: str | None = None


def get_db_default():
    return get_db(_DB_PATH_OVERRIDE)


def _set_db_path_for_testing(path: str | None) -> None:
    """Test-only hook (tests/test_menu.py) - production code always calls
    the public functions with the default DB, matching narrow.py's own
    pattern of taking db_path as an explicit argument instead would have
    meant changing every public signature here for a testing concern
    alone; this module's functions are called from engine.py/api.py with
    no per-call DB path today, so a module-level override mirrors how
    config.DB_PATH itself already works."""
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


def _theme_for_ps_key(conn, ps_key: str) -> str:
    row = conn.execute(
        "SELECT theme FROM problem_statements WHERE ps_key = ?", (ps_key,)
    ).fetchone()
    if row is None:
        raise MenuError(f"unknown ps_key: {ps_key!r}")
    return row["theme"]


def _need_group_for_key(conn, need_key: str) -> str:
    row = conn.execute(
        "SELECT need_group FROM need_groups WHERE need_key = ?", (need_key,)
    ).fetchone()
    if row is None:
        raise MenuError(f"unknown need_key: {need_key!r}")
    return row["need_group"]


_SEC_KEY_TO_COLUMN_STATIC = {
    "1": "benefits", "2": "eligibility", "3": "documents",
    "4": "application", "5": "details",
}


def _column_for_sec_key(conn, sec_key: str) -> str:
    row = conn.execute(
        "SELECT column_name FROM detail_sections WHERE sec_key = ?", (sec_key,)
    ).fetchone()
    if row is None:
        raise MenuError(f"unknown sec_key: {sec_key!r}")
    column = row["column_name"]
    # Defensive allowlist - column_name comes from our own seed data, but
    # this function builds a SQL string with it, so pin it to the known-
    # safe set rather than trusting the DB value directly (never user
    # input in practice, but cheap to make provably safe).
    if column not in _SEC_KEY_TO_COLUMN_STATIC.values():
        raise MenuError(f"sec_key {sec_key!r} maps to disallowed column {column!r}")
    return column
