"""
test_unit.py — 单元层：ring / 契约 / seq / gate。

每个 BUG 一条永久回归，测试名字里带 BUG 编号——将来谁想删，先解释为什么。
"""

from __future__ import annotations

import asyncio

import pytest

from backend.contract import ContractViolation, validate_outbound
from backend.gate import Gate
from backend.hub import Hub
from backend.ring import RingBuffer


# ═══════════════════════════════════════════════════════════════
# RingBuffer —— B-5
# ═══════════════════════════════════════════════════════════════

def _ev(seq: int, etype: str = "message") -> dict:
    return {"type": etype, "seq": seq, "project_id": "p1"}


def test_ring_incremental_replay():
    ring = RingBuffer(capacity=10)
    for i in range(1, 6):
        ring.append(_ev(i))

    events, gap = ring.replay_since(2)
    assert gap is False
    assert [e["seq"] for e in events] == [3, 4, 5]


def test_ring_replay_from_zero_when_nothing_evicted():
    ring = RingBuffer(capacity=10)
    for i in range(1, 4):
        ring.append(_ev(i))

    events, gap = ring.replay_since(0)
    assert gap is False
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_B5_since_zero_after_eviction_reports_gap_not_partial_history():
    """
    ★ B-5 永久回归：ring 淘汰之后，since_seq=0 必须报 gap，
      **绝不能把残缺历史当完整历史返回**（旧版就是这么把开头吞掉的）。
    """
    ring = RingBuffer(capacity=3)
    for i in range(1, 7):          # 1..6，容量 3 → 1/2/3 被淘汰，只剩 4/5/6
        ring.append(_ev(i))

    assert ring.evicted is True
    events, gap = ring.replay_since(0)
    assert gap is True, "淘汰后 since_seq=0 必须报 gap"
    assert events == [], "报 gap 时一条残缺历史都不许返回"


def test_B5_gap_when_requested_range_evicted():
    ring = RingBuffer(capacity=3)
    for i in range(1, 7):          # 剩 4/5/6
        ring.append(_ev(i))

    _, gap = ring.replay_since(1)  # 想要 2 起，但 2/3 已没了 → 有洞
    assert gap is True

    events, gap = ring.replay_since(3)  # 想要 4 起，正好还在 → 无洞
    assert gap is False
    assert [e["seq"] for e in events] == [4, 5, 6]


def test_ring_rejects_events_without_seq():
    ring = RingBuffer()
    with pytest.raises(ValueError):
        ring.append({"type": "pong"})


# ═══════════════════════════════════════════════════════════════
# 契约校验 —— B-6
# ═══════════════════════════════════════════════════════════════

def test_B6_unregistered_event_type_is_rejected():
    """★ B-6：没在 EVENT_SPEC 里登记的事件，根本发不出去。"""
    with pytest.raises(ContractViolation, match="未登记"):
        validate_outbound({"type": "turn_end", "project_id": "p1", "seq": 1, "ts": "x"})


def test_B6_missing_required_field_is_rejected():
    with pytest.raises(ContractViolation, match="缺必填字段"):
        validate_outbound({"type": "stream_delta", "agent_id": "a",
                           "project_id": "p1", "seq": 1, "ts": "x"})  # 缺 content


def test_B6_wrong_field_name_is_rejected():
    """stream_delta 是 content 不是 text；tool_gen 是 tool_name 不是 tool。"""
    with pytest.raises(ContractViolation):
        validate_outbound({"type": "stream_delta", "agent_id": "a", "text": "hi",
                           "project_id": "p1", "seq": 1, "ts": "x"})
    with pytest.raises(ContractViolation):
        validate_outbound({"type": "tool_gen", "agent_id": "a", "tool": "x",
                           "project_id": "p1", "seq": 1, "ts": "x"})


def test_B6_no_seq_whitelist_must_not_carry_seq():
    """无 seq 白名单事件带上 seq → 前端水位会被污染 → 直接拒发。"""
    with pytest.raises(ContractViolation, match="不得带 seq"):
        validate_outbound({"type": "project_created", "project_id": "p1", "seq": 3})
    for etype in ("pong", "replay_complete", "resync_required", "project_created"):
        pass  # 白名单成员见 contract.NO_SEQ_EVENT_TYPES


