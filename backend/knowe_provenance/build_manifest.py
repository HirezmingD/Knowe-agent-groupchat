"""Content-addressed build manifests and append-only service startup events."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .model import Provenance, recorded_provenance, unknown_legacy_provenance
from .schema_registry import load_schema_registry, schema_registry_hash

_LOCK = threading.RLock()
_ACTIVE: Provenance | None = None
_ACTIVE_MANIFEST: dict[str, Any] | None = None

_SOURCE_SUFFIXES = {
    ".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".ini", ".cfg",
}
_EXCLUDED_DIRS = {
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "data", "logs", "wave0_artifacts",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        # Tests and historical backups are not part of the production build identity.
        if rel.parts and rel.parts[0] in {"tests", "benchmarks"}:
            continue
        if path.name.endswith((".v23backup", ".v24")) or "_test_old" in path.name:
            continue
        yield path


def _tree_hash(root: Path, files: Iterable[Path]) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def _prompt_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for base in (root / "knowe_prompts", root / "backend" / "souls"):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
                files.append(path)
    return sorted(set(files), key=lambda item: item.as_posix())


def _git_commit(root: Path) -> tuple[str, str]:
    explicit = os.environ.get("KNOWE_GIT_COMMIT", "").strip()
    if explicit:
        return explicit, "environment"
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unavailable"
    value = completed.stdout.strip()
    return (value, "git") if completed.returncode == 0 and value else ("unknown", "unavailable")


def generate_build_manifest(source_root: str | Path, *, application_version: str = "") -> dict[str, Any]:
    root = Path(source_root).expanduser().resolve()
    registry = load_schema_registry()
    registry_sha = schema_registry_hash(registry)
    source_sha, source_count = _tree_hash(root, _iter_source_files(root))
    prompt_sha, prompt_count = _tree_hash(root, _prompt_files(root))
    git_commit, git_source = _git_commit(root)
    identity = {
        "application_version": str(application_version or "unknown"),
        "git_commit": git_commit,
        "source_tree_sha256": source_sha,
        "schema_registry_sha256": registry_sha,
        "prompt_bundle_sha256": prompt_sha,
        "runtime_schema_version": str(registry["runtime_schema_version"]),
        "harness_schema_version": str(registry["harness_schema_version"]),
        "migration_epoch": int(registry["migration_epoch"]),
    }
    build_id = "build_" + hashlib.sha256(_canonical(identity)).hexdigest()[:24]
    prompt_version = "prompt_" + prompt_sha[:24]
    manifest: dict[str, Any] = {
        "manifest_schema_version": int(registry["components"]["build_manifest"]),
        "build_id": build_id,
        "application_version": identity["application_version"],
        "git_commit": git_commit,
        "git_commit_source": git_source,
        "source_root": str(root),
        "source_tree_sha256": source_sha,
        "source_file_count": source_count,
        "prompt_bundle_version": prompt_version,
        "prompt_bundle_sha256": prompt_sha,
        "prompt_file_count": prompt_count,
        "runtime_schema_version": identity["runtime_schema_version"],
        "harness_schema_version": identity["harness_schema_version"],
        "migration_epoch": identity["migration_epoch"],
        "schema_registry_sha256": registry_sha,
        "schema_registry": registry,
    }
    manifest["build_manifest_sha256"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    return manifest


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def activate_build(
    data_root: str | Path,
    source_root: str | Path,
    *,
    application_version: str = "",
    started_at: str | None = None,
) -> Provenance:
    """Persist one build manifest and one auditable startup event, then activate it.

    The build manifest is content-addressed and can be reused across starts.  Startup
    events are never overwritten: every invocation appends a distinct startup_id.
    """

    global _ACTIVE, _ACTIVE_MANIFEST
    with _LOCK:
        manifest = generate_build_manifest(source_root, application_version=application_version)
        startup_id = "startup_" + uuid.uuid4().hex
        recorded_at = started_at or utc_now()
        provenance = recorded_provenance({
            "build_id": manifest["build_id"],
            "git_commit": manifest["git_commit"],
            "runtime_schema_version": manifest["runtime_schema_version"],
            "harness_schema_version": manifest["harness_schema_version"],
            "prompt_bundle_version": manifest["prompt_bundle_version"],
            "migration_epoch": manifest["migration_epoch"],
            "build_manifest_sha256": manifest["build_manifest_sha256"],
            "source_tree_sha256": manifest["source_tree_sha256"],
            "schema_registry_sha256": manifest["schema_registry_sha256"],
            "startup_id": startup_id,
            "recorded_at": recorded_at,
        })
        root = Path(data_root).expanduser().resolve() / "harness"
        build_path = root / "builds" / f"{manifest['build_id']}.json"
        if build_path.exists():
            try:
                existing = json.loads(build_path.read_text("utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                existing = None
            if not isinstance(existing, Mapping) or existing.get("build_manifest_sha256") != manifest["build_manifest_sha256"]:
                raise RuntimeError(f"build manifest collision or corruption: {build_path}")
        else:
            _atomic_json(build_path, manifest)
        current = {
            "activated_at": recorded_at,
            "build_manifest": str(build_path),
            "provenance": provenance.to_dict(),
        }
        _atomic_json(root / "current_build.json", current)
        startup_event = {
            "event_schema_version": int(manifest["schema_registry"]["components"]["startup_event"]),
            "type": "service_started",
            "started_at": recorded_at,
            "pid": os.getpid(),
            "build_manifest": str(build_path),
            **provenance.to_dict(),
        }
        _append_jsonl(root / "startup_events.jsonl", startup_event)
        _ACTIVE = provenance
        _ACTIVE_MANIFEST = dict(manifest)
        return provenance


def current_provenance() -> Provenance:
    with _LOCK:
        return _ACTIVE or unknown_legacy_provenance()


def current_provenance_dict() -> dict[str, Any]:
    return current_provenance().to_dict()


def active_build_manifest() -> dict[str, Any] | None:
    with _LOCK:
        return None if _ACTIVE_MANIFEST is None else dict(_ACTIVE_MANIFEST)


def set_active_provenance(value: Provenance | Mapping[str, Any] | None) -> None:
    """Test/integration seam. ``None`` resets to the explicit legacy marker."""

    global _ACTIVE, _ACTIVE_MANIFEST
    from .model import normalize_provenance

    with _LOCK:
        _ACTIVE = None if value is None else normalize_provenance(value)
        if value is None:
            _ACTIVE_MANIFEST = None


__all__ = [
    "activate_build",
    "active_build_manifest",
    "current_provenance",
    "current_provenance_dict",
    "generate_build_manifest",
    "set_active_provenance",
    "utc_now",
]
