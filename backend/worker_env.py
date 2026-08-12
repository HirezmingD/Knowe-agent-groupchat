# knowe v0.32 — C3：运行环境事实块（治 FM8「环境失明」）
"""
worker_env.py — 一段注进每个 Worker prompt 的**环境事实**。

## 病根

  Worker 每一轮的上下文里，关于"我站在哪台机器上"的信息只有一行【项目根目录】。
  OS 是什么、Python 几点几、终端跑的是哪个 shell——一概没有。于是它只能猜：
  在 Windows 上写 `ls -la`（Knowe 的 terminal 走的是系统 shell，Windows 下是 cmd，
  没有 ls）；凭训练记忆咬定 Python 3.9 然后写了 3.12 才有的语法报错；
  猜错一次 = 一次失败的工具调用 + 一轮上下文 + 用户多等十秒。

  Hermes 的 build_environment_hints() 每次开机都注入 OS/家目录/cwd，
  Windows 还专门写一句 shell 提示——**它踩过这个坑**。这里照方抓药，
  按 Knowe 的实际情况改配方（Knowe 的 terminal 在 Windows 上是 cmd，不是 bash）。

## 成本

  探测只在**进程生命周期里跑一次**（模块级缓存）：platform/sys 都是本地调用，
  微秒级；之后每轮只是字符串拼接。块本身 3~4 行——它买到的是
  "少一整类猜错环境的失败调用"，这笔账怎么算都划算。

同步纯函数，不认识引擎；engine 组装 worker prompt 时调用（见 engine._run_agent_turn）。
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from .i18n_backend import msg

__all__ = ["environment_block", "reset_probe_cache"]

#: (system, release, machine, python) —— 进程内探一次就够了，这些东西不会中途变。
_PROBE_CACHE: tuple[str, str, str, str] | None = None


def _probe() -> tuple[str, str, str, str]:
    global _PROBE_CACHE
    if _PROBE_CACHE is None:
        _PROBE_CACHE = (
            platform.system() or msg("we.010"),
            platform.release() or "",
            platform.machine() or "",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    return _PROBE_CACHE


def reset_probe_cache() -> None:
    """测试钩子：monkeypatch platform 之后把缓存清掉。"""
    global _PROBE_CACHE
    _PROBE_CACHE = None


def environment_block(workspace_root: Path | str, *, terminal_enabled: bool,
                      _system: str | None = None) -> str:
    """
    拼出【运行环境】块。`_system` 只给测试注入用（"Windows" 走专属提示分支）。

    这里的每一项都对应一类真实的猜错：
      OS/平台   → 在 Windows 上写 bash 语法、在 Linux 上找 C:\\ 盘
      Python    → 凭训练记忆答版本号、用错语法特性
      项目根    → 写出根目录之外的相对路径（配合【项目根目录】那块一起看）
      终端可用性 → 终端被 KNOWE_TERMINAL_ENABLED=0 关掉时，别再教它"去跑个命令"
    """
    system, release, machine, py = _probe()
    if _system is not None:
        system = _system

    os_label = f"{system} {release}".strip()
    term = msg("we.005") + (msg("we.006") if system == "Windows" else "") + msg("we.007") \
        if terminal_enabled else msg("we.008")

    lines = [
        msg("we.001"),
        f"OS: {os_label}{msg('we.002')}{machine or msg('we.003')} | Python: {py}{msg('we.011')}{term}",
        msg("we.004", root=workspace_root),
    ]
    if system == "Windows" and terminal_enabled:
        lines.append(msg("we.009"))
    return "\n".join(lines)
