# knowe v0.12 — Harness D · 平台清单与变更日志
"""
platform.py —— Knowe 软件自身的「平台上下文」：安装路径、版本、以及**从安装那一刻起**
的文件变更日志。这是问题五 5e 的答案。

用户要的是：知知（平台级接待）应该知道 Knowe 软件本身的情况——装在哪、什么版本、
有哪些文件；而且**从安装成功那一刻起**，安装目录下任何变化（版本更新、增删文件）
都要记 log，作为知知的基础上下文一起注入。除非卸载重装 → 一切清空从头开始。

怎么实现（不依赖任何外部服务、纯文件系统）：
  · 第一次跑 → 给安装目录拍一张「文件清单快照」（路径 → 大小+mtime），记下安装时间和版本，
    写 changelog 第一条「首次安装」。
  · 以后每次跑 → 拿当前清单和上次快照对一下：新增/删除/改动了哪些文件、版本变没变，
    把差异**追加**进 changelog，再更新快照。
  · 卸载重装 = data 目录被清掉 → 快照没了 → 下次跑又从「首次安装」开始。正是要的语义。

一条铁律：**这套东西是尽力而为的，绝不能拖垮启动。** 扫描/写盘出任何岔子，
记一条日志、跳过，主流程照跑。

落点（问题六 6a：收进 data/harness/ 子目录，不裸在 data/ 下）：
  data/harness/platform.json          清单（安装信息 + 版本 + 关键路径）
  data/harness/changelog.md           人读的变更日志（从安装起，只追加）
  data/harness/.platform_snapshot.json 文件清单快照（内部用，给下次做 diff）
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("knowe.platform")

from .i18n_backend import msg  # noqa: E402  [v1.0.21.3] 平台上下文按语言渲染

# 扫描安装目录时跳过的噪音目录（这些不是「Knowe 的文件」，是构建产物/依赖/运行时数据）
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".pytest_cache", ".mypy_cache", "data", ".idea",
    ".vscode", "out", "coverage", ".cache",
})
# 只跟踪这些后缀的文件（源码/配置/文档——版本更新时真正会变的东西）
_TRACK_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".txt", ".md",
    ".toml", ".cfg", ".ini", ".yaml", ".yml", ".html", ".css",
})
_MAX_FILES = 4000          # 清单最多跟这么多文件，够了；再多也没意义，还慢
_MAX_CHANGELOG_LINES = 40  # 注进知知上下文时，changelog 只带最近这么多行


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlatformManifest:
    """Knowe 平台清单 + 变更日志。所有方法尽力而为，绝不抛。"""

    def __init__(self, data_dir: Path | str, install_root: Path | str,
                 version: str) -> None:
        self.data_dir = Path(data_dir)
        self.harness_dir = self.data_dir / "harness"
        self.install_root = Path(install_root).resolve()
        self.version = version
        self.manifest_path = self.harness_dir / "platform.json"
        self.changelog_path = self.harness_dir / "changelog.md"
        self._snapshot_path = self.harness_dir / ".platform_snapshot.json"

    # ═══════════════════════════════════════════════════════════
    # 对外：启动时刷新一次
    # ═══════════════════════════════════════════════════════════
    def refresh(self) -> None:
        """扫描安装目录、和上次快照对差异、追加 changelog、更新快照与清单。不抛。"""
        try:
            self.harness_dir.mkdir(parents=True, exist_ok=True)
            current = self._scan()
            prev = self._load_snapshot()

            if prev is None:
                # 第一次（或卸载重装后）：从「首次安装」起头
                self._append_changelog([
                    msg("platform.010", version=self.version, dir=str(self.install_root)),
                    msg("platform.011", count=len(current)),
                ])
                log.info("平台清单：首次建立（%d 个文件，版本 %s）",
                         len(current), self.version)
            else:
                entries = self._diff(prev, current)
                if entries:
                    self._append_changelog(entries)
                    log.info("平台清单：记录 %d 条变更", len(entries))

            self._save_snapshot(current)
            self._save_manifest(len(current))
        except Exception:
            log.exception("平台清单刷新失败（忽略，不影响主流程）")

    # ═══════════════════════════════════════════════════════════
    # 对外：给知知的极简平台上下文
    # ═══════════════════════════════════════════════════════════
    def key_paths(self) -> dict[str, str]:
        """关键文件/目录的真实路径 —— 知知回答「XX 存在哪」时用（问题 5b）。"""
        return {
            msg("platform.001"): str(self.install_root),
            msg("platform.002"): str(self.data_dir.resolve()),
            msg("platform.003"): str((self.harness_dir / "harness_memory.md").resolve()),
            msg("platform.004"): str(self.changelog_path.resolve()),
            msg("platform.005"): str((self.data_dir / "projects.json").resolve()),
        }

    def read_brief(self) -> str:
        """
        [5b/5e] 注进知知上下文的平台上下文（紧凑）：版本、安装/数据路径、关键文件位置、
        最近若干条变更。目的：让知知**默认就知道**软件本身的情况，用户问起才答，不主动显摆。
        """
        lines = [
            msg("platform.007", version=self.version),
            msg("platform.008"),
        ]
        for k, v in self.key_paths().items():
            lines.append(msg("platform.018", name=k, value=v))
        recent = self._recent_changelog()
        if recent:
            lines.append(msg("platform.009"))
            lines.extend(f"  · {r}" for r in recent)
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 内部
    # ═══════════════════════════════════════════════════════════
    def _scan(self) -> dict[str, list[float]]:
        """扫描安装目录 → {相对路径: [size, mtime]}。跳过噪音目录、只看跟踪后缀。"""
        out: dict[str, list[float]] = {}
        root = self.install_root
        if not root.is_dir():
            return out
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # 就地剪掉噪音目录（os.walk 会据此不再往下走）
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if Path(fn).suffix.lower() not in _TRACK_SUFFIXES:
                    continue
                fp = Path(dirpath) / fn
                try:
                    st = fp.stat()
                    rel = str(fp.relative_to(root))
                    out[rel] = [st.st_size, round(st.st_mtime, 1)]
                    count += 1
                    if count >= _MAX_FILES:
                        return out
                except OSError:
                    continue
        return out

    @staticmethod
    def _diff(prev: dict[str, list[float]],
              cur: dict[str, list[float]]) -> list[str]:
        """两张清单的差异 → 人读的 changelog 行。数量多时折叠，别把日志刷爆。"""
        prev_keys, cur_keys = set(prev), set(cur)
        added = sorted(cur_keys - prev_keys)
        removed = sorted(prev_keys - cur_keys)
        changed = sorted(k for k in (cur_keys & prev_keys) if prev[k] != cur[k])

        entries: list[str] = []

        def summarize(label: str, items: list[str]) -> None:
            if not items:
                return
            shown = items[:8]
            tail = msg("platform.015", count=len(items)) if len(items) > 8 else ""
            entries.append(msg("platform.016", label=label, count=len(items), tail=tail, names="、".join(shown)))

        summarize(msg("platform.012"), added)
        summarize(msg("platform.013"), removed)
        summarize(msg("platform.014"), changed)
        return entries

    def _load_snapshot(self) -> dict[str, list[float]] | None:
        try:
            if self._snapshot_path.is_file():
                data = json.loads(self._snapshot_path.read_text("utf-8"))
                files = data.get("files") if isinstance(data, dict) else None
                if isinstance(files, dict):
                    # 版本变了 → 也当一条变更记下来
                    old_ver = data.get("version")
                    if old_ver and old_ver != self.version:
                        self._append_changelog(
                            [msg("platform.017", old=old_ver, new=self.version)])
                    return files
        except (OSError, json.JSONDecodeError):
            log.warning("平台快照读失败/损坏 —— 当作首次安装重新建立")
        return None

    def _save_snapshot(self, files: dict[str, list[float]]) -> None:
        self._atomic(self._snapshot_path, json.dumps(
            {"version": self.version, "captured": _now(), "files": files},
            ensure_ascii=False))

    def _save_manifest(self, file_count: int) -> None:
        manifest = {
            "version": self.version,
            "install_root": str(self.install_root),
            "data_dir": str(self.data_dir.resolve()),
            "file_count": file_count,
            "updated": _now(),
            "key_paths": self.key_paths(),
        }
        self._atomic(self.manifest_path,
                     json.dumps(manifest, ensure_ascii=False, indent=2))

    def _append_changelog(self, entries: list[str]) -> None:
        if not entries:
            return
        if not self.changelog_path.exists():
            header = ("# Knowe 变更日志\n\n"
                      "> 从安装那一刻起记录。系统维护，只追加。卸载重装则清空重记。\n\n")
            self._atomic(self.changelog_path, header)
        ts = _now()
        block = f"## {ts}\n" + "".join(f"- {e}\n" for e in entries) + "\n"
        try:
            with open(self.changelog_path, "a", encoding="utf-8") as f:
                f.write(block)
        except OSError as exc:
            log.warning("变更日志写不下去：%s", exc)

    def _recent_changelog(self, max_lines: int = _MAX_CHANGELOG_LINES) -> list[str]:
        try:
            if not self.changelog_path.is_file():
                return []
            lines = self.changelog_path.read_text("utf-8").splitlines()
            # 只要条目行（`- ` 开头）和日期行（`## ` 开头），取最近的
            picked = [ln.strip() for ln in lines
                      if ln.startswith("- ") or ln.startswith("## ")]
            return picked[-max_lines:]
        except OSError:
            return []

    @staticmethod
    def _atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, "utf-8")
        tmp.replace(path)


__all__ = ["PlatformManifest"]
