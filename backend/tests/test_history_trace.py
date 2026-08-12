"""[v0.34 修复B] 跨回合保留完整工具轨迹（不再只留最后一条 assistant 文本）。

对应审计报告：
  · GPT §三「更严重的跨回合历史丢失」+ §十.5「完整轨迹跨回合测试」
  · Claude 方案（工具轨迹持久化）

跑法：  pytest tests/test_history_trace.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowe_core.agent_loop import AgentLoop, AgentLoopConfig
from knowe_core.tool_registry import ToolRegistry

from backend.agents.knowe_agent import _history_key
from tests._fakes import FakeProvider, text_turn, tool_turn


def _read_registry():
    reg = ToolRegistry()
    reg.register(name="safe_read_file", description="read",
                 parameters={"type": "object"},
                 handler=lambda a, **k: '{"status":"ok","content":"chapter 2 body"}')
    return reg


@pytest.mark.asyncio
async def test_new_messages_carries_full_atomic_chain():
    """一轮里：读文件 → 拿到回执 → 收尾。result.new_messages 必须完整保留
    assistant(tool_calls) + tool(result) + assistant(final)，而不是只有最后一句。"""
    provider = FakeProvider([
        tool_turn("safe_read_file", {"path": "index.html"}, call_id="c1"),
        text_turn("第二章读到了 chapter 2 body。"),
    ])
    loop = AgentLoop(client=provider, registry=_read_registry())
    cfg = AgentLoopConfig(
        system_prompt="s",
        messages=[{"role": "user", "content": "读第二章"}],
    )
    result = await loop.run(cfg)

    roles = [m["role"] for m in result.new_messages]
    # 至少包含：assistant(带 tool_calls) → tool 回执 → assistant(final)
    assert "tool" in roles, "工具回执丢了 —— 跨回合就没有证据了"
    assert roles.count("assistant") >= 2, "中间的 assistant(tool_calls) 丢了"

    # 工具回执带 tool_call_id，且内容是真实回执
    tool_msgs = [m for m in result.new_messages if m["role"] == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert "chapter 2 body" in tool_msgs[0]["content"]

    # 带 tool_calls 的 assistant 也在（不是只留最后一句文本）
    tc_assistants = [m for m in result.new_messages
                     if m["role"] == "assistant" and m.get("tool_calls")]
    assert tc_assistants, "带 tool_calls 的 assistant 消息必须保留"
    assert tc_assistants[0]["tool_calls"][0]["function"]["name"] == "safe_read_file"


@pytest.mark.asyncio
async def test_plain_model_text_adds_no_hidden_scaffolding_to_trace():
    """Action-sounding prose is committed as-is; no hidden user nudge is invented."""
    provider = FakeProvider([
        text_turn("让我看看 index.html 第二章。"),
        tool_turn("safe_read_file", {"path": "index.html"}),
    ])
    loop = AgentLoop(client=provider, registry=_read_registry())
    cfg = AgentLoopConfig(
        system_prompt="s",
        messages=[{"role": "user", "content": "看第二章"}],
    )
    result = await loop.run(cfg)

    assert provider.call_count == 1
    assert result.new_messages == [
        {"role": "assistant", "content": "让我看看 index.html 第二章。"}
    ]
    assert all(message["role"] != "user" for message in result.new_messages)


def test_history_key_does_not_collapse_distinct_tool_results():
    """两条不同 tool_call_id 的回执、内容都为空 → 不能被 (role,content) 误判成重复。"""
    a = {"role": "tool", "tool_call_id": "c1", "content": ""}
    b = {"role": "tool", "tool_call_id": "c2", "content": ""}
    assert _history_key(a) != _history_key(b)


def test_history_key_distinguishes_tool_call_assistants():
    """两条 content 均为 None、但 tool_calls 不同的 assistant → 键必须不同。"""
    a = {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "safe_read_file",
                                                  "arguments": "{}"}}]}
    b = {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c2", "function": {"name": "safe_write_file",
                                                  "arguments": "{}"}}]}
    assert _history_key(a) != _history_key(b)


def test_history_key_plain_messages_dedup_normally():
    """普通消息仍按 (role, content) 去重（幂等合并不回归）。"""
    a = {"role": "user", "content": "hello"}
    b = {"role": "user", "content": "hello"}
    assert _history_key(a) == _history_key(b)
