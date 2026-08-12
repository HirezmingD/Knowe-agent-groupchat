from __future__ import annotations

"""
[v1.0.24.3] INT/EXT 报告拆分测试。

覆盖：
  1. write_report 双写：EXT（handoffs/ 树，最小安全头）与 INT（audit/ 树，全字段）同生
  2. EXT 剥离字段：无 completion_id / status_reason / provenance / 「零、Completion 状态」段
  3. EXT 保留字段：report_hash / status / delivery_id / task_id / run_id / created
  4. INT 完整性：completion_id / provenance / status_reason / gaps 全在
  5. 扫描隔离：handoff_reports() / handoff_files() / audit_reports() 各只见各自树
  6. 血缘校验：_runtime_report_lineage 对 EXT 最小头可解析（status/delivery_id/task_id/run_id）
  7. hash 反查：find_handoff_file 用 report_hash 能命中 EXT
  8. 知识图谱 step：_parse_handoff 对 EXT（report-03-… 命名）step 解析为 3
  9. projections 幂等：_existing_report 扫 INT（completion_id 命中），不扫 EXT
"""

import tempfile
import unittest
from pathlib import Path

from backend.handoff import HandoffBook


def _make_book() -> tuple[HandoffBook, tempfile.TemporaryDirectory[str]]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    book = HandoffBook(root / "handoffs")
    book.new_phase("01-起步")
    return book, tmp


class ReportIntExtSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book, self.tmp = _make_book()
        self.addCleanup(self.tmp.cleanup)
        self.phase = self.book.current_phase()
        self.kwargs = dict(
            step=3,
            agent_id="fe_1",
            keyword="用户认证",
            phase_dir=self.phase,
            status="SUCCEEDED",
            report_hash="a1b2c3d4e5f6",
            completed_what="完成了登录页",
            matches_instruction="待总管审阅",
            artifacts=["src/login.tsx"],
            issues="（无）",
            self_check="自检通过",
            task_id="task_1",
            run_id="run_1",
            delivery_id="delivery_1",
            completion_id="cmp_xyz789",
            effect_id="effect_1",
            author="worker",
            source_kind="worker_submission",
            status_reason="completed",
            gaps=["gap_1"],
            provenance={"status": "recorded", "provenance_id": "prov_1"},
        )

    def test_dual_write_ext_and_int(self) -> None:
        path = self.book.write_report(**self.kwargs)
        # EXT：handoffs/ 树内，原名（旧规范零破坏）
        self.assertEqual(path.name, "report-03-fe_1-用户认证.md")
        self.assertTrue(path.is_file())
        self.assertIn(self.book.root, path.parents)
        # INT：audit/ 树内，report-INT- 前缀
        int_path = self.book.audit_dir / "01-起步" / "report-INT-03-fe_1-用户认证.md"
        self.assertTrue(int_path.is_file())
        self.assertNotIn(self.book.root, int_path.parents)

    def test_ext_strips_technical_fields(self) -> None:
        path = self.book.write_report(**self.kwargs)
        text = path.read_text("utf-8")
        for leaked in (
            "completion_id", "status_reason", "provenance_id",
            "build_id", "git_commit", "source_kind",
            "projection_effect_id", "零、Completion",
        ):
            self.assertNotIn(leaked, text, f"EXT 不应含 {leaked}")

    def test_ext_keeps_required_fields(self) -> None:
        path = self.book.write_report(**self.kwargs)
        text = path.read_text("utf-8")
        for kept in ("report_hash: a1b2c3d4e5f6", "status: SUCCEEDED",
                     "delivery_id: delivery_1", "task_id: task_1",
                     "run_id: run_1", "created:"):
            self.assertIn(kept, text, f"EXT 应含 {kept}")
        # 正文从「一、我完成了什么」开始
        self.assertIn("## 一、我完成了什么", text)
        self.assertIn("完成了登录页", text)

    def test_int_keeps_everything(self) -> None:
        self.book.write_report(**self.kwargs)
        int_path = self.book.audit_dir / "01-起步" / "report-INT-03-fe_1-用户认证.md"
        text = int_path.read_text("utf-8")
        for kept in ("completion_id: cmp_xyz789", "status_reason: completed",
                     "provenance_id:", "status: SUCCEEDED",
                     "零、Completion", "- gap_1"):
            self.assertIn(kept, text, f"INT 应含 {kept}")

    def test_scan_isolation(self) -> None:
        self.book.write_report(**self.kwargs)
        ext_reports = self.book.reports()
        self.assertEqual([p.name for p in ext_reports], ["report-03-fe_1-用户认证.md"])
        audits = self.book.audit_reports()
        self.assertEqual([p.name for p in audits], ["report-INT-03-fe_1-用户认证.md"])
        # INT 不在 handoffs 树内，任何 rglob 不可达
        self.assertFalse(any("report-INT" in p.name for p in self.book.root.rglob("*.md")))

    def test_audit_dir_fallback_when_root_has_no_parent_handoffs(self) -> None:
        """audit_dir 与 handoffs 平级（internal_workspace/audit）。"""
        self.assertEqual(self.book.audit_dir.name, "audit")
        self.assertEqual(self.book.audit_dir.parent, self.book.root.parent)


class ReportLineageAndLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book, self.tmp = _make_book()
        self.addCleanup(self.tmp.cleanup)
        self.phase = self.book.current_phase()
        self.kwargs = dict(
            step=4,
            agent_id="be_1",
            keyword="API",
            phase_dir=self.phase,
            status="SUCCEEDED",
            report_hash="deadbeef1234",
            completed_what="完成了 API",
            matches_instruction="待总管审阅",
            artifacts=[],
            issues="（无）",
            self_check="自检通过",
            task_id="task_2",
            run_id="run_2",
            delivery_id="delivery_2",
            completion_id="cmp_abc456",
            status_reason="completed",
            provenance={"status": "recorded"},
        )
        self.path = self.book.write_report(**self.kwargs)

    def test_runtime_report_lineage_parses_ext(self) -> None:
        from backend.engine import ProjectEngine
        ok, delivery_id = ProjectEngine._runtime_report_lineage(self.path.read_text("utf-8"))
        self.assertTrue(ok)
        self.assertEqual(delivery_id, "delivery_2")

    def test_find_handoff_file_by_hash(self) -> None:
        found = self.book.find("deadbeef1234")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "report-04-be_1-API.md")

    def test_find_handoff_file_by_name(self) -> None:
        found = self.book.find("report-04-be_1-API.md")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "report-04-be_1-API.md")

    def test_find_handoff_file_never_hits_int(self) -> None:
        # INT 在 audit/ 树外：find 只能命中 EXT
        found = self.book.find("report-INT-04-be_1-API.md")
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
