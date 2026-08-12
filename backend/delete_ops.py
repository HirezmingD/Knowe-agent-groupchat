"""Small crash-recoverable filesystem primitives for permanent deletion.

Correct resource closure is the primary mechanism.  The pre-commit path therefore performs one
project-root rename with a short bounded retry; it never recursively evacuates a tree, copies files
to bypass a lock, invokes GC, or waits for guessed quiescence.  A final Windows Restart Manager
probe is diagnostic only.
"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger("knowe.delete_ops")

# Windows may keep a just-closed SQLite WAL/SHM mapping briefly.  Keep the total
# retry sleep below two seconds so Engine close + staging still fits the 8-second
# pre-commit deadline while absorbing that transient release window.
DELETE_RENAME_DELAYS: tuple[float, ...] = (0.0, 0.05, 0.15, 0.30, 0.50, 0.75)
_MAX_RM_RESOURCES = 128


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_lock_like(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and (
        getattr(exc, "winerror", None) in {5, 32, 33, 1224}
        or exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY}
    )


def _short_error(exc: BaseException, limit: int = 260) -> str:
    text = " ".join(str(exc or "").split()) or exc.__class__.__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def is_link_like(path: Path) -> bool:
    """Return True for a symlink or a Windows reparse point without following it."""

    value = Path(path)
    try:
        if value.is_symlink():
            return True
        return bool(getattr(value.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


@dataclass(frozen=True)
class LockingProcess:
    pid: int
    name: str
    executable: str | None = None
    restartable: bool | None = None
    current_process: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "executable": self.executable,
            "restartable": self.restartable,
            "current_process": self.current_process,
        }


@dataclass(frozen=True)
class DeleteStageResult:
    original: str
    staged: str
    method: str
    empty_source_shell_left: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "staged": self.staged,
            "method": self.method,
            "empty_source_shell_left": self.empty_source_shell_left,
        }


class DeletePathBusyError(PermissionError):
    """The root rename or staged purge was blocked after the bounded retry."""

    def __init__(
        self,
        path: Path,
        operation: str,
        cause: BaseException,
        *,
        attempts: Sequence[str] = (),
        locking_processes: Sequence[LockingProcess] = (),
        resource_close_issues: Sequence[str] = (),
    ) -> None:
        self.path = Path(path)
        self.operation = operation
        self.cause = cause
        self.attempts = tuple(attempts)
        self.locking_processes = tuple(locking_processes)
        self.resource_close_issues = tuple(resource_close_issues)
        super().__init__(self._message())

    def _message(self) -> str:
        parts = [f"无法{self.operation}：{self.path}"]
        if self.locking_processes:
            labels = []
            for process in self.locking_processes[:8]:
                suffix = "，Knowe 本进程" if process.current_process else ""
                labels.append(f"{process.name}（PID {process.pid}{suffix}）")
            parts.append("占用进程：" + "、".join(labels))
        if self.resource_close_issues:
            parts.append("Knowe 资源关闭报告：" + "；".join(self.resource_close_issues[:6]))
        parts.append("底层错误：" + _short_error(self.cause))
        return "；".join(parts)


def _process_executable_windows(pid: int) -> str | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = open_process(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            return buffer.value if query(handle, 0, buffer, ctypes.byref(size)) else None
        finally:
            close(handle)
    except Exception:
        return None


def _restart_manager_resources(paths: Iterable[Path]) -> list[str]:
    resources: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(os.path.abspath(os.fspath(path)))
        if key not in seen and len(resources) < _MAX_RM_RESOURCES:
            seen.add(key)
            resources.append(os.path.abspath(os.fspath(path)))

    for root in map(Path, paths):
        add(root)
        if len(resources) >= _MAX_RM_RESOURCES or is_link_like(root) or not root.is_dir():
            continue
        try:
            for directory, dirnames, filenames in os.walk(root, followlinks=False):
                here = Path(directory)
                dirnames[:] = sorted(name for name in dirnames if not is_link_like(here / name))
                add(here)
                for name in sorted(filenames):
                    add(here / name)
                    if len(resources) >= _MAX_RM_RESOURCES:
                        break
                if len(resources) >= _MAX_RM_RESOURCES:
                    break
        except OSError:
            continue
    return resources


def find_locking_processes(paths: Iterable[Path]) -> list[LockingProcess]:
    """Return a single bounded Windows Restart Manager diagnosis."""

    if not _is_windows():
        return []
    resources = _restart_manager_resources(paths)
    if not resources:
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ("Process", RM_UNIQUE_PROCESS),
                ("strAppName", wintypes.WCHAR * 256),
                ("strServiceShortName", wintypes.WCHAR * 64),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL),
            ]

        rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)
        start = rm.RmStartSession
        register = rm.RmRegisterResources
        get_list = rm.RmGetList
        end = rm.RmEndSession
        start.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
        register.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR)]
        get_list.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT), ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD)]
        end.argtypes = [wintypes.DWORD]
        session = wintypes.DWORD(0)
        key = ctypes.create_unicode_buffer(33)
        if start(ctypes.byref(session), 0, key) != 0:
            return []
        try:
            registered = 0
            for resource in resources:
                one = (wintypes.LPCWSTR * 1)(resource)
                if register(session.value, 1, one, 0, None, 0, None) == 0:
                    registered += 1
            if not registered:
                return []
            needed = wintypes.UINT(0)
            count = wintypes.UINT(0)
            reasons = wintypes.DWORD(0)
            result = get_list(session.value, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
            if result == 0 and needed.value == 0:
                return []
            if result != 234 or needed.value == 0:  # ERROR_MORE_DATA
                return []
            buffer = (RM_PROCESS_INFO * int(needed.value))()
            count = wintypes.UINT(int(needed.value))
            if get_list(session.value, ctypes.byref(needed), ctypes.byref(count), buffer, ctypes.byref(reasons)) != 0:
                return []
            found: dict[int, LockingProcess] = {}
            for index in range(count.value):
                row = buffer[index]
                pid = int(row.Process.dwProcessId)
                executable = _process_executable_windows(pid)
                name = str(row.strAppName or row.strServiceShortName or "").strip()
                found[pid] = LockingProcess(
                    pid=pid,
                    name=name or (Path(executable).name if executable else "未知进程"),
                    executable=executable,
                    restartable=bool(row.bRestartable),
                    current_process=pid == os.getpid(),
                )
            return sorted(found.values(), key=lambda item: (not item.current_process, item.name.casefold(), item.pid))
        finally:
            end(session.value)
    except Exception:
        log.debug("Windows Restart Manager 锁定诊断不可用", exc_info=True)
        return []


def _rename_with_retry(original: Path, staged: Path) -> None:
    last: OSError | None = None
    for index, delay in enumerate(DELETE_RENAME_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            os.rename(original, staged)
            return
        except OSError as exc:
            last = exc
            if not _is_lock_like(exc) or index == len(DELETE_RENAME_DELAYS) - 1:
                raise
    if last is not None:  # pragma: no cover
        raise last


def stage_project_root(original: Path, staged: Path) -> None:
    """Atomically detach exactly one project root with a short bounded retry budget."""

    source = Path(original)
    target = Path(staged)
    if source == target or source.parent.resolve(strict=False) != target.parent.resolve(strict=False):
        raise ValueError("删除暂存目标必须是项目根的同级路径")
    if _lexists(target):
        if not _lexists(source):
            return
        raise FileExistsError(f"原目录和删除暂存目录同时存在：{source}；{target}")
    if not _lexists(source):
        return
    _rename_with_retry(source, target)


def stage_delete_path(
    original: Path,
    staged: Path,
    *,
    resource_close_issues: Sequence[str] = (),
) -> DeleteStageResult:
    """Compatibility wrapper around the one-root staging primitive."""

    source = Path(original)
    target = Path(staged)
    if _lexists(target) and not _lexists(source):
        return DeleteStageResult(str(source), str(target), "already-staged")
    if not _lexists(source):
        return DeleteStageResult(str(source), str(target), "absent")
    try:
        stage_project_root(source, target)
        return DeleteStageResult(str(source), str(target), "rename")
    except OSError as exc:
        if not _is_lock_like(exc):
            raise
        raise DeletePathBusyError(
            source,
            "暂存项目根目录",
            exc,
            attempts=("短时有界原子改名",),
            locking_processes=find_locking_processes((source,)),
            resource_close_issues=resource_close_issues,
        ) from exc


def restore_staged_path(original: Path, staged: Path) -> None:
    """Restore an uncommitted root; never merge two trees."""

    source = Path(original)
    target = Path(staged)
    if not _lexists(target):
        return
    if _lexists(source):
        raise FileExistsError(f"回滚冲突：原目录与暂存目录同时存在：{source}；{target}")
    _rename_with_retry(target, source)


def purge_staged_path(original: Path, staged: Path) -> None:
    """Physically remove only the staged root after logical commit."""

    source = Path(original)
    target = Path(staged)
    if _lexists(source):
        raise OSError(f"提交后原项目路径重新出现，拒绝误删：{source}")
    if not _lexists(target):
        return
    try:
        if is_link_like(target) or not target.is_dir():
            target.unlink()
        else:
            shutil.rmtree(target)
    except OSError as exc:
        if not _is_lock_like(exc):
            raise
        raise DeletePathBusyError(
            target,
            "物理清除删除暂存目录",
            exc,
            attempts=("递归删除暂存根",),
            locking_processes=find_locking_processes((target,)),
        ) from exc


__all__ = [
    "DELETE_RENAME_DELAYS",
    "DeletePathBusyError",
    "DeleteStageResult",
    "LockingProcess",
    "find_locking_processes",
    "is_link_like",
    "purge_staged_path",
    "restore_staged_path",
    "stage_delete_path",
    "stage_project_root",
]
