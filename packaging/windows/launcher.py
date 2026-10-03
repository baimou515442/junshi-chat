# -*- coding: utf-8 -*-
"""Windows 双击入口：给 exe 用，保证用户**永远看不到命令行**。

做的事：
1. 挑一个能用的端口（默认 8765，被占了就往后找，而不是报错退出）；
2. 起本地服务；
3. 自动打开默认浏览器；
4. 出任何问题都弹一个原生对话框说清楚，而不是静默退出或闪一个黑窗。

窗口化模式（console=False）下 sys.stdout/stderr 可能是 None，打印会抛异常，
所以这里的输出全部走 _say()，它自己判断有没有可用的流。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _say(message: str) -> None:
    """有控制台就打印，没有（窗口模式）就忽略。"""
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            stream.write(message + "\n")
            stream.flush()
        except Exception:
            pass


def _fatal(title: str, message: str) -> None:
    """出错时弹原生对话框。弹不出来就退回 stderr，总之不要静默失败。"""
    _say(f"[{title}] {message}")
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _pick_port(preferred: int = 8765, tries: int = 20) -> int:
    """从 preferred 开始找第一个能绑上的端口。被占就换，不打扰用户。"""
    for offset in range(tries):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0  # 交给系统随机分配


def _port_from_httpd(httpd) -> int:
    try:
        return int(httpd.server_address[1])
    except Exception:
        return 0


def main() -> int:
    try:
        from junshi import __version__
        from junshi.server.app import create_server, lan_ip  # noqa: F401
    except Exception as exc:  # 打包缺文件时会走到这里
        _fatal("军师 Chat 启动失败",
               f"程序文件不完整，缺少内部模块。\n\n{type(exc).__name__}: {exc}\n\n"
               "请重新下载完整的 exe。")
        return 1

    # 先确认前端资源在：缺了的话服务能起来，但打开是白页，用户只会更困惑
    try:
        from junshi.paths import resource_path

        index = resource_path("app", "static", "index.html")
        if not os.path.isfile(index):
            _fatal("军师 Chat 启动失败",
                   f"找不到前端文件：\n{index}\n\nexe 可能被杀毒软件拆掉了部分内容，"
                   "请重新解压/下载。")
            return 1
    except Exception:
        pass

    port = _pick_port()
    try:
        httpd, store = create_server("127.0.0.1", port)
    except OSError as exc:
        _fatal("军师 Chat 启动失败",
               f"无法在本机启动服务（端口 {port}）：\n{exc}\n\n"
               "请检查是否有安全软件拦截了本地网络访问。")
        return 1
    except Exception as exc:
        _fatal("军师 Chat 启动失败",
               f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-800:]}")
        return 1

    port = _port_from_httpd(httpd) or port
    url = f"http://127.0.0.1:{port}/"
    _say(f"军师 Chat v{__version__}")
    _say(f"  界面地址：{url}")
    _say(f"  数据目录：{store.data_dir}")
    _say("  关闭这个窗口就会停止服务")

    def _open_browser() -> None:
        time.sleep(0.7)
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            _say(f"没能自动打开浏览器，请手动访问：{url}")

    threading.Thread(target=_open_browser, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 最后一道兜底：宁可弹窗也不要静默消失
        _fatal("军师 Chat 异常退出", f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)
