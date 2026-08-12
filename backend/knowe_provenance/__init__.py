"""Wave 0 version lineage and forensic provenance API."""

from .build_manifest import (
    activate_build,
    active_build_manifest,
    current_provenance,
    current_provenance_dict,
    generate_build_manifest,
    set_active_provenance,
)
from .lineage import resolve_task_lineage
from .model import (
    LINEAGE_FIELDS,
    RECORDED,
    UNKNOWN_LEGACY,
    Provenance,
    assert_provenance_matches,
    normalize_provenance,
    provenance_dict,
    provenance_matches,
    recorded_provenance,
    unknown_legacy_provenance,
)
from .schema_registry import (
    component_version,
    harness_schema_version,
    load_schema_registry,
    migration_epoch,
    runtime_schema_version,
    schema_registry_hash,
)

__all__ = [
    "LINEAGE_FIELDS",
    "RECORDED",
    "UNKNOWN_LEGACY",
    "Provenance",
    "activate_build",
    "active_build_manifest",
    "assert_provenance_matches",
    "component_version",
    "current_provenance",
    "current_provenance_dict",
    "generate_build_manifest",
    "harness_schema_version",
    "load_schema_registry",
    "migration_epoch",
    "normalize_provenance",
    "provenance_dict",
    "provenance_matches",
    "recorded_provenance",
    "resolve_task_lineage",
    "runtime_schema_version",
    "schema_registry_hash",
    "set_active_provenance",
    "unknown_legacy_provenance",
]
