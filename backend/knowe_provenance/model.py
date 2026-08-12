"""Canonical provenance values and legacy-safe normalization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

UNKNOWN_LEGACY = "unknown_legacy"
RECORDED = "recorded"
PROVENANCE_SCHEMA_VERSION = 1

LINEAGE_FIELDS = (
    "build_id",
    "git_commit",
    "runtime_schema_version",
    "harness_schema_version",
    "prompt_bundle_version",
)


@dataclass(frozen=True)
class Provenance:
    status: str
    provenance_schema_version: int
    provenance_id: str
    build_id: str
    git_commit: str
    runtime_schema_version: str
    harness_schema_version: str
    prompt_bundle_version: str
    migration_epoch: int
    build_manifest_sha256: str = ""
    source_tree_sha256: str = ""
    schema_registry_sha256: str = ""
    startup_id: str = ""
    recorded_at: str = ""

    @property
    def is_recorded(self) -> bool:
        return self.status == RECORDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provenance_schema_version": self.provenance_schema_version,
            "provenance_id": self.provenance_id,
            "build_id": self.build_id,
            "git_commit": self.git_commit,
            "runtime_schema_version": self.runtime_schema_version,
            "harness_schema_version": self.harness_schema_version,
            "prompt_bundle_version": self.prompt_bundle_version,
            "migration_epoch": self.migration_epoch,
            "build_manifest_sha256": self.build_manifest_sha256,
            "source_tree_sha256": self.source_tree_sha256,
            "schema_registry_sha256": self.schema_registry_sha256,
            "startup_id": self.startup_id,
            "recorded_at": self.recorded_at,
        }


def _fingerprint(value: Mapping[str, Any]) -> str:
    payload = {
        key: value.get(key, "")
        for key in (
            *LINEAGE_FIELDS,
            "migration_epoch",
            "build_manifest_sha256",
            "source_tree_sha256",
            "schema_registry_sha256",
            "startup_id",
        )
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "prov_" + hashlib.sha256(encoded).hexdigest()[:24]


def unknown_legacy_provenance() -> Provenance:
    """Return the only legal representation for records that predate provenance.

    Empty version fields are deliberate.  They prevent a current build from being
    retroactively attributed to historical data whose producing code is unknown.
    """

    return Provenance(
        status=UNKNOWN_LEGACY,
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        provenance_id=UNKNOWN_LEGACY,
        build_id="",
        git_commit="",
        runtime_schema_version="",
        harness_schema_version="",
        prompt_bundle_version="",
        migration_epoch=0,
    )


def recorded_provenance(value: Mapping[str, Any]) -> Provenance:
    data = dict(value)
    missing = [field for field in LINEAGE_FIELDS if not str(data.get(field) or "").strip()]
    if missing:
        raise ValueError(f"recorded provenance missing required fields: {', '.join(missing)}")
    data["migration_epoch"] = int(data.get("migration_epoch") or 0)
    if data["migration_epoch"] < 1:
        raise ValueError("recorded provenance migration_epoch must be >= 1")
    data.setdefault("provenance_schema_version", PROVENANCE_SCHEMA_VERSION)
    data.setdefault("status", RECORDED)
    data["status"] = RECORDED
    data["provenance_id"] = str(data.get("provenance_id") or _fingerprint(data))
    return Provenance(
        status=RECORDED,
        provenance_schema_version=int(data.get("provenance_schema_version") or PROVENANCE_SCHEMA_VERSION),
        provenance_id=data["provenance_id"],
        build_id=str(data.get("build_id") or ""),
        git_commit=str(data.get("git_commit") or ""),
        runtime_schema_version=str(data.get("runtime_schema_version") or ""),
        harness_schema_version=str(data.get("harness_schema_version") or ""),
        prompt_bundle_version=str(data.get("prompt_bundle_version") or ""),
        migration_epoch=int(data["migration_epoch"]),
        build_manifest_sha256=str(data.get("build_manifest_sha256") or ""),
        source_tree_sha256=str(data.get("source_tree_sha256") or ""),
        schema_registry_sha256=str(data.get("schema_registry_sha256") or ""),
        startup_id=str(data.get("startup_id") or ""),
        recorded_at=str(data.get("recorded_at") or ""),
    )


def normalize_provenance(
    value: Provenance | Mapping[str, Any] | None,
    *,
    legacy_if_missing: bool = True,
) -> Provenance:
    if isinstance(value, Provenance):
        return value
    if not isinstance(value, Mapping):
        if legacy_if_missing:
            return unknown_legacy_provenance()
        raise ValueError("provenance is required")
    data = dict(value)
    nested = data.get("provenance")
    if isinstance(nested, Mapping) and not any(key in data for key in LINEAGE_FIELDS):
        data = dict(nested)
    status = str(data.get("status") or data.get("provenance") or "").strip()
    if status == UNKNOWN_LEGACY:
        return unknown_legacy_provenance()
    if not any(str(data.get(field) or "").strip() for field in LINEAGE_FIELDS):
        if legacy_if_missing:
            return unknown_legacy_provenance()
        raise ValueError("provenance has no lineage fields")
    try:
        return recorded_provenance(data)
    except (TypeError, ValueError):
        if legacy_if_missing:
            return unknown_legacy_provenance()
        raise


def provenance_dict(value: Provenance | Mapping[str, Any] | None) -> dict[str, Any]:
    return normalize_provenance(value).to_dict()


def provenance_matches(left: Any, right: Any) -> bool:
    a = normalize_provenance(left)
    b = normalize_provenance(right)
    if a.status == UNKNOWN_LEGACY or b.status == UNKNOWN_LEGACY:
        return a.status == b.status
    return a.provenance_id == b.provenance_id and all(
        getattr(a, field) == getattr(b, field) for field in LINEAGE_FIELDS
    )


def assert_provenance_matches(left: Any, right: Any, *, context: str = "run") -> None:
    if not provenance_matches(left, right):
        a = normalize_provenance(left)
        b = normalize_provenance(right)
        raise ValueError(
            f"{context} provenance mismatch: {a.provenance_id or a.status} != {b.provenance_id or b.status}"
        )


__all__ = [
    "LINEAGE_FIELDS",
    "PROVENANCE_SCHEMA_VERSION",
    "Provenance",
    "RECORDED",
    "UNKNOWN_LEGACY",
    "assert_provenance_matches",
    "normalize_provenance",
    "provenance_dict",
    "provenance_matches",
    "recorded_provenance",
    "unknown_legacy_provenance",
]
