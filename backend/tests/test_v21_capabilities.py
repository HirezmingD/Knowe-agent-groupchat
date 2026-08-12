"""v2.2 Worker capability projection tests.

The registry is a fixed 19-tool contract.  Capability text may group that contract for
humans, but it must never filter tools in response to feature flags or service health.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from backend import capabilities, tools_knowe
from backend.capabilities import (
    CAPABILITIES,
    capability_lines,
    capability_projection,
    coordinator_block,
    detour_hints,
    worker_tool_names,
    zinnia_block,
)
from backend.runtime import WORKER_TOOL_NAMES


class FakeEngine:
    project_id = "proj-test"
    workspace_root = Path("/tmp/ws")
    internal_workspace = Path("/tmp/int")


def test_registry_and_projection_share_exact_fixed_order() -> None:
    registry = tools_knowe.build_worker_registry(FakeEngine(), "fe_1")
    assert tuple(registry.names()) == WORKER_TOOL_NAMES
    assert worker_tool_names() == WORKER_TOOL_NAMES
    assert len(WORKER_TOOL_NAMES) == 19


def test_every_fixed_tool_is_grouped_once() -> None:
    grouped = [name for capability in CAPABILITIES for name in capability.tools]
    assert tuple(grouped) == WORKER_TOOL_NAMES
    assert len(set(grouped)) == 19


def test_service_flags_never_hide_tools_or_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_knowe,
        "CONFIG",
        dataclasses.replace(
            tools_knowe.CONFIG,
            terminal_enabled=False,
            web_enabled=False,
            browser_enabled=False,
        ),
    )
    assert tuple(tools_knowe.build_worker_registry(FakeEngine(), "fe_1").names()) == WORKER_TOOL_NAMES
    text = "\n".join(capability_lines())
    assert "运行命令" in text
    assert "上网" in text
    assert "开浏览器" in text


def test_human_projection_mentions_terminal_browser_and_external_boundary() -> None:
    text = "\n".join(capability_lines())
    assert "受控 shell" in text
    assert "Chromium" in text
    assert "任务授权外部根目录" in text


def test_projection_is_machine_readable_and_complete() -> None:
    value = capability_projection().to_dict()
    assert value["schema"] == "knowe.worker-capabilities.v2.2"
    assert tuple(value["tool_names"]) == WORKER_TOOL_NAMES
    assert value["lines"] == capability_lines()


def test_detour_hints_derive_only_from_given_fixed_subset() -> None:
    assert any("shell" in item for item in detour_hints())
    assert any("浏览器" in item for item in detour_hints())
    assert detour_hints(("safe_read_file",)) == []


def test_coordinator_and_zinnia_share_one_fixed_source() -> None:
    lines = capability_lines()
    coordinator = coordinator_block()
    zinnia = zinnia_block()
    for line in lines:
        assert line in coordinator
        assert line in zinnia
    assert "固定工具清单" in coordinator
    assert "固定工具清单" in zinnia


def test_projection_failure_is_nonfatal(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(capabilities, "capability_lines", boom)
    assert coordinator_block() == ""
    assert zinnia_block() == ""
