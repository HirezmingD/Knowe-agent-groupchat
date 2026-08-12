"""Shared scripted Provider doubles for Coordinator AgentLoop regressions."""

from __future__ import annotations

import json
from typing import Any


# ──────────────────────────────────────────────────────────────
# Event builders — the shape ProviderClient.chat_stream yields and
# StreamAssembler.feed consumes.
# ──────────────────────────────────────────────────────────────
def text_turn(content: str) -> list[dict[str, Any]]:
    """A plain-text assistant turn (no tool calls)."""
    return [
        {"type": "delta", "content": content},
        {"type": "finish", "reason": "stop"},
    ]


def tool_turn(name: str, args: dict[str, Any] | None = None,
              call_id: str = "call_1", content: str = "") -> list[dict[str, Any]]:
    """An assistant turn that emits a single tool call (optionally with text)."""
    events: list[dict[str, Any]] = []
    if content:
        events.append({"type": "delta", "content": content})
    events.append({
        "type": "tool_call",
        "tool_call": {
            "index": 0,
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args or {}, ensure_ascii=False),
            },
        },
    })
    events.append({"type": "finish", "reason": "tool_calls"})
    return events


class FakeProvider:
    """Scripts a fixed sequence of model turns and records each call.

    ``scripts`` is a list of event-lists (one per model turn). Each call to
    ``chat_stream`` pops the next script. ``calls`` records the kwargs of every
    call so tests can assert on ``extra_body`` (tool_choice), tools, etc.
    """

    def __init__(self, scripts: list[list[dict[str, Any]]]):
        self._scripts = list(scripts)
        self._i = 0
        self.calls: list[dict[str, Any]] = []

    def chat_stream(self, *, messages, tools=None, temperature=0.7,
                    max_tokens=None, extra_body=None):
        self.calls.append({
            "messages": [dict(m) for m in messages],
            "tools": tools,
            "extra_body": dict(extra_body) if extra_body else None,
        })
        script = (self._scripts[self._i]
                  if self._i < len(self._scripts)
                  else text_turn("(fake provider ran out of script)"))
        self._i += 1

        async def _gen():
            for ev in script:
                yield ev

        return _gen()

    # convenience
    @property
    def call_count(self) -> int:
        return len(self.calls)

    def tool_choice_of(self, call_index: int) -> str | None:
        eb = self.calls[call_index].get("extra_body") or {}
        return eb.get("tool_choice")
