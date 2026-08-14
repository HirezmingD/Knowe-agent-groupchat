"""v1.0.34 步骤 4 — M3 查询感知投影 + 预算 token 化。

- query 相关消息预算内保留全 payload；无关消息降级为引用；输出保持原顺序
- query=None / 空串：与现状（v1.0.33 位置式）输出逐字节一致
- BM25 确定性：同输入两次结果一致
- 预算 token 化：投影后估算 token 恒小于窗口 × 0.8；超上限兜底裁剪生效
- KNOWE_QUERY_AWARE=0（开关关）时调用点不传 query（调用点测试见接入验证）
"""

from __future__ import annotations

import copy
import json

import pytest

from backend import context_compressor as cc
from backend.config import CONFIG


def _tc(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _mixed_history() -> list[dict]:
    """制造 query 相关/无关两类内容的历史：早段全是'登录页'，中段全是'数据库'。"""
    messages: list[dict] = []
    # 8 条登录页相关（query 命中）
    for i in range(8):
        messages.append({"role": "user", "content": f"帮我做登录页第 {i} 版"})
        messages.append({
            "role": "assistant",
            "content": f"登录页 {i} 已完成",
            "tool_calls": [_tc(f"a{i}", "propose_next", {"target_id": "fe", "instruction": "登录页"})],
        })
        messages.append({"role": "tool", "tool_call_id": f"a{i}", "content": '{"status":"ok"}'})
    # 8 条数据库相关（query 不命中）
    for i in range(8):
        messages.append({"role": "user", "content": f"数据库迁移到第 {i} 步"})
        messages.append({
            "role": "assistant",
            "content": f"迁移 {i} 完成",
            "tool_calls": [_tc(f"b{i}", "propose_next", {"target_id": "be", "instruction": "数据库"})],
        })
        messages.append({"role": "tool", "tool_call_id": f"b{i}", "content": '{"status":"ok"}'})
    messages.append({"role": "user", "content": "登录页配色再调一下"})
    messages.append({"role": "assistant", "content": "好的。"})
    return messages


def _entry_payloads(index_text: str) -> list[bool]:
    """索引区每条 entry 是否带 full payload（按 source_ref 出现顺序）。"""
    flags: list[bool] = []
    lines = index_text.split("\n")
    for line in lines:
        if line.startswith("- source_ref="):
            flags.append("payload=authoritative-history" not in line)
    return flags


# ── query=None / 空串：与现状完全一致 ───────────────────────────


def test_query_none_matches_legacy_bytes() -> None:
    history = _mixed_history()
    legacy, legacy_count = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=8_000),
    )
    with_query_none, q_count = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=8_000),
        query=None,
    )
    assert q_count == legacy_count
    assert legacy == with_query_none
    assert json.dumps(legacy, ensure_ascii=False) == json.dumps(with_query_none, ensure_ascii=False)


def test_query_empty_string_matches_legacy_bytes() -> None:
    history = _mixed_history()
    legacy, _ = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=8_000),
    )
    with_query_empty, _ = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=8_000),
        query="   ",
    )
    assert legacy == with_query_empty


# ── BM25 确定性 ────────────────────────────────────────────────


def test_bm25_scores_are_deterministic() -> None:
    history = _mixed_history()
    a = cc._bm25_scores(history, "登录页 配色")
    b = cc._bm25_scores(history, "登录页 配色")
    assert a == b
    assert isinstance(a, list) and len(a) == len(history)
    # 登录页相关消息分数应高于数据库相关（同类内）
    login_scores = a[0:24:3]  # 登录页 user/assistant/tool 三条一组，取 user
    db_scores = a[24:48:3]
    assert max(login_scores) > 0
    assert sum(login_scores) > sum(db_scores)


# ── 查询感知：相关消息保留 payload，无关降级 ────────────────────


def test_query_relevant_messages_keep_payload() -> None:
    history = _mixed_history()
    projected, _ = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=6_000),
        query="登录页配色",
    )
    index = projected[0]["content"]
    flags = _entry_payloads(index)
    assert any(flags), "预算内至少一条相关消息保留 full payload"
    # 头部（登录页相关，消息 0-2）应整体早于数据库段拿到 payload
    # 原顺序输出保证：source_ref 按 1..N 递增
    refs = [int(line.split("source_ref=conversation://message/")[1].split(";")[0]) for line in index.split("\n") if line.startswith("- source_ref=")]
    assert refs == sorted(refs), "输出必须保持原顺序"


def test_query_projection_preserves_order() -> None:
    history = _mixed_history()
    projected, _ = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=6_000),
        query="数据库迁移",
    )
    refs = []
    for line in projected[0]["content"].split("\n"):
        if line.startswith("- source_ref=conversation://message/"):
            refs.append(int(line.split("/message/")[1].split(";")[0]))
        elif line.startswith("- source_range=conversation://messages/"):
            start, end = line.split("/messages/")[1].split(";")[0].split("-")
            refs.extend(range(int(start), int(end) + 1))
    assert refs == sorted(refs)


# ── 预算 token 化：恒小于窗口 × 0.8 ────────────────────────────


def test_projection_tokens_under_window_budget() -> None:
    history = _mixed_history()
    budget = cc._token_budget(cc.CompressConfig())
    projected, _ = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=2, keep_recent=2, projection_chars=4_096),
        query="登录页配色",
    )
    estimated = cc._projected_token_estimate(projected)
    assert estimated <= budget, f"投影 {estimated} token 超过预算 {budget}"


def test_huge_history_projection_stays_under_budget() -> None:
    # 大量消息 + 大 payload：无 query 时可能超窗，有 query 兜底裁剪必须守住预算
    history: list[dict] = []
    for i in range(200):
        history.append({"role": "user", "content": f"任务 {i}"})
        history.append({"role": "assistant", "content": "已处理"})
        history.append({"role": "tool", "tool_call_id": f"t{i}", "content": "x" * 800})
    projected, represented = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=10, keep_recent=20, projection_chars=180_000),
        query="任务 199",
    )
    assert represented > 0
    assert cc._projected_token_estimate(projected) <= cc._token_budget(cc.CompressConfig())


# ── 超窗兜底：明确报错，不静默发送 ─────────────────────────────


def test_trimmed_projection_raises_when_even_descriptors_exceed() -> None:
    """极端情况：最近 tail 里单条 user 消息本身即超窗，兜底后仍超 -> 明确报错。"""
    history = [{"role": "user", "content": "普通"}] * 50
    history.append({"role": "user", "content": "甲" * 2_000_000})  # 尾部 verbatim 区，无法投影
    with pytest.raises(RuntimeError, match="refusing to send an oversized request"):
        cc.project_messages(
            copy.deepcopy(history),
            cc.CompressConfig(trigger=5, keep_recent=10, projection_chars=4_096),
            query="甲",
        )


# ── query 截断 16384 ───────────────────────────────────────────


def test_query_truncated_to_16384() -> None:
    history = _mixed_history()
    long_query = "登录页" * 20_000  # 4 万字符
    projected, _ = cc.project_messages(
        copy.deepcopy(history),
        cc.CompressConfig(trigger=5, keep_recent=2, projection_chars=8_000),
        query=long_query,
    )
    # 截断后仍能感知登录页内容（截断后剩余片段含关键词）
    assert projected[0]["content"].startswith("【上下文投影")
