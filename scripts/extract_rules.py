#!/usr/bin/env python3
"""Offline, one-time batch pass: LLM-assisted scheme_rules extraction from
real eligibility prose. This is Step 9's eligibility-rules half, done with
LLM help instead of fully by hand - BUILD_STEPS.md's original plan was a
human converting "eligibility prose to scheme_rules... hard=1 only where
the text states an absolute bar... when in doubt, soft" per scheme; this
script does the same conversion, then a human still marks verified=1 only
after checking a sample against the source (unchanged from the original
plan - this script never sets verified itself).

NOT run per-call. Eligibility shouldn't be re-derived differently every
time the same scheme is discussed - it's data, written once, checked once,
then read many times by narrow.py, exactly like scheme_rules already
worked for the 20-scheme demo catalogue (scripts/seed_demo.py's rules were
hand-written; this script's rules are LLM-drafted from real prose instead,
same destination table, same shape, same downstream consumer).

Grounding discipline: the LLM is given ONLY the known attribute vocabulary
(attribute_seed.sql + question_bank.yaml's real DTMF value sets) and the
scheme's own eligibility/details text. Every extracted rule must carry a
source_quote that is a VERBATIM SUBSTRING of that scheme's own text - a
rule whose source_quote doesn't appear in the source is rejected outright,
never inserted. This is the same "extraction, not invention" discipline as
present.py (Step 9's live wording half), applied to structured rules
instead of spoken sentences.

Usage: python3 scripts/extract_rules.py [db_path] [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from haqdaar import llm  # noqa: E402

VALID_OPS = {"eq", "in", "not_in", "gte", "lte", "any"}
MAX_RULES_PER_SCHEME = 6  # mechanical backstop, not just a prompt instruction

# Attribute -> allowed enum/band values, extracted from question_bank.yaml's
# real DTMF `set` values (the ONLY values narrow.py/engine.py ever write
# into `answers`, so a rule referencing anything else can never match any
# real answer - useless at best, silently-dead at worst).
ENUM_VALUES = {
    "aadhaar_linked": ["no", "unknown", "yes"],
    "age_band": ["lt18", "18_40", "41_59", "gte60"],
    "applicant_type": ["both", "business", "person"],
    "applies_as": ["individual", "society"],
    "boat_owner": ["no", "yes"],
    "coop_member": ["no", "yes"],
    "craft_type": ["coir", "handloom", "other"],
    "crop_grown": ["coconut", "fodder", "horticulture", "other", "paddy", "sugarcane"],
    "currently_employed": ["no", "yes"],
    "disability": ["no", "yes"],
    "education_need": ["no", "yes"],
    "employment_created": ["none", "1_5", "gt5"],
    "enterprise_type": ["both", "manufacturing", "service"],
    "existing_pension": ["no", "yes"],
    "gender": ["F", "M", "O"],
    "has_aadhaar": ["no", "yes"],
    "has_bank_account": ["no", "yes"],
    "has_income_cert": ["no", "yes"],
    "household_size": ["1_2", "3_5", "gt5"],
    "income_band": ["lt1l", "1l_2_5l", "2_5l_5l", "gt5l"],
    "investment_size": ["lt25l", "25l_5cr", "gt5cr", "unknown"],
    "is_literate": ["no", "yes"],
    "is_registered_firm": ["no", "yes"],
    "is_student": ["no", "yes"],
    "is_widow": ["no", "yes"],
    "loan_taken": ["no", "yes"],
    "owns_land": ["no", "yes"],
    "persona": ["artisan", "business", "farmer", "fisher", "other"],
    "social_category": ["GEN", "MIN", "OBC", "SC", "ST"],
    "theme": ["business", "craft", "farming", "fisheries", "training", "welfare"],
    "training_related": ["no", "yes"],
    "udyam_registered": ["no", "unknown", "yes"],
    "unit_stage": ["existing", "new"],
    "worker_registered": ["no", "unknown", "yes"],
    "years_in_trade_band": ["lt3y", "gte3y"],
}

# Attributes state_scope already covers structurally (narrow.py handles it
# as a separate NULL-safe tiebreaker, never a scheme_rules row) or that are
# session/scratch (leading underscore, never eligibility-relevant) are
# never legal extraction targets.
BANNED_ATTRS = {"language", "intent", "on_behalf"}

ALL_ATTRS = sorted(set(ENUM_VALUES) - BANNED_ATTRS)

SYSTEM_PROMPT = f"""You extract structured eligibility rules from Indian government scheme eligibility text. You are grounding, not inventing: every rule must be directly supported by the given text.

