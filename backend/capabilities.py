# Knowe v2.2 — human projection of the one fixed Worker registry.
"""Render Worker capabilities for Coordinator/Zinnia prompts.

The production tool inventory lives only in :mod:`backend.runtime`.  This module groups
that fixed tuple into human wording; it neither enables nor filters tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .runtime import WORKER_TOOL_NAMES
from .i18n_backend import msg

log = logging.getLogger("knowe.capabilities")


@dataclass(frozen=True)
class Capability:
    # [v1.0.21.3] 字段存 locales key（代码零中文）；渲染时按当前语言取文案
    title: str
    blurb: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class WorkerCapabilityProjection:
    tool_names: tuple[str, ...]
    lines: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "knowe.worker-capabilities.v2.2",
            "tool_names": list(self.tool_names),
            "lines": list(self.lines),
        }


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "cap.001",
        "cap.002",
        WORKER_TOOL_NAMES[:6],
    ),
    Capability(
        "cap.003",
        "cap.004",
        WORKER_TOOL_NAMES[6:9],
    ),
    Capability(
        "cap.005",
        "cap.006",
        ("safe_bash",),
    ),
    Capability(
        "cap.007",
        "cap.008",
        ("web_search", "web_extract"),
    ),
    Capability(
        "cap.009",
        "cap.010",
        WORKER_TOOL_NAMES[12:],
    ),
)


def worker_tool_names(*_ignored: Any, **_ignored_kw: Any) -> tuple[str, ...]:
    return WORKER_TOOL_NAMES


def capability_lines(
    names: tuple[str, ...] | None = None,
    **_ignored: Any,
) -> list[str]:
    have = set(names or WORKER_TOOL_NAMES)
    lines: list[str] = []
    covered: set[str] = set()
    for capability in CAPABILITIES:
        owned = tuple(name for name in capability.tools if name in have)
        if not owned:
            continue
        covered.update(owned)
        suffix = msg("cap.011") if "read_external_file" in owned else ""
        lines.append(msg("cap.020", title=msg(capability.title), blurb=msg(capability.blurb), suffix=suffix))
    leftovers = tuple(name for name in WORKER_TOOL_NAMES if name in have and name not in covered)
    if leftovers:
        log.warning("[capabilities] ungrouped fixed Worker tools: %s", "、".join(leftovers))
        lines.append(msg("cap.021", names=msg("cap.022").join(leftovers)))
    return lines


_DETOUR_HINTS: tuple[tuple[str, str], ...] = (
    ("safe_bash", "cap.013"),
    ("browser_navigate", "cap.014"),
    ("web_search", "cap.015"),
    ("safe_patch", "cap.016"),
)


def detour_hints(names: tuple[str, ...] | None = None, **_ignored: Any) -> list[str]:
    have = set(names or WORKER_TOOL_NAMES)
    return [msg(text) for name, text in _DETOUR_HINTS if name in have]


def capability_projection(*_ignored: Any, **_ignored_kw: Any) -> WorkerCapabilityProjection:
    names = worker_tool_names()
    return WorkerCapabilityProjection(names, tuple(capability_lines(names)))


def coordinator_block(*_ignored: Any, **_ignored_kw: Any) -> str:
    try:
        names = worker_tool_names()
        lines = capability_lines(names)
        detours = detour_hints(names)
    except Exception:
        log.warning("[capabilities] failed to render Coordinator capability context", exc_info=True)
        return ""
    if not lines:
        return ""
    return msg("cap.017").format(
        lines="\n".join(lines),
        detours="\n".join(f"- {item}" for item in detours) or msg("cap.018"),
    )


def zinnia_block(*_ignored: Any, **_ignored_kw: Any) -> str:
    try:
        lines = capability_lines()
    except Exception:
        log.warning("[capabilities] failed to render Zinnia capability context", exc_info=True)
        return ""
    return msg("cap.019").format(lines="\n".join(lines)) if lines else ""


__all__ = [
    "CAPABILITIES",
    "Capability",
    "WorkerCapabilityProjection",
    "capability_projection",
    "capability_lines",
    "coordinator_block",
    "detour_hints",
    "worker_tool_names",
    "zinnia_block",
]
