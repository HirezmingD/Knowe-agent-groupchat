from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.runtime import (
    WORKER_TOOL_NAMES,
    RuntimeConfig,
    RuntimeStatus,
    StepOutcome,
    TaskEnvelope,
    ToolCall,
    WorkerRuntime,
)

# v1.0.17 — Runtime is a carrier, not a judge. These tests assert the invariants,
# not the removed judge behavior. (See design/2-PRD.md, design/3-架构指令.md.)


def _schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"fixed schema for {name}",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in WORKER_TOOL_NAMES
    ]


class FakeRegistry:
    def __init__(self, root: Path, *, fail_code: str = "") -> None:
        self.root = root
        self.fail_code = fail_code
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._schemas = _schemas()

    def names(self) -> list[str]:
        return list(WORKER_TOOL_NAMES)

    def get_schemas(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._schemas)

    async def execute(self, name: str, args: dict[str, Any], **_: Any) -> str:
        self.calls.append((name, dict(args)))
        if self.fail_code:
            return json.dumps(
                {"status": "error", "code": self.fail_code, "message": "service unavailable"}
            )
        if name == "safe_write_file":
            rel = str(args["path"])
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = str(args["content"]).encode("utf-8")
            target.write_bytes(payload)
            artifact = {
                "path": rel,
                "kind": "file",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "verified": True,
                "media_type": "text/plain",
            }
            return json.dumps({"status": "ok", "artifact": artifact, "artifacts": [artifact]})
        return json.dumps({"status": "ok", "message": f"{name} completed"})