Allowed attributes and their ONLY legal values:
{json.dumps(ENUM_VALUES, indent=2)}

Allowed ops: eq (equals), in (one of a list), not_in (none of a list), gte (at or above, band ordinal), lte (at or below, band ordinal).

CRITICAL - banded (ordinal) attributes like age_band/income_band/investment_size/employment_created/household_size/years_in_trade_band have MULTIPLE named bands in a fixed order. gte/lte compare against ONE band boundary, they do not mean "pick whichever band the text mentions":
  - Bands for investment_size, in order: lt25l (band 1) < 25l_5cr (band 2) < gt5cr (band 3).
  - Text "investment BELOW Rs 25 lakh" means only band 1 qualifies -> {{"attribute": "investment_size", "op": "lte", "value": "lt25l"}} (NOT "25l_5cr" - that would wrongly also admit band 2, which is 25 lakh to 5 crore, the opposite of "below 25 lakh").
  - Text "investment ABOVE Rs 5 crore" means only band 3 qualifies -> {{"op": "gte", "value": "gt5cr"}}.
  - Same logic for every other banded attribute: pick the SMALLEST/LARGEST band whose own range matches what the text actually restricts to, never a band that would also silently admit values the text excludes.
  - Bands for age_band, in order: lt18 (1) < 18_40 (2) < 41_59 (3) < gte60 (4). "above 18" -> gte 18_40. "must be 60 or older" -> gte gte60.
  - Bands for income_band, in order: lt1l (1) < 1l_2_5l (2) < 2_5l_5l (3) < gt5l (4).
  - Bands for employment_created, in order: none (1) < 1_5 (2) < gt5 (3).
  - Bands for household_size, in order: 1_2 (1) < 3_5 (2) < gt5 (3).
  - Bands for years_in_trade_band, in order: lt3y (1) < gte3y (2).

Rules:
1. Only use attributes and values from the list above. Never invent a new attribute or value.
2. hard=true ONLY if the text states an absolute, unconditional bar ("must be", "only", "shall not exceed", "restricted to"). If the text is a preference, a typical case, or ambiguous, hard=false.
3. Every rule needs a "source_quote": the EXACT substring from the given text that justifies this rule. Copy it character-for-character. Do not paraphrase the quote.
4. If nothing in the text maps to any allowed attribute, return an empty rules list. Do not force a match.
5. Prefer fewer, confident rules over many speculative ones. Most schemes should get 0-4 rules. NEVER exceed 6 rules for one scheme - if you find more than 6 candidates, keep only the 6 most clearly stated ones and drop the rest. Do not extract a rule for procedural/administrative details (deadlines for claims, which office to submit to, quarterly filing windows) - only extract WHO IS ELIGIBLE, not HOW/WHEN TO APPLY.

