# knowe v0.20 — Batch 4：文件检索与精准修改
"""
file_ops.py — `safe_search_files` 和 `safe_patch` 的纯逻辑层。

这两个工具填的是 `safe_read_file` 和 `safe_write_file` 之间那个洞：
**改一行字，不该整篇重写。**

整篇重写有三样代价，一样比一样疼：
  ① 贵    —— 一个 2000 行的文件，改一个函数名要吐 2000 行 token；
  ② 慢    —— 那 2000 行得一个字一个字生成出来；
  ③ **危险** —— 模型「重写」的时候会顺手改掉它觉得不好看的地方，
      而用户根本没让它动那些地方。整篇重写的每一次都是一次全文重新赌博。

safe_patch 把赌注缩小到「这一段」：找到 old_string，换成 new_string，
别的**一个字节都不许动**。这也是这个模块所有设计的出发点。

—— 这里全是**同步纯函数**。调用方（tools_knowe）负责 resolve_in_sandbox 和
   asyncio.to_thread —— 沙箱是权限的事，线程是并发的事，都不是这儿的事。
"""

from __future__ import annotations

import ast
import difflib
import fnmatch
import json
import os
import re
import stat
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent_runtime import ToolError

# ═══════════════════════════════════════════════════════════════
# 检索
# ═══════════════════════════════════════════════════════════════

#: 默认跳过的目录 —— 不是为了「安全」，是为了**结果有用**。
#:
#: 在一个 Node 项目里 grep 一个函数名，node_modules 会给你 4000 条命中，
#: 全是别人的代码，而你要的那一条在第 3971 行。跳过它不是偷懒，
#: 是承认「用户的项目 ≠ 用户下载的依赖」。真要搜依赖 → include_ignored=true。
DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".bzr",
    "node_modules", "bower_components", "vendor",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", "site-packages", ".eggs",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit",
    ".cache", ".parcel-cache", ".turbo", ".gradle",
    ".idea", ".vscode", ".DS_Store",
    "coverage", "htmlcov",
})

#: Returned match text is bounded, but the window is centred on the actual match and
#: carries absolute column coordinates.  The source line itself is never truncated while
#: scanning.
_MAX_LINE_CHARS = 400
_BINARY_PROBE = 8192


def _is_filesystem_alias(path: Path) -> bool:
    """Hard links and reparse points are outside the broker's trust model."""

    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(
        stat.S_ISLNK(info.st_mode)
        or attributes & reparse_flag
        or (stat.S_ISREG(info.st_mode) and int(getattr(info, "st_nlink", 1)) > 1)
    )


@dataclass
class SearchOutcome:
    matches: list[dict[str, Any]] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    truncated: bool = False
    completeness: bool = True
    cancelled: bool = False
    skipped_binary: int = 0


def _looks_binary(chunk: bytes) -> bool:
    return b"\x00" in chunk


def _globs(raw: str | None) -> list[str]:
    """`*.py` / `*.py,*.md` 都认 —— 模型很自然会想一次搜两种文件。"""
    if not raw or not str(raw).strip():
        return []
    return [g.strip() for g in str(raw).split(",") if g.strip()]


def _name_matches(rel: str, name: str, globs: list[str]) -> bool:
    if not globs:
        return True
    for g in globs:
        target = rel if ("/" in g or os.sep in g) else name
        if fnmatch.fnmatch(target, g):
            return True
        if "/" not in g and fnmatch.fnmatch(rel, g):
            return True
    return False


def _context_line(line: str) -> str:
    line = line.rstrip("\r\n")
    if len(line) <= _MAX_LINE_CHARS:
        return line
    from .i18n_backend import msg  # 局部导入：避免模块级语言固化
    return line[:_MAX_LINE_CHARS] + msg("ctx.005", count=len(line))


