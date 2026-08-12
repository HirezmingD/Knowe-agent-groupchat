# knowe v0.20 — Batch 4 测试：检索 + 精准修改
"""
这些测试**不需要网络、不需要 Playwright、不需要 API key**，也不需要 pytest-asyncio
（异步的地方一律 asyncio.run，见 test_batch4_terminal.py）——
CI 里跑不起来的测试等于没写。

覆盖的是 file_ops 的纯逻辑：沙箱由 tools_knowe 负责，这里只管「找得准、改得对」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent_runtime import ToolError            # noqa: E402
from backend.file_ops import patch_file, search_files  # noqa: E402


# ═══════════════════════════════ 检索 ═══════════════════════════════

@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import os\n\n\ndef login(user):\n    return user\n", "utf-8")
    (tmp_path / "src" / "util.py").write_text("def login_helper():\n    pass\n", "utf-8")
    (tmp_path / "README.md").write_text("# 项目\n\nlogin 说明\n", "utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.py").write_text("def login(): pass\n", "utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("login\n", "utf-8")
    (tmp_path / "blob.bin").write_bytes(b"login\x00\x01\x02binary")
    return tmp_path


def test_finds_matches_with_line_numbers(tree: Path) -> None:
    out = search_files(tree, tree, r"def login")
    files = {m["file"] for m in out.matches}
    assert files == {"src/app.py", "src/util.py"}
    hit = next(m for m in out.matches if m["file"] == "src/app.py")
    assert hit["line"] == 4
    assert "def login(user)" in hit["text"]


def test_skips_noise_dirs_by_default(tree: Path) -> None:
    """node_modules 里 4000 条命中会把真正那一条淹掉——默认必须跳过。"""
    out = search_files(tree, tree, "login")
    assert not any("node_modules" in m["file"] for m in out.matches)
    assert not any(".git" in m["file"] for m in out.matches)


def test_include_ignored_opens_the_door(tree: Path) -> None:
    """跳过是默认，不是封死：确实要搜依赖时得搜得到。"""
    out = search_files(tree, tree, "login", include_ignored=True)
    assert any("node_modules" in m["file"] for m in out.matches)


def test_skips_binary_files(tree: Path) -> None:
    out = search_files(tree, tree, "login")
    assert not any(m["file"] == "blob.bin" for m in out.matches)
    assert out.skipped_binary >= 1


def test_file_glob_single_and_multi(tree: Path) -> None:
    only_md = search_files(tree, tree, "login", file_glob="*.md").matches
    assert {m["file"] for m in only_md} == {"README.md"}
    # 逗号分隔：模型很自然会想「*.ts,*.tsx 一起搜」，让它如愿比让它搜两遍好
    both = search_files(tree, tree, "login", file_glob="*.md,*.py").matches
    assert {m["file"] for m in both} == {"README.md", "src/app.py", "src/util.py"}


def test_context_lines(tree: Path) -> None:
    out = search_files(tree, tree, r"return user", context=2)
    hit = out.matches[0]
    assert hit["before"] == ["", "def login(user):"]
    assert hit["after"] == []


def test_limit_marks_truncated(tree: Path) -> None:
    out = search_files(tree, tree, "login", limit=1)
    assert len(out.matches) == 1 and out.truncated is True


def test_bad_regex_is_a_message_not_a_crash(tree: Path) -> None:
    with pytest.raises(ToolError) as e:
        search_files(tree, tree, "def login(")
    assert "正则" in str(e.value)


def test_reserved_dirs_are_invisible(tmp_path: Path) -> None:
    """v0.16 迁移边界：残留的 handoffs/ 不能通过通用工具被看到。"""
    (tmp_path / "handoffs").mkdir()
    (tmp_path / "handoffs" / "report-01.md").write_text("secret", "utf-8")
    (tmp_path / "ok.md").write_text("secret", "utf-8")
    out = search_files(tmp_path, tmp_path, "secret",
                       reserved_root_dirs=("handoffs", ".project", ".agents"))
    assert {m["file"] for m in out.matches} == {"ok.md"}


def test_symlink_out_of_root_is_not_readable(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("PASSWORD=hunter2", "utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    try:
        (root / "link.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("这个平台不支持符号链接")
    out = search_files(root, root, "PASSWORD")
    assert out.matches == []


# ═══════════════════════════════ 精准修改 ═══════════════════════════

def test_patch_replaces_once_and_returns_diff(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 2\n", "utf-8")
    out = patch_file(f, "a.py", "y = 2", "y = 3")
    assert f.read_text("utf-8") == "x = 1\ny = 3\n"
    assert out.replacements == 1
    assert "-y = 2" in out.diff and "+y = 3" in out.diff
    assert out.syntax_checked == "python"


def test_patch_refuses_ambiguous_old_string(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("a = 1\nb = 1\n", "utf-8")
    with pytest.raises(ToolError) as e:
        patch_file(f, "a.py", "= 1", "= 2")
    assert "2 次" in str(e.value)
    assert f.read_text("utf-8") == "a = 1\nb = 1\n"     # 一个字节都没动


def test_patch_replace_all(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("cat cat cat", "utf-8")
    out = patch_file(f, "a.txt", "cat", "dog", replace_all=True)
    assert f.read_text("utf-8") == "dog dog dog"
    assert out.replacements == 3


def test_patch_writes_syntax_breaking_edit_with_warning(tmp_path: Path) -> None:
    """Syntax checking is a post-write warning, not a Runtime veto."""
    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n", "utf-8")
    out = patch_file(f, "a.py", "return 1", "return (1")
    assert f.read_text("utf-8") == "def f():\n    return (1\n"
    assert out.syntax_warning and "修改已按请求原子写入" in out.syntax_warning
    assert out.self_check["performed"] is True
    assert out.self_check["passed"] is False


def test_patch_allows_fixing_an_already_broken_file(tmp_path: Path) -> None:
    """
    ★ 反向的坑：文件本来就是坏的（Worker 正是来修它的）。
      天真的实现会因为「改完还是坏的」而拒绝，于是这个文件永远修不好。
    """
    f = tmp_path / "a.py"
    f.write_text("def f(:\n    return 1\nx = (\n", "utf-8")
    out = patch_file(f, "a.py", "def f(:", "def f():")
    assert "def f():" in f.read_text("utf-8")
    assert out.syntax_warning is not None
    assert out.self_check["before_error"] is not None
    assert out.self_check["after_error"] is not None


def test_patch_reports_fixing_syntax(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("def f(:\n    return 1\n", "utf-8")
    out = patch_file(f, "a.py", "def f(:", "def f():")
    assert out.syntax_note is not None and "顺带修好" in out.syntax_note


def test_patch_reports_json_warning_after_write(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text('{"a": 1}', "utf-8")
    out = patch_file(f, "p.json", '{"a": 1}', '{"a": 1,}')
    assert f.read_text("utf-8") == '{"a": 1,}'
    assert out.syntax_warning is not None
    assert out.self_check["checker"] == "json"
    assert out.self_check["passed"] is False


def test_patch_preserves_crlf(tmp_path: Path) -> None:
    """
    ★ Windows 上的经典事故：read_text/write_text 会把全文换行翻一遍，
      于是「改一行」变成了 800 行的 diff，代码评审时谁也看不出改了什么。
    """
    f = tmp_path / "a.txt"
    f.write_bytes(b"line1\r\nline2\r\nline3\r\n")
    patch_file(f, "a.txt", "line2", "LINE2")
    assert f.read_bytes() == b"line1\r\nLINE2\r\nline3\r\n"


def test_patch_matches_lf_old_string_against_crlf_file(tmp_path: Path) -> None:
    """模型写的 old_string 永远是 \\n；文件是 \\r\\n。这不该算「找不到」。"""
    f = tmp_path / "a.txt"
    f.write_bytes(b"a\r\nb\r\nc\r\n")
    out = patch_file(f, "a.txt", "a\nb", "a\nB")
    assert f.read_bytes() == b"a\r\nB\r\nc\r\n"
    assert out.syntax_note is not None and "CRLF" in out.syntax_note


def test_patch_not_found_hint_points_at_whitespace(tmp_path: Path) -> None:
    """
    找不到的时候要给线索，不能只说「没找到」——那等于让它再猜一轮
    （用户多等十秒、多付一次钱，而且它很可能猜同样的东西）。
    这里造的是最常见的一种：文件用 tab，模型写的是空格。
    """
    f = tmp_path / "a.py"
    f.write_text("def f():\n\treturn 1\n", "utf-8")
    with pytest.raises(ToolError) as e:
        patch_file(f, "a.py", "    return 1", "    return 2")
    msg = str(e.value)
    assert "空白" in msg and "2 行" in msg          # 说清楚「像在第几行、差在哪」


def test_patch_not_found_hint_when_only_first_line_matches(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n", "utf-8")
    with pytest.raises(ToolError) as e:
        patch_file(f, "a.py", "def f():\n    return 999\n    # 我编的\n", "x")
    assert "第一行" in str(e.value)


def test_patch_rejects_binary(tmp_path: Path) -> None:
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00stuff")
    with pytest.raises(ToolError) as e:
        patch_file(f, "a.png", "stuff", "other")
    assert "二进制" in str(e.value)


def test_patch_rejects_noop_and_empty(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", "utf-8")
    with pytest.raises(ToolError):
        patch_file(f, "a.txt", "hello", "hello")
    with pytest.raises(ToolError):
        patch_file(f, "a.txt", "", "x")


def test_patch_deletes_when_new_is_empty(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("keep\nDROP\nkeep2\n", "utf-8")
    patch_file(f, "a.txt", "DROP\n", "")
    assert f.read_text("utf-8") == "keep\nkeep2\n"


def test_patch_leaves_no_temp_file(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", "utf-8")
    patch_file(f, "a.txt", "x", "y")
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]
