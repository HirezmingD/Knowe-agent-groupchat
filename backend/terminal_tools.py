# knowe v0.20 — Batch 4：终端 / 后台进程 / 代码执行
"""
terminal_tools.py — `terminal` · `process` · `execute_code`。

这是 Batch 4 里**权力最大**的一块。所有模型可达命令现在都必须经
``sandbox_runner`` 进入 Microsoft Execution Containers (MXC)：项目目录是唯一
读写根，网络默认关闭，父进程环境不继承。MXC 不可用时终端会 fail-closed，
绝不退回宿主 shell。

    那为什么下面还是有一张 `_CATASTROPHIC` 表？因为它防的**不是敌人，是事故**。
    真实的失败模式不是「模型想删你的家目录」，是「模型想删 build/，
    但把 cwd 记错了，于是写成了 rm -rf /」。这类命令有一个共同特征：
    **没有任何正常开发流程会需要它们**。拦下来，零误伤，换回一个不可逆事故。
    这是 assert，不是防火墙 —— 注释里说人话，比在 FIX_NOTES 里吹牛强。

  ★ **不能阻塞事件循环。**
    一个进程跑着所有项目的引擎。`subprocess.run("npm install")` 一卡 90 秒，
    另外三个项目的用户会一起看着界面转圈，而他们什么都没干。
    所以全程 asyncio 子进程 + 有界读取。

  ★ **输出必须有界。**
    `npm install --verbose` 能吐几十 MB；`cat bigfile.bin` 能吐几个 G。
    全读进内存 = OOM，全塞给模型 = 上下文炸掉。这里边读边丢，
    但**始终把管子抽干**——不抽干的话子进程会卡在写管道上，永远不退出，
    然后 timeout 触发，用户看到一个「超时」，而真相是我们没读。
"""

from __future__ import annotations

import asyncio
import hashlib
import contextlib
import locale
import logging
import os
import re
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_runtime import ToolError, clip_middle, normalize_command
from . import sandbox_runner

log = logging.getLogger("knowe.terminal")

_IS_WINDOWS = os.name == "nt"

# ═══════════════════════════════════════════════════════════════
# 事故拦截（不是安全边界，见模块头）
# ═══════════════════════════════════════════════════════════════

#: 每一条都满足：① 不可逆 ② 目标在项目之外 ③ 没有任何正常开发流程会用到。
#: 命中 → 回一句人话让模型把范围收回项目里，**不弹卡、不中断回合**。
_CATASTROPHIC: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rR][a-zA-Z]*[fF]?[a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|/\*|~|~/|\$HOME|\$\{HOME\})(\s|$|\*)"),
     "这条命令会递归删除项目目录之外的整棵目录树（/ 或家目录）"),
    (re.compile(r"\brm\s+.*\s(/etc|/usr|/bin|/sbin|/var|/boot|/lib|/opt|/root)(/\S*)?(\s|$)"),
     "这条命令会删除系统目录"),
    (re.compile(r"\bmkfs(\.\w+)?\b|\bfdisk\b|\bdiskutil\s+(erase|partition)"),
     "这条命令会格式化磁盘"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/(disk|sd|nvme|hd)"),
     "这条命令会直接写裸盘设备"),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
     "这是 fork 炸弹"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b|\bkillall5\b"),
     "这条命令会关机或重启用户的电脑"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|disk)\w*"),
     "这条命令会覆写裸盘设备"),
    (re.compile(r"\bchmod\s+(-[a-zA-Z]+\s+)*777\s+(/|/etc|/usr|~)(\s|$)"),
     "这条命令会改掉系统目录的权限"),
)


def guard_command(command: str) -> None:
    for rx, why in _CATASTROPHIC:
        if rx.search(command):
            raise ToolError(
                f"这条命令被拦下了：{why}。\n"
                "终端的工作目录已经在项目根目录里了——要清理产物请用相对路径"
                "（如 `rm -rf build`），不要写 / 或 ~ 开头的绝对路径。"
                "如果你确实需要动项目之外的东西，请让用户自己在他的终端里执行。"
            )


# ═══════════════════════════════════════════════════════════════
# 子进程基础设施
# ═══════════════════════════════════════════════════════════════

def child_env(
    extra: dict[str, str] | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, str]:
    """Return MXC's strict child allowlist; never clone ``os.environ``."""

    if workspace_root is None:
        raise ToolError("sandbox workspace is required before constructing a child environment")
    try:
        return sandbox_runner.minimal_environment(workspace_root, extra)
    except (sandbox_runner.SandboxUnavailable, ValueError) as exc:
        raise ToolError(f"终端沙箱环境无效：{exc}") from None


