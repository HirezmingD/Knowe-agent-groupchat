from __future__ import annotations

"""Minimal Worker runtime.

This module owns the complete Worker attempt: boundary DTOs, one fixed-schema provider
loop, native tool execution, strict completion verification, cancellation/timeout
handling, and attempt-owned resource cleanup.
"""

import asyncio
import contextlib
import copy
import dataclasses
import hashlib
import inspect
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence

from knowe_provenance import current_provenance_dict, normalize_provenance, unknown_legacy_provenance

from knowe_core.errors import ProviderError
from .content_compress import compress_tool_result  # [v1.0.34] 工具结果流压缩


WORKER_TOOL_NAMES: tuple[str, ...] = (
    "safe_read_file",
    "safe_write_file",
    "safe_patch",
    "safe_list_dir",
    "safe_search_files",
    "safe_delete_file",
    "read_external_file",
    "list_external_dir",
    "copy_external_file",
    "safe_bash",
    "web_search",
    "web_extract",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_screenshot",
)

_MUTATION_TOOLS = frozenset(
    {"safe_write_file", "safe_patch", "copy_external_file", "browser_screenshot"}
)
_DELETE_TOOLS = frozenset({"safe_delete_file"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


class TaskState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def available(self) -> bool:
        return self is TaskState.IDLE

    @classmethod
    def coerce(cls, value: "TaskState | str") -> "TaskState":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().upper()
        try:
            return cls(text)
        except ValueError as exc:
            raise ValueError(f"unknown worker state: {value!r}") from exc


class RuntimeStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class OutcomeKind(str, Enum):
    ACTIONS = "ACTIONS"
    FINAL = "FINAL"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"


class DeliveryAudience(str, Enum):
    USER = "user"
    BACKGROUND = "background"


@dataclass(frozen=True)
class ContextReference:
    kind: str
    ref: str
    summary: str = ""
    priority: int = 50
    estimated_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: "ContextReference | str | Mapping[str, Any]") -> "ContextReference":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(kind="reference", ref=value)
        return cls(
            kind=str(value.get("kind") or "reference"),
            ref=str(value.get("ref") or ""),
            summary=str(value.get("summary") or ""),
            priority=int(value.get("priority") or 50),
            estimated_tokens=int(value.get("estimated_tokens") or 0),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    provider_request_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ModelUsage":
        data = dict(value or {})
        prompt = data.get("input_tokens", data.get("prompt_tokens", 0))
        completion = data.get("output_tokens", data.get("completion_tokens", 0))
        details = data.get("completion_tokens_details") or data.get("output_tokens_details") or {}
        cache = data.get("prompt_tokens_details") or data.get("input_tokens_details") or {}
        return cls(
            input_tokens=int(prompt or 0),
            output_tokens=int(completion or 0),
            reasoning_tokens=int(data.get("reasoning_tokens", details.get("reasoning_tokens", 0)) or 0),
            cache_read_tokens=int(data.get("cache_read_tokens", cache.get("cached_tokens", 0)) or 0),
            cache_write_tokens=int(data.get("cache_write_tokens", 0) or 0),
            cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
            provider_request_id=str(data.get("provider_request_id") or data.get("id") or ""),
            raw=data,
        )


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("tool call id and name must be non-empty")
        if not isinstance(self.arguments, dict):
            raise TypeError("tool call arguments must be an object")

    @classmethod
    def from_provider(cls, value: Mapping[str, Any], index: int = 0) -> "ToolCall":
        fn = value.get("function") if isinstance(value.get("function"), Mapping) else value
        name = str((fn or {}).get("name") or value.get("name") or "")
        raw_args = (fn or {}).get("arguments", value.get("arguments", {}))
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args or "{}")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"tool call {name or index} has malformed JSON arguments") from exc
        else:
            parsed = raw_args
        if not isinstance(parsed, dict):
            raise ValueError(f"tool call {name or index} arguments must decode to an object")
        call_id = str(value.get("id") or "")
        if not call_id:
            payload = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
            call_id = "call_" + hashlib.sha256(f"{index}\0{name}\0{payload}".encode()).hexdigest()[:16]
        return cls(call_id, name, parsed)

    def to_provider_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False, separators=(",", ":")),
            },
        }


@dataclass(frozen=True)
class StepOutcome:
    kind: OutcomeKind
    assistant_text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    question: str = ""
    dependency: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: ModelUsage = field(default_factory=ModelUsage)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", self.kind if isinstance(self.kind, OutcomeKind) else OutcomeKind(str(self.kind)))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if self.kind is OutcomeKind.ACTIONS and not self.tool_calls:
            raise ValueError("ACTIONS outcome requires at least one native tool call")
        if self.kind is OutcomeKind.WAITING and not self.question.strip():
            raise ValueError("WAITING outcome requires a question")
        if self.kind is OutcomeKind.BLOCKED and not self.dependency.strip():
            raise ValueError("BLOCKED outcome requires a dependency")

    @classmethod
    def actions(cls, *calls: ToolCall, narration: str = "", **metadata: Any) -> "StepOutcome":
        return cls(OutcomeKind.ACTIONS, assistant_text=narration, tool_calls=tuple(calls), metadata=metadata)

    @classmethod
    def final(cls, text: str, usage: ModelUsage | None = None, **metadata: Any) -> "StepOutcome":
        return cls(OutcomeKind.FINAL, assistant_text=text, metadata=metadata, usage=usage or ModelUsage())

    @classmethod
    def waiting(cls, question: str, dependency: str = "user_input", usage: ModelUsage | None = None, **metadata: Any) -> "StepOutcome":
        return cls(OutcomeKind.WAITING, question=question, dependency=dependency, metadata=metadata, usage=usage or ModelUsage())

    @classmethod
    def blocked(cls, dependency: str, text: str = "", usage: ModelUsage | None = None, **metadata: Any) -> "StepOutcome":
        return cls(OutcomeKind.BLOCKED, assistant_text=text, dependency=dependency, metadata=metadata, usage=usage or ModelUsage())