def _match_row(rel: str, line_no: int, line: str, match: re.Match[str]) -> dict[str, Any]:
    """Return a bounded window centred on the match plus recoverable coordinates."""

    line = line.rstrip("\r\n")
    line_length = len(line)
    match_start = match.start()
    match_end = match.end()
    width = min(line_length, max(_MAX_LINE_CHARS, min(2_000, match_end - match_start)))
    centre = (match_start + match_end) // 2
    window_start = max(0, centre - width // 2)
    window_end = min(line_length, window_start + width)
    window_start = max(0, window_end - width)
    shown = line[window_start:window_end]
    if window_start:
        shown = "…" + shown
    if window_end < line_length:
        shown += "…"
    return {
        "file": rel,
        "line": line_no,
        "column": match_start + 1,
        "end_column": match_end + 1,
        "match_start_column": match_start + 1,
        "match_end_column": match_end + 1,
        "window_start_column": window_start + 1,
        "window_end_column": window_end,
        "line_length": line_length,
        "text": shown,
    }


def search_files(
    root: Path,
    base: Path,
    pattern: str,
    *,
    file_glob: str | None = None,
    offset: int = 0,
    limit: int = 50,
    context: int = 0,
    include_ignored: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    reserved_root_dirs: tuple[str, ...] = (),
    time_budget_s: float | None = None,
) -> SearchOutcome:
    """Stream-search project text without a fixed deadline or file-size skip.

    ``time_budget_s`` is retained only as a source-compatible ignored argument for older
    embedders; cancellation is cooperative through ``cancel_check``.  Pagination is based
    on the stable global match offset.  ``completeness`` is true only after the requested
    search space has been scanned through EOF.
    """

    del time_budget_s
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError(
            f"正则表达式不合法：{exc}。"
            "提示：搜普通字符串时记得转义 . ( ) [ ] * + ? 等元字符，"
            "或者用 re.escape 后的写法。"
        ) from None

    offset = max(0, int(offset))
    limit = max(1, min(200, int(limit)))
    context = max(0, min(10, int(context)))
    globs = _globs(file_glob)
    out = SearchOutcome()
    root = root.resolve()
    matches_seen = 0

    def cancelled() -> bool:
        if cancel_check is None:
            return False
        try:
            return bool(cancel_check())
        except Exception:
            return False

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        if cancelled():
            out.cancelled = True
            out.completeness = False
            out.truncated = True
            return out
        here = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if not (here == root and d in reserved_root_dirs)
            and (include_ignored or (d not in DEFAULT_SKIP_DIRS and not d.startswith(".")))
            and not _is_filesystem_alias(here / d)
        ]
        dirnames.sort()

        for name in sorted(filenames):
            if cancelled():
                out.cancelled = True
                out.completeness = False
                out.truncated = True
                return out
            fpath = here / name
            try:
                rel = fpath.relative_to(root).as_posix()
            except ValueError:
                continue
            if not _name_matches(rel, name, globs):
                continue
            if not include_ignored and name.startswith("."):
                continue

            try:
                if _is_filesystem_alias(fpath):
                    continue
                if not fpath.is_file():
                    continue
                byte_size = fpath.stat().st_size
                with fpath.open("rb") as probe_handle:
                    probe = probe_handle.read(_BINARY_PROBE)
                if _looks_binary(probe):
                    out.skipped_binary += 1
                    continue
            except OSError:
                continue

            out.files_scanned += 1
            out.bytes_scanned += byte_size
            before: deque[str] = deque(maxlen=context)
            pending: list[tuple[dict[str, Any], int]] = []
            extra_found = False
            try:
                with fpath.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                    for line_no, raw_line in enumerate(handle, 1):
                        if cancelled():
                            out.cancelled = True
                            out.completeness = False
                            out.truncated = True
                            return out
                        line = raw_line.rstrip("\r\n")

                        if pending:
                            next_pending: list[tuple[dict[str, Any], int]] = []
                            projected = _context_line(line)
                            for row, remaining in pending:
                                row.setdefault("after", []).append(projected)
                                remaining -= 1
                                if remaining > 0:
                                    next_pending.append((row, remaining))
                            pending = next_pending
                            if extra_found and not pending:
                                out.completeness = False
                                return out

                        if extra_found:
                            continue

                        match = rx.search(line)
                        if match is not None:
                            if matches_seen < offset:
                                matches_seen += 1
                            elif len(out.matches) < limit:
                                row = _match_row(rel, line_no, line, match)
                                if context:
                                    row["before"] = list(before)
                                    row["after"] = []
                                    pending.append((row, context))
                                out.matches.append(row)
                                matches_seen += 1
                            else:
                                # One extra match proves a continuation exists.  Read only
                                # enough following lines to finish context for rows already
                                # returned, then stop; the next call rescans and skips by offset.
                                out.truncated = True
                                out.completeness = False
                                extra_found = True
                                if not pending:
                                    return out

                        if context:
                            before.append(_context_line(line))
            except OSError:
                continue

            # Context never crosses a file boundary.  Rows near EOF legitimately have a
            # shorter ``after`` array.
            pending.clear()
            if extra_found:
                out.completeness = False
                return out

    return out


