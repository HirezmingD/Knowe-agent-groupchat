"""
[v0.12 D] 问题二 / 三 / 四 的回归测试 —— 纯 persist/hub 层，不依赖 knowe_core。
"""
import sys, os, tempfile, shutil
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.persist import Store, PERSISTABLE_TYPES
from backend.hub import Hub
from backend.config import CONFIG


def _mk():
    d = tempfile.mkdtemp()
    return Store(d), d, "p_test"


# ── 问题四：逐字增量不落盘，只留聊天记录 ──────────────────────────
def test_stream_delta_not_persisted():
    s, d, pid = _mk()
    seq = 0
    def ev(t, **kw):
        nonlocal seq; seq += 1
        return {"type": t, "seq": seq, "project_id": pid, "ts": "t", **kw}
    s.append_event(pid, ev("user_echo", content="你好"))
    for _ in range(40):
        s.append_event(pid, ev("stream_delta", agent_id="coordinator", content="字"))
    for _ in range(5):
        s.append_event(pid, ev("agent_thinking", agent_id="coordinator"))
    s.append_event(pid, ev("tool_start", agent_id="coordinator"))
    s.append_event(pid, ev("message", agent_id="coordinator", content="你好，我是总管"))
    on_disk = s.load_all_events(pid)
    # 磁盘上只剩 user_echo + message 两条结构事件
    assert len(on_disk) == 2, f"应只落盘 2 条聊天记录，实际 {len(on_disk)}"
    assert {e["type"] for e in on_disk} == {"user_echo", "message"}
    shutil.rmtree(d)


# ── 问题二：温载读全量，绝不截断，一条聊天记录都不丢 ─────────────
def test_no_truncation_on_reload():
    s, d, pid = _mk()
    # 落盘 1500 条聊天记录（远超 ring_capacity=1000）
    for i in range(1500):
        s.append_event(pid, {"type": "message", "seq": i + 1, "project_id": pid,
                             "ts": "t", "agent_id": "coordinator", "content": f"第{i+1}句"})
    # 模拟启动温载：读全量 → compact 全量（新逻辑）
    events = s.load_all_events(pid)
    assert len(events) == 1500, "load_all_events 必须读回全部"
    s.compact(pid, events)                       # 全量重写：不丢
    after = s.load_all_events(pid)
    assert len(after) == 1500, f"重启后应还是 1500 条，实际 {len(after)}（问题二回归！）"
    # 第一条和最后一条都在
    assert after[0]["content"] == "第1句"
    assert after[-1]["content"] == "第1500句"
    shutil.rmtree(d)


# ── 问题二：ring 温载后能装下全部历史，前端能 replay since 0 无 gap ──
def test_ring_holds_full_history_after_restore():
    s, d, pid = _mk()
    n = 2500
    for i in range(n):
        s.append_event(pid, {"type": "message", "seq": i + 1, "project_id": pid,
                             "ts": "t", "agent_id": "coordinator", "content": f"m{i+1}"})
    hub = Hub(store=s)
    events = s.load_all_events(pid)
    proj = hub.restore(pid, "测试项目", events)
    # 从头回放：不能有 gap（否则前端只能看到最近一屏）
    replayed, gap = proj.ring.replay_since(0)
    assert gap is False, "ring 装不下全部历史 → replay 报 gap → 用户看不到前面的聊天（问题二）"
    assert len(replayed) == n, f"应回放全部 {n} 条，实际 {len(replayed)}"
    assert proj.seq == n
    shutil.rmtree(d)


# ── 问题四：老文件（含逐字增量）温载后被瘦身，聊天记录不丢 ─────────
def test_legacy_file_debloat_keeps_history():
    # server._history_only 只是按 PERSISTABLE_TYPES 过滤；在隔离环境里 server.py 会连带
    # import 缺失的 agent 模块，故这里直接用等价过滤（逻辑与 server._history_only 一致）。
    def _history_only(events):
        return [e for e in events if e.get("type") in PERSISTABLE_TYPES]
    s, d, pid = _mk()
    # 直接手写一个「老文件」：混着 message 和一堆 stream_delta
    path = s.events_path(pid)
    lines = []
    seq = 0
    import json
    for i in range(100):
        seq += 1
        lines.append(json.dumps({"type": "message", "seq": seq, "project_id": pid,
                                 "ts": "t", "agent_id": "c", "content": f"话{i}"}))
        for _ in range(30):   # 每句话 30 条逐字增量（老文件的样子）
            seq += 1
            lines.append(json.dumps({"type": "stream_delta", "seq": seq,
                                     "project_id": pid, "ts": "t", "agent_id": "c", "content": "x"}))
    path.write_text("\n".join(lines) + "\n", "utf-8")
    big = path.stat().st_size
    # 温载：读全量 → 只留结构事件 → compact
    events = _history_only(s.load_all_events(pid))
    s.compact(pid, events)
    small = path.stat().st_size
    after = s.load_all_events(pid)
    assert len(after) == 100, f"100 条聊天记录必须全留下，实际 {len(after)}"
    assert all(e["type"] == "message" for e in after)
    assert small < big / 10, f"瘦身后应小很多：{big} → {small}"
    shutil.rmtree(d)


# ── PERSISTABLE_TYPES 覆盖所有聊天记录类型 ────────────────────────
def test_persistable_types_cover_chat():
    for t in ("message", "user_echo", "approval_card", "approval_resolved",
              "agents_created", "agent_removed", "instruction_injected",
              "report_submitted", "error", "recovery_notice"):
        assert t in PERSISTABLE_TYPES, f"{t} 是聊天记录，必须落盘"
    for t in ("stream_delta", "agent_thinking", "tool_start", "tool_complete",
              "tool_gen", "stream_reset", "state_snapshot"):
        assert t not in PERSISTABLE_TYPES, f"{t} 是瞬时事件，不该落盘"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
