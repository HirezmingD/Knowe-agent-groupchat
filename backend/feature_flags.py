"""Small, process-local feature flags for non-Worker product features.

Worker behavior is intentionally not configurable here.  The Worker Runtime and its
19-tool surface are a single, fixed architecture rather than a rollout profile.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Mapping


class FeatureFlag(str, Enum):
    MODEL_READINESS_GATE_V1 = "MODEL_READINESS_GATE_V1"
    COMPLETION_VIEW_V1 = "COMPLETION_VIEW_V1"
    SEEN_SPEECH_V1 = "SEEN_SPEECH_V1"
    IDENTITY_CONTRACT_V1 = "IDENTITY_CONTRACT_V1"


_DEFAULTS: dict[FeatureFlag, bool] = {flag: True for flag in FeatureFlag}
_TRUE = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE = frozenset({"0", "false", "no", "off", "disabled"})


def _read_flag(
    flag: FeatureFlag,
    *,
    default: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    source = os.environ if environ is None else environ
    fallback = _DEFAULTS[flag] if default is None else bool(default)
    raw = source.get(flag.value)
    if raw is None:
        raw = source.get(f"KNOWE_{flag.value}")
    if raw is None:
        return fallback
    value = str(raw).strip().casefold()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return fallback


def enabled(flag: FeatureFlag | str, *, default: bool | None = None) -> bool:
    try:
        normalized = flag if isinstance(flag, FeatureFlag) else FeatureFlag(str(flag))
    except ValueError:
        return bool(default) if default is not None else False
    return _read_flag(normalized, default=default)


def snapshot() -> dict[str, bool]:
    return {flag.value: enabled(flag) for flag in FeatureFlag}


__all__ = ["FeatureFlag", "enabled", "snapshot"]
