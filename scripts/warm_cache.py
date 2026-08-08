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
import time

from haqdaar.bank import load_bank
from haqdaar import twilio_adapter, voice


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
    # The wait message twilio_adapter plays on EVERY turn. It is not a
    # question prompt, so walking the bank alone would miss it - and a
    # cache miss on the single most-played clip in the system is the most
    # expensive one there is.
    out.append((twilio_adapter.WAIT_TEXT, twilio_adapter.TTS_LANG))
    # de-dupe, preserve order
    seen = set()
    deduped = []
    for text, lang in out:
        k = (text, lang)
        if k not in seen:
            seen.add(k)
            deduped.append((text, lang))
    return deduped


# A long prompt takes 4-6s to synthesize (measured). config.TTS_TIMEOUT_MS
# is deliberately 4s because synthesis happens inside a Twilio webhook
# that must answer within 15s - but THIS is an offline batch job where
# latency costs nothing and an incomplete cache costs everything, so it
# gets its own budget. Without this the longest prompts silently fail to
# warm, which is exactly the case where a live cache miss hurts most.
WARM_TIMEOUT_MS = 30_000


def main() -> int:
    if not voice.config.SARVAM_API_KEY:
        print("SARVAM_API_KEY not set - nothing to warm (tts() will print text instead).")
        return 0

    voice.config.TTS_TIMEOUT_MS = WARM_TIMEOUT_MS

    prompts = _all_prompt_texts()
    print(f"Warming {len(prompts)} prompts...")
    ok, failed = 0, 0
    failures: list[str] = []
    for i, (text, lang) in enumerate(prompts, 1):
        audio = voice.tts(text, lang)
        if audio:
            ok += 1
        else:
            # One retry: the free tier rate-limits under a fast batch, and
            # a transient miss here becomes a live synthesis during a call.
            time.sleep(2.0)
            audio = voice.tts(text, lang)
            if audio:
                ok += 1
            else:
                failed += 1
                failures.append(text)
                print(f"  [{i}/{len(prompts)}] FAILED: {text[:60]!r}")

    print(f"Done: {ok} cached, {failed} failed.")
    if failures:
        print("\nThese will be synthesized live during a call (4-6s each) or fall")
        print("back to Twilio's own voice. Re-run to retry them.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
