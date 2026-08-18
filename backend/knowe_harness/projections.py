from __future__ import annotations

"""Wave 5-7 idempotent CompletionEvent projection consumers.

Every business-facing effect in this module is derived from one persisted
CompletionEvent.  Consumers are replay-safe: the transactional outbox invokes them at
least once, while deterministic effect ids and projection receipts make the visible
business effect exactly once.
"""

import hashlib
import inspect
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from knowe_storage._sqlite import json_dumps
from backend.runtime import DeliveryAudience, DeliveryRecord, TaskEnvelope, utc_now

from .completion import (
    CompletionEvent,
    CompletionStatus,
    completion_policy,
    completion_scope_id,
    OutboxEntry,
    OutboxState,
    ProjectionKind,
    SQLiteCompletionStore,
)



class ProjectionFaultInjector(Protocol):
    def __call__(
        self,
        stage: str,
        entry: OutboxEntry,
        event: CompletionEvent,
    ) -> Any: ...


@dataclass(frozen=True)
class ProjectionEffect:
    effect_ref: str
    payload: dict[str, Any]


_PRIORITY = {
    ProjectionKind.TASK_STATE: 10,
    ProjectionKind.REPORT: 20,
    ProjectionKind.COORDINATOR: 40,
    ProjectionKind.MEMORY: 50,
    ProjectionKind.KNOWLEDGE: 60,
    ProjectionKind.DELIVERY: 70,
    ProjectionKind.UI: 80,
}

_REPORT_STEP_RE = re.compile(r"(?:instruction|report)-(\d{2,})-")


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _event_summary(event: CompletionEvent) -> str:
    """Project one legal status without leaking enum names as user copy."""

    record = event.delivery_record or {}
    text = " ".join(str(record.get("text") or "").split())
    if text:
        return text
    if event.status is CompletionStatus.WAITING and event.question:
        return event.question
    if event.status is CompletionStatus.BLOCKED and event.dependency:
        return event.dependency
    return event.terminal_reason or completion_policy(event.status).projection_summary


def _status_author(event: CompletionEvent) -> str:
    value = str(event.delivery_intent.get("author") or "").strip()
    if value:
        return value
    return "worker" if event.status is CompletionStatus.SUCCEEDED else "harness"


def _maybe_await(value: Any) -> Awaitable[Any] | None:
    return value if inspect.isawaitable(value) else None


