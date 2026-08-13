"""Windows cmd mkdir 兼容回归测试：normalize_command 消除「-p」目录事故（2026-08-09）。"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.terminal_tools import run_command  # noqa: E402
from backend.agent_runtime import normalize_command  # noqa: E402


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd 专属事故")
def test_mkdir_p_no_longer_creates_dash_p(tmp_path) -> None:
    r = asyncio.run(run_command("mkdir -p testdir/sub", cwd=tmp_path, timeout_s=45, max_output=10_000))
    files = sorted(os.listdir(tmp_path))
    assert r.exit_code == 0
    assert "-p" not in files, f"-p 目录仍被建出: {files}"
    assert "testdir" in files and (tmp_path / "testdir" / "sub").is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd 专属事故")
def test_mkdir_p_multi_paths(tmp_path) -> None:
    r = asyncio.run(run_command("mkdir -p one two", cwd=tmp_path, timeout_s=45, max_output=10_000))
    files = sorted(os.listdir(tmp_path))
    assert r.exit_code == 0
    assert "-p" not in files and "one" in files and "two" in files


def test_normalize_command_units() -> None:
    cases = [
        ("mkdir -p dist", "mkdir dist"),
        ("mkdir -p a/b/c", "mkdir a\\b\\c"),
        ("mkdir --parents out", "mkdir out"),
        ("mkdir -p ./dist", "mkdir dist"),
        ("mkdir -p ./a/b/c", "mkdir a\\b\\c"),
        ("cd src && mkdir -p build", "cd src && mkdir build"),
        ("mkdir -p a; ls", "mkdir a; ls"),
        ("mkdir -p a/b; cd a/b", "mkdir a\\b; cd a/b"),
        ('git commit -m "mkdir -p test"', 'git commit -m "mkdir -p test"'),
        ("mkdir -path", "mkdir -path"),
        ("mkdir dist", "mkdir dist"),
        ("echo mkdir -p x", "echo mkdir -p x"),
        ("npm run build", "npm run build"),
    ]
    for inp, want in cases:
        assert normalize_command(inp) == want, f"{inp!r} → {normalize_command(inp)!r}, 期望 {want!r}"