def test_engine_events_require_ts():
    with pytest.raises(ContractViolation, match="ts"):
        validate_outbound({"type": "message", "agent_id": "a", "content": "hi",
                           "project_id": "p1", "seq": 1})


def test_approval_card_shape_is_enforced():
    good = {
        "type": "approval_card", "agent_id": "coordinator", "tool": "propose_agents",
        "card_id": "ap_1", "project_id": "p1", "seq": 1, "ts": "x",
        "card": {"status": "pending_approval", "expires_at": "x", "approval_id": "ap_1",
                 "proposed": [{"id": "fe_1", "role": "前端"}]},
    }
    validate_outbound(good)

    bad = {**good, "card": {**good["card"], "proposed": [{"id": "fe_1"}]}}  # 缺 role
    with pytest.raises(ContractViolation):
        validate_outbound(bad)


def test_resolution_enum_is_enforced():
    base = {"type": "approval_resolved", "card_id": "ap_1", "project_id": "p1",
            "seq": 1, "ts": "x"}
    for r in ("approved", "rejected", "timeout", "cancelled"):
        validate_outbound({**base, "resolution": r})
    with pytest.raises(ContractViolation):
        validate_outbound({**base, "resolution": "expired"})  # v1 的旧值，后端从不发


def test_server_error_has_no_seq_engine_error_has_seq():
    """B-3：两级 error 都合法，靠 seq 区分。"""
    validate_outbound({"type": "error", "message": "畸形帧"})                       # 服务器级
    validate_outbound({"type": "error", "message": "炸了", "project_id": "p1",
                       "seq": 7, "ts": "x"})                                        # 引擎级


# ═══════════════════════════════════════════════════════════════
# seq 按项目隔离（PROTOCOL §a）
# ═══════════════════════════════════════════════════════════════

async def test_seq_is_per_project_and_monotonic():
    hub = Hub()
    a1 = await hub.emit("A", {"type": "message", "agent_id": "x", "content": "1"})
    b1 = await hub.emit("B", {"type": "message", "agent_id": "x", "content": "1"})
    a2 = await hub.emit("A", {"type": "message", "agent_id": "x", "content": "2"})
    b2 = await hub.emit("B", {"type": "message", "agent_id": "x", "content": "2"})

    assert (a1["seq"], a2["seq"]) == (1, 2)
    assert (b1["seq"], b2["seq"]) == (1, 2)   # ★ B 的 seq 不受 A 影响


async def test_concurrent_emits_never_collide():
    """单点加锁盖号：并发 100 条也不会撞号。"""
    hub = Hub()
    await asyncio.gather(*[
        hub.emit("A", {"type": "message", "agent_id": "x", "content": str(i)})
        for i in range(100)
    ])
    seqs = [e["seq"] for e in hub.projects["A"].ring.events()]
    assert seqs == list(range(1, 101))


async def test_no_seq_event_cannot_go_through_emit():
    hub = Hub()
    with pytest.raises(ContractViolation):
        await hub.emit("A", {"type": "project_created", "project_id": "A"})


async def test_snapshot_consumes_a_seq_and_enters_ring():
    hub = Hub()
    await hub.emit("A", {"type": "message", "agent_id": "x", "content": "hi"})
    snap = await hub.snapshot("A")

    assert snap["last_seq"] == 1          # 快照拍下的水位
    assert snap["seq"] == 2               # ★ 快照本身消耗一个 seq
    assert hub.projects["A"].ring.newest_seq == 2   # 且进了 ring
    assert [e["type"] for e in snap["conversation"]] == ["message"]


async def test_snapshot_conversation_only_has_structural_events():
    hub = Hub()
    await hub.emit("A", {"type": "agent_thinking", "agent_id": "x"})
    await hub.emit("A", {"type": "stream_delta", "agent_id": "x", "content": "你"})
    await hub.emit("A", {"type": "message", "agent_id": "x", "content": "你好"})

    snap = await hub.snapshot("A")
    types = [e["type"] for e in snap["conversation"]]
    assert types == ["message"]           # 瞬时事件不进时间线


