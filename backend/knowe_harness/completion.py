from __future__ import annotations

"""Completion journal, outbox, decisions, and waiting projection.

The Worker Runtime remains the owner of execution mechanics.  This module is the
Harness-side durability boundary that turns a Runtime stop into exactly one active
CompletionEvent and a replayable set of projection intents.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from knowe_provenance import assert_provenance_matches, normalize_provenance
from knowe_storage._sqlite import (
    PROVENANCE_COLUMN_DEFS,
    SQLiteDatabase,
    json_dumps,
    json_loads,
    provenance_sql_values,
)
from knowe_storage.task_run_repository import (
    OptimisticLockError,
    SQLiteTaskRunRepository,
    TaskRunAlreadyExists,
)
from backend.runtime import ArtifactRecord, DeliveryRecord, TaskRun, TaskState, utc_now


class ArtifactDisposition(str, Enum):
    """Stable projection labels for delivered or missing files."""

    CREATED_IN_ATTEMPT = "created_in_attempt"
    MODIFIED_IN_ATTEMPT = "modified_in_attempt"
    PREEXISTING_UNCLAIMED = "preexisting_unclaimed"
    UNAUTHORIZED_MUTATION = "unauthorized_mutation"
    DELETED_IN_ATTEMPT = "deleted_in_attempt"
    MISSING = "missing"


class CompletionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    CANCELLED = "CANCELLED"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    TIMED_OUT = "TIMED_OUT"
    ROLLED_BACK = "ROLLED_BACK"
    SUPERSEDED = "SUPERSEDED"

    @property
    def terminal(self) -> bool:
        return completion_policy(self).terminal

    @property
    def verified_delivery(self) -> bool:
        """Compatibility name for a submitted/explicitly accepted delivery."""

        return self in {CompletionStatus.SUCCEEDED, CompletionStatus.PARTIAL}

    @classmethod
    def from_run(cls, run: TaskRun) -> "CompletionStatus":
        """Read the authoritative task result without deriving it from availability."""

        raw = run.completion_status
        if raw:
            try:
                return cls(raw)
            except ValueError as exc:
                raise CompletionConflict(f"invalid completion status on run: {raw}") from exc
        # A Runtime stop without an explicit result is a protocol error, never an
        # implicit success and never something the projection layer may guess from text.
        return cls.SYSTEM_ERROR if run.state is TaskState.RUNNING else cls.FAILED


@dataclass(frozen=True)
class CompletionStatusPolicy:
    """Total product semantics for one authoritative completion status."""

    terminal: bool
    next_actions: tuple[str, ...]
    owner: str
    user_label: str
    fallback_summary: str
    projection_summary: str


_COMPLETION_STATUS_POLICIES: dict[CompletionStatus, CompletionStatusPolicy] = {
    CompletionStatus.SUCCEEDED: CompletionStatusPolicy(
        True, ("accept_delivery", "reject_delivery"), "NONE", "已完成",
        "任务已经完成。", "执行成员已提交结果，等待总管审阅。",
    ),
    CompletionStatus.PARTIAL: CompletionStatusPolicy(
        True, ("accept_partial", "retry", "reject_delivery"), "WORKER", "部分完成",
        "已完成可交付的部分，仍有事项需要补齐。", "执行成员提交了部分结果。",
    ),
    CompletionStatus.FAILED: CompletionStatusPolicy(
        True, ("retry", "reject_delivery", "cancel"), "WORKER", "未完成",
        "任务未能完成，已保留当前进度。", "任务未能完成。",
    ),
    CompletionStatus.BLOCKED: CompletionStatusPolicy(
        True, ("provide_dependency", "retry", "cancel"), "COORDINATOR", "受阻",
        "当前条件不足，任务暂时无法继续。", "任务因依赖或能力缺失而受阻。",
    ),
    CompletionStatus.WAITING: CompletionStatusPolicy(
        False, ("answer_question", "cancel", "supersede"), "USER", "等待补充信息",
        "还需要补充信息后才能继续。", "任务正在等待可恢复输入。",
    ),
    CompletionStatus.CANCELLED: CompletionStatusPolicy(
        True, ("retry",), "COORDINATOR", "已取消",
        "任务已停止。", "任务已取消。",
    ),
    CompletionStatus.SYSTEM_ERROR: CompletionStatusPolicy(
        True, ("retry", "inspect_diagnostics", "cancel"), "INFRASTRUCTURE", "系统异常",
        "执行过程中发生系统错误，已保留当前进度。", "执行过程中发生系统异常。",
    ),
    CompletionStatus.TIMED_OUT: CompletionStatusPolicy(
        True, ("retry", "inspect_diagnostics", "cancel"), "INFRASTRUCTURE", "执行超时",
        "任务执行超时，已保留当前进度。", "任务执行超过允许时限。",
    ),
    CompletionStatus.ROLLED_BACK: CompletionStatusPolicy(
        True, ("retry", "cancel"), "COORDINATOR", "已回滚",
        "本次变更已回滚。", "该历史结果已回滚。",
    ),
    CompletionStatus.SUPERSEDED: CompletionStatusPolicy(
        True, ("follow_superseding_task",), "COORDINATOR", "已由新任务接替",
        "旧任务已由更新的任务接替。", "该任务已由更新的任务接替。",
    ),
}

if set(_COMPLETION_STATUS_POLICIES) != set(CompletionStatus):
    missing = sorted(status.value for status in set(CompletionStatus) - set(_COMPLETION_STATUS_POLICIES))
    extra = sorted(status.value for status in set(_COMPLETION_STATUS_POLICIES) - set(CompletionStatus))
    raise RuntimeError(f"CompletionStatus policy table is not exhaustive; missing={missing}, extra={extra}")


def completion_policy(status: CompletionStatus | str) -> CompletionStatusPolicy:
    """Return the one authoritative policy for a legal status."""

    return _COMPLETION_STATUS_POLICIES[CompletionStatus(status)]


def completion_scope_id(task_id: str, attempt_id: str) -> str:
    """Stable lifecycle scope shared by Runtime, projections, and UI."""

    task = str(task_id or "").strip()
    attempt = str(attempt_id or "").strip()
    return f"task:{task}:attempt:{attempt}" if task or attempt else ""


def _required_completion_status(value: Any, *, field_name: str = "status") -> CompletionStatus:
    raw = str(value or "").strip()
    if not raw:
        raise CompletionConflict(f"missing required completion {field_name}")
    try:
        return CompletionStatus(raw)
    except ValueError as exc:
        raise CompletionConflict(f"invalid completion {field_name}: {raw}") from exc

class ProjectionKind(str, Enum):
    TASK_STATE = "task_state"
    REPORT = "report"
    COORDINATOR = "coordinator"
    UI = "ui"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    DELIVERY = "delivery"


class OutboxState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"


class DecisionType(str, Enum):
    PLAN_APPROVED = "plan_approved"
    TASK_DISPATCHED = "task_dispatched"
    DELIVERY_ACCEPTED = "delivery_accepted"
    DELIVERY_REJECTED = "delivery_rejected"
    DEPENDENCY_PROVIDED = "dependency_provided"
    TASK_CANCELLED = "task_cancelled"
    RETRY_REQUESTED = "retry_requested"
    PARTIAL_ACCEPTED = "partial_accepted"
    ROLLBACK_REQUESTED = "rollback_requested"
    SUPERSEDE_REQUESTED = "supersede_requested"


class CoordinatorAction(str, Enum):
    PROVIDE_DEPENDENCY = "provide_dependency"
    CANCEL = "cancel"
    ACCEPT_PARTIAL = "accept_partial"
    RETRY = "retry"
    REJECT = "reject"
    ROLLBACK = "rollback"
    SUPERSEDE = "supersede"


class CompletionConflict(RuntimeError):
    pass


class InvalidCoordinatorDecision(ValueError):
    pass


@dataclass(frozen=True)
class CompletionEvent:
    completion_id: str
    idempotency_key: str
    task_id: str
    attempt_id: str
    run_id: str
    project_id: str
    worker_id: str
    status: CompletionStatus
    runtime_state: str
    terminal_reason: str = ""
    artifact_manifest: tuple[dict[str, Any], ...] = ()
    delivery_intent: dict[str, Any] = field(default_factory=dict)
    delivery_record: dict[str, Any] | None = None
    question: str = ""
    dependency: str = ""
    gaps: tuple[str, ...] = ()
    gap_details: tuple[dict[str, Any], ...] = ()
    next_actions: tuple[str, ...] = ()
    version: int = 1
    active: bool = True
    supersedes_completion_id: str = ""
    rollback_of_completion_id: str = ""
    retry_of_completion_id: str = ""
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CompletionStatus(self.status))
        object.__setattr__(
            self,
            "provenance",
            normalize_provenance(self.provenance, legacy_if_missing=True).to_dict(),
        )
        object.__setattr__(self, "artifact_manifest", tuple(dict(x) for x in self.artifact_manifest))
        object.__setattr__(self, "gaps", tuple(str(x) for x in self.gaps if str(x).strip()))
        object.__setattr__(self, "gap_details", tuple(dict(x) for x in self.gap_details if isinstance(x, Mapping)))
        object.__setattr__(self, "next_actions", tuple(str(x) for x in self.next_actions if str(x).strip()))
        object.__setattr__(self, "version", max(1, int(self.version or 1)))

    @property
    def terminal(self) -> bool:
        return self.status.terminal

    @property
    def verified_delivery(self) -> bool:
        """Compatibility name for a committed Worker submission.

        No quality judgment happens here. The Coordinator owns accept, retry, and
        reject decisions after reading the report and artifacts.
        """

        if self.status is CompletionStatus.SUCCEEDED:
            return isinstance(self.delivery_record, Mapping)
        if self.status is CompletionStatus.PARTIAL:
            return bool(self.metadata.get("partial_accepted"))
        return False

    @property
    def received_artifacts(self) -> tuple[str, ...]:
        return tuple(
            str(row.get("path") or "")
            for row in self.artifact_manifest
            if str(row.get("path") or "")
            and bool(row.get("claimable"))
            and str(row.get("disposition") or "") not in {
                ArtifactDisposition.DELETED_IN_ATTEMPT.value,
                ArtifactDisposition.MISSING.value,
            }
        )

    @property
    def missing_artifacts(self) -> tuple[str, ...]:
        return tuple(
            str(row.get("path") or "")
            for row in self.artifact_manifest
            if str(row.get("path") or "")
            and bool((row.get("metadata") or {}).get("required", True))
            and (
                not bool(row.get("claimable"))
                or str(row.get("disposition") or "")
                in {
                    ArtifactDisposition.MISSING.value,
                    ArtifactDisposition.UNAUTHORIZED_MUTATION.value,
                    ArtifactDisposition.PREEXISTING_UNCLAIMED.value,
                }
            )
        )

    @property
    def files(self) -> tuple[dict[str, Any], ...]:
        files: list[dict[str, Any]] = []
        for row in self.artifact_manifest:
            current = dict(row.get("current") or {})
            path = str(row.get("path") or current.get("path") or "").strip()
            disposition = str(row.get("disposition") or "")
            if not path or not bool(row.get("claimable")):
                continue
            if disposition in {ArtifactDisposition.DELETED_IN_ATTEMPT.value, ArtifactDisposition.MISSING.value}:
                continue
            if not bool(current.get("exists", False)) or not bool(current.get("is_file", False)):
                continue
            files.append({
                "path": path,
                "name": Path(path).name,
                "sha256": str(current.get("sha256") or ""),
                "bytes": max(0, int(current.get("size") or 0)),
                "disposition": disposition,
                "identity": f"{self.completion_id}:{path}:{current.get('sha256') or ''}",
            })
        return tuple(files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "knowe.harness.completion-event.v2",
            "completion_id": self.completion_id,
            "idempotency_key": self.idempotency_key,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "worker_id": self.worker_id,
            "status": self.status.value,
            "runtime_state": self.runtime_state,
            "terminal": self.terminal,
            "terminal_reason": self.terminal_reason,
            "artifact_manifest": [dict(x) for x in self.artifact_manifest],
            "delivery_intent": dict(self.delivery_intent),
            "delivery_record": dict(self.delivery_record) if self.delivery_record else None,
            "question": self.question,
            "dependency": self.dependency,
            "gaps": list(self.gaps),
            "gap_details": [dict(x) for x in self.gap_details],
            "next_actions": list(self.next_actions),
            "files": [dict(x) for x in self.files],
            "version": self.version,
            "active": self.active,
            "supersedes_completion_id": self.supersedes_completion_id,
            "rollback_of_completion_id": self.rollback_of_completion_id,
            "retry_of_completion_id": self.retry_of_completion_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompletionEvent":
        return cls(
            completion_id=str(value.get("completion_id") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            task_id=str(value.get("task_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            run_id=str(value.get("run_id") or ""),
            project_id=str(value.get("project_id") or ""),
            worker_id=str(value.get("worker_id") or ""),
            status=_required_completion_status(value.get("status")),
            runtime_state=str(value.get("runtime_state") or ""),
            terminal_reason=str(value.get("terminal_reason") or ""),
            artifact_manifest=tuple(dict(x) for x in value.get("artifact_manifest") or () if isinstance(x, Mapping)),
            delivery_intent=dict(value.get("delivery_intent") or {}),
            delivery_record=(dict(value["delivery_record"]) if isinstance(value.get("delivery_record"), Mapping) else None),
            question=str(value.get("question") or ""),
            dependency=str(value.get("dependency") or ""),
            gaps=tuple(value.get("gaps") or ()),
            gap_details=tuple(dict(x) for x in (value.get("gap_details") or ()) if isinstance(x, Mapping)),
            next_actions=tuple(value.get("next_actions") or ()),
            version=max(1, int(value.get("version") or (value.get("metadata") or {}).get("state_version") or 1)),
            active=bool(value.get("active", True)),
            supersedes_completion_id=str(value.get("supersedes_completion_id") or ""),
            rollback_of_completion_id=str(value.get("rollback_of_completion_id") or ""),
            retry_of_completion_id=str(value.get("retry_of_completion_id") or ""),
            created_at=str(value.get("created_at") or utc_now()),
            metadata=dict(value.get("metadata") or {}),
            provenance=dict(value.get("provenance") or {}),
        )


@dataclass(frozen=True)
class TaskResultV1:
    """Atomic, user-visible result projection for one task attempt."""

    result_id: str
    completion_id: str
    task_id: str
    attempt_id: str
    run_id: str
    project_id: str
    worker_id: str
    status: CompletionStatus
    terminal: bool
    summary: str = ""
    checks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    owner: str = ""
    trace_id: str = ""
    effects: tuple[dict[str, Any], ...] = ()
    terminal_reason: str = ""
    artifact_manifest: tuple[dict[str, Any], ...] = ()
    files: tuple[dict[str, Any], ...] = ()
    gaps: tuple[str, ...] = ()
    gap_details: tuple[dict[str, Any], ...] = ()
    next_actions: tuple[str, ...] = ()
    delivery_record: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CompletionStatus(self.status))
        object.__setattr__(self, "artifact_manifest", tuple(dict(item) for item in self.artifact_manifest))
        object.__setattr__(self, "files", tuple(dict(item) for item in self.files))
        object.__setattr__(self, "checks", tuple(dict(item) for item in self.checks))
        object.__setattr__(self, "warnings", tuple(dict(item) for item in self.warnings))
        object.__setattr__(self, "effects", tuple(dict(item) for item in self.effects))
        object.__setattr__(self, "gaps", tuple(str(item) for item in self.gaps))
        object.__setattr__(self, "gap_details", tuple(dict(item) for item in self.gap_details))
        object.__setattr__(self, "next_actions", tuple(str(item) for item in self.next_actions))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(
            self,
            "provenance",
            normalize_provenance(self.provenance, legacy_if_missing=True).to_dict(),
        )

    @classmethod
    def from_completion(cls, event: CompletionEvent) -> "TaskResultV1":
        checks: list[dict[str, Any]] = []
        raw_checks = event.metadata.get("check_results") or ()
        if isinstance(raw_checks, (list, tuple)):
            checks.extend(dict(item) for item in raw_checks if isinstance(item, Mapping))
        warnings: list[dict[str, Any]] = []
        raw_warnings = event.metadata.get("runtime_warnings") or ()
        if isinstance(raw_warnings, (list, tuple)):
            warnings.extend(dict(item) for item in raw_warnings if isinstance(item, Mapping))
        summary = str(event.metadata.get("summary") or "").strip()
        if not summary and isinstance(event.delivery_record, Mapping):
            summary = str(event.delivery_record.get("text") or "").strip()
        policy = completion_policy(event.status)
        if not summary:
            summary = (
                event.question
                if event.status is CompletionStatus.WAITING and event.question
                else event.terminal_reason or policy.fallback_summary
            )
        owner = str(event.metadata.get("owner") or "").strip().upper()
        if not owner:
            owner = policy.owner
        effects = event.metadata.get("effect_journal") or ()
        return cls(
            result_id=_stable_id("result_", event.completion_id),
            completion_id=event.completion_id,
            task_id=event.task_id,
            attempt_id=event.attempt_id,
            run_id=event.run_id,
            project_id=event.project_id,
            worker_id=event.worker_id,
            status=event.status,
            terminal=event.terminal,
            summary=summary,
            checks=tuple(checks),
            warnings=tuple(warnings),
            owner=owner,
            trace_id=str(event.metadata.get("trace_id") or f"{event.task_id}:{event.attempt_id}"),
            effects=tuple(dict(item) for item in effects if isinstance(item, Mapping)),
            terminal_reason=event.terminal_reason,
            artifact_manifest=event.artifact_manifest,
            files=event.files,
            gaps=event.gaps,
            gap_details=event.gap_details,
            next_actions=event.next_actions,
            delivery_record=event.delivery_record,
            created_at=event.created_at,
            metadata={
                "runtime_state": event.runtime_state,
                "delivery_intent": dict(event.delivery_intent),
                "completion_version": event.version,
                "source": "CompletionEvent",
            },
            provenance=event.provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "knowe.harness.task-result.v1",
            "result_id": self.result_id,
            "completion_id": self.completion_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "worker_id": self.worker_id,
            "status": self.status.value,
            "terminal": self.terminal,
            "summary": self.summary,
            "checks": [dict(item) for item in self.checks],
            "warnings": [dict(item) for item in self.warnings],
            "owner": self.owner,
            "trace_id": self.trace_id,
            "effects": [dict(item) for item in self.effects],
            "terminal_reason": self.terminal_reason,
            "artifact_manifest": [dict(item) for item in self.artifact_manifest],
            "files": [dict(item) for item in self.files],
            "gaps": list(self.gaps),
            "gap_details": [dict(item) for item in self.gap_details],
            "next_actions": list(self.next_actions),
            "delivery_record": dict(self.delivery_record) if self.delivery_record else None,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskResultV1":
        return cls(
            result_id=str(value.get("result_id") or ""),
            completion_id=str(value.get("completion_id") or ""),
            task_id=str(value.get("task_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            run_id=str(value.get("run_id") or ""),
            project_id=str(value.get("project_id") or ""),
            worker_id=str(value.get("worker_id") or ""),
            status=_required_completion_status(value.get("status")),
            terminal=bool(value.get("terminal", True)),
            summary=str(value.get("summary") or ""),
            checks=tuple(dict(item) for item in (value.get("checks") or ()) if isinstance(item, Mapping)),
            warnings=tuple(dict(item) for item in (value.get("warnings") or ()) if isinstance(item, Mapping)),
            owner=str(value.get("owner") or ""),
            trace_id=str(value.get("trace_id") or ""),
            effects=tuple(dict(item) for item in (value.get("effects") or ()) if isinstance(item, Mapping)),
            terminal_reason=str(value.get("terminal_reason") or ""),
            artifact_manifest=tuple(dict(item) for item in (value.get("artifact_manifest") or ())),
            files=tuple(dict(item) for item in (value.get("files") or ())),
            gaps=tuple(value.get("gaps") or ()),
            gap_details=tuple(dict(item) for item in (value.get("gap_details") or ())),
            next_actions=tuple(value.get("next_actions") or ()),
            delivery_record=(
                dict(value["delivery_record"])
                if isinstance(value.get("delivery_record"), Mapping)
                else None
            ),
            created_at=str(value.get("created_at") or utc_now()),
            metadata=dict(value.get("metadata") or {}),
            provenance=dict(value.get("provenance") or {}),
        )


@dataclass(frozen=True)
class TaskJournalEntryV1:
    journal_entry_id: str
    task_id: str
    attempt_id: str
    run_id: str
    project_id: str
    worker_id: str
    sequence: int
    state: str
    runtime_state: str
    terminal: bool = False
    reason: str = ""
    heartbeat_at: str = field(default_factory=utc_now)
    completion_id: str = ""
    result_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "knowe.harness.task-journal-entry.v1",
            "journal_entry_id": self.journal_entry_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "worker_id": self.worker_id,
            "sequence": self.sequence,
            "state": self.state,
            "runtime_state": self.runtime_state,
            "terminal": self.terminal,
            "reason": self.reason,
            "heartbeat_at": self.heartbeat_at,
            "completion_id": self.completion_id,
            "result_id": self.result_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskJournalEntryV1":
        return cls(
            journal_entry_id=str(value.get("journal_entry_id") or ""),
            task_id=str(value.get("task_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            run_id=str(value.get("run_id") or ""),
            project_id=str(value.get("project_id") or ""),
            worker_id=str(value.get("worker_id") or ""),
            sequence=max(0, int(value.get("sequence") or 0)),
            state=str(value.get("state") or "STARTED"),
            runtime_state=str(value.get("runtime_state") or ""),
            terminal=bool(value.get("terminal", False)),
            reason=str(value.get("reason") or ""),
            heartbeat_at=str(value.get("heartbeat_at") or utc_now()),
            completion_id=str(value.get("completion_id") or ""),
            result_id=str(value.get("result_id") or ""),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OutboxEntry:
    outbox_id: str
    completion_id: str
    projection_kind: ProjectionKind
    route_key: str
    state: OutboxState = OutboxState.PENDING
    attempts: int = 0
    last_error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbox_id": self.outbox_id,
            "completion_id": self.completion_id,
            "projection_kind": self.projection_kind.value,
            "route_key": self.route_key,
            "state": self.state.value,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "payload": dict(self.payload),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OutboxEntry":
        return cls(
            outbox_id=str(value.get("outbox_id") or ""),
            completion_id=str(value.get("completion_id") or ""),
            projection_kind=ProjectionKind(str(value.get("projection_kind") or ProjectionKind.UI.value)),
            route_key=str(value.get("route_key") or ""),
            state=OutboxState(str(value.get("state") or OutboxState.PENDING.value)),
            attempts=int(value.get("attempts") or 0),
            last_error=str(value.get("last_error") or ""),
            payload=dict(value.get("payload") or {}),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
        )


@dataclass(frozen=True)
class WaitToken:
    wait_token_id: str
    completion_id: str
    task_id: str
    attempt_id: str
    run_id: str
    project_id: str
    worker_id: str
    question: str
    dependencies: tuple[str, ...]
    status: str = "open"
    answer: str = ""
    answer_ref: str = ""
    resume_run_id: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(str(x) for x in self.dependencies if str(x).strip()))
        object.__setattr__(self, "provenance", normalize_provenance(self.provenance, legacy_if_missing=True).to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "knowe.harness.wait-token.v1",
            "wait_token_id": self.wait_token_id,
            "completion_id": self.completion_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "worker_id": self.worker_id,
            "question": self.question,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "answer": self.answer,
            "answer_ref": self.answer_ref,
            "resume_run_id": self.resume_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WaitToken":
        return cls(
            wait_token_id=str(value.get("wait_token_id") or ""),
            completion_id=str(value.get("completion_id") or ""),
            task_id=str(value.get("task_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            run_id=str(value.get("run_id") or ""),
            project_id=str(value.get("project_id") or ""),
            worker_id=str(value.get("worker_id") or ""),
            question=str(value.get("question") or ""),
            dependencies=tuple(value.get("dependencies") or ()),
            status=str(value.get("status") or "open"),
            answer=str(value.get("answer") or ""),
            answer_ref=str(value.get("answer_ref") or ""),
            resume_run_id=str(value.get("resume_run_id") or ""),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
            provenance=dict(value.get("provenance") or {}),
        )


@dataclass(frozen=True)
class DecisionEvent:
    decision_id: str
    decision_type: DecisionType
    project_id: str
    actor: str
    task_id: str = ""
    attempt_id: str = ""
    completion_id: str = ""
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_type", DecisionType(self.decision_type))
        object.__setattr__(self, "provenance", normalize_provenance(self.provenance, legacy_if_missing=True).to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "knowe.harness.decision-event.v1",
            "decision_id": self.decision_id,
            "decision_type": self.decision_type.value,
            "project_id": self.project_id,
            "actor": self.actor,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "completion_id": self.completion_id,
            "reason": self.reason,
            "payload": dict(self.payload),
            "created_at": self.created_at,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DecisionEvent":
        return cls(
            decision_id=str(value.get("decision_id") or ""),
            decision_type=DecisionType(str(value.get("decision_type") or DecisionType.TASK_DISPATCHED.value)),
            project_id=str(value.get("project_id") or ""),
            actor=str(value.get("actor") or "system"),
            task_id=str(value.get("task_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            completion_id=str(value.get("completion_id") or ""),
            reason=str(value.get("reason") or ""),
            payload=dict(value.get("payload") or {}),
            created_at=str(value.get("created_at") or utc_now()),
            provenance=dict(value.get("provenance") or {}),
        )


def _stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    raw = "\0".join(json_dumps(part) if isinstance(part, (dict, list, tuple)) else str(part) for part in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _attempt_id(run: TaskRun) -> str:
    return run.envelope.attempt_id


def _worker_id(run: TaskRun) -> str:
    return run.envelope.worker_id


def _gap_details_for_run(
    run: TaskRun,
    manifest: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    details: list[dict[str, Any]] = [
        dict(item)
        for item in (run.metadata.get("gap_details") or ())
        if isinstance(item, Mapping)
    ]
    known = {
        (str(item.get("code") or ""), str(item.get("path") or item.get("reference") or ""))
        for item in details
    }
    for row in manifest:
        metadata = dict(row.get("metadata") or {})
        if not bool(metadata.get("required", True)) or bool(row.get("claimable")):
            continue
        path = str(row.get("path") or "artifact")
        marker = ("artifact_not_claimable", path)
        if marker in known:
            continue
        details.append({
            "code": "artifact_not_claimable",
            "path": path,
            "operation": str(row.get("operation") or ""),
            "disposition": str(row.get("disposition") or ""),
            "expected_sha256": metadata.get("last_attributed_sha256"),
            "actual_sha256": (row.get("current") or {}).get("sha256"),
            "repair_action": "Inspect the recorded workspace mutation before retrying.",
        })
        known.add(marker)
    return tuple(details)


def _block_projection_metadata(run: TaskRun) -> dict[str, Any]:
    """Project the runtime recovery decision into the durable CompletionEvent.

    Coordinators must not have to reverse-engineer a low-level exception string.  The
    runtime owns the distinction between a denied operation, a recoverable technical
    obstacle, a real product decision, and a hard security boundary, so that decision is
    persisted verbatim with the completion.
    """

    projected: dict[str, Any] = {}
    decision = run.metadata.get("block_decision")
    if isinstance(decision, Mapping):
        projected["block_decision"] = dict(decision)
        projected["block_disposition"] = str(
            decision.get("disposition") or run.metadata.get("block_disposition") or ""
        )
    for key in (
        "retry_same_task",
        "new_user_approval_required",
        "escalation_target",
        "context_preflight_recovery_attempt",
        "mutation_preflight_recovery_attempt",
        "recovery_attempts",
        "manual_review_required",
        "preserved_after_failure",
        "preserved_artifacts",
    ):
        value = run.metadata.get(key)
        if value not in (None, "", (), [], {}):
            projected[key] = value
    return projected


def _next_actions(status: CompletionStatus) -> tuple[str, ...]:
    return completion_policy(status).next_actions


def _gaps_for_run(
    run: TaskRun,
    manifest: Sequence[Mapping[str, Any]],
    status: CompletionStatus,
) -> tuple[str, ...]:
    gaps: list[str] = [
        str(item) for item in (run.metadata.get("gaps") or ()) if str(item).strip()
    ]
    for row in manifest:
        if bool((row.get("metadata") or {}).get("required", True)) and not bool(row.get("claimable")):
            text = f"artifact:{row.get('path')}:{row.get('disposition')}"
            if text not in gaps:
                gaps.append(text)
    if run.dependency:
        dependency = f"dependency:{run.dependency}"
        if dependency not in gaps:
            gaps.append(dependency)
    if not gaps and run.terminal_reason and status is not CompletionStatus.SUCCEEDED:
        gaps.append(run.terminal_reason)
    return tuple(gaps)


class SQLiteCompletionStore:
    """Authoritative CompletionEvent, transactional outbox, and wait/decision store."""

    def close(self) -> None:
        """Release the authoritative SQLite connection explicitly and idempotently."""
        if self._closed:
            return
        self.db.close()
        self._closed = True

    def __enter__(self) -> "SQLiteCompletionStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __init__(self, path: str | Path | SQLiteDatabase) -> None:
        self._closed = False
        self.db = path if isinstance(path, SQLiteDatabase) else SQLiteDatabase(path)

        # A v2 prerelease created ``agent_completion_outcomes_v2`` without
        # ``attempt_id``.  ``CREATE TABLE IF NOT EXISTS`` does not evolve that table,
        # and the index later in the schema script references the missing column, so
        # initialization used to fail before the generic provenance migrations could
        # run.  Repair the legacy shape before creating any attempt-based index.
        outcome_columns = self.db.table_columns("agent_completion_outcomes_v2")
        if outcome_columns and "attempt_id" not in outcome_columns:
            self.db.ensure_columns(
                "agent_completion_outcomes_v2",
                {"attempt_id": "TEXT NOT NULL DEFAULT ''"},
            )
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE agent_completion_outcomes_v2
                       SET attempt_id = CASE
                           WHEN TRIM(COALESCE(attempt_id, '')) <> '' THEN attempt_id
                           WHEN TRIM(COALESCE(run_id, '')) <> '' THEN 'legacy:' || run_id
                           WHEN TRIM(COALESCE(task_id, '')) <> '' THEN 'legacy:' || task_id
                           ELSE 'legacy:' || outcome_id
                       END
                     WHERE TRIM(COALESCE(attempt_id, '')) = ''
                    """
                )

        self.db.initialize(
            """
            CREATE TABLE IF NOT EXISTS completion_events_v2 (
                completion_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                runtime_state TEXT NOT NULL,
                terminal INTEGER NOT NULL,
                active INTEGER NOT NULL,
                supersedes_completion_id TEXT,
                rollback_of_completion_id TEXT,
                retry_of_completion_id TEXT,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_completion_task_attempt
                ON completion_events_v2(task_id, attempt_id, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_completion_active_lineage
                ON completion_events_v2(task_id, attempt_id) WHERE active=1;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_completion_active_terminal
                ON completion_events_v2(task_id, attempt_id) WHERE active=1 AND terminal=1;

            CREATE TABLE IF NOT EXISTS completion_outbox_v2 (
                outbox_id TEXT PRIMARY KEY,
                completion_id TEXT NOT NULL,
                projection_kind TEXT NOT NULL,
                route_key TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                last_error TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(completion_id, projection_kind, route_key),
                FOREIGN KEY(completion_id) REFERENCES completion_events_v2(completion_id)
            );
            CREATE INDEX IF NOT EXISTS idx_completion_outbox_pending
                ON completion_outbox_v2(state, updated_at, outbox_id);

            CREATE TABLE IF NOT EXISTS completion_projection_receipts_v2 (
                effect_id TEXT PRIMARY KEY,
                outbox_id TEXT NOT NULL UNIQUE,
                completion_id TEXT NOT NULL,
                projection_kind TEXT NOT NULL,
                effect_ref TEXT NOT NULL,
                effect_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wait_tokens_v2 (
                wait_token_id TEXT PRIMARY KEY,
                completion_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                question TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                answer TEXT NOT NULL,
                answer_ref TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wait_active_lineage
                ON wait_tokens_v2(task_id, attempt_id) WHERE status IN ('open','resuming');
            CREATE INDEX IF NOT EXISTS idx_wait_worker
                ON wait_tokens_v2(worker_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS decision_events_v2 (
                decision_id TEXT PRIMARY KEY,
                decision_type TEXT NOT NULL,
                project_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                completion_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decision_task
                ON decision_events_v2(task_id, attempt_id, created_at);

            CREATE TABLE IF NOT EXISTS agent_completion_outcomes_v2 (
                outcome_id TEXT PRIMARY KEY,
                completion_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                terminal_reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(completion_id) REFERENCES completion_events_v2(completion_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_completion_outcomes_task
                ON agent_completion_outcomes_v2(task_id, attempt_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_completion_outcomes_worker
                ON agent_completion_outcomes_v2(project_id, worker_id, created_at);

            CREATE TABLE IF NOT EXISTS task_run_history_v2 (
                history_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_results_v1 (
                result_id TEXT PRIMARY KEY,
                completion_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                terminal INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(completion_id) REFERENCES completion_events_v2(completion_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_task_result_lineage
                ON task_results_v1(task_id, attempt_id, completion_id);
            CREATE INDEX IF NOT EXISTS idx_task_result_project_worker
                ON task_results_v1(project_id, worker_id, created_at);

            CREATE TABLE IF NOT EXISTS task_journal_v1 (
                journal_entry_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                state TEXT NOT NULL,
                runtime_state TEXT NOT NULL,
                terminal INTEGER NOT NULL,
                reason TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                completion_id TEXT NOT NULL,
                result_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(task_id, attempt_id, sequence, state, runtime_state)
            );
            CREATE INDEX IF NOT EXISTS idx_task_journal_lineage
                ON task_journal_v1(task_id, attempt_id, sequence);

            CREATE TABLE IF NOT EXISTS task_attempts_v1 (
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                state TEXT NOT NULL,
                runtime_state TEXT NOT NULL,
                terminal INTEGER NOT NULL,
                reason TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                completion_id TEXT NOT NULL,
                result_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(task_id, attempt_id)
            );
            CREATE INDEX IF NOT EXISTS idx_task_attempt_active
                ON task_attempts_v1(terminal, heartbeat_at);
            """
        )
        for table in (
            "completion_events_v2",
            "wait_tokens_v2",
            "decision_events_v2",
            "agent_completion_outcomes_v2",
        ):
            self.db.ensure_columns(table, PROVENANCE_COLUMN_DEFS)
        self.db.register_schema("harness.completion_event")
        self.db.register_schema("harness.completion_outbox")
        self.db.register_schema("harness.wait_token")
        self.db.register_schema("harness.decision_event")
        self.db.register_schema("harness.agent_completion_outcome")
        self.db.register_schema("harness.task_result")
        self.db.register_schema("harness.task_journal")

    @staticmethod
    def _payload_provenance(payload_json: str) -> dict[str, Any]:
        payload = json_loads(payload_json, {})
        return normalize_provenance(
            payload.get("provenance") if isinstance(payload, Mapping) else None,
            legacy_if_missing=True,
        ).to_dict()

    @staticmethod
    def _journal_state_for_run(run: TaskRun) -> str:
        if run.completion_status:
            return run.completion_status
        if run.state is TaskState.IDLE:
            return "STARTED"
        return "RUNNING"

    def _journal_for_run(
        self,
        run: TaskRun,
        *,
        state: str | None = None,
        sequence: int | None = None,
        terminal: bool | None = None,
        reason: str = "",
        completion_id: str = "",
        result_id: str = "",
    ) -> TaskJournalEntryV1:
        selected = str(state or self._journal_state_for_run(run))
        if terminal is None:
            try:
                is_terminal = CompletionStatus(selected).terminal
            except ValueError:
                is_terminal = False
        else:
            is_terminal = bool(terminal)
        attempt_id = _attempt_id(run)
        journal_sequence = max(
            0,
            int(run.version or 0) if sequence is None else int(sequence),
        )
        return TaskJournalEntryV1(
            journal_entry_id=_stable_id(
                "journal_",
                run.envelope.task_id,
                attempt_id,
                run.run_id,
                journal_sequence,
                selected,
                run.state.value,
                completion_id,
            ),
            task_id=run.envelope.task_id,
            attempt_id=attempt_id,
            run_id=run.run_id,
            project_id=run.envelope.project_id,
            worker_id=_worker_id(run),
            sequence=journal_sequence,
            state=selected,
            runtime_state=run.state.value,
            terminal=is_terminal,
            reason=str(reason or run.terminal_reason or run.dependency or ""),
            heartbeat_at=run.updated_at or utc_now(),
            completion_id=completion_id,
            result_id=result_id,
            metadata={"task_run_version": run.version},
        )

    @staticmethod
    def _insert_journal_entry(conn: Any, entry: TaskJournalEntryV1) -> TaskJournalEntryV1:
        payload = entry.to_dict()
        conn.execute(
            """
            INSERT OR IGNORE INTO task_journal_v1(
                journal_entry_id, task_id, attempt_id, run_id, project_id,
                worker_id, sequence, state, runtime_state, terminal, reason,
                heartbeat_at, completion_id, result_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.journal_entry_id,
                entry.task_id,
                entry.attempt_id,
                entry.run_id,
                entry.project_id,
                entry.worker_id,
                entry.sequence,
                entry.state,
                entry.runtime_state,
                1 if entry.terminal else 0,
                entry.reason,
                entry.heartbeat_at,
                entry.completion_id,
                entry.result_id,
                json_dumps(payload),
            ),
        )
        conn.execute(
            """
            INSERT INTO task_attempts_v1(
                task_id, attempt_id, run_id, project_id, worker_id, state,
                runtime_state, terminal, reason, heartbeat_at,
                completion_id, result_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, attempt_id) DO UPDATE SET
                run_id=excluded.run_id,
                project_id=excluded.project_id,
                worker_id=excluded.worker_id,
                state=excluded.state,
                runtime_state=excluded.runtime_state,
                terminal=excluded.terminal,
                reason=excluded.reason,
                heartbeat_at=excluded.heartbeat_at,
                completion_id=excluded.completion_id,
                result_id=excluded.result_id,
                payload_json=excluded.payload_json
            WHERE task_attempts_v1.terminal=0 OR excluded.terminal=1
            """,
            (
                entry.task_id,
                entry.attempt_id,
                entry.run_id,
                entry.project_id,
                entry.worker_id,
                entry.state,
                entry.runtime_state,
                1 if entry.terminal else 0,
                entry.reason,
                entry.heartbeat_at,
                entry.completion_id,
                entry.result_id,
                json_dumps(payload),
            ),
        )
        return entry

    def create_started_run(self, run: TaskRun) -> TaskRun:
        """Atomically persist the immutable execution input and initial journal states."""

        with self.db.transaction() as conn:
            self._ensure_runtime_tables(conn)
            existing = conn.execute(
                "SELECT run_id FROM task_runs WHERE task_id=?",
                (run.envelope.task_id,),
            ).fetchone()
            if existing is not None and str(existing["run_id"]) != run.run_id:
                raise TaskRunAlreadyExists(
                    f"task already has a different run: {run.envelope.task_id}"
                )
            self._upsert_task_run(conn, run)
            queued = self._journal_for_run(
                run,
                state="QUEUED",
                sequence=0,
                terminal=False,
                reason="task_envelope_persisted",
            )
            started = self._journal_for_run(
                run,
                state="STARTED",
                sequence=1,
                terminal=False,
                reason="worker_session_acquired",
            )
            self._insert_journal_entry(conn, queued)
            self._insert_journal_entry(conn, started)
        return run

    def mark_started(self, run: TaskRun) -> TaskJournalEntryV1:
        entry = self._journal_for_run(run, state="STARTED", terminal=False)
        with self.db.transaction() as conn:
            self._insert_journal_entry(conn, entry)
        return entry

    def record_progress(self, run: TaskRun) -> TaskJournalEntryV1:
        entry = self._journal_for_run(run, terminal=False)
        with self.db.transaction() as conn:
            current = conn.execute(
                "SELECT payload_json, terminal FROM task_attempts_v1 WHERE task_id=? AND attempt_id=?",
                (entry.task_id, entry.attempt_id),
            ).fetchone()
            if current is not None and bool(current["terminal"]):
                return TaskJournalEntryV1.from_dict(json_loads(current["payload_json"], {}))
            self._insert_journal_entry(conn, entry)
        return entry

    @staticmethod
    def _insert_task_result(conn: Any, event: CompletionEvent) -> TaskResultV1:
        result = TaskResultV1.from_completion(event)
        existing = conn.execute(
            "SELECT payload_json FROM task_results_v1 WHERE completion_id=?",
            (event.completion_id,),
        ).fetchone()
        if existing is not None:
            return TaskResultV1.from_dict(json_loads(existing["payload_json"], {}))
        conn.execute(
            """
            INSERT INTO task_results_v1(
                result_id, completion_id, task_id, attempt_id, run_id,
                project_id, worker_id, status, terminal, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.result_id,
                result.completion_id,
                result.task_id,
                result.attempt_id,
                result.run_id,
                result.project_id,
                result.worker_id,
                result.status.value,
                1 if result.terminal else 0,
                result.created_at,
                json_dumps(result.to_dict()),
            ),
        )
        return result

    @staticmethod
    def _ensure_runtime_tables(conn: Any) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                task_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                version INTEGER NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_task_run ON deliveries(task_id, run_id);
            CREATE TABLE IF NOT EXISTS delivery_artifacts (
                delivery_id TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size INTEGER NOT NULL,
                ownership TEXT NOT NULL,
                media_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(delivery_id, path, sha256),
                FOREIGN KEY(delivery_id) REFERENCES deliveries(delivery_id) ON DELETE CASCADE
            );
            """
        )

    def _insert_outbox(
        self,
        conn: Any,
        event: CompletionEvent,
        kinds: Iterable[ProjectionKind],
        *,
        result: TaskResultV1 | None = None,
    ) -> None:
        task_result = result or TaskResultV1.from_completion(event)
        for kind in kinds:
            route_key = self.route_key(event, kind)
            outbox_id = _stable_id("out_", event.completion_id, kind.value, route_key)
            entry = OutboxEntry(
                outbox_id=outbox_id,
                completion_id=event.completion_id,
                projection_kind=kind,
                route_key=route_key,
                payload={
                    "completion_id": event.completion_id,
                    "task_result_id": task_result.result_id,
                    "task_result": task_result.to_dict(),
                    "task_id": event.task_id,
                    "attempt_id": event.attempt_id,
                    "worker_id": event.worker_id,
                    "project_id": event.project_id,
                    "status": event.status.value,
                    "terminal": event.terminal,
                    "terminal_reason": event.terminal_reason,
                    "version": event.version,
                    "gaps": list(event.gaps),
                    "gap_details": [dict(item) for item in event.gap_details],
                    "next_actions": list(event.next_actions),
                    "files": [dict(item) for item in event.files],
                    "delivery_intent": dict(event.delivery_intent),
                },
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO completion_outbox_v2(
                    outbox_id, completion_id, projection_kind, route_key, state,
                    attempts, last_error, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.outbox_id,
                    entry.completion_id,
                    entry.projection_kind.value,
                    entry.route_key,
                    entry.state.value,
                    entry.attempts,
                    entry.last_error,
                    json_dumps(entry.to_dict()),
                    entry.created_at,
                    entry.updated_at,
                ),
            )

    def _ensure_result_commit(self, conn: Any, event: CompletionEvent) -> TaskResultV1:
        """Ensure result, journal snapshot, and all projection intents exist together.

        This helper is used for fresh commits and every idempotent replay path.  Older
        databases may contain a CompletionEvent written before TaskResult/Task Journal
        existed; replay repairs that historical row without creating a second outcome.
        """

        result = self._insert_task_result(conn, event)
        journal = TaskJournalEntryV1(
            journal_entry_id=_stable_id(
                "journal_",
                event.task_id,
                event.attempt_id,
                event.run_id,
                event.version,
                event.status.value,
                event.runtime_state,
                event.completion_id,
            ),
            task_id=event.task_id,
            attempt_id=event.attempt_id,
            run_id=event.run_id,
            project_id=event.project_id,
            worker_id=event.worker_id,
            sequence=event.version,
            state=event.status.value,
            runtime_state=event.runtime_state,
            terminal=event.terminal,
            reason=event.terminal_reason,
            heartbeat_at=event.created_at,
            completion_id=event.completion_id,
            result_id=result.result_id,
            metadata={"source": "CompletionEvent"},
        )
        self._insert_journal_entry(conn, journal)
        self._insert_outbox(
            conn,
            event,
            self.projection_kinds(event),
            result=result,
        )
        return result

    @staticmethod
    def route_key(event: CompletionEvent, kind: ProjectionKind) -> str:
        if kind is ProjectionKind.DELIVERY:
            return str(event.delivery_intent.get("audience") or "user") + ":" + str(
                event.delivery_intent.get("channel") or "default"
            )
        if kind in {ProjectionKind.MEMORY, ProjectionKind.KNOWLEDGE}:
            return event.project_id
        return event.worker_id or event.task_id

    @staticmethod
    def projection_kinds(event: CompletionEvent) -> tuple[ProjectionKind, ...]:
        # User-visible truth must be available before slow report/coordinator/memory
        # projections.  The order is persisted and reinforced by pending_outbox().
        kinds: list[ProjectionKind] = [
            ProjectionKind.TASK_STATE,
            ProjectionKind.UI,
            ProjectionKind.DELIVERY,
        ]
        kinds.extend((
            ProjectionKind.REPORT,
            ProjectionKind.COORDINATOR,
            ProjectionKind.MEMORY,
            ProjectionKind.KNOWLEDGE,
        ))
        return tuple(kinds)

    def _insert_event(self, conn: Any, event: CompletionEvent) -> CompletionEvent:
        existing = conn.execute(
            "SELECT payload_json FROM completion_events_v2 WHERE idempotency_key=?",
            (event.idempotency_key,),
        ).fetchone()
        if existing is not None:
            stored = CompletionEvent.from_dict(json_loads(existing["payload_json"], {}))
            try:
                assert_provenance_matches(stored.provenance, event.provenance, context="CompletionEvent replay")
            except ValueError as exc:
                raise CompletionConflict(str(exc)) from exc
            self._ensure_result_commit(conn, stored)
            return stored

        active = conn.execute(
            """
            SELECT payload_json FROM completion_events_v2
             WHERE task_id=? AND attempt_id=? AND active=1
             LIMIT 1
            """,
            (event.task_id, event.attempt_id),
        ).fetchone()
        supersedes = event.supersedes_completion_id
        if active is not None:
            active_event = CompletionEvent.from_dict(json_loads(active["payload_json"], {}))
            if active_event.run_id == event.run_id and active_event.status == event.status:
                self._ensure_result_commit(conn, active_event)
                return active_event
            supersedes = supersedes or active_event.completion_id
            conn.execute(
                "UPDATE completion_events_v2 SET active=0 WHERE completion_id=?",
                (active_event.completion_id,),
            )
            old_payload = active_event.to_dict()
            old_payload["active"] = False
            conn.execute(
                "UPDATE completion_events_v2 SET payload_json=? WHERE completion_id=?",
                (json_dumps(old_payload), active_event.completion_id),
            )
            if (
                active_event.status is CompletionStatus.WAITING
                and event.status is not CompletionStatus.WAITING
            ):
                self._resolve_wait_rows(
                    conn,
                    completion_id=active_event.completion_id,
                    status=("superseded" if event.status is CompletionStatus.SUPERSEDED else "resolved"),
                )

        effective = replace(event, supersedes_completion_id=supersedes)
        payload = effective.to_dict()
        provenance = provenance_sql_values(effective.provenance)
        conn.execute(
            """
            INSERT INTO completion_events_v2(
                completion_id, idempotency_key, task_id, attempt_id, run_id,
                project_id, worker_id, status, runtime_state, terminal, active,
                supersedes_completion_id, rollback_of_completion_id,
                retry_of_completion_id, created_at, payload_json,
                provenance_json, build_id, git_commit, runtime_schema_version,
                harness_schema_version, prompt_bundle_version, migration_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                effective.completion_id,
                effective.idempotency_key,
                effective.task_id,
                effective.attempt_id,
                effective.run_id,
                effective.project_id,
                effective.worker_id,
                effective.status.value,
                effective.runtime_state,
                1 if effective.terminal else 0,
                1 if effective.active else 0,
                effective.supersedes_completion_id or None,
                effective.rollback_of_completion_id or None,
                effective.retry_of_completion_id or None,
                effective.created_at,
                json_dumps(payload),
                *provenance,
            ),
        )
        # A resumed lineage is still the same task/attempt.  Any non-WAITING active
        # CompletionEvent closes the prior wait token in the *same transaction* as
        # terminal state and outbox insertion.  This prevents a crash between terminal
        # commit and an asynchronous cleanup from leaving an orphan open wait token.
        if effective.status is not CompletionStatus.WAITING:
            rows = conn.execute(
                """
                SELECT wait_token_id, payload_json FROM wait_tokens_v2
                 WHERE task_id=? AND attempt_id=? AND status IN ('open','resuming')
                """,
                (effective.task_id, effective.attempt_id),
            ).fetchall()
            for row in rows:
                token = WaitToken.from_dict(json_loads(row["payload_json"], {}))
                closed = replace(
                    token,
                    status=(
                        "cancelled" if effective.status is CompletionStatus.CANCELLED
                        else "superseded" if effective.status is CompletionStatus.SUPERSEDED
                        else "resolved"
                    ),
                    updated_at=effective.created_at,
                )
                conn.execute(
                    """
                    UPDATE wait_tokens_v2
                       SET status=?, updated_at=?, payload_json=?
                     WHERE wait_token_id=?
                    """,
                    (closed.status, closed.updated_at, json_dumps(closed.to_dict()), closed.wait_token_id),
                )
        self._ensure_result_commit(conn, effective)
        return effective

    def _insert_agent_completion_outcome(
        self,
        conn: Any,
        event: CompletionEvent,
    ) -> None:
        """Persist one idempotent Worker/Agent stop projection in the same transaction."""

        outcome_id = _stable_id("aco_", event.completion_id)
        payload = {
            "schema_version": "knowe.harness.agent-completion-outcome.v2",
            "outcome_id": outcome_id,
            "completion_id": event.completion_id,
            "task_id": event.task_id,
            "attempt_id": event.attempt_id,
            "run_id": event.run_id,
            "project_id": event.project_id,
            "worker_id": event.worker_id,
            "status": event.status.value,
            "runtime_state": event.runtime_state,
            "terminal": event.terminal,
            "terminal_reason": event.terminal_reason,
            "artifact_manifest": [dict(item) for item in event.artifact_manifest],
            "gaps": list(event.gaps),
            "created_at": event.created_at,
            "provenance": dict(event.provenance),
        }
        provenance = provenance_sql_values(event.provenance)
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_completion_outcomes_v2(
                outcome_id, completion_id, task_id, attempt_id, run_id,
                project_id, worker_id, status, terminal_reason, created_at,
                payload_json, provenance_json, build_id, git_commit,
                runtime_schema_version, harness_schema_version,
                prompt_bundle_version, migration_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome_id,
                event.completion_id,
                event.task_id,
                event.attempt_id,
                event.run_id,
                event.project_id,
                event.worker_id,
                event.status.value,
                event.terminal_reason,
                event.created_at,
                json_dumps(payload),
                *provenance,
            ),
        )

    @staticmethod
    def _resolve_wait_rows(
        conn: Any,
        *,
        completion_id: str,
        status: str,
    ) -> None:
        """Resolve wait rows and their canonical payload together.

        Keeping the indexed ``status`` column and ``payload_json`` in lock-step is
        important because recovery reads both forms in different code paths.
        """

        rows = conn.execute(
            "SELECT wait_token_id, payload_json FROM wait_tokens_v2 WHERE completion_id=?",
            (completion_id,),
        ).fetchall()
        now = utc_now()
        for row in rows:
            token = WaitToken.from_dict(json_loads(row["payload_json"], {}))
            if token.status not in {"open", "resuming"}:
                continue
            updated = replace(token, status=status, updated_at=now)
            conn.execute(
                """
                UPDATE wait_tokens_v2
                   SET status=?, updated_at=?, payload_json=?
                 WHERE wait_token_id=?
                """,
                (status, now, json_dumps(updated.to_dict()), token.wait_token_id),
            )

    def _upsert_task_run(self, conn: Any, run: TaskRun) -> None:
        self._ensure_runtime_tables(conn)
        payload = json_dumps(run.to_dict())
        row = conn.execute(
            "SELECT run_id, version, payload_json FROM task_runs WHERE task_id=?",
            (run.envelope.task_id,),
        ).fetchone()
        provenance = provenance_sql_values(run.provenance)
        # Columns can be absent when this store is created before the regular repository.
        columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(task_runs)").fetchall()}
        for name, definition in PROVENANCE_COLUMN_DEFS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE task_runs ADD COLUMN {name} {definition}")
        if row is None:
            conn.execute(
                """
                INSERT INTO task_runs(
                    task_id, run_id, version, state, payload_json, created_at, updated_at,
                    provenance_json, build_id, git_commit, runtime_schema_version,
                    harness_schema_version, prompt_bundle_version, migration_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.envelope.task_id,
                    run.run_id,
                    run.version,
                    run.state.value,
                    payload,
                    run.started_at,
                    run.updated_at,
                    *provenance,
                ),
            )
            return
        if str(row["run_id"]) != run.run_id:
            raise CompletionConflict(
                f"task {run.envelope.task_id} currently belongs to run {row['run_id']}, not {run.run_id}"
            )
        stored = TaskRun.from_dict(json_loads(row["payload_json"], {}))
        try:
            assert_provenance_matches(stored.provenance, run.provenance, context="CompletionEvent/TaskRun")
        except ValueError as exc:
            raise CompletionConflict(str(exc)) from exc
        if int(row["version"]) > run.version:
            raise CompletionConflict(
                f"completion snapshot is stale: task version {run.version}, stored {row['version']}"
            )
        conn.execute(
            """
            UPDATE task_runs
               SET version=?, state=?, payload_json=?, updated_at=?,
                   provenance_json=?, build_id=?, git_commit=?, runtime_schema_version=?,
                   harness_schema_version=?, prompt_bundle_version=?, migration_epoch=?
             WHERE task_id=? AND run_id=?
            """,
            (
                run.version,
                run.state.value,
                payload,
                run.updated_at,
                *provenance,
                run.envelope.task_id,
                run.run_id,
            ),
        )

    def _insert_delivery(self, conn: Any, record: DeliveryRecord) -> DeliveryRecord:
        self._ensure_runtime_tables(conn)
        columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(deliveries)").fetchall()}
        for name, definition in PROVENANCE_COLUMN_DEFS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE deliveries ADD COLUMN {name} {definition}")
        existing = conn.execute(
            "SELECT payload_json FROM deliveries WHERE idempotency_key=?",
            (record.idempotency_key,),
        ).fetchone()
        if existing is not None:
            stored = DeliveryRecord.from_dict(json_loads(existing["payload_json"], {}))
            try:
                assert_provenance_matches(stored.provenance, record.provenance, context="Delivery replay")
            except ValueError as exc:
                raise CompletionConflict(str(exc)) from exc
            return stored
        other = conn.execute(
            "SELECT payload_json FROM deliveries WHERE task_id=? AND run_id=?",
            (record.task_id, record.run_id),
        ).fetchone()
        if other is not None:
            stored = DeliveryRecord.from_dict(json_loads(other["payload_json"], {}))
            if stored.idempotency_key != record.idempotency_key:
                raise CompletionConflict(f"run {record.run_id} already committed a different delivery")
            return stored
        provenance = provenance_sql_values(record.provenance)
        conn.execute(
            """
            INSERT INTO deliveries(
                delivery_id, idempotency_key, task_id, run_id, project_id, state,
                created_at, payload_json, provenance_json, build_id, git_commit,
                runtime_schema_version, harness_schema_version, prompt_bundle_version,
                migration_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.delivery_id,
                record.idempotency_key,
                record.task_id,
                record.run_id,
                record.project_id,
                record.state.value,
                record.created_at,
                json_dumps(record.to_dict()),
                *provenance,
            ),
        )
        for artifact in record.artifacts:
            conn.execute(
                """
                INSERT OR IGNORE INTO delivery_artifacts(
                    delivery_id, path, sha256, size, ownership, media_type, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.delivery_id,
                    artifact.path,
                    artifact.sha256,
                    artifact.size,
                    artifact.ownership,
                    artifact.media_type,
                    json_dumps(artifact.__dict__),
                ),
            )
        return record

    def _wait_token_for_event(self, conn: Any, event: CompletionEvent) -> WaitToken:
        # A worker may ask a second question after the user answered the first.  Keep a
        # single durable wait object for the task/attempt lineage instead of violating
        # the active-lineage unique index or silently creating a new task.
        prior_row = conn.execute(
            """
            SELECT payload_json FROM wait_tokens_v2
             WHERE task_id=? AND attempt_id=? AND status IN ('open','resuming')
             ORDER BY updated_at DESC LIMIT 1
            """,
            (event.task_id, event.attempt_id),
        ).fetchone()
        if prior_row is not None:
            prior = WaitToken.from_dict(json_loads(prior_row["payload_json"], {}))
            updated = replace(
                prior,
                completion_id=event.completion_id,
                run_id=event.run_id,
                status="open",
                question=event.question or "Additional input is required.",
                dependencies=tuple(x for x in (event.dependency,) if x),
                answer="",
                answer_ref="",
                resume_run_id="",
                updated_at=event.created_at,
            )
            conn.execute(
                """
                UPDATE wait_tokens_v2
                   SET completion_id=?, run_id=?, status=?, question=?, dependencies_json=?,
                       answer=?, answer_ref=?, updated_at=?, payload_json=?
                 WHERE wait_token_id=?
                """,
                (
                    updated.completion_id,
                    updated.run_id,
                    updated.status,
                    updated.question,
                    json_dumps(list(updated.dependencies)),
                    updated.answer,
                    updated.answer_ref,
                    updated.updated_at,
                    json_dumps(updated.to_dict()),
                    updated.wait_token_id,
                ),
            )
            return updated
        token_id = _stable_id("wait_", event.task_id, event.attempt_id, event.completion_id)
        token = WaitToken(
            wait_token_id=token_id,
            completion_id=event.completion_id,
            task_id=event.task_id,
            attempt_id=event.attempt_id,
            run_id=event.run_id,
            project_id=event.project_id,
            worker_id=event.worker_id,
            question=event.question or "Additional input is required.",
            dependencies=tuple(x for x in (event.dependency,) if x),
            provenance=event.provenance,
        )
        provenance = provenance_sql_values(token.provenance)
        conn.execute(
            """
            INSERT OR IGNORE INTO wait_tokens_v2(
                wait_token_id, completion_id, task_id, attempt_id, run_id,
                project_id, worker_id, status, question, dependencies_json,
                answer, answer_ref, created_at, updated_at, payload_json,
                provenance_json, build_id, git_commit, runtime_schema_version,
                harness_schema_version, prompt_bundle_version, migration_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token.wait_token_id,
                token.completion_id,
                token.task_id,
                token.attempt_id,
                token.run_id,
                token.project_id,
                token.worker_id,
                token.status,
                token.question,
                json_dumps(list(token.dependencies)),
                token.answer,
                token.answer_ref,
                token.created_at,
                token.updated_at,
                json_dumps(token.to_dict()),
                *provenance,
            ),
        )
        return token

    def manifest_for_task(
        self,
        task_id: str,
        *,
        delivery_artifacts: Iterable[ArtifactRecord] = (),
        envelope_metadata: Mapping[str, Any] | None = None,
        workspace_root: str | Path | None = None,
        baseline: Sequence[str] = (),
    ) -> tuple[dict[str, Any], ...]:
        del envelope_metadata
        # The projection is built only from files the Runtime verified and submitted.
        output: list[dict[str, Any]] = []
        for record in delivery_artifacts:
            output.append({
                "task_id": task_id,
                "path": record.path,
                "operation": "observed",
                "baseline": {},
                "current": {
                    "path": record.path,
                    "exists": True,
                    "is_file": True,
                    "readable": True,
                    "sha256": record.sha256,
                    "size": record.size,
                },
                "disposition": ArtifactDisposition.CREATED_IN_ATTEMPT.value,
                "claimable": True,
                "metadata": {
                    "source": "worker_submission",
                    "media_type": record.media_type,
                    "ownership": record.ownership,
                },
            })
        # [v1.0.23.8-C] workspace 扫描兜底：worker 用 shell cp 之类不产生
        #   verified fact 的方式交付的文件（例如被沙箱拦截后改用 shell 复制，
        #   复制还会保留源 mtime，时间窗方案会漏捕）→ 用快照对比：
        #   attempt 开始时 engine 拍过 workspace 文件清单（baseline），
        #   这里扫描当前 workspace，凡是 baseline 里没有的交付文件就是本次
        #   attempt 新增的，补进 manifest（无论 mtime）。只认常见交付扩展名
        #   + 排除系统/内部目录，避免误捕。
        if workspace_root:
            output.extend(self._scan_workspace_deltas(task_id, workspace_root, baseline, existing=output))
        return tuple(output)

    @staticmethod
    def _scan_workspace_deltas(
        task_id: str,
        workspace_root: str | Path,
        baseline: Sequence[str] = (),
        *,
        existing: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        # Scan workspace for delivery files absent from the attempt baseline.
        # Harness-side fallback: files the Runtime never verified (shell cp,
        # direct os ops) still surface as file cards. Best-effort: any scan
        # error yields nothing, never raises (delivery must not break).
        try:
            root = Path(workspace_root).expanduser().resolve()
            if not root.is_dir():
                return []
        except Exception:
            return []

        known = set()
        for row in existing:
            p = str(row.get("path") or "").strip().replace("\\", "/")
            if p:
                known.add(p.casefold())

        baseline_set = {str(p).strip().replace("\\", "/").casefold() for p in baseline if str(p).strip()}

        SKIP_DIRS = {
            "node_modules", ".git", "__pycache__", "logs", "backups", "backup",
            "dist", "build", "out", ".venv", "venv", "data", "runtime",
            "agents", "handoffs", "knowledge", "memory", ".idea", ".vscode",
        }
        DELIVERY_EXTS = {
            ".md", ".mdx", ".txt", ".pdf", ".docx", ".xlsx", ".pptx",
            ".csv", ".json", ".yaml", ".yml", ".html", ".htm",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
            ".zip", ".tar", ".gz", ".py", ".js", ".ts", ".tsx",
        }
        rows: list[dict[str, Any]] = []
        try:
            for path in root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    rel = path.relative_to(root).as_posix()
                    if any(part.casefold() in SKIP_DIRS for part in path.parts):
                        continue
                    rel_key = rel.casefold()
                    if rel_key in baseline_set:
                        continue
                    if rel_key in known:
                        continue
                    suffix = path.suffix.casefold()
                    if suffix not in DELIVERY_EXTS:
                        continue
                    size = path.stat().st_size
                    sha256 = ""
                    try:
                        digest = hashlib.sha256()
                        with path.open("rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                digest.update(chunk)
                        sha256 = digest.hexdigest()
                    except OSError:
                        pass
                    rows.append({
                        "task_id": task_id,
                        "path": rel,
                        "operation": "observed",
                        "baseline": {},
                        "current": {
                            "path": rel,
                            "exists": True,
                            "is_file": True,
                            "readable": True,
                            "sha256": sha256,
                            "size": size,
                        },
                        "disposition": ArtifactDisposition.CREATED_IN_ATTEMPT.value,
                        "claimable": True,
                        "metadata": {
                            "source": "workspace_scan",
                            "media_type": "",
                            "ownership": "",
                        },
                    })
                except OSError:
                    continue
        except Exception:
            # 扫描失败绝不影响交付
            return []
        return rows


    @staticmethod
    def delivery_intent(run: TaskRun) -> dict[str, Any]:
        target = run.envelope.delivery
        metadata = dict(target.metadata or {})
        return {
            "audience": target.audience.value,
            "channel": target.channel,
            "attempt_id": target.attempt_id or _attempt_id(run),
            "report_required": bool(metadata.get("report_required", True)),
            "author": (
                "worker"
                if run.completion_status in {CompletionStatus.SUCCEEDED.value, CompletionStatus.PARTIAL.value}
                else "harness"
            ),
        }

    def _event_for_run(
        self,
        run: TaskRun,
        *,
        status: CompletionStatus,
        manifest: Sequence[Mapping[str, Any]],
        record: DeliveryRecord | None,
        idempotency_key: str = "",
        supersedes_completion_id: str = "",
        rollback_of_completion_id: str = "",
        retry_of_completion_id: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> CompletionEvent:
        attempt = _attempt_id(run)
        base_key = idempotency_key or _stable_id(
            "cmpkey_",
            run.envelope.task_id,
            attempt,
            run.run_id,
            status.value,
            run.version,
            run.terminal_reason,
        )
        completion_id = _stable_id("cmp_", base_key)
        event = CompletionEvent(
            completion_id=completion_id,
            idempotency_key=base_key,
            task_id=run.envelope.task_id,
            attempt_id=attempt,
            run_id=run.run_id,
            project_id=run.envelope.project_id,
            worker_id=_worker_id(run),
            status=status,
            runtime_state=run.state.value,
            terminal_reason=run.terminal_reason or run.dependency,
            artifact_manifest=tuple(dict(x) for x in manifest),
            delivery_intent=self.delivery_intent(run),
            delivery_record=record.to_dict() if record is not None else None,
            question=run.waiting_question,
            dependency=run.dependency,
            gaps=_gaps_for_run(run, manifest, status),
            gap_details=_gap_details_for_run(run, manifest),
            next_actions=tuple(dict.fromkeys((
                *tuple(str(item) for item in (run.metadata.get("next_actions") or ()) if str(item).strip()),
                *_next_actions(status),
            ))),
            version=max(1, int(run.version or 1)),
            supersedes_completion_id=supersedes_completion_id,
            rollback_of_completion_id=rollback_of_completion_id,
            retry_of_completion_id=retry_of_completion_id,
            metadata={
                "source": run.envelope.source,
                "model_calls": run.model_calls,
                "tool_calls": run.tool_calls,
                "state_version": run.version,
                "instruction_ref": str(run.envelope.metadata.get("instruction_ref") or ""),
                "handoff_step": int(run.envelope.metadata.get("handoff_step") or 0),
                "handoff_keyword": str(run.envelope.metadata.get("handoff_keyword") or ""),
                "handoff_phase": str(run.envelope.metadata.get("handoff_phase") or ""),
                "task_title": run.envelope.title,
                "resume_wait_token_id": str(run.envelope.metadata.get("resume_wait_token_id") or ""),
                # The CompletionEvent carries the same immutable TaskEnvelope used by
                # Engine and Runtime so retry/resume never reconstructs a second model.
                "task_envelope": run.envelope.to_dict(),
                "task_envelope_ref": str(run.envelope.metadata.get("task_envelope_ref") or ""),
                "trace_id": str(run.envelope.metadata.get("trace_id") or f"{run.envelope.task_id}:{attempt}"),
                "summary": str(run.final_candidate or "").strip(),
                "effect_journal": [
                    dict(row)
                    for row in (run.metadata.get("effect_journal") or ())
                    if isinstance(row, Mapping)
                ],
                "check_results": [
                    dict(row)
                    for row in (run.metadata.get("check_results") or ())
                    if isinstance(row, Mapping)
                ],
                "runtime_warnings": [
                    dict(row)
                    for row in (run.metadata.get("runtime_warnings") or ())
                    if isinstance(row, Mapping)
                ],
                "owner": str(run.metadata.get("owner") or ""),
                **(
                    {"qa_findings": [dict(row) for row in run.metadata.get("qa_findings", ()) if isinstance(row, Mapping)]}
                    if isinstance(run.metadata.get("qa_findings"), (list, tuple))
                    else {}
                ),
                **_block_projection_metadata(run),
                # [v1.0.23.5] worker 推理全文（runtime 在任务结束时写入 run.metadata）
                "reasoning": str(run.metadata.get("reasoning") or "").strip(),
                # [v1.0.23.6] 推理耗时（秒）——「思考了 Xs」展示
                "reasoning_seconds": (
                    float(run.metadata["reasoning_seconds"])
                    if isinstance(run.metadata.get("reasoning_seconds"), (int, float))
                    else None
                ),
                **dict(metadata or {}),
            },
            provenance=run.provenance,
        )
        return event

    def commit_success(
        self,
        staged_run: TaskRun,
        record: DeliveryRecord,
        *,
        manifest: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[CompletionEvent, DeliveryRecord]:
        rows = tuple(manifest or self.manifest_for_task(
            staged_run.envelope.task_id,
            delivery_artifacts=record.artifacts,
            envelope_metadata=staged_run.envelope.metadata,
            workspace_root=staged_run.envelope.scope_root or None,
            baseline=staged_run.envelope.metadata.get("workspace_baseline") or (),
        ))
        event = self._event_for_run(
            staged_run,
            status=CompletionStatus.SUCCEEDED,
            manifest=rows,
            record=record,
            idempotency_key="completion:" + record.idempotency_key,
        )
        with self.db.transaction() as conn:
            self._upsert_task_run(conn, staged_run)
            stored_record = self._insert_delivery(conn, record)
            stored_event = self._insert_event(conn, event)
            self._insert_agent_completion_outcome(conn, stored_event)
        return stored_event, stored_record

    def commit_run(
        self,
        run: TaskRun,
        *,
        status: CompletionStatus | None = None,
        manifest: Sequence[Mapping[str, Any]] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CompletionEvent:
        selected = status or CompletionStatus.from_run(run)
        run.set_completion_status(selected.value)
        if run.state is TaskState.RUNNING:
            run.state = TaskState.IDLE
            run.version = max(1, int(run.version or 0) + 1)
            run.updated_at = utc_now()
            if not run.terminal_reason:
                run.terminal_reason = selected.value.lower()
        elif run.version == 0:
            run.version = 1
            run.updated_at = utc_now()
        rows = tuple(manifest or self.manifest_for_task(
            run.envelope.task_id,
            envelope_metadata=run.envelope.metadata,
            workspace_root=run.envelope.scope_root or None,
            baseline=run.envelope.metadata.get("workspace_baseline") or (),
        ))
        event = self._event_for_run(
            run,
            status=selected,
            manifest=rows,
            record=None,
            metadata=metadata,
        )
        with self.db.transaction() as conn:
            self._upsert_task_run(conn, run)
            stored = self._insert_event(conn, event)
            self._insert_agent_completion_outcome(conn, stored)
            if stored.status is CompletionStatus.WAITING:
                self._wait_token_for_event(conn, stored)
        return stored

    def _build_transition_event(
        self,
        prior: CompletionEvent,
        status: CompletionStatus,
        *,
        actor: str,
        reason: str,
        metadata: Mapping[str, Any] | None = None,
        rollback_of_completion_id: str = "",
        retry_of_completion_id: str = "",
    ) -> CompletionEvent:
        status = CompletionStatus(status)
        key = _stable_id(
            "cmpkey_",
            prior.completion_id,
            status.value,
            actor,
            reason,
            metadata or {},
        )
        event = CompletionEvent(
            completion_id=_stable_id("cmp_", key),
            idempotency_key=key,
            task_id=prior.task_id,
            attempt_id=prior.attempt_id,
            run_id=prior.run_id,
            project_id=prior.project_id,
            worker_id=prior.worker_id,
            status=status,
            runtime_state=prior.runtime_state,
            terminal_reason=reason or prior.terminal_reason,
            artifact_manifest=prior.artifact_manifest,
            delivery_intent={
                **prior.delivery_intent,
                "author": "harness" if status is not CompletionStatus.SUCCEEDED else prior.delivery_intent.get("author", "worker"),
            },
            delivery_record=prior.delivery_record,
            gaps=prior.gaps,
            gap_details=prior.gap_details,
            next_actions=_next_actions(status),
            version=prior.version + 1,
            supersedes_completion_id=prior.completion_id,
            rollback_of_completion_id=rollback_of_completion_id,
            retry_of_completion_id=retry_of_completion_id,
            metadata={
                **prior.metadata,
                "decision_actor": actor,
                **({"partial_accepted": True} if status is CompletionStatus.PARTIAL else {}),
                **dict(metadata or {}),
            },
            provenance=prior.provenance,
        )
        if status is CompletionStatus.PARTIAL and not event.artifact_manifest:
            raise CompletionConflict("PARTIAL requires an ArtifactManifest")
        return event

    def transition_completion(
        self,
        prior: CompletionEvent,
        status: CompletionStatus,
        *,
        actor: str,
        reason: str,
        metadata: Mapping[str, Any] | None = None,
        rollback_of_completion_id: str = "",
        retry_of_completion_id: str = "",
    ) -> CompletionEvent:
        event = self._build_transition_event(
            prior,
            status,
            actor=actor,
            reason=reason,
            metadata=metadata,
            rollback_of_completion_id=rollback_of_completion_id,
            retry_of_completion_id=retry_of_completion_id,
        )
        with self.db.transaction() as conn:
            stored = self._insert_event(conn, event)
            if prior.status is CompletionStatus.WAITING:
                self._resolve_wait_rows(
                    conn,
                    completion_id=prior.completion_id,
                    status=("superseded" if status is CompletionStatus.SUPERSEDED else "resolved"),
                )
        return stored

    def transition_with_decision(
        self,
        prior: CompletionEvent,
        status: CompletionStatus,
        decision: DecisionEvent,
        *,
        actor: str,
        reason: str,
        metadata: Mapping[str, Any] | None = None,
        rollback_of_completion_id: str = "",
        retry_of_completion_id: str = "",
    ) -> tuple[CompletionEvent, DecisionEvent]:
        """Atomically persist a state-changing Coordinator decision.

        A process crash must not leave an accepted-partial/cancel/rollback/supersede
        CompletionEvent without the DecisionEvent that authorized it.  Both rows and the
        new event's outbox are therefore inserted in one SQLite transaction.
        """

        event = self._build_transition_event(
            prior,
            status,
            actor=actor,
            reason=reason,
            metadata=metadata,
            rollback_of_completion_id=rollback_of_completion_id,
            retry_of_completion_id=retry_of_completion_id,
        )
        effective_decision = replace(decision, completion_id=event.completion_id)
        with self.db.transaction() as conn:
            stored = self._insert_event(conn, event)
            stored_decision = self._insert_decision(
                conn,
                replace(effective_decision, completion_id=stored.completion_id),
            )
            if prior.status is CompletionStatus.WAITING:
                self._resolve_wait_rows(
                    conn,
                    completion_id=prior.completion_id,
                    status=(
                        "superseded"
                        if CompletionStatus(status) is CompletionStatus.SUPERSEDED
                        else "resolved"
                    ),
                )
        return stored, stored_decision

    def get(self, completion_id: str) -> CompletionEvent | None:
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT payload_json FROM completion_events_v2 WHERE completion_id=?",
                (completion_id,),
            ).fetchone()
        return None if row is None else CompletionEvent.from_dict(json_loads(row["payload_json"], {}))

    def active_for(self, task_id: str, attempt_id: str | None = None) -> CompletionEvent | None:
        params: list[Any] = [task_id]
        clause = "task_id=? AND active=1"
        if attempt_id is not None:
            clause += " AND attempt_id=?"
            params.append(attempt_id)
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                f"SELECT payload_json FROM completion_events_v2 WHERE {clause} ORDER BY created_at DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return None if row is None else CompletionEvent.from_dict(json_loads(row["payload_json"], {}))

    def list_for_task(self, task_id: str) -> tuple[CompletionEvent, ...]:
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM completion_events_v2 WHERE task_id=? ORDER BY created_at, completion_id",
                (task_id,),
            ).fetchall()
        return tuple(CompletionEvent.from_dict(json_loads(row["payload_json"], {})) for row in rows)

    def list_active(self, *, project_id: str = "") -> tuple[CompletionEvent, ...]:
        query = "SELECT payload_json FROM completion_events_v2 WHERE active=1"
        params: tuple[Any, ...] = ()
        if project_id:
            query += " AND project_id=?"
            params = (project_id,)
        query += " ORDER BY created_at, completion_id"
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(query, params).fetchall()
        return tuple(CompletionEvent.from_dict(json_loads(row["payload_json"], {})) for row in rows)

    def task_result(
        self,
        *,
        completion_id: str = "",
        task_id: str = "",
        attempt_id: str = "",
    ) -> TaskResultV1 | None:
        """Return the same authoritative TaskResult used by UI and coordinator views."""

        if not completion_id and not task_id:
            raise ValueError("completion_id or task_id is required")
        with self.db.transaction(immediate=False) as conn:
            if completion_id:
                row = conn.execute(
                    "SELECT payload_json FROM task_results_v1 WHERE completion_id=?",
                    (completion_id,),
                ).fetchone()
            else:
                params: list[Any] = [task_id]
                clause = "c.task_id=? AND c.active=1"
                if attempt_id:
                    clause += " AND c.attempt_id=?"
                    params.append(attempt_id)
                row = conn.execute(
                    f"""
                    SELECT r.payload_json
                      FROM completion_events_v2 c
                      JOIN task_results_v1 r ON r.completion_id=c.completion_id
                     WHERE {clause}
                     ORDER BY c.created_at DESC, c.completion_id DESC
                     LIMIT 1
                    """,
                    tuple(params),
                ).fetchone()
        return None if row is None else TaskResultV1.from_dict(json_loads(row["payload_json"], {}))

    def journal_for(self, task_id: str, attempt_id: str = "") -> tuple[TaskJournalEntryV1, ...]:
        params: list[Any] = [str(task_id)]
        clause = "task_id=?"
        if attempt_id:
            clause += " AND attempt_id=?"
            params.append(str(attempt_id))
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json FROM task_journal_v1
                 WHERE {clause}
                 ORDER BY sequence, heartbeat_at, journal_entry_id
                """,
                tuple(params),
            ).fetchall()
        return tuple(TaskJournalEntryV1.from_dict(json_loads(row["payload_json"], {})) for row in rows)

    def attempt_snapshot(self, task_id: str, attempt_id: str) -> TaskJournalEntryV1 | None:
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT payload_json FROM task_attempts_v1 WHERE task_id=? AND attempt_id=?",
                (str(task_id), str(attempt_id)),
            ).fetchone()
        return None if row is None else TaskJournalEntryV1.from_dict(json_loads(row["payload_json"], {}))

    def active_attempts(self, *, worker_id: str = "", project_id: str = "") -> tuple[TaskJournalEntryV1, ...]:
        query = "SELECT payload_json FROM task_attempts_v1 WHERE terminal=0"
        params: list[Any] = []
        if worker_id:
            query += " AND worker_id=?"
            params.append(worker_id)
        if project_id:
            query += " AND project_id=?"
            params.append(project_id)
        query += " ORDER BY heartbeat_at, task_id, attempt_id"
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return tuple(TaskJournalEntryV1.from_dict(json_loads(row["payload_json"], {})) for row in rows)

    def worker_idle(self, worker_id: str) -> bool:
        """Return whether no queued/started/running attempt currently owns the Worker.

        WAITING remains an open task result for later resume, but it does not occupy the
        Worker and therefore must not make this availability check return ``False``.
        """

        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM task_attempts_v1
                 WHERE worker_id=? AND terminal=0
                   AND state IN ('QUEUED','STARTED','RUNNING')
                 LIMIT 1
                """,
                (str(worker_id),),
            ).fetchone()
        return row is None

    def pending_outbox(self, *, limit: int = 100, include_failed: bool = True) -> tuple[OutboxEntry, ...]:
        states = (OutboxState.PENDING.value, OutboxState.PROCESSING.value)
        if include_failed:
            states = (*states, OutboxState.FAILED.value)
        placeholders = ",".join("?" for _ in states)
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json FROM completion_outbox_v2
                 WHERE state IN ({placeholders})
                 ORDER BY CASE projection_kind
                            WHEN 'task_state' THEN 0
                            WHEN 'ui' THEN 1
                            WHEN 'delivery' THEN 2
                                                        WHEN 'report' THEN 3
                            WHEN 'coordinator' THEN 4
                            WHEN 'memory' THEN 5
                            WHEN 'knowledge' THEN 6
                            ELSE 99 END,
                          created_at, outbox_id LIMIT ?
                """,
                (*states, max(1, min(int(limit), 10_000))),
            ).fetchall()
        return tuple(OutboxEntry.from_dict(json_loads(row["payload_json"], {})) for row in rows)

    def claim_outbox(self, outbox_id: str) -> OutboxEntry | None:
        now = utc_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM completion_outbox_v2 WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                return None
            current = OutboxEntry.from_dict(json_loads(row["payload_json"], {}))
            if current.state is OutboxState.DELIVERED:
                return current
            updated = replace(
                current,
                state=OutboxState.PROCESSING,
                attempts=current.attempts + 1,
                updated_at=now,
            )
            conn.execute(
                """
                UPDATE completion_outbox_v2
                   SET state=?, attempts=?, last_error=?, payload_json=?, updated_at=?
                 WHERE outbox_id=?
                """,
                (
                    updated.state.value,
                    updated.attempts,
                    updated.last_error,
                    json_dumps(updated.to_dict()),
                    updated.updated_at,
                    updated.outbox_id,
                ),
            )
            return updated

    def acknowledge_outbox(
        self,
        outbox_id: str,
        *,
        effect_ref: str,
        effect_payload: Mapping[str, Any] | None = None,
    ) -> OutboxEntry:
        now = utc_now()
        payload = dict(effect_payload or {})
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM completion_outbox_v2 WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise KeyError(outbox_id)
            current = OutboxEntry.from_dict(json_loads(row["payload_json"], {}))
            updated = replace(current, state=OutboxState.DELIVERED, last_error="", updated_at=now)
            effect_id = _stable_id("effect_", current.completion_id, current.projection_kind.value, current.route_key)
            effect_sha = hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT OR IGNORE INTO completion_projection_receipts_v2(
                    effect_id, outbox_id, completion_id, projection_kind,
                    effect_ref, effect_sha256, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect_id,
                    current.outbox_id,
                    current.completion_id,
                    current.projection_kind.value,
                    str(effect_ref),
                    effect_sha,
                    json_dumps(payload),
                    now,
                ),
            )
            # [v1.0.31 R5] 送达即删：delivered 后无任何读取方（pending_outbox
            # 只查 state!='delivered'），删除替代仅改状态，防表无限膨胀。
            # 审计链由上方 completion_projection_receipts_v2 回执完整保留。
            conn.execute(
                "DELETE FROM completion_outbox_v2 WHERE outbox_id=?",
                (outbox_id,),
            )
        return updated

    def fail_outbox(self, outbox_id: str, error: BaseException | str) -> OutboxEntry:
        now = utc_now()
        message = str(error)[:4000]
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM completion_outbox_v2 WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise KeyError(outbox_id)
            current = OutboxEntry.from_dict(json_loads(row["payload_json"], {}))
            updated = replace(current, state=OutboxState.FAILED, last_error=message, updated_at=now)
            conn.execute(
                """
                UPDATE completion_outbox_v2
                   SET state=?, last_error=?, payload_json=?, updated_at=? WHERE outbox_id=?
                """,
                (updated.state.value, message, json_dumps(updated.to_dict()), now, outbox_id),
            )
        return updated

    def projection_receipt(self, outbox_id: str) -> dict[str, Any] | None:
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT * FROM completion_projection_receipts_v2 WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "effect_id": str(row["effect_id"]),
            "effect_ref": str(row["effect_ref"]),
            "effect_sha256": str(row["effect_sha256"]),
            "payload": json_loads(row["payload_json"], {}),
            "created_at": str(row["created_at"]),
        }

    def active_wait_for_completion(self, completion_id: str) -> WaitToken | None:
        """Return the open/resuming wait token for one exact CompletionEvent."""

        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM wait_tokens_v2
                 WHERE completion_id=? AND status IN ('open','resuming')
                 ORDER BY updated_at DESC LIMIT 1
                """,
                (completion_id,),
            ).fetchone()
        return None if row is None else WaitToken.from_dict(json_loads(row["payload_json"], {}))

    def active_wait_for_task(self, task_id: str, *, worker_id: str = "") -> WaitToken | None:
        """Return the open/resuming wait token for one exact task lineage."""

        query = (
            "SELECT payload_json FROM wait_tokens_v2 "
            "WHERE task_id=? AND status IN ('open','resuming')"
        )
        params: tuple[Any, ...] = (task_id,)
        if worker_id:
            query += " AND worker_id=?"
            params = (task_id, worker_id)
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(query, params).fetchone()
        return None if row is None else WaitToken.from_dict(json_loads(row["payload_json"], {}))

    def get_wait_token(self, wait_token_id: str) -> WaitToken | None:
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT payload_json FROM wait_tokens_v2 WHERE wait_token_id=?",
                (wait_token_id,),
            ).fetchone()
        return None if row is None else WaitToken.from_dict(json_loads(row["payload_json"], {}))

    def resume_wait(
        self,
        wait_token_id: str,
        *,
        answer: str,
        answer_ref: str,
        resume_run_id: str = "",
        actor: str = "user",
    ) -> WaitToken:
        clean = str(answer).strip()
        if not clean:
            raise ValueError("waiting answer must be non-empty")
        now = utc_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM wait_tokens_v2 WHERE wait_token_id=?",
                (wait_token_id,),
            ).fetchone()
            if row is None:
                raise KeyError(wait_token_id)
            token = WaitToken.from_dict(json_loads(row["payload_json"], {}))
            if token.status == "resolved":
                return token
            if token.status not in {"open", "resuming"}:
                raise CompletionConflict(f"wait token {wait_token_id} is {token.status}")
            updated = replace(
                token,
                status="resuming",
                answer=clean,
                answer_ref=str(answer_ref),
                resume_run_id=str(resume_run_id),
                updated_at=now,
            )
            conn.execute(
                """
                UPDATE wait_tokens_v2
                   SET status=?, answer=?, answer_ref=?, updated_at=?, payload_json=?
                 WHERE wait_token_id=?
                """,
                (
                    updated.status,
                    updated.answer,
                    updated.answer_ref,
                    updated.updated_at,
                    json_dumps(updated.to_dict()),
                    wait_token_id,
                ),
            )
            decision = DecisionEvent(
                decision_id=_stable_id("dec_", wait_token_id, updated.answer, actor),
                decision_type=DecisionType.DEPENDENCY_PROVIDED,
                project_id=updated.project_id,
                actor=actor,
                task_id=updated.task_id,
                attempt_id=updated.attempt_id,
                completion_id=updated.completion_id,
                reason="waiting_dependency_provided",
                payload={
                    "wait_token_id": wait_token_id,
                    "answer_ref": updated.answer_ref,
                    "resume_run_id": updated.resume_run_id,
                },
                provenance=updated.provenance,
            )
            self._insert_decision(conn, decision)
        return updated

    def resolve_wait(self, wait_token_id: str, *, status: str = "resolved") -> WaitToken:
        if status not in {"resolved", "cancelled", "superseded"}:
            raise ValueError(status)
        now = utc_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM wait_tokens_v2 WHERE wait_token_id=?",
                (wait_token_id,),
            ).fetchone()
            if row is None:
                raise KeyError(wait_token_id)
            token = WaitToken.from_dict(json_loads(row["payload_json"], {}))
            updated = replace(token, status=status, updated_at=now)
            conn.execute(
                "UPDATE wait_tokens_v2 SET status=?, updated_at=?, payload_json=? WHERE wait_token_id=?",
                (status, now, json_dumps(updated.to_dict()), wait_token_id),
            )
        return updated

    def assert_worker_available(
        self,
        worker_id: str,
        *,
        supersedes_task_id: str = "",
        actor: str = "coordinator",
        reason: str = "explicit_new_dispatch_superseded_waiting_task",
    ) -> None:
        # WAITING belongs to a task lineage, not to Worker availability.  A new
        # unrelated dispatch is therefore always allowed.  Only an explicit
        # supersedes_task_id changes the matching WAITING CompletionEvent.
        if not supersedes_task_id:
            return
        token = self.active_wait_for_task(supersedes_task_id, worker_id=worker_id)
        if token is None:
            return
        prior = self.get(token.completion_id)
        if prior is None:
            raise CompletionConflict(
                f"active wait token {token.wait_token_id} has no CompletionEvent"
            )
        decision = DecisionEvent(
            decision_id=_stable_id(
                "decision",
                DecisionType.SUPERSEDE_REQUESTED.value,
                prior.completion_id,
                actor,
                reason,
            ),
            decision_type=DecisionType.SUPERSEDE_REQUESTED,
            project_id=prior.project_id,
            actor=actor,
            task_id=prior.task_id,
            attempt_id=prior.attempt_id,
            completion_id=prior.completion_id,
            reason=reason,
            payload={
                "wait_token_id": token.wait_token_id,
                "superseded_by_dispatch": True,
            },
            provenance=dict(prior.provenance),
        )
        self.transition_with_decision(
            prior,
            CompletionStatus.SUPERSEDED,
            decision,
            actor=actor,
            reason=reason,
            metadata={"superseded_by_dispatch": True},
        )

    def reconcile_terminal_runs(self) -> dict[str, Any]:
        """Repair result-bearing TaskRuns that are missing a CompletionEvent.

        New rows store only IDLE/RUNNING in the runtime state column. Older serialized
        rows remain readable because ``TaskRun.from_dict`` moves their outcome into the
        CompletionEvent status metadata before this scanner evaluates them.
        """

        repaired: list[str] = []
        already_consistent: list[str] = []
        downgraded_orphans: list[str] = []
        with self.db.transaction(immediate=False) as conn:
            self._ensure_runtime_tables(conn)
            rows = conn.execute(
                "SELECT payload_json FROM task_runs ORDER BY updated_at, task_id"
            ).fetchall()

        candidates: list[TaskRun] = []
        for row in rows:
            run = TaskRun.from_dict(json_loads(row["payload_json"], {}))
            if run.stopped:
                candidates.append(run)

        for run in candidates:
            attempt_id = _attempt_id(run)
            if self.active_for(run.envelope.task_id, attempt_id) is not None:
                already_consistent.append(run.envelope.task_id)
                continue
            status = CompletionStatus.from_run(run)
            if status is CompletionStatus.SUCCEEDED:
                with self.db.transaction(immediate=False) as conn:
                    delivery_row = conn.execute(
                        "SELECT payload_json FROM deliveries WHERE task_id=? AND run_id=? LIMIT 1",
                        (run.envelope.task_id, run.run_id),
                    ).fetchone()
                if delivery_row is not None:
                    record = DeliveryRecord.from_dict(
                        json_loads(delivery_row["payload_json"], {})
                    )
                    self.commit_success(run, record)
                    repaired.append(run.envelope.task_id)
                    continue
                # A submitted result without a durable DeliveryRecord is not success.
                orphan = run.clone()
                orphan.terminal_reason = "orphan_submission_without_delivery"
                orphan.set_completion_status(CompletionStatus.FAILED.value)
                self.commit_run(
                    orphan,
                    status=CompletionStatus.FAILED,
                    metadata={
                        "reconciled_from_orphan_result": True,
                        "original_completion_status": CompletionStatus.SUCCEEDED.value,
                    },
                )
                repaired.append(run.envelope.task_id)
                downgraded_orphans.append(run.envelope.task_id)
                continue
            self.commit_run(
                run,
                status=status,
                metadata={"reconciled_from_result_task_run": True},
            )
            repaired.append(run.envelope.task_id)
        return {
            "scanned": len(candidates),
            "repaired": repaired,
            "already_consistent": already_consistent,
            "downgraded_orphans": downgraded_orphans,
        }

    def _insert_decision(self, conn: Any, event: DecisionEvent) -> DecisionEvent:
        existing = conn.execute(
            "SELECT payload_json FROM decision_events_v2 WHERE decision_id=?",
            (event.decision_id,),
        ).fetchone()
        if existing is not None:
            return DecisionEvent.from_dict(json_loads(existing["payload_json"], {}))
        provenance = provenance_sql_values(event.provenance)
        conn.execute(
            """
            INSERT INTO decision_events_v2(
                decision_id, decision_type, project_id, actor, task_id,
                attempt_id, completion_id, created_at, payload_json,
                provenance_json, build_id, git_commit, runtime_schema_version,
                harness_schema_version, prompt_bundle_version, migration_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.decision_id,
                event.decision_type.value,
                event.project_id,
                event.actor,
                event.task_id,
                event.attempt_id,
                event.completion_id,
                event.created_at,
                json_dumps(event.to_dict()),
                *provenance,
            ),
        )
        return event

    def record_decision(self, event: DecisionEvent) -> DecisionEvent:
        with self.db.transaction() as conn:
            return self._insert_decision(conn, event)

    def decisions_for_task(self, task_id: str) -> tuple[DecisionEvent, ...]:
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM decision_events_v2 WHERE task_id=? ORDER BY created_at, decision_id",
                (task_id,),
            ).fetchall()
        return tuple(DecisionEvent.from_dict(json_loads(row["payload_json"], {})) for row in rows)

    def list_decisions(
        self,
        *,
        project_id: str = "",
        completion_id: str = "",
        task_id: str = "",
    ) -> tuple[DecisionEvent, ...]:
        query = "SELECT payload_json FROM decision_events_v2"
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if completion_id:
            clauses.append("completion_id=?")
            params.append(completion_id)
        if task_id:
            clauses.append("task_id=?")
            params.append(task_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, decision_id"
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return tuple(
            DecisionEvent.from_dict(json_loads(row["payload_json"], {}))
            for row in rows
        )

    def archive_run_for_resume(self, run: TaskRun, *, reason: str = "waiting_resume") -> str:
        history_id = _stable_id("runhist_", run.envelope.task_id, _attempt_id(run), run.run_id, reason)
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO task_run_history_v2(
                    history_id, task_id, attempt_id, run_id, reason, payload_json, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    run.envelope.task_id,
                    _attempt_id(run),
                    run.run_id,
                    reason,
                    json_dumps(run.to_dict()),
                    utc_now(),
                ),
            )
        return history_id

    def orphan_summary(self) -> dict[str, Any]:
        with self.db.transaction(immediate=False) as conn:
            self._ensure_runtime_tables(conn)
            open_waits = int(conn.execute(
                "SELECT COUNT(*) AS n FROM wait_tokens_v2 WHERE status IN ('open','resuming')"
            ).fetchone()["n"])
            active_completions = int(conn.execute(
                "SELECT COUNT(*) AS n FROM completion_events_v2 WHERE active=1"
            ).fetchone()["n"])
            pending_outbox = int(conn.execute(
                "SELECT COUNT(*) AS n FROM completion_outbox_v2 WHERE state!='delivered'"
            ).fetchone()["n"])
            orphan_waits = int(conn.execute(
                """
                SELECT COUNT(*) AS n FROM wait_tokens_v2 w
                 LEFT JOIN completion_events_v2 c ON c.completion_id=w.completion_id
                 WHERE w.status IN ('open','resuming') AND (c.completion_id IS NULL OR c.active=0)
                """
            ).fetchone()["n"])
            run_rows = conn.execute(
                "SELECT task_id, payload_json FROM task_runs"
            ).fetchall()
            active_task_ids = {
                str(row["task_id"])
                for row in conn.execute(
                    "SELECT DISTINCT task_id FROM completion_events_v2 WHERE active=1"
                ).fetchall()
            }
            active_without_run = int(conn.execute(
                """
                SELECT COUNT(*) AS n
                  FROM completion_events_v2 c
             LEFT JOIN task_runs t ON t.task_id=c.task_id
                 WHERE c.active=1 AND t.task_id IS NULL
                """
            ).fetchone()["n"])

        result_runs = [
            (str(row["task_id"]), TaskRun.from_dict(json_loads(row["payload_json"], {})))
            for row in run_rows
        ]
        terminal_without_completion = sum(
            1
            for task_id, run in result_runs
            if run.stopped and task_id not in active_task_ids
        )
        return {
            "open_wait_tokens": open_waits,
            "active_completions": active_completions,
            "pending_outbox": pending_outbox,
            "orphan_wait_tokens": orphan_waits,
            "terminal_runs_without_completion": terminal_without_completion,
            "active_completions_without_task_run": active_without_run,
            "consistent": (
                orphan_waits == 0
                and terminal_without_completion == 0
                and active_without_run == 0
            ),
            "fully_projected": pending_outbox == 0,
        }


class CompletionAwareTaskRunRepository:
    """TaskRun repository adapter for atomic-finalization replay and same-lineage resume."""

    def __init__(self, path: str | Path | SQLiteDatabase, store: SQLiteCompletionStore | None = None) -> None:
        self._owns_store = store is None
        self._closed = False
        self.store = store or SQLiteCompletionStore(path)
        self.delegate = SQLiteTaskRunRepository(self.store.db)
        self.db = self.delegate.db

    def close(self) -> None:
        """Close a store created by this adapter; never close a caller-owned store."""
        if self._closed:
            return
        if self._owns_store:
            self.store.close()
        self._closed = True

    def __enter__(self) -> "CompletionAwareTaskRunRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def create(self, run: TaskRun) -> TaskRun:
        try:
            # Persist the immutable execution input and the QUEUED -> STARTED
            # Task Journal transition in one transaction.
            return self.store.create_started_run(run)
        except TaskRunAlreadyExists:
            token_id = str(run.envelope.metadata.get("resume_wait_token_id") or "")
            if not token_id:
                raise
            token = self.store.get_wait_token(token_id)
            if token is None or token.status != "resuming":
                raise
            if token.task_id != run.envelope.task_id or token.attempt_id != _attempt_id(run):
                raise CompletionConflict("resume TaskEnvelope changed task/attempt lineage")
            existing = self.delegate.get(run.envelope.task_id)
            if existing is None:
                return self.store.create_started_run(run)
            if CompletionStatus.from_run(existing) is not CompletionStatus.WAITING:
                raise CompletionConflict("only a WAITING task result can resume in-place")
            self.store.archive_run_for_resume(existing)
            provenance = provenance_sql_values(run.provenance)
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE task_runs
                       SET run_id=?, version=?, state=?, payload_json=?, created_at=?, updated_at=?,
                           provenance_json=?, build_id=?, git_commit=?, runtime_schema_version=?,
                           harness_schema_version=?, prompt_bundle_version=?, migration_epoch=?
                     WHERE task_id=? AND run_id=?
                    """,
                    (
                        run.run_id,
                        run.version,
                        run.state.value,
                        json_dumps(run.to_dict()),
                        run.started_at,
                        run.updated_at,
                        *provenance,
                        run.envelope.task_id,
                        existing.run_id,
                    ),
                )
            self.store.mark_started(run)
            return run

    def save(self, run: TaskRun, *, expected_version: int) -> TaskRun:
        # Runtime result stops are represented by an IDLE Worker plus an explicit
        # CompletionEvent status. Successful records are committed by commit_success().
        selected: CompletionStatus | None = None
        if run.completion_status:
            selected = CompletionStatus.from_run(run)
        if run.state is TaskState.IDLE and selected is not None and selected is not CompletionStatus.SUCCEEDED:
            self.store.commit_run(run, status=selected)
            return run
        try:
            stored = self.delegate.save(run, expected_version=expected_version)
            if run.state is TaskState.RUNNING:
                self.store.record_progress(run)
            return stored
        except OptimisticLockError:
            stored = self.delegate.get(run.envelope.task_id)
            if stored is None or stored.run_id != run.run_id:
                raise
            if (
                stored.version == run.version
                and stored.state is TaskState.IDLE
                and run.state is TaskState.IDLE
                and stored.completion_status == CompletionStatus.SUCCEEDED.value
                and run.completion_status == CompletionStatus.SUCCEEDED.value
            ):
                # Success stored the IDLE result snapshot before later projections.
                provenance = provenance_sql_values(run.provenance)
                with self.db.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE task_runs
                           SET state=?, payload_json=?, updated_at=?,
                               provenance_json=?, build_id=?, git_commit=?, runtime_schema_version=?,
                               harness_schema_version=?, prompt_bundle_version=?, migration_epoch=?
                         WHERE task_id=? AND run_id=? AND version=?
                        """,
                        (
                            run.state.value,
                            json_dumps(run.to_dict()),
                            run.updated_at,
                            *provenance,
                            run.envelope.task_id,
                            run.run_id,
                            run.version,
                        ),
                    )
                return run
            raise

    def get(self, task_id: str) -> TaskRun | None:
        return self.delegate.get(task_id)

    def get_by_run_id(self, run_id: str) -> TaskRun | None:
        return self.delegate.get_by_run_id(run_id)

    def list_by_state(self, state: str, *, limit: int = 100) -> tuple[TaskRun, ...]:
        return self.delegate.list_by_state(state, limit=limit)


class CompletionCommitter:
    """Harness API used after non-delivery Runtime stops and by coordinator decisions."""

    def __init__(self, store: SQLiteCompletionStore) -> None:
        self.store = store

    def commit_runtime_stop(self, run: TaskRun) -> CompletionEvent:
        return self.store.commit_run(run)

    def decide(
        self,
        completion_id: str,
        action: CoordinatorAction | str,
        *,
        actor: str = "coordinator",
        reason: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[CompletionEvent | None, DecisionEvent]:
        prior = self.store.get(completion_id)
        if prior is None:
            raise KeyError(completion_id)
        action = action if isinstance(action, CoordinatorAction) else CoordinatorAction(str(action))
        data = dict(payload or {})
        decision_type: DecisionType
        transition_status: CompletionStatus | None = None
        transition_reason = reason
        transition_metadata: dict[str, Any] = dict(data)
        rollback_of = ""

        if action is CoordinatorAction.PROVIDE_DEPENDENCY:
            if prior.status not in {CompletionStatus.WAITING, CompletionStatus.BLOCKED}:
                raise InvalidCoordinatorDecision("provide_dependency requires WAITING or BLOCKED")
            decision_type = DecisionType.DEPENDENCY_PROVIDED
        elif action is CoordinatorAction.CANCEL:
            decision_type = DecisionType.TASK_CANCELLED
            transition_status = CompletionStatus.CANCELLED
            transition_reason = reason or "cancelled"
        elif action is CoordinatorAction.ACCEPT_PARTIAL:
            if prior.status not in {CompletionStatus.FAILED, CompletionStatus.BLOCKED, CompletionStatus.PARTIAL}:
                raise InvalidCoordinatorDecision("accept_partial requires FAILED/BLOCKED/PARTIAL")
            if not reason.strip():
                raise InvalidCoordinatorDecision("accept_partial requires an explicit reason")
            decision_type = DecisionType.PARTIAL_ACCEPTED
            transition_status = CompletionStatus.PARTIAL
        elif action is CoordinatorAction.RETRY:
            if prior.status is CompletionStatus.SUCCEEDED:
                raise InvalidCoordinatorDecision("cannot retry a succeeded completion without rejection")
            decision_type = DecisionType.RETRY_REQUESTED
        elif action is CoordinatorAction.REJECT:
            decision_type = DecisionType.DELIVERY_REJECTED
            # Rejection is terminal for the current attempt.  Leaving the prior FAILED
            # or PARTIAL completion active makes the task look retryable even though the
            # acceptance owner explicitly ended this delivery.  A new retry, when
            # desired, must be an explicit later decision with fresh lineage.
            transition_status = CompletionStatus.CANCELLED
            transition_reason = reason or "delivery_rejected"
            transition_metadata.setdefault("rejected", True)
        elif action is CoordinatorAction.ROLLBACK:
            decision_type = DecisionType.ROLLBACK_REQUESTED
            transition_status = CompletionStatus.ROLLED_BACK
            transition_reason = reason or "rolled_back"
            rollback_of = prior.completion_id
        elif action is CoordinatorAction.SUPERSEDE:
            decision_type = DecisionType.SUPERSEDE_REQUESTED
            transition_status = CompletionStatus.SUPERSEDED
            transition_reason = reason or "superseded"
        else:  # pragma: no cover - Enum is exhaustive
            raise InvalidCoordinatorDecision(str(action))

        decision = DecisionEvent(
            decision_id=_stable_id("dec_", prior.completion_id, action.value, actor, reason, data),
            decision_type=decision_type,
            project_id=prior.project_id,
            actor=actor,
            task_id=prior.task_id,
            attempt_id=prior.attempt_id,
            completion_id=prior.completion_id,
            reason=reason,
            payload={"action": action.value, **data},
            provenance=prior.provenance,
        )
        if transition_status is None:
            return None, self.store.record_decision(decision)

        next_event, stored_decision = self.store.transition_with_decision(
            prior,
            transition_status,
            decision,
            actor=actor,
            reason=transition_reason,
            metadata=transition_metadata,
            rollback_of_completion_id=rollback_of,
        )
        return next_event, stored_decision


def classify_user_decision(text: str, *, explicit_control_action: str = "") -> DecisionType | None:
    """Conservative classifier: conversational continuation is never delivery acceptance."""

    action = str(explicit_control_action or "").strip().lower()
    explicit = {
        "approve_plan": DecisionType.PLAN_APPROVED,
        "dispatch": DecisionType.TASK_DISPATCHED,
        "accept_delivery": DecisionType.DELIVERY_ACCEPTED,
        "reject_delivery": DecisionType.DELIVERY_REJECTED,
    }
    if action in explicit:
        return explicit[action]
    normalized = "".join(str(text or "").split()).lower()
    if not normalized:
        return None
    # These phrases continue the conversation or authorize work; they do not attest output.
    if normalized in {"然后呢", "继续", "继续做", "接着做", "可以", "好", "好的", "开始吧"}:
        return None
    return None


__all__ = [
    "CompletionAwareTaskRunRepository",
    "CompletionCommitter",
    "CompletionConflict",
    "CompletionEvent",
    "CompletionStatus",
    "CoordinatorAction",
    "DecisionEvent",
    "DecisionType",
    "InvalidCoordinatorDecision",
    "OutboxEntry",
    "OutboxState",
    "ProjectionKind",
    "SQLiteCompletionStore",
    "TaskJournalEntryV1",
    "TaskResultV1",
    "WaitToken",
    "classify_user_decision",
]
