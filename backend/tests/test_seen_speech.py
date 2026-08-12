# [v1.0.18] Seen Speech is advisory context, never an outbound rewrite filter.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.seen_speech import (
    SeenSpeechLedger,
    VisibleSpeech,
    notification_from_unknown,
    render_seen_speech_block,
)


class SeenSpeechTest(unittest.TestCase):
    def test_record_is_idempotent_and_reloadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.jsonl"
            speech = VisibleSpeech.create(
                project_id="p1",
                visible_id="visible-1",
                completion_id="c1",
                agent_id="worker-1",
                agent_name="小林",
                text="已完成页面并通过测试。",
                seq=9,
            )
            ledger = SeenSpeechLedger(path)
            self.assertTrue(ledger.record(speech))
            self.assertFalse(ledger.record(speech))
            self.assertEqual(ledger.count(), 1)
            loaded = SeenSpeechLedger(path).by_completion("c1")
            self.assertEqual([row.text for row in loaded], [speech.text])

    def test_block_contains_verbatim_text_and_speaker_without_internal_ids(self) -> None:
        speech = VisibleSpeech.create(
            project_id="p1",
            visible_id="visible-1",
            completion_id="c1",
            agent_id="worker-1",
            agent_name="小林",
            text="已完成页面并通过测试。我的判断是仍需补一次移动端验收。",
        )
        block = render_seen_speech_block([speech], total_count=12, max_chars=4)
        # [v1.0.24.2] 账本字段（visible_id / completion_id）不再注入 LLM 上下文
        self.assertNotIn("source_ref=", block)
        self.assertNotIn("completion_id=", block)
        self.assertNotIn("visible-1", block)
        self.assertIn("speaker=小林", block)
        self.assertIn(speech.text, block)
        self.assertIn("selected_rows=1; authoritative_rows=12", block)
        self.assertIn("Runtime 不会据此删除或改写最终文本", block)

    def test_selected_rows_are_not_similarity_filtered_or_deduplicated(self) -> None:
        rows = [
            VisibleSpeech.create(
                project_id="p1",
                visible_id="v1",
                completion_id="c1",
                agent_id="w1",
                agent_name="小林",
                text="已完成页面并通过测试。",
            ),
            VisibleSpeech.create(
                project_id="p1",
                visible_id="v2",
                completion_id="c2",
                agent_id="w2",
                agent_name="小周",
                text="已完成页面并通过测试。",
            ),
        ]
        block = render_seen_speech_block(rows)
        self.assertEqual(block.count("已完成页面并通过测试。"), 2)
        # [v1.0.24.2] 不再注入 source_ref/visible_id；speaker 保留用于区分行
        self.assertNotIn("seen-speech://", block)
        self.assertIn("speaker=小林", block)
        self.assertIn("speaker=小周", block)

    def test_multiple_completions_are_strictly_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SeenSpeechLedger(Path(tmp) / "seen.jsonl")
            for completion_id, visible_id, text in (
                ("c1", "v1", "第一项已经完成。"),
                ("c2", "v2", "第二项仍有风险。"),
            ):
                ledger.record(VisibleSpeech.create(
                    project_id="p1",
                    visible_id=visible_id,
                    completion_id=completion_id,
                    agent_id="worker-1",
                    agent_name="小林",
                    text=text,
                ))
            self.assertEqual([row.text for row in ledger.by_completion("c1")], ["第一项已经完成。"])
            self.assertEqual([row.text for row in ledger.by_completion("c2")], ["第二项仍有风险。"])

    def test_structured_notification_rejects_malformed_boundary_values(self) -> None:
        self.assertIsNone(notification_from_unknown(None))
        self.assertIsNone(notification_from_unknown({"kind": "completion_review"}))
        normalized = notification_from_unknown({
            "kind": "completion_review",
            "completion_id": " c1 ",
            "decision_required": ["accept", "DROP TABLE", "retry"],
        })
        self.assertEqual(normalized["completion_id"], "c1")
        self.assertEqual(normalized["decision_required"], ["accept", "retry"])


if __name__ == "__main__":
    unittest.main()
