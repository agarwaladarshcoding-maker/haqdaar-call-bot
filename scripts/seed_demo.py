#!/usr/bin/env python3
"""Haqdaar demo seed - 20 synthetic schemes with realistic scheme_rules.

Real ingest (ingest.py) loads 100 real schemes but scheme_rules stays empty
until Step 9's human eligibility-prose pass. This script lets Steps 3-8 build
and test narrowing against real hard/soft rules before that content lands.

Idempotent: rebuilds from scratch each run, like ingest.py.

Usage:  python3 scripts/seed_demo.py [out.db]
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ingest  # reuse SCHEMA + loader logic

DB = sys.argv[1] if len(sys.argv) > 1 else "haqdaar.db"

THEMES = ["business", "craft", "fisheries", "training", "farming", "welfare"]
OCCUPATIONS = ["business", "weaver", "fisher", "farmer", "unemployed", "artisan"]
AGE_BANDS = [("lt18", 1), ("18_40", 2), ("41_59", 3), ("gte60", 4)]
INCOME_BANDS = [("lt1l", 1), ("1l_2_5l", 2), ("2_5l_5l", 3), ("gt5l", 4)]

random.seed(42)


def synth_schemes():
    out = []
    for i in range(1, 21):
        theme = THEMES[(i - 1) % len(THEMES)]
        occ = OCCUPATIONS[(i - 1) % len(OCCUPATIONS)]
        out.append(
            {
                "slug": f"demo-{i:03d}",
                "scheme_name": f"Demo Scheme {i} for {occ.title()} Support",
                "details": f"A synthetic demo scheme supporting {occ} applicants. "
                f"Extended to the region of West Bengal." if i % 5 == 0 else
                f"A synthetic demo scheme supporting {occ} applicants.",
                "benefits": f"Rs {10000 * i} one-time assistance for eligible {occ} applicants.",
                "eligibility": f"Applicant should be a {occ} aged 18 to 60 with household "
                f"income under Rs 5 lakh.",
                "application": "Apply at the nearest block office with the listed documents.",
                "documents": "Aadhaar card, income certificate, bank passbook.",
                "level": "State" if i % 5 == 0 else "Central",
                "schemeCategory": [theme.title()],
                "tags": [occ, theme, "demo"],
            }
        )
    return out


def seed_rules(db):
    """3-6 rules per scheme, mixing hard and soft, matching the worked
    example pattern in scheme_catalogue.sql section 3."""
    rows = db.execute("select slug, theme from schemes order by scheme_no").fetchall()
    for slug, theme in rows:
        occ = OCCUPATIONS[(int(slug.split("-")[1]) - 1) % len(OCCUPATIONS)]
        age_lo, age_hi = "18_40", "41_59"
        rules = [
            (slug, "occupation", "in", json.dumps([occ]), 1, 1.0,
             f"Applicant should be a {occ}."),
            (slug, "age_band", "gte", age_lo, 1, 1.0, "Aged 18 to 60."),
            (slug, "age_band", "lte", age_hi, 1, 1.0, "Aged 18 to 60."),
            (slug, "income_band", "lte", "2_5l_5l", 0, 0.8,
             "Household income under Rs 5 lakh."),
        ]
        if theme == "welfare":
            rules.append((slug, "existing_pension", "eq", "no", 0, 0.6,
                          "Not already receiving a pension."))
        db.executemany(
            "INSERT INTO scheme_rules (scheme_id, attribute, op, value, hard, weight, source_quote) "
            "VALUES (?,?,?,?,?,?,?)",
            rules,
        )


def main():
    src = synth_schemes()
    if os.path.exists(DB):
        os.remove(DB)

    import sqlite3

    db = sqlite3.connect(DB)
    db.executescript(ingest.SCHEMA)
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
              VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,NULL,?,1)""",
            (
                s["slug"], i, s["scheme_name"], s.get("details"), s.get("benefits"),
                s.get("eligibility"), s.get("application"), s.get("documents"),
                s.get("level"), st, th, ng, at, sn, 1 if flag else 0,
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
    print(f"seed_demo: {count} schemes, {rules} rules -> {DB}")


if __name__ == "__main__":
    main()
