"""[v1.0.24.4] 权威活动账本单元测试。

记账/销账只挂在 engine.emit 唯一出站口，这里直接驱动 emit 验证配对，
另测 purge 与 stop 清账——覆盖设计稿 §八「每个发射点进出配对」的等价面。
"""

import tempfile
import unittest
from pathlib import Path

from backend.engine import ProjectEngine
from backend.hub import Hub


class ActivityLedgerTests(unittest.IsolatedAsyncioTestCase):
    """账本记账/销账配对（engine.emit 出站口驱动）。"""

    def _make_engine(self, project_id: str = "p1") -> ProjectEngine:
        return ProjectEngine(
            Hub(), project_id, agent=None,
            workspace_root=Path(tempfile.mkdtemp(prefix="knowe-ledger-")),
        )

    async def test_active_records_and_idle_releases(self) -> None:
        engine = self._make_engine()
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "reason": "coordinator_turn"}
        )
        snap = engine.open_activity_snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["agent_id"], "pm_1")
        self.assertEqual(snap[0]["channel_id"], "p1")
        self.assertIsInstance(snap[0]["started_at"], int)

        await engine.emit(
            {"type": "agent_idle", "agent_id": "pm_1", "status": "AVAILABLE"}
        )
        self.assertEqual(engine.open_activity_snapshot(), [])

    async def test_scope_keyed_entries_are_independent(self) -> None:
        """同成员两个 scope 并行 → 销一个 scope 不牵连另一个（键同构的粒度）。"""
        engine = self._make_engine()
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "scope_id": "s_a", "reason": "t"}
        )
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "scope_id": "s_b", "reason": "t"}
        )
        snap = engine.open_activity_snapshot()
        self.assertEqual(len(snap), 2)
        self.assertEqual({e["scope_id"] for e in snap}, {"s_a", "s_b"})

        await engine.emit(
            {"type": "agent_idle", "agent_id": "pm_1", "scope_id": "s_a", "status": "AVAILABLE"}
        )
        snap = engine.open_activity_snapshot()
        self.assertEqual([e["scope_id"] for e in snap], ["s_b"])

    async def test_same_scope_double_active_is_idempotent(self) -> None:
        """同一键重复记账 → 只有一条（dict 键覆盖），started_at 不后退。"""
        engine = self._make_engine()
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "scope_id": "s_a", "reason": "t"}
        )
        first = engine.open_activity_snapshot()[0]["started_at"]
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "scope_id": "s_a", "reason": "t"}
        )
        snap = engine.open_activity_snapshot()
        self.assertEqual(len(snap), 1)
        self.assertGreaterEqual(snap[0]["started_at"], first)

    async def test_purge_removes_all_entries_of_member(self) -> None:
        """成员移除/清退时 purge：只清该成员，不牵连他人。"""
        engine = self._make_engine()
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "reason": "t"}
        )
        await engine.emit(
            {"type": "agent_active", "agent_id": "fe_1", "reason": "t"}
        )
        engine._purge_open_activity("pm_1")
        snap = engine.open_activity_snapshot()
        self.assertEqual([e["agent_id"] for e in snap], ["fe_1"])

    async def test_ledger_empty_when_no_activity(self) -> None:
        engine = self._make_engine()
        self.assertEqual(engine.open_activity_snapshot(), [])

    async def test_stop_clears_ledger(self) -> None:
        """引擎收摊 = 现场无任何进行中的事 → 账本清空（前端据此全员空闲）。"""
        engine = self._make_engine()
        await engine.emit(
            {"type": "agent_active", "agent_id": "pm_1", "reason": "t"}
        )
        await engine.stop(immediate=True)
        self.assertEqual(engine.open_activity_snapshot(), [])

    async def test_dm_channel_entries_carry_dm_channel_id(self) -> None:
        """私聊频道的 active 记在 dm:* 频道键下——全量下发后按 channel 过滤自取。"""
        engine = self._make_engine()
        await engine.emit(
            {"type": "agent_active", "agent_id": "fe_1", "reason": "dm_turn"},
            channel="dm:p1:fe_1",
        )
        snap = engine.open_activity_snapshot()
        self.assertEqual(snap[0]["channel_id"], "dm:p1:fe_1")


if __name__ == "__main__":
    unittest.main()
