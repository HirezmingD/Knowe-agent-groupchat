"""Read-only task lineage resolver spanning Runtime and Harness projections."""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from .model import LINEAGE_FIELDS, RECORDED, UNKNOWN_LEGACY, normalize_provenance, unknown_legacy_provenance

_REQUIRED_PROJECTIONS = ("task_runs", "completions", "reports", "memory", "knowledge")


def _read_json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value in (None, "", b""):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _provenance_from(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        raw = value.get("provenance")
        if isinstance(raw, Mapping):
            return normalize_provenance(raw).to_dict()
        if any(key in value for key in (*LINEAGE_FIELDS, "provenance_id")):
            return normalize_provenance(value).to_dict()
    return unknown_legacy_provenance().to_dict()


def _readonly_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def _staged_sqlite(source: Path) -> Iterable[Path]:
    """Yield a disposable copy of a SQLite database and its WAL sidecars.

    SQLite may update bytes in ``-shm`` while opening an otherwise read-only WAL
    database.  Lineage inspection therefore never opens the evidence source
    directly: the main database, WAL and SHM are copied first, and all SQLite
    activity is confined to a temporary directory.
    """

    with tempfile.TemporaryDirectory(prefix="knowe-lineage-") as tmp:
        staged = Path(tmp) / source.name
        copied = False
        for suffix in ("", "-wal", "-shm"):
            src = Path(f"{source}{suffix}")
            if not src.exists():
                continue
            shutil.copy2(src, Path(f"{staged}{suffix}"))
            copied = True
        if not copied:
            raise FileNotFoundError(source)
        yield staged


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table.replace("_", "").isalnum():
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _row_provenance(row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("provenance")
    if isinstance(nested, Mapping):
        return normalize_provenance(nested).to_dict()
    raw = row.get("provenance_json") if isinstance(row, Mapping) else None
    parsed = _read_json(raw)
    if parsed:
        return normalize_provenance(parsed).to_dict()
    flat = {
        field: row.get(field)
        for field in (*LINEAGE_FIELDS, "migration_epoch")
        if isinstance(row, Mapping) and row.get(field) not in (None, "")
    }
    return normalize_provenance(flat).to_dict() if flat else unknown_legacy_provenance().to_dict()


def _sqlite_records(db_path: Path, task_id: str) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    result = {"task_runs": [], "completions": [], "runtime_events": []}
    warnings: list[str] = []
    try:
        with _staged_sqlite(db_path) as staged_db:
            with _readonly_connect(staged_db) as conn:
                tables = _tables(conn)
                specs = (
                    ("task_runs", "task_runs", "payload_json", "created_at"),
                    ("deliveries", "completions", "payload_json", "created_at"),
                    ("runtime_events", "runtime_events", "event_json", "sequence"),
                )
                for table, bucket, payload_col, order_col in specs:
                    if table not in tables:
                        continue
                    cols = _columns(conn, table)
                    if "task_id" not in cols:
                        continue
                    select_cols = list(cols)
                    order = order_col if order_col in cols else "rowid"
                    rows = conn.execute(
                        f"SELECT {', '.join(select_cols)} FROM {table} WHERE task_id=? ORDER BY {order}",
                        (task_id,),
                    ).fetchall()
                    for sqlite_row in rows:
                        row = dict(sqlite_row)
                        payload = _read_json(row.get(payload_col))
                        provenance = _row_provenance(row, payload)
                        result[bucket].append({
                            "source": str(db_path),
                            "table": table,
                            "task_id": task_id,
                            "run_id": str(row.get("run_id") or payload.get("run_id") or ""),
                            "delivery_id": str(row.get("delivery_id") or payload.get("delivery_id") or ""),
                            "state": str(row.get("state") or payload.get("state") or ""),
                            "sequence": row.get("sequence"),
                            "type": str(row.get("type") or payload.get("type") or ""),
                            "provenance": provenance,
                        })
    except (OSError, sqlite3.Error) as exc:
        warnings.append(f"sqlite_read_failed:{db_path}:{type(exc).__name__}")
    return result, warnings


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key.strip()] = value.replace('\\"', '"').replace("\\'", "'")
    return result


def _front_provenance(front: Mapping[str, Any]) -> dict[str, Any]:
    candidate = {
        key: front.get(key)
        for key in (
            "provenance_schema_version", "provenance_id", "build_id", "git_commit",
            "runtime_schema_version", "harness_schema_version", "prompt_bundle_version",
            "migration_epoch", "build_manifest_sha256", "source_tree_sha256",
            "schema_registry_sha256", "startup_id", "recorded_at",
        )
        if front.get(key) not in (None, "")
    }
    status = str(front.get("provenance") or "")
    if status:
        candidate["status"] = status
    return normalize_provenance(candidate).to_dict() if candidate else unknown_legacy_provenance().to_dict()


def _report_records(internal_root: Path, task_id: str) -> list[dict[str, Any]]:
    handoffs = internal_root / "handoffs"
    if not handoffs.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(handoffs.rglob("report-*.md")):
        front = _frontmatter(path)
        if str(front.get("task_id") or "") != task_id:
            continue
        out.append({
            "source": str(path),
            "task_id": task_id,
            "run_id": str(front.get("run_id") or ""),
            "delivery_id": str(front.get("delivery_id") or ""),
            "status": str(front.get("status") or ""),
            "report_hash": str(front.get("report_hash") or ""),
            "provenance": _front_provenance(front),
        })
    return out


def _jsonl_records(path: Path) -> Iterable[dict[str, Any]]:
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(value, Mapping):
                    yield dict(value)
    except (OSError, EOFError, gzip.BadGzipFile):
        return


def _memory_records(internal_root: Path, task_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    history = internal_root / "memory" / "history"
    if history.is_dir():
        paths = sorted(history.glob("segment-*.jsonl.gz")) + sorted(history.glob("*.jsonl"))
        for path in paths:
            for record in _jsonl_records(path):
                lineage = record.get("lineage") if isinstance(record.get("lineage"), Mapping) else {}
                if str(lineage.get("task_id") or "") != task_id:
                    continue
                out.append({
                    "source": str(path),
                    "kind": "project_memory_history",
                    "memory_id": f"m{int(record.get('i') or 0):012d}",
                    "task_id": task_id,
                    "run_id": str(lineage.get("run_id") or ""),
                    "delivery_id": str(lineage.get("delivery_id") or ""),
                    "provenance": _provenance_from(record),
                })
    for path in sorted((internal_root / "agents").glob("*/memory/state.json")):
        try:
            state = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(state, Mapping):
            continue
        last_task = state.get("last_task") if isinstance(state.get("last_task"), Mapping) else {}
        if str(last_task.get("task_id") or "") != task_id:
            continue
        out.append({
            "source": str(path),
            "kind": "agent_memory_projection",
            "task_id": task_id,
            "run_id": str(last_task.get("run_id") or ""),
            "delivery_id": str(last_task.get("delivery_id") or ""),
            "provenance": _provenance_from(last_task if last_task else state),
        })
    return out


def _knowledge_records(internal_root: Path, task_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    events = internal_root / "knowledge" / "events.jsonl"
    for event in _jsonl_records(events):
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        event_task = str(event.get("task_id") or metadata.get("task_id") or "")
        if event_task != task_id:
            continue
        out.append({
            "source": str(events),
            "kind": "knowledge_event",
            "type": str(event.get("type") or ""),
            "task_id": task_id,
            "run_id": str(event.get("run_id") or metadata.get("run_id") or ""),
            "delivery_id": str(event.get("delivery_id") or metadata.get("delivery_id") or ""),
            "provenance": _provenance_from(event),
        })
    graph_path = internal_root / "knowledge" / ".graph.json"
    try:
        graph = json.loads(graph_path.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        graph = {}
    if isinstance(graph, Mapping):
        sources = graph.get("sources") if isinstance(graph.get("sources"), Mapping) else {}
        for source_id, source in sources.items():
            if not isinstance(source, Mapping):
                continue
            lineage = source.get("lineage") if isinstance(source.get("lineage"), Mapping) else {}
            metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
            if str(lineage.get("task_id") or metadata.get("task_id") or "") != task_id:
                continue
            out.append({
                "source": str(graph_path),
                "kind": "knowledge_source_projection",
                "source_id": str(source_id),
                "task_id": task_id,
                "run_id": str(lineage.get("run_id") or metadata.get("run_id") or ""),
                "delivery_id": str(lineage.get("delivery_id") or metadata.get("delivery_id") or ""),
                "provenance": _provenance_from(source),
            })
    return out


def _find_manifest(data_root: Path, build_id: str) -> dict[str, Any] | None:
    if not build_id:
        return None
    candidates = [data_root / "harness" / "builds" / f"{build_id}.json"]
    candidates.extend(data_root.rglob(f"{build_id}.json"))
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            value = json.loads(resolved.read_text("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, Mapping) and str(value.get("build_id") or "") == build_id:
            return {"source": str(resolved), **dict(value)}
    return None


def _internal_root_for_db(db_path: Path) -> Path:
    return db_path.parent.parent if db_path.parent.name == "runtime" else db_path.parent


def resolve_task_lineage(data_root: str | Path, task_id: str) -> dict[str, Any]:
    """Resolve one task without opening any writable connection or mutating legacy data."""

    root = Path(data_root).expanduser().resolve()
    task = str(task_id or "").strip()
    if not task:
        raise ValueError("task_id is required")

    buckets: dict[str, list[dict[str, Any]]] = {
        "task_runs": [], "completions": [], "runtime_events": [],
        "reports": [], "memory": [], "knowledge": [],
    }
    warnings: list[str] = []
    internal_roots: set[Path] = set()
    for db_path in sorted(root.rglob("runtime.sqlite3")):
        records, db_warnings = _sqlite_records(db_path, task)
        warnings.extend(db_warnings)
        if any(records.values()):
            internal = _internal_root_for_db(db_path)
            internal_roots.add(internal)
            for key, values in records.items():
                buckets[key].extend(values)

    # A caller may point directly at an internal workspace with file projections but no DB.
    if (root / "handoffs").is_dir() or (root / "memory").is_dir() or (root / "knowledge").is_dir():
        internal_roots.add(root)

    for internal in sorted(internal_roots):
        buckets["reports"].extend(_report_records(internal, task))
        buckets["memory"].extend(_memory_records(internal, task))
        buckets["knowledge"].extend(_knowledge_records(internal, task))

    all_records = [item for values in buckets.values() for item in values]
    provenances = [normalize_provenance(item.get("provenance")).to_dict() for item in all_records]
    recorded = [p for p in provenances if p["status"] == RECORDED]
    legacy = [p for p in provenances if p["status"] == UNKNOWN_LEGACY]
    ids = sorted({p["provenance_id"] for p in recorded})
    lineage_tuples = sorted({tuple(str(p.get(field) or "") for field in LINEAGE_FIELDS) for p in recorded})

    violations: list[str] = []
    if len(ids) > 1 or len(lineage_tuples) > 1:
        violations.append("recorded_provenance_mismatch")
    if recorded and legacy:
        violations.append("mixed_recorded_and_unknown_legacy")
    for p in recorded:
        missing = [field for field in LINEAGE_FIELDS if not str(p.get(field) or "")]
        if missing:
            violations.append("recorded_missing:" + ",".join(missing))

    authoritative = recorded[0] if recorded and not violations else (
        unknown_legacy_provenance().to_dict() if all_records else None
    )
    projection_counts = {name: len(buckets[name]) for name in _REQUIRED_PROJECTIONS}
    projection_counts["runtime_events"] = len(buckets["runtime_events"])
    if not all_records:
        consistency_status = "not_found"
    elif violations:
        consistency_status = "mismatch"
    elif recorded:
        consistency_status = "consistent"
    else:
        consistency_status = "unknown_legacy"

    manifest = _find_manifest(root, str((authoritative or {}).get("build_id") or ""))
    lineage = None
    if authoritative is not None:
        lineage = {
            "status": authoritative["status"],
            "provenance_id": authoritative["provenance_id"],
            "build_id": authoritative["build_id"],
            "git_commit": authoritative["git_commit"],
            "runtime_schema_version": authoritative["runtime_schema_version"],
            "harness_schema_version": authoritative["harness_schema_version"],
            "prompt_bundle_version": authoritative["prompt_bundle_version"],
            "migration_epoch": authoritative["migration_epoch"],
            "startup_id": authoritative.get("startup_id", ""),
        }

    return {
        "lineage_schema_version": "1",
        "task_id": task,
        "data_root": str(root),
        "status": consistency_status,
        "lineage": lineage,
        "build_manifest": manifest,
        "consistency": {
            "ok": consistency_status in {"consistent", "unknown_legacy"},
            "status": consistency_status,
            "recorded_provenance_ids": ids,
            "unknown_legacy_records": len(legacy),
            "violations": sorted(set(violations)),
            "projection_counts": projection_counts,
        },
        "records": buckets,
        "warnings": warnings,
    }


__all__ = ["resolve_task_lineage"]
