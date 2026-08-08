"""One-time offline pass: Hindi benefit lines for every scheme.

The catalogue's `benefits` column is entirely English ("Stipend of
₹7,000/-. Skill development training for construction workers."), and
`benefit_one_line` is empty for all 100 rows - so present.py has always
fallen back to reading an English sentence in a Hindi voice. This fills a
new `benefit_one_line_hi` column with Sarvam mayura:v1 translations, in
the same Roman-script Hindi-with-English-terms register the question bank
already uses (mode=code-mixed, output_script=roman).

OFFLINE ON PURPOSE. Translating during a call would add a network round
trip to the results turn - the one turn that already came closest to
Twilio's 15s webhook timeout - and would make the same scheme sound
different on two different calls. This runs once and is committed as data.

--------------------------------------------------------------------------
ACCURACY, AND WHY THIS SCRIPT WRITES A REVIEW FILE
--------------------------------------------------------------------------
present.py has a hard rule (K2/K3): any benefit spoken must be a verbatim
substring of the DB text, so the system can never invent an amount. A
translation breaks that rule by construction - translated text is by
definition not the source text. Two guardrails replace it, and it is
important to be honest that they are weaker:

1. NUMBER SURVIVAL (automatic, enforced here). Every number and ₹ amount
   in the source must appear in the translation. A dropped or altered
   figure is the highest-consequence error, and this catches it. Rows that
   fail keep the English text rather than speaking a wrong number.

2. HUMAN REVIEW (not automatic - that is the point). Number survival does
   NOT catch meaning inversion. A real example from the first run:

     EN: "...cost of the Enterprise INCLUSIVE OF land, machinery, and
          construction limited to the maximum amount of ₹6.25 lakhs"
     HI: "...land, machinery, aur construction ko CHHODKAR ... jo maximum
          ₹6.25 lakhs tak hoga"

   "inclusive of" became "chhodkar" (excluding) - the opposite of what the
   scheme covers - and every number survived, so check 1 passed it. For a
   government benefits line that is a serious error.

   So this script also writes benefit_translations_review.tsv with the
   English and Hindi side by side, longest and most clause-heavy sentences
   first (those translate worst). Skim it before relying on the audio.
   Rows can be corrected in the DB directly; this script never overwrites
   a row that already has a hand-edited translation unless --force.

Usage:
    python -m scripts.translate_benefits            # translate missing rows
    python -m scripts.translate_benefits --dry-run  # show, write nothing
    python -m scripts.translate_benefits --force    # redo every row
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time

import httpx

from haqdaar import config

TRANSLATE_URL = "https://api.sarvam.ai/translate"
MODEL = "mayura:v1"
MAX_INPUT_CHARS = 1000  # mayura:v1's documented limit
REVIEW_PATH = "benefit_translations_review.tsv"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Digits with optional separators/decimals: catches 7,000 / 6.25 / 25 / 40.
_NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")


def first_sentence(text: str) -> str:
    """Mirrors present.py's _first_sentence - the same span that would
    otherwise be spoken in English, so the two stay in step."""
    if not text:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return (parts[0] if parts else text.strip())[:MAX_INPUT_CHARS]


def _numbers(text: str) -> list[str]:
    """Normalized numbers, so "7,000" and "7000" compare equal - the
    translator legitimately reformats separators, and that is not an error."""
    return sorted(n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text))


def numbers_survived(source: str, translated: str) -> bool:
    """Every figure in the source must still be present. Extra numbers in
    the translation are allowed (dates written out, etc); MISSING or
    CHANGED ones are not, because that is a wrong benefit amount."""
    src, out = _numbers(source), _numbers(translated)
    return all(n in out for n in src)


def risk_score(source: str) -> int:
    """Rough "how likely is this to be mistranslated" ordering for the
    review file. Long sentences with many clauses are where meaning
    inversions like inclusive/excluding actually happened."""
    return len(source) + 40 * source.count(",") + 60 * len(
        re.findall(r"\b(inclusive|exclusive|excluding|including|other than|except|not)\b", source, re.I)
    )


def translate(text: str) -> str | None:
    """Roman-script Hindi with English terms left in English - the same
    register as the question bank's own prompts, and what Sarvam's TTS
    already voices correctly. Returns None on any failure; the caller then
    keeps the English text rather than speaking nothing."""
    try:
        resp = httpx.post(
            TRANSLATE_URL,
            headers={"api-subscription-key": config.SARVAM_API_KEY},
            json={
                "input": text,
                "source_language_code": "en-IN",
                "target_language_code": "hi-IN",
                "model": MODEL,
                "mode": "code-mixed",
                "output_script": "roman",
                "numerals_format": "international",  # keep ₹6.25 as digits
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        return (resp.json().get("translated_text") or "").strip() or None
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def ensure_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schemes)")}
    if "benefit_one_line_hi" not in cols:
        conn.execute("ALTER TABLE schemes ADD COLUMN benefit_one_line_hi TEXT")
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--force", action="store_true", help="retranslate rows that already have text")
    ap.add_argument("--db", default=None, help="database path (default: config.DB_PATH)")
    args = ap.parse_args()

    if not config.SARVAM_API_KEY:
        print("SARVAM_API_KEY not set - nothing to do. The system still works: "
              "present.py keeps reading the English benefit sentence.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_column(conn)

    rows = conn.execute("SELECT slug, scheme_name, benefits, benefit_one_line_hi FROM schemes").fetchall()
    todo = [r for r in rows if r["benefits"] and (args.force or not r["benefit_one_line_hi"])]
    print(f"{len(rows)} schemes, {len(todo)} to translate")

    review: list[tuple[int, str, str, str, str]] = []
    ok = skipped = failed = 0

    for i, row in enumerate(todo, 1):
        source = first_sentence(row["benefits"])
        if not source:
            continue
        translated = translate(source)
        if translated is None:
            failed += 1
            status = "TRANSLATE-FAILED"
        elif not numbers_survived(source, translated):
            # A changed or dropped figure is the one error we refuse to
            # ship - keep English rather than speak a wrong amount.
            skipped += 1
            status = "NUMBERS-LOST"
            translated = ""
        else:
            ok += 1
            status = "ok"
            if not args.dry_run:
                conn.execute(
                    "UPDATE schemes SET benefit_one_line_hi = ? WHERE slug = ?", (translated, row["slug"])
                )
        review.append((risk_score(source), status, row["scheme_name"], source, translated or ""))
        print(f"  [{i}/{len(todo)}] {status:<16} {row['scheme_name'][:50]}")
        time.sleep(0.15)  # be gentle with the free tier

    if not args.dry_run:
        conn.commit()
        review.sort(reverse=True)  # riskiest first - that is what needs eyes
        with open(REVIEW_PATH, "w", encoding="utf-8") as f:
            f.write("risk\tstatus\tscheme\tenglish\thindi\n")
            for score, status, name, src, out in review:
                f.write(f"{score}\t{status}\t{name}\t{src}\t{out}\n".replace("\n\t", "\t"))
        print(f"\nreview file: {REVIEW_PATH} (riskiest first)")

    print(f"\ntranslated {ok}, kept English {skipped} (numbers lost), failed {failed}")
    print("Number survival is checked automatically. MEANING IS NOT - skim the")
    print("review file before the demo; see this script's docstring for a real")
    print("inclusive/excluding inversion it would not have caught.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
