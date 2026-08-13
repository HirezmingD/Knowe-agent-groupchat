"""Fail-closed Microsoft Execution Containers (MXC) command runner.

Every model-controlled process must be launched through :func:`spawn`.  The
module deliberately has no host-shell fallback: if Windows, the MXC binary, or
the native isolation probe is unavailable, command execution is unavailable.

Packaging contract
------------------
``wxc-exec.exe`` must be shipped outside an ``.asar`` at one of these locations:

* ``%KNOWE_MXC_EXECUTABLE%`` (absolute path; deployment/test override),
* ``<resources>\\sandbox\\wxc-exec.exe`` via that environment variable
  (packaged Electron contract), or
* ``<repository>\\node_modules\\@microsoft\\mxc-sdk\\bin\\<arch>\\wxc-exec.exe``
  (development fallback).

The binary is invoked directly with ``--config-base64``.  No command text is
passed to a host shell by this module; the shell runs *inside* AppContainer.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


MXC_POLICY_VERSION = "0.7.0-alpha"
_MIN_WINDOWS_BUILD = 26100
_VALID_TIERS = {"base-container", "appcontainer-bfs", "appcontainer-dacl"}
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_WORKSPACE_FILESYSTEMS = {"NTFS", "REFS"}
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_EXTRA_ENV_ALLOWLIST = {
    "KNOWE_PROJECT_ID",
    "KNOWE_TASK_ID",
    "KNOWE_ATTEMPT_ID",
    "PYTHONPATH",
}

# Runtime directories are intentionally project-local so the AppContainer never
# needs a writable profile outside the workspace.  Remember exactly which
# directory objects this backend created: cleanup may remove those objects when
# empty, but must never remove a pre-existing user directory with the same name.
_RUNTIME_LOCK = threading.Lock()
_ACTIVE_RUNTIME_ROOTS: dict[Path, int] = {}
_OWNED_RUNTIME_DIRS: dict[Path, tuple[int, int]] = {}
_CACHED_SUPPORT: SandboxSupport | None = None


class SandboxUnavailable(RuntimeError):
    """The required native sandbox cannot safely run on this host."""


@dataclass(frozen=True)
class SandboxSupport:
    executable: Path | None
    available: bool
    reason: str
    isolation_tier: str = ""
    launcher: Path | None = None


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_workspace(value: str | Path) -> Path:
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxUnavailable(f"project workspace is unavailable: {exc}") from None
    if not root.is_dir() or root == root.parent:
        raise SandboxUnavailable("project workspace must be an existing non-root directory")
    return root


def _lexical_absolute(value: str | Path) -> Path:
    """Return an absolute path without following a junction or symlink."""

    expanded = Path(value).expanduser()
    return Path(os.path.abspath(os.fspath(expanded)))


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise SandboxUnavailable(f"cannot inspect sandbox path {path}: {exc}") from None
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _directory_identity(path: Path) -> tuple[int, int]:
    info = os.lstat(path)
    return int(info.st_dev), int(info.st_ino)


def _ensure_owned_directory(path: Path, root: Path) -> None:
    """Create *path* and record only directory objects created by this process."""

    chain: list[Path] = []
    cursor = path
    while cursor != root:
        if not _is_under(cursor, root):
            raise SandboxUnavailable("sandbox runtime directory escaped the project")
        chain.append(cursor)
        cursor = cursor.parent
    for candidate in reversed(chain):
        try:
            candidate.mkdir()
        except FileExistsError:
            if not candidate.is_dir() or _is_reparse_point(candidate):
                raise SandboxUnavailable(
                    f"sandbox runtime path is not a plain directory: {candidate}"
                ) from None
        except OSError as exc:
            raise SandboxUnavailable(
                f"cannot create sandbox runtime directory {candidate}: {exc}"
            ) from None
        else:
            identity = _directory_identity(candidate)
            with _RUNTIME_LOCK:
                _OWNED_RUNTIME_DIRS[candidate] = identity


def _reserve_runtime(root: Path) -> None:
    with _RUNTIME_LOCK:
        _ACTIVE_RUNTIME_ROOTS[root] = _ACTIVE_RUNTIME_ROOTS.get(root, 0) + 1


def _release_runtime(root: Path) -> None:
    with _RUNTIME_LOCK:
        remaining = _ACTIVE_RUNTIME_ROOTS.get(root, 0) - 1
        if remaining > 0:
            _ACTIVE_RUNTIME_ROOTS[root] = remaining
        else:
            _ACTIVE_RUNTIME_ROOTS.pop(root, None)
    cleanup_empty_runtime_dirs(root)


def cleanup_empty_runtime_dirs(workspace_root: str | Path) -> None:
    """Remove empty sandbox-owned directories when no command still uses them.

    ``rmdir`` is deliberate: non-empty directories are preserved.  The recorded
    file identity also prevents removing a user replacement after a directory
    was renamed or recreated during command execution.
    """

    root = _resolve_workspace(workspace_root)
    with _RUNTIME_LOCK:
        if _ACTIVE_RUNTIME_ROOTS.get(root, 0):
            return
        owned_paths = sorted(
            (path for path in _OWNED_RUNTIME_DIRS if _is_under(path, root)),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in owned_paths:
            expected = _OWNED_RUNTIME_DIRS[path]
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                _OWNED_RUNTIME_DIRS.pop(path, None)
                continue
            except OSError:
                continue
            current = (int(info.st_dev), int(info.st_ino))
            is_reparse = bool(
                getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
            )
            if current != expected or is_reparse:
                _OWNED_RUNTIME_DIRS.pop(path, None)
                continue
            try:
                path.rmdir()
            except OSError:
                # Non-empty (including user-created content) or temporarily in use.
                continue
            _OWNED_RUNTIME_DIRS.pop(path, None)


def create_execution_directory(workspace_root: str | Path, name: str) -> Path:
    """Create one owned, unique directory for an ``execute_code`` script."""

    root = validate_workspace_security(workspace_root)
    if not re.fullmatch(r"[0-9a-f]{20}", str(name)):
        raise SandboxUnavailable("invalid sandbox execution directory name")
    target = root / ".knowe-sandbox" / "execute" / str(name)
    if target.exists():
        raise SandboxUnavailable("sandbox execution directory already exists")
    _ensure_owned_directory(target, root)
    return target


def _volume_filesystem(path: Path) -> str:
    """Return the Windows volume filesystem name without spawning host tools."""

    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_volume_path = kernel32.GetVolumePathNameW
    get_volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_volume_path.restype = wintypes.BOOL
    get_volume_info = kernel32.GetVolumeInformationW
    get_volume_info.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_volume_info.restype = wintypes.BOOL
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [wintypes.LPCWSTR]
    get_drive_type.restype = wintypes.UINT

    volume_path = ctypes.create_unicode_buffer(32768)
    if not get_volume_path(str(path), volume_path, len(volume_path)):
        error = ctypes.get_last_error()
        raise SandboxUnavailable(f"cannot resolve sandbox volume (WinError {error})")
    # DRIVE_FIXED == 3.  A mapped drive can report NTFS while still escaping to
    # a remote machine, so filesystem-name validation alone is insufficient.
    if get_drive_type(volume_path.value) != 3:
        raise SandboxUnavailable("sandbox workspace must be on a fixed local volume")
    filesystem = ctypes.create_unicode_buffer(64)
    serial = wintypes.DWORD()
    maximum_component = wintypes.DWORD()
    flags = wintypes.DWORD()
    if not get_volume_info(
        volume_path.value,
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(maximum_component),
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    ):
        error = ctypes.get_last_error()
        raise SandboxUnavailable(f"cannot inspect sandbox filesystem (WinError {error})")
    return filesystem.value.upper()


def validate_workspace_security(value: str | Path) -> Path:
    """Reject aliases that can make an AppContainer+DACL grant escape its root.

    Tier-3 MXC temporarily grants an AppContainer SID access to policy paths.
    A junction, symlink, mount point, or hardlink in the project could otherwise
    alias an object outside the project while that grant is being applied.
    Validation therefore fails closed before every launch.
    """

    lexical = _lexical_absolute(value)
    if os.name != "nt":
        return _resolve_workspace(lexical)

    raw = str(lexical)
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\") or normalized.lower().startswith("\\\\?\\unc\\"):
        raise SandboxUnavailable("sandbox workspace must be on a local Windows volume, not UNC")
    if not lexical.drive or not lexical.is_absolute():
        raise SandboxUnavailable("sandbox workspace must be an absolute local drive path")
    if lexical == Path(lexical.anchor):
        raise SandboxUnavailable("sandbox workspace cannot be a volume root")
    filesystem = _volume_filesystem(lexical)
    if filesystem not in _ALLOWED_WORKSPACE_FILESYSTEMS:
        raise SandboxUnavailable(
            f"sandbox workspace requires NTFS or ReFS (found {filesystem or 'unknown'})"
        )

    # Inspect the lexical chain before calling resolve(); resolve() would hide
    # the very junction/symlink we need to reject.
    current = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        current /= component
        if _is_reparse_point(current):
            raise SandboxUnavailable(f"sandbox workspace path contains a reparse point: {current}")

    root = _resolve_workspace(lexical)
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    item = Path(entry.path)
                    try:
                        # DirEntry.stat() on Windows currently reports zero for
                        # st_nlink/st_ino.  os.lstat() asks the filesystem for
                        # link metadata and is required for the hardlink gate.
                        info = os.lstat(item)
                    except OSError as exc:
                        raise SandboxUnavailable(
                            f"cannot inspect sandbox workspace entry {item}: {exc}"
                        ) from None
                    if getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
                        raise SandboxUnavailable(
                            f"sandbox workspace contains a reparse point: {item}"
                        )
                    if info.st_nlink > 1:
                        raise SandboxUnavailable(
                            f"sandbox workspace contains a hardlinked entry: {item}"
                        )
                    if stat.S_ISDIR(info.st_mode):
                        stack.append(item)
        except SandboxUnavailable:
            raise
        except OSError as exc:
            raise SandboxUnavailable(f"cannot scan sandbox workspace {directory}: {exc}") from None
    return root


def _arch_dir() -> str:
    return "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"


def _candidate_executables() -> tuple[Path, ...]:
    candidates: list[Path] = []
    override = os.environ.get("KNOWE_MXC_EXECUTABLE", "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    arch = _arch_dir()
    if getattr(sys, "frozen", False):
        backend_dir = Path(sys.executable).resolve().parent
        # Compatibility-only discovery.  Production Electron must inject the
        # resources/sandbox absolute path so backend layout changes cannot
        # silently select an unintended binary.
        candidates.append(backend_dir.parent / "sandbox" / "wxc-exec.exe")

    repo_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        (
            repo_root / "node_modules" / "@microsoft" / "mxc-sdk" / "bin" / arch / "wxc-exec.exe",
        )
    )
    result: list[Path] = []
    for item in candidates:
        try:
            resolved = item.resolve(strict=False)
        except OSError:
            continue
        if resolved not in result:
            result.append(resolved)
    return tuple(result)


def find_mxc_executable() -> Path | None:
    for candidate in _candidate_executables():
        try:
            if candidate.is_file() and ".asar" not in str(candidate).lower():
                return candidate
        except OSError:
            continue
    return None


def _candidate_launchers() -> tuple[Path, ...]:
    candidates: list[Path] = []
    override = os.environ.get("KNOWE_SANDBOX_LAUNCHER", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    if getattr(sys, "frozen", False):
        backend_dir = Path(sys.executable).resolve().parent
        candidates.append(backend_dir.parent / "sandbox" / "knowe-sandbox-launcher.exe")
    repo_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        (
            repo_root / "build" / "native" / "knowe-sandbox-launcher.exe",
            repo_root
            / "native"
            / "knowe-sandbox-launcher"
            / "target"
            / "release"
            / "knowe-sandbox-launcher.exe",
        )
    )
    result: list[Path] = []
    for item in candidates:
        try:
            resolved = item.resolve(strict=False)
        except OSError:
            continue
        if resolved not in result:
            result.append(resolved)
    return tuple(result)


def find_sandbox_launcher() -> Path | None:
    for candidate in _candidate_launchers():
        try:
            if candidate.is_file() and ".asar" not in str(candidate).lower():
                return candidate
        except OSError:
            continue
    return None


def _windows_build() -> int:
    try:
        return int(platform.version().split(".")[-1])
    except (TypeError, ValueError):
        return 0


def probe(*, executable: Path | None = None, timeout_s: float = 30.0) -> SandboxSupport:
    """Probe the native runner.  Malformed/partial probe output fails closed."""

    if os.name != "nt":
        return SandboxSupport(None, False, "MXC terminal sandbox requires Windows 11")
    build = _windows_build()
    if build < _MIN_WINDOWS_BUILD:
        return SandboxSupport(
            None,
            False,
            f"MXC terminal sandbox requires Windows 11 build {_MIN_WINDOWS_BUILD}+ (found {build or 'unknown'})",
        )
    binary = executable or find_mxc_executable()
    if binary is None:
        return SandboxSupport(None, False, "wxc-exec.exe is missing from the packaged MXC runtime")
    launcher = find_sandbox_launcher()
    if launcher is None:
        return SandboxSupport(
            binary,
            False,
            "knowe-sandbox-launcher.exe is missing from the packaged sandbox runtime",
        )
    try:
        completed = subprocess.run(
            [str(binary), "--probe"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=max(0.1, timeout_s),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return SandboxSupport(binary, False, f"MXC probe failed: {exc}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode("utf-8", "replace").strip()
        return SandboxSupport(binary, False, f"MXC probe exited {completed.returncode}: {detail[:400]}")
    recovery_detail = completed.stderr.decode("utf-8", "replace").strip()
    recovery_error_count = re.search(r"DACL recovery:.*?([0-9]+) error\(s\)", recovery_detail)
    if "DACL recovery failed:" in recovery_detail or (
        recovery_error_count is not None and int(recovery_error_count.group(1)) > 0
    ):
        return SandboxSupport(
            binary,
            False,
            f"MXC could not restore a prior sandbox policy: {recovery_detail[:400]}",
            launcher=launcher,
        )
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SandboxSupport(binary, False, "MXC probe returned malformed JSON")
    tier = payload.get("tier") if isinstance(payload, dict) else None
    if tier not in _VALID_TIERS:
        return SandboxSupport(binary, False, "MXC probe did not report an enforceable isolation tier")
    return SandboxSupport(binary, True, "", str(tier), launcher)


def require_support() -> SandboxSupport:
    global _CACHED_SUPPORT
    with _RUNTIME_LOCK:
        active = any(_ACTIVE_RUNTIME_ROOTS.values())
        cached = _CACHED_SUPPORT
    if active:
        if (
            cached is not None
            and cached.available
            and cached.executable is not None
            and cached.executable.is_file()
            and cached.launcher is not None
            and cached.launcher.is_file()
        ):
            # wxc --probe performs global crash recovery; do not let one live
            # sandbox's DACL journal be mistaken for abandoned state.
            return cached
        raise SandboxUnavailable("cannot probe MXC while another sandbox is active")
    result = probe()
    if not result.available or result.executable is None or result.launcher is None:
        raise SandboxUnavailable(result.reason or "MXC terminal sandbox is unavailable")
    with _RUNTIME_LOCK:
        _CACHED_SUPPORT = result
    return result


def minimal_environment(
    workspace_root: str | Path,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an allowlisted child environment; parent secrets are never copied."""

    root = _resolve_workspace(workspace_root)
    sandbox_home = root / ".knowe" / "sandbox-home"
    sandbox_temp = root / ".knowe" / "sandbox-temp"
    _ensure_owned_directory(sandbox_home, root)
    _ensure_owned_directory(sandbox_temp, root)

    environment: dict[str, str] = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", os.environ.get("SystemRoot", r"C:\Windows")),
        "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": str(sandbox_home),
        "USERPROFILE": str(sandbox_home),
        "TEMP": str(sandbox_temp),
        "TMP": str(sandbox_temp),
        "KNOWE_WORKSPACE_ROOT": str(root),
    }
    # PATH is retained only as a lookup list.  Read permission is granted
    # separately and narrowly by ``readonly_tool_paths``.
    environment["PATH"] = os.pathsep.join(_sanitized_path_entries(root))
    for key, value in (extra or {}).items():
        if not _ENV_KEY.fullmatch(str(key)):
            raise ValueError(f"invalid sandbox environment key: {key!r}")
        if str(key) not in _EXTRA_ENV_ALLOWLIST:
            raise ValueError(f"sandbox environment key is not allowlisted: {key}")
        if any(character in str(value) for character in ('\x00', '\r', '\n', '"')):
            raise ValueError(f"sandbox environment value cannot be represented safely: {key}")
        if str(key) == "PYTHONPATH":
            for entry in str(value).split(os.pathsep):
                if not entry:
                    continue
                try:
                    resolved_entry = Path(entry).expanduser().resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ValueError(f"PYTHONPATH entry is unavailable: {entry}: {exc}") from None
                if not _is_under(resolved_entry, root):
                    raise ValueError("PYTHONPATH entries must stay inside the project")
        environment[str(key)] = str(value)
    return environment


