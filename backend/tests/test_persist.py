"""
test_persist.py — 落盘。

要证明的不是「文件写出去了」，是**「关了再开，东西真的还在，而且 seq 接得上」**。
seq 接不上，前端会把新事件当成重复的丢掉，界面就再也不动了——
所以那条测试是这一组里最要紧的一条。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.config import CONFIG
from backend.hub import Hub
from backend.persist import Store
from backend.server import KnoweServer


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


# ═══════════════════════════════════════════════════════════════
# 项目注册表
# ═══════════════════════════════════════════════════════════════

def test_project_registry_roundtrip(store: Store):
    store.upsert_project("p1", "官网改版")
    store.upsert_project("p2", "内部工具")

    reopened = Store(store.root)          # 假装重启
    rows = reopened.load_projects()

    assert [r["project_id"] for r in rows] == ["p1", "p2"]
    assert rows[0]["name"] == "官网改版"
    assert rows[0]["created_at"]          # 建档时间要有


def test_upsert_updates_name_but_keeps_created_at(store: Store):
    store.upsert_project("p1", "旧名字")
    born = store.load_projects()[0]["created_at"]

    store.upsert_project("p1", "新名字")
    rows = store.load_projects()

    assert len(rows) == 1                 # 不许重复建档
    assert rows[0]["name"] == "新名字"
    assert rows[0]["created_at"] == born  # created_at 是历史，不是状态，不该被改


def test_write_is_atomic_no_tmp_files_left(store: Store):
    store.upsert_project("p1", "官网")
    leftovers = [p.name for p in store.root.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "临时文件必须被 os.replace 换走，不能留在目录里"


def test_corrupt_registry_does_not_block_startup(store: Store):
    """★ projects.json 烂了 → 当空的用，不许把服务卡在启动阶段。"""
    store.registry_path.write_text("{ 这不是 JSON", encoding="utf-8")

    assert store.load_projects() == []
    store.upsert_project("p1", "还能建新项目")     # 而且还能继续用
    assert len(store.load_projects()) == 1


# ═══════════════════════════════════════════════════════════════
# 事件流水
# ═══════════════════════════════════════════════════════════════

def test_events_append_and_load(store: Store):
    for i in range(1, 4):
        store.append_event("p1", {"type": "message", "seq": i, "content": str(i)})

    events = store.load_events("p1", limit=100)
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_no_seq_events_are_not_persisted(store: Store):
    """无 seq 的旁路事件（pong / project_created）不进 ring，也不该进盘。"""
    store.append_event("p1", {"type": "pong"})
    assert store.load_events("p1", limit=10) == []


def test_broken_last_line_is_skipped_not_fatal(store: Store):
    """★ 写到一半断电 → 最后一行是残的。跳过它，别让整个项目开不了。"""
    store.append_event("p1", {"type": "message", "seq": 1, "content": "好的"})
    with open(store.events_path("p1"), "a", encoding="utf-8") as f:
        f.write('{"type": "message", "seq": 2, "cont')   # 断电了

    events = store.load_events("p1", limit=100)
    assert [e["seq"] for e in events] == [1]             # 好的那条还在


def test_load_returns_only_the_most_recent_n(store: Store):
    for i in range(1, 21):
        store.append_event("p1", {"type": "message", "seq": i})

    events = store.load_events("p1", limit=5)
    assert [e["seq"] for e in events] == [16, 17, 18, 19, 20]


def test_compact_shrinks_the_file(store: Store):
    for i in range(1, 21):
        store.append_event("p1", {"type": "message", "seq": i})

    keep = store.load_events("p1", limit=5)
    store.compact("p1", keep)

    lines = store.events_path("p1").read_text("utf-8").strip().split("\n")
    assert len(lines) == 5                              # 文件真的瘦下来了
    assert json.loads(lines[0])["seq"] == 16


def test_weird_project_id_cannot_escape_the_data_dir(store: Store):
    """★ project_id 是用户可控的字符串——不能让它变成 ../../etc/passwd。"""
    store.append_event("../../etc/passwd", {"type": "message", "seq": 1})

    written = list(store.events_dir.iterdir())
    assert len(written) == 1
    assert written[0].parent == store.events_dir        # 老老实实待在数据目录里
    assert ".." not in written[0].name


# ═══════════════════════════════════════════════════════════════
# 温载：hub 层
# ═══════════════════════════════════════════════════════════════

async def test_hub_restore_continues_the_seq():
    """★ 最要紧的一条：重启后 seq 必须接着往下走，不能从 1 重新数。"""
    hub = Hub()
    old = [
        {"type": "message", "seq": 1, "project_id": "p1", "ts": "x", "agent_id": "a", "content": "1"},
        {"type": "message", "seq": 2, "project_id": "p1", "ts": "x", "agent_id": "a", "content": "2"},
    ]
    hub.restore("p1", "官网", old)

    assert hub.projects["p1"].seq == 2
    assert len(hub.projects["p1"].ring) == 2

    ev = await hub.emit("p1", {"type": "message", "agent_id": "a", "content": "3"})
    assert ev["seq"] == 3, "重启后的第一条事件必须是 3，不是 1（否则前端会当成重复丢掉）"


async def test_hub_emit_writes_through_to_disk(tmp_path: Path):
    store = Store(tmp_path)
    hub = Hub(store=store)

    await hub.emit("p1", {"type": "message", "agent_id": "a", "content": "落盘"})

    # emit() commits to the FIFO persistence queue without blocking the event
    # loop on disk I/O.  Readers that need a durable boundary must flush first.
    assert store.flush()
    events = store.load_events("p1", limit=10)
    assert len(events) == 1
    assert events[0]["content"] == "落盘"
    assert events[0]["seq"] == 1


async def test_hub_without_store_is_pure_memory(tmp_path: Path):
    """不给 store → 行为和 v0.3 一模一样（不写任何文件）。"""
    hub = Hub()
    await hub.emit("p1", {"type": "message", "agent_id": "a", "content": "x"})
    assert list(tmp_path.iterdir()) == []


# ═══════════════════════════════════════════════════════════════
# 端到端：关了再开
# ═══════════════════════════════════════════════════════════════

async def test_restart_keeps_projects_and_history(tmp_path: Path):
    """★ 这就是这一整个功能存在的理由：关掉进程，再开，东西还在。"""
    # ── 第一次启动：建项目、说几句话 ──
    s1 = KnoweServer(data_dir=str(tmp_path))
    s1.store.upsert_project("p1", "官网改版")     # type: ignore[union-attr]
    await s1.hub.emit("p1", {"type": "user_echo", "content": "做个官网", "client_msg_id": "cm_1"})
    await s1.hub.emit("p1", {"type": "message", "agent_id": "coordinator", "content": "好的"})
    assert s1.hub.projects["p1"].seq == 2

    # ── 关掉，重开 ──
    # [v1.0.24.4] 磁盘写入改走异步队列，持久化在关机时定稿（生产关机路径
    # 同样 flush+close）。模拟「关掉进程」就得真的执行定稿——这是新契约下
    # 「关掉再开东西还在」成立的前提。
    s1.store.flush()
    s2 = KnoweServer(data_dir=str(tmp_path))
    s2.load_from_disk()

    assert "p1" in s2.hub.projects
    assert s2.hub.projects["p1"].name == "官网改版"          # ★ 项目还在
    assert s2.hub.projects["p1"].seq == 2                    # ★ seq 接上了

    ring = s2.hub.projects["p1"].ring.events()
    assert [e["type"] for e in ring] == ["user_echo", "message"]   # ★ 历史还在
    assert ring[0]["content"] == "做个官网"

    # ── 重启后新说的话，seq 从 3 开始 ──
    ev = await s2.hub.emit("p1", {"type": "message", "agent_id": "coordinator", "content": "继续"})
    assert ev["seq"] == 3


async def test_load_preserves_full_structural_history_and_seq(tmp_path: Path):
    """Warm load must not truncate durable project history to ring_capacity.

    The pre-v0.12 expectation compacted the event log to the in-memory ring size
    and could permanently delete older chat records.  The deployed contract loads
    all structural history, expands the restored ring to fit it, and preserves the
    persisted sequence watermark.
    """
    object.__setattr__(CONFIG, "ring_capacity", 5)
    s1 = s2 = None
    try:
        s1 = KnoweServer(data_dir=str(tmp_path))
        s1.store.upsert_project("p1", "官网")      # type: ignore[union-attr]
        for i in range(20):
            await s1.hub.emit("p1", {"type": "message", "agent_id": "a", "content": str(i)})

        # [v1.0.24.4] 写入走异步队列；模拟重启前先定稿（等同关机 flush）。
        s1.store.flush()
        s2 = KnoweServer(data_dir=str(tmp_path))
        s2.load_from_disk()

        lines = s2.store.events_path("p1").read_text("utf-8").strip().split("\n")  # type: ignore[union-attr]
        assert len(lines) == 20, "结构化聊天历史不得因 ring 配置而被截断"
        assert [event["content"] for event in s2.hub.projects["p1"].ring.events()] == [str(i) for i in range(20)]
        assert s2.hub.projects["p1"].seq == 20, "重启后的 seq 必须继续原水位"
    finally:
        for server in (s1, s2):
            if server is None:
                continue
            task = getattr(server, "_harness_refresh_task", None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
        object.__setattr__(CONFIG, "ring_capacity", 1000)


async def test_platform_conversation_is_not_a_project(tmp_path: Path):
    """★ 知知住的 __platform__ 不是项目——它绝不能出现在 projects.json 里。"""
    s = KnoweServer(data_dir=str(tmp_path))
    s.start_platform()

    assert "__platform__" in s.hub.projects            # 它是个会话（有 seq、有 ring）
    assert s.store.load_projects() == []               # type: ignore[union-attr]
    #                                                     但不是项目——左栏不该多出个假项目

    await asyncio.sleep(0)
    await s.platform.stop()                            # type: ignore[union-attr]
