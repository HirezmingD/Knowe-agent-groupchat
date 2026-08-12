from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import weakref
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from knowe_provenance import (
    component_version,
    current_provenance_dict,
    load_schema_registry,
    normalize_provenance,
    schema_registry_hash,
)


log = logging.getLogger("knowe.storage.sqlite")


# ``ProjectEngine.stop()`` can only close connections that are reachable through its
# own fields.  WorkerRuntime/repository adapters may legitimately create additional
# ``SQLiteDatabase`` wrappers for the same project-local database, and a stale runtime
# object can keep one of those wrappers alive after the Engine task has stopped.  On
# Windows, one such connection is enough to make renaming the project root fail with
# ``WinError 5``.
#
# Keep a weak process-wide registry of every wrapper and a path quarantine used by the
# permanent-delete barrier.  The registry does not extend object lifetime; it merely
# makes *explicit* close possible even when the owning object is no longer reachable
# from ProjectEngine.  Constructor registration and root quarantine use one lock, so a
# connection can never slip between "block new opens" and "close the snapshot".
_SQLITE_REGISTRY_LOCK = threading.RLock()
_SQLITE_DATABASES: weakref.WeakSet[Any] = weakref.WeakSet()
_SQLITE_QUIESCED_ROOTS: dict[str, int] = {}


def _filesystem_key(path: str | Path) -> str:
    return os.path.normcase(
        os.path.abspath(os.fspath(Path(path).expanduser().resolve(strict=False)))
    )


def _path_is_within(path_key: str, root_key: str) -> bool:
    if not path_key or not root_key:
        return False
    try:
        return os.path.commonpath((path_key, root_key)) == root_key
    except ValueError:
        # Different Windows drives have no common path.
        return False


def _quiesced_root_for(path_key: str) -> str | None:
    matches = [
        root
        for root, count in _SQLITE_QUIESCED_ROOTS.items()
        if count > 0 and _path_is_within(path_key, root)
    ]
    return max(matches, key=len) if matches else None


class SQLiteRootQuiescedError(RuntimeError):
    """A project-local SQLite path is fenced for permanent deletion."""

    def __init__(self, path: str | Path, root: str | Path) -> None:
        self.path = str(path)
        self.root = str(root)
        super().__init__(f"SQLite 根目录正在关闭，拒绝重新打开：{self.path}（根：{self.root}）")


@dataclass(frozen=True)
class SQLiteCloseReport:
    """Result of closing every registered database below one filesystem root."""

    root: str
    matched: int
    closed: int
    remaining: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.remaining and not self.errors


def _open_databases_under(root_key: str) -> list[Any]:
    with _SQLITE_REGISTRY_LOCK:
        return [
            database
            for database in tuple(_SQLITE_DATABASES)
            if not bool(getattr(database, "_closed", True))
            and _path_is_within(str(getattr(database, "_path_key", "")), root_key)
        ]


def close_sqlite_databases_under(root: str | Path) -> SQLiteCloseReport:
    """Explicitly close all registered SQLite wrappers below ``root``.

    This is intentionally stronger than dropping Python references.  It waits on each
    wrapper's serialized transaction lock and invokes ``Connection.close()`` directly,
    which is the boundary Windows needs before a project-root rename can succeed.
    """

    root_key = _filesystem_key(root)
    initial = _open_databases_under(root_key)
    errors: list[str] = []
    closed = 0
    for database in initial:
        path = str(getattr(database, "path", "<unknown>"))
        try:
            was_closed = bool(getattr(database, "_closed", False))
            database.close()
            if not was_closed and bool(getattr(database, "_closed", False)):
                closed += 1
        except Exception as exc:  # noqa: BLE001 - report every failed close to delete barrier
            detail = " ".join(str(exc).split()) or exc.__class__.__name__
            errors.append(f"{path}: {detail}")

    remaining_objects = _open_databases_under(root_key)
    remaining = tuple(
        dict.fromkeys(str(getattr(database, "path", "<unknown>")) for database in remaining_objects)
    )
    return SQLiteCloseReport(
        root=root_key,
        matched=len(initial),
        closed=closed,
        remaining=remaining,
        errors=tuple(dict.fromkeys(errors)),
    )


