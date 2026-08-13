"""PyInstaller 打包入口（v1.0.24.7 结构归一后）。

直接用 exe 运行时，入口脚本是冻结后的顶层模块——``backend/__main__.py`` 的相对导入
（``from .server import main``）在冻结态没有父包可依，会报
"attempted relative import with no known parent package"。

本启动器用**全限定绝对导入** ``backend.server`` 定位实现包：
- PyInstaller 分析期：pathex 首位是 backend/，``backend`` = 实现包（归一后单层），
  静态可解析、必然被收集。
- 冻结运行期：backend 以冻结模块存在，导入链与源码态一致。
- 注意：本启动器仅用于打包态入口；开发态 Electron 仍走 ``python -m backend``
  （cwd=项目根，经 PYTHONPATH 解析 knowe_*），不经过本文件。
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _sandbox_execute_mode() -> bool:
    """Run one project-local script inside the already-enforced MXC boundary.

    PyInstaller's onedir bundle has an embedded CPython runtime but intentionally
    does not ship a separate ``python.exe``.  This narrow mode makes packaged
    ``execute_code`` use that runtime without starting the backend server.  It
    is useful only when MXC explicitly grants the bundle read access and sets a
    project-local ``KNOWE_WORKSPACE_ROOT``.
    """

    if len(sys.argv) != 3 or sys.argv[1] != "--knowe-sandbox-execute":
        return False
    root_value = os.environ.get("KNOWE_WORKSPACE_ROOT", "").strip()
    if not root_value:
        raise SystemExit("sandbox workspace environment is missing")
    root = Path(root_value).resolve(strict=True)
    script = Path(sys.argv[2]).resolve(strict=True)
    if not script.is_file() or (script != root and root not in script.parents):
        raise SystemExit("sandbox script must stay inside the project workspace")

    # console=False PyInstaller builds may expose no Python text streams even
    # when Windows std handles are inherited. Rebind them to MXC's pipes.
    def inherited_stream(fd: int, std_handle: int, mode: str):
        try:
            duplicate = os.dup(fd)
        except OSError:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_std_handle = kernel32.GetStdHandle
            get_std_handle.argtypes = [wintypes.DWORD]
            get_std_handle.restype = wintypes.HANDLE
            handle = get_std_handle(std_handle & 0xFFFFFFFF)
            invalid_handle = ctypes.c_void_p(-1).value
            if handle in (None, invalid_handle):
                raise SystemExit(f"sandbox standard handle {fd} is unavailable")
            flags = os.O_RDONLY if "r" in mode else os.O_WRONLY
            duplicate = msvcrt.open_osfhandle(int(handle), flags)
        return os.fdopen(
            duplicate,
            mode,
            encoding="utf-8",
            buffering=1,
            closefd=False,
        )

    if sys.stdin is None:
        sys.stdin = inherited_stream(0, -10, "r")
    if sys.stdout is None:
        sys.stdout = inherited_stream(1, -11, "w")
    if sys.stderr is None:
        sys.stderr = inherited_stream(2, -12, "w")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name="__main__")
    return True

if __name__ == "__main__":
    if not _sandbox_execute_mode():
        from backend.server import main

        main()
