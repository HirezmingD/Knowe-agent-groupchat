# -*- coding: utf-8 -*-
"""v1.0.23.8-A/C 验证：外部根自动授权 + workspace 快照兜底文件卡片。"""
import json
from pathlib import Path

import pytest

from backend.engine import _auto_external_roots


class TestAutoExternalRoots:
    """A 方案：从 goal 文本自动提取外部根目录。"""

    def test_extracts_file_parent_from_goal(self) -> None:
        goal = "用 copy_external_file 将 D:/Projects/knowe/测试/测试5/qwen3.8-max_analysis_report.md 复制到项目根目录"
        roots = _auto_external_roots(goal)
        assert any(r.replace("\\", "/") == "D:/Projects/knowe/测试/测试5" for r in roots)

    def test_accepts_backslash_paths(self) -> None:
        goal = "复制 D:\\data\\input\\report.md 到根目录"
        roots = _auto_external_roots(goal)
        assert any(r.replace("\\", "/").endswith("data/input") for r in roots)

    def test_directory_path_kept_as_is(self) -> None:
        goal = "读取 D:/shared/docs 下所有文件"
        roots = _auto_external_roots(goal)
        assert any(r.replace("\\", "/") == "D:/shared/docs" for r in roots)

    def test_drive_root_not_authorized(self) -> None:
        goal = "访问 D:/ 根目录"
        assert _auto_external_roots(goal) == ()

    def test_empty_goal(self) -> None:
        assert _auto_external_roots("") == ()
        assert _auto_external_roots(None) == ()
