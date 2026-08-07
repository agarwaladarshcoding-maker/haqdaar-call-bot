"""Loads and validates question_bank.yaml into a Bank object. Step 2.

Validation reuses the same rules as validate_bank.py (kept as the standalone
pre-commit check) but raises BankError instead of printing + exiting, since a
malformed bank must never reach a caller (BUILD_STEPS.md Step 2 / test L3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from haqdaar import config

RESERVED = {"0", "*", "#"}

# requires mini-language: exactly four forms.
#   always | <attr> == 'value' | <attr> in ['a','b'] | <attr> is answered
_RE_EQ = re.compile(r"^([a-z_][a-z0-9_]*)\s*==\s*'([^']*)'$")
_RE_IN = re.compile(r"^([a-z_][a-z0-9_]*)\s+in\s+\[(.*)\]$")
_RE_ANSWERED = re.compile(r"^([a-z_][a-z0-9_]*)\s+is\s+answered$")


class BankError(Exception):
    """Raised when question_bank.yaml fails validation. Never let a
    malformed bank reach a caller - fail at startup, not at question 4."""


@dataclass(frozen=True)
class Question:
    id: str
    raw: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.raw[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


def _tokens(expr: str) -> set[str]:
    """Attribute names referenced by a requires expression."""
    if not expr or expr == "always":
        return set()
    return set(
        re.findall(
            r"\b([a-z_][a-z0-9_]*)\b(?=\s*(?:==|!=|\bin\b|\bis answered\b))", expr
        )
    )


def _load_attributes(sql_path: str) -> set[str]:
    txt = open(sql_path, encoding="utf-8").read()
    return set(
        re.findall(r"^\('([a-z_]+)','(?:enum|bool|band|int|session)'", txt, re.M)
    )


def _validate(raw: dict, declared_attrs: set[str]) -> None:
    errors: list[str] = []
    qs = raw.get("questions") or []
    by_id = {q["id"]: q for q in qs if "id" in q}

    if len(qs) != raw.get("meta", {}).get("question_count"):
        errors.append(
            f"meta.question_count={raw.get('meta', {}).get('question_count')} "
            f"but {len(qs)} questions found"
        )
    if len(by_id) != len(qs):
        errors.append("duplicate question ids")

    written: dict[str, list[str]] = {}
    for q in qs:
        qid = q.get("id", "<missing id>")
        w = q.get("writes")
        if not w:
            errors.append(f"{qid}: no `writes`")
            continue
        if not w.startswith("_") and w not in declared_attrs:
            errors.append(f"{qid}: writes undeclared attribute `{w}`")
        written.setdefault(w, []).append(qid)

        for key in q.get("dtmf") or {}:
            if key in RESERVED:
                errors.append(f"{qid}: uses reserved key `{key}` as an answer option")
            if not key.isdigit() or not (1 <= int(key) <= 9):
                errors.append(f"{qid}: answer key `{key}` outside 1-9")

        n = len(q.get("dtmf") or {})
        if n > 9:
            errors.append(f"{qid}: {n} options, max 9")

        collect = q.get("collect", raw.get("defaults", {}).get("collect", {}))
        if not collect.get("speech", False) and not q.get("speech_disabled_reason"):
            errors.append(f"{qid}: speech is off but no speech_disabled_reason given")
        if q.get("speech_disabled_reason") and collect.get("speech", False):
            errors.append(f"{qid}: speech_disabled_reason set but speech is still on")

        if not q.get("why") or len(q["why"].split()) < 8:
            errors.append(f"{qid}: `why` missing or too short - the selector reads this")
        if not q.get("tags"):
            errors.append(f"{qid}: no tags")
        if not q.get("say_key"):
            errors.append(f"{qid}: no say_key - cannot pre-record audio")

        if not q.get("dtmf") and not q.get("option_source"):
            if q.get("collect", {}).get("digits") != 0:
                errors.append(f"{qid}: no dtmf options and not marked speech-only")

        if not (1 <= q.get("gain", 0) <= 10):
            errors.append(f"{qid}: gain must be 1-10")

        req = q.get("requires", "always")
        if req != "always" and not (
            _RE_EQ.match(req) or _RE_IN.match(req) or _RE_ANSWERED.match(req)
            or " and " in req or " or " in req
        ):
            errors.append(f"{qid}: requires `{req}` is not one of the four legal forms")

    # DAG: every referenced attribute is produced by some question, no cycles
    producer = {w: ids[0] for w, ids in written.items()}
    edges: dict[str, set[str]] = {}
    for q in qs:
        qid = q.get("id", "<missing id>")
        deps = _tokens(q.get("requires", "always"))
        for d in deps:
            if d not in producer:
                errors.append(f"{qid}: requires `{d}` which no question ever writes")
        edges[qid] = {producer[d] for d in deps if d in producer}

    WHITE, GREY, BLACK = 0, 1, 2
    color = {qid: WHITE for qid in by_id}

    def visit(n: str, stack: list[str]) -> None:
        if color.get(n) == GREY:
            errors.append(f"CYCLE: {' -> '.join(stack + [n])}")
            return
        if color.get(n) == BLACK:
            return
        color[n] = GREY
        for m in edges.get(n, ()):
            visit(m, stack + [n])
        color[n] = BLACK

    for qid in by_id:
        visit(qid, [])

    roots = [q["id"] for q in qs if q.get("requires", "always") == "always"]
    if not roots:
        errors.append("no root question (requires: always)")

    cap = raw.get("meta", {}).get("max_questions_per_call", 10)
    depth_cache: dict[str, int] = {}

    def depth(n: str) -> int:
        if n in depth_cache:
            return depth_cache[n]
        depth_cache[n] = 1
        d = 1 + max((depth(p) for p in edges.get(n, ())), default=0)
        depth_cache[n] = d
        return d

    for qid in by_id:
        if depth(qid) > cap:
            errors.append(
                f"{qid}: dependency depth {depth(qid)} exceeds max_questions_per_call={cap}"
            )

    if errors:
        raise BankError(f"{len(errors)} error(s) in question bank:\n" + "\n".join(errors))


def _eval_requires(expr: str, answers: dict[str, Any]) -> bool:
    """Evaluates one of the four legal requires forms, or a conjunction /
    disjunction of them joined by ` and ` / ` or ` (both used in
    question_bank.yaml, e.g. 'gender == \\'F\\' and age_band in [...]')."""
    expr = expr.strip()
    if expr == "always":
        return True
    if " and " in expr:
        return all(_eval_requires(p, answers) for p in expr.split(" and "))
    if " or " in expr:
        return any(_eval_requires(p, answers) for p in expr.split(" or "))

    m = _RE_ANSWERED.match(expr)
    if m:
        return m.group(1) in answers

    m = _RE_EQ.match(expr)
    if m:
        attr, val = m.groups()
        return answers.get(attr) == val

    m = _RE_IN.match(expr)
    if m:
        attr, vals_raw = m.groups()
        vals = [v.strip().strip("'\"") for v in vals_raw.split(",") if v.strip()]
        return answers.get(attr) in vals

    raise BankError(f"cannot evaluate requires expression: {expr!r}")


class Bank:
    def __init__(self, raw: dict):
        self._raw = raw
        self._questions = {q["id"]: Question(q["id"], q) for q in raw["questions"]}
        self._order = [q["id"] for q in raw["questions"]]
        self.controls = raw["controls"]
        self.policies = {
            "silence_ladder": raw["silence_ladder"],
            "speech_policy": raw["speech_policy"],
            "invalid_policy": raw["invalid_policy"],
        }
        self.meta = raw["meta"]
        self.defaults = raw["defaults"]

    def question(self, qid: str) -> Question:
        return self._questions[qid]

    def askable(self, answers: dict[str, Any]) -> list[Question]:
        """Every question whose `requires` is satisfied by the current
        answers and which has not been asked yet."""
        answered_attrs = set(answers.keys())
        out = []
        for qid in self._order:
            q = self._questions[qid]
            if q.raw["writes"] in answered_attrs:
                continue
            if _eval_requires(q.get("requires", "always"), answers):
                out.append(q)
        return out


def load_bank(path: str | None = None) -> Bank:
    """Load and validate question_bank.yaml. Raises BankError on any
    validation failure - a malformed bank must never reach a caller."""
    bank_path = path or config.QUESTION_BANK_PATH
    try:
        with open(bank_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise BankError(f"malformed YAML in {bank_path}: {e}") from e

    if not raw or "questions" not in raw:
        raise BankError(f"{bank_path} has no top-level `questions` key")

    declared_attrs = _load_attributes("attribute_seed.sql")
    _validate(raw, declared_attrs)
    return Bank(raw)