# ═══════════════════════════════════════════════════════════════
# 精准修改
# ═══════════════════════════════════════════════════════════════

_PATCH_MAX_BYTES = 10_000_000
_DIFF_MAX_LINES = 240


def _check_python(text: str) -> str | None:
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return f"第 {exc.lineno} 行：{exc.msg}"
    except (ValueError, RecursionError) as exc:
        return str(exc)
    return None


def _check_json(text: str) -> str | None:
    try:
        json.loads(text)
    except ValueError as exc:
        return str(exc)
    return None


def _check_yaml(text: str) -> str | None:
    try:
        import yaml  # 可选依赖：装了就查，没装就跳过——不为了「更严格」而多一个必选依赖
    except ImportError:
        return _SKIP
    try:
        list(yaml.safe_load_all(text))
    except Exception as exc:
        return str(exc).replace("\n", " ")[:300]
    return None


def _check_toml(text: str) -> str | None:
    try:
        import tomllib          # 3.11+ stdlib
    except ImportError:
        return _SKIP
    try:
        tomllib.loads(text)
    except Exception as exc:
        return str(exc)[:300]
    return None


#: 「这个格式我查不了」的哨兵 —— 和「查了，没问题」(None) 是两回事。
_SKIP = "\x00skip"

_CHECKERS: dict[str, tuple[str, Callable[[str], str | None]]] = {
    ".py":    ("python", _check_python),
    ".pyi":   ("python", _check_python),
    ".json":  ("json", _check_json),
    ".jsonc": ("json", _check_json),
    ".yaml":  ("yaml", _check_yaml),
    ".yml":   ("yaml", _check_yaml),
    ".toml":  ("toml", _check_toml),
}


def _occurrence_lines(text: str, needle: str) -> list[int]:
    lines: list[int] = []
    start = 0
    while True:
        pos = text.find(needle, start)
        if pos < 0:
            return lines
        lines.append(text.count("\n", 0, pos) + 1)
        start = pos + max(1, len(needle))


