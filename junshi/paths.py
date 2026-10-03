# -*- coding: utf-8 -*-
"""路径解析：同时兼容「直接用源码跑」和「被 PyInstaller 打成单文件 exe」。

PyInstaller 单文件模式下，运行时会把自己解包到一个临时目录（sys._MEIPASS），
静态资源和知识库都在那里，而**可写数据**（配置、档案）绝对不能写进去——
那个目录是临时的，进程一退就没了，而且用户根本找不到。

所以这里把两类路径彻底分开：
- resource_path()：只读资源（前端文件、知识库、SKILL.md）→ 冻结时在 _MEIPASS 里；
- data_path()：可写数据（config.json、profiles.json）→ 冻结时放 exe 同级的 data/，
  源码时放项目根的 data/。这样用户双击 exe、数据就在看得见的地方。
"""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """是否运行在 PyInstaller（或同类打包器）冻结出来的单文件里。"""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundle_dir() -> str:
    """只读资源根目录。"""
    if is_frozen():
        return str(getattr(sys, "_MEIPASS"))
    # 源码模式：junshi/paths.py → 上两级是项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """拼一个只读资源的绝对路径。"""
    return os.path.join(bundle_dir(), *parts)


def executable_dir() -> str:
    """exe 或项目根所在的目录——给用户放数据用的。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return bundle_dir()


def data_path(*parts: str) -> str:
    """可写数据目录下的路径，必要时创建目录。

    冻结成 exe 时的落点顺序：
    1. JUNSHI_DATA_DIR（用户显式指定）；
    2. exe 同级的 data/ —— 绿色版最直观，用户看得见自己的数据；
    3. 上面写不进去（比如 exe 被放在 Program Files）就退回用户目录
       （Windows: %LOCALAPPDATA%，其它: ~/.local/share），而不是直接崩。
    """
    override = (os.environ.get("JUNSHI_DATA_DIR") or "").strip()
    if override:
        base = override
    else:
        primary = os.path.join(executable_dir(), "data")
        base = primary if _writable(primary) else _fallback_data_dir()
    if parts:
        base = os.path.join(base, *parts)
        os.makedirs(os.path.dirname(base), exist_ok=True)
    else:
        os.makedirs(base, exist_ok=True)
    return base


def _writable(path: str) -> bool:
    """真的试着写一个文件——只看权限位在 Windows 上不作数。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _fallback_data_dir() -> str:
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(local, "JunshiChat")


if __name__ == "__main__":
    assert callable(resource_path) and callable(data_path)
    assert not is_frozen()  # 自检时一定不是冻结模式
    assert resource_path("app", "static", "index.html").endswith(
        os.path.join("app", "static", "index.html"))
    assert os.path.isabs(data_path())
    print("paths ok")
