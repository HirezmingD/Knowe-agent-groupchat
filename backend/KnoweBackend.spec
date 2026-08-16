# -*- mode: python ; coding: utf-8 -*-
# KnoweBackend.spec — PyInstaller 6.x 后端打包（v1.0.24.7 结构归一后，源码保密）
#
# 约定：本 spec 位于 <工作副本>/backend/ 下；PyInstaller 执行 spec 前会
#   chdir 到 spec 所在目录，因此 spec 内所有相对路径均相对本目录。
#
# 关键点（已审计定案）：
#   1. 入口 = run_backend.py（绝对导入 backend.server）；pathex=[SPECPATH]
#      保证 backend./knowe_provenance/knowe_core/knowe_harness/knowe_storage/
#      knowe_adapters 六个包可解析（v1.0.24.7 归一后 backend 为单层实现包）。
#   2. datas 目标不带双层前缀（归一前为 backend/backend）：引擎按 __file__
#      相对路径取数据（engine.py:_SOULS=parent/"souls"、zinnia.py:_PROMPTS_DIR=
#      parent.parent/"prompts"、i18n_backend.py:_DIR=parent/"locales"、
#      worker_gateway_runtime.py:Path(__file__).with_name("worker_prompt.md")），
#      打包后模块落在 _internal/backend/，故数据须同层布局。
#   3. console=False（窗口子系统），日志走 server.py 的文件双写保险丝。

import os  # noqa: E402

try:  # version 资源仅 Windows 构建机可用；拿不到就略过，不阻塞构建
    from PyInstaller.utils.win32.versioninfo import (  # noqa: F401
        FixedFileVersion,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    _VERSION = VSVersionInfo(
        ffi=FixedFileVersion(1, 0, 25, 2),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Knowe"),
                            StringStruct("FileDescription", "Knowe Backend Service"),
                            StringStruct("FileVersion", "1.0.25.2"),
                            StringStruct("InternalName", "KnoweBackend"),
                            StringStruct("OriginalFilename", "KnoweBackend.exe"),
                            StringStruct("ProductName", "Knowe"),
                            StringStruct("ProductVersion", "1.0.25.2"),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
except Exception:  # pragma: no cover - 非 Windows 构建机
    _VERSION = None

# icon 在 <工作副本>/build/icon.ico（spec 上一级目录）。PyInstaller 把相对路径
# 按 spec 所在目录解析，故用 SPECPATH 推导而不是字面 'build/icon.ico'。
_ICON = os.path.join(SPECPATH, "..", "build", "icon.ico")

a = Analysis(
    ["run_backend.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        # (源相对 spec 目录, 目标相对 _internal/) —— 与 __file__ 相对路径匹配
        # v1.0.24.7 归一后 souls/prompts/locales 已在 backend/ 下，源相对 spec 目录直接写名字
        ("souls", "backend/souls"),
        ("prompts", "backend/prompts"),
        ("locales", "backend/locales"),
        ("worker_prompt.md", "backend"),
    ],
    hiddenimports=[
        "playwright",
        "playwright.async_api",
        "playwright._impl._driver",
        "ddgs",
        "duckduckgo_search",
        "yaml",
        "dotenv",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "IPython", "numpy", "scipy", "mkl", "mkl_fft", "mkl_random", "mkl-service"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KnoweBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    version=_VERSION,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="KnoweBackend",
)
