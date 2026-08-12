"""Versioned schema registry shared by the Harness and Worker persistence boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_REGISTRY: dict[str, Any] = {
    "registry_schema_version": 1,
    "migration_epoch": 5,
    "runtime_schema_version": "3",
    "harness_schema_version": "7",
    "components": {
        "provenance": "1",
        "build_manifest": "1",
        "startup_event": "1",
        "runtime.task_run": "2",
        "runtime.delivery": "2",
        "runtime.event": "2",
        "harness.report": "2",
        "harness.memory_projection": "3",
        "harness.memory_history": "2",
        "harness.agent_memory": "2",
        "harness.knowledge_graph": "1",
        "harness.knowledge_event": "2",
        "harness.structured_fact": "1",
        "harness.knowledge_projection": "1",
        "lineage": "1",
        "harness.task_envelope": "1",
        # Completion/outbox schemas are initialized by SQLiteCompletionStore.  Keep
        # them in the bundled fallback registry as well as the production JSON
        # registry so a source-only/package deployment never fails with an unknown
        # component before it can perform or audit a migration.
        "harness.completion_event": "2",
        "harness.completion_outbox": "1",
        "harness.wait_token": "1",
        "harness.decision_event": "1",
        "harness.agent_completion_outcome": "1",
        "harness.task_result": "1",
        "harness.task_journal": "1",
    },
}


def _candidate_paths() -> tuple[Path, ...]:
    env = os.environ.get("KNOWE_SCHEMA_REGISTRY", "").strip()
    package_root = Path(__file__).resolve().parents[1]
    paths: list[Path] = []
    if env:
        paths.append(Path(env).expanduser())
    paths.extend((package_root / "schema_registry.json", Path.cwd() / "schema_registry.json"))
    return tuple(paths)


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    components = value.get("components")
    if not isinstance(components, Mapping):
        raise ValueError("schema registry must contain a components object")
    migration_epoch = int(value.get("migration_epoch") or 0)
    if migration_epoch < 1:
        raise ValueError("migration_epoch must be >= 1")
    result = {
        "registry_schema_version": int(value.get("registry_schema_version") or 1),
        "migration_epoch": migration_epoch,
        "runtime_schema_version": str(value.get("runtime_schema_version") or ""),
        "harness_schema_version": str(value.get("harness_schema_version") or ""),
        "components": {str(k): str(v) for k, v in components.items()},
    }
    if not result["runtime_schema_version"] or not result["harness_schema_version"]:
        raise ValueError("aggregate runtime/harness schema versions must be non-empty")
    return result


@lru_cache(maxsize=1)
def load_schema_registry() -> dict[str, Any]:
    for path in _candidate_paths():
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(data, Mapping):
            return _validate(data)
    return _validate(_DEFAULT_REGISTRY)


def schema_registry_hash(registry: Mapping[str, Any] | None = None) -> str:
    payload = dict(registry or load_schema_registry())
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def component_version(component: str) -> str:
    registry = load_schema_registry()
    value = registry["components"].get(str(component))
    if value is None:
        raise KeyError(f"unknown schema component: {component}")
    return str(value)


def migration_epoch() -> int:
    return int(load_schema_registry()["migration_epoch"])


def runtime_schema_version() -> str:
    return str(load_schema_registry()["runtime_schema_version"])


def harness_schema_version() -> str:
    return str(load_schema_registry()["harness_schema_version"])


__all__ = [
    "component_version",
    "harness_schema_version",
    "load_schema_registry",
    "migration_epoch",
    "runtime_schema_version",
    "schema_registry_hash",
]