def _not_found_hint(text: str, old: str) -> str:
    """
    没找到的时候，**别只说「没找到」**。

    模型 patch 失败的原因九成是同一个：它凭记忆重写了 old_string，
    缩进少了两个空格，或者把 tab 打成了空格。这时候回一句「文件里找不到这段内容」
    等于让它再猜一轮（用户多等 10 秒、多付一次钱，而且它很可能猜同样的东西）。
    所以这里多花二十行，把「像在哪、差在哪」直接告诉它。
    """
    # [v0.32 B2] safe_read_file 现在带行号显示（「  12│ 」前缀）。新失败形态随之而来：
    #   模型把显示格式**连行号一起**复制进了 old_string。这一种要**点名**说破，
    #   否则下面的宽松匹配全部失灵，它只会得到最含糊的那句兜底。
    if re.search(r"(?m)^\s*\d+│", old):
        return (
            "old_string 里带着「 12│ 」这样的行号前缀——那是 safe_read_file 的**显示格式**，"
            "文件内容里并没有这些数字和竖线。把每行行首的『数字│』去掉再试；"
            "或者既然你已经知道行号了，直接改用 start_line/end_line 行号模式，连复制都省了。"
        )
    stripped = old.strip()
    if stripped and stripped in text:
        line = text.count("\n", 0, text.find(stripped)) + 1
        return (
            f"没找到 old_string，但把首尾空白去掉之后能在第 {line} 行附近找到。"
            "说明**缩进或行首行尾的空白对不上**——请先 safe_read_file 把那几行原样复制过来，"
            "连缩进一起。"
        )

    tokens = [re.escape(t) for t in stripped.split()]
    if tokens:
        try:
            loose = re.compile(r"\s+".join(tokens))
            m = loose.search(text)
        except re.error:
            m = None
        if m:
            line = text.count("\n", 0, m.start()) + 1
            return (
                f"没找到 old_string，但第 {line} 行附近有一段**只在空白/换行上不同**的内容。"
                "请用 safe_read_file 重新读取那一段并原样复制（注意 tab 和空格、以及换行位置）。"
            )

    first = stripped.splitlines()[0].strip() if stripped.splitlines() else ""
    if first and first in text:
        line = text.count("\n", 0, text.find(first)) + 1
        return (
            f"没找到完整的 old_string，只匹配上了它的第一行（在第 {line} 行）。"
            "后面几行和文件里的实际内容不一样——请先 safe_read_file 确认原文，别凭记忆写。"
        )

    return (
        "文件里找不到 old_string。请先用 safe_read_file 读一遍确认内容，"
        "或者用 safe_search_files 定位；不要凭记忆构造 old_string。"
    )


@dataclass
class PatchOutcome:
    diff: str
    replacements: int
    syntax_checked: str | None
    syntax_note: str | None
    bytes_before: int
    bytes_after: int
    diff_truncated: bool
    syntax_warning: str | None = None
    self_check: dict[str, Any] = field(default_factory=dict)
    #: [v0.32 B2] 行号模式下：实际被替换的 (start, end)（1 起，含端点）。old_string 模式为 None。
    line_range: tuple[int, int] | None = None