def _sanitized_path_entries(workspace: Path) -> tuple[str, ...]:
    allowed: list[str] = [str(workspace)]
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for path in (system_root / "System32", system_root):
        if path.is_dir():
            allowed.append(str(path.resolve()))

    # Permit executable lookup from PATH, but only expose the directory that
    # actually contains an executable.  Home/profile roots are never granted.
    for command in ("git.exe", "node.exe", "npm.cmd", "python.exe", "py.exe"):
        located = shutil.which(command)
        if not located:
            continue
        try:
            parent = Path(located).resolve(strict=True).parent
        except OSError:
            continue
        if _is_under(parent, Path.home().resolve()):
            # Per-user tool installs are useful, but granting the tool's exact
            # directory is safe; never grant a parent such as the user profile.
            pass
        text = str(parent)
        if text not in allowed:
            allowed.append(text)
    return tuple(allowed)


def readonly_tool_paths(workspace_root: str | Path, executable: str | Path | None = None) -> tuple[str, ...]:
    root = _resolve_workspace(workspace_root)
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(strict=False)
    # Windows grants AppContainers the OS runtime they need.  Re-granting
    # C:\Windows through Tier-3 DACL mutation is both unnecessary and very slow.
    paths = [
        entry
        for entry in _sanitized_path_entries(root)[1:]
        if not _is_under(Path(entry).resolve(strict=False), system_root)
    ]
    if executable:
        try:
            tool_dir = Path(executable).expanduser().resolve(strict=True).parent
        except (OSError, RuntimeError) as exc:
            raise SandboxUnavailable(f"sandbox tool is unavailable: {exc}") from None
        text = str(tool_dir)
        if text not in paths and not _is_under(tool_dir, root):
            paths.append(text)
        # A standalone Python installation needs its standard library and DLL
        # tree, not merely the directory containing python.exe.  Grant the
        # interpreter prefix only when that prefix is outside the project.
        try:
            resolved_executable = Path(executable).expanduser().resolve(strict=True)
            current_executable = Path(sys.executable).resolve(strict=True)
        except (OSError, RuntimeError):
            resolved_executable = current_executable = Path()
        if resolved_executable == current_executable:
            for prefix in {Path(sys.prefix), Path(getattr(sys, "base_prefix", sys.prefix))}:
                try:
                    resolved_prefix = prefix.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                prefix_text = str(resolved_prefix)
                if not _is_under(resolved_prefix, root) and prefix_text not in paths:
                    paths.append(prefix_text)
    return tuple(paths)


