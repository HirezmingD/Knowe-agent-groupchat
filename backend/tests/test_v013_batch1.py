"""v0.13 batch1 回归：持久回放、seq 水位、硬脱敏、停机审批落定。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest

from backend.gate import ApprovalCancelled, Gate
from backend.hub import Hub
from backend.persist import Store
from backend.privacy import sanitize_event, sanitize_text
from backend.ring import RingBuffer


class PrivacyGuardTests(unittest.TestCase):
    def test_agent_ids_internal_paths_and_control_ids_are_hidden(self) -> None:
        text = (
            "项目 p_12345678 的审批 ap_abcdef123："
            "Hickory（pm_1）请看 handoffs/03-后端/report-03-pm_1-验收.md"
        )
        safe = sanitize_text(text, {"pm_1": "Hickory"})
        self.assertNotIn("pm_1", safe)
        self.assertNotIn("handoffs/", safe)
        self.assertNotIn("p_12345678", safe)
        self.assertNotIn("ap_abcdef123", safe)
        self.assertIn("Hickory", safe)

        absolute = sanitize_text(
            r"请看 D:\Projects\demo\handoffs\03-后端\report-03-pm_1-验收.md",
            {"pm_1": "Hickory"},
        )
        self.assertEqual(absolute, "请看 内部交接文件")
        self.assertEqual(sanitize_text("coordinator 请通知 zinnia"), "项目经理 请通知 知知")

    def test_snapshot_pending_card_is_filtered(self) -> None:
        event = sanitize_event(
            {
                "type": "state_snapshot",
                "conversation": [{"type": "message", "content": "pm_1 完成了"}],
                "pending_card": {"instruction": "让 pm_1 读 handoffs/a.md"},
            },
            {"pm_1": "Hickory"},
        )
        self.assertNotIn("pm_1", event["conversation"][0]["content"])
        self.assertNotIn("handoffs", event["pending_card"]["instruction"])


class DurableReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_disk_fills_ring_gap_and_seq_watermark_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Store(td)
            hub = Hub(store=store)
            hub.set_public_text_filter(
                "p_1", lambda event: sanitize_event(event, {"pm_1": "Hickory"})
            )
            project = hub.get_or_create("p_1", "测试")
            project.ring = RingBuffer(2)

            await hub.emit(
                "p_1",
                {
                    "type": "message",
                    "agent_id": "coordinator",
                    "content": "pm_1 看 handoffs/01/report-01-pm_1-x.md",
                },
            )
            await hub.emit(
                "p_1",
                {
                    "type": "approval_card",
                    "agent_id": "coordinator",
                    "tool": "propose_next",
                    "card_id": "ap_test",
                    "card": {
                        "status": "pending_approval",
                        "expires_at": "2099-01-01T00:00:00Z",
                        "approval_id": "ap_test",
                        "target_id": "pm_1",
                        "instruction": "看 handoffs/x.md",
                    },
                },
            )
            await hub.emit("p_1", {"type": "agent_thinking", "agent_id": "coordinator"})
            await hub.emit("p_1", {"type": "tool_start", "agent_id": "coordinator"})
            self.assertTrue(project.ring.evicted)

            # [v1.0.24.4] 写入走异步队列；读盘前先定稿（等同关机 flush）。
            store.flush()
            events, gap, source = hub.replay("p_1", 0)
            self.assertFalse(gap)
            self.assertEqual(source, "disk+ring")
            self.assertEqual([event["type"] for event in events], ["message", "approval_card"])
            self.assertEqual(store.load_seq_watermark("p_1"), 4)

            durable = store.load_all_events("p_1")
            self.assertEqual(max(event["seq"] for event in durable), 2)
            restarted = Hub(store=store)
            restarted.restore(
                "p_1", "测试", durable, store.load_seq_watermark("p_1")
            )
            self.assertEqual(restarted.projects["p_1"].seq, 4)
            await restarted.emit(
                "p_1", {"type": "message", "agent_id": "coordinator", "content": "继续"}
            )
            self.assertEqual(restarted.projects["p_1"].seq, 5)

            # [v1.0.24.4] 停掉写入线程并排空队列——否则异步作业可能落在
            # TemporaryDirectory 清理期间，WinError 145「目录不是空的」。
            store.close()

    async def test_legacy_transient_jsonl_seeds_watermark_before_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Store(td)
            store.events_path("p_legacy").write_text(
                '{"type":"message","seq":2}\n'
                '{"type":"tool_start","seq":17}\n',
                encoding="utf-8",
            )
            self.assertEqual(store.load_seq_watermark("p_legacy"), 17)
            self.assertEqual(store.seq_path("p_legacy").read_text("ascii").strip(), "17")


class GateShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_all_settles_cards_before_engine_task_is_cancelled(self) -> None:
        hub = Hub()
        gate = Gate(hub, "p_gate")
        task = asyncio.create_task(
            gate.propose(
                tool="propose_next",
                agent_id="coordinator",
                card_body={"target_id": "fe_1", "instruction": "做测试"},
                timeout_s=60,
            )
        )
        await asyncio.sleep(0)
        self.assertTrue(gate.has_pending())

        self.assertEqual(await gate.cancel_all_settled("cancelled"), 1)
        self.assertFalse(gate.has_pending())
        with self.assertRaises(ApprovalCancelled):
            await task

        self.assertEqual(
            [event["type"] for event in hub.projects["p_gate"].ring.events()],
            ["approval_card", "approval_resolved"],
        )


if __name__ == "__main__":
    unittest.main()
