"""Task-envelope persistence and user-facing completion projections."""

from .contracts import normalize_relative_path, stable_identifier
from .store import TaskEnvelopeStore
from .completion import (
    CompletionAwareTaskRunRepository,
    CompletionConflict,
    CompletionEvent,
    CompletionStatus,
    CompletionStatusPolicy,
    completion_policy,
    completion_scope_id,
    DecisionEvent,
    DecisionType,
    OutboxEntry,
    OutboxState,
    ProjectionKind,
    SQLiteCompletionStore,
    TaskJournalEntryV1,
    TaskResultV1,
    WaitToken,
)
from .projections import CompletionProjector, ProjectionEffect, ProjectionFaultInjector

__all__ = [
    "CompletionAwareTaskRunRepository",
    "CompletionConflict",
    "CompletionEvent",
    "CompletionProjector",
    "CompletionStatus",
    "CompletionStatusPolicy",
    "completion_policy",
    "completion_scope_id",
    "DecisionEvent",
    "DecisionType",
    "OutboxEntry",
    "OutboxState",
    "ProjectionEffect",
    "ProjectionFaultInjector",
    "ProjectionKind",
    "SQLiteCompletionStore",
    "TaskEnvelopeStore",
    "TaskJournalEntryV1",
    "TaskResultV1",
    "WaitToken",
    "normalize_relative_path",
    "stable_identifier",
]
