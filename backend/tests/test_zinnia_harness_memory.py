"""Zinnia Harness Memory regression tests.

The original file was a standalone script that called ``sys.exit`` at import time,
which made pytest collection abort.  Keep the same four assertions as normal tests.
"""

from __future__ import annotations

import pytest

from backend.agents.zinnia import ZinniaAgent, _build_tools


def _tool_names() -> set[str]:
    return {
        str((item.get("function") or {}).get("name") or "")
        for item in _build_tools()
        if isinstance(item, dict)
    }


def test_zinnia_tools_include_harness_memory_and_project_creation() -> None:
    names = _tool_names()
    assert "read_harness_memory" in names
    assert "create_project" in names


@pytest.mark.asyncio
async def test_zinnia_reads_injected_harness_memory() -> None:
    agent = ZinniaAgent(read_harness=lambda: "＝全局公告栏＝")
    call = {"function": {"name": "read_harness_memory", "arguments": "{}"}}

    output = await agent._execute(call, None, None)

    assert "全局公告栏" in output


@pytest.mark.asyncio
async def test_zinnia_missing_harness_memory_is_graceful() -> None:
    agent = ZinniaAgent()
    call = {"function": {"name": "read_harness_memory", "arguments": "{}"}}

    output = await agent._execute(call, None, None)

    assert "未启用" in output


# ── [v1.0.22.1-对齐 B] 平台级对话记忆（知知频道沉淀）──

from backend.agents.base import Turn  # noqa: E402
from backend.memory_manager import MemoryManager  # noqa: E402


def _turn(content: str) -> Turn:
    return Turn("__platform__", "Zinnia", content, [])


def test_zinnia_sink_platform_memory_records_reply() -> None:
    captured: list[str] = []
    agent = ZinniaAgent(memory_sink=captured.append)
    agent._sink_platform_memory(_turn("我是谁"), "你是 kai")

    assert len(captured) == 1
    assert "我是谁" in captured[0]
    assert "你是 kai" in captured[0]


def test_zinnia_sink_skips_empty_sides() -> None:
    captured: list[str] = []
    agent = ZinniaAgent(memory_sink=captured.append)
    agent._sink_platform_memory(_turn(""), "reply")   # 空用户消息 → 跳过
    agent._sink_platform_memory(_turn("hi"), "")       # 空回复 → 跳过

    assert captured == []


def test_zinnia_sink_survives_sink_exception() -> None:
    def boom(_line: str) -> None:
        raise RuntimeError("sink down")

    agent = ZinniaAgent(memory_sink=boom)
    agent._sink_platform_memory(_turn("hi"), "hello")  # 异常不冒泡
    assert True


def test_zinnia_sink_noop_without_sink() -> None:
    agent = ZinniaAgent()
    agent._sink_platform_memory(_turn("hi"), "hello")  # 无 sink 不抛
    assert True


# ── memory_manager 平台记忆层 ──


def test_platform_memory_append_and_read(tmp_path) -> None:
    mm = MemoryManager(tmp_path)
    mm.append_platform_memory("第一条")
    mm.append_platform_memory("第二条")

    assert mm.read_platform_memory_lines() == ["第一条", "第二条"]
    assert mm.platform_memory_path.is_file()


def test_platform_memory_bounded(tmp_path) -> None:
    mm = MemoryManager(tmp_path)
    for i in range(25):
        mm.append_platform_memory(f"第{i}条")

    lines = mm.read_platform_memory_lines()
    assert len(lines) == 20
    assert lines[0] == "第5条"
    assert lines[-1] == "第24条"


def test_platform_memory_brief_last_three(tmp_path) -> None:
    mm = MemoryManager(tmp_path)
    for i in range(5):
        mm.append_platform_memory(f"第{i}条")

    brief = mm.read_platform_memory_brief()
    assert "第2条" in brief
    assert "第4条" in brief
    assert "第0条" not in brief


def test_platform_memory_empty_when_missing(tmp_path) -> None:
    mm = MemoryManager(tmp_path)
    assert mm.read_platform_memory_lines() == []
    assert mm.read_platform_memory_brief() == ""