def quiesce_sqlite_databases_under(root: str | Path) -> SQLiteCloseReport:
    """Fence ``root`` against new SQLite opens, then close every existing wrapper.

    The fence remains active until ``release_sqlite_quiescence(root)`` is called.  This
    covers the critical gap between Engine teardown and the atomic project-root rename:
    a delayed callback holding only a path cannot silently recreate a connection there.
    """

    root_key = _filesystem_key(root)
    with _SQLITE_REGISTRY_LOCK:
        _SQLITE_QUIESCED_ROOTS[root_key] = _SQLITE_QUIESCED_ROOTS.get(root_key, 0) + 1
    return close_sqlite_databases_under(root_key)


def release_sqlite_quiescence(root: str | Path) -> None:
    """Release one matching delete fence created by ``quiesce_sqlite_databases_under``."""

    root_key = _filesystem_key(root)
    with _SQLITE_REGISTRY_LOCK:
        count = _SQLITE_QUIESCED_ROOTS.get(root_key, 0)
        if count <= 1:
            _SQLITE_QUIESCED_ROOTS.pop(root_key, None)
        else:
            _SQLITE_QUIESCED_ROOTS[root_key] = count - 1


def _busy_timeout_ms() -> int:
    """Return the bounded SQLite lock wait used by production repositories.

    Startup previously inherited SQLite's 30-second wait at every write boundary.
    A single externally locked project database could therefore keep the process alive
    but unable to expose its health endpoint.  Keep the default short enough for a
    bounded, explicit startup failure while retaining an environment override for
    deployments that deliberately prefer a longer queue.
    """

    raw = os.environ.get("KNOWE_SQLITE_BUSY_TIMEOUT_MS", "1000").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 1000
    return max(0, min(value, 60_000))


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


# ═══════════════════════════════════════════════════════════════
# [v1.0.31 R1] 大 JSON 列压缩存储
#
# 明文 JSON 一律以 `{` 开头；压缩载荷带 `z1:` 前缀（zlib+base64），
# 天然可区分。写入端：task_runs 等大列用 compress_json_dumps；
# 读取端：json_loads 自动识别两种格式，旧明文数据零迁移即可读。
# ═══════════════════════════════════════════════════════════════
PAYLOAD_COMPRESSED_PREFIX = "z1:"


def is_compressed_payload(value: str | bytes | None) -> bool:
    return isinstance(value, str) and value.startswith(PAYLOAD_COMPRESSED_PREFIX)


def compress_json_dumps(value: Any) -> str:
    """压缩序列化（R1）：zlib 压缩 + base64，带 ``z1:`` 前缀。"""
    raw = json_dumps(value).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    return PAYLOAD_COMPRESSED_PREFIX + base64.b64encode(compressed).decode("ascii")


def payload_sha256(value: Any) -> str:
    """压缩前对原始 JSON 内容算指纹（R1 审计校验用）。"""
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


def json_loads(value: str | bytes | None, default: Any = None) -> Any:
    if value in (None, "", b""):
        return default
    if is_compressed_payload(value):
        try:
            raw = zlib.decompress(base64.b64decode(value[len(PAYLOAD_COMPRESSED_PREFIX):]))
            return json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError, json.JSONDecodeError, zlib.error, binascii.Error):
            return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


