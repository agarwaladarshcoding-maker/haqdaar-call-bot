#!/usr/bin/env python3
"""Haqdaar demo seed - 20 synthetic schemes with realistic scheme_rules.

Real ingest (ingest.py) loads 100 real schemes but scheme_rules stays empty
until Step 9's human eligibility-prose pass. This script lets Steps 3-8 build
and test narrowing against real hard/soft rules before that content lands.

Rules use question_bank.yaml v3's actual attribute vocabulary (persona,
applicant_type, theme, age_band, income_band, state, social_category,
existing_pension) - NOT the v1/v2 farmer-era `occupation` attribute, which no
question in v3 ever writes. Testing narrow.py against a dead attribute would
silently test nothing.

Idempotent: rebuilds from scratch each run, like ingest.py.

Usage:  python3 scripts/seed_demo.py [out.db]
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ingest  # reuse SCHEMA + loader logic

DB = sys.argv[1] if len(sys.argv) > 1 else "haqdaar.db"

THEMES = ["business", "craft", "fisheries", "training", "farming", "welfare"]
PERSONAS = {
    "business": "business",
    "craft": "artisan",
    "fisheries": "fisher",
    "training": "other",
    "farming": "farmer",
    "welfare": "other",
}
# One real theme-classifier keyword per theme (from ingest.py THEMES), so
# classify_theme() lands the synthetic scheme on the intended theme instead
# of falling back to its 'business' default. Without this, e.g. "welfare"
# schemes with no welfare keyword silently reclassify as "business".
THEME_KEYWORD = {
    "business": "enterprise",
    "craft": "handicraft",
    "fisheries": "fisher",
    "training": "training",
    "farming": "farmer",
    "welfare": "pension",
}
APPLICANT_TYPES = ["business", "person", "both"]
STATES = [None, None, "PY", "WB", "MP"]  # mostly NULL, matching the real 62% PY skew


def synth_schemes():
    out = []
    for i in range(1, 21):
        theme = THEMES[(i - 1) % len(THEMES)]
        persona = PERSONAS[theme]
        keyword = THEME_KEYWORD[theme]
        state = STATES[(i - 1) % len(STATES)]
        state_sentence = (
            f" The scheme is extended to the region of {'Puducherry' if state == 'PY' else 'West Bengal' if state == 'WB' else 'Madhya Pradesh'}."
            if state
            else ""
        )
        # Every 5th scheme is unverified and carries an unconfirmed deadline
        # sentence in `application`, matching PRD M12 ("deadlines spoken
        # only from verified=1 rows") - menu.py's section_text() must strip
        # exactly this sentence, and only for these schemes, per H9. The
        # real 100-scheme catalogue has no deadline text yet (Step 9 content
        # pass hasn't run), so this is the only place that behaviour is
        # exercisable until then.
        is_unverified = i % 5 == 0
        deadline_sentence = (
            " Applications must be submitted before the last date of 31 March."
            if is_unverified
            else ""
        )
        out.append(
            {
                "slug": f"demo-{i:03d}",
                "scheme_name": f"Demo Scheme {i} for {persona.title()} Support",
                "details": f"A synthetic demo {keyword} scheme supporting {persona} applicants.{state_sentence}",
                "benefits": f"Rs {10000 * i} one-time assistance for eligible {persona} applicants.",
                "eligibility": f"Applicant should be a {persona} aged 18 to 60 with household "
                f"income under Rs 5 lakh.",
                "application": f"Apply at the nearest block office with the listed documents.{deadline_sentence}",
                "documents": "Aadhaar card, income certificate, bank passbook.",
                "level": "State" if state else "Central",
                "schemeCategory": [theme.title()],
                "tags": [persona, theme, "demo"],
                "verified": 0 if is_unverified else 1,
            }
        )
    return out


def seed_rules(db):
    """3-6 rules per scheme, mixing hard and soft, covering every op
    (eq/in/not_in/gte/lte) and every scenario the B-series tests need:
    a state-scoped scheme (hard eq), a state-NULL scheme (never eliminated
    by state), soft income preference, hard age band via gte+lte."""
    rows = db.execute(
        "select slug, scheme_no, theme, state_scope from schemes order by scheme_no"
    ).fetchall()
    for slug, scheme_no, theme, state_scope in rows:
        persona = PERSONAS[theme]
        rules = [
            (slug, "persona", "in", json.dumps([persona]), 1, 1.0,
             f"Applicant should be a {persona}."),
            (slug, "age_band", "gte", "18_40", 1, 1.0, "Aged 18 to 60."),
            (slug, "age_band", "lte", "41_59", 1, 1.0, "Aged 18 to 60."),
            (slug, "income_band", "lte", "2_5l_5l", 0, 0.8,
             "Household income under Rs 5 lakh (soft preference)."),
        ]
        # Every 4th scheme carries an explicit applicant_type hard rule so
        # narrow.py's `eq` op is exercised, not just `in`/`gte`/`lte`.
        if scheme_no % 4 == 0:
            at = APPLICANT_TYPES[scheme_no % 3]
            rules.append(
                (slug, "applicant_type", "eq", at, 1, 1.0,
                 f"Applicant type must be {at}.")
            )
        # state_scope is derived by ingest.extract_state from the details
        # prose above; no separate scheme_rules row needed - narrow.py reads
        # schemes.state_scope directly, matching the real catalogue's design.
        if theme == "welfare":
            rules.append((slug, "existing_pension", "eq", "no", 0, 0.6,
                          "Not already receiving a pension (soft)."))
        # A not_in rule on social_category for a couple of schemes, so that
        # op is exercised too.
        if scheme_no in (5, 15):
            rules.append(
                (slug, "social_category", "not_in", json.dumps(["GEN"]), 0, 0.5,
                 "Preference for reserved-category applicants (soft).")
            )
        db.executemany(
            "INSERT INTO scheme_rules (scheme_id, attribute, op, value, hard, weight, source_quote) "
            "VALUES (?,?,?,?,?,?,?)",
            rules,
        )


def main():
    src = synth_schemes()
    if os.path.exists(DB):
        os.remove(DB)

    db = sqlite3.connect(DB)
    db.executescript(ingest.SCHEMA)
    if os.path.exists(ingest.ATTR_SEED_PATH):
        db.executescript(open(ingest.ATTR_SEED_PATH, encoding="utf-8").read())

    db.executemany(
        "INSERT INTO detail_sections VALUES (?,?,?,?,?)",
        [(k, c, hi, en, i + 1) for i, (k, c, hi, en) in enumerate(ingest.SECTIONS)],
    )
    db.executemany(
        "INSERT INTO problem_statements VALUES (?,?,?,?,?)",
        [(k, t, hi, en, i + 1) for i, (k, t, hi, en, _) in enumerate(ingest.THEMES)],
    )
    db.executemany(
        "INSERT INTO need_groups VALUES (?,?,?,?,?)",
        [(k, t, hi, en, i + 1) for i, (k, t, hi, en, _) in enumerate(ingest.NEEDS)],
    )

    for i, s in enumerate(sorted(src, key=lambda x: x["slug"]), start=1):
        st = ingest.extract_state(s)
        th = ingest.classify_theme(s)
        ng = ingest.classify_need(s) if th == "business" else None
        at = ingest.applicant_type(s)
        sn, flag = ingest.short_name(s)
        db.execute(
            """INSERT INTO schemes
              (slug,scheme_no,scheme_name,details,benefits,eligibility,application,
               documents,level,state_scope,district_scope,theme,need_group,
               applicant_type,name_short_hi,benefit_one_line,needs_human_name,verified)
              VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,NULL,?,?)""",
            (
                s["slug"], i, s["scheme_name"], s.get("details"), s.get("benefits"),
                s.get("eligibility"), s.get("application"), s.get("documents"),
                s.get("level"), st, th, ng, at, sn, 1 if flag else 0,
                s.get("verified", 1),
            ),
        )
        for c in s.get("schemeCategory") or []:
            db.execute("INSERT OR IGNORE INTO scheme_categories VALUES (?,?)", (s["slug"], c))
        for t in s.get("tags") or []:
            db.execute("INSERT OR IGNORE INTO scheme_tags VALUES (?,?)", (s["slug"], t))

    seed_rules(db)
    db.commit()

    count = db.execute("select count(*) from schemes").fetchone()[0]
    rules = db.execute("select count(*) from scheme_rules").fetchone()[0]
    null_state = db.execute("select count(*) from schemes where state_scope is null").fetchone()[0]
    print(f"seed_demo: {count} schemes, {rules} rules, {null_state} state-NULL -> {DB}")


if __name__ == "__main__":
    main()
