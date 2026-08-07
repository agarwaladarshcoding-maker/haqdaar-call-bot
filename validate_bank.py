#!/usr/bin/env python3
"""
Haqdaar Question Bank validator.
Run before every commit. If this fails, the flowchart and the code disagree.

  python3 validate_bank.py question_bank.yaml attribute_seed.sql
"""
import sys, re, json
try:
    import yaml
except ImportError:
    print("pip install pyyaml"); sys.exit(2)

RESERVED = {"0", "*", "#"}   # LOCKED 8 Aug 2026: 0=main menu, *=repeat, #=back
ERRORS, WARNINGS = [], []


def err(m): ERRORS.append(m)
def warn(m): WARNINGS.append(m)


def load_attributes(sql_path):
    txt = open(sql_path, encoding="utf-8").read()
    attrs = set(re.findall(r"^\('([a-z_]+)','(?:enum|bool|band|int|session)'", txt, re.M))
    return attrs


def tokens(expr):
    """Attribute names referenced by a requires expression."""
    if not expr or expr == "always":
        return set()
    return set(re.findall(r"\b([a-z_][a-z0-9_]*)\b(?=\s*(?:==|!=|\bin\b|\bis answered\b))", expr))


def main(bank_path, sql_path):
    bank = yaml.safe_load(open(bank_path, encoding="utf-8"))
    declared_attrs = load_attributes(sql_path)
    qs = bank["questions"]
    by_id = {q["id"]: q for q in qs}

    # ---- 1. count matches meta
    if len(qs) != bank["meta"]["question_count"]:
        err(f"meta.question_count={bank['meta']['question_count']} but {len(qs)} questions found")

    # ---- 2. unique ids
    if len(by_id) != len(qs):
        err("duplicate question ids")

    written = {}
    for q in qs:
        qid = q["id"]

        # ---- 3. every question writes exactly one attribute
        w = q.get("writes")
        if not w:
            err(f"{qid}: no `writes`")
            continue
        # attributes prefixed with _ are session-scratch, not scheme-matchable
        if not w.startswith("_") and w not in declared_attrs:
            err(f"{qid}: writes undeclared attribute `{w}` (add it to attribute_seed.sql)")
        written.setdefault(w, []).append(qid)

        # ---- 4. reserved keys never reused as answers
        for key in (q.get("dtmf") or {}):
            if key in RESERVED:
                err(f"{qid}: uses reserved key `{key}` as an answer option")
            if not key.isdigit() or not (1 <= int(key) <= 9):
                err(f"{qid}: answer key `{key}` outside 1-9")

        # ---- 5. at most 8 options (DTMF ceiling) and at most 6 spoken (memory ceiling)
        n = len(q.get("dtmf") or {})
        if n > 9:
            err(f"{qid}: {n} options, max 9")
        if n > 6:
            warn(f"{qid}: {n} spoken options exceeds the 6-item working-memory guideline (PRD s2)")

        # ---- 6. speech live at every node
        collect = q.get("collect", bank["defaults"]["collect"])
        if not collect.get("speech", False) and not q.get("speech_disabled_reason"):
            err(f"{qid}: speech is off but no speech_disabled_reason given")
        if q.get("speech_disabled_reason") and collect.get("speech", False):
            err(f"{qid}: speech_disabled_reason set but speech is still on")

        if collect.get("speech", False) and not q.get("speech_intents"):
            warn(f"{qid}: speech is on but no speech_intents declared")

        # ---- 7. every question carries a `why` for the LLM selector
        if not q.get("why") or len(q["why"].split()) < 8:
            err(f"{qid}: `why` missing or too short — the selector reads this")
        if not q.get("tags"):
            err(f"{qid}: no tags")
        if not q.get("say_key"):
            err(f"{qid}: no say_key — cannot pre-record audio")

        # ---- 8. options must be static (pre-recordable) unless explicitly dynamic
        if not q.get("dtmf") and not q.get("option_source"):
            if q.get("collect", {}).get("digits") != 0:
                err(f"{qid}: no dtmf options and not marked speech-only")

        # ---- 9. gain sanity
        if not (1 <= q.get("gain", 0) <= 10):
            err(f"{qid}: gain must be 1-10")

    # ---- 10. one writer per attribute (two questions writing the same attribute
    #          is legal only if their `requires` are mutually exclusive)
    for attr, ids in written.items():
        if len(ids) > 1:
            warn(f"attribute `{attr}` written by {ids} — verify their requires are disjoint")

    # ---- 11. DAG: every referenced attribute is produced by some question
    #          and no cycles exist
    producer = {w: ids[0] for w, ids in written.items()}
    edges = {}
    for q in qs:
        deps = tokens(q.get("requires", "always"))
        for d in deps:
            if d not in producer:
                err(f"{q['id']}: requires `{d}` which no question ever writes — unreachable node")
        edges[q["id"]] = {producer[d] for d in deps if d in producer}

    # cycle detection
    WHITE, GREY, BLACK = 0, 1, 2
    color = {qid: WHITE for qid in by_id}

    def visit(n, stack):
        if color[n] == GREY:
            err(f"CYCLE: {' -> '.join(stack + [n])}")
            return
        if color[n] == BLACK:
            return
        color[n] = GREY
        for m in edges.get(n, ()):
            visit(m, stack + [n])
        color[n] = BLACK

    for qid in by_id:
        visit(qid, [])

    # ---- 12. reachability: every question must be reachable from a root
    roots = [q["id"] for q in qs if q.get("requires", "always") == "always"]
    if not roots:
        err("no root question (requires: always)")
    reachable, frontier = set(roots), list(roots)
    rev = {}
    for child, parents in edges.items():
        for p in parents:
            rev.setdefault(p, set()).add(child)
    while frontier:
        n = frontier.pop()
        for c in rev.get(n, ()):
            if c not in reachable:
                reachable.add(c); frontier.append(c)
    for qid in by_id:
        if qid not in reachable:
            err(f"{qid}: unreachable from any root question")

    # ---- 13. depth check: no path longer than max_questions_per_call
    cap = bank["meta"]["max_questions_per_call"]
    depth_cache = {}

    def depth(n):
        if n in depth_cache:
            return depth_cache[n]
        depth_cache[n] = 1  # guard
        d = 1 + max((depth(p) for p in edges.get(n, ())), default=0)
        depth_cache[n] = d
        return d

    for qid in by_id:
        if depth(qid) > cap:
            err(f"{qid}: dependency depth {depth(qid)} exceeds max_questions_per_call={cap}")

    # ---- report
    print(f"questions      : {len(qs)}")
    print(f"attributes used: {len(written)}")
    print(f"root questions : {roots}")
    print(f"max depth      : {max(depth_cache.values()) if depth_cache else 0}")
    for w in WARNINGS:
        print(f"WARN  {w}")
    for e in ERRORS:
        print(f"ERROR {e}")
    if ERRORS:
        print(f"\nFAILED with {len(ERRORS)} error(s)")
        return 1
    print(f"\nOK ({len(WARNINGS)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
