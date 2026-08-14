"""v1.0.18 non-destructive context projection regressions."""

from __future__ import annotations

import copy
import json
import tempfile
import time
from pathlib import Path

import pytest

from backend import context_compressor as cc
from knowe_core.messages import sanitize_messages


def _tc(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _long_history(exec_pairs: int = 40) -> list[dict]:
    messages = [
        {"role": "user", "content": "帮我做一个小说阅读器，务必用纯 HTML，绝对不要 React"},
        {
            "role": "assistant",
            "content": "好的",
            "tool_calls": [_tc("a1", "propose_agents", {"members": "前端"})],
        },
        {"role": "tool", "tool_call_id": "a1", "content": '{"status":"ok"}'},
        {
            "role": "assistant",
            "content": "派活",
            "tool_calls": [
                _tc(
                    "a2",
                    "propose_next",
                    {"target_id": "fe_1", "instruction": "把第二章正文写进 index.html"},
                )
            ],
        },
        {"role": "tool", "tool_call_id": "a2", "content": '{"status":"ok"}'},
    ]
    for index in range(exec_pairs):
        call_id = f"r{index}"
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [_tc(call_id, "safe_read_file", {"path": "index.html"})],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": "<html>" + "x" * 400 + "</html>",
        })
    messages += [
        {
            "role": "assistant",
            "content": "第二章已写入并重读确认 230 段",
            "worker_runtime": {
                "state": "IDLE",
                "completion_status": "SUCCEEDED",
                "submission_committed": True,
            },
        },
        {"role": "user", "content": "现在把第三章也写进去"},
        {"role": "assistant", "content": "好的，我来安排。"},
    ]
    return messages


def test_projection_does_not_mutate_authoritative_history() -> None:
    messages = _long_history()
    before = copy.deepcopy(messages)

    projected, represented = cc.project_messages(messages)

    assert represented > 0
    assert messages == before
    assert projected is not messages
    assert projected[0]["metadata"]["authoritative"] is False
    assert projected[0]["metadata"]["source_count"] == represented


def test_every_older_message_is_represented_by_ref_or_range() -> None:
    messages = _long_history(exec_pairs=40)
    projected, represented = cc.project_messages(
        messages,
        cc.CompressConfig(trigger=10, keep_recent=2, projection_chars=8_000),
    )
    index = projected[0]["content"]

    assert represented == len(messages) - 2
    assert f"projected_messages={represented}" in index
    assert "source_ref=conversation://message/" in index or "source_range=conversation://messages/" in index
    assert "sha256=" in index
    assert "payload=authoritative-history" in index
    assert "非权威副本" in index


def test_recent_protocol_complete_tail_is_verbatim() -> None:
    messages = _long_history()
    projected, _ = cc.project_messages(messages)
    assert projected[-2:] == messages[-2:]


def test_projection_budget_changes_payload_detail_not_authority_count() -> None:
    messages = _long_history(exec_pairs=80)
    small, small_count = cc.project_messages(
        messages,
        cc.CompressConfig(trigger=2, keep_recent=2, projection_chars=4_096),
    )
    large, large_count = cc.project_messages(
        messages,
        cc.CompressConfig(trigger=2, keep_recent=2, projection_chars=40_000),
    )
    assert small_count == large_count == len(messages) - 2
    assert len(small[0]["content"]) < len(large[0]["content"])
    assert small[0]["metadata"]["source_count"] == large[0]["metadata"]["source_count"]


def test_projected_sequence_is_provider_protocol_valid() -> None:
    projected, _ = cc.project_messages(_long_history())
    assert all(message.get("role") in {"user", "assistant", "tool"} for message in projected)
    assert sanitize_messages(projected) == projected
    assert "tool_calls" not in projected[0]
    tail = projected[1:]
    if tail:
        assert tail[0]["role"] == "user"


def test_one_huge_turn_is_represented_without_slicing_ordinary_language() -> None:
    text = "甲" * 250_000
    messages = [{"role": "user", "content": text}]
    projected, represented = cc.project_messages(
        messages,
        cc.CompressConfig(trigger=1, keep_recent=1, projection_chars=4_096),
    )
    assert represented == 1
    assert len(projected) == 1
    assert projected[0]["metadata"]["source_ref"] == "conversation://messages/1-1"
    assert text not in projected[0]["content"]
    assert "chars=" in projected[0]["content"] and "sha256=" in projected[0]["content"]


def test_performance_thousands_of_messages() -> None:
    messages = _long_history(exec_pairs=2_000)
    started = time.perf_counter()
    projected, represented = cc.project_messages(messages)
    elapsed = time.perf_counter() - started
    assert represented > 0
    assert elapsed < 2.0
    assert len(messages) > len(projected)


