# -*- coding: utf-8 -*-
"""v1.0.23.8-C 验证：workspace 快照对比兜底文件卡片（shell cp 无 verified fact 也能出卡）。"""
import hashlib
from pathlib import Path

from backend.knowe_harness.completion import ArtifactDisposition, SQLiteCompletionStore


def _make_store(tmp_path: Path):
    # 用真实 SQLiteCompletionStore 的静态方法，直接构造最小实例
    store = object.__new__(SQLiteCompletionStore)
    return store


class TestScanWorkspaceDeltas:
    def _setup_workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "workspace"
        ws.mkdir()
        # attempt 前就存在的文件（baseline 已有）
        (ws / "preexisting.md").write_text("old", encoding="utf-8")
        # attempt 期间 shell cp 出来的文件（baseline 没有，无 verified fact）
        (ws / "report.md").write_text("# Report\n" * 100, encoding="utf-8")
        # 系统目录里的文件必须被排除
        (ws / "node_modules").mkdir()
        (ws / "node_modules" / "x.md").write_text("noise", encoding="utf-8")
        return ws

    def test_scan_captures_new_files_only(self, tmp_path: Path) -> None:
        ws = self._setup_workspace(tmp_path)
        rows = SQLiteCompletionStore._scan_workspace_deltas(
            "task_1", ws, baseline=("preexisting.md",), existing=()
        )
        paths = [r["path"] for r in rows]
        assert "report.md" in paths
        assert "preexisting.md" not in paths
        assert not any("node_modules" in p for p in paths)

    def test_scan_skips_existing_verified(self, tmp_path: Path) -> None:
        ws = self._setup_workspace(tmp_path)
        rows = SQLiteCompletionStore._scan_workspace_deltas(
            "task_1", ws, baseline=("preexisting.md",),
            existing=({"path": "report.md"},),
        )
        assert rows == []

    def test_scan_bad_dir_returns_empty(self) -> None:
        rows = SQLiteCompletionStore._scan_workspace_deltas(
            "task_1", r"D:\Projects\knowe\不存在_xyz", baseline=(), existing=()
        )
        assert rows == []

    def test_scan_rows_are_claimable(self, tmp_path: Path) -> None:
        ws = self._setup_workspace(tmp_path)
        rows = SQLiteCompletionStore._scan_workspace_deltas(
            "task_1", ws, baseline=("preexisting.md",), existing=()
        )
        assert rows
        row = rows[0]
        assert row["disposition"] == ArtifactDisposition.CREATED_IN_ATTEMPT.value
        assert row["claimable"] is True
        assert row["metadata"]["source"] == "workspace_scan"
        assert row["current"]["is_file"] is True
        assert row["current"]["sha256"]  # 有哈希
