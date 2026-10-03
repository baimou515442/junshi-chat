# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把军师 Chat 打成**单个 .exe**。

用户拿到 exe 双击就能用，不需要 Python、不需要命令行：
程序会起本地服务并自动打开默认浏览器，界面就是前面那套响应式前端。

要一起打进去的东西（漏一个，exe 就会在用户机器上报「前端文件缺失」）：
- app/static/  —— 前端三件套
- junshi/kb/   —— SKILL.md + 44 份参考文档（知识库检索靠它）
- junshi/      —— 会 import 的模块，用 collect_submodules 全量收

排除项是为了把体积压下来：这个项目只用标准库，不该把 numpy/PyQt 之类拖进来。
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# PyInstaller 的 spec 里没有 SPECPATH 这种内置变量（误用它会把路径拼成
# packaging/packaging/windows/... 导致「script not found」——踩过）。
# spec 执行时 __file__ 就是本文件，可靠地反推出项目根。
_HERE = os.path.dirname(os.path.abspath(__file__))          # packaging/windows
PROJECT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # 项目根

# launcher.py 位于 packaging/windows/，它在运行时要 sys.path 里能找到 junshi，
# 所以把 junshi 包也当作数据打进去（PyInstaller 会把 .py 编进包，这里是为了
# resource_path 能找到 junshi/kb 下的知识库）。
datas = [
    (os.path.join(PROJECT, "app", "static"), os.path.join("app", "static")),
    (os.path.join(PROJECT, "junshi", "kb"), os.path.join("junshi", "kb")),
]

hiddenimports = collect_submodules("junshi")

# 知识库是数据文件不是 .py，但显式排除掉之前 PyInstaller 可能扫进来的缓存
excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "test", "distutils",
    "numpy", "PIL", "PySide6", "PyQt5", "PyQt6", "matplotlib",
    "openai", "anthropic", "google",
]

a = Analysis(
    [os.path.join(PROJECT, "packaging", "windows", "launcher.py")],
    pathex=[PROJECT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="军师Chat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 常被杀软误报，宁可大几 MB 也不要用户装不上
    runtime_tmpdir=None,
    console=False,      # 不要黑窗口：用户双击后只看到浏览器打开
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