PROVENANCE_COLUMN_DEFS: dict[str, str] = {
    "provenance_json": "TEXT",
    "build_id": "TEXT",
    "git_commit": "TEXT",
    "runtime_schema_version": "TEXT",
    "harness_schema_version": "TEXT",
    "prompt_bundle_version": "TEXT",
    "migration_epoch": "INTEGER",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def provenance_sql_values(value: Mapping[str, Any] | None) -> tuple[Any, ...]:
    provenance = normalize_provenance(value).to_dict()
    return (
        json_dumps(provenance),
        provenance.get("build_id") or None,
        provenance.get("git_commit") or None,
        provenance.get("runtime_schema_version") or None,
        provenance.get("harness_schema_version") or None,
        provenance.get("prompt_bundle_version") or None,
        int(provenance.get("migration_epoch") or 0) or None,
    )


class SQLiteDatabase:
    """Small serialized SQLite transaction boundary shared by repositories.

    Wave 0 adds two generic migration primitives without changing repository behavior:
    idempotent column addition and a database-local schema/migration-epoch registry.
    """

    def __init__(self, path: str | Path) -> None:
        raw = str(path)
        self._lock = threading.RLock()
        self._closed = True
        self._path_key = ""
        busy_timeout_ms = _busy_timeout_ms()
        filesystem_path = raw != ":memory:"
        if filesystem_path:
            file_path = Path(raw).expanduser().resolve(strict=False)
            raw = str(file_path)
            self._path_key = _filesystem_key(file_path)
        self.path = raw

        # Constructor registration and delete quiescence share one lock.  Holding it
        # through parent creation and ``sqlite3.connect`` is deliberate: after a delete
        # fence is installed, no late callback may recreate the just-staged project
        # directory before its connection becomes visible to the close snapshot.
        connection: sqlite3.Connection | None = None
        with _SQLITE_REGISTRY_LOCK:
            if filesystem_path:
                blocked_root = _quiesced_root_for(self._path_key)
                if blocked_root is not None:
                    raise SQLiteRootQuiescedError(self.path, blocked_root)
                file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                connection = sqlite3.connect(
                    raw,
                    check_same_thread=False,
                    timeout=max(0.001, busy_timeout_ms / 1000.0),
                )
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:
                    pass
                connection.execute("PRAGMA synchronous=NORMAL")
            except BaseException:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                raise
            self._connection = connection
            self._closed = False
            if filesystem_path:
                _SQLITE_DATABASES.add(self)

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def initialize(self, script: str) -> None:
        with self._lock:
            self._connection.executescript(script)
            self._connection.commit()

    def table_columns(self, table: str) -> set[str]:
        if not _IDENTIFIER_RE.fullmatch(table):
            raise ValueError(f"invalid SQLite identifier: {table!r}")
        with self._lock:
            rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}

    def ensure_columns(self, table: str, columns: Mapping[str, str]) -> None:
        if not _IDENTIFIER_RE.fullmatch(table):
            raise ValueError(f"invalid SQLite identifier: {table!r}")
        for name, definition in columns.items():
            if not _IDENTIFIER_RE.fullmatch(str(name)):
                raise ValueError(f"invalid SQLite column: {name!r}")
            if not str(definition).strip():
                raise ValueError(f"empty SQLite column definition: {name}")
        with self._lock:
            existing = self.table_columns(table)
            for name, definition in columns.items():
                if name in existing:
                    continue
                try:
                    self._connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
                    self._connection.commit()
                except sqlite3.OperationalError as exc:
                    # A second process may have won the same idempotent migration.
                    if "duplicate column" not in str(exc).lower():
                        self._connection.rollback()
                        raise
                existing.add(name)

    def register_schema(self, component: str, *, version: str | None = None) -> None:
        registry = load_schema_registry()
        target_version = str(version or component_version(component))
        epoch = int(registry["migration_epoch"])
        provenance = current_provenance_dict()
        applied_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        registry_sha = schema_registry_hash(registry)
        target_applied_at = applied_at
        target_build_id = provenance.get("build_id") or None
        target_git_commit = provenance.get("git_commit") or None
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_registry (
                    component TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    migration_epoch INTEGER NOT NULL,
                    registry_sha256 TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    build_id TEXT,
                    git_commit TEXT
                );
                CREATE TABLE IF NOT EXISTS migration_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    migration_epoch INTEGER NOT NULL,
                    registry_sha256 TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    build_id TEXT,
                    git_commit TEXT,
                    UNIQUE(component, schema_version, migration_epoch)
                );
                """
            )
            # Migration epochs are monotonic.  A source-only fallback registry can
            # be older than a database produced by a newer packaged registry; never
            # downgrade durable metadata in that situation.  Reuse the newest known
            # target (from either the current row or its migration ledger) and surface
            # the registry mismatch explicitly.
            durable_target = self._connection.execute(
                """
                SELECT schema_version, migration_epoch, registry_sha256,
                       applied_at, build_id, git_commit
                  FROM (
                        SELECT schema_version, migration_epoch, registry_sha256,
                               applied_at, build_id, git_commit, 0 AS ordering_id
                          FROM schema_registry
                         WHERE component=?
                        UNION ALL
                        SELECT schema_version, migration_epoch, registry_sha256,
                               applied_at, build_id, git_commit, id AS ordering_id
                          FROM migration_log
                         WHERE component=?
                       )
                 ORDER BY migration_epoch DESC, ordering_id DESC
                 LIMIT 1
                """,
                (component, component),
            ).fetchone()
            if durable_target is not None and int(durable_target["migration_epoch"]) > epoch:
                loaded_epoch = epoch
                loaded_version = target_version
                target_version = str(durable_target["schema_version"])
                epoch = int(durable_target["migration_epoch"])
                registry_sha = str(durable_target["registry_sha256"])
                target_applied_at = str(durable_target["applied_at"])
                target_build_id = durable_target["build_id"]
                target_git_commit = durable_target["git_commit"]
                log.warning(
                    "schema registry version mismatch for %s: loaded target=%s/epoch-%s "
                    "is older than durable target=%s/epoch-%s; preserving durable target",
                    component,
                    loaded_version,
                    loaded_epoch,
                    target_version,
                    epoch,
                )

            previous = self._connection.execute(
                """
                SELECT schema_version, migration_epoch, registry_sha256,
                       applied_at, build_id, git_commit
                  FROM schema_registry
                 WHERE component=?
                """,
                (component,),
            ).fetchone()
            if previous is not None:
                previous_version = str(previous["schema_version"])
                previous_epoch = int(previous["migration_epoch"])
                previous_sha = str(previous["registry_sha256"])
                mismatch = (
                    previous_version != target_version
                    or previous_epoch != epoch
                    or previous_sha != registry_sha
                )
                if mismatch:
                    # Preserve the exact row that is about to be replaced.  Older
                    # registries may already have a current-version migration row, so
                    # this copy must happen before the upsert rather than relying on the
                    # later INSERT of the target version.
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO migration_log(
                            component, schema_version, migration_epoch, registry_sha256,
                            applied_at, build_id, git_commit
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            component,
                            previous_version,
                            previous_epoch,
                            previous_sha,
                            str(previous["applied_at"]),
                            previous["build_id"],
                            previous["git_commit"],
                        ),
                    )
                    log.warning(
                        "schema registry mismatch for %s: stored=%s/%s/%s target=%s/%s/%s; "
                        "previous registry row preserved in migration_log",
                        component,
                        previous_version,
                        previous_epoch,
                        previous_sha,
                        target_version,
                        epoch,
                        registry_sha,
                    )
            self._connection.execute(
                """
                INSERT INTO schema_registry(
                    component, schema_version, migration_epoch, registry_sha256,
                    applied_at, build_id, git_commit
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    migration_epoch=excluded.migration_epoch,
                    registry_sha256=excluded.registry_sha256,
                    applied_at=excluded.applied_at,
                    build_id=excluded.build_id,
                    git_commit=excluded.git_commit
                """,
                (
                    component,
                    target_version,
                    epoch,
                    registry_sha,
                    target_applied_at,
                    target_build_id,
                    target_git_commit,
                ),
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO migration_log(
                    component, schema_version, migration_epoch, registry_sha256,
                    applied_at, build_id, git_commit
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    component,
                    target_version,
                    epoch,
                    registry_sha,
                    target_applied_at,
                    target_build_id,
                    target_git_commit,
                ),
            )
            self._connection.commit()

    def close(self) -> None:
        closed = False
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True
            closed = True
        if closed:
            with _SQLITE_REGISTRY_LOCK:
                _SQLITE_DATABASES.discard(self)

    def __enter__(self) -> "SQLiteDatabase":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


__all__ = [
    "PROVENANCE_COLUMN_DEFS",
    "SQLiteCloseReport",
    "SQLiteDatabase",
    "SQLiteRootQuiescedError",
    "close_sqlite_databases_under",
    "json_dumps",
    "json_loads",
    "provenance_sql_values",
    "quiesce_sqlite_databases_under",
    "release_sqlite_quiescence",
]
