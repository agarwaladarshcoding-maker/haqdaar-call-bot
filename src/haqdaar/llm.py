"""Thin OpenAI-chat-completions-compatible client, shared by every LLM call
site in this codebase (select.py's question tie-break, present.py's live
wording, scripts/extract_rules.py's offline rule extraction).

This module makes network calls. Nothing in engine.py, narrow.py, or
menu.py imports it directly or transitively through a required path -
every call site that uses it takes an injectable `llm_caller` and has a
deterministic fallback for when this module's calls fail, time out, or
LLM_API_KEY is unset (config.py's contract: every external key is
optional and the system runs correctly without it).
"""
from __future__ import annotations

import json

import httpx

from haqdaar import config


class LLMError(Exception):
    """Raised on any network/API failure. Callers catch this and fall back -
    never let an LLM outage propagate into a live call."""


def chat(
    system: str,
    user: str,
    *,
    timeout_s: float | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    json_mode: bool = True,
) -> str:
    """One-shot chat completion. Returns the raw text content. Raises
    LLMError on any failure - never returns None or a partial response
    silently, so callers can distinguish "got a bad answer" (their job to
    validate) from "the call itself failed" (always a hard fallback)."""
    if not config.LLM_API_KEY:
        raise LLMError("LLM_API_KEY not set")

    base_url = config.LLM_BASE_URL or "https://api.openai.com/v1"
    timeout = timeout_s if timeout_s is not None else config.LLM_TIMEOUT_MS / 1000.0

    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except httpx.HTTPError as e:
        raise LLMError(f"HTTP error: {e}") from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMError(f"malformed response shape: {e}") from e