def patch_file(path: Path, rel_label: str, old: str, new: str, *,
               replace_all: bool = False,
               start_line: int | None = None,
               end_line: int | None = None) -> PatchOutcome:
    """
    在 `path` 里改**一段**内容。失败 → 抛 ToolError，**文件一个字节没动**。

    [v0.32 B2] 两种定位方式，二选一：
      · old_string 模式（原有）：找到 old，换成 new；
      · 行号模式（新）：把第 start_line~end_line 行（1 起，含端点）整体换成 new。
        它从根上绕开「逐字复现原文」——两起线上补丁死循环的直接死因就是
        凭记忆拼 old_string 差了两个空格。知道行号（safe_read_file 现在带行号）
        就不必再赌那口气。

    做对的几件事，每一件都对应一个真实事故：
      · 二进制/非 UTF-8 → 直接拒（不然一次 replace 就把 .png 毁了）
      · CRLF 文件 → 按字节读写，不让 Python 的通用换行悄悄把全文 LF↔CRLF 翻一遍
        （行号模式同样适配：替换段按全文风格写 CRLF，其余字节原样）
      · 唯一性 → 默认必须唯一（"i" 这种字符串在文件里有 300 处）
      · 语法检查 → 写后自检并回 syntax_warning；不替 Worker 否决已请求的写入
      · 原子写 → 先 .tmp 再 replace，半截文件不会被别人读到
    """
    if path.is_symlink():
        raise ToolError("safe_patch 不修改符号链接；请让用户明确处理该链接指向的文件")
    if not path.is_file():
        raise ToolError(f"文件不存在：{rel_label}")
    if not isinstance(new, str):
        raise ToolError("new_string 必须是字符串（要删掉这段内容就传空字符串）")

    range_mode = start_line is not None or end_line is not None
    if range_mode and isinstance(old, str) and old != "":
        raise ToolError(
            "old_string 和 start_line/end_line 是两种**互斥**的定位方式，只能选一种："
            "知道行号就只传行号（old_string 留空），只知道内容就只传 old_string。"
        )
    if not range_mode:
        if not isinstance(old, str) or old == "":
            raise ToolError(
                "两种定位方式选一种：传 old_string（文件里那段原文），"
                "或者传 start_line/end_line（要替换的行号范围）。这次两样都没给。"
            )
        if old == new:
            raise ToolError("old_string 和 new_string 完全一样，这次修改没有任何效果")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"读不了：{exc}") from None
    if len(raw) > _PATCH_MAX_BYTES:
        raise ToolError(f"文件超过 {_PATCH_MAX_BYTES // 1_000_000}MB，safe_patch 不处理这么大的文件")
    if _looks_binary(raw[:_BINARY_PROBE]):
        raise ToolError("这是二进制文件，safe_patch 只改文本文件")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(
            "文件不是 UTF-8 文本，safe_patch 不碰它——按 UTF-8 重写会破坏原有编码"
        ) from None

    crlf_adapted = False
    line_range: tuple[int, int] | None = None

    if range_mode:
        new_text, line_range, crlf_adapted = _apply_line_range(
            text, new, start_line, end_line)
        count = 1
    else:
        # ★ CRLF：Windows 上 checkout 出来的仓库全是 \r\n，而模型写 old_string 时
        #   只会写 \n。直接判「没找到」是冤枉它。这里补一次 CRLF 匹配，
        #   并且把 new_string 也换成 CRLF —— 只改这一段，全文换行风格保持原样。
        search_old, write_new = old, new
        if old not in text and "\r\n" in text and "\r\n" not in old:
            crlf_old = old.replace("\n", "\r\n")
            if crlf_old in text:
                search_old = crlf_old
                write_new = new.replace("\n", "\r\n")
                crlf_adapted = True

        count = text.count(search_old)
        if count == 0:
            raise ToolError(_not_found_hint(text, old))
        if count > 1 and not replace_all:
            lines = _occurrence_lines(text, search_old)
            shown = "、".join(str(n) for n in lines[:8])
            more = f" 等 {len(lines)} 处" if len(lines) > 8 else ""
            raise ToolError(
                f"old_string 在文件里出现了 {count} 次（第 {shown} 行{more}），无法确定改哪一处。"
                "要么把 old_string 写长一点、带上前后几行让它唯一，"
                "要么确认这 %d 处都要改，再传 replace_all=true。" % count
            )

        new_text = text.replace(search_old, write_new) if replace_all \
            else text.replace(search_old, write_new, 1)

    # ── 语法检查 ──
    checked_name: str | None = None
    note: str | None = None
    syntax_warning: str | None = None
    self_check: dict[str, Any] = {"performed": False}
    entry = _CHECKERS.get(path.suffix.lower())
    if entry is not None:
        checked_name, checker = entry
        before_err = checker(text)
        if before_err == _SKIP:
            checked_name = None
        else:
            after_err = checker(new_text)
            if after_err == _SKIP:
                checked_name = None
            else:
                self_check = {
                    "performed": True,
                    "checker": checked_name,
                    "before_error": before_err,
                    "after_error": after_err,
                    "passed": after_err is None,
                }
                if after_err:
                    syntax_warning = (
                        f"写后 {checked_name} 自检发现语法问题：{after_err}。"
                        "修改已按请求原子写入；请读取当前文件并继续修复。"
                    )
                    note = syntax_warning
                elif before_err:
                    note = f"顺带修好了原来的 {checked_name} 语法错误。"

    diff_lines = list(difflib.unified_diff(
        text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{rel_label}", tofile=f"b/{rel_label}", n=3,
    ))
    diff_truncated = len(diff_lines) > _DIFF_MAX_LINES
    if diff_truncated:
        diff_lines = diff_lines[:_DIFF_MAX_LINES] + [f"…（diff 太长，共 {len(diff_lines)} 行，已截断）\n"]
    diff = "".join(diff_lines)

    payload = new_text.encode("utf-8")
    _atomic_write_bytes(path, payload)

    if crlf_adapted and note is None:
        note = "文件用的是 CRLF 换行，替换段已自动按 CRLF 写入，全文换行风格保持不变。"

    return PatchOutcome(
        diff=diff,
        replacements=count if replace_all else 1,
        syntax_checked=checked_name,
        syntax_note=note,
        bytes_before=len(raw),
        bytes_after=len(payload),
        diff_truncated=diff_truncated,
        syntax_warning=syntax_warning,
        self_check=self_check,
        line_range=line_range,
    )