def _command_line(command_file: Path) -> str:
    comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    # The model command lives in a project-local .cmd file so arbitrary nested
    # quotes never have to survive another CreateProcess command-line parser.
    return f'"{comspec}" /d /s /c call "{command_file}"'


def _batch_environment_line(key: str, value: str) -> str:
    """Encode one validated environment assignment for a UTF-8 .cmd file."""

    if not _ENV_KEY.fullmatch(key):
        raise SandboxUnavailable(f"invalid sandbox environment key: {key!r}")
    if any(character in value for character in ('\x00', '\r', '\n', '"')):
        raise SandboxUnavailable(f"sandbox environment value cannot be represented safely: {key}")
    # Percent expansion happens while cmd parses a batch file, even with
    # delayed expansion disabled.  Doubling preserves the literal value.
    return f'set "{key}={value.replace("%", "%%")}"'


def _batch_script(command: str, environment: Mapping[str, str]) -> str:
    lines = [
        "@echo off",
        "@chcp 65001 >nul",
        "@setlocal EnableExtensions DisableDelayedExpansion",
        # MXC's AppContainer path normally creates a clean environment.  Clear
        # it anyway so a future backend change cannot silently reintroduce host
        # inheritance.  WXC 0.7.0 on the DACL tier cannot accept process.env
        # (CreateProcessW 0x800700CB), so rebuilding in the child is required.
        "@for /f \"delims==\" %%K in ('set') do @set \"%%K=\"",
    ]
    lines.extend(_batch_environment_line(key, value) for key, value in environment.items())
    lines.append(str(command))
    return "\r\n".join(lines) + "\r\n"


