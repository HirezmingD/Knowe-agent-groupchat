# v1.0.17 — CompletionV1 structure tests.
# render_user_facing_completion was removed (I-3): the Worker's final text is now
# passed through unaltered by the engine, not re-templated here. Its placeholder-
# sentence behavior ("本任务无文件产物" / "未执行额外验证" / "风险：未另行评估") is
# gone by design. What remains is build_user_facing_completion, which produces the
# *structured* view (safe artifact list, verbatim authoritative fields) without rewriting prose.
from __future__ import annotations

import unittest

from knowe_harness.completion import CompletionStatus, completion_policy

from backend.worker_completion import (
    RUNTIME_DELIVERABLE_STATES,
    build_user_facing_completion,
    format_completion,
    completion_from_mapping,
    completion_from_message,
)


class CompletionViewV1Tests(unittest.TestCase):
    def test_authoritative_terms_are_preserved_and_arrays_are_present(self) -> None:
        view = build_user_facing_completion(
            status="PARTIAL",
            summary="WorkerRuntime 已调用 submit_report",
            risks=["pipeline 仍需复核"],
        )
        payload = view.to_dict()
        self.assertEqual(payload["summary"], "WorkerRuntime 已调用 submit_report")
        self.assertEqual(payload["risks"], ["pipeline 仍需复核"])
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(payload["verification"], [])
        self.assertEqual(payload["gaps"], [])

    def test_plain_state_and_chat_text_are_not_a_completion_protocol(self) -> None:
        self.assertIsNone(completion_from_mapping({"state": "SUCCEEDED", "content": "done"}))
        self.assertIsNone(completion_from_message({"type": "message", "content": "SUCCEEDED"}))

    def test_typed_runtime_boundary_is_projected(self) -> None:
        completion = completion_from_mapping({
            "runtime_result": {
                "status": "SUCCEEDED",
                "summary": "已完成并验证",
            }
        })
        self.assertIsNotNone(completion)
        assert completion is not None
        self.assertEqual(completion.state, "SUCCEEDED")
        self.assertEqual(completion.text, "已完成并验证")

    def test_unsafe_artifact_paths_are_rejected(self) -> None:
        view = build_user_facing_completion(
            status="SUCCEEDED",
            summary="已完成安全检查",
            artifacts=[
                {"path": "/etc/passwd", "name": "passwd"},
                {"path": "C:/secret.txt", "name": "secret.txt"},
                {"path": "../escape.txt", "name": "escape.txt"},
                {"path": "output/report.md", "name": "report.md"},
            ],
        )
        self.assertEqual([item.path for item in view.artifacts], ["output/report.md"])

    def test_every_completion_status_has_total_runtime_and_user_projection(self) -> None:
        self.assertEqual(RUNTIME_DELIVERABLE_STATES, {status.value for status in CompletionStatus})
        for status in CompletionStatus:
            with self.subTest(status=status.value):
                policy = completion_policy(status)
                self.assertTrue(policy.next_actions)
                self.assertTrue(policy.user_label)
                self.assertEqual(status.terminal, policy.terminal)

                runtime_result = {
                    "status": status.value,
                    "summary": f"summary-{status.value}",
                }
                if status is CompletionStatus.WAITING:
                    runtime_result["waiting_question"] = "需要补充什么？"
                completion = completion_from_mapping({"runtime_result": runtime_result})
                self.assertIsNotNone(completion)
                assert completion is not None
                self.assertEqual(completion.state, status.value)
                self.assertIn(policy.user_label, format_completion(completion))

                view = build_user_facing_completion(
                    status=status.value,
                    summary="",
                    next_actions=policy.next_actions,
                )
                self.assertEqual(view.summary, "")
                self.assertEqual(view.next_actions, policy.next_actions)

    def test_all_authoritative_text_fields_are_verbatim(self) -> None:
        view = build_user_facing_completion(
            status="PARTIAL",
            summary="  summary with spacing  ",
            verification=["check", "check", "  exact  "],
            risks=["WorkerRuntime risk"],
            gaps=["submit_report gap"],
            next_actions=["next", "next"],
            fallback_summary="must not overwrite",
        )
        self.assertEqual(view.summary, "  summary with spacing  ")
        self.assertEqual(view.verification, ("check", "check", "  exact  "))
        self.assertEqual(view.risks, ("WorkerRuntime risk",))
        self.assertEqual(view.gaps, ("submit_report gap",))
        self.assertEqual(view.next_actions, ("next", "next"))

    def test_structured_summary_carries_the_worker_words_not_a_template(self) -> None:
        # [I-3] build_ keeps the Worker's own summary in the structured view; it does
        # NOT inject "产物：本任务无文件产物 / 验证：未执行额外验证 / 风险：未另行评估".
        view = build_user_facing_completion(status="SUCCEEDED", summary="已完成口头分析，建议方案 B。")
        payload = view.to_dict()
        self.assertIn("已完成口头分析", payload["summary"])
        for placeholder in ("本任务无文件产物", "未执行额外验证", "未另行评估"):
            self.assertNotIn(placeholder, payload["summary"])


if __name__ == "__main__":
    unittest.main()
