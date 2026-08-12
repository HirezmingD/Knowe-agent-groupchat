"""Wave 9 migration of the message-pipeline regressions.

The retired v0.23 protocol streamed model scratch text before the final answer and
then tried to repair already-visible text with ``stream_reset``.  The deployed
protocol buffers deltas, sanitizes the complete text, and replays exactly once
only when the buffered text is the final answer.  These tests preserve the
privacy, marker-suppression and no-duplication invariants without reintroducing
that retired state machine.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import backend.engine as E
from backend.engine import ProjectEngine


class FakeHub:
    def __init__(self) -> None:
        self.out: list[dict] = []

    async def emit(self, project_id: str, payload: dict) -> dict:
        self.out.append(dict(payload))
        return dict(payload)


def bare() -> ProjectEngine:
    engine = ProjectEngine.__new__(ProjectEngine)
    engine.project_id = "p"
    engine._stream_buffers = {}
    engine._files_produced = {}
    engine.hub = FakeHub()
    engine._public_names = lambda: []  # type: ignore[assignment]
    engine._sanitize_outbound = lambda payload: dict(payload)  # type: ignore[assignment]
    engine.history = []
    engine._trim = lambda: None  # type: ignore[assignment]
    engine._record_activity_from_event = lambda payload: None  # type: ignore[assignment]
    return engine


def deltas(engine: ProjectEngine, agent_id: str | None = None) -> list[str]:
    return [
        item["content"]
        for item in engine.hub.out
        if item.get("type") == "stream_delta"
        and (agent_id is None or item.get("agent_id") == agent_id)
    ]


def feed(engine: ProjectEngine, agent_id: str, *chunks: str) -> None:
    async def _go() -> None:
        for chunk in chunks:
            await engine.emit({"type": "stream_delta", "agent_id": agent_id, "content": chunk})

    asyncio.run(_go())


def finish(engine: ProjectEngine, agent_id: str, final: str) -> None:
    asyncio.run(engine.emit({"type": "message", "agent_id": agent_id, "content": final}))


@pytest.fixture(autouse=True)
def deterministic_sanitizer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        E,
        "sanitize_text",
        lambda text, names=None: re.sub(
            r"\b(fe|be|pm|ux|writer)_\d+\b", "某成员", text or ""
        ),
    )


def test_split_agent_id_is_buffered_and_never_leaks() -> None:
    engine = bare()
    feed(engine, "coordinator", "我让 ", "pm", "_1", " 去做这件事")
    assert deltas(engine) == []
    finish(engine, "coordinator", "我让 某成员 去做这件事")
    assert "".join(deltas(engine)) == "我让 某成员 去做这件事"
    assert "pm_1" not in repr(engine.hub.out)


def test_intermediate_reasoning_is_not_visible_when_final_differs() -> None:
    engine = bare()
    feed(engine, "coordinator", "我先读取 pm", "_1 的内部记录再判断。")
    assert engine.hub.out == []
    finish(engine, "coordinator", "已经处理完成。")
    assert deltas(engine) == []
    assert engine.hub.out[-1]["content"] == "已经处理完成。"


def test_matching_buffer_replays_once_then_emits_one_message() -> None:
    engine = bare()
    body = "这是一段最终可见文本。"
    feed(engine, "coordinator", *body)
    finish(engine, "coordinator", body)
    assert deltas(engine) == [body]
    messages = [item for item in engine.hub.out if item.get("type") == "message"]
    assert len(messages) == 1 and messages[0]["content"] == body


def test_no_delta_goes_out_before_final_message() -> None:
    engine = bare()
    feed(engine, "coordinator", "我先看文件，", "再决定怎么改。")
    assert engine.hub.out == []


def test_empty_final_suppresses_nothing_to_add_marker() -> None:
    engine = bare()
    feed(engine, "coordinator", "NOTHING", "_TO_", "ADD")
    finish(engine, "coordinator", "")
    assert deltas(engine) == []
    assert "NOTHING_TO_ADD" not in repr(engine.hub.out)


def test_handoff_marker_never_replays_as_visible_text() -> None:
    engine = bare()
    feed(engine, "coordinator", "先做测试。\nNEXT_HANDOFF_DIR: handoffs/02-test/")
    finish(engine, "coordinator", "先做测试。")
    assert deltas(engine) == []
    assert "NEXT_HANDOFF_DIR" not in repr(engine.hub.out)


def test_stream_reset_discards_buffered_scratch_text() -> None:
    engine = bare()
    feed(engine, "coordinator", "半句话")
    asyncio.run(engine.emit({"type": "stream_reset", "agent_id": "coordinator"}))
    assert engine._stream_buffers.get("coordinator") is None


def test_final_message_clears_stream_state_for_next_turn() -> None:
    engine = bare()
    feed(engine, "coordinator", "第一轮")
    finish(engine, "coordinator", "第一轮")
    assert not engine._stream_buffers


def test_two_agents_have_independent_buffers() -> None:
    engine = bare()
    feed(engine, "fe_1", "前端完成")
    feed(engine, "be_1", "后端完成")
    finish(engine, "fe_1", "前端完成")
    finish(engine, "be_1", "后端完成")
    assert deltas(engine, "fe_1") == ["前端完成"]
    assert deltas(engine, "be_1") == ["后端完成"]


def test_dispatch_result_uses_card_as_the_visible_message() -> None:
    from backend import tools_knowe

    source = Path(tools_knowe.__file__).read_text("utf-8")
    block = source.rsplit('engine.record_committed_action("propose_next")', 1)[1][:12000]
    message_block = block.split("message=(", 1)[1].split(").replace", 1)[0]
    assert "卡把话说完了" in message_block
    assert "NOTHING_TO_ADD" in message_block
    assert "谁去做、做什么" not in message_block
    assert 'message=f"任务已派给' not in source


def test_coordinator_soul_keeps_structured_card_silence_contract() -> None:
    soul = Path(E.__file__).parent.joinpath("souls", "coordinator.txt").read_text("utf-8")
    assert "工具生成的卡片已经承载动作本身" in soul
    assert "普通自然语言不是系统状态协议" in soul
    assert "NOTHING_TO_ADD" in soul
    assert "正文不要机械复述卡片" in soul
    assert "然后一句「谁去干什么」" not in soul
