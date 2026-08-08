"""Pre-generates TTS audio for every static prompt in question_bank.yaml,
so a live demo makes ~zero Sarvam calls (SETUP_KEYS.md: "cache every audio
clip to disk on first generation"). Scheme benefit/name text from the DB is
NOT included here - that's per-call, LLM-selected content, not a fixed
prompt set (present.py), so it is cached lazily by voice.tts() itself the
first time each distinct sentence is actually spoken.

Usage:
    python -m scripts.warm_cache
"""
from __future__ import annotations

import sys

from haqdaar.bank import load_bank
from haqdaar import voice


def _walk_policy_strings(node) -> list[tuple[str, str]]:
    """silence_ladder/speech_policy/invalid_policy nest {hi, en} pairs at
    varying depths - walk anything dict/list-shaped and pick up every hi/en
    leaf rather than assuming one fixed shape."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key in ("hi", "en"):
            text = node.get(key)
            if isinstance(text, str) and text.strip():
                out.append((text, "hi-IN" if key == "hi" else "en-IN"))
        for v in node.values():
            out.extend(_walk_policy_strings(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_policy_strings(item))
    return out


def _all_prompt_texts() -> list[tuple[str, str]]:
    bank = load_bank()
    out: list[tuple[str, str]] = []
    for qid in bank._order:
        q = bank.question(qid)
        for key, lang in (("prompt_hi", "hi-IN"), ("prompt_en", "en-IN")):
            text = q.get(key)
            if text:
                out.append((text, lang))
    out.extend(_walk_policy_strings(bank.policies))
    # de-dupe, preserve order
    seen = set()
    deduped = []
    for text, lang in out:
        k = (text, lang)
        if k not in seen:
            seen.add(k)
            deduped.append((text, lang))
    return deduped


def main() -> int:
    if not voice.config.SARVAM_API_KEY:
        print("SARVAM_API_KEY not set - nothing to warm (tts() will print text instead).")
        return 0

    prompts = _all_prompt_texts()
    print(f"Warming {len(prompts)} prompts...")
    ok, failed = 0, 0
    for i, (text, lang) in enumerate(prompts, 1):
        audio = voice.tts(text, lang)
        if audio:
            ok += 1
        else:
            failed += 1
            print(f"  [{i}/{len(prompts)}] FAILED: {text[:60]!r}")
    print(f"Done: {ok} cached, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
