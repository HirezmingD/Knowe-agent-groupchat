from __future__ import annotations

from pathlib import Path

from knowe_provenance import assert_provenance_matches
from backend.runtime import TaskRun, utc_now

from ._sqlite import (
    PROVENANCE_COLUMN_DEFS,
    SQLiteDatabase,
    json_dumps,
    json_loads,
    provenance_sql_values,
)


class OptimisticLockError(RuntimeError):
    pass


class TaskRunAlreadyExists(RuntimeError):
    pass


class SQLiteTaskRunRepository:
    """Persists authoritative TaskRun snapshots with compare-and-swap versions."""

    def __init__(self, path: str | Path | SQLiteDatabase) -> None:
        self.db = path if isinstance(path, SQLiteDatabase) else SQLiteDatabase(path)
        self.db.initialize(
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
            CREATE INDEX IF NOT EXISTS idx_task_runs_state ON task_runs(state, updated_at);
            """
        )
        self.db.ensure_columns("task_runs", PROVENANCE_COLUMN_DEFS)
        self.db.ensure_columns("task_runs", {"payload_sha256": "TEXT"})  # [v1.0.31 R1] 压缩指纹
        self.db.register_schema("runtime.task_run")

    def create(self, run: TaskRun) -> TaskRun:
        payload = compress_json_dumps(run.to_dict())  # [v1.0.31 R1] 压缩存储
        payload_digest = payload_sha256(run.to_dict())
        provenance = provenance_sql_values(run.provenance)
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT run_id, payload_json FROM task_runs WHERE task_id=?",
                (run.envelope.task_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["run_id"]) == run.run_id:
                    stored = TaskRun.from_dict(json_loads(existing["payload_json"], {}))
                    try:
                        assert_provenance_matches(stored.provenance, run.provenance, context="TaskRun create")
                    except ValueError as exc:
                        raise TaskRunAlreadyExists(str(exc)) from exc
                    return stored
                raise TaskRunAlreadyExists(f"task already has a different run: {run.envelope.task_id}")
            conn.execute(
                """
                INSERT INTO task_runs(
                    task_id, run_id, version, state, payload_json, created_at, updated_at,
                    provenance_json, build_id, git_commit, runtime_schema_version,
                    harness_schema_version, prompt_bundle_version, migration_epoch,
                    payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload_digest,
                ),
            )
        return run

    def save(self, run: TaskRun, *, expected_version: int) -> TaskRun:
        if expected_version < 0:
            raise ValueError("expected_version must be non-negative")
        if run.version <= expected_version:
            raise ValueError("saved TaskRun version must advance beyond expected_version")
        payload = compress_json_dumps(run.to_dict())  # [v1.0.31 R1] 压缩存储
        payload_digest = payload_sha256(run.to_dict())
        provenance = provenance_sql_values(run.provenance)
        with self.db.transaction() as conn:
            current = conn.execute(
                "SELECT payload_json FROM task_runs WHERE task_id=? AND run_id=?",
                (run.envelope.task_id, run.run_id),
            ).fetchone()
            if current is not None:
                stored = TaskRun.from_dict(json_loads(current["payload_json"], {}))
                try:
                    assert_provenance_matches(stored.provenance, run.provenance, context="TaskRun save")
                except ValueError as exc:
                    raise OptimisticLockError(str(exc)) from exc
            cursor = conn.execute(
                """
                UPDATE task_runs
                  SET version=?, state=?, payload_json=?, updated_at=?,
                      provenance_json=?, build_id=?, git_commit=?, runtime_schema_version=?,
                      harness_schema_version=?, prompt_bundle_version=?, migration_epoch=?,
                      payload_sha256=?
                WHERE task_id=? AND run_id=? AND version=?
                """,
                (
                    run.version,
                    run.state.value,
                    payload,
                    run.updated_at or utc_now(),
                    *provenance,
                    payload_digest,
                    run.envelope.task_id,
                    run.run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                actual = conn.execute(
                    "SELECT version, run_id FROM task_runs WHERE task_id=?",
                    (run.envelope.task_id,),
                ).fetchone()
                actual_version = None if actual is None else int(actual["version"])
                raise OptimisticLockError(
                    f"task {run.envelope.task_id} expected version {expected_version}, actual {actual_version}"
                )
        return run

    def get(self, task_id: str) -> TaskRun | None:
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute("SELECT payload_json FROM task_runs WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return TaskRun.from_dict(json_loads(row["payload_json"], {}))

    def get_by_run_id(self, run_id: str) -> TaskRun | None:
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute("SELECT payload_json FROM task_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return TaskRun.from_dict(json_loads(row["payload_json"], {}))

    def list_by_state(self, state: str, *, limit: int = 100) -> tuple[TaskRun, ...]:
        with self.db.transaction(immediate=False) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM task_runs WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                (str(state), max(1, min(int(limit), 10_000))),
            ).fetchall()
        return tuple(TaskRun.from_dict(json_loads(row["payload_json"], {})) for row in rows)


TaskRunRepository = SQLiteTaskRunRepository

__all__ = [
    "OptimisticLockError",
    "SQLiteTaskRunRepository",
    "TaskRunAlreadyExists",
    "TaskRunRepository",
]
