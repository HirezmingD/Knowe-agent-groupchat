# knowe v0.6 — Harness 核心引擎
# 【纯移植】v0.1 同步版逐字搬过来——它是纯数据处理，不碰 IO，不需要 asyncio。
"""
Message list utilities — structural sanitation and diagnostic token estimates.

Hermes conversation_loop:802 lesson:
    "orphan tool results" — when a tool call exists in messages but
    its result is missing (e.g., conversation was truncated mid-execution).
    OpenAI-compatible APIs reject such messages. We MUST detect and
    either remove the orphan or inject a stub result.
"""

from __future__ import annotations

import json
import re
from typing import Any

from knowe_core.errors import MessageError


# ── Orphan tool result detection & stubbing ──

def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure no orphan tool_calls without corresponding tool results.

    Strategy (from Hermes):
    1. Collect all assistant messages that have tool_calls
    2. For each tool_call, check if a corresponding tool message follows
    3. If not, inject a stub tool result message

    This prevents OpenAI API errors like:
        "An assistant message with 'tool_calls' must be followed by
         tool messages responding to each 'tool_call_id'"

    Returns a NEW list — does not mutate the input.
    """
    result: list[dict[str, Any]] = []
    pending_tool_ids: list[str] = []

    for msg in messages:
        role = msg.get("role", "")

        # [I-6] Provider-agnostic well-formedness: drop a malformed empty assistant
        # frame (neither non-empty content nor tool_calls). OpenAI-compatible APIs
        # reject it ("content or tool_calls must be set"), and a single such frame
        # left in history poisons every subsequent request. This guards legacy
        # frames that predate the drop in the agent loop. It is format hygiene, not
        # rewriting: an empty frame carries no LLM meaning to preserve.
        if role == "assistant":
            has_text = bool(str(msg.get("content") or "").strip())
            has_calls = bool(msg.get("tool_calls"))
            if not has_text and not has_calls:
                continue

        # Collect tool_call IDs from assistant messages
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if "id" in tc:
                    pending_tool_ids.append(tc["id"])

        # tool messages satisfy pending IDs
        if role == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id in pending_tool_ids:
                pending_tool_ids.remove(tc_id)

        result.append(msg)

    # Inject stubs for remaining orphans
    for orphan_id in pending_tool_ids:
        result.append({
            "role": "tool",
            "tool_call_id": orphan_id,
            "content": json.dumps({"error": "tool result unavailable (stub)"}),
        })

    return result


# ── Token estimation (rough, no tiktoken dep) ──

# OpenAI-compatible: ~1 token per 4 characters for English,
# ~1 token per 1.5 characters for Chinese (rough).
# This is NOT exact — purpose is to avoid context overflow, not billing.

_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def estimate_tokens(text: str) -> int:
    """Rough token count for a single string.

    English text: ~4 chars per token.
    Chinese text: ~1.5 chars per token.
    Mixed text: we count CJK chars separately.
    """
    if not text:
        return 0
    cn_chars = len(_CN_CHAR_RE.findall(text))
    other_chars = len(text) - cn_chars
    return int(cn_chars / 1.5 + other_chars / 4)


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count for a list of messages.

    Per OpenAI pricing: each message has ~4 tokens overhead (role framing).
    Tool calls: ~8 tokens overhead.
    """
    total = 0
    for msg in messages:
        total += 4  # role overhead
        if "content" in msg and isinstance(msg["content"], str):
            total += estimate_tokens(msg["content"])
        if "tool_calls" in msg:
            total += 8
            for tc in msg["tool_calls"]:
                if "function" in tc:
                    fn = tc["function"]
                    total += estimate_tokens(fn.get("name", ""))
                    total += estimate_tokens(fn.get("arguments", ""))
        if "tool_call_id" in msg:
            total += 2
    return total


# ── Legacy compatibility ──

def truncate_messages(
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    system_preserved: bool = True,
) -> list[dict[str, Any]]:
    """Return structurally valid messages without applying a token/FIFO crop.

    Context projection is owned by ``backend.context_compressor.project_messages``.
    This compatibility symbol remains for third-party callers from older releases; its
    historical ``max_tokens`` and ``system_preserved`` arguments are accepted but do not
    authorize deletion of conversation facts.
    """

    del max_tokens, system_preserved
    return sanitize_messages(list(messages))