def decode_output(raw: bytes) -> str:
    """UTF-8 优先；解不开就按系统本地编码再试一次（中文 Windows 的 cp936）。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    enc = locale.getpreferredencoding(False) or "utf-8"
    try:
        return raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")


def _kill_tree(proc: asyncio.subprocess.Process, *, force: bool = False) -> None:
    if proc.returncode is not None:
        return
    try:
        if _IS_WINDOWS:
            # MXC owns the sandbox Job Object.  Let its CTRL_BREAK cleanup path
            # kill descendants and restore Tier-3 DACL state; force is only the
            # bounded emergency fallback.
            sandbox_runner.terminate(proc, force=force)
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill() if force else proc.terminate()
        except (ProcessLookupError, OSError):
            pass


def sync_kill_tree(pid: int) -> None:
    """atexit 路径用的同步版本——那时候已经没有事件循环了。"""
    try:
        if _IS_WINDOWS:
            # ``pid`` is the trusted outer launcher, not the model process.
            # TerminateProcess closes its sole Job handle, so
            # KILL_ON_JOB_CLOSE atomically reaps wxc-exec and every descendant.
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


@dataclass(frozen=True)
class _DrainCapture:
    prefix: bytes
    tail: bytes
    bytes_total: int
    sha256: str


async def _drain_output(
    stream: asyncio.StreamReader,
    *,
    capture_bytes: int,
    log_path: Path | None = None,
) -> _DrainCapture:
    """Drain the pipe completely, hash every byte, and optionally persist all output.

    Only a bounded prefix/tail stays in memory.  When ``log_path`` is provided, the file
    is flushed and fsynced before the result is returned, so a successful terminal tool
    response can safely advertise it as the complete attempt log.
    """

    cap = max(1, int(capture_bytes))
    tail_cap = max(1, cap // 2)
    prefix = bytearray()
    tail = bytearray()
    total = 0
    digest = hashlib.sha256()
    handle = None
    try:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("wb")
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
            if handle is not None:
                handle.write(chunk)
            room = cap - len(prefix)
            if room > 0:
                prefix.extend(chunk[:room])
            tail.extend(chunk)
            if len(tail) > tail_cap:
                del tail[: len(tail) - tail_cap]
        if handle is not None:
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if handle is not None:
            handle.close()
    return _DrainCapture(
        prefix=bytes(prefix),
        tail=bytes(tail),
        bytes_total=total,
        sha256=digest.hexdigest(),
    )


@dataclass
class CommandResult:
    output: str
    exit_code: int | None
    timed_out: bool
    truncated: bool
    duration_s: float
    bytes_total: int
    output_head: str = ""
    output_tail: str = ""
    output_sha256: str = ""


_MXC_LIFECYCLE_GRACE_S = 30.0
_MXC_TIMEOUT_MARKER = b"script timed out after "
_MXC_TIMEOUT_EXIT_CODES = {-1, 124, 0xFFFFFFFF}


def _mxc_reported_timeout(
    capture: _DrainCapture,
    *,
    returncode: int | None,
    timeout_s: float,
) -> bool:
    """Normalize both MXC timeout surfaces into one product-level signal.

    MXC 0.7.0 may either print ``script timed out after ...`` or exit with the
    unsigned Windows value ``0xFFFFFFFF`` and an empty pipe.  Python can expose
    that value as either ``4294967295`` or ``-1`` depending on the launcher.
    """

    return (
        _MXC_TIMEOUT_MARKER in capture.prefix
        or _MXC_TIMEOUT_MARKER in capture.tail
        or (float(timeout_s) > 0 and returncode in _MXC_TIMEOUT_EXIT_CODES)
    )


def _windows_cmd_compatibility_note(command: str, output: str, returncode: int | None) -> str:
    """Explain a known MXC/cmd incompatibility instead of returning a bare denial."""

    if (
        _IS_WINDOWS
        and returncode not in (None, 0)
        and "access is denied." in output.lower()
        and re.search(r"(?i)(?:^|[&|]\s*)dir(?:\.exe)?(?:\s|$)", command)
    ):
        return (
            output
            + "\n[Knowe sandbox] This Windows MXC AppContainer cannot run cmd.exe's "
            "built-in dir formatter. The project filesystem boundary is still active; "
            "enumerate with a project-local script (for example, Python os.listdir) instead.\n"
        )
    return output


async def run_command(
    command: str,
    *,
    cwd: Path,
    timeout_s: float,
    max_output: int,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> CommandResult:
    """Run one command and preserve its complete merged output in an attempt log.

    ``timeout_s`` is the caller's command-operation boundary, not an installation-wide
    task ceiling.  The default is supplied by the tool handler only when the argument is
    omitted.  Cancellation and timeout always terminate the process tree.
    """

    started = time.monotonic()
    # [2026-08-09] Windows 命令规范化：LLM 习惯 `mkdir -p`，cmd.exe 会把 -p 当目录建出来
    command = normalize_command(command)
    try:
        proc = await sandbox_runner.spawn(
            command,
            workspace_root=cwd,
            cwd=str(cwd),
            timeout_s=timeout_s,
            env=env,
        )
    except (sandbox_runner.SandboxUnavailable, OSError, ValueError) as exc:
        raise ToolError(f"终端沙箱不可用：{exc}") from None

    assert proc.stdout is not None
    capture_bytes = max(4096, max(1, int(max_output)) * 4)
    reader = asyncio.create_task(
        _drain_output(proc.stdout, capture_bytes=capture_bytes, log_path=log_path)
    )
    timed_out = False
    capture: _DrainCapture | None = None
    try:
        # MXC enforces process.timeout *inside* the sandbox and tree-kills its
        # Job Object before restoring policy.  Give that security teardown a
        # bounded grace period rather than racing it with a host-side kill.
        await asyncio.wait_for(
            proc.wait(),
            timeout=max(0.001, float(timeout_s)) + _MXC_LIFECYCLE_GRACE_S,
        )
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc, force=True)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5)
        try:
            sandbox_runner.recover_after_termination()
        except sandbox_runner.SandboxUnavailable as exc:
            raise ToolError(f"终端沙箱超时后策略恢复失败：{exc}") from None
    except asyncio.CancelledError:
        _kill_tree(proc, force=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
        with contextlib.suppress(sandbox_runner.SandboxUnavailable):
            sandbox_runner.recover_after_termination()
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        raise
    finally:
        if not reader.cancelled():
            try:
                capture = await asyncio.wait_for(reader, timeout=5)
            except asyncio.TimeoutError:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader

    if capture is None:
        raise ToolError("命令输出管道未能完整落盘")
    await sandbox_runner.wait_for_cleanup(proc)
    timed_out = (
        timed_out
        or _mxc_reported_timeout(
            capture,
            returncode=proc.returncode,
            timeout_s=timeout_s,
        )
    )
    if timed_out:
        try:
            sandbox_runner.recover_after_termination()
        except sandbox_runner.SandboxUnavailable as exc:
            raise ToolError(f"终端沙箱超时后策略恢复失败：{exc}") from None

    truncated = capture.bytes_total > len(capture.prefix)
    if truncated:
        head_chars = max(1, int(max_output) // 2)
        tail_chars = max(1, int(max_output) - head_chars)
        output = ""
        output_head = decode_output(capture.prefix)[:head_chars]
        output_tail = decode_output(capture.tail)[-tail_chars:]
    else:
        output, clipped = clip_middle(decode_output(capture.prefix), max(1, int(max_output)))
        truncated = clipped
        output_head = output if truncated else ""
        output_tail = ""
    output = _windows_cmd_compatibility_note(command, output, proc.returncode)

    return CommandResult(
        output=output,
        exit_code=proc.returncode,
        timed_out=timed_out,
        truncated=truncated,
        duration_s=round(time.monotonic() - started, 2),
        bytes_total=capture.bytes_total,
        output_head=output_head,
        output_tail=output_tail,
        output_sha256=capture.sha256,
    )


# ═══════════════════════════════════════════════════════════════
# execute_code
# ═══════════════════════════════════════════════════════════════

def python_for(workspace_root: Path) -> tuple[str, str]:
    """
    跑 execute_code 该用哪个 Python？

    直觉答案是 `sys.executable`（Knowe 自己的解释器）。但那个解释器里
    **没有用户项目的依赖**：Worker 刚 `pip install pandas` 装进了项目的
    .venv，转头 execute_code 里 `import pandas` 报 ModuleNotFoundError——
    它会以为是自己装错了，然后再装一遍。

    所以：项目里有 venv 就用项目的；源码开发态退回基础解释器（不能用
    启动后端的外层 venv shim）；PyInstaller 打包态由 KnoweBackend.exe 的
    专用 ``--knowe-sandbox-execute`` 入口运行。
    返回 (可执行路径, 说明) —— 说明会回给模型，让它知道自己站在哪。
    """
    names = ("Scripts/python.exe",) if _IS_WINDOWS else ("bin/python3", "bin/python")
    for venv in (".venv", "venv", "env", ".env"):
        for name in names:
            candidate = workspace_root / venv / name
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate), f"项目虚拟环境（{venv}）"
            except OSError:
                continue
    if getattr(sys, "frozen", False):
        return sys.executable, "Knowe 打包内置解释器（项目里没找到虚拟环境）"
    base_executable = str(getattr(sys, "_base_executable", "") or "")
    if base_executable and Path(base_executable).is_file():
        return base_executable, "Knowe 后端基础解释器（项目里没找到虚拟环境）"
    return sys.executable, "Knowe 后端解释器（项目里没找到虚拟环境）"


async def run_python(
    code: str,
    *,
    cwd: Path,
    timeout_s: float,
    max_output: int,
) -> tuple[CommandResult, str]:
    """
    脚本暂存在项目内的隐藏 sandbox 临时目录；这是 MXC 唯一允许写入的根。
    进程退出后立即删除，项目模块通过受限的 ``PYTHONPATH`` 导入。
    """
    try:
        sandbox_runner.validate_workspace_security(cwd)
    except sandbox_runner.SandboxUnavailable as exc:
        raise ToolError(f"Python 沙箱项目门禁失败：{exc}") from None
    interpreter, why = python_for(cwd)
    execution_name = hashlib.sha256(
        f"{time.time_ns()}\0{code}".encode("utf-8")
    ).hexdigest()[:20]
    try:
        tmpdir = sandbox_runner.create_execution_directory(cwd, execution_name)
    except sandbox_runner.SandboxUnavailable as exc:
        raise ToolError(f"Python 沙箱临时目录不可用：{exc}") from None
    script = tmpdir / "knowe_script.py"
    proc: asyncio.subprocess.Process | None = None
    try:
        script.write_text(code, "utf-8")
        env = {"PYTHONPATH": str(cwd)}
        started = time.monotonic()
        escaped_interpreter = str(interpreter).replace('"', '""')
        escaped_script = str(script).replace('"', '""')
        if getattr(sys, "frozen", False) and Path(interpreter) == Path(sys.executable):
            command = (
                f'"{escaped_interpreter}" --knowe-sandbox-execute "{escaped_script}"'
            )
        else:
            command = f'"{escaped_interpreter}" "{escaped_script}"'
        try:
            proc = await sandbox_runner.spawn(
                command,
                workspace_root=cwd,
                cwd=str(cwd),
                timeout_s=timeout_s,
                env=env,
                executable=interpreter,
            )
        except (sandbox_runner.SandboxUnavailable, OSError, ValueError) as exc:
            raise ToolError(f"Python 沙箱不可用（{interpreter}）：{exc}") from None

        assert proc.stdout is not None
        reader = asyncio.create_task(
            _drain_output(proc.stdout, capture_bytes=max(4096, max_output * 4))
        )
        timed_out = False
        try:
            await asyncio.wait_for(
                proc.wait(),
                timeout=max(0.001, float(timeout_s)) + _MXC_LIFECYCLE_GRACE_S,
            )
        except asyncio.TimeoutError:
            timed_out = True
            _kill_tree(proc, force=True)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
            try:
                sandbox_runner.recover_after_termination()
            except sandbox_runner.SandboxUnavailable as exc:
                raise ToolError(f"Python 沙箱超时后策略恢复失败：{exc}") from None
        except asyncio.CancelledError:
            _kill_tree(proc, force=True)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            with contextlib.suppress(sandbox_runner.SandboxUnavailable):
                sandbox_runner.recover_after_termination()
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
            raise
        capture = await asyncio.wait_for(reader, timeout=5)
        timed_out = (
            timed_out
            or _mxc_reported_timeout(
                capture,
                returncode=proc.returncode,
                timeout_s=timeout_s,
            )
        )
        if timed_out:
            try:
                sandbox_runner.recover_after_termination()
            except sandbox_runner.SandboxUnavailable as exc:
                raise ToolError(f"Python 沙箱超时后策略恢复失败：{exc}") from None

        text = decode_output(capture.prefix)
        # 临时目录路径会出现在 traceback 里，对模型是噪音（它没写过那个路径）。
        text = text.replace(str(script), "<execute_code>")
        text, clipped = clip_middle(text, max_output)
        truncated = clipped or capture.bytes_total > len(capture.prefix)
        tail = decode_output(capture.tail).replace(str(script), "<execute_code>") if truncated else ""
        return CommandResult(
            output=text,
            exit_code=proc.returncode,
            timed_out=timed_out,
            truncated=truncated,
            duration_s=round(time.monotonic() - started, 2),
            bytes_total=capture.bytes_total,
            output_head=text if truncated else "",
            output_tail=tail[-max(1, max_output // 2):] if truncated else "",
            output_sha256=capture.sha256,
        ), why
    finally:
        if proc is not None and proc.returncode is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sandbox_runner.wait_for_cleanup(proc)
        shutil.rmtree(tmpdir, ignore_errors=True)
        sandbox_runner.cleanup_empty_runtime_dirs(cwd)


# ═══════════════════════════════════════════════════════════════
# 后台进程
# ═══════════════════════════════════════════════════════════════

class _RingLog:
    """
    头 + 尾的日志环。

    纯尾巴环（只留最后 N 字节）会丢掉一个服务最重要的一行：
    `Listening on http://localhost:3000` —— 它在**最开头**，而之后几千行
    请求日志会把它挤出去。于是 Worker 起了服务，却查不到端口是多少。
    留头 + 留尾，中间挖掉，两个关键区都在。
    """

    __slots__ = ("_head", "_tail", "_head_cap", "_tail_cap", "_dropped", "total")

    def __init__(self, cap: int) -> None:
        self._head_cap = max(1, cap // 5)
        self._tail_cap = max(1, cap - self._head_cap)
        self._head = bytearray()
        self._tail = bytearray()
        self._dropped = 0
        self.total = 0

    def write(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._head_cap - len(self._head)
        if room > 0:
            self._head.extend(chunk[:room])
            chunk = chunk[room:]
            if not chunk:
                return
        self._tail.extend(chunk)
        overflow = len(self._tail) - self._tail_cap
        if overflow > 0:
            del self._tail[:overflow]
            self._dropped += overflow

    def read(self) -> str:
        head = decode_output(bytes(self._head))
        tail = decode_output(bytes(self._tail))
        if not self._dropped:
            return head + tail
        return f"{head}\n…（中间省略 {self._dropped} 字）…\n{tail}"

    def tail_lines(self, n: int) -> str:
        text = self.read()
        lines = text.splitlines()
        if len(lines) <= n:
            return text
        return "\n".join(lines[-n:])


@dataclass
class BackgroundProcess:
    session_id: str
    command: str
    agent_id: str
    cwd: str
    proc: asyncio.subprocess.Process
    log: _RingLog
    started_at: float
    exited: asyncio.Event = field(default_factory=asyncio.Event)
    exit_code: int | None = None
    finished_at: float | None = None
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return not self.exited.is_set()

    def info(self) -> dict[str, Any]:
        end = self.finished_at or time.monotonic()
        return {
            "session_id": self.session_id,
            "command": self.command,
            "started_by": self.agent_id,
            "state": "running" if self.running else "exited",
            "exit_code": self.exit_code,
            "uptime_s": round(end - self.started_at, 1),
            "output_bytes": self.log.total,
            "pid": self.proc.pid,
        }


class ProcessPool:
    """
    一个**项目**（不是一个 Worker）名下的后台进程。

    为什么按项目而不按 agent 隔离：这是一支团队。前端起了 dev server，
    测试要连上去跑 e2e —— 如果两人各看各的进程表，测试会再起一个，
    然后 3000 端口冲突，然后它以为是代码坏了。
    表里带 started_by，谁起的一目了然；杀别人的进程是可以的，
    就像两个同事共用一台开发机。
    """

    def __init__(self, project_id: str, *, max_procs: int, log_bytes: int) -> None:
        self.project_id = project_id
        self._procs: dict[str, BackgroundProcess] = {}
        self._seq = 0
        self._max = max_procs
        self._log_bytes = log_bytes

    # ── 生命周期 ──
    async def start(self, command: str, *, agent_id: str, cwd: Path) -> BackgroundProcess:
        command = normalize_command(command)
        guard_command(command)
        alive = [p for p in self._procs.values() if p.running]
        if len(alive) >= self._max:
            listed = "、".join(p.session_id for p in alive)
            raise ToolError(
                f"后台进程已经有 {len(alive)} 个了（{listed}），到上限了。"
                "先用 process(action='kill') 收掉不用的，再起新的。"
            )
        try:
            proc = await sandbox_runner.spawn(
                command,
                workspace_root=cwd,
                cwd=str(cwd),
            )
        except (sandbox_runner.SandboxUnavailable, OSError, ValueError) as exc:
            raise ToolError(f"后台终端沙箱不可用：{exc}") from None

        self._seq += 1
        bp = BackgroundProcess(
            session_id=f"proc_{self._seq}",
            command=command,
            agent_id=agent_id,
            cwd=str(cwd),
            proc=proc,
            log=_RingLog(self._log_bytes),
            started_at=time.monotonic(),
        )
        self._procs[bp.session_id] = bp
        bp._tasks.append(asyncio.ensure_future(self._supervise(bp)))
        return bp

    async def _supervise(self, bp: BackgroundProcess) -> None:
        """
        一直抽日志，直到进程死掉。

        **这个任务是后台进程能活下去的原因**：没人读管道的话，子进程写满 64KB
        缓冲就会永久阻塞。一个「起了但是卡死」的 dev server，根因往往就是这个。
        """
        try:
            assert bp.proc.stdout is not None
            while True:
                chunk = await bp.proc.stdout.read(65536)
                if not chunk:
                    break
                bp.log.write(chunk)
        except (asyncio.CancelledError, ValueError, OSError):
            pass
        finally:
            try:
                bp.exit_code = await bp.proc.wait()
            except (asyncio.CancelledError, ProcessLookupError, OSError):
                bp.exit_code = bp.proc.returncode
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sandbox_runner.wait_for_cleanup(bp.proc)
            bp.finished_at = time.monotonic()
            bp.exited.set()

    def get(self, session_id: Any) -> BackgroundProcess:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolError("要指定 session_id —— 先用 process(action='list') 看有哪些")
        bp = self._procs.get(session_id.strip())
        if bp is None:
            known = "、".join(self._procs) or "（一个都没有）"
            raise ToolError(f"没有这个后台进程：{session_id}。现有：{known}")
        return bp

    def list(self) -> list[dict[str, Any]]:
        return [p.info() for p in self._procs.values()]

    async def wait(self, bp: BackgroundProcess, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(bp.exited.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    async def kill(self, bp: BackgroundProcess, *, grace_s: float = 3.0) -> bool:
        if not bp.running:
            return False
        _kill_tree(bp.proc, force=True)
        try:
            await asyncio.wait_for(bp.exited.wait(), timeout=grace_s)
        except asyncio.TimeoutError:
            _kill_tree(bp.proc, force=True)
            try:
                await asyncio.wait_for(bp.exited.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass
        if _IS_WINDOWS:
            try:
                sandbox_runner.recover_after_termination()
            except sandbox_runner.SandboxUnavailable as exc:
                raise ToolError(f"后台沙箱终止后策略恢复失败：{exc}") from None
        return True

    async def submit(self, bp: BackgroundProcess, data: str) -> None:
        if not bp.running:
            raise ToolError(f"{bp.session_id} 已经结束了（exit_code={bp.exit_code}），没法再给它输入")
        if _IS_WINDOWS:
            raise ToolError(
                "Windows MXC 0.7 不会将 wxc-exec 的标准输入转交给容器内进程；"
                "为避免输入静默丢失，交互式 stdin 已 fail-closed。"
                "请把输入放在命令参数或项目内文件中后重新启动。"
            )
        stdin = bp.proc.stdin
        if stdin is None:
            raise ToolError(f"{bp.session_id} 没有可写的标准输入")
        try:
            stdin.write((data if data.endswith("\n") else data + "\n").encode("utf-8"))
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise ToolError(f"写标准输入失败（进程可能已经不收了）：{exc}") from None

    # ── 收摊 ──
    async def aclose(self, *, immediate: bool = False) -> None:
        for bp in list(self._procs.values()):
            if bp.running:
                await self.kill(bp, grace_s=0.5 if immediate else 3.0)
            for task in bp._tasks:
                task.cancel()
        self._procs.clear()

    def emergency_close(self) -> None:
        for bp in list(self._procs.values()):
            if bp.running:
                sync_kill_tree(bp.proc.pid)
        self._procs.clear()


__all__ = [
    "BackgroundProcess",
    "CommandResult",
    "ProcessPool",
    "child_env",
    "decode_output",
    "guard_command",
    "python_for",
    "run_command",
    "run_python",
]