# ═══════════════════════════════════════════════════════════════
# Gate —— BUG-2 / 恰好一次解决
# ═══════════════════════════════════════════════════════════════

async def test_gate_approve_resolves_worker_and_emits_once():
    hub = Hub()
    gate = Gate(hub, "A")

    task = asyncio.create_task(gate.propose(
        tool="propose_agents", agent_id="coordinator",
        card_body={"proposed": [{"id": "fe_1", "role": "前端"}]},
    ))
    await asyncio.sleep(0)                      # 让卡发出去
    card_id = hub.projects["A"].pending_card["approval_id"]

    assert gate.resolve(card_id, "approved") is True
    assert await task == "approved"

    resolved = [e for e in hub.projects["A"].ring.events()
                if e["type"] == "approval_resolved"]
    assert len(resolved) == 1                   # ★ 恰好一条
    assert resolved[0]["resolution"] == "approved"


async def test_BUG2_approve_is_not_stolen_by_a_busy_worker():
    """
    ★ BUG-2 永久回归：worker 正在忙（等审批 = 挂在 Future 上），
      控制指令 approve 必须**立刻**生效——因为它根本不走工作队列。
    """
    hub = Hub()
    gate = Gate(hub, "A")

    worker_done = asyncio.Event()

    async def worker() -> str:
        r = await gate.propose(tool="propose_next", agent_id="coordinator",
                               card_body={"target_id": "fe_1", "instruction": "干活"})
        worker_done.set()
        return r

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    card_id = hub.projects["A"].pending_card["approval_id"]

    gate.resolve(card_id, "approved")           # 控制面直达，没有任何队列可抢
    await asyncio.wait_for(worker_done.wait(), timeout=1)
    assert await task == "approved"


async def test_gate_second_resolution_is_ignored_first_wins():
    hub = Hub()
    gate = Gate(hub, "A")
    task = asyncio.create_task(gate.propose(
        tool="propose_agents", agent_id="c",
        card_body={"proposed": [{"id": "fe_1", "role": "前端"}]}))
    await asyncio.sleep(0)
    card_id = hub.projects["A"].pending_card["approval_id"]

    assert gate.resolve(card_id, "approved") is True
    assert gate.resolve(card_id, "rejected") is False   # 第二次被幂等忽略
    await task

    resolved = [e for e in hub.projects["A"].ring.events()
                if e["type"] == "approval_resolved"]
    assert len(resolved) == 1                          # ★ 后端只发一条


async def test_gate_timeout_emits_timeout_resolution():
    hub = Hub()
    gate = Gate(hub, "A")
    result = await gate.propose(
        tool="propose_agents", agent_id="c",
        card_body={"proposed": [{"id": "fe_1", "role": "前端"}]},
        timeout_s=0.05,
    )
    assert result == "timeout"
    resolved = [e for e in hub.projects["A"].ring.events()
                if e["type"] == "approval_resolved"]
    assert len(resolved) == 1 and resolved[0]["resolution"] == "timeout"


async def test_B4_recovered_card_carries_full_top_level_fields():
    """★ B-4：复提卡的顶层 card_id / agent_id / tool 一个都不能少，且带 recovered 标记。"""
    hub = Hub()
    gate = Gate(hub, "A")
    task = asyncio.create_task(gate.propose(
        tool="propose_agents", agent_id="coordinator",
        card_body={"proposed": [{"id": "fe_1", "role": "前端"}]},
        recovered=True, timeout_s=0.05,
    ))
    await asyncio.sleep(0)

    card_ev = [e for e in hub.projects["A"].ring.events() if e["type"] == "approval_card"][0]
    assert card_ev["card_id"] and card_ev["agent_id"] == "coordinator"
    assert card_ev["tool"] == "propose_agents"
    assert card_ev["card"]["recovered"] is True
    assert card_ev["card"]["approval_id"] == card_ev["card_id"]   # 顶层 == 内层 approval_id
    await task
