# knowe v0.20 — Batch 4：Agent 运行时资源
"""
agent_runtime.py — 新工具们（终端 / 后台进程 / 浏览器）共用的**寿命管理**。

v0.19 之前，Worker 的工具全是「一次调用、一次返回」：读个文件、写个文件，
调用结束，什么都不剩。Batch 4 第一次引入**比一次调用活得久的东西**：

  · `process` 起的后台进程 —— `npm run dev` 会一直占着 3000 端口；
  · `browser_*` 的 Playwright 会话 —— 一个 headless Chromium 常驻 150~250MB。

这些东西必须有人管着关，否则：用户切一次项目目录漏一个 Chromium，
切十次就是 2GB；应用崩一次，`npm run dev` 就永远占着端口，直到他自己去
任务管理器里找。**这是最容易被忽略、又最容易被用户骂的一类 bug。**

所以这里立一条规矩：**凡是活过一次调用的资源，都挂在 ProjectRuntime 上。**

  ┌─ ProjectRuntime(project_id)
  │    ├─ slot("processes") → terminal_tools.ProcessPool
  │    └─ slot("browser")   → browser_tools.BrowserPool
  └─ engine.stop() → shutdown_project_runtime(project_id) → 全部收摊

**为什么是 slot 而不是写死两个字段**：写死就得 `import terminal_tools`，
而 terminal_tools 要 `import agent_runtime` 拿 ToolError —— 循环导入。
slot 只认「一个有 aclose() 的东西」，谁都不认识谁，就没有循环。

三道关门的口子，从优雅到粗暴：
  ① engine.stop()      —— 正常路径（切目录、关应用）
  ② 空闲回收           —— 浏览器自己开的 reaper，闲够了就把 Chromium 放了
  ③ atexit 兜底        —— 进程要没了还来得及给后台进程补一刀 SIGKILL

第 ③ 道只对**进程**有意义（Playwright 的 node driver 会自己带走 Chromium），
而且它是同步的：atexit 里没有事件循环，await 不了任何东西。
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import re
import signal
import subprocess
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

log = logging.getLogger("knowe.runtime")


class ToolError(Exception):
    """
    工具层的「把话说回给模型」异常。

    **message 是要给 LLM 看的中文人话**，不是 traceback。tools_knowe 的
    `_guarded` 会把它转成 `{"status":"error","message":...}` 交回模型，
    模型下一轮就能自己改。

    抛这个异常 = 我预见到了这种失败，并且已经想好该怎么跟模型解释。
    抛别的异常 = 我没预见到 —— 那种会被 `_guarded` 兜住，记进日志，
    同样不会把引擎带倒（§七：不抛异常、不阻塞 Agent 回合）。
    """


# ═══════════════════════════════════════════════════════════════
# Windows 命令规范化（源头消除「-p」目录事故）
# ═══════════════════════════════════════════════════════════════
# Worker 的 LLM 习惯生成 Linux 写法的 `mkdir -p 目录`；后端用
# `create_subprocess_shell` 执行，Windows 上 = `cmd.exe /c`。
# cmd.exe 的 mkdir 有两处跟 Linux 不同，都会出事故（2026-08-09 实测）：
#   ① 不认 `-p` 标志——把它当**第一个路径参数**，静默建出名为「-p」的目录、
#      退出码 0，模型还以为成功了；
#   ② 路径只认反斜杠——`mkdir a/b`、`mkdir ./dist` 都报语法错误（exit 1），
#      只有 `mkdir a\b`（反斜杠、可一次建多级）能过。
# 这里把 Linux 写法翻译成 cmd 等价形式：去标志 + 去 ./ 前缀 + 正斜杠转反斜杠。
#
# 只匹配命令边界（行首 / ; & | 之后）的 mkdir：
#   ① 不误伤引号/字符串里出现的 "mkdir -p" 文本（如 git commit -m）；
#   ② `mkdir -path`（-p 后跟字母）不是标志，不动。
# 非 Windows 平台不处理（sh 的 mkdir -p 本来就合法）。
_MKDIR_P = re.compile(r"(^|[;&|]\s*)mkdir\s+(-p|--parents)(?=\s|$|[./])", re.IGNORECASE)
_MKDIR_ARGS = re.compile(r"(^|[;&|]\s*)(?:mkdir|md)\s+([^;&|]*?)(?=$|[;&|])", re.IGNORECASE)


def _fix_mkdir_args(m: re.Match[str]) -> str:
    prefix, args = m.group(1), m.group(2)
    args = re.sub(r"(^|[\s\"']+)\./", r"\1", args)  # 去掉 ./ 前缀（路径开头/空格/引号后）
    args = args.replace("/", "\\")                 # 正斜杠 → 反斜杠（cmd mkdir 只认反斜杠）
    return f"{prefix}mkdir {args}"


def normalize_command(command: str) -> str:
    """把 Linux 习惯命令规范成当前平台 shell 能正确执行的等价形式。"""
    if os.name != "nt":
        return command
    command = _MKDIR_P.sub(r"\1mkdir", command)
    return _MKDIR_ARGS.sub(_fix_mkdir_args, command)


class Closable(Protocol):
    """挂在 ProjectRuntime 上的东西，只需要会关门。"""

    async def aclose(self, *, immediate: bool = False) -> Any: ...


T = TypeVar("T")


_ATTEMPT_PROCESS_REGISTRIES: "weakref.WeakSet[AttemptProcessRegistry]" = weakref.WeakSet()


@dataclass
class _AttemptProcess:
    process_id: str
    command: str
    process: asyncio.subprocess.Process
    started_at: float
    log: bytearray = field(default_factory=bytearray)
    reader_task: asyncio.Task[Any] | None = None


class AttemptProcessRegistry:
    """Own every background process started by one task attempt.

    This object is injected into tool runtime context; it is never a model-facing tool.
    Completion, cancellation, WAITING/BLOCKED finalization, and Runtime errors all call
    ``aclose``. Exact task/attempt lineage prevents the project-wide process control that
    the retired ``process`` model tool exposed.
    """

    def __init__(
        self,
        *,
        project_id: str,
        task_id: str,
        attempt_id: str,
        workspace_root: str | Path,
        max_processes: int = 4,
        log_bytes: int = 64_000,
    ) -> None:
        self.project_id = str(project_id)
        self.task_id = str(task_id)
        self.attempt_id = str(attempt_id)
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        if not all((self.project_id, self.task_id, self.attempt_id)):
            raise ValueError("AttemptProcessRegistry requires project/task/attempt lineage")
        self.max_processes = max(1, int(max_processes))
        self.log_bytes = max(4_096, int(log_bytes))
        self._items: dict[str, _AttemptProcess] = {}
        self._closed = False
        self._lock = asyncio.Lock()
        _ATTEMPT_PROCESS_REGISTRIES.add(self)

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    async def start(self, command: str, *, cwd: str | Path) -> dict[str, Any]:
        command = normalize_command(str(command or "").strip())
        if not command:
            raise ValueError("background command must be non-empty")
        workdir = Path(cwd).expanduser().resolve()
        if not self._under(workdir, self.workspace_root) or not workdir.is_dir():
            raise PermissionError("background command cwd must be an existing project directory")

        async with self._lock:
            if self._closed:
                raise RuntimeError("attempt process registry is closed")
            running = [item for item in self._items.values() if item.process.returncode is None]
            if len(running) >= self.max_processes:
                raise RuntimeError(f"attempt background process limit reached ({self.max_processes})")

            environment = os.environ.copy()
            environment.update(
                {
                    "KNOWE_PROJECT_ID": self.project_id,
                    "KNOWE_TASK_ID": self.task_id,
                    "KNOWE_ATTEMPT_ID": self.attempt_id,
                    "KNOWE_WORKSPACE_ROOT": str(self.workspace_root),
                }
            )
            kwargs: dict[str, Any] = {
                "cwd": str(workdir),
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.STDOUT,
                "env": environment,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            process = await asyncio.create_subprocess_shell(command, **kwargs)
            process_id = "attempt_proc_" + uuid.uuid4().hex[:12]
            item = _AttemptProcess(process_id, command, process, time.monotonic())
            item.reader_task = asyncio.create_task(
                self._drain(item),
                name=f"knowe-{process_id}-log",
            )
            self._items[process_id] = item

        # Surface immediate startup failures without providing lifecycle controls to the model.
        await asyncio.sleep(0.25)
        return self._info(item)

    async def _drain(self, item: _AttemptProcess) -> None:
        stream = item.process.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(4_096)
                if not chunk:
                    break
                item.log.extend(chunk)
                overflow = len(item.log) - self.log_bytes
                if overflow > 0:
                    del item.log[:overflow]
        except (asyncio.CancelledError, RuntimeError):
            raise
        except Exception:
            log.debug("attempt process log drain failed", exc_info=True)

    def _info(self, item: _AttemptProcess) -> dict[str, Any]:
        return {
            "process_id": item.process_id,
            "running": item.process.returncode is None,
            "exit_code": item.process.returncode,
            "pid": item.process.pid,
            "log_tail": bytes(item.log).decode("utf-8", "replace")[-4_000:],
            "owner": {
                "project_id": self.project_id,
                "task_id": self.task_id,
                "attempt_id": self.attempt_id,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        """Internal telemetry only; no handler exposes this as a model action."""
        return {
            "schema": "knowe.runtime.attempt-process-registry.v1",
            "project_id": self.project_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "closed": self._closed,
            "processes": [self._info(item) for item in self._items.values()],
        }

    async def _terminate(self, item: _AttemptProcess, *, immediate: bool) -> None:
        process = item.process
        if process.returncode is None:
            try:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await killer.wait()
                else:
                    sig = signal.SIGKILL if immediate else signal.SIGTERM
                    os.killpg(process.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                with contextlib.suppress(ProcessLookupError):
                    process.kill() if immediate else process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5 if immediate else 3.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=2.0)
        if item.reader_task is not None:
            if not item.reader_task.done():
                item.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await item.reader_task

    async def aclose(self, *, immediate: bool = False) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            items = tuple(self._items.values())
        await asyncio.gather(
            *(self._terminate(item, immediate=immediate) for item in items),
            return_exceptions=True,
        )
        _ATTEMPT_PROCESS_REGISTRIES.discard(self)

    def emergency_close(self) -> None:
        self._closed = True
        for item in tuple(self._items.values()):
            process = item.process
            if process.returncode is not None:
                continue
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                with contextlib.suppress(Exception):
                    process.kill()


class ProjectRuntime:
    """一个项目名下所有「活过一次调用」的资源。"""

    __slots__ = ("project_id", "_slots")

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self._slots: dict[str, Any] = {}

    def slot(self, name: str, factory: Callable[[], T]) -> T:
        """取一个具名槽位，没有就现建。**同步**——工具调用路径上不能有 await 抖动。"""
        existing = self._slots.get(name)
        if existing is None:
            existing = factory()
            self._slots[name] = existing
        return existing  # type: ignore[return-value]

    def peek(self, name: str) -> Any | None:
        """看一眼槽位，**不创建**。关门的时候用——没建过就没什么可关的。"""
        return self._slots.get(name)

    async def aclose(self, *, immediate: bool = False) -> list[str]:
        """Close every long-lived slot and return an auditable issue list.

        Normal shutdown remains best-effort, but permanent deletion needs to know
        whether a browser/process pool failed to close before it mutates the project
        directory.  A failed async closer receives one synchronous emergency-close
        attempt in immediate mode; the issue is still returned so the filesystem
        staging error can name Knowe's own teardown problem instead of reporting only
        a generic WinError 5.
        """
        slots, self._slots = self._slots, {}
        issues: list[str] = []
        for name, obj in slots.items():
            closer = getattr(obj, "aclose", None)
            if not callable(closer):
                issue = f"运行时资源 {name} 未提供异步关闭接口"
                issues.append(issue)
                log.warning("[%s] %s", self.project_id, issue)
                if immediate:
                    killer = getattr(obj, "emergency_close", None)
                    if callable(killer):
                        try:
                            killer()
                        except Exception as kill_exc:
                            issues.append(
                                f"运行时资源 {name} 强制关闭失败："
                                f"{' '.join(str(kill_exc).split()) or kill_exc.__class__.__name__}"
                            )
                continue
            try:
                await closer(immediate=immediate)
            except Exception as exc:
                issue = f"运行时资源 {name} 关闭失败：{' '.join(str(exc).split()) or exc.__class__.__name__}"
                issues.append(issue)
                log.warning("[%s] %s", self.project_id, issue, exc_info=True)
                if immediate:
                    killer = getattr(obj, "emergency_close", None)
                    if callable(killer):
                        try:
                            killer()
                        except Exception as kill_exc:
                            kill_issue = (
                                f"运行时资源 {name} 强制关闭失败："
                                f"{' '.join(str(kill_exc).split()) or kill_exc.__class__.__name__}"
                            )
                            issues.append(kill_issue)
                            log.warning("[%s] %s", self.project_id, kill_issue, exc_info=True)
        # Let subprocess transports and Playwright driver callbacks finish reaping
        # handles before the caller starts a Windows rename transaction.
        await asyncio.sleep(0)
        return issues

    def emergency_close(self) -> None:
        """进程要退了。同步、粗暴、尽力而为——只有实现了这个方法的槽位会响应。"""
        for obj in list(self._slots.values()):
            killer = getattr(obj, "emergency_close", None)
            if callable(killer):
                try:
                    killer()
                except Exception:
                    pass


_RUNTIMES: dict[str, ProjectRuntime] = {}


def runtime_for(project_id: str) -> ProjectRuntime:
    rt = _RUNTIMES.get(project_id)
    if rt is None:
        rt = ProjectRuntime(project_id)
        _RUNTIMES[project_id] = rt
    return rt


async def shutdown_project_runtime(project_id: str, *, immediate: bool = False) -> list[str]:
    """
    engine.stop() 的唯一入口。项目没用过这些工具 → 字典里没这一项 → 直接返回，
    不会为了「万一」去 import Playwright。返回值供永久删除做资源关闭审计；
    其他调用方可以继续忽略它。
    """
    rt = _RUNTIMES.pop(project_id, None)
    if rt is None:
        return []
    return await rt.aclose(immediate=immediate)


async def shutdown_all_runtimes(*, immediate: bool = True) -> None:
    """整个进程收摊（测试、以及 server 的总关机路径可用）。"""
    ids = list(_RUNTIMES)
    for project_id in ids:
        await shutdown_project_runtime(project_id, immediate=immediate)


def _atexit_sweep() -> None:
    """
    ③ 号口子：Python 要退了。

    正常关机走不到这儿（engine.stop 已经清干净了）。走到这儿说明是崩溃、
    Ctrl-C 或者哪里漏了 stop —— 这时候用户的 `npm run dev` 还占着 3000 端口，
    而他并不知道那是谁起的。给它补一刀，比让他自己去翻任务管理器强。
    """
    for registry in list(_ATTEMPT_PROCESS_REGISTRIES):
        registry.emergency_close()
    _ATTEMPT_PROCESS_REGISTRIES.clear()
    for rt in list(_RUNTIMES.values()):
        rt.emergency_close()
    _RUNTIMES.clear()


atexit.register(_atexit_sweep)


def clip(text: str, limit: int, *, note: str = "已截断") -> tuple[str, bool]:
    """
    尾部截断。返回 (文本, 是否截断过)。

    截断标记是**中文人话**，因为它最终会被模型读到 —— 它得知道
    「后面还有，不是没有了」，否则它会拿半截输出当全部去下结论。
    """
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n…（{note}，共 {len(text)} 字）", True


def clip_middle(text: str, limit: int, *, head_ratio: float = 0.35) -> tuple[str, bool]:
    """
    **掐头去尾式**截断：留头 + 留尾，挖掉中间。

    为什么不像 clip 那样只留头：构建日志的信息全在**两头**——
    头上是「在装什么」，尾巴上是「为什么挂了」。只留头 = 把报错扔了，
    只留尾 = 不知道它在干嘛。中间那几万行 `Downloading...` 谁也不想看。
    """
    if len(text) <= limit:
        return text, False
    head_n = max(0, int(limit * head_ratio))
    tail_n = max(0, limit - head_n)
    dropped = len(text) - head_n - tail_n
    return (
        text[:head_n]
        + f"\n\n…（中间省略 {dropped} 字；需要完整内容请缩小命令范围或重定向到文件）…\n\n"
        + text[len(text) - tail_n:]
    ), True


def run_blocking(func: Callable[..., T], /, *args: Any) -> "asyncio.Future[T]":
    """
    把阻塞调用挪出事件循环。

    ★ 这条规矩对 Batch 4 是**硬的**：一个进程里跑着所有项目的引擎，
      在 handler 里直接 `subprocess.run` 或 `DDGS().text()` 阻塞 5 秒，
      **另外三个项目的用户会同时看到界面卡死 5 秒**，而他们什么都没干。
    """
    return asyncio.ensure_future(asyncio.to_thread(func, *args))


__all__ = [
    "AttemptProcessRegistry",
    "Closable",
    "ProjectRuntime",
    "ToolError",
    "clip",
    "clip_middle",
    "run_blocking",
    "runtime_for",
    "shutdown_all_runtimes",
    "shutdown_project_runtime",
]
