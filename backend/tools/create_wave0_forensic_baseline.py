#!/usr/bin/env python3
"""Create Wave 0 read-only forensic baselines for incident projects.

The source projects are opened read-only.  Outputs are written outside the source
``data/`` tree and are made read-only after verification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

ZERO_HASH = bytes(32)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_bytes(relative_path: Path) -> bytes:
    return os.fsencode(str(relative_path))


def path_display(relative_path: Path) -> str:
    """Return a reversible ASCII display for potentially non-UTF-8 paths."""
    return json.dumps(os.fsdecode(path_bytes(relative_path)), ensure_ascii=True)[1:-1]


def path_b64(relative_path: Path) -> str:
    return base64.b64encode(path_bytes(relative_path)).decode("ascii")


def iter_files(root: Path) -> Iterator[Path]:
    yield from sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: path_bytes(p.relative_to(root)))


def inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in iter_files(root):
        rel = path.relative_to(root)
        st = path.stat()
        rows.append(
            {
                "path_display": path_display(rel),
                "path_fs_b64": path_b64(rel),
                "sha256": sha256_file(path),
                "size": st.st_size,
                "mode": stat.S_IMODE(st.st_mode),
                "mtime_ns": st.st_mtime_ns,
            }
        )
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="ascii")


def write_manifest(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for row in rows:
            handle.write(f"{row['sha256']}  {row['path_display']}\n")


def canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): canonicalize(value[key]) for key in sorted(value, key=lambda key: str(key))}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, bytes):
        return {"$bytes_b64": base64.b64encode(value).decode("ascii")}
    return value


def parse_json_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[:1] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return value


def canonical_line(value: Any) -> bytes:
    return (
        json.dumps(canonicalize(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


@dataclass(frozen=True)
class SequenceReceipt:
    name: str
    path: str
    count: int
    content_sha256: str
    chain_sha256: str
    algorithm: str = "sha256(prev_digest_bytes || canonical_json_line_bytes)"


def write_sequence(path: Path, name: str, values: Iterable[Any]) -> SequenceReceipt:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = hashlib.sha256()
    chain = ZERO_HASH
    count = 0
    with path.open("wb") as handle:
        for value in values:
            line = canonical_line(value)
            handle.write(line)
            content.update(line)
            chain = hashlib.sha256(chain + line).digest()
            count += 1
    return SequenceReceipt(
        name=name,
        path=str(path.name),
        count=count,
        content_sha256=content.hexdigest(),
        chain_sha256=chain.hex(),
    )


def sqlite_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def sqlite_rows(connection: sqlite3.Connection, table: str, order_by: Sequence[str]) -> Iterator[dict[str, Any]]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    order = [column for column in order_by if column in columns]
    sql = f'SELECT * FROM "{table}"' + (" ORDER BY " + ", ".join(f'"{column}"' for column in order) if order else "")
    for row in connection.execute(sql):
        converted: dict[str, Any] = {}
        for key in row.keys():
            value = row[key]
            if key.endswith("_json"):
                converted[key] = parse_json_maybe(value)
            elif isinstance(value, bytes):
                converted[key] = {"$bytes_b64": base64.b64encode(value).decode("ascii")}
            else:
                converted[key] = value
        yield converted


def consistent_sqlite_export(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for candidate in (destination, Path(f"{destination}-wal"), Path(f"{destination}-shm")):
        if candidate.exists():
            candidate.unlink()
    with sqlite_readonly(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        dst.commit()
        # Produce a self-contained portable DB; the source staging copy retains WAL semantics.
        mode = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise RuntimeError(f"failed to normalize forensic SQLite journal mode: {mode}")
        dst.commit()


def sqlite_schema(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return [dict(row) for row in rows]


def write_sql_dump(connection: sqlite3.Connection, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for line in connection.iterdump():
            handle.write(line)
            handle.write("\n")


def iter_jsonl_file(path: Path, root: Path) -> Iterator[dict[str, Any]]:
    raw = path.read_bytes().splitlines()
    rel = path.relative_to(root)
    for line_number, line in enumerate(raw, start=1):
        if not line.strip():
            continue
        try:
            value: Any = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {"$raw_b64": base64.b64encode(line).decode("ascii")}
        yield {
            "source_path_display": path_display(rel),
            "source_path_fs_b64": path_b64(rel),
            "line_number": line_number,
            "record": value,
        }


def frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    result: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        value = raw.strip().strip('"').strip("'")
        result[key.strip()] = value
    return result


def handoff_timeline(project: Path) -> Iterator[dict[str, Any]]:
    handoffs = project / "handoffs"
    if not handoffs.exists():
        return
    candidates = sorted((p for p in handoffs.rglob("*.md") if p.is_file()), key=lambda p: path_bytes(p.relative_to(project)))
    for path in candidates:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        rel = path.relative_to(project)
        yield {
            "source_path_display": path_display(rel),
            "source_path_fs_b64": path_b64(rel),
            "kind": "approval" if path.name.startswith(".approval-") else "instruction" if path.name.startswith("instruction-") else "report" if path.name.startswith("report-") else "other",
            "sha256": sha256_bytes(raw),
            "size": len(raw),
            "frontmatter": frontmatter(text),
        }


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"$raw_b64": base64.b64encode(path.read_bytes()).decode("ascii")}


def collect_project_sequences(project: Path, consistent_db: Path, sequence_dir: Path) -> list[SequenceReceipt]:
    receipts: list[SequenceReceipt] = []
    with sqlite_readonly(consistent_db) as connection:
        table_specs = (
            ("runtime_events", ("task_id", "run_id", "sequence", "event_id")),
            ("task_runs", ("task_id", "run_id")),
            ("evidence", ("task_id", "created_at", "evidence_id")),
            ("deliveries", ("task_id", "run_id", "created_at", "delivery_id")),
            ("delivery_artifacts", ("delivery_id", "path")),
        )
        for table, order in table_specs:
            receipts.append(write_sequence(sequence_dir / f"sqlite_{table}.jsonl", f"sqlite.{table}", sqlite_rows(connection, table, order)))

    memory_history = sorted(project.glob("memory/history/*.jsonl"), key=lambda p: path_bytes(p.relative_to(project)))
    receipts.append(
        write_sequence(
            sequence_dir / "memory_history.jsonl",
            "memory.history",
            (record for path in memory_history for record in iter_jsonl_file(path, project)),
        )
    )

    knowledge_events = project / "knowledge/events.jsonl"
    receipts.append(
        write_sequence(
            sequence_dir / "knowledge_events.jsonl",
            "knowledge.events",
            iter_jsonl_file(knowledge_events, project) if knowledge_events.exists() else (),
        )
    )

    agent_state_paths = sorted(project.glob("agents/*/memory/state.json"), key=lambda p: path_bytes(p.relative_to(project)))
    receipts.append(
        write_sequence(
            sequence_dir / "agent_memory_states.jsonl",
            "agent.memory.states",
            (
                {
                    "source_path_display": path_display(path.relative_to(project)),
                    "source_path_fs_b64": path_b64(path.relative_to(project)),
                    "record": read_json(path),
                }
                for path in agent_state_paths
            ),
        )
    )

    receipts.append(write_sequence(sequence_dir / "handoff_timeline.jsonl", "handoff.timeline", handoff_timeline(project)))
    return receipts


def chmod_read_only_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_dir():
                path.chmod(0o555)
            else:
                path.chmod(0o444)
        except OSError:
            pass
    root.chmod(0o555)


def compare_inventory(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    before_map = {row["path_fs_b64"]: row for row in before}
    after_map = {row["path_fs_b64"]: row for row in after}
    added = sorted(set(after_map) - set(before_map))
    removed = sorted(set(before_map) - set(after_map))
    changed = sorted(
        key for key in set(before_map) & set(after_map)
        if before_map[key]["sha256"] != after_map[key]["sha256"] or before_map[key]["size"] != after_map[key]["size"]
    )
    return {
        "identical": not added and not removed and not changed,
        "before_file_count": len(before),
        "after_file_count": len(after),
        "added_path_fs_b64": added,
        "removed_path_fs_b64": removed,
        "changed_path_fs_b64": changed,
    }


def build_project(project: Path, output_root: Path) -> dict[str, Any]:
    project_id = project.name
    before = inventory(project)
    project_output = output_root / "golden" / project_id
    raw_output = project_output / "raw"
    sqlite_output = project_output / "sqlite_export"
    sequence_output = project_output / "sequences"
    project_output.mkdir(parents=True, exist_ok=True)

    shutil.copytree(project, raw_output, copy_function=shutil.copy2, symlinks=True)
    raw_inventory = inventory(raw_output)
    raw_compare = compare_inventory(before, raw_inventory)
    if not raw_compare["identical"]:
        raise RuntimeError(f"raw golden copy differs from source for {project_id}: {raw_compare}")

    consistent_db = sqlite_output / "runtime.consistent.sqlite3"
    # SQLite readers may update bytes in a WAL shared-memory file even in mode=ro.
    # Therefore, open only a disposable copy of the main/WAL/SHM triad.
    with tempfile.TemporaryDirectory(prefix=f"{project_id}-sqlite-stage-", dir=str(output_root)) as temp_dir:
        staged_runtime = Path(temp_dir) / "runtime"
        staged_runtime.mkdir(parents=True, exist_ok=True)
        for name in ("runtime.sqlite3", "runtime.sqlite3-wal", "runtime.sqlite3-shm"):
            source_part = raw_output / "runtime" / name
            if source_part.exists():
                shutil.copy2(source_part, staged_runtime / name)
        consistent_sqlite_export(staged_runtime / "runtime.sqlite3", consistent_db)
    with sqlite_readonly(consistent_db) as connection:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError(f"consistent SQLite export failed integrity check for {project_id}: {integrity}")
        write_json(sqlite_output / "schema.json", sqlite_schema(connection))
        write_sql_dump(connection, sqlite_output / "runtime.sql")
        sqlite_counts = {
            row[0]: connection.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        }
    (sqlite_output / "integrity_check.txt").write_text("\n".join(integrity) + "\n", encoding="ascii")

    receipts = collect_project_sequences(project, consistent_db, sequence_output)
    write_json(sequence_output / "SEQUENCE_HASHES.json", [receipt.__dict__ for receipt in receipts])

    write_json(project_output / "SOURCE_INVENTORY.json", before)
    write_manifest(project_output / "SOURCE_MANIFEST.sha256", before)
    write_json(project_output / "RAW_COPY_VERIFICATION.json", raw_compare)

    after = inventory(project)
    source_compare = compare_inventory(before, after)
    if not source_compare["identical"]:
        raise RuntimeError(f"source data changed while creating baseline for {project_id}: {source_compare}")
    write_json(project_output / "SOURCE_POST_EXPORT_VERIFICATION.json", source_compare)

    result = {
        "project_id": project_id,
        "source_path": str(project),
        "source_file_count": len(before),
        "source_manifest_sha256": sha256_file(project_output / "SOURCE_MANIFEST.sha256"),
        "raw_copy_identical": raw_compare["identical"],
        "source_unchanged": source_compare["identical"],
        "consistent_sqlite_sha256": sha256_file(consistent_db),
        "sqlite_integrity_check": integrity,
        "sqlite_table_counts": sqlite_counts,
        "sequence_receipts": [receipt.__dict__ for receipt in receipts],
    }
    write_json(project_output / "PROJECT_FORENSIC_RECEIPT.json", result)

    # Exclude the two self-referential inventory files; every other artifact is covered.
    artifact_inventory = [
        row for row in inventory(project_output)
        if row["path_display"] not in {"GOLDEN_MANIFEST.sha256", "GOLDEN_INVENTORY.json"}
    ]
    write_json(project_output / "GOLDEN_INVENTORY.json", artifact_inventory)
    write_manifest(project_output / "GOLDEN_MANIFEST.sha256", artifact_inventory)
    chmod_read_only_tree(project_output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True, help="Directory containing incident project_* directories")
    parser.add_argument("--output-root", type=Path, required=True, help="Forensic artifact output directory (outside data-root)")
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    output_root = args.output_root.resolve()
    if data_root == output_root or data_root in output_root.parents or output_root in data_root.parents:
        raise SystemExit("data-root and output-root must be disjoint directories")

    projects = sorted(path for path in data_root.iterdir() if path.is_dir() and path.name.startswith("project_"))
    if not projects:
        raise SystemExit(f"no project_* directories found under {data_root}")

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    global_before = {project.name: inventory(project) for project in projects}
    results = []
    for project in projects:
        print(f"[forensic] building {project.name}", flush=True)
        result = build_project(project, output_root)
        results.append(result)
        print(
            f"[forensic] {project.name}: {result['source_file_count']} source files; "
            f"SQLite integrity={result['sqlite_integrity_check']}; source unchanged",
            flush=True,
        )
    global_after = {project.name: inventory(project) for project in projects}
    global_comparisons = {
        project.name: compare_inventory(global_before[project.name], global_after[project.name]) for project in projects
    }
    if not all(item["identical"] for item in global_comparisons.values()):
        raise RuntimeError(f"one or more source projects changed: {global_comparisons}")

    index = {
        "artifact_type": "wave0_incident_forensic_baseline",
        "schema_version": 1,
        "created_at": utc_now(),
        "source_data_root": str(data_root),
        "source_mutation_policy": "read_only; no source files changed",
        "raw_copy_policy": "byte-for-byte file copy including SQLite main/WAL/SHM",
        "sqlite_export_policy": "SQLite online backup from mode=ro/query_only connection",
        "projects": results,
        "source_immutability": global_comparisons,
    }
    write_json(output_root / "GOLDEN_COPY_INDEX.json", index)
    write_json(output_root / "SOURCE_IMMUTABILITY_VERIFICATION.json", global_comparisons)
    (output_root / "SOURCE_IMMUTABILITY_VERIFICATION.txt").write_text(
        "\n".join(
            f"{project}: {'IDENTICAL' if result['identical'] else 'CHANGED'} "
            f"({result['before_file_count']} files before, {result['after_file_count']} files after)"
            for project, result in sorted(global_comparisons.items())
        ) + "\n",
        encoding="ascii",
    )
    write_json(
        output_root / "EVENT_SEQUENCE_INDEX.json",
        {
            result["project_id"]: result["sequence_receipts"]
            for result in results
        },
    )
    print(f"[forensic] source immutability verified for {sum(item['source_file_count'] for item in results)} files", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
