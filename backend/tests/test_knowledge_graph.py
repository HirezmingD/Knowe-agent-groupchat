from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.handoff import HandoffBook
from backend.knowledge_graph import KnowledgeGraphManager


async def _no_aux(_system: str, _user: str) -> str:
    return ""


async def _broken_aux(_system: str, _user: str) -> str:
    return "```json\nnot valid json\n```"


class KnowledgeGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.internal = Path(self.tmp.name) / "internal" / "project_demo"
        self.book = HandoffBook(self.internal / "handoffs")
        self.phase = self.book.new_phase("01-认证")

    def manager(self, aux=_no_aux) -> KnowledgeGraphManager:
        return KnowledgeGraphManager(Path(self.tmp.name), aux_call=aux)

    async def test_instruction_approval_report_merge_into_one_topic(self) -> None:
        instruction = self.book.write_instruction(
            step=1,
            target="be_1",
            keyword="用户认证",
            phase_dir=self.phase,
            background="桌面端需要安全登录",
            task="实现 JWT 登录与刷新令牌轮换",
            acceptance="刷新失败后必须退出登录",
            notes="不要把 token 写入日志",
        )
        approval = self.book.write_approval(
            step=1,
            decision="approved",
            target="be_1",
            keyword="用户认证",
            phase_dir=self.phase,
            instruction_file=instruction.name,
            instruction_text="实现 JWT 登录与刷新令牌轮换",
        )
        report = self.book.write_report(
            step=1,
            agent_id="be_1",
            keyword="用户认证",
            phase_dir=self.phase,
            completed_what="完成 JWT 登录和刷新令牌轮换",
            matches_instruction="完全符合",
            artifacts=["backend/auth.py"],
            issues="旧客户端需要重新登录",
            self_check="测试通过",
        )
        self.book.link_report_into_approval(1, report.name, phase_dir=self.phase)

        manager = self.manager()
        await manager.ingest_handoff("project_demo", self.internal, instruction, "instruction")
        await manager.ingest_handoff("project_demo", self.internal, approval, "approval")
        await manager.ingest_handoff("project_demo", self.internal, report, "report")

        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        topics = [node for node in graph["nodes"].values() if node["title"] == "用户认证"]
        self.assertEqual(len(topics), 1)
        topic = topics[0]
        self.assertEqual(topic["metrics"]["source_count"], 3)
        # Wave 6: plan approval and a legacy report are not verified delivery.
        self.assertEqual(topic["metrics"]["approval_score"], 0.0)
        self.assertEqual(topic["metrics"]["positive_events"], 0)
        self.assertFalse(topic["metrics"]["promotion_ready"])
        self.assertTrue(any(edge["type"] == "produces" for edge in graph["edges"].values()))
        self.assertTrue(any(node["type"] == "risk" for node in graph["nodes"].values()))

        result = manager.search("project_demo", self.internal, "JWT 用户认证", limit=5)
        self.assertTrue(result["results"])
        self.assertEqual(result["results"][0]["title"], "用户认证")
        self.assertTrue((self.internal / "knowledge" / "graph.md").is_file())
        self.assertTrue(any((self.internal / "knowledge" / "nodes").glob("*.md")))

    async def test_single_rejection_is_bounded_and_idempotent(self) -> None:
        approval = self.book.write_approval(
            step=1,
            decision="rejected",
            target="fe_1",
            keyword="改用全量重写",
            phase_dir=self.phase,
            instruction_text="放弃增量修复，改为全量重写",
        )
        manager = self.manager()
        first = await manager.ingest_handoff(
            "project_demo", self.internal, approval, "approval", {"decision": "rejected"},
        )
        second = await manager.ingest_handoff(
            "project_demo", self.internal, approval, "approval", {"decision": "rejected"},
        )
        self.assertEqual(first["status"], "processed")
        self.assertEqual(second["status"], "skipped")

        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        self.assertEqual(len(graph["signals"]), 1)
        node = next(node for node in graph["nodes"].values() if node["title"] == "改用全量重写")
        score = node["metrics"]["approval_score"]
        self.assertLess(score, 0)
        self.assertGreater(score, -0.30, "一次拒绝不应把节点打成永久负分")

    async def test_historical_approval_uses_source_time_for_decay(self) -> None:
        approval = self.book.write_approval(
            step=1, decision="approved", target="be_1", keyword="旧架构方案",
            phase_dir=self.phase, instruction_text="沿用旧架构方案",
        )
        text = approval.read_text("utf-8").replace(
            "created: 2026-07-15", "created: 2020-01-01",
        )
        # The exact test run date may differ, so replace any generated ISO date defensively.
        import re
        text = re.sub(r"created: \d{4}-\d{2}-\d{2}", "created: 2020-01-01", text)
        approval.write_text(text, "utf-8")

        manager = self.manager()
        await manager.ingest_handoff("project_demo", self.internal, approval, "approval")
        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        node = next(node for node in graph["nodes"].values() if node["title"] == "旧架构方案")
        self.assertLess(abs(node["metrics"]["approval_score"]), 0.01)
        signal = next(iter(graph["signals"].values()))
        self.assertTrue(signal["at"].startswith("2020-01-01"))

    async def test_ingestion_rejects_files_outside_project_handoffs(self) -> None:
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text("# secret", "utf-8")
        result = await self.manager().ingest_handoff(
            "project_demo", self.internal, outside, "instruction",
        )
        self.assertEqual(result, {"status": "failed", "reason": "source_outside_handoffs"})
        self.assertFalse((self.internal / "knowledge" / ".graph.json").exists())

    async def test_semantically_empty_auxiliary_output_falls_back_to_rules(self) -> None:
        async def empty_aux(_system: str, _user: str) -> str:
            return json.dumps({"source_summary": "", "nodes": [{}], "relations": []})

        instruction = self.book.write_instruction(
            step=1, target="design_1", keyword="导航设计", phase_dir=self.phase,
            task="设计项目导航", acceptance="当前项目必须清晰可见",
        )
        result = await self.manager(aux=empty_aux).ingest_handoff(
            "project_demo", self.internal, instruction, "instruction",
        )
        self.assertEqual(result["method"], "rules")

    async def test_invalid_auxiliary_output_falls_back_to_rules(self) -> None:
        instruction = self.book.write_instruction(
            step=1,
            target="design_1",
            keyword="空状态设计",
            phase_dir=self.phase,
            task="设计首次进入时的空状态",
            acceptance="必须给出下一步动作",
        )
        manager = self.manager(aux=_broken_aux)
        result = await manager.ingest_handoff(
            "project_demo", self.internal, instruction, "instruction",
        )
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["method"], "rules")
        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        self.assertTrue(graph["nodes"])
        self.assertEqual(graph["revision"], 1)

    async def test_updated_approval_replaces_signal_and_reaches_report_nodes(self) -> None:
        instruction = self.book.write_instruction(
            step=1, target="be_1", keyword="访问令牌", phase_dir=self.phase,
            task="实现访问令牌轮换", acceptance="令牌泄露后可立即失效",
        )
        approval = self.book.write_approval(
            step=1, decision="approved", target="be_1", keyword="访问令牌",
            phase_dir=self.phase, instruction_file=instruction.name,
            instruction_text="实现访问令牌轮换",
        )
        manager = self.manager()
        await manager.ingest_handoff("project_demo", self.internal, instruction, "instruction")
        await manager.ingest_handoff("project_demo", self.internal, approval, "approval")

        report = self.book.write_report(
            step=1, agent_id="be_1", keyword="访问令牌", phase_dir=self.phase,
            completed_what="已实现令牌轮换", matches_instruction="完全符合",
            artifacts=["backend/token_rotation.py"], issues="旧令牌缓存可能延迟失效",
            self_check="测试通过",
        )
        self.book.link_report_into_approval(1, report.name, phase_dir=self.phase)
        await manager.ingest_handoff("project_demo", self.internal, report, "report")
        refreshed = await manager.ingest_handoff(
            "project_demo", self.internal, approval, "approval", {"trigger": "report_receipt"},
        )
        self.assertEqual(refreshed["status"], "processed")
        self.assertEqual(refreshed["method"], "receipt_refresh")

        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        self.assertEqual(len(graph["signals"]), 1, "审批文件更新不得重复累计信号")
        risk = next(node for node in graph["nodes"].values() if node["type"] == "risk")
        artifact = next(node for node in graph["nodes"].values() if node["type"] == "artifact")
        self.assertEqual(len(risk["signal_ids"]), 1)
        self.assertEqual(len(artifact["signal_ids"]), 1)

    async def test_report_rewrite_rebuilds_signals_and_prunes_old_risk(self) -> None:
        instruction = self.book.write_instruction(
            step=1, target="be_1", keyword="缓存策略", phase_dir=self.phase,
            task="实现缓存策略", acceptance="缓存必须可失效",
        )
        approval = self.book.write_approval(
            step=1, decision="approved", target="be_1", keyword="缓存策略",
            phase_dir=self.phase, instruction_file=instruction.name,
            instruction_text="实现缓存策略",
        )
        report = self.book.write_report(
            step=1, agent_id="be_1", keyword="缓存策略", phase_dir=self.phase,
            completed_what="实现缓存策略", matches_instruction="完全符合",
            issues="首次风险描述", self_check="通过",
        )
        self.book.link_report_into_approval(1, report.name, phase_dir=self.phase)
        manager = self.manager()
        for path, kind in ((instruction, "instruction"), (approval, "approval"), (report, "report")):
            await manager.ingest_handoff("project_demo", self.internal, path, kind)

        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        old_risk_id = next(
            node["id"] for node in graph["nodes"].values()
            if node["type"] == "risk" and "首次风险描述" in node["summary"]
        )

        # Same report path is overwritten; approval backlink text is unchanged.
        report = self.book.write_report(
            step=1, agent_id="be_1", keyword="缓存策略", phase_dir=self.phase,
            completed_what="调整缓存策略", matches_instruction="完全符合",
            issues="新的失效竞态风险", self_check="通过",
        )
        await manager.ingest_handoff("project_demo", self.internal, report, "report")
        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        self.assertNotIn(old_risk_id, graph["nodes"], "被改写来源的旧孤儿风险应被清理")
        new_risk = next(
            node for node in graph["nodes"].values()
            if node["type"] == "risk" and "新的失效竞态风险" in node["summary"]
        )
        self.assertEqual(len(new_risk["signal_ids"]), 1)
        self.assertEqual(len(graph["signals"]), 1)

    async def test_bootstrap_is_idempotent(self) -> None:
        instruction = self.book.write_instruction(
            step=1, target="qa_1", keyword="回归测试", phase_dir=self.phase,
            task="补充登录回归测试", acceptance="覆盖错误密码与令牌过期",
        )
        approval = self.book.write_approval(
            step=1, decision="approved", target="qa_1", keyword="回归测试",
            phase_dir=self.phase, instruction_file=instruction.name,
            instruction_text="补充登录回归测试",
        )
        report = self.book.write_report(
            step=1, agent_id="qa_1", keyword="回归测试", phase_dir=self.phase,
            completed_what="已补充登录回归测试", matches_instruction="完全符合",
            issues="令牌过期边界仍需观察", self_check="测试通过",
        )
        self.book.link_report_into_approval(1, report.name, phase_dir=self.phase)

        manager = self.manager()
        first = await manager.bootstrap_project("project_demo", self.internal)
        second = await manager.bootstrap_project("project_demo", self.internal)
        self.assertEqual(first["processed"], 3)
        self.assertEqual(second["skipped"], 3)
        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        self.assertEqual(len(graph["sources"]), 3)
        self.assertEqual(len(graph["signals"]), 1)
        risk = next(node for node in graph["nodes"].values() if node["type"] == "risk")
        self.assertEqual(len(risk["signal_ids"]), 1, "历史补录应与实时链路得到相同最终信号覆盖")

    async def test_explicit_llm_contradiction_marks_nodes_contested(self) -> None:
        instruction = self.book.write_instruction(
            step=1, target="be_1", keyword="令牌存储", phase_dir=self.phase,
            task="把令牌放入系统钥匙串", acceptance="不得明文落盘",
        )
        manager = self.manager()
        await manager.ingest_handoff("project_demo", self.internal, instruction, "instruction")
        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        existing = next(node for node in graph["nodes"].values() if node["title"] == "令牌存储")

        async def contradiction_aux(_system: str, _user: str) -> str:
            return json.dumps({
                "source_summary": "提出把令牌明文写入配置文件",
                "nodes": [{
                    "key": "n1", "match_id": "", "type": "decision",
                    "title": "明文令牌配置", "summary": "把令牌明文写入配置文件",
                    "aliases": [], "keywords": ["令牌", "配置文件"], "confidence": 0.92,
                }],
                "relations": [],
                "contradictions": [{
                    "new": "n1", "existing": existing["id"],
                    "summary": "是否允许令牌明文落盘相互冲突",
                    "severity": 0.95, "supersedes": False,
                }],
            }, ensure_ascii=False)

        manager._aux_call = contradiction_aux
        second = self.book.write_instruction(
            step=2, target="be_2", keyword="明文令牌配置", phase_dir=self.phase,
            task="把令牌写入配置文件", acceptance="便于人工编辑",
        )
        result = await manager.ingest_handoff("project_demo", self.internal, second, "instruction")
        self.assertEqual(result["method"], "llm")
        graph = json.loads((self.internal / "knowledge" / ".graph.json").read_text("utf-8"))
        self.assertTrue(any(edge["type"] == "contradicts" for edge in graph["edges"].values()))
        self.assertEqual(graph["nodes"][existing["id"]]["status"], "contested")
        new_node = next(node for node in graph["nodes"].values() if node["title"] == "明文令牌配置")
        self.assertEqual(new_node["status"], "contested")
        self.assertFalse(new_node["metrics"]["promotion_ready"])

    async def test_three_plan_approvals_alone_do_not_create_harness_candidate(self) -> None:
        manager = self.manager()
        for step in range(1, 4):
            instruction = self.book.write_instruction(
                step=step,
                target="be_1",
                keyword="审计日志不可丢失",
                phase_dir=self.phase,
                task="保留完整审计日志",
                acceptance="重启后仍可追溯",
            )
            approval = self.book.write_approval(
                step=step,
                decision="approved",
                target="be_1",
                keyword="审计日志不可丢失",
                phase_dir=self.phase,
                instruction_file=instruction.name,
                instruction_text="保留完整审计日志",
            )
            await manager.ingest_handoff(
                "project_demo", self.internal, instruction, "instruction",
            )
            await manager.ingest_handoff(
                "project_demo", self.internal, approval, "approval",
            )

        # Wave 6 deliberately separates plan approval from verified delivery.
        # Repeated approval of the work direction must not promote an undelivered
        # topic into a Harness knowledge candidate.
        candidates = manager.export_harness_candidates("project_demo", self.internal)
        self.assertEqual(candidates, [])
        export_path = self.internal / "knowledge" / "export" / "harness_candidates.jsonl"
        self.assertTrue(export_path.is_file())
        self.assertEqual(export_path.read_text("utf-8"), "")


if __name__ == "__main__":
    unittest.main()