def build_config(
    command: str,
    *,
    workspace_root: str | Path,
    cwd: str | Path | None = None,
    timeout_s: float = 0,
    env: Mapping[str, str] | None = None,
    executable: str | Path | None = None,
    command_file: str | Path | None = None,
) -> dict[str, object]:
    root = validate_workspace_security(workspace_root)
    if not isinstance(command, str) or not command.strip():
        raise SandboxUnavailable("sandbox command must be non-empty")
    workdir = Path(cwd or root).expanduser().resolve(strict=True)
    if not workdir.is_dir() or not _is_under(workdir, root):
        raise SandboxUnavailable("sandbox cwd must be an existing project directory")
    # Validate the allowlist here, but do not put it in process.env.  MXC 0.7.0
    # AppContainer+DACL fails CreateProcessW with 0x800700CB when that array is
    # non-empty.  spawn() writes the same allowlist into the inner .cmd preamble.
    minimal_environment(root, env)
    if command_file is None:
        command_path = root / ".knowe-sandbox" / "commands" / (uuid.uuid4().hex + ".cmd")
    else:
        command_path = Path(command_file).expanduser().resolve(strict=False)
    if not _is_under(command_path, root):
        raise SandboxUnavailable("sandbox command file must stay inside the project")
    container_id = "knowe-" + uuid.uuid4().hex
    return {
        "version": MXC_POLICY_VERSION,
        "containment": "process",
        "containerId": container_id,
        "lifecycle": {"destroyOnExit": True, "preservePolicy": False},
        "process": {
            "commandLine": _command_line(command_path),
            "cwd": str(workdir),
            "timeout": max(0, int(float(timeout_s) * 1000)),
        },
        "filesystem": {
            "readwritePaths": [str(root)],
            "readonlyPaths": list(readonly_tool_paths(root, executable)),
            "deniedPaths": [],
        },
        "network": {
            # Blocking requires only AppContainer capabilities; firewall mode would
            # demand elevation and is unnecessary because no host allowlist exists.
            "enforcementMode": "capabilities",
            "defaultPolicy": "block",
            "allowLocalNetwork": False,
            "allowedHosts": [],
            "blockedHosts": [],
        },
        "fallback": {"allowDaclMutation": True},
        "processContainer": {
            # The 0.7.0 SDK emits this backend name even though the stable
            # schema's prose omits it; keep it identical to containerId so the
            # native profile and policy-cleanup identity cannot diverge.
            "name": container_id,
            # MXC names its LPAC opt-out switch ``leastPrivilege``.  ``true``
            # removes the ALL APPLICATION PACKAGES compatibility grants and
            # makes the shipping Windows toolchain unable to start/read its
            # runtime on the AppContainer+DACL tier.  ``false`` is MXC 0.7's
            # documented default and still creates an AppContainer Low-IL
            # token; our token integration test locks that security boundary.
            "leastPrivilege": False,
            "capabilities": [],
            "ui": {
                "isolation": "container",
                "desktopSystemControl": False,
                "systemSettings": "none",
                "ime": False,
            },
        },
        # Do not apply the blanket Win32k syscall mitigation: ordinary console
        # tools such as ``cmd.exe /c dir`` and ``whoami /all`` use Win32k while
        # formatting output and otherwise fail with ERROR_ACCESS_DENIED.  The
        # granular Job UI limits above still block external UI objects, global
        # atoms, desktop switching/logoff, system settings and IME changes;
        # these top-level fields separately block clipboard and input injection.
        "ui": {"disable": False, "clipboard": "none", "injection": False},
    }