class CompletionProjector:
    """Replay durable CompletionEvent outbox intents into Harness projections."""

    def __init__(
        self,
        store: SQLiteCompletionStore,
        engine: Any,
        *,
        fault_injector: ProjectionFaultInjector | None = None,
        **_removed_trust_options: Any,
    ) -> None:
        self.store: SQLiteCompletionStore | None = store
        self.engine: Any | None = engine
        self.fault_injector = fault_injector
        self._closed = False

    def _ensure_open(self) -> tuple[SQLiteCompletionStore, Any]:
        store = self.store
        engine = self.engine
        if self._closed or store is None or engine is None:
            raise RuntimeError("CompletionProjector 已关闭")
        return store, engine

    def close(self) -> None:
        """Detach Engine/Store references after every projection task has joined."""
        if self._closed:
            return
        self._closed = True
        self.fault_injector = None
        self.store = None
        self.engine = None

    @property
    def internal_workspace(self) -> Path:
        _store, engine = self._ensure_open()
        return Path(getattr(engine, "internal_workspace")).expanduser().resolve()

    async def _fault(self, stage: str, entry: OutboxEntry, event: CompletionEvent) -> None:
        if self.fault_injector is None:
            return
        value = self.fault_injector(stage, entry, event)
        if inspect.isawaitable(value):
            await value

    def _effect_id(self, entry: OutboxEntry) -> str:
        return _stable_id("effect_", entry.completion_id, entry.projection_kind.value, entry.route_key)

    async def reconcile(self, *, limit: int = 10_000) -> dict[str, Any]:
        """Replay all non-delivered intents.  Runtime side effects are never rerun."""
        self._ensure_open()
        return await self.drain(limit=limit)

    async def drain(
        self,
        *,
        completion_id: str = "",
        limit: int = 10_000,
        raise_errors: bool = False,
    ) -> dict[str, Any]:
        self._ensure_open()
        rows = list(self.store.pending_outbox(limit=limit, include_failed=True))
        if completion_id:
            rows = [row for row in rows if row.completion_id == completion_id]
        rows.sort(key=lambda row: (_PRIORITY.get(row.projection_kind, 999), row.created_at, row.outbox_id))
        result = {"processed": 0, "replayed": 0, "failed": 0, "errors": []}
        for row in rows:
            receipt = self.store.projection_receipt(row.outbox_id)
            if receipt is not None:
                self.store.acknowledge_outbox(
                    row.outbox_id,
                    effect_ref=str(receipt.get("effect_ref") or "replayed"),
                    effect_payload=dict(receipt.get("payload") or {}),
                )
                result["replayed"] += 1
                continue
            claimed = self.store.claim_outbox(row.outbox_id)
            if claimed is None or claimed.state is OutboxState.DELIVERED:
                continue
            event = self.store.get(claimed.completion_id)
            if event is None:
                exc = RuntimeError(f"CompletionEvent missing for outbox {claimed.outbox_id}")
                self.store.fail_outbox(claimed.outbox_id, exc)
                result["failed"] += 1
                result["errors"].append(str(exc))
                if raise_errors:
                    raise exc
                continue
            try:
                effect = await self._project(claimed, event)
                self.store.acknowledge_outbox(
                    claimed.outbox_id,
                    effect_ref=effect.effect_ref,
                    effect_payload=effect.payload,
                )
                await self._fault(
                    f"after_{claimed.projection_kind.value}_projection",
                    claimed,
                    event,
                )
                result["processed"] += 1
            except BaseException as exc:  # fault injection deliberately includes BaseException
                # If a consumer committed its durable effect and then the injected process
                # fault fired, a receipt may already exist.  Preserve that exactly-once fact.
                if self.store.projection_receipt(claimed.outbox_id) is None:
                    try:
                        self.store.fail_outbox(claimed.outbox_id, exc)
                    except Exception:
                        pass
                result["failed"] += 1
                result["errors"].append(f"{claimed.projection_kind.value}: {exc}")
                if raise_errors:
                    raise
        result["decisions"] = self._reconcile_decisions()
        result["pending"] = len(self.store.pending_outbox(limit=limit, include_failed=True))
        result["orphan_summary"] = self.store.orphan_summary()
        return result

    def _reconcile_decisions(self) -> dict[str, Any]:
        """Persist typed Coordinator decisions without extracting secondary facts."""

        root = self.internal_workspace / "runtime" / "memory-projections" / "decisions"
        projected = 0
        for decision in self.store.list_decisions(
            project_id=str(getattr(self.engine, "project_id", "") or "")
        ):
            path = root / f"{decision.decision_id}.json"
            payload = {
                "schema_version": "knowe.harness.decision-journal-projection.v2",
                "decision_id": decision.decision_id,
                "decision_type": decision.decision_type.value,
                "project_id": decision.project_id,
                "task_id": decision.task_id,
                "attempt_id": decision.attempt_id,
                "completion_id": decision.completion_id,
                "actor": decision.actor,
                "reason": decision.reason,
                "payload": dict(decision.payload),
                "provenance": decision.provenance,
            }
            _atomic_json(path, payload)
            projected += 1
        return {"projected": projected, "path": root.as_posix()}

    async def _project(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        # Coordinator decisions (accept-partial / cancel / rollback / supersede) create a
        # new authoritative CompletionEvent for the same task+attempt.  An older outbox
        # row may still be pending when the decision commits.  Replaying that stale row
        # must never overwrite the active report, reopen a task, or re-pollute memory and
        # knowledge with the superseded status.  The historical CompletionEvent remains
        # queryable for audit; only its business projections are suppressed.
        active = self.store.active_for(event.task_id, event.attempt_id)
        if (
            not event.active
            or (active is not None and active.completion_id != event.completion_id)
        ):
            effect_id = self._effect_id(entry)
            return ProjectionEffect(
                f"superseded://{event.completion_id}/{entry.projection_kind.value}",
                {
                    "effect_id": effect_id,
                    "skipped": "inactive_completion",
                    "completion_id": event.completion_id,
                    "active_completion_id": active.completion_id if active else "",
                },
            )
        handlers = {
            ProjectionKind.TASK_STATE: self._task_state,
            ProjectionKind.REPORT: self._report,
            ProjectionKind.COORDINATOR: self._coordinator,
            ProjectionKind.UI: self._ui,
            ProjectionKind.MEMORY: self._memory,
            ProjectionKind.KNOWLEDGE: self._knowledge,
            ProjectionKind.DELIVERY: self._delivery,
        }
        return await handlers[entry.projection_kind](entry, event)

    def _task_envelope_matches(self, event: CompletionEvent) -> tuple[bool, TaskEnvelope | None]:
        rows = getattr(self.engine, "_task_envelopes", None)
        if not isinstance(rows, Mapping):
            return False, None
        envelope = rows.get(event.worker_id)
        if not isinstance(envelope, TaskEnvelope):
            return False, None
        return (
            envelope.task_id == event.task_id
            and envelope.attempt_id == event.attempt_id,
            envelope,
        )

    async def _task_state(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        effect_id = self._effect_id(entry)
        matches, _ = self._task_envelope_matches(event)
        token = (
            self.store.active_wait_for_completion(event.completion_id)
            if event.status is CompletionStatus.WAITING
            else None
        )
        if matches:
            closer = getattr(self.engine, "_close_task_envelope", None)
            if callable(closer):
                closer(event.worker_id)
            else:
                task_rows = getattr(self.engine, "_task_envelopes", None)
                if isinstance(task_rows, dict):
                    task_rows.pop(event.worker_id, None)
                activity = getattr(self.engine, "_workers_with_open_activity", None)
                if isinstance(activity, set):
                    activity.discard(event.worker_id)
        runs = getattr(self.engine, "_worker_runtime_runs", None)
        if isinstance(runs, dict):
            run_repo = getattr(self.engine, "completion_run_for", None)
            if callable(run_repo):
                run = run_repo(event.task_id)
                if run is not None:
                    runs[event.worker_id] = run
        pointer = self.internal_workspace / "runtime" / "completion-projections" / "task-state" / f"{event.completion_id}.json"
        payload = {
            "effect_id": effect_id,
            "completion_id": event.completion_id,
            "task_id": event.task_id,
            "attempt_id": event.attempt_id,
            "worker_id": event.worker_id,
            "status": event.status.value,
            "open": False,
            "wait_token_id": token.wait_token_id if token else "",
            "projected_at": utc_now(),
        }
        _atomic_json(pointer, payload)
        return ProjectionEffect(f"task-state://{pointer}", payload)

    def _existing_report(self, completion_id: str) -> Path | None:
        # [v1.0.24.3] completion_id 只存在于 INT 审计副本（audit/ 树，handoffs/ 树外）。
        #   幂等判定扫 INT：EXT 无 completion_id 字段，扫 EXT 会每次漏判 → 重复写报告。
        handoff = getattr(self.engine, "handoff", None)
        audit_reports = getattr(handoff, "audit_reports", None) if handoff is not None else None
        paths = audit_reports() if callable(audit_reports) else []
        needle = f"completion_id: {completion_id}"
        quoted = f'completion_id: "{completion_id}"'
        for path in paths:
            try:
                head = Path(path).read_text("utf-8", errors="replace")[:8192]
            except OSError:
                continue
            if needle in head or quoted in head:
                return Path(path)
        return None

    @staticmethod
    def _report_status_text(event: CompletionEvent) -> tuple[str, str, str]:
        summary = _event_summary(event)
        issues = "\n".join(event.gaps) or event.dependency or event.terminal_reason or "（无）"
        matches = {
            CompletionStatus.SUCCEEDED: "已完成：执行成员已提交结果。",
            CompletionStatus.PARTIAL: "部分交付已记录，等待总管决定是否接受。",
            CompletionStatus.WAITING: "否：任务保持开放，等待补充信息。",
            CompletionStatus.BLOCKED: "否：外部依赖或能力缺失。",
            CompletionStatus.FAILED: "否：任务未形成可审阅的最终结果。",
            CompletionStatus.CANCELLED: "否：任务已取消。",
            CompletionStatus.SYSTEM_ERROR: "否：系统异常中止了执行。",
            CompletionStatus.TIMED_OUT: "否：执行超过允许时限。",
            CompletionStatus.ROLLED_BACK: "否：该历史结果已回滚。",
            CompletionStatus.SUPERSEDED: "否：该任务已由更新任务接替。",
        }[event.status]
        completed = summary if event.status in {CompletionStatus.SUCCEEDED, CompletionStatus.PARTIAL} else "（无新提交）"
        return completed, matches, issues

    def _report_coordinates(self, event: CompletionEvent) -> tuple[int, str, Path | None, str]:
        meta = event.metadata
        step = int(meta.get("handoff_step") or 0)
        instruction_ref = str(meta.get("instruction_ref") or "")
        if not step:
            match = _REPORT_STEP_RE.search(instruction_ref)
            if match:
                step = int(match.group(1))
        if not step:
            # Stable fallback for true direct-user tasks; it never depends on current
            # directory contents and therefore cannot change during crash recovery.
            step = 100_000 + int(hashlib.sha256(event.task_id.encode()).hexdigest()[:8], 16) % 800_000
        keyword = str(meta.get("handoff_keyword") or meta.get("task_title") or "").strip()
        if not keyword:
            keyword = event.task_id[:12] or "任务"
        # HandoffBook applies filename sanitization; keep the semantic word here.
        phase = str(meta.get("handoff_phase") or "").strip()
        phase_dir: Path | None = None
        handoff = getattr(self.engine, "handoff", None)
        if handoff is not None and phase:
            phase_dir = Path(handoff.root) / phase
        return step, keyword, phase_dir, instruction_ref

    async def _report(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        effect_id = self._effect_id(entry)
        if not bool(event.delivery_intent.get("report_required", True)):
            return ProjectionEffect("report://not-required", {"effect_id": effect_id, "skipped": "not_required"})
        existing = self._existing_report(event.completion_id)
        if existing is not None:
            ref_fn = getattr(self.engine, "handoff_ref", None)
            ref = ref_fn(existing) if callable(ref_fn) else str(existing)
            return ProjectionEffect(ref, {"effect_id": effect_id, "report_ref": ref, "reused": True})

        handoff = getattr(self.engine, "handoff", None)
        if handoff is None:
            raise RuntimeError("engine does not expose a HandoffBook")
        step, keyword, phase_dir, instruction_ref = self._report_coordinates(event)
        report_dir = phase_dir or Path(handoff.current_phase())
        report_dir.mkdir(parents=True, exist_ok=True)
        candidate = report_dir / f"report-{step:02d}-{event.worker_id}-{keyword}.md"
        if candidate.is_file():
            # [v1.0.24.3] completion_id 已移入 INT 审计副本（audit/ 树）：
            #   candidate（EXT）无 completion_id 字段，同名判定改查 INT 同名文件。
            audit_candidate = None
            audit_dir = getattr(handoff, "audit_dir", None)
            if audit_dir is not None:
                audit_candidate = Path(audit_dir) / report_dir.name / (
                    f"report-INT-{step:02d}-{event.worker_id}-{keyword}.md"
                )
            check_path = audit_candidate if (audit_candidate is not None and audit_candidate.is_file()) else candidate
            try:
                existing_head = check_path.read_text("utf-8", errors="replace")[:8192]
            except OSError:
                existing_head = ""
            if (
                f"completion_id: {event.completion_id}" not in existing_head
                and f'completion_id: "{event.completion_id}"' not in existing_head
            ):
                # A retry or later Coordinator decision must not overwrite the earlier
                # audit report at the legacy coordinate.  The first projection keeps the
                # familiar filename; subsequent CompletionEvents receive a stable suffix.
                keyword = f"{keyword}-c{event.completion_id[-8:]}"
        completed, matches, issues = self._report_status_text(event)
        record = event.delivery_record or {}
        delivery_id = str(record.get("delivery_id") or "")
        report_hash = hashlib.sha256(json_dumps(event.to_dict()).encode("utf-8")).hexdigest()
        path = handoff.write_report(
            step=step,
            agent_id=event.worker_id,
            keyword=keyword,
            phase_dir=phase_dir,
            status=event.status.value,
            report_hash=report_hash,
            instruction_ref=Path(instruction_ref).name if instruction_ref and not "://" in instruction_ref else instruction_ref,
            completed_what=completed,
            matches_instruction=matches,
            artifacts=list(event.received_artifacts),
            issues=issues or "（无）",
            self_check="由 CompletionEvent 与 Task Journal 投影；质量判断交由总管。",
            task_id=event.task_id,
            run_id=event.run_id,
            delivery_id=delivery_id,
            completion_id=event.completion_id,
            effect_id=effect_id,
            author=_status_author(event),
            source_kind="worker_submission",
            status_reason=event.terminal_reason,
            gaps=list(event.gaps),
            provenance=event.provenance,
        )
        try:
            handoff.link_report_into_approval(step, path.name, phase_dir=phase_dir)
        except Exception:
            pass
        ref_fn = getattr(self.engine, "handoff_ref", None)
        ref = ref_fn(path) if callable(ref_fn) else str(path)
        # Fault point after the deterministic report is durable but before outbox ack.
        await self._fault("during_report_projection", entry, event)
        return ProjectionEffect(ref, {
            "effect_id": effect_id,
            "report_ref": ref,
            "report_path": str(path),
            "report_hash": report_hash,
            "status": event.status.value,
            "author": _status_author(event),
        })

    def _report_ref_for(self, completion_id: str) -> str:
        existing = self._existing_report(completion_id)
        if existing is None:
            return ""
        ref_fn = getattr(self.engine, "handoff_ref", None)
        return ref_fn(existing) if callable(ref_fn) else str(existing)

    async def _coordinator(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        effect_id = self._effect_id(entry)
        notification_id = _stable_id("coord_", event.completion_id)
        gaps = "\n".join(f"- {gap}" for gap in event.gaps) or "- （无）"
        actions = "、".join(event.next_actions) or "none"
        message = (
            f"【CompletionEvent】{event.worker_id} · {event.task_id} · attempt {event.attempt_id}\n"
            f"status: {event.status.value}\n"
            f"reason: {event.terminal_reason or '（无）'}\n"
            f"report: {self._report_ref_for(event.completion_id) or '（未要求或待恢复）'}\n"
            f"received_artifacts: {', '.join(event.received_artifacts) or '（无）'}\n"
            f"missing_artifacts: {', '.join(event.missing_artifacts) or '（无）'}\n"
            f"gaps:\n{gaps}\n"
            f"next_actions: {actions}\n"
            f"notification_id: {notification_id}"
        )
        # [完整性 · single channel] This harness projection no longer notifies the
        # Coordinator. The Coordinator review is triggered by exactly one channel —
        # the structured ``completion_review`` emitted from
        # engine._emit_completion_visible — deduplicated by (completion_id, version)
        # at the notify_coordinator boundary. Firing a second, free-text notice here
        # is what caused the same delivery to be reviewed twice. We keep the audit
        # pointer below (durability/replay), but send no coordinator turn from here.
        pointer = self.internal_workspace / "runtime" / "completion-projections" / "coordinator" / f"{notification_id}.json"
        payload = {
            "effect_id": effect_id,
            "notification_id": notification_id,
            "completion_id": event.completion_id,
            "status": event.status.value,
            "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
            "created_at": utc_now(),
        }
        _atomic_json(pointer, payload)
        # notify_coordinator persists ``notification_id`` before queueing; replay therefore
        # cannot duplicate the coordinator turn even if the process dies right here.
        await self._fault("during_coordinator_projection", entry, event)
        return ProjectionEffect(f"coordinator://{notification_id}", payload)

    async def _ui(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        effect_id = self._effect_id(entry)
        emit = getattr(self.engine, "emit", None)
        channel = str(event.delivery_intent.get("channel") or "")
        scope_id = completion_scope_id(event.task_id, event.attempt_id)
        correlation = {
            "completion_id": event.completion_id,
            "task_id": event.task_id,
            "attempt_id": event.attempt_id,
            "run_id": event.run_id,
            "scope_id": scope_id,
        }
        payload = {
            "type": "completion_status",
            "event_id": effect_id,
            "agent_id": event.worker_id,
            "status": event.status.value,
            "terminal": event.terminal,
            "reason": event.terminal_reason,
            "gaps": list(event.gaps),
            "next_actions": list(event.next_actions),
            **correlation,
        }

        async def emit_visible(current: Mapping[str, Any]) -> None:
            if not callable(emit):
                return
            try:
                value = emit(dict(current), channel=channel or None)
            except TypeError:
                value = emit(dict(current))
            if inspect.isawaitable(value):
                await value

        if callable(emit):
            # Report visibility is a projection of this exact completion version.  It
            # shares the attempt scope and channel with status/message/idle.
            report = self._existing_report(event.completion_id)
            if report is not None:
                report_hash = hashlib.sha256(json_dumps(event.to_dict()).encode("utf-8")).hexdigest()
                report_event: dict[str, Any] = {
                    "type": "report_submitted",
                    "event_id": effect_id + ":report",
                    "agent_id": event.worker_id,
                    "status": event.status.value,
                    "report_hash": report_hash,
                    **correlation,
                }
                await emit_visible(report_event)
            await emit_visible(payload)
            # Availability is separate from completion, but every Runtime stop closes
            # the exact scope that emitted the Worker activity. A late idle therefore
            # cannot end a newer attempt for the same Worker.
            await emit_visible({
                "type": "agent_idle",
                "event_id": effect_id + ":idle",
                "agent_id": event.worker_id,
                "status": "AVAILABLE",
                "terminal": False,
                "derived": True,
                "derived_from": "worker_run_stopped",
                **correlation,
            })
        return ProjectionEffect(f"ui://{effect_id}", payload)

    async def _memory(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        """Project the raw CompletionEvent into the readable Worker journal."""

        effect_id = self._effect_id(entry)
        root = self.internal_workspace / "runtime" / "memory-projections" / event.worker_id
        projection_path = root / f"{event.completion_id}.json"
        with self.store.db.transaction(immediate=False) as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                  FROM agent_completion_outcomes_v2
                 WHERE project_id=? AND worker_id=?
                 GROUP BY status
                """,
                (event.project_id, event.worker_id),
            ).fetchall()
        counts = {str(row["status"]): int(row["n"]) for row in rows}
        metrics = {
            "reports_completed": counts.get(CompletionStatus.SUCCEEDED.value, 0),
            **{status.value.casefold(): counts.get(status.value, 0) for status in CompletionStatus},
        }
        summary = _event_summary(event)
        payload = {
            "schema_version": "knowe.harness.completion-journal-projection.v5",
            "effect_id": effect_id,
            "completion_id": event.completion_id,
            "task_id": event.task_id,
            "attempt_id": event.attempt_id,
            "worker_id": event.worker_id,
            "status": event.status.value,
            "summary": summary,
            "report_ref": self._report_ref_for(event.completion_id),
            "artifact_manifest": [dict(item) for item in event.artifact_manifest],
            "gaps": list(event.gaps),
            "next_actions": list(event.next_actions),
            "metrics": metrics,
            "provenance": event.provenance,
            "projected_at": utc_now(),
        }
        _atomic_json(projection_path, payload)

        ensure = getattr(self.engine, "_ensure_agent_worklog", None)
        if callable(ensure) and event.worker_id:
            try:
                log_path, identity = ensure(event.worker_id)
                log_path = Path(log_path)
                try:
                    existing = log_path.read_text("utf-8", errors="replace")
                except OSError:
                    existing = ""
                marker = f"completion_id: {event.completion_id}"
                if marker not in existing:
                    lines = [
                        f"## {utc_now()} · CompletionEvent · {event.status.value}",
                        f"- completion_id: {event.completion_id}",
                        f"- task_id: {event.task_id}",
                        f"- attempt_id: {event.attempt_id}",
                        f"- summary: {summary}",
                    ]
                    for item in event.artifact_manifest:
                        path = str(item.get("path") or "").strip()
                        if path:
                            lines.append(f"- artifact: {path}")
                    lines.append("")
                    with open(log_path, "a", encoding="utf-8") as handle:
                        handle.write("\n".join(lines) + "\n")
                state_path = log_path.parent / "state.json"
                try:
                    state = json.loads(state_path.read_text("utf-8")) if state_path.is_file() else {}
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                if "legacy_reports_submitted" not in state and state.get("reports_submitted") is not None:
                    state["legacy_reports_submitted"] = int(state.get("reports_submitted") or 0)
                state.update({
                    "schema_version": 4,
                    "id": identity.get("id", event.worker_id),
                    "name": identity.get("name", event.worker_id),
                    "role": identity.get("role", "worker"),
                    "reports_completed": metrics["reports_completed"],
                    "reports_submitted": metrics["reports_completed"],
                    "completion_metrics": metrics,
                    "last_completion": {
                        "completion_id": event.completion_id,
                        "task_id": event.task_id,
                        "attempt_id": event.attempt_id,
                        "status": event.status.value,
                        "summary": summary,
                        "provenance": event.provenance,
                    },
                    "last_updated": utc_now(),
                    "provenance": event.provenance,
                })
                _atomic_json(state_path, state)
            except Exception:
                pass
        await self._fault("during_memory_projection", entry, event)
        return ProjectionEffect(f"memory://{projection_path}", payload)

    async def _knowledge(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        """Acknowledge pre-upgrade outbox rows without projecting structured knowledge."""

        payload = {
            "effect_id": self._effect_id(entry),
            "completion_id": event.completion_id,
            "skipped": "structured_knowledge_projection_removed",
        }
        return ProjectionEffect(f"removed://knowledge/{event.completion_id}", payload)

    async def _delivery(self, entry: OutboxEntry, event: CompletionEvent) -> ProjectionEffect:
        effect_id = self._effect_id(entry)
        audience = str(event.delivery_intent.get("audience") or DeliveryAudience.USER.value)
        channel = str(event.delivery_intent.get("channel") or "")
        payload = {
            "type": "message",
            "event_id": effect_id,
            "completion_id": event.completion_id,
            "task_id": event.task_id,
            "attempt_id": event.attempt_id,
            "agent_id": event.worker_id,
            "status": event.status.value,
            "content": _event_summary(event),
        }
        # [v1.0.23.5] worker 推理全文随落定消息透传（前端 applyReasoningFields 写入气泡；
        #   流式占位累积的推理不受影响——applyReasoningFields 仅在事件带值时覆盖）
        _reasoning = str(event.metadata.get("reasoning") or "").strip()
        if _reasoning:
            payload["reasoning"] = _reasoning
            # [v1.0.23.6] 推理耗时（秒）——「思考了 Xs」展示
            _rseconds = event.metadata.get("reasoning_seconds")
            if isinstance(_rseconds, (int, float)):
                payload["reasoning_seconds"] = float(_rseconds)
        if event.received_artifacts:
            payload["files"] = [
                {
                    "path": path,
                    "disposition": next(
                        (row.get("disposition") for row in event.artifact_manifest if row.get("path") == path),
                        "unknown",
                    ),
                }
                for path in event.received_artifacts
            ]
        sent = False
        emit = getattr(self.engine, "emit", None)
        if callable(emit):
            try:
                value = emit(payload, channel=channel or None)
            except TypeError:
                value = emit(payload)
            if inspect.isawaitable(value):
                await value
            sent = True
        return ProjectionEffect(
            f"delivery://{audience}/{effect_id}",
            {**payload, "audience": audience, "sent": sent},
        )


__all__ = [
    "CompletionProjector",
    "ProjectionEffect",
    "ProjectionFaultInjector",
]
