from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend import tools_knowe
from backend.runtime import WORKER_TOOL_NAMES


class FakeEngine:
    def __init__(self, workspace: Path, internal: Path) -> None:
        self.project_id = "project-tools"
        self.workspace_root = workspace
        self.internal_workspace = internal
        self.produced: list[tuple[str, str]] = []

    def note_file_produced(self, agent_id: str, path: str) -> None:
        self.produced.append((agent_id, path))


def _engine(tmp_path: Path) -> FakeEngine:
    workspace = tmp_path / "project"
    internal = tmp_path / "internal" / "project-tools"
    workspace.mkdir(parents=True)
    internal.mkdir(parents=True)
    return FakeEngine(workspace, internal)


def _call(registry, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    raw = asyncio.run(registry.execute(name, arguments))
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def test_registry_is_exact_fixed_19_in_canonical_order(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    registry = tools_knowe.build_worker_registry(engine, "worker-1")

    assert tuple(registry.names()) == WORKER_TOOL_NAMES
    assert len(registry.get_schemas()) == 19
    assert tuple(
        item["function"]["name"] for item in registry.get_schemas()
    ) == WORKER_TOOL_NAMES
    serialized = json.dumps(registry.get_schemas(), sort_keys=True)
    assert "read_result_ref" not in serialized
    assert "raw_ref" not in serialized
    assert "blob" not in serialized


def test_write_patch_read_and_same_tool_pagination_are_verified(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    registry = tools_knowe.build_worker_registry(engine, "worker-1")

    write = _call(
        registry,
        "safe_write_file",
        {"path": "notes/report.txt", "content": "alpha\nbeta\ngamma\ndelta\n"},
    )
    assert write["status"] == "ok"
    assert write["artifact"]["verified"] is True
    assert write["artifact"]["size"] == len(b"alpha\nbeta\ngamma\ndelta\n")
    assert write["artifact"]["sha256"] == hashlib.sha256(
        b"alpha\nbeta\ngamma\ndelta\n"
    ).hexdigest()

    patch = _call(
        registry,
        "safe_patch",
        {"path": "notes/report.txt", "old_string": "beta", "new_string": "BETA"},
    )
    assert patch["status"] == "ok"
    assert patch["artifact"]["verified"] is True
    assert (engine.workspace_root / "notes/report.txt").read_text("utf-8").splitlines()[1] == "BETA"

    first = _call(
        registry,
        "safe_read_file",
        {"path": "notes/report.txt", "start_line": 1, "end_line": 2},
    )
    second = _call(
        registry,
        "safe_read_file",
        {"path": "notes/report.txt", "start_line": first["next_start_line"], "end_line": 4},
    )
    assert first["truncated"] is True
    assert "safe_read_file" in first["continuation"]
    assert "alpha" in first["content"] and "BETA" in first["content"]
    assert "gamma" in second["content"] and "delta" in second["content"]
    assert engine.produced == [
        ("worker-1", "notes/report.txt"),
        ("worker-1", "notes/report.txt"),
    ]


def test_list_and_search_continue_with_offset_limit(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for index in range(4):
        (engine.workspace_root / f"file-{index}.txt").write_text(
            f"needle {index}\n", encoding="utf-8"
        )
    registry = tools_knowe.build_worker_registry(engine, "worker-1")

    page1 = _call(registry, "safe_list_dir", {"path": ".", "offset": 0, "limit": 2})
    page2 = _call(
        registry,
        "safe_list_dir",
        {"path": ".", "offset": page1["next_offset"], "limit": 2},
    )
    assert [row["name"] for row in page1["entries"] + page2["entries"]] == [
        "file-0.txt",
        "file-1.txt",
        "file-2.txt",
        "file-3.txt",
    ]
    assert "safe_list_dir" in page1["continuation"]

    search1 = _call(
        registry,
        "safe_search_files",
        {"pattern": "needle", "path": ".", "offset": 0, "limit": 2},
    )
    search2 = _call(
        registry,
        "safe_search_files",
        {
            "pattern": "needle",
            "path": ".",
            "offset": search1["next_offset"],
            "limit": 2,
        },
    )
    assert search1["count"] == 2 and search1["truncated"] is True
    assert search2["count"] == 2
    assert "safe_search_files" in search1["continuation"]
    assert len(search1["matches"] + search2["matches"]) == 4


def test_project_traversal_and_symlinks_are_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (engine.workspace_root / "link.txt").symlink_to(outside)
    registry = tools_knowe.build_worker_registry(engine, "worker-1")

    traversal = _call(registry, "safe_read_file", {"path": "../outside.txt"})
    symlink = _call(registry, "safe_read_file", {"path": "link.txt"})

    assert traversal["status"] == "error"
    assert symlink["status"] == "error"
    assert "symbolic" in symlink["message"].lower()


def test_delete_available_without_intent_and_verifies_absence(tmp_path: Path) -> None:
    """[v1.0.23.5] 删除意图安全门已移除：worker 无需任何意图标记即可删除项目内普通文件；
    路径硬校验（拒绝符号链接/目录、删除后验证不存在）保留。"""
    engine = _engine(tmp_path)
    target = engine.workspace_root / "obsolete.txt"
    target.write_text("remove me", encoding="utf-8")

    registry = tools_knowe.build_worker_registry(engine, "worker-1")
    deleted = _call(registry, "safe_delete_file", {"path": "obsolete.txt"})
    assert deleted["status"] == "ok"
    assert deleted["deletion"]["verified_absent"] is True
    assert not target.exists()

    # 硬校验仍在：目录不可删、符号链接不可删
    folder = engine.workspace_root / "folder"
    folder.mkdir()
    refused_dir = _call(registry, "safe_delete_file", {"path": "folder"})
    assert refused_dir["status"] == "error"
    assert folder.exists()


def test_external_roots_are_authorized_redacted_and_copy_verified(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    external = tmp_path / "authorized"
    external.mkdir()
    source = external / "source.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    other = tmp_path / "unauthorized.txt"
    other.write_text("no", encoding="utf-8")
    link = external / "link.txt"
    link.symlink_to(source)

    registry = tools_knowe.build_worker_registry(
        engine,
        "worker-1",
        authorized_external_roots=(external,),
    )
    first = _call(
        registry,
        "read_external_file",
        {"path": str(source), "offset": 0, "limit": 2},
    )
    second = _call(
        registry,
        "read_external_file",
        {"path": str(source), "offset": first["next_offset"], "limit": 2},
    )
    assert first["path"] == "external_root_1/source.txt"
    assert str(external) not in json.dumps(first)
    assert first["content"] == "one\ntwo"
    assert second["content"] == "three"

    listing = _call(
        registry,
        "list_external_dir",
        {"path": str(external), "offset": 0, "limit": 1},
    )
    assert listing["path"] == "external_root_1"
    assert listing["entries"][0]["name"] == "source.txt"

    copied = _call(
        registry,
        "copy_external_file",
        {"source": str(source), "destination": "imports/source.txt"},
    )
    destination = engine.workspace_root / "imports/source.txt"
    assert copied["status"] == "ok"
    assert destination.read_bytes() == source.read_bytes()
    assert copied["artifact"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert source.read_text("utf-8") == "one\ntwo\nthree\n"

    unauthorized = _call(registry, "read_external_file", {"path": str(other)})
    linked = _call(registry, "read_external_file", {"path": str(link)})
    assert unauthorized["status"] == "error"
    assert linked["status"] == "error"
    assert "symbolic" in linked["message"].lower()


def test_disabled_services_stay_registered_and_return_stable_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    disabled = dataclasses.replace(
        tools_knowe.CONFIG,
        terminal_enabled=False,
        web_enabled=False,
        browser_enabled=False,
    )
    monkeypatch.setattr(tools_knowe, "CONFIG", disabled)
    registry = tools_knowe.build_worker_registry(engine, "worker-1")

    assert tuple(registry.names()) == WORKER_TOOL_NAMES
    assert _call(registry, "safe_bash", {"command": "echo hi"})["code"] == "terminal_unavailable"
    assert _call(registry, "web_search", {"query": "x"})["code"] == "web_unavailable"
    assert _call(registry, "web_extract", {"urls": "https://example.com"})["code"] == "web_unavailable"
    assert _call(registry, "browser_navigate", {"url": "https://example.com"})["code"] == "browser_unavailable"
    assert _call(registry, "browser_snapshot", {})["code"] == "browser_unavailable"
    assert _call(registry, "browser_screenshot", {"path": "screen.png"})["code"] == "browser_unavailable"


def test_browser_screenshot_is_statted_and_hashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)
    png = b"\x89PNG\r\n\x1a\nverified-image"

    class FakePage:
        url = "https://example.test/page"

        async def screenshot(self, *, path: str, full_page: bool, timeout: int) -> None:
            assert full_page is True
            assert timeout > 0
            Path(path).write_bytes(png)

    class FakePool:
        async def session(self, agent_id: str):
            assert agent_id == "worker-1"
            return SimpleNamespace(page=FakePage(), dialog_policy="", dialog_text=None)

    monkeypatch.setattr(tools_knowe, "_browser_pool", lambda _engine: FakePool())
    monkeypatch.setattr(
        tools_knowe,
        "CONFIG",
        dataclasses.replace(tools_knowe.CONFIG, browser_enabled=True),
    )
    registry = tools_knowe.build_worker_registry(engine, "worker-1")

    result = _call(
        registry,
        "browser_screenshot",
        {"path": "screens/capture", "full_page": True},
    )

    target = engine.workspace_root / "screens/capture.png"
    assert target.read_bytes() == png
    assert result["artifact"]["verified"] is True
    assert result["artifact"]["size"] == len(png)
    assert result["artifact"]["sha256"] == hashlib.sha256(png).hexdigest()
