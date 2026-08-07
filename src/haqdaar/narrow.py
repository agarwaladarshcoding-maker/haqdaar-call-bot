"""Narrowing engine: narrow(answers) -> ranked surviving schemes. Step 3.

THE core rule of the whole product (PRD principle 3, SYSTEM_DESIGN.md #7):
three-valued matching over scheme_rules.

  satisfied -> keep
  violated  -> eliminate ONLY if hard=1, else -weight (soft penalty)
  unknown   -> keep, always. An unanswered attribute never eliminates a scheme.

Implemented as `NOT EXISTS (violated hard rule)`, joined only against
ANSWERED attributes - never `EXISTS (satisfied rule)`. These are not
equivalent: EXISTS(satisfied) would drop every scheme whose rules simply
haven't been asked about yet, which is exactly the bug TEST_CASES.md B1
exists to catch (see attribute_schema.sql section 5 for the original query
this ports).

state_scope is a NULL-safe tiebreaker only (never a scheme_rules row) per
CHANGE_REPORT #3: it demotes cleanly - NULL state_scope is always kept.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from haqdaar.db import get_db


@dataclass(frozen=True)
class Candidate:
    slug: str
    scheme_no: int
    scheme_name: str
    name_short_hi: str | None
    benefit_one_line: str | None
    theme: str
    verified: int
    score: float


def _ord_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    return {
        (r["attribute"], r["value"]): r["ord"]
        for r in conn.execute("SELECT attribute, value, ord FROM attr_values")
        if r["ord"] is not None
    }


def _violates(op: str, rule_value: str, answer: str, ords: dict[tuple[str, str], int], attribute: str) -> bool:
    """True if `answer` contradicts a rule of this op/value on this attribute.
    Only called for ANSWERED attributes - unanswered never reaches here."""
    if op == "eq":
        return answer != rule_value
    if op == "in":
        return answer not in json.loads(rule_value)
    if op == "not_in":
        return answer in json.loads(rule_value)
    if op in ("gte", "lte"):
        a_ord = ords.get((attribute, answer))
        r_ord = ords.get((attribute, rule_value))
        if a_ord is None or r_ord is None:
            # Can't compare bands we have no ord for - never eliminate on an
            # unresolvable comparison (same safe-failure direction as unknown).
            return False
        return a_ord < r_ord if op == "gte" else a_ord > r_ord
    if op == "any":
        return False
    raise ValueError(f"unsupported op: {op}")


def narrow(answers: dict[str, Any], db_path: str | None = None) -> list[Candidate]:
    """Returns surviving schemes ranked by verified DESC, soft-rule score
    DESC, name. Empty answers returns ALL schemes (B1, the most important
    test in the project)."""
    conn = get_db(db_path)
    try:
        ords = _ord_lookup(conn)
        answered = {k: v for k, v in answers.items() if not k.startswith("_")}

        schemes = conn.execute("SELECT * FROM schemes").fetchall()
        rules_by_scheme: dict[str, list[sqlite3.Row]] = {}
        for r in conn.execute("SELECT * FROM scheme_rules"):
            rules_by_scheme.setdefault(r["scheme_id"], []).append(r)

        out: list[Candidate] = []
        for s in schemes:
            slug = s["slug"]
            rules = rules_by_scheme.get(slug, [])

            eliminated = False
            score = 0.0
            for r in rules:
                attr = r["attribute"]
                if attr not in answered:
                    continue  # unknown -> keep, always. Never touches score or elimination.
                if _violates(r["op"], r["value"], answered[attr], ords, attr):
                    if r["hard"]:
                        eliminated = True
                        break
                    score -= r["weight"]
                else:
                    score += r["weight"] if r["hard"] == 0 else 0.0

            if eliminated:
                continue

            # State: NULL-safe tiebreaker, never a hard scheme_rules row.
            state_scope = s["state_scope"]
            answer_state = answered.get("state")
            if state_scope is not None and answer_state is not None and state_scope != answer_state:
                continue

            out.append(
                Candidate(
                    slug=slug,
                    scheme_no=s["scheme_no"],
                    scheme_name=s["scheme_name"],
                    name_short_hi=s["name_short_hi"],
                    benefit_one_line=s["benefit_one_line"],
                    theme=s["theme"],
                    verified=s["verified"],
                    score=score,
                )
            )

        out.sort(key=lambda c: (-c.verified, -c.score, c.scheme_name))
        return out
    finally:
        conn.close()