def _apply_line_range(text: str, new: str,
                      start_line: int | None,
                      end_line: int | None) -> tuple[str, tuple[int, int], bool]:
    """
    [v0.32 B2] 行号模式：把第 start~end 行（1 起，含端点）换成 `new`。
    返回 (新全文, (start, end), 是否做了 CRLF 适配)。

    边界上的每一个选择都写在这儿，免得下一版猜：
      · 只给 start 不给 end → end = start（「把第 12 行换掉」是最常见的用法）；
      · new 为空串 → **整行删除**（连那几行的换行符一起拿走，不留空行）；
      · new 不以换行结尾、而被换掉的那段后面还有内容 → 按全文风格补一个换行——
        模型十次有九次忘了结尾换行，忘一次就把下一行黏上来，等于替它挖坑；
      · 被换的是**最后几行且原本没有结尾换行** → 也不给 new 强加换行，
        「文件末尾有没有 \\n」这个字节级事实保持原样。
    """
    if start_line is None:
        raise ToolError("行号模式要给 start_line（要替换的起始行，从 1 数）")
    try:
        start = int(start_line)
        end = int(end_line) if end_line is not None else start
    except (TypeError, ValueError):
        raise ToolError("start_line / end_line 必须是整数行号（从 1 数）") from None

    lines = text.splitlines(keepends=True)
    total = len(lines)
    if total == 0:
        raise ToolError("这个文件是空的，没有可替换的行——直接用 safe_write_file 写入内容")
    if start < 1 or end < 1:
        raise ToolError(f"行号从 1 数：收到 start_line={start}, end_line={end}")
    if start > end:
        raise ToolError(f"start_line({start}) 不能大于 end_line({end})")
    if end > total:
        raise ToolError(
            f"文件一共只有 {total} 行，替换不了第 {start}~{end} 行。"
            "先 safe_read_file 看一眼当前内容——文件可能已经变了。"
        )

    eol = "\r\n" if "\r\n" in text else "\n"
    write_new = new
    crlf_adapted = False
    if eol == "\r\n" and "\n" in new and "\r\n" not in new:
        write_new = new.replace("\n", "\r\n")
        crlf_adapted = True

    removed = "".join(lines[start - 1:end])
    if write_new != "":
        removed_had_eol = removed.endswith(("\n", "\r\n", "\r"))
        if removed_had_eol and not write_new.endswith(("\n", "\r")):
            write_new += eol

    if write_new == removed:
        raise ToolError(
            f"新内容和第 {start}~{end} 行的现有内容完全一样，这次修改没有任何效果。"
            "先 safe_read_file 确认这几行现在长什么样。"
        )

    new_text = "".join(lines[:start - 1]) + write_new + "".join(lines[end:])
    return new_text, (start, end), crlf_adapted


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """先写 .tmp 再 replace：写到一半断电，用户的原文件还是完整的。"""
    tmp = path.with_name(path.name + ".knowe-tmp")
    try:
        tmp.write_bytes(payload)
        try:            # 尽量保住原文件的权限位（比如可执行脚本）
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ToolError(f"写不了：{exc}") from None


__all__ = [
    "DEFAULT_SKIP_DIRS",
    "PatchOutcome",
    "SearchOutcome",
    "patch_file",
    "search_files",
]