async def spawn(
    command: str,
    *,
    workspace_root: str | Path,
    cwd: str | Path | None = None,
    timeout_s: float = 0,
    env: Mapping[str, str] | None = None,
    executable: str | Path | None = None,
) -> asyncio.subprocess.Process:
    """Launch one command through MXC.  There is intentionally no fallback."""

    support = require_support()
    root = validate_workspace_security(workspace_root)
    _reserve_runtime(root)
    lexical_root = _lexical_absolute(root)
    command_dir = lexical_root / ".knowe-sandbox" / "commands"
    command_file = command_dir / (uuid.uuid4().hex + ".cmd")
    try:
        config = build_config(
            command,
            workspace_root=workspace_root,
            cwd=cwd,
            timeout_s=timeout_s,
            env=env,
            executable=executable,
            command_file=command_file,
        )
        child_environment = minimal_environment(root, env)
        _ensure_owned_directory(command_dir, root)
        command_file.write_text(
            _batch_script(command, child_environment),
            encoding="utf-8",
            newline="",
        )
    except Exception:
        command_file.unlink(missing_ok=True)
        _release_runtime(root)
        raise
    encoded = base64.b64encode(
        json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    try:
        process = await asyncio.create_subprocess_exec(
            str(support.launcher),
            "--parent-pid",
            str(os.getpid()),
            "--timeout-ms",
            str(max(0, int(float(timeout_s) * 1000))),
            "--",
            str(support.executable),
            "--config-base64",
            encoded,
            cwd=str(root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_runner_environment(root),
            creationflags=flags,
        )
    except (OSError, ValueError) as exc:
        command_file.unlink(missing_ok=True)
        _release_runtime(root)
        raise SandboxUnavailable(f"MXC sandbox failed to start: {exc}") from None
    cleanup_task = asyncio.create_task(_cleanup_command_file(process, command_file, root))
    setattr(process, "_knowe_cleanup_task", cleanup_task)
    return process


def _runner_environment(workspace_root: Path) -> dict[str, str]:
    """Environment for trusted wxc-exec itself (not inherited by its child)."""

    result = minimal_environment(workspace_root)
    for key in ("LOCALAPPDATA", "PROGRAMDATA"):
        value = os.environ.get(key)
        if value:
            result[key] = value
    return result


async def _cleanup_command_file(
    process: asyncio.subprocess.Process,
    path: Path,
    root: Path,
) -> None:
    try:
        await process.wait()
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        _release_runtime(root)


async def wait_for_cleanup(process: asyncio.subprocess.Process) -> None:
    """Wait until this command released its runtime-directory reservation."""

    task = getattr(process, "_knowe_cleanup_task", None)
    if isinstance(task, asyncio.Task) and task is not asyncio.current_task():
        await asyncio.shield(task)


def terminate(process: asyncio.subprocess.Process, *, force: bool = False) -> None:
    """Kill the supervisor; KILL_ON_JOB_CLOSE atomically reaps its whole tree."""

    if process.returncode is not None:
        return
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass


def recover_after_termination() -> SandboxSupport:
    """Run MXC's crash-recovery probe after an abnormal supervisor stop."""

    with _RUNTIME_LOCK:
        active = any(_ACTIVE_RUNTIME_ROOTS.values())
        cached = _CACHED_SUPPORT
    if active:
        if cached is None:
            raise SandboxUnavailable("MXC recovery deferred without a validated runtime")
        # The last live sandbox will perform the global recovery at count zero.
        return cached

    # Closing the outer Job starts kernel tree termination, but Process.wait()
    # only observes the launcher.  Repeat the canonical MXC recovery trigger so
    # a just-dying wxc-exec cannot make the first probe mistake its journal for
    # an active run.  Each probe also fails closed on a non-zero recovery error
    # count (see probe()).
    result: SandboxSupport | None = None
    for delay in (0.10, 0.25, 0.50):
        time.sleep(delay)
        result = probe()
        if not result.available:
            raise SandboxUnavailable(
                f"MXC post-termination policy recovery failed: {result.reason}"
            )
    assert result is not None
    return result


__all__ = [
    "MXC_POLICY_VERSION",
    "SandboxSupport",
    "SandboxUnavailable",
    "build_config",
    "find_mxc_executable",
    "find_sandbox_launcher",
    "minimal_environment",
    "probe",
    "recover_after_termination",
    "readonly_tool_paths",
    "require_support",
    "spawn",
    "terminate",
    "validate_workspace_security",
]
