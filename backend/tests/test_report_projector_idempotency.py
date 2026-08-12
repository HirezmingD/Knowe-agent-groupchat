"""
[v1.0.24.3] CompletionProjector 幂等链路（INT 审计副本承载 completion_id）。

覆盖：
  · _existing_report 扫 audit/ INT（completion_id 命中）→ 重放不重写
  · 同名 EXT 已存在但 completion_id 不同 → keyword 加 -c 后缀（不覆盖旧审计）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.handoff import HandoffBook


class _FakeEngine:
    """最小 engine 桩：只提供 CompletionProjector._existing_report 需要的面。"""

    def __init__(self, book: HandoffBook) -> None:
        self.book = book
        self.handoff = book

    @property
    def internal_workspace(self) -> Path:
        return self.book.root.parent


class ProjectorIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.book = HandoffBook(Path(self.tmp.name) / "handoffs")
        self.phase = self.book.new_phase("01-起步")
        self.engine = _FakeEngine(self.book)

    def _write(self, completion_id: str, keyword: str = "用户认证") -> Path:
        return self.book.write_report(
            step=1,
            agent_id="be_1",
            keyword=keyword,
            phase_dir=self.phase,
            status="SUCCEEDED",
            report_hash="h" + completion_id[-8:],
            completed_what="完成 JWT",
            matches_instruction="待总管审阅",
            artifacts=["backend/auth.py"],
            issues="（无）",
            self_check="测试通过",
            task_id="task_1",
            run_id="run_1",
            delivery_id="delivery_1",
            completion_id=completion_id,
            status_reason="completed",
            provenance={"status": "recorded"},
        )

    def test_existing_report_finds_int_not_ext(self) -> None:
        from knowe_harness.projections import CompletionProjector

        self._write("cmp_first1111")
        # 构造一个只有 projector 的实例（store 置 None 即可，_existing_report 不碰 store）
        projector = CompletionProjector(store=None, engine=self.engine)  # type: ignore[arg-type]
        found = projector._existing_report("cmp_first1111")
        self.assertIsNotNone(found)
        self.assertIn("report-INT", found.name, "幂等判定必须命中 audit/ INT 副本")
        # EXT 已无 completion_id → 扫 EXT 必然漏判；扫 INT 才命中
        self.assertNotIn("completion_id", self.book.reports()[0].read_text("utf-8"))

    def test_existing_report_misses_unknown_completion(self) -> None:
        from knowe_harness.projections import CompletionProjector

        self._write("cmp_first1111")
        projector = CompletionProjector(store=None, engine=self.engine)  # type: ignore[arg-type]
        self.assertIsNone(projector._existing_report("cmp_other2222"))


if __name__ == "__main__":
    unittest.main()