@dataclass(frozen=True)
class BudgetSpec:
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None = None) -> "BudgetSpec":
        del value
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {}


@dataclass(frozen=True)
class DeliveryTarget:
    audience: DeliveryAudience = DeliveryAudience.USER
    step_id: str = ""
    attempt_id: str = ""
    channel: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "DeliveryTarget":
        data = dict(value or {})
        audience = data.get("audience", DeliveryAudience.USER)
        if not isinstance(audience, DeliveryAudience):
            audience = DeliveryAudience(str(audience))
        return cls(
            audience=audience,
            step_id=str(data.get("step_id") or ""),
            attempt_id=str(data.get("attempt_id") or ""),
            channel=str(data.get("channel") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TaskEnvelope:
    task_id: str
    project_id: str
    goal: str
    worker_id: str
    attempt_id: str
    title: str = ""
    worker_name: str = ""
    worker_role: str = "worker"
    context_refs: tuple[ContextReference, ...] = ()
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    delivery: DeliveryTarget = field(default_factory=DeliveryTarget)
    source: str = "coordinator_instruction"
    scope_root: str = ""
    instruction_ref: str = ""
    created_at: str = field(default_factory=utc_now)
    started_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=current_provenance_dict)

    def __post_init__(self) -> None:
        for name in ("task_id", "project_id", "goal", "worker_id", "attempt_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "context_refs", tuple(ContextReference.from_value(x) for x in self.context_refs))
        if not isinstance(self.budget, BudgetSpec):
            object.__setattr__(self, "budget", BudgetSpec.from_mapping(self.budget if isinstance(self.budget, Mapping) else None))
        if not isinstance(self.delivery, DeliveryTarget):
            object.__setattr__(self, "delivery", DeliveryTarget.from_mapping(self.delivery if isinstance(self.delivery, Mapping) else None))
        object.__setattr__(self, "metadata", copy.deepcopy(dict(self.metadata or {})))
        object.__setattr__(self, "provenance", normalize_provenance(self.provenance).to_dict())

    @property
    def digest(self) -> str:
        raw = json.dumps(self.to_dict(include_digest=False), ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        data = _jsonable(self)
        if include_digest:
            data["envelope_sha256"] = hashlib.sha256(
                json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskEnvelope":
        data = dict(value)
        supplied = str(data.pop("envelope_sha256", "") or "")
        envelope = cls(
            task_id=str(data.get("task_id") or ""),
            project_id=str(data.get("project_id") or ""),
            goal=str(data.get("goal") or ""),
            worker_id=str(data.get("worker_id") or ""),
            attempt_id=str(data.get("attempt_id") or ""),
            title=str(data.get("title") or ""),
            worker_name=str(data.get("worker_name") or ""),
            worker_role=str(data.get("worker_role") or "worker"),
            context_refs=tuple(ContextReference.from_value(x) for x in (data.get("context_refs") or ())),
            budget=BudgetSpec.from_mapping(data.get("budget") if isinstance(data.get("budget"), Mapping) else None),
            delivery=DeliveryTarget.from_mapping(data.get("delivery") if isinstance(data.get("delivery"), Mapping) else None),
            source=str(data.get("source") or "coordinator_instruction"),
            scope_root=str(data.get("scope_root") or ""),
            instruction_ref=str(data.get("instruction_ref") or ""),
            created_at=str(data.get("created_at") or utc_now()),
            started_at=str(data.get("started_at") or data.get("created_at") or utc_now()),
            metadata=dict(data.get("metadata") or {}),
            provenance=normalize_provenance(data.get("provenance"), legacy_if_missing=True).to_dict(),
        )
        if supplied and supplied != envelope.digest:
            raise ValueError("TaskEnvelope digest mismatch")
        return envelope


@dataclass(frozen=True)
class ArtifactFact:
    path: str
    kind: str
    size: int
    sha256: str
    verified: bool = True
    media_type: str = "application/octet-stream"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        path = str(self.path or "").replace("\\", "/").strip()
        while path.startswith("./"):
            path = path[2:]
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("artifact path must be project-relative")
        if self.size < 0 or not re.fullmatch(r"[0-9a-f]{64}", str(self.sha256 or "")):
            raise ValueError("artifact size/hash is invalid")
        object.__setattr__(self, "path", path)


# External products still name delivered files ArtifactRecord. Keep that small boundary
# alias as a concrete type rather than maintaining a second artifact subsystem.
@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    sha256: str
    size: int
    ownership: str = "task"
    media_type: str = "application/octet-stream"
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeletionFact:
    path: str
    verified_absent: bool = True
    size_before: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationFact:
    kind: str
    ok: bool
    detail: str
    path: str = ""


@dataclass(frozen=True)
class BudgetSummary:
    model_calls: int
    tool_calls: int
    tool_failures: int
    subagent_calls: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    elapsed_ms: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "BudgetSummary":
        data = dict(value or {})
        return cls(
            model_calls=int(data.get("model_calls") or 0),
            tool_calls=int(data.get("tool_calls") or 0),
            tool_failures=int(data.get("tool_failures") or 0),
            subagent_calls=int(data.get("subagent_calls") or 0),
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            reasoning_tokens=int(data.get("reasoning_tokens") or 0),
            cache_read_tokens=int(data.get("cache_read_tokens") or 0),
            cache_write_tokens=int(data.get("cache_write_tokens") or 0),
            cost_usd=float(data.get("cost_usd") or 0.0),
            elapsed_ms=int(data.get("elapsed_ms") or 0),
        )


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    idempotency_key: str
    task_id: str
    run_id: str
    project_id: str
    state: TaskState
    text: str
    terminal_reason: str
    artifacts: tuple[ArtifactRecord, ...]
    budget: BudgetSummary
    event_sequence: int
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=current_provenance_dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", normalize_provenance(self.provenance).to_dict())

    @property
    def model_calls(self) -> int:
        return self.budget.model_calls

    @property
    def tool_calls(self) -> int:
        return self.budget.tool_calls

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeliveryRecord":
        metadata = dict(value.get("metadata") or {})
        metadata.setdefault("completion_status", RuntimeStatus.SUCCEEDED.value)
        return cls(
            delivery_id=str(value.get("delivery_id") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            task_id=str(value.get("task_id") or ""),
            run_id=str(value.get("run_id") or ""),
            project_id=str(value.get("project_id") or ""),
            state=TaskState.coerce(value.get("state", TaskState.IDLE)),
            text=str(value.get("text") or ""),
            terminal_reason=str(value.get("terminal_reason") or ""),
            artifacts=tuple(x if isinstance(x, ArtifactRecord) else ArtifactRecord(**x) for x in (value.get("artifacts") or ())),
            budget=BudgetSummary.from_mapping(value.get("budget") if isinstance(value.get("budget"), Mapping) else None),
            event_sequence=int(value.get("event_sequence") or 0),
            created_at=str(value.get("created_at") or utc_now()),
            metadata=metadata,
            provenance=normalize_provenance(value.get("provenance"), legacy_if_missing=True).to_dict(),
        )


@dataclass
class TaskRun:
    envelope: TaskEnvelope
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: TaskState = TaskState.IDLE
    version: int = 0
    event_sequence: int = 0
    final_candidate: str = ""
    targeted_feedback: str = ""
    terminal_reason: str = ""
    correction_signatures: set[str] = field(default_factory=set)
    model_calls: int = 0
    tool_calls: int = 0
    waiting_question: str = ""
    dependency: str = ""
    started_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=current_provenance_dict)

    def __post_init__(self) -> None:
        candidate = self.envelope.metadata.get("provenance")
        self.provenance = normalize_provenance(candidate if isinstance(candidate, Mapping) else self.provenance).to_dict()

    @property
    def completion_status(self) -> str:
        raw = self.metadata.get("completion_status")
        return str(getattr(raw, "value", raw) or "").strip().upper()

    def set_completion_status(self, status: Any) -> str:
        value = str(getattr(status, "value", status) or "").strip().upper()
        if not value:
            raise ValueError("completion status must be non-empty")
        self.metadata["completion_status"] = value
        return value

    @property
    def stopped(self) -> bool:
        return self.state is TaskState.IDLE and bool(self.completion_status)

    def clone(self) -> "TaskRun":
        return copy.deepcopy(self)

    def apply(self, other: "TaskRun") -> None:
        if self.run_id != other.run_id:
            raise ValueError("cannot apply a different task run")
        for f in dataclasses.fields(self):
            setattr(self, f.name, copy.deepcopy(getattr(other, f.name)))

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(self)
        data["correction_signatures"] = sorted(self.correction_signatures)
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskRun":
        envelope = value.get("envelope")
        if not isinstance(envelope, Mapping):
            raise ValueError("task run payload has no envelope")
        return cls(
            envelope=TaskEnvelope.from_dict(envelope),
            run_id=str(value.get("run_id") or uuid.uuid4().hex),
            state=TaskState.coerce(value.get("state", TaskState.IDLE)),
            version=int(value.get("version") or 0),
            event_sequence=int(value.get("event_sequence") or 0),
            final_candidate=str(value.get("final_candidate") or ""),
            targeted_feedback=str(value.get("targeted_feedback") or ""),
            terminal_reason=str(value.get("terminal_reason") or ""),
            correction_signatures=set(value.get("correction_signatures") or ()),
            model_calls=int(value.get("model_calls") or 0),
            tool_calls=int(value.get("tool_calls") or 0),
            waiting_question=str(value.get("waiting_question") or ""),
            dependency=str(value.get("dependency") or ""),
            started_at=str(value.get("started_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
            metadata=dict(value.get("metadata") or {}),
            events=list(value.get("events") or ()),
            provenance=normalize_provenance(value.get("provenance") or current_provenance_dict()).to_dict(),
        )


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    effect: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class RuntimeContext:
    messages: tuple[dict[str, Any], ...]
    tool_schemas: tuple[dict[str, Any], ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeConfig:
    # No turn ceiling, tool-error cap, correction budget, or default task clock.
    # A wall clock exists only when the caller explicitly opts in.
    timeout_seconds: float | None = None
    enforce_fixed_tools: bool = True
    expected_tool_names: tuple[str, ...] = WORKER_TOOL_NAMES
    prompt_path: str = ""

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive or None")


@dataclass(frozen=True)
class RuntimeResult:
    status: RuntimeStatus
    final_text: str = ""
    artifacts: tuple[ArtifactFact, ...] = ()
    deletions: tuple[DeletionFact, ...] = ()
    verification: tuple[VerificationFact, ...] = ()
    gaps: tuple[str, ...] = ()
    reason: str = ""
    model_calls: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)
    started_at: str = field(default_factory=utc_now)
    finished_at: str = field(default_factory=utc_now)
    task_run: TaskRun | None = field(default=None, compare=False, repr=False)
    delivery: DeliveryRecord | None = field(default=None, compare=False, repr=False)

    @property
    def succeeded(self) -> bool:
        return self.status is RuntimeStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(self)
        data.pop("task_run", None)
        data.pop("delivery", None)
        return data

    def __iter__(self):
        """Compatibility with the Engine's historic ``run, record = await run()``."""
        yield self.task_run
        yield self.delivery


class RuntimeEventType(str, Enum):
    STATE_CHANGED = "state_changed"
    MODEL_CALLED = "model_called"
    MODEL_COMPLETED = "model_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_REJECTED = "tool_rejected"
    PROTOCOL_REPAIR = "protocol_repair"
    DELIVERY_COMMITTED = "delivery_committed"
    TASK_WAITING = "task_waiting"
    TASK_BLOCKED = "task_blocked"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"

    @classmethod
    def coerce(cls, value: "RuntimeEventType | str") -> "RuntimeEventType":
        """Reject protocol drift instead of disguising it as a state update."""

        if isinstance(value, cls):
            return value
        return cls(str(value))


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    task_id: str
    run_id: str
    sequence: int
    type: RuntimeEventType
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=current_provenance_dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", normalize_provenance(self.provenance).to_dict())

    @classmethod
    def create(cls, *, task_id: str, run_id: str, sequence: int, event_type: RuntimeEventType | str, payload: Mapping[str, Any] | None = None, provenance: Mapping[str, Any] | None = None) -> "RuntimeEvent":
        return cls(
            event_id=uuid.uuid4().hex,
            task_id=task_id,
            run_id=run_id,
            sequence=sequence,
            type=RuntimeEventType.coerce(event_type),
            created_at=utc_now(),
            payload=dict(payload or {}),
            provenance=normalize_provenance(provenance).to_dict() if provenance is not None else current_provenance_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeEvent":
        return cls(
            event_id=str(value.get("event_id") or uuid.uuid4().hex),
            task_id=str(value.get("task_id") or ""),
            run_id=str(value.get("run_id") or ""),
            sequence=int(value.get("sequence") or 0),
            type=RuntimeEventType.coerce(value.get("type", RuntimeEventType.STATE_CHANGED)),
            created_at=str(value.get("created_at") or utc_now()),
            payload=dict(value.get("payload") or {}),
            provenance=(normalize_provenance(value.get("provenance"), legacy_if_missing=True).to_dict() if value.get("provenance") is not None else unknown_legacy_provenance().to_dict()),
        )


class EventSink(Protocol):
    async def append(self, event: RuntimeEvent) -> None: ...


class NullEventSink:
    async def append(self, event: RuntimeEvent) -> None:
        del event


class EventEmitter:
    def __init__(self, sink: EventSink | None = None, listeners: tuple[Callable[[RuntimeEvent], Awaitable[None] | None], ...] = ()) -> None:
        self._sink = sink or NullEventSink()
        self._listeners = list(listeners)

    def add_listener(self, listener: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._listeners = [item for item in self._listeners if item != listener]

    def has_listener(self, listener: Callable[[RuntimeEvent], Awaitable[None] | None]) -> bool:
        return listener in self._listeners

    async def emit(self, run: TaskRun, event_type: RuntimeEventType | str, **payload: Any) -> RuntimeEvent:
        run.event_sequence += 1
        event = RuntimeEvent.create(
            task_id=run.envelope.task_id,
            run_id=run.run_id,
            sequence=run.event_sequence,
            event_type=event_type,
            payload=payload,
            provenance=run.provenance,
        )
        run.events.append(event.to_dict())
        await self._sink.append(event)
        for listener in tuple(self._listeners):
            result = listener(event)
            if inspect.isawaitable(result):
                await result
        return event


class ModelAdapter(Protocol):
    async def step(self, context: RuntimeContext, **kwargs: Any) -> StepOutcome: ...


class Registry(Protocol):
    def names(self) -> list[str]: ...
    def get_schemas(self) -> list[dict[str, Any]]: ...
    async def execute(self, name: str, args: dict[str, Any], **context: Any) -> str: ...



class _RuntimeCancelled(Exception):
    pass


class WorkerRuntime:
    """One task attempt, one fixed Registry, one provider loop."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        registry: Registry | None = None,
        tools: Registry | None = None,
        config: RuntimeConfig | None = None,
        prompt: str = "",
        prompt_path: str | Path = "",
        workspace_root: str | Path | None = None,
        cancellation_event: asyncio.Event | None = None,
        process_registry: Any | None = None,
        cleanup_callbacks: Sequence[Callable[[], Any]] = (),
        events: EventEmitter | None = None,
        on_run_started: Callable[[TaskRun], Any] | None = None,
        reasoning_relay: Callable[[str], Any] | None = None,  # [v1.0.23.3] 推理增量直通回调（fire-and-forget）
        **compatibility: Any,
    ) -> None:
        del compatibility
        self.model = model
        self.registry = registry or tools
        if self.registry is None:
            raise TypeError("WorkerRuntime requires a ToolRegistry")
        self.config = config or RuntimeConfig()
        self.prompt = self._load_prompt(prompt, prompt_path or self.config.prompt_path)
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else None
        self.cancellation_event = cancellation_event or asyncio.Event()
        self.process_registry = process_registry
        self.cleanup_callbacks = tuple(cleanup_callbacks)
        self.events = events or EventEmitter()
        self.on_run_started = on_run_started
        self.reasoning_relay = reasoning_relay
        self.completion_managed = False
        self._running_task: asyncio.Task[Any] | None = None

        names = tuple(map(str, self.registry.names()))
        if self.config.enforce_fixed_tools and names != tuple(self.config.expected_tool_names):
            raise ValueError(
                "Worker registry must expose the fixed 19 tools in canonical order; "
                f"got {names!r}"
            )
        schemas = tuple(copy.deepcopy(self.registry.get_schemas()))
        if tuple(self._schema_name(x) for x in schemas) != names:
            raise ValueError("Worker registry names and provider schemas disagree")
        self._schemas = schemas
        self._schema_digest = self._digest_schemas(schemas)

    @staticmethod
    def _load_prompt(prompt: str, path: str | Path) -> str:
        if str(prompt or "").strip():
            return str(prompt).strip()
        if path:
            candidate = Path(path).expanduser()
            try:
                return candidate.read_text("utf-8").strip()
            except OSError as exc:
                raise RuntimeError(f"Worker prompt is unreadable: {candidate}") from exc
        return (
            "You are a Knowe Worker. Use only native provider tool calls. "
            "Never print tool-call markup. Verify file effects before reporting completion."
        )

    @staticmethod
    def _schema_name(schema: Mapping[str, Any]) -> str:
        function = schema.get("function") if isinstance(schema, Mapping) else None
        return str(function.get("name") or "") if isinstance(function, Mapping) else ""

    @staticmethod
    def _digest_schemas(schemas: Iterable[Mapping[str, Any]]) -> str:
        raw = json.dumps(list(schemas), ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def cancel(self) -> None:
        self.cancellation_event.set()

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return remaining

    def _check_cancel(self, deadline: float | None) -> None:
        if self.cancellation_event.is_set():
            raise _RuntimeCancelled
        self._remaining(deadline)

    async def _await(self, value: Awaitable[Any], deadline: float | None) -> Any:
        """Await one operation without ``asyncio.wait_for`` deadline inheritance.

        With the default ``deadline=None`` this is only a cancellation race; Provider,
        terminal, browser, and network tools keep their own operation-specific timeouts.
        An explicitly configured task wall clock is represented by a separate timer and
        remains an opt-in mechanical stop.
        """

        self._check_cancel(deadline)
        operation = asyncio.ensure_future(value)
        cancelled = asyncio.create_task(self.cancellation_event.wait())
        timer: asyncio.Task[Any] | None = None
        waiters: set[asyncio.Task[Any]] = {operation, cancelled}
        remaining = self._remaining(deadline)
        if remaining is not None:
            timer = asyncio.create_task(asyncio.sleep(remaining))
            waiters.add(timer)
        try:
            done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done and self.cancellation_event.is_set():
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await operation
                raise _RuntimeCancelled
            if timer is not None and timer in done:
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await operation
                raise asyncio.TimeoutError
            return await operation
        finally:
            cancelled.cancel()
            if timer is not None:
                timer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cancelled
            if timer is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await timer

    def _initial_messages(self, envelope: TaskEnvelope) -> list[dict[str, Any]]:
        context_rows: list[dict[str, Any]] = []
        for ref in envelope.context_refs:
            # References are transport descriptors, not a second lossy body cache.
            # Gateway-prepared large files expose path/size/hash/required metadata so the
            # Worker can retrieve any range with its existing fixed read tool.
            context_rows.append({
                "kind": ref.kind,
                "ref": ref.ref,
                "summary": ref.summary,
                "priority": ref.priority,
                "estimated_tokens": ref.estimated_tokens,
                "metadata": copy.deepcopy(ref.metadata),
            })
        metadata = envelope.metadata
        # [I-2] The task goal is opaque payload handed to the LLM. Runtime does not
        # infer expected artifacts / deletions / task-type from it. Acceptance
        # criteria are carried through only because the Coordinator authored them.
        task_payload = {
            "task_id": envelope.task_id,
            "project_id": envelope.project_id,
            "worker_id": envelope.worker_id,
            "goal": envelope.goal,
            "workspace": ".",
            "acceptance": metadata.get("acceptance_criteria") or metadata.get("acceptance") or "",
        }
        text = "Task envelope:\n" + json.dumps(task_payload, ensure_ascii=False, indent=2)
        if context_rows:
            text += "\n\nExplicit context references:\n" + json.dumps(
                context_rows, ensure_ascii=False, indent=2, default=str
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": text},
        ]
        # [v1.0.19.5] engine 把 worker 自己的 worklog 尾部并入 envelope.metadata；
        #   这里注入 system prompt 尾部，与项目经理/知知的常驻记忆注入同款语义。
        _mem = metadata.get("agent_memory")
        if isinstance(_mem, str) and _mem.strip():
            messages[0] = {
                "role": "system",
                "content": self.prompt + "\n" + _mem.strip(),
            }
        # [v1.0.19.5] 群聊派活/@直达带附件时，engine 把附件元数据并入 envelope.metadata；
        #   这里注入最后一条 user 消息（OpenAI 多模态数组，验签后读本地字节）。
        raw_attachments = metadata.get("attachments")
        if isinstance(raw_attachments, list) and raw_attachments:
            from .attachments import inject_into_last_user
            inject_into_last_user(messages, raw_attachments)
        return messages

    async def run(self, envelope: TaskEnvelope) -> RuntimeResult:
        if not isinstance(envelope, TaskEnvelope):
            if isinstance(envelope, Mapping):
                envelope = TaskEnvelope.from_dict(envelope)
            else:
                raise TypeError("run() requires TaskEnvelope")
        started_monotonic = time.monotonic()
        deadline = (
            started_monotonic + float(self.config.timeout_seconds)
            if self.config.timeout_seconds is not None
            else None
        )
        started_at = utc_now()
        run = TaskRun(envelope=envelope, state=TaskState.RUNNING, started_at=started_at)
        messages = self._initial_messages(envelope)
        artifacts: dict[str, ArtifactFact] = {}
        deletions: dict[str, DeletionFact] = {}
        verifications: list[VerificationFact] = []
        failures: list[ToolResult] = []  # recorded for telemetry only, never a verdict
        usage = ModelUsage()
        result: RuntimeResult | None = None
        # [v1.0.23.5] worker 推理全文累积：reasoning_relay 只做 fire-and-forget 转发，
        #   这里在转发的同时把增量攒起来，任务结束时写入 run.metadata["reasoning"]，
        #   由 completion 投影带进落定消息（worker 气泡刷新后/落定后仍有完整推理）。
        reasoning_buf: list[str] = []
        # [v1.0.23.6] 推理计时：首个推理增量到达时刻 → 结束时算耗时（reasoning_seconds）
        reasoning_started: float | None = None
        self._running_task = asyncio.current_task()

        if self.on_run_started is not None:
            value = self.on_run_started(run)
            if inspect.isawaitable(value):
                await value
        await self.events.emit(run, RuntimeEventType.STATE_CHANGED, state=TaskState.RUNNING.value)

        try:
            # [I-4] Unbounded carrier loop. It only exits when the LLM produces a
            # semantic outcome (final / waiting / blocked) or a mechanical stop
            # fires (wall-clock timeout, external cancel, unrecoverable error).
            turn = 0
            while True:
                turn += 1
                self._check_cancel(deadline)
                current_schemas = tuple(copy.deepcopy(self.registry.get_schemas()))
                if self._digest_schemas(current_schemas) != self._schema_digest:
                    raise RuntimeError("Worker tool schemas changed during the attempt")
                await self.events.emit(run, RuntimeEventType.MODEL_CALLED, turn=turn, tool_names=list(self.registry.names()))
                context = RuntimeContext(
                    messages=tuple(copy.deepcopy(messages)),
                    tool_schemas=current_schemas,
                    metadata={
                        "task_id": envelope.task_id,
                        "run_id": run.run_id,
                        "turn": turn,
                        "tool_schema_sha256": self._schema_digest,
                    },
                )

                # [v1.0.23.3] worker 推理增量实时转发：provider 流里的
                #   reasoning_content 增量 → reasoning_relay 直通回调（engine 侧
                #   fire-and-forget 广播 WS，与主 Agent 的 reasoning_delta_callback
                #   同哲学）。**不 await**：推理转发绝不能阻塞 provider 流——
                #   走 EventEmitter 全链路（sink+listeners+WS 广播）会让 worker
                #   推理「卡死」（实测：await emit 拖住流式循环）。
                if self.reasoning_relay is not None:
                    # [v1.0.23.5] 转发同时累积全文（缓冲在 run 级，跨 turn 持续攒）
                    def _relay_buffered(text: str) -> None:
                        nonlocal reasoning_started
                        if reasoning_started is None:
                            reasoning_started = time.monotonic()   # [v1.0.23.6] 首个增量 = 计时起点
                        reasoning_buf.append(text)
                        self.reasoning_relay(text)  # type: ignore[arg-type]
                    outcome = await self._await(
                        self.model.step(
                            context,
                            reasoning_delta_callback=_relay_buffered,
                        ),
                        deadline,
                    )
                else:
                    outcome = await self._await(self.model.step(context), deadline)
                if not isinstance(outcome, StepOutcome):
                    raise TypeError("model adapter returned a non-StepOutcome")
                run.model_calls += 1
                usage = self._merge_usage(usage, outcome.usage)
                await self.events.emit(run, RuntimeEventType.MODEL_COMPLETED, turn=turn, outcome=outcome.kind.value)

                if outcome.kind is OutcomeKind.ACTIONS:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": outcome.assistant_text or None,
                            "tool_calls": [call.to_provider_dict() for call in outcome.tool_calls],
                        }
                    )
                    for call in outcome.tool_calls:
                        self._check_cancel(deadline)
                        await self.events.emit(run, RuntimeEventType.TOOL_STARTED, call_id=call.id, name=call.name)
                        tool_result = await self._execute_tool(call, run, deadline)
                        run.tool_calls += 1
                        if tool_result.ok:
                            self._collect_facts(tool_result, artifacts, deletions, verifications)
                            await self.events.emit(run, RuntimeEventType.TOOL_COMPLETED, call_id=call.id, name=call.name, ok=True)
                        else:
                            failures.append(tool_result)
                            code = tool_result.error_code or tool_result.error or "tool_error"
                            await self.events.emit(
                                run,
                                RuntimeEventType.TOOL_REJECTED,
                                call_id=call.id,
                                name=call.name,
                                code=code,
                                error=tool_result.error,
                            )
                            # [I-4] No error cap. The failure is re-injected below as
                            # a normal tool result; the LLM sees it and decides whether
                            # to retry, change approach, or give up. Runtime does not
                            # adjudicate effort.
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                # [v1.0.34] 请求载体压缩：tool_result 原文进权威存储，
                                # 发给 provider 的 content 走 compress_tool_result（开关内判）。
                                "content": compress_tool_result(
                                    json.dumps(
                                        tool_result.to_dict(),
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    )
                                ),
                            }
                        )
                    continue

                if outcome.kind is OutcomeKind.WAITING:
                    run.waiting_question = outcome.question
                    run.dependency = outcome.dependency or "user_input"
                    result = self._finish_result(
                        RuntimeStatus.WAITING,
                        run,
                        final_text=outcome.question,
                        artifacts=artifacts,
                        deletions=deletions,
                        verifications=verifications,
                        gaps=(outcome.question,),
                        reason=run.dependency,
                        failures=failures,
                        usage=usage,
                        started_at=started_at,
                    )
                    await self.events.emit(run, RuntimeEventType.TASK_WAITING, question=outcome.question, dependency=run.dependency)
                    break

                if outcome.kind is OutcomeKind.BLOCKED:
                    result = self._finish_result(
                        RuntimeStatus.BLOCKED,
                        run,
                        final_text=outcome.assistant_text,
                        artifacts=artifacts,
                        deletions=deletions,
                        verifications=verifications,
                        gaps=(outcome.dependency,),
                        reason=outcome.dependency,
                        failures=failures,
                        usage=usage,
                        started_at=started_at,
                    )
                    run.dependency = outcome.dependency
                    await self.events.emit(run, RuntimeEventType.TASK_BLOCKED, dependency=outcome.dependency)
                    break

                # [I-1] The LLM returned a final answer, so the work is done.
                # Runtime does not verify artifacts, re-stat files, clean the text,
                # or inject corrections. The final answer is carried to the
                # Coordinator as the report; the Coordinator's LLM is the judge.
                final_text = str(outcome.assistant_text or "")
                messages.append({"role": "assistant", "content": final_text})
                result = self._finish_result(
                    RuntimeStatus.SUCCEEDED,
                    run,
                    final_text=final_text,
                    artifacts=artifacts,
                    deletions=deletions,
                    verifications=verifications,
                    gaps=(),
                    reason="completed",
                    failures=failures,
                    usage=usage,
                    started_at=started_at,
                )
                break

        except _RuntimeCancelled:
            result = self._finish_result(
                RuntimeStatus.CANCELLED,
                run,
                final_text="",
                artifacts=artifacts,
                deletions=deletions,
                verifications=verifications,
                gaps=("Task was cancelled before completion.",),
                reason="cancelled",
                failures=failures,
                usage=usage,
                started_at=started_at,
            )
            await self.events.emit(run, RuntimeEventType.TASK_CANCELLED, reason="cancelled")
        except asyncio.TimeoutError:
            result = self._finish_result(
                RuntimeStatus.TIMED_OUT,
                run,
                final_text="",
                artifacts=artifacts,
                deletions=deletions,
                verifications=verifications,
                gaps=("Task exceeded its wall-clock timeout.",),
                reason="timeout",
                failures=failures,
                usage=usage,
                started_at=started_at,
            )
            await self.events.emit(run, RuntimeEventType.TASK_FAILED, reason="timeout")
        except asyncio.CancelledError:
            self.cancellation_event.set()
            result = self._finish_result(
                RuntimeStatus.CANCELLED,
                run,
                final_text="",
                artifacts=artifacts,
                deletions=deletions,
                verifications=verifications,
                gaps=("Task execution was cancelled.",),
                reason="cancelled",
                failures=failures,
                usage=usage,
                started_at=started_at,
            )
            await self.events.emit(run, RuntimeEventType.TASK_CANCELLED, reason="cancelled")
        except Exception as exc:
            # v1.0.19.5: 不再把异常吞成裸类名——ProviderError 自带结构化诊断
            # （message / HTTP 状态码 / 服务商响应体），必须透传给项目经理与交接文档。
            detail = str(exc).strip() or type(exc).__name__
            if isinstance(exc, ProviderError):
                detail = exc.message
                if exc.status_code:
                    detail = f"{detail} [HTTP {exc.status_code}]"
                if exc.response_body:
                    detail = f"{detail} :: {str(exc.response_body)[:300]}"
            result = self._finish_result(
                RuntimeStatus.SYSTEM_ERROR,
                run,
                final_text="",
                artifacts=artifacts,
                deletions=deletions,
                verifications=verifications,
                gaps=(f"Internal runtime error: {detail}",),
                reason=f"runtime_error:{type(exc).__name__}",
                failures=failures,
                usage=usage,
                started_at=started_at,
            )
            await self.events.emit(run, RuntimeEventType.TASK_FAILED, reason=result.reason)
        finally:
            await self._cleanup()
            self._running_task = None

        # [v1.0.23.5] 推理全文随任务结果落盘（completion 投影带进落定消息）
        if reasoning_buf:
            run.metadata["reasoning"] = "".join(reasoning_buf).strip()
            # [v1.0.23.6] 推理耗时（首个增量 → 任务收尾），供「思考了 Xs」展示
            if reasoning_started is not None:
                run.metadata["reasoning_seconds"] = round(time.monotonic() - reasoning_started, 1)

        assert result is not None
        delivery = self._delivery_for(result, run, started_monotonic)
        result = dataclasses.replace(result, task_run=run, delivery=delivery)
        run.metadata["runtime_result"] = result.to_dict()
        if result.status is RuntimeStatus.SUCCEEDED:
            await self.events.emit(run, RuntimeEventType.DELIVERY_COMMITTED, delivery_id=delivery.delivery_id if delivery else "")
        return result

    async def _execute_tool(self, call: ToolCall, run: TaskRun, deadline: float | None) -> ToolResult:
        started = time.monotonic()
        if call.name not in set(self.registry.names()):
            return ToolResult(
                call_id=call.id,
                name=call.name,
                ok=False,
                summary="Unknown tool.",
                error=f"unknown tool: {call.name}",
                error_code="unknown_tool",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        try:
            raw = await self._await(
                self.registry.execute(
                    call.name,
                    call.arguments,
                    task_run=run,
                    cancellation_event=self.cancellation_event,
                    attempt_process_registry=self.process_registry,
                ),
                deadline,
            )
        except _RuntimeCancelled:
            raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            return ToolResult(
                call_id=call.id,
                name=call.name,
                ok=False,
                summary=f"{call.name} failed.",
                error=str(exc),
                error_code="tool_exception",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        payload: dict[str, Any]
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                payload = dict(parsed) if isinstance(parsed, Mapping) else {"status": "ok", "data": parsed}
            except (ValueError, TypeError, json.JSONDecodeError):
                payload = {"status": "ok", "data": str(raw)}
        elif isinstance(raw, Mapping):
            payload = dict(raw)
        else:
            payload = {"status": "ok", "data": raw}
        ok = str(payload.get("status") or "ok").lower() not in {"error", "failed", "failure"} and not bool(payload.get("error"))
        error = str(payload.get("message") or payload.get("error") or "") if not ok else ""
        code = str(payload.get("code") or payload.get("error_code") or ("tool_error" if not ok else ""))
        summary = str(payload.get("message") or (f"{call.name} completed." if ok else f"{call.name} failed."))
        return ToolResult(
            call_id=call.id,
            name=call.name,
            ok=ok,
            summary=summary,
            facts=payload,
            error=error,
            error_code=code,
            effect=("delete" if call.name in _DELETE_TOOLS else "mutation" if call.name in _MUTATION_TOOLS else "read"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _collect_facts(result: ToolResult, artifacts: dict[str, ArtifactFact], deletions: dict[str, DeletionFact], verifications: list[VerificationFact]) -> None:
        payload = result.facts
        artifact_rows: list[Mapping[str, Any]] = []
        raw_artifact = payload.get("artifact")
        if isinstance(raw_artifact, Mapping):
            artifact_rows.append(raw_artifact)
        raw_artifacts = payload.get("artifacts")
        if isinstance(raw_artifacts, Sequence) and not isinstance(raw_artifacts, (str, bytes, bytearray)):
            artifact_rows.extend(x for x in raw_artifacts if isinstance(x, Mapping))
        for row in artifact_rows:
            try:
                fact = ArtifactFact(
                    path=str(row.get("path") or ""),
                    kind=str(row.get("kind") or "file"),
                    size=int(row.get("size", row.get("bytes", -1))),
                    sha256=str(row.get("sha256") or "").lower(),
                    verified=bool(row.get("verified", False)),
                    media_type=str(row.get("media_type") or "application/octet-stream"),
                    metadata=dict(row.get("metadata") or {}),
                )
            except (TypeError, ValueError):
                continue
            if fact.verified:
                artifacts[fact.path] = fact
                verifications.append(VerificationFact("after_effect", True, f"Verified {fact.kind}: {fact.path}", fact.path))
        raw_deletion = payload.get("deletion")
        if isinstance(raw_deletion, Mapping):
            try:
                fact = DeletionFact(
                    path=str(raw_deletion.get("path") or "").replace("\\", "/"),
                    verified_absent=bool(raw_deletion.get("verified_absent", False)),
                    size_before=(int(raw_deletion["size_before"]) if raw_deletion.get("size_before") is not None else None),
                    metadata=dict(raw_deletion.get("metadata") or {}),
                )
            except (TypeError, ValueError):
                fact = None
            if fact is not None and fact.path and fact.verified_absent:
                deletions[fact.path] = fact
                verifications.append(VerificationFact("deletion", True, f"Verified absent: {fact.path}", fact.path))

    @staticmethod
    def _merge_usage(a: ModelUsage, b: ModelUsage) -> ModelUsage:
        return ModelUsage(
            input_tokens=a.input_tokens + b.input_tokens,
            output_tokens=a.output_tokens + b.output_tokens,
            reasoning_tokens=a.reasoning_tokens + b.reasoning_tokens,
            cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
            cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
            cost_usd=a.cost_usd + b.cost_usd,
            provider_request_id=b.provider_request_id or a.provider_request_id,
            raw={"turns": int(a.raw.get("turns", 0) or 0) + 1},
        )

    def _finish_result(self, status: RuntimeStatus, run: TaskRun, *, final_text: str, artifacts: Mapping[str, ArtifactFact], deletions: Mapping[str, DeletionFact], verifications: Sequence[VerificationFact], gaps: Sequence[str], reason: str, failures: Sequence[ToolResult], usage: ModelUsage, started_at: str) -> RuntimeResult:
        run.state = TaskState.IDLE
        run.final_candidate = str(final_text or "")
        run.terminal_reason = reason
        run.updated_at = utc_now()
        run.set_completion_status(status.value)
        # [I-1] Facts are recorded as telemetry/information for the UI, never as a
        # pass/fail verdict. No artifact_required / expected_* / missing_* / text_only.
        run.metadata.update(
            {
                "verified_artifacts": [_jsonable(x) for x in artifacts.values()],
                "verified_deletions": [_jsonable(x) for x in deletions.values()],
                "verification": [_jsonable(x) for x in verifications],
                "gaps": list(gaps),
                "tool_failures": [x.to_dict() for x in failures],
            }
        )
        # [v1.0.20.3 M1 采集点 B 修复] run.usage 此前从未赋值——usage 只传给了
        # RuntimeResult，engine._persist_worker_token_usage 读的是 run.usage，
        # 拿到的永远是默认空 ModelUsage（全 0 → 被「全零则跳过」拦截），
        # 导致 worker 的 token 统计结构性永不落盘。这里补上同一次赋值的另一头。
        run.usage = usage
        return RuntimeResult(
            status=status,
            final_text=run.final_candidate,
            artifacts=tuple(artifacts.values()),
            deletions=tuple(deletions.values()),
            verification=tuple(verifications),
            gaps=tuple(gaps),
            reason=reason,
            model_calls=run.model_calls,
            tool_calls=run.tool_calls,
            tool_failures=len(failures),
            usage=usage,
            started_at=started_at,
            finished_at=run.updated_at,
            task_run=run,
        )

    def _delivery_for(self, result: RuntimeResult, run: TaskRun, started_monotonic: float) -> DeliveryRecord | None:
        if result.status is not RuntimeStatus.SUCCEEDED:
            return None
        artifact_rows = tuple(
            ArtifactRecord(
                path=fact.path,
                sha256=fact.sha256,
                size=fact.size,
                media_type=fact.media_type,
                metadata={"kind": fact.kind, "verified": fact.verified, **fact.metadata},
            )
            for fact in result.artifacts
        )
        budget = BudgetSummary(
            model_calls=result.model_calls,
            tool_calls=result.tool_calls,
            tool_failures=result.tool_failures,
            subagent_calls=0,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            reasoning_tokens=result.usage.reasoning_tokens,
            cache_read_tokens=result.usage.cache_read_tokens,
            cache_write_tokens=result.usage.cache_write_tokens,
            cost_usd=result.usage.cost_usd,
            elapsed_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        )
        key = hashlib.sha256(f"{run.envelope.task_id}\0{run.envelope.attempt_id}\0{run.run_id}".encode()).hexdigest()
        return DeliveryRecord(
            delivery_id="delivery_" + key[:24],
            idempotency_key=key,
            task_id=run.envelope.task_id,
            run_id=run.run_id,
            project_id=run.envelope.project_id,
            state=TaskState.IDLE,
            text=result.final_text,
            terminal_reason=result.reason,
            artifacts=artifact_rows,
            budget=budget,
            event_sequence=run.event_sequence,
            metadata={
                "completion_status": RuntimeStatus.SUCCEEDED.value,
                "verification": [_jsonable(x) for x in result.verification],
                "deletions": [_jsonable(x) for x in result.deletions],
                "text_only": bool(run.metadata.get("text_only")),
            },
            provenance=run.provenance,
        )

    async def _cleanup(self) -> None:
        callbacks: list[Callable[[], Any]] = []
        if self.process_registry is not None:
            closer = getattr(self.process_registry, "aclose", None)
            if callable(closer):
                callbacks.append(lambda: closer(immediate=self.cancellation_event.is_set()))
        callbacks.extend(self.cleanup_callbacks)
        for callback in callbacks:
            try:
                value = callback()
                if inspect.isawaitable(value):
                    await value
            except Exception:
                # Cleanup is best-effort, but it always runs and never turns partial drafts
                # into success. Callers may observe failures through their resource logs.
                continue


Runtime = WorkerRuntime


__all__ = [
    "ArtifactFact",
    "ArtifactRecord",
    "BudgetSpec",
    "BudgetSummary",
    "ContextReference",
    "DeletionFact",
    "DeliveryAudience",
    "DeliveryRecord",
    "DeliveryTarget",
    "EventEmitter",
    "ModelUsage",
    "OutcomeKind",
    "Runtime",
    "RuntimeConfig",
    "RuntimeContext",
    "RuntimeEvent",
    "RuntimeEventType",
    "RuntimeResult",
    "RuntimeStatus",
    "StepOutcome",
    "TaskEnvelope",
    "TaskRun",
    "TaskState",
    "ToolCall",
    "ToolResult",
    "VerificationFact",
    "WORKER_TOOL_NAMES",
    "WorkerRuntime",
    "utc_now",
]
