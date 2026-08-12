"""Thin projections for authoritative Runtime and completion records.

This module never decides whether work succeeded.  It accepts only structured outcome
metadata produced by the Runtime or completion store, then normalizes that already-made
decision for memory, replay, and the user-facing CompletionView payload.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from knowe_harness.completion import CompletionStatus, completion_policy

__all__ = [
    "RUNTIME_DELIVERABLE_STATES",
    "ArtifactV1",
    "CompletionViewV1",
    "UserFacingCompletionV1",
    "WorkerCompletion",
    "completion_from_mapping",
    "completion_from_message",
    "build_user_facing_completion",
    "format_completion",
    "has_runtime_outcome_metadata",
]

RUNTIME_DELIVERABLE_STATES = frozenset(status.value for status in CompletionStatus)

# Each nested key names a typed boundary.  A generic top-level ``state`` remains
# intentionally insufficient because chat/UI objects use that word for many purposes.
_BOUNDARY_KEYS = (
    "worker_runtime",
    "_worker_runtime",
    "runtime_completion",
    "runtime_result",
    "delivery_record",
    "task_run",
    "completion_event",
)
_OUTCOME_MARKERS = frozenset({
    "completion_status",
    "runtime_state",
    "task_state",
    "terminal_reason",
    "waiting_question",
    "dependency",
    "delivery_id",
    "run_id",
    "submission_committed",
})


def _state_value(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip().upper()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value in (None, "", (), [], {}):
        return ""
    return str(value).strip()


def _first(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(data.get(key))
        if value:
            return value
    return ""


def _metadata(data: Mapping[str, Any]) -> Mapping[str, Any]:
    value = data.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _candidate_mappings(value: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    rows: list[tuple[str, Mapping[str, Any]]] = []
    for key in _BOUNDARY_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            rows.append((key, candidate))
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        for key in _BOUNDARY_KEYS:
            candidate = metadata.get(key)
            if isinstance(candidate, Mapping):
                rows.append((f"metadata.{key}", candidate))
        if _OUTCOME_MARKERS.intersection(metadata):
            rows.append(("metadata", metadata))
    if _OUTCOME_MARKERS.intersection(value):
        rows.append(("mapping", value))
    return tuple(rows)


def _candidate_state(data: Mapping[str, Any], *, nested: bool) -> str:
    metadata = _metadata(data)
    state = _state_value(
        data.get("completion_status")
        or metadata.get("completion_status")
        or data.get("runtime_status")
        or data.get("status")
    )
    if state in RUNTIME_DELIVERABLE_STATES:
        return state
    # Typed task/run records commonly store execution state separately and the terminal
    # outcome in metadata.  Plain ``state`` is considered only inside a typed boundary.
    if nested:
        state = _state_value(data.get("state"))
        if state in RUNTIME_DELIVERABLE_STATES:
            return state
    return ""


@dataclass(frozen=True, slots=True)
class WorkerCompletion:
    """A structured Runtime outcome for compact memory/context rendering."""

    state: str
    text: str
    terminal_reason: str = ""
    dependency: str = ""
    source: str = "runtime"

    def __post_init__(self) -> None:
        normalized = _state_value(self.state)
        try:
            CompletionStatus(normalized)
        except ValueError as exc:
            raise ValueError(f"unknown Runtime completion status: {self.state!r}") from exc
        object.__setattr__(self, "state", normalized)

    @property
    def is_submitted_final(self) -> bool:
        return self.state == CompletionStatus.SUCCEEDED.value


def _completion_from_candidate(
    data: Mapping[str, Any],
    *,
    source: str,
    fallback_text: str,
) -> WorkerCompletion | None:
    state = _candidate_state(data, nested=source != "mapping")
    if state not in RUNTIME_DELIVERABLE_STATES:
        return None

    metadata = _metadata(data)
    terminal_reason = _first(data, "terminal_reason", "reason") or _first(
        metadata, "terminal_reason", "reason"
    )
    dependency = _first(data, "dependency") or _first(metadata, "dependency")
    if state == "WAITING":
        output = _first(data, "waiting_question", "question", "text", "message")
    elif state == "BLOCKED":
        output = _first(
            data,
            "text",
            "summary",
            "final_candidate",
            "message",
            "dependency",
            "terminal_reason",
            "reason",
        )
    else:
        output = _first(
            data,
            "text",
            "summary",
            "delivery_text",
            "final_candidate",
            "final_response",
            "message",
        )
    output = output or _first(metadata, "text", "summary", "user_summary") or fallback_text
    if not output:
        output = dependency or terminal_reason
    if not output:
        return None
    return WorkerCompletion(
        state=state,
        text=output,
        terminal_reason=terminal_reason,
        dependency=dependency,
        source=source,
    )


def completion_from_mapping(
    value: Mapping[str, Any] | None,
    *,
    fallback_text: str = "",
) -> WorkerCompletion | None:
    """Project an explicit structured outcome; never infer one from ordinary text."""

    if not isinstance(value, Mapping):
        return None
    for source, candidate in _candidate_mappings(value):
        completion = _completion_from_candidate(
            candidate,
            source=source,
            fallback_text=fallback_text,
        )
        if completion is not None:
            return completion
    return None


def has_runtime_outcome_metadata(value: Mapping[str, Any] | None) -> bool:
    """Return whether a mapping contains an explicit typed outcome boundary."""

    return isinstance(value, Mapping) and bool(_candidate_mappings(value))


def completion_from_message(message: Mapping[str, Any] | None) -> WorkerCompletion | None:
    """Project structured metadata attached to a message; message text is not a protocol."""

    if not isinstance(message, Mapping):
        return None
    return completion_from_mapping(message, fallback_text=_text(message.get("content")))


def format_completion(completion: WorkerCompletion) -> str:
    """Render a legal status without exposing internal enum identifiers."""

    policy = completion_policy(CompletionStatus(completion.state))
    return f"【{policy.user_label}】{completion.text}".strip()


def _verbatim_text(value: Any) -> str:
    """Coerce one authoritative text field without rewriting its language."""

    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _verbatim_texts(value: Any) -> tuple[str, ...]:
    """Preserve order, duplicates, whitespace, and wording of authoritative text rows."""

    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (bytes, bytearray)):
        return (value.decode("utf-8", errors="replace"),)
    if not isinstance(value, Sequence):
        return (str(value),)
    return tuple(_verbatim_text(item) for item in value if item is not None)


@dataclass(frozen=True, slots=True)
class ArtifactV1:
    """A minimal, already-verified project file card."""

    path: str
    name: str
    sha256: str = ""
    size: int = 0

    @classmethod
    def from_unknown(cls, value: Any) -> "ArtifactV1 | None":
        if isinstance(value, Mapping):
            raw = value
        else:
            raw = {
                key: getattr(value, key, None)
                for key in ("path", "name", "sha256", "size", "bytes")
            }
        raw_path = str(raw.get("path") or "").replace("\\", "/").strip()
        if (
            not raw_path
            or raw_path.startswith(("/", "../"))
            or re.match(r"^[A-Za-z]:/", raw_path)
            or "://" in raw_path
            or "/../" in f"/{raw_path}/"
        ):
            return None
        path = raw_path.strip("/")
        name = _verbatim_text(raw.get("name") or path.rsplit("/", 1)[-1])
        if not name.strip():
            return None
        sha256 = str(raw.get("sha256") or "").strip().lower()
        if sha256 and not re.fullmatch(r"[a-f0-9]{64}", sha256):
            sha256 = ""
        try:
            size = max(0, int(raw.get("size", raw.get("bytes", 0)) or 0))
        except (TypeError, ValueError):
            size = 0
        return cls(path=path, name=name, sha256=sha256, size=size)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        if not self.sha256:
            row.pop("sha256")
        if not self.size:
            row.pop("size")
        return row


@dataclass(frozen=True, slots=True)
class UserFacingCompletionV1:
    """Normalized facts used by the UI and Coordinator review projection."""

    summary: str
    artifacts: tuple[ArtifactV1, ...] = ()
    verification: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "verification": list(self.verification),
            "risks": list(self.risks),
            "gaps": list(self.gaps),
            "next_actions": list(self.next_actions),
        }


@dataclass(frozen=True, slots=True)
class CompletionViewV1:
    """One atomic, replay-safe user projection for a completion version."""

    event_id: str
    completion_id: str
    task_id: str
    attempt_id: str
    agent_id: str
    version: int
    status: str
    terminal: bool
    user_visible: UserFacingCompletionV1
    rendered_text: str
    created_at: str
    run_id: str = ""
    scope_id: str = ""
    delivery: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None
    reasoning: str = ""   # [v1.0.23.5] worker 推理全文（view_v1 是权威投影，必须自带推理）
    reasoning_seconds: float | None = None   # [v1.0.23.6] 推理耗时（秒）

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "completion_view_v1",
            "event_id": self.event_id,
            "completion_id": self.completion_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "agent_id": self.agent_id,
            "version": max(1, int(self.version)),
            "status": self.status,
            "terminal": bool(self.terminal),
            "user_visible": self.user_visible.to_dict(),
            "rendered_text": self.rendered_text,
            "created_at": self.created_at,
        }
        if self.reasoning:
            payload["reasoning"] = self.reasoning
        if self.reasoning_seconds is not None:
            payload["reasoning_seconds"] = self.reasoning_seconds
        if self.run_id:
            payload["run_id"] = self.run_id
        if self.scope_id:
            payload["scope_id"] = self.scope_id
        if self.delivery:
            payload["delivery"] = dict(self.delivery)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def build_user_facing_completion(
    *,
    status: str,
    summary: Any,
    artifacts: Iterable[Any] = (),
    verification: Any = (),
    risks: Any = (),
    gaps: Any = (),
    next_actions: Any = (),
    fallback_summary: str = "",
) -> UserFacingCompletionV1:
    """Normalize an authoritative outcome without changing its status."""

    normalized_status = _state_value(status)
    if not normalized_status:
        raise ValueError("completion status is required")
    try:
        status_value = CompletionStatus(normalized_status)
    except ValueError as exc:
        raise ValueError(f"unknown completion status: {status!r}") from exc
    del fallback_summary
    # Runtime/CompletionStore fields are authoritative.  Do not replace terminology,
    # remove clauses, collapse whitespace, deduplicate rows, or synthesize a fallback.
    raw_summary = _verbatim_text(summary)

    parsed_artifacts: list[ArtifactV1] = []
    seen_artifacts: set[tuple[str, str]] = set()
    for raw in artifacts:
        artifact = ArtifactV1.from_unknown(raw)
        if artifact is None:
            continue
        key = (artifact.path.casefold(), artifact.sha256)
        if key in seen_artifacts:
            continue
        seen_artifacts.add(key)
        parsed_artifacts.append(artifact)

    return UserFacingCompletionV1(
        summary=raw_summary,
        artifacts=tuple(parsed_artifacts),
        verification=_verbatim_texts(verification),
        risks=_verbatim_texts(risks),
        gaps=_verbatim_texts(gaps),
        next_actions=_verbatim_texts(next_actions),
    )

