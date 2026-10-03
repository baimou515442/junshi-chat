#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""军师 Chat 的两用入口。

    python3 main.py                     # 本地启动，浏览器打开 http://127.0.0.1:8765
    python3 main.py --mobile            # 手机上跑，打印手机浏览器要访问的地址
    python3 main.py --host 0.0.0.0      # 让同一局域网的手机访问（注意暴露风险）
    python3 main.py --check             # 只做自检，不启动服务

桌面版和移动版共用这一个入口：界面是同一套响应式前端，
宽屏渲染成侧栏布局，窄屏渲染成底部标签布局。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from junshi.server.app import main  # noqa: E402


def _check() -> int:
    """离线自检：确认知识库、引擎、存储、前端资源都在位。不联网、不读 Key。"""
    ok = True
    from junshi import __version__
    from junshi.core import engine, persona  # noqa: F401
    from junshi.kb.retriever import default_kb
    from junshi.memory.profile import ProfileStore
    from junshi.trend import series

    print(f"军师 Chat v{__version__} 自检")

    kb = default_kb()
    docs = kb.doc_count()
    print(f"  知识库：{docs} 份参考文档")
    ok &= docs >= 40

    hits = kb.search("他总是冷淡不回消息", top_k=3)
    print(f"  检索试跑：{hits[0][0].title if hits else '（没有命中）'}")
    ok &= bool(hits)

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "static")
    for name in ("index.html", os.path.join("css", "app.css"), os.path.join("js", "app.js")):
        exists = os.path.isfile(os.path.join(static_dir, name))
        print(f"  前端 {name}: {'OK' if exists else '缺失'}")
        ok &= exists

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = ProfileStore(tmp)
        # 未同意时不写任何东西，这条是产品承诺，自检里也要守住
        try:
            store.save({"codename": "自检"})
            print("  长期记忆门禁：失败（未同意却写入了）")
            ok = False
        except PermissionError:
            print("  长期记忆门禁：OK（未同意不写入）")

    series.compute_series([{"from": "me", "text": "在吗", "time": "2026-09-01 20:00"}])
    print("  趋势计算：OK")
    print("自检结果：" + ("全部通过" if ok else "有问题，见上面"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    raise SystemExit(main())