Return JSON: {{"rules": [{{"attribute": "...", "op": "...", "value": "... or [\\"...\\",\\"...\\"] for in/not_in", "hard": true|false, "source_quote": "..."}}]}}"""


def _build_user_prompt(scheme_name: str, eligibility: str, details: str) -> str:
    return (
        f"Scheme: {scheme_name}\n\n"
        f"Eligibility text:\n{eligibility}\n\n"
        f"Details (for context only, quotes should come from eligibility text when possible):\n{details[:800]}"
    )


def _validate_rule(rule: dict, source_text: str) -> tuple[bool, str]:
    attr = rule.get("attribute")
    op = rule.get("op")
    value = rule.get("value")
    hard = rule.get("hard")
    quote = rule.get("source_quote")

    if attr not in ALL_ATTRS:
        return False, f"unknown attribute {attr!r}"
    if op not in VALID_OPS:
        return False, f"unknown op {op!r}"
    if not isinstance(hard, bool):
        return False, f"hard must be boolean, got {hard!r}"
    if not isinstance(quote, str) or not quote.strip():
        return False, "missing source_quote"
    if quote not in source_text:
        return False, f"source_quote not found verbatim in source text: {quote!r}"

    allowed_values = set(ENUM_VALUES.get(attr, []))
    if op in ("eq", "gte", "lte"):
        if value not in allowed_values:
            return False, f"value {value!r} not in allowed set for {attr}"
    elif op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            return False, f"{op} requires a non-empty list, got {value!r}"
        bad = [v for v in value if v not in allowed_values]
        if bad:
            return False, f"values {bad} not in allowed set for {attr}"
    return True, ""


def _chat_with_retry(system: str, user: str, *, max_retries: int = 5, max_tokens: int = 700) -> str:
    """Rate limits (429) are expected on a free-tier key processing 100
    schemes back to back - retry with exponential backoff rather than
    treating a transient rate limit the same as a real API failure."""
    delay = 15.0
    last_error: llm.LLMError | None = None
    for attempt in range(max_retries):
        try:
            return llm.chat(system, user, timeout_s=25, max_tokens=max_tokens)
        except llm.LLMError as e:
            last_error = e
            if "429" not in str(e) and "503" not in str(e):
                raise
            print(f"    rate limited, retrying in {delay:.0f}s (attempt {attempt+1}/{max_retries})")
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise last_error  # type: ignore[misc]


def extract_for_scheme(scheme_name: str, eligibility: str, details: str) -> list[dict]:
    """Returns validated rules only - anything that fails _validate_rule is
    dropped with a warning, never inserted. Raises llm.LLMError upward on
    any network/API failure (caller decides skip-vs-abort)."""
    user_prompt = _build_user_prompt(scheme_name, eligibility, details)
    raw = _chat_with_retry(SYSTEM_PROMPT, user_prompt, max_tokens=700)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"    WARN: malformed JSON from LLM, skipping scheme")
        return []

    rules = parsed.get("rules")
    if not isinstance(rules, list):
        print(f"    WARN: no 'rules' list in LLM response, skipping scheme")
        return []

    source_text = eligibility + " " + details
    good = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        ok, reason = _validate_rule(r, source_text)
        if ok:
            good.append(r)
        else:
            print(f"    REJECTED rule: {reason}")

    if len(good) > MAX_RULES_PER_SCHEME:
        # Mechanical cap, not just a prompt instruction - a scheme with
        # many procedural rules is more likely noise than genuine
        # eligibility gates; keep the hard rules first (they carry more
        # weight in narrow.py), then fill remaining slots with soft ones.
        hard = [r for r in good if r.get("hard")]
        soft = [r for r in good if not r.get("hard")]
        dropped = len(good) - MAX_RULES_PER_SCHEME
        good = (hard + soft)[:MAX_RULES_PER_SCHEME]
        print(f"    capped at {MAX_RULES_PER_SCHEME} rules, dropped {dropped}")

    return good


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", nargs="?", default="haqdaar.db")
    parser.add_argument("--dry-run", action="store_true", help="extract and validate but do not write to the DB")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N schemes (for testing)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT slug, scheme_name, eligibility, details FROM schemes ORDER BY scheme_no"
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]

    total_rules = 0
    schemes_with_zero_rules = []

    for i, row in enumerate(rows):
        slug = row["slug"]
        print(f"[{i+1}/{len(rows)}] {slug}: {row['scheme_name'][:60]}", flush=True)
        try:
            rules = extract_for_scheme(row["scheme_name"], row["eligibility"] or "", row["details"] or "")
        except llm.LLMError as e:
            print(f"    LLM ERROR: {e} - skipping scheme, 0 rules")
            schemes_with_zero_rules.append(slug)
            continue

        if not rules:
            schemes_with_zero_rules.append(slug)

        if not args.dry_run:
            conn.execute("DELETE FROM scheme_rules WHERE scheme_id = ?", (slug,))
            for r in rules:
                value = r["value"] if isinstance(r["value"], str) else json.dumps(r["value"])
                conn.execute(
                    "INSERT INTO scheme_rules (scheme_id, attribute, op, value, hard, weight, source_quote) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (slug, r["attribute"], r["op"], value, int(r["hard"]), 1.0, r["source_quote"]),
                )
            conn.commit()

        total_rules += len(rules)
        print(f"    -> {len(rules)} rules", flush=True)
        # This prompt (~1200 system-prompt tokens + eligibility text + up
        # to 700 output tokens) is large relative to the smaller model's
        # free-tier 6000-tokens/minute budget - roughly 2-3 calls/minute
        # is the real ceiling, not a request-count limit. Pace to stay
        # under it rather than relying on retry/backoff to paper over
        # constant 429s.
        time.sleep(20)

    conn.close()

    print()
    print("=" * 60)
    print(f"schemes processed     : {len(rows)}")
    print(f"total rules extracted : {total_rules}")
    print(f"schemes with 0 rules  : {len(schemes_with_zero_rules)}")
    if schemes_with_zero_rules:
        print(f"  {schemes_with_zero_rules[:10]}{'...' if len(schemes_with_zero_rules) > 10 else ''}")
    print(f"mode                  : {'DRY RUN (nothing written)' if args.dry_run else 'WRITTEN, verified=0'}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
