# -*- coding: utf-8 -*-
"""
[v1.0.23.6] 增量读取接口测试：hub.durable_since / HTTP /api/events 的过滤语义。

覆盖：
· after_seq 过滤（只返回 seq > N）
· 磁盘 + ring 合并（ring 未落盘事件也进增量）
· 空增量（已同步到最新）
· 非结构事件排除（stream_delta 等不进增量）
"""

import pytest

from backend.hub import Hub
from backend.persist import Store


@pytest.mark.asyncio
async def test_durable_since_returns_only_events_after_seq(tmp_path):
    store = Store(tmp_path)
    hub = Hub(store=store)

    for i in range(1, 6):
        await hub.emit("p1", {"type": "message", "agent_id": "a", "content": f"msg {i}"})

    # after_seq=2 → 只回 3,4,5
    events = hub.durable_since("p1", 2)
    assert [e["seq"] for e in events] == [3, 4, 5]

    # after_seq=5 → 空增量（已同步到最新）
    assert hub.durable_since("p1", 5) == []

    # after_seq=0 → 全量
    assert len(hub.durable_since("p1", 0)) == 5


@pytest.mark.asyncio
async def test_durable_since_includes_ring_events_not_yet_on_disk(tmp_path):
    """ring 未落盘的结构事件也要进增量（写盘偶发失败时不能丢）。"""
    store = Store(tmp_path)
    hub = Hub(store=store)

    await hub.emit("p1", {"type": "message", "agent_id": "a", "content": "1"})
    # 模拟「进了 ring 但磁盘没写」：直接往 ring 塞一条（hub.emit 会双写，这里手动模拟）
    proj = hub.projects["p1"]
    ev = {"type": "approval_card", "project_id": "p1", "seq": 2,
          "card_id": "c1", "instruction": "x", "agent_id": "a",
          "ts": "t", "status": "pending"}
    proj.ring.append(ev)

    events = hub.durable_since("p1", 1)
    seqs = [e["seq"] for e in events]
    assert 2 in seqs, "ring 未落盘的结构事件必须出现在增量里"


@pytest.mark.asyncio
async def test_durable_since_excludes_non_structural_events(tmp_path):
    """stream_delta 等瞬时帧不进增量（与落盘白名单一致）。"""
    store = Store(tmp_path)
    hub = Hub(store=store)

    await hub.emit("p1", {"type": "message", "agent_id": "a", "content": "1"})
    # stream_delta 是 NO_SEQ 事件吗？走 emit 会被盖 seq——这里直接塞 ring 模拟
    proj = hub.projects["p1"]
    proj.ring.append({"type": "stream_delta", "project_id": "p1", "seq": 2,
                    "agent_id": "a", "content": "delta"})

    events = hub.durable_since("p1", 0)
    assert [e["seq"] for e in events] == [1], "stream_delta 不应出现在增量里"


@pytest.mark.asyncio
async def test_durable_since_is_sorted_by_seq(tmp_path):
    store = Store(tmp_path)
    hub = Hub(store=store)

    for i in range(1, 4):
        await hub.emit("p1", {"type": "message", "agent_id": "a", "content": f"m{i}"})

    events = hub.durable_since("p1", 0)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
