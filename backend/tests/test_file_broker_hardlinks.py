from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.file_ops import search_files
from backend.tools_knowe import _resolve_external_read, resolve_in_sandbox


def _hardlink(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable on this test filesystem: {exc}")


def test_project_file_broker_rejects_hardlink_to_outside_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do-not-expose", encoding="utf-8")
    _hardlink(outside, workspace / "innocent.txt")

    with pytest.raises(ValueError, match="hard link"):
        resolve_in_sandbox(workspace, "innocent.txt", operation="read")
    with pytest.raises(ValueError, match="hard link"):
        resolve_in_sandbox(workspace, "innocent.txt", operation="write")


def test_search_skips_hardlinked_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("sentinel-secret", encoding="utf-8")
    _hardlink(outside, workspace / "innocent.txt")

    outcome = search_files(workspace, workspace, "sentinel-secret")
    assert outcome.matches == []
    assert outcome.files_scanned == 0


def test_external_reader_rejects_hardlink_alias(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    approved = tmp_path / "approved"
    approved.mkdir()
    alias = approved / "alias.txt"
    _hardlink(outside, alias)

    with pytest.raises(ValueError, match="hard link"):
        _resolve_external_read(str(alias))
