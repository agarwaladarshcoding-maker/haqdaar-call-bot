#!/usr/bin/env python3
"""Builds a JSON trace of engine.step() reachable states, annotated with
which files were actually active on each turn (bank.py, narrow.py,
select.py, engine.py) so the visual demo (step5_system_flow.html) can
light up an architecture diagram in sync with a call transcript.

Regenerate after any change to engine.py/bank.py/narrow.py/select.py or
question_bank.yaml that could change reachable call states:

    .venv/bin/python demos/gen_step5_trace.py
    # then splice demos/step5_trace.json into step5_system_flow.html's
    # `const TRACE = ...` line and re-open the file.

Usage: run from the repo root (or anywhere - paths are resolved relative
to this file's own location, not the current working directory).
"""
import json
import os
import sys
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

DB = os.path.join(tempfile.gettempdir(), "haqdaar_step5_trace_demo.db")
subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py"), DB], check=True, capture_output=True)

from haqdaar.bank import load_bank
from haqdaar.engine import CallState, step

bank = load_bank()


def snapshot(state, active_files):
    q = bank.question(state.current_question) if state.current_question else None
    opts = []
    if q is not None:
        for k, opt in (q.get("dtmf") or {}).items():
            opts.append({"key": k, "hi": opt.get("hi", ""), "en": opt.get("en", "")})
    return {
        "phase": state.phase,
        "question_id": state.current_question,
        "prompt": (q.get("prompt_hi") if q else None) or state.last_spoken,
        "options": opts,
        "answers": dict(state.answers),
        "asked": list(state.asked),
        "candidate_count": len(state.candidates),
        "candidate_names": [c.scheme_name for c in state.candidates[:6]],
        "invalid_count": state.invalid_count,
        "speech_attempts": state.speech_attempts,
        "silence_elapsed": state.silence_elapsed,
        "active_files": active_files,
    }


def files_for_event(event, changed_answers, presented):
    """Which real files actually did meaningful work for this event -
    engine.py always (it's the entry point), bank.py whenever a question
    is looked up or askable() is consulted, narrow.py whenever candidates
    are recomputed (any answers change or undo), select.py whenever a NEW
    question needs to be chosen (i.e. we're continuing to ask, not
    presenting/ending)."""
    files = ["engine.py"]
    if "dtmf" in event or "timeout" in event or "speech" in event:
        files.append("bank.py")
    if changed_answers:
        files.append("narrow.py")
    if not presented and ("dtmf" in event or "timeout" in event or "speech" in event):
        files.append("select.py")
    return files


nodes = {}
edges = {}

MAX_DEPTH = 4
MAX_BRANCH = 3


def path_id(path):
    return "|".join(path)


# A canonical path is used as the visited-key so that reaching the "same"
# call state via different button sequences (e.g. answer 3 questions then
# undo once, vs. answer 2 questions - both land on the same engine state)
# converges onto one node instead of duplicating it or infinitely
# recursing back and forth between a node and its own undo child.
canonical = {}  # CallState signature -> path already explored for it


def state_signature(state):
    return (
        state.phase, state.current_question,
        tuple(sorted(state.answers.items())), state.asked,
        state.invalid_count, state.speech_attempts, state.silence_elapsed,
    )


def explore(state, path, depth, active_files):
    sig = state_signature(state)
    if sig in canonical:
        # Already have a node for this exact engine state under a
        # different path - point new edges at the canonical path instead
        # of re-exploring (and instead of leaving the edge dangling).
        return canonical[sig]

    pid = path_id(path)
    canonical[sig] = pid
    nodes[pid] = snapshot(state, active_files)
    edges[pid] = {}

    # Global keys (0/*/#) work from EVERY reachable node, including
    # "presenting" (the real engine's step() explicitly handles them there
    # too) - recursing into them (not just snapshotting once) is what
    # makes chained undos and undo-from-presenting actually navigable in
    # the demo, matching the real engine exactly.
    hash_state, _ = step(state, {"dtmf": "#"}, bank, DB)
    hash_pid = explore(hash_state, path + ("undo",), depth, ["engine.py", "bank.py", "narrow.py", "select.py"])
    edges[pid]["undo"] = hash_pid

    zero_state, _ = step(state, {"dtmf": "0"}, bank, DB)
    zero_pid = explore(zero_state, path + ("restart",), 0, ["engine.py", "bank.py", "narrow.py"])
    edges[pid]["restart"] = zero_pid

    if depth < MAX_DEPTH:
        silence_state, _ = step(state, {"timeout": 30}, bank, DB)
        silence_af = files_for_event({"timeout": 30}, dict(silence_state.answers) != dict(state.answers), silence_state.phase != "asking")
        silence_pid = explore(silence_state, path + ("silence30",), depth, silence_af)
        edges[pid]["silence30"] = silence_pid

    if state.phase == "asking" and depth < MAX_DEPTH:
        q = bank.question(state.current_question)
        dtmf_keys = list((q.get("dtmf") or {}).keys())[:MAX_BRANCH]
        for k in dtmf_keys:
            before_answers = dict(state.answers)
            child_state, _ = step(state, {"dtmf": k}, bank, DB)
            changed = dict(child_state.answers) != before_answers
            presented = child_state.phase != "asking"
            af = files_for_event({"dtmf": k}, changed, presented)
            child_pid = explore(child_state, path + (f"dtmf:{k}",), depth + 1, af)
            edges[pid][f"dtmf:{k}"] = child_pid

    return pid


start_state, _ = step(CallState(), {}, bank, DB)
root_pid = explore(start_state, (), 0, ["engine.py", "bank.py", "narrow.py", "select.py"])

out = {"nodes": nodes, "edges": edges, "root": root_pid}
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step5_trace.json")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(json.dumps(out, ensure_ascii=False))
print(f"total nodes: {len(nodes)} -> {out_path}", file=sys.stderr)
