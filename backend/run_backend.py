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
from backend.server import main

if __name__ == "__main__":
    main()