class ScriptedModel:
    def __init__(self, outcomes: list[StepOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.contexts = []

    async def step(self, context, **_: Any) -> StepOutcome:
        self.contexts.append(context)
        if not self.outcomes:
            raise AssertionError("model called more times than scripted")
        return self.outcomes.pop(0)


def _envelope() -> TaskEnvelope:
    # [I-2] The goal is opaque payload — no inferred expectations. Deliberately
    # includes section-number-looking tokens to prove they are never parsed.
    return TaskEnvelope(
        task_id="task-1",
        project_id="project-1",
        goal="更新第 5.8 节，比例 ×1.333，产出 docs/design.md",
        worker_id="worker-1",
        attempt_id="attempt-1",
        metadata={},
    )


def _run(runtime: WorkerRuntime, envelope: TaskEnvelope | None = None):
    return asyncio.run(runtime.run(envelope or _envelope()))


def test_native_write_succeeds_and_every_turn_has_identical_fixed_schema(tmp_path: Path) -> None:
    registry = FakeRegistry(tmp_path)
    model = ScriptedModel(
        [
            StepOutcome.actions(
                ToolCall("call-1", "safe_write_file", {"path": "out.txt", "content": "hello"})
            ),
            StepOutcome.final("Created and verified out.txt."),
        ]
    )
    runtime = WorkerRuntime(
        model=model,
        registry=registry,
        workspace_root=tmp_path,
        config=RuntimeConfig(timeout_seconds=2),
    )

    result = _run(runtime)

    assert result.status is RuntimeStatus.SUCCEEDED
    assert result.delivery is not None
    assert (tmp_path / "out.txt").read_text("utf-8") == "hello"
    # Facts are still collected as telemetry (information), just not as a verdict.
    assert [fact.path for fact in result.artifacts] == ["out.txt"]
    assert result.artifacts[0].sha256 == hashlib.sha256(b"hello").hexdigest()
    assert registry.calls == [("safe_write_file", {"path": "out.txt", "content": "hello"})]
    assert model.contexts[0].tool_schemas == model.contexts[1].tool_schemas


def test_llm_final_is_completion_even_without_any_artifact(tmp_path: Path) -> None:
    # [I-1] A pure-answer task with no file output succeeds on the LLM's word.
    registry = FakeRegistry(tmp_path)
    model = ScriptedModel([StepOutcome.final("分析完成：建议采用方案 B。")])
    result = _run(
        WorkerRuntime(model=model, registry=registry, workspace_root=tmp_path)
    )
    assert result.status is RuntimeStatus.SUCCEEDED
    assert result.reason == "completed"
    assert registry.calls == []


def test_plain_text_tool_markup_is_still_never_executed(tmp_path: Path) -> None:
    # [I-3] The final is accepted verbatim (no _clean_final judging), and the
    # security property still holds: only native tool_calls execute, so text that
    # merely *looks* like a tool call never touches the filesystem.
    registry = FakeRegistry(tmp_path)
    markup = '<tool_call>{"name":"safe_write_file","arguments":{"path":"pwned.txt","content":"x"}}</tool_call>'
    model = ScriptedModel([StepOutcome.final(markup)])
    result = _run(
        WorkerRuntime(model=model, registry=registry, workspace_root=tmp_path,
                      config=RuntimeConfig(timeout_seconds=2))
    )
    assert result.status is RuntimeStatus.SUCCEEDED
    assert registry.calls == []
    assert not (tmp_path / "pwned.txt").exists()
    assert result.final_text == markup  # passed through unaltered


def test_final_text_is_passed_through_and_never_judged(tmp_path: Path) -> None:
    # [I-3] Text that the old _clean_final would have rejected (internal-ref-looking
    # strings) now succeeds and reaches the report verbatim.
    registry = FakeRegistry(tmp_path)
    text = "结果在 " + "raw" + "_" + "ref=/tmp/internal，已完成。"
    model = ScriptedModel([StepOutcome.final(text)])
    result = _run(WorkerRuntime(model=model, registry=registry, workspace_root=tmp_path))
    assert result.status is RuntimeStatus.SUCCEEDED
    assert result.final_text == text


def test_repeated_tool_error_does_not_abort_the_run(tmp_path: Path) -> None:
    # [I-4] The same tool error many times in a row does NOT terminate the run.
    # The LLM stays in control and ends the turn itself.
    registry = FakeRegistry(tmp_path, fail_code="web_unavailable")
    def action(n: int) -> StepOutcome:
        return StepOutcome.actions(ToolCall(f"call-{n}", "web_search", {"query": "x"}))
    model = ScriptedModel(
        [action(1), action(2), action(3), action(4), action(5),
         StepOutcome.final("The service is down; I'll report the limitation.")]
    )
    result = _run(
        WorkerRuntime(model=model, registry=registry, workspace_root=tmp_path,
                      config=RuntimeConfig(timeout_seconds=5))
    )
    assert result.status is RuntimeStatus.SUCCEEDED           # LLM decided to finish
    assert len(registry.calls) == 5                            # no cap at 2 or 3
    assert all(
        tuple(item["function"]["name"] for item in ctx.tool_schemas) == WORKER_TOOL_NAMES
        for ctx in model.contexts
    )


def test_unbounded_loop_runs_many_turns_before_final(tmp_path: Path) -> None:
    # [I-4] No turn ceiling: 30 tool turns then a final all execute.
    registry = FakeRegistry(tmp_path)
    outcomes = [
        StepOutcome.actions(ToolCall(f"c{n}", "safe_list_dir", {"path": "."}))
        for n in range(30)
    ]
    outcomes.append(StepOutcome.final("done"))
    model = ScriptedModel(outcomes)
    result = _run(
        WorkerRuntime(model=model, registry=registry, workspace_root=tmp_path,
                      config=RuntimeConfig(timeout_seconds=10))
    )
    assert result.status is RuntimeStatus.SUCCEEDED
    assert len(registry.calls) == 30


def test_waiting_and_blocked_are_explicit_llm_outcomes(tmp_path: Path) -> None:
    waiting = WorkerRuntime(
        model=ScriptedModel([StepOutcome.waiting("Which account should I use?")]),
        registry=FakeRegistry(tmp_path),
        workspace_root=tmp_path,
    )
    blocked = WorkerRuntime(
        model=ScriptedModel([StepOutcome.blocked("missing_credentials", "Credentials are required.")]),
        registry=FakeRegistry(tmp_path),
        workspace_root=tmp_path,
    )

    waiting_result = _run(waiting)
    blocked_result = _run(blocked)

    assert waiting_result.status is RuntimeStatus.WAITING
    assert waiting_result.task_run.waiting_question == "Which account should I use?"
    assert blocked_result.status is RuntimeStatus.BLOCKED
    assert blocked_result.task_run.dependency == "missing_credentials"


def test_timeout_and_cancellation_always_run_cleanup(tmp_path: Path) -> None:
    cleaned: list[str] = []

    class SlowModel:
        async def step(self, context, **_: Any) -> StepOutcome:
            del context
            await asyncio.sleep(0.1)
            return StepOutcome.final("late")

    timed = WorkerRuntime(
        model=SlowModel(),
        registry=FakeRegistry(tmp_path),
        workspace_root=tmp_path,
        config=RuntimeConfig(timeout_seconds=0.01),
        cleanup_callbacks=(lambda: cleaned.append("timeout"),),
    )
    timed_result = _run(timed)

    cancel_event = asyncio.Event()
    cancel_event.set()
    cancelled = WorkerRuntime(
        model=ScriptedModel([StepOutcome.final("should not run")]),
        registry=FakeRegistry(tmp_path),
        workspace_root=tmp_path,
        cancellation_event=cancel_event,
        cleanup_callbacks=(lambda: cleaned.append("cancel"),),
    )
    cancelled_result = _run(cancelled)

    assert timed_result.status is RuntimeStatus.TIMED_OUT
    assert cancelled_result.status is RuntimeStatus.CANCELLED
    assert cleaned == ["timeout", "cancel"]
