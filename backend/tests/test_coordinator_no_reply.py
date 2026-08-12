"""v1.0.18 exact control-marker handling without semantic reruns."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.hub import Hub
from backend.engine import ProjectEngine, COORDINATOR, COORDINATOR_ROLE


class _FakeCoord:
    def __init__(self, scripts):
        self.agent_id = COORDINATOR
        self.role = COORDINATOR_ROLE
        self.ephemeral_system_prompt = ""
        self._history: list = []
        self.stream_delta_callback = None
        self.tool_gen_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None
        self._scripts = list(scripts)
        self._i = 0
        self.calls: list[str] = []

    async def run_conversation(self, content, conversation_history=None, attachments=None):
        del conversation_history, attachments
        self.calls.append(content)
        response = self._scripts[self._i] if self._i < len(self._scripts) else "(out)"
        self._i += 1
        return {
            "final_response": response,
            "tool_calls": [],
            "iterations": 1,
            "is_interrupted": False,
            "error": None,
            "new_messages": [],
        }

    def interrupt(self):
        pass

    def steer(self, _text):
        pass

    @property
    def history(self):
        return self._history


def _engine_with_coord(scripts):
    root = tempfile.mkdtemp()
    engine = ProjectEngine(Hub(), "p1", agent=None, workspace_root=Path(root))
    fake = _FakeCoord(scripts)
    engine._agents[COORDINATOR] = fake
    emitted: list[dict] = []
    original_emit = engine.emit

    async def capture(event):
        emitted.append(event)
        return await original_emit(event)

    engine.emit = capture
    return engine, fake, emitted


def _messages(emitted):
    return [event for event in emitted if event.get("type") == "message"]


@pytest.mark.asyncio
async def test_exact_nothing_to_add_is_stripped_without_rerun() -> None:
    engine, fake, emitted = _engine_with_coord([
        "NOTHING_TO_ADD",
        "这条不应被请求",
    ])

    await engine._run_agent_turn("你能帮我看看第二章吗？")

    assert len(fake.calls) == 1
    assert _messages(emitted)[-1].get("content") == ""


@pytest.mark.asyncio
async def test_markdown_wrapped_exact_marker_is_stripped_without_rerun() -> None:
    engine, fake, emitted = _engine_with_coord(["**NOTHING_TO_ADD**"])

    await engine._run_agent_turn("嗯好的谢谢")

    assert len(fake.calls) == 1
    assert _messages(emitted)[-1].get("content") == ""


@pytest.mark.asyncio
async def test_sentence_containing_marker_is_preserved_verbatim() -> None:
    text = "文档里写着 NOTHING_TO_ADD，但这句话本身需要保留。"
    engine, fake, emitted = _engine_with_coord([text])

    await engine._run_agent_turn("原文是什么？")

    assert len(fake.calls) == 1
    assert _messages(emitted)[-1].get("content") == text


@pytest.mark.asyncio
async def test_internal_notification_marker_is_not_rerun() -> None:
    engine, fake, emitted = _engine_with_coord(["NOTHING_TO_ADD"])

    await engine._run_agent_turn("（系统内部通知）某成员交了报告", internal=True)

    assert len(fake.calls) == 1
    assert _messages(emitted)[-1].get("content") == ""