def test_projection_is_deterministic_across_runs() -> None:
    """M1 幂等契约：同输入两次投影，字节级一致。"""
    messages = _long_history(exec_pairs=40)
    first, first_count = cc.project_messages(messages)
    second, second_count = cc.project_messages(messages)
    assert first_count == second_count
    assert first == second
    # 深度序列化后逐字节一致（防"相等但字节不同"的幻影）
    import json as _json

    assert _json.dumps(first, ensure_ascii=False) == _json.dumps(second, ensure_ascii=False)


def test_projection_is_idempotent_on_projected_output() -> None:
    """M1 幂等契约：投影产物再投影，输出与首次投影字节级一致。"""
    messages = _long_history(exec_pairs=40)
    once, _ = cc.project_messages(messages)
    twice, second_count = cc.project_messages(once)
    assert second_count == 0  # 投影产物已低于触发条件，不应二次改写
    assert twice == once
    import json as _json

    assert _json.dumps(twice, ensure_ascii=False) == _json.dumps(once, ensure_ascii=False)


def test_projection_bytes_identical_with_default_config() -> None:
    """M1 幂等契约：默认配置下同输入两次投影字节一致（覆盖 DEFAULT_CONFIG 路径）。"""
    messages = _long_history(exec_pairs=80)
    first, _ = cc.project_messages(messages, cc.DEFAULT_CONFIG)
    second, _ = cc.project_messages(messages, cc.DEFAULT_CONFIG)
    import json as _json

    assert _json.dumps(first, ensure_ascii=False) == _json.dumps(second, ensure_ascii=False)


def test_layer_classification() -> None:
    assert cc.classify_layer({"role": "user", "content": "写个登录页"}) == "L1_user"
    assert cc.classify_layer({
        "role": "user",
        "content": "⚠【系统内部指令 · 用户看不到这段文字】\n...",
    }) == "L3_system"
    assert cc.classify_layer({
        "role": "assistant",
        "tool_calls": [_tc("c", "propose_next", {})],
    }) == "L1_decision"
    assert cc.classify_layer({
        "role": "assistant",
        "content": "验收通过，已交付",
        "worker_runtime": {"completion_status": "SUCCEEDED"},
    }) == "L1_deliverable"
    assert cc.classify_layer({
        "role": "assistant",
        "content": "请提供登录凭据",
        "worker_runtime": {"completion_status": "WAITING"},
    }) == "L1_deliverable"
    assert cc.classify_layer({
        "role": "assistant",
        "tool_calls": [_tc("c", "safe_read_file", {})],
    }) == "L2_exec"


def test_scaffolding_detection_is_exact_to_harness_prefixes() -> None:
    assert cc.is_scaffolding_user("⚠【系统内部指令 · 用户看不到这段文字】\n通知")
    assert cc.is_scaffolding_user("⚠【执行提醒 · 内部纸条，勿向用户复述】...")
    assert cc.is_scaffolding_user("⚠【上一轮事故 · 内部】...")
    assert not cc.is_scaffolding_user("如果任务已完成，请走正式出口：...")
    assert not cc.is_scaffolding_user("帮我把第三章写进去")


def test_toggle_off_returns_full_equal_copy() -> None:
    messages = _long_history()
    projected, represented = cc.project_messages(messages, cc.CompressConfig(enabled=False))
    assert represented == 0
    assert projected == messages
    assert projected is not messages


def test_below_trigger_is_equal_copy() -> None:
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    projected, represented = cc.project_messages(messages)
    assert represented == 0
    assert projected == messages
    assert projected is not messages


class _FakeAgentWithHistory:
    def __init__(self, history):
        self.agent_id = "coordinator"
        self._history = history


def test_engine_repairs_protocol_without_trimming_history() -> None:
    from backend.engine import ProjectEngine
    from backend.hub import Hub

    engine = ProjectEngine(Hub(), "p1", agent=None, workspace_root=Path(tempfile.mkdtemp()))
    history = _long_history(exec_pairs=60)
    before = copy.deepcopy(history)
    agent = _FakeAgentWithHistory(history)

    fixed = engine.repair_agent_history(agent)

    assert fixed == 0
    assert agent._history == before


@pytest.mark.asyncio
async def test_knowe_agent_projects_only_at_provider_boundary() -> None:
    from backend.agents.knowe_agent import KnoweAgent
    from tests._fakes import FakeProvider, text_turn

    agent = KnoweAgent("coordinator", "总管")
    agent._history = _long_history(exec_pairs=60)
    before = copy.deepcopy(agent._history)
    provider = FakeProvider([text_turn("完成。")])
    agent._client = provider

    result = await agent.run_conversation("继续")

    assert result["final_response"] == "完成。"
    assert agent._history[: len(before)] == before
    assert agent._history[len(before)] == {"role": "user", "content": "继续"}
    assert not any(
        isinstance(message.get("content"), str)
        and message["content"].startswith(cc.SUMMARY_MARK)
        for message in agent._history
    )
    sent = provider.calls[0]["messages"]
    assert any(
        isinstance(message.get("content"), str)
        and message["content"].startswith(cc.SUMMARY_MARK)
        for message in sent
    )
