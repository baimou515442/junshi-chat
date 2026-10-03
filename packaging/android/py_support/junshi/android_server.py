# -*- coding: utf-8 -*-
"""Android 端启动 Python 引擎的入口，由 MainActivity 通过 Chaquopy 调用。

这个文件由 packaging/sync_android_assets.sh 复制进 APK 的 Python 源码目录，
所以它放在 packaging/android/py_support/ 下，而不是直接被同步脚本覆盖的
app/src/main/python/ 里。

为什么单独写一个 android_server 而不是复用 server.app.main()：
main() 会读命令行参数、打印、装信号处理，那些在 Android 上没意义。
这里只要「起一个线程跑 HTTP 服务，把端口告诉科特林」。

数据目录由科特林传进来（应用私有目录），通过 JUNSHI_DATA_DIR 环境变量生效——
这样 memory/profile.py 和 server/app.py 不用为 Android 写任何特例分支，
它们照旧走 junshi.paths.data_path()。
"""

from __future__ import annotations

import os
import socket
import sys
import threading

# 在 Chaquopy 里 junshi 包已经在 sys.path 上；但直接当脚本跑（本文件底部的 __main__）
# 时不会，这里自举一下，保证两种跑法都能用。
_PY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PY_ROOT not in sys.path:
    sys.path.insert(0, _PY_ROOT)

DEFAULT_PORT = 8765
DEFAULT_TRIES = 12


def _port_available(port: int) -> bool:
    """先探一下再启动：create_server 失败时留下的半开 socket 在某些设备上不好收拾。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start(start_port: int = DEFAULT_PORT, tries: int = DEFAULT_TRIES, data_dir: str = ""):
    """在后台线程起服务，返回 {"port": int, "error": str}。

    port <= 0 表示失败，error 里是给人看的原因；
    科特林据此决定是加载页面还是显示「重试」。
    """
    if data_dir:
        os.environ["JUNSHI_DATA_DIR"] = data_dir
        try:
            os.makedirs(data_dir, exist_ok=True)
        except OSError:
            pass

    try:
        from junshi.server.app import create_server
    except Exception as exc:  # 打包缺文件
        return {"port": 0, "error": f"缺少内部模块：{type(exc).__name__}: {exc}"}

    first = int(start_port or DEFAULT_PORT)
    attempts = max(1, int(tries or DEFAULT_TRIES))
    last_error = ""
    for offset in range(attempts):
        candidate = first + offset
        if not _port_available(candidate):
            last_error = f"端口 {candidate} 被占用"
            continue
        try:
            httpd, _store = create_server("127.0.0.1", candidate)
        except OSError as exc:
            last_error = f"端口 {candidate} 启动失败：{exc}"
            continue
        except Exception as exc:
            return {"port": 0, "error": f"{type(exc).__name__}: {exc}"}

        port = int(httpd.server_address[1])
        threading.Thread(target=_serve, args=(httpd,), daemon=True).start()
        return {"port": port, "error": ""}

    return {"port": 0, "error": last_error or "没有可用端口"}


def _serve(httpd) -> None:
    try:
        # poll_interval 短一点：应用退出时线程能更快察觉，不至于拖着进程
        httpd.serve_forever(poll_interval=0.4)
    except Exception:
        # 服务线程挂掉不该带走整个应用：Activity 里的「重试」还能救
        pass


def stop(httpd) -> None:
    """留给未来做「完全退出」用；目前进程退出即可。"""
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass


if __name__ == "__main__":
    # 在桌面上也能跑这个入口，方便先确认它本身没问题（不依赖 Android）
    result = start(8899, 3)
    print(result)
    assert result["port"] > 0 and not result["error"], result
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{result['port']}/api/health", timeout=10) as resp:
        print(resp.read().decode())
    print("android_server ok")
