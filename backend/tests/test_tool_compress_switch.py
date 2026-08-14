"""v1.0.34 步骤 3 — 工具结果压缩开关验证。

- 开关=0（默认）：请求载体与权威副本均原文，行为零变化
- 开关=1：messages（发给 provider）里 tool content 被压缩，
  结构（role/tool_call_id）不变；new_messages（权威历史）仍是原文
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import CONFIG
from knowe_core.agent_loop import AgentLoop, AgentLoopConfig
from knowe_core.tool_registry import ToolRegistry

from tests._fakes import FakeProvider, text_turn, tool_turn

_BIG_REPEATED_LOG = ("ERROR connection timeout\n" * 400) + "INFO done\n"  # ~8.8K 字符，过 2000 门槛
_BIG_JSON = json.dumps([{"name": "a", "qty": 1}] * 1000, ensure_ascii=False)  # ~23K 字符


def _registry_with_verbose_tools() -> tuple[ToolRegistry, list[str]]:
    reg = ToolRegistry()
    calls: list[str] = []

    def _log_tool(args, **_ctx):
        calls.append("log")
        return _BIG_REPEATED_LOG

    def _json_tool(args, **_ctx):
        calls.append("json")
        return _BIG_JSON

    reg.register(
        name="run_pytest",
        description="run tests",
        parameters={"type": "object"},
        handler=_log_tool,
    )
    reg.register(
        name="list_rows",
        description="list rows",
        parameters={"type": "object"},
        handler=_json_tool,
    )
    return reg, calls


def _cfg() -> AgentLoopConfig:
    return AgentLoopConfig(
        system_prompt="sys",
        messages=[{"role": "user", "content": "跑测试"}],
    )


def _provider_tool_messages(provider: FakeProvider) -> list[dict]:
    """收集发给 provider 的请求里所有 role=tool 的消息。"""
    out = []
    for call in provider.calls:
        out.extend(m for m in call["messages"] if m.get("role") == "tool")
    return out


# ── 开关=0（默认）：行为零变化 ──────────────────────────────────


@pytest.mark.asyncio
async def test_switch_off_passthrough_exact() -> None:
    provider = FakeProvider([
        tool_turn("run_pytest", {}),
        text_turn("修复完成。"),
    ])
    reg, calls = _registry_with_verbose_tools()
    object.__setattr__(CONFIG, "tool_compress_enabled", False)  # 显式关（不依赖默认值）
    try:
        result = await AgentLoop(client=provider, registry=reg).run(_cfg())

        assert result.final_response == "修复完成。"
        assert calls == ["log"]
        sent = _provider_tool_messages(provider)
        assert len(sent) == 1
        assert sent[0]["content"] == _BIG_REPEATED_LOG  # 原文，一字未动
        assert sent[0]["role"] == "tool"
        assert "elided" not in sent[0]["content"]
        # 权威副本 = 原文
        authoritative_tool = [m for m in result.new_messages if m.get("role") == "tool"]
        assert authoritative_tool[0]["content"] == _BIG_REPEATED_LOG
    finally:
        object.__setattr__(CONFIG, "tool_compress_enabled", True)


# ── 开关=1：请求载体压缩，权威副本原文 ──────────────────────────


@pytest.mark.asyncio
async def test_switch_on_compresses_request_carrier_only() -> None:
    provider = FakeProvider([
        tool_turn("run_pytest", {}),
        text_turn("修复完成。"),
    ])
    reg, calls = _registry_with_verbose_tools()
    object.__setattr__(CONFIG, "tool_compress_enabled", True)

    try:
        result = await AgentLoop(client=provider, registry=reg).run(_cfg())
    finally:
        object.__setattr__(CONFIG, "tool_compress_enabled", False)

    assert calls == ["log"]
    sent = _provider_tool_messages(provider)
    assert len(sent) == 1
    # 请求载体被压缩：400 行相同 -> 标记行
    assert "… 400 lines elided (knowe) …" in sent[0]["content"]
    assert sent[0]["role"] == "tool"
    assert sent[0]["tool_call_id"]  # 结构不变
    # 权威副本仍是原文
    authoritative_tool = [m for m in result.new_messages if m.get("role") == "tool"]
    assert authoritative_tool[0]["content"] == _BIG_REPEATED_LOG


@pytest.mark.asyncio
async def test_switch_on_compresses_json_tool_result() -> None:
    provider = FakeProvider([
        tool_turn("list_rows", {}),
        text_turn("查完了。"),
    ])
    reg, calls = _registry_with_verbose_tools()
    object.__setattr__(CONFIG, "tool_compress_enabled", True)

    try:
        result = await AgentLoop(client=provider, registry=reg).run(_cfg())
    finally:
        object.__setattr__(CONFIG, "tool_compress_enabled", False)

    assert calls == ["json"]
    sent = _provider_tool_messages(provider)
    assert len(sent) == 1
    assert "rows[1000]{name,qty}:" in sent[0]["content"]  # 同构对象数组 -> 表格
    assert sent[0]["role"] == "tool"
    authoritative_tool = [m for m in result.new_messages if m.get("role") == "tool"]
    assert authoritative_tool[0]["content"] == _BIG_JSON
