# -*- coding: utf-8 -*-
"""本地 HTTP 服务：把引擎包成 REST 接口，同时托管前端静态文件。

只用标准库（http.server + ThreadingHTTPServer），理由和 core/api.py 一样：
这个项目要能在被 proot 包起来的手机 Ubuntu 里直接跑，不该强制装任何第三方包。

两种客户端共用这一个服务：
- **桌面版**：本机浏览器打开 http://127.0.0.1:8765，宽屏三栏布局；
- **移动版**：手机浏览器打开同一个地址，窄屏自动切换成底部标签布局。
前端用 CSS 媒体查询自适应，所以后端只有一套接口，不存在两个版本逻辑分叉。

安全：
- 配置里的 API Key 只存在本机 data/config.json（0600），接口永远只回掩码；
- 默认只监听 127.0.0.1。要手机访问就跑在手机上，或用 --host 0.0.0.0 并自行承担局域网风险。
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

if __package__ in (None, ""):  # 允许 python -m junshi.server.app 之外直接跑
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from junshi import __version__  # noqa: E402
from junshi.core import api, engine, persona  # noqa: E402
from junshi.kb.retriever import default_kb  # noqa: E402
from junshi.memory.profile import ProfileStore  # noqa: E402
from junshi.paths import resource_path  # noqa: E402
from junshi.trend import series as trend_series  # noqa: E402

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(_PKG_DIR)
# 前端资源是只读的：打包成 exe 后在 PyInstaller 的解包目录里
STATIC_DIR = resource_path("app", "static")
DEFAULT_PORT = 8765
MAX_BODY = 4 * 1024 * 1024  # 4MB：粘贴长记录甚至截图 base64 都够用

CONFIG_FILE = "config.json"
_DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": api.DEFAULT_BASE,
    "model": api.DEFAULT_MODEL,
    "style": "",
    "relationship": "",
    "goal": "",
    "use_kb": True,
}


class Config:
    """本机配置。Key 落盘，但接口只回掩码。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, CONFIG_FILE)
        self._lock = threading.Lock()
        self._data = dict(_DEFAULT_CONFIG)
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                for key in _DEFAULT_CONFIG:
                    if key in raw and raw[key] is not None:
                        self._data[key] = raw[key]
        except (OSError, ValueError):
            pass
        # 环境变量优先：方便在不动界面的情况下切换（CI / 临时测试）
        env_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if env_key:
            self._data["api_key"] = env_key
        if self._data.get("api_key"):
            api.register_secret(str(self._data["api_key"]))

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def update(self, patch: dict) -> dict:
        with self._lock:
            for key in _DEFAULT_CONFIG:
                if key in patch and patch[key] is not None:
                    value = patch[key]
                    if key == "use_kb":
                        self._data[key] = bool(value)
                    elif isinstance(_DEFAULT_CONFIG[key], str):
                        self._data[key] = str(value).strip()
                    else:
                        self._data[key] = value
            if self._data.get("api_key"):
                api.register_secret(str(self._data["api_key"]))
            self._save()
            return dict(self._data)

    def public(self, *, models: list[str] | None = None, model_error: str = "") -> dict:
        """给前端的视图：**绝不含明文 Key**，只给掩码和是否已配置。"""
        with self._lock:
            data = dict(self._data)
        key = str(data.get("api_key") or "")
        out = {
            "base_url": data.get("base_url") or api.DEFAULT_BASE,
            "model": data.get("model") or api.DEFAULT_MODEL,
            "style": data.get("style") or "",
            "relationship": data.get("relationship") or "",
            "goal": data.get("goal") or "",
            "use_kb": bool(data.get("use_kb", True)),
            "key_set": bool(key),
            "key_mask": api.mask(key),
            "models": list(models) if models else list(api.FALLBACK_MODELS),
            "model_error": model_error,
        }
        return out


def _fmt_error(exc: Exception) -> tuple[int, str]:
    """异常 → (HTTP 状态, 给人看的话)。密钥一律过脱敏。"""
    if isinstance(exc, api.LLMError):
        message = api.redact(str(exc))
        # 「还没填 Key」是客户端没配好，属于 400；只有真的上游出错才用 502
        if exc.status is None and "API Key" in message:
            return 400, message
        status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 502
        return status, message
    if isinstance(exc, PermissionError):
        return 403, api.redact(str(exc))
    if isinstance(exc, (ValueError, KeyError)):
        return 400, api.redact(str(exc) or "请求参数有问题")
    return 500, api.redact(f"{type(exc).__name__}: {exc}")


class Handler(BaseHTTPRequestHandler):
    server_version = f"JunshiChat/{__version__}"
    protocol_version = "HTTP/1.1"

    # 这些由 create_server 挂上来
    config: Config
    store: ProfileStore
    deps: dict

    # ---------- 基础 ----------

    def log_message(self, fmt: str, *args) -> None:
        # 默认实现会把整行打到 stderr；这里过一层脱敏，避免 Key 混进终端
        sys.stderr.write("%s - %s\n" % (self.address_string(), api.redact(fmt % args)))

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": api.redact(message)}, status=status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # ---------- 路由 ----------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                return self._api_get(path, query)
            return self._static(path)
        except Exception as exc:  # 兜底：任何异常都要变成可读响应，不能让连接挂住
            status, message = _fmt_error(exc)
            self._error(status, message)

    do_HEAD = do_GET

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/reply":
                return self._api_reply()
            if path == "/api/tone":
                return self._api_tone_add()
            if path == "/api/analyze":
                return self._api_analyze()
            if path == "/api/config":
                return self._api_config_write()
            if path == "/api/config/verify":
                return self._api_verify()
            if path == "/api/history/clear":
                return self._api_history_clear()
            if path == "/api/tone/clear":
                return self._api_tone_clear()
            if path == "/api/profiles":
                return self._api_profile_save()
            if path == "/api/profiles/event":
                return self._api_profile_event()
            if path == "/api/consent":
                return self._api_consent()
            if path == "/api/clear-all":
                return self._api_clear_all()
            if path == "/api/trend":
                return self._api_trend()
            return self._error(404, "没有这个接口")
        except Exception as exc:
            status, message = _fmt_error(exc)
            self._error(status, message)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/profiles/"):
                pid = path[len("/api/profiles/"):].strip()
                if not pid:
                    return self._error(400, "缺少档案 id")
                return self._json({"deleted": self.store.delete(pid)})
            return self._error(404, "没有这个接口")
        except Exception as exc:
            status, message = _fmt_error(exc)
            self._error(status, message)

    # ---------- API: 读 ----------

    def _api_get(self, path: str, query: dict) -> None:
        if path == "/api/health":
            kb = self.deps.get("kb") or default_kb()
            return self._json({
                "ok": True, "version": __version__,
                "kb_docs": kb.doc_count(),
                "key_set": self.config.get("api_key") != "",
            })
        if path == "/api/config":
            models, err = None, ""
            if query.get("models") and self.config.get("api_key"):
                try:
                    models = api.list_models(str(self.config.get("api_key")),
                                             str(self.config.get("base_url")), timeout=15)
                except api.LLMError as exc:
                    err = api.redact(str(exc))
            return self._json(self.config.public(models=models, model_error=err))
        if path == "/api/history":
            profile_id = (query.get("profile_id") or ["default"])[0]
            limit = _int_arg(query, "limit", 40, 1, 200)
            return self._json({"items": self.store.history(profile_id, limit=limit),
                               "tone_count": self.store.tone_count(profile_id),
                               "tone_samples": self.store.tone_samples(profile_id)})
        if path == "/api/profiles":
            return self._json({"items": self.store.list_profiles(),
                               "summary": self.store.summary(),
                               "relationships": persona.RELATIONSHIPS})
        if path == "/api/consent":
            return self._json(self.store.consent())
        if path == "/api/knowledge":
            kb = self.deps.get("kb") or default_kb()
            return self._json({
                "docs": [{"path": d.path, "title": d.title, "category": d.category}
                         for d in kb.docs()],
                "count": kb.doc_count(),
            })
        if path == "/api/knowledge/search":
            kb = self.deps.get("kb") or default_kb()
            q = (query.get("q") or [""])[0]
            k = _int_arg(query, "k", 3, 1, 10)
            hits = kb.search(q, top_k=k)
            return self._json({"query": q,
                               "hits": [{"title": d.title, "path": d.path,
                                         "category": d.category, "score": round(s, 3)}
                                        for d, s in hits]})
        if path == "/api/trend/presets":
            return self._json({"items": trend_series.presets()})
        if path == "/api/trend/preset":
            key = (query.get("key") or [""])[0]
            return self._json(trend_series.preset_series(key))
        if path == "/api/trend/sample":
            # 用内置示例数据生成一条真实口径的曲线，方便没数据时先看效果
            return self._json(trend_series.compute_series(_sample_records()))
        return self._error(404, "没有这个接口")

    # ---------- API: 写 ----------

    def _api_config_write(self) -> None:
        body = self._body()
        # api_key 允许显式清空（传空串），也允许「不动」（不传这个字段）
        patch = {k: v for k, v in body.items() if k in _DEFAULT_CONFIG}
        if "api_key" in patch:
            value = str(patch["api_key"] or "").strip()
            if value in ("", None):
                # 清空 Key
                patch["api_key"] = ""
            elif value.startswith("•") or "…" in value or "..." in value:
                # 前端把掩码回传了：当成「不改」，否则会把真 Key 覆盖成掩码
                patch.pop("api_key")
        self.config.update(patch)
        self._json(self.config.public())

    def _api_verify(self) -> None:
        body = self._body()
        key = str(body.get("api_key") or "").strip() or str(self.config.get("api_key") or "")
        base = str(body.get("base_url") or self.config.get("base_url") or api.DEFAULT_BASE)
        if not key:
            return self._error(400, "还没填 API Key")
        ok, message = api.verify_key(key, base)
        return self._json({"ok": ok, "message": message})

    def _api_reply(self) -> None:
        """「回她」：对方最新一句 → 我该发什么。

        history 的语义在这里变了：它存的是**真实对话**——
        用户粘进来的对方的话标成 her，他实际发出去的标成 me。
        所以 memory_block 和 history_block 都是真的上下文，不是 AI 扮演的产物。
        """
        body = self._body()
        latest = str(body.get("latest") or body.get("message") or "").strip()
        if not latest:
            return self._error(400, "先把对方最新发来的话粘进来")
        profile_id = str(body.get("profile_id") or "default")
        store_profile = body.get("profile") if isinstance(body.get("profile"), dict) else None
        if store_profile is None and profile_id != "default":
            store_profile = self.store.get(profile_id)

        history = self.store.history(profile_id, limit=24)
        tone_samples = self.store.tone_samples(profile_id)
        result = engine.reply_to_her(
            latest,
            history=history,
            relationship=str(body.get("relationship") or self.config.get("relationship") or ""),
            goal=str(body.get("goal") or self.config.get("goal") or ""),
            style=str(body.get("style") or self.config.get("style") or ""),
            tone_samples=tone_samples,
            profile=store_profile,
            user_note=str(body.get("note") or ""),
            use_kb=bool(body.get("use_kb", self.config.get("use_kb", True))),
            model=str(body.get("model") or self.config.get("model") or api.DEFAULT_MODEL),
            api_key=str(self.config.get("api_key") or ""),
            base_url=str(self.config.get("base_url") or api.DEFAULT_BASE),
            kb=self.deps.get("kb"),
        )

        # 只有长期记忆开着才落盘。这里记的是**对方说过的话**，不是 AI 生成的内容，
        # 所以「他实际发了什么」由前端通过 /api/tone 单独回报，不在这里瞎猜。
        saved = False
        if self.store.consent()["enabled"]:
            try:
                self.store.append_history(profile_id, "her", latest,
                                          meta={"model": result.get("model"),
                                                "refs": result.get("refs")})
                saved = True
            except (OSError, ValueError, PermissionError):
                saved = False

        self._json({
            "candidates": result["candidates"],
            "best": result["best"],
            "best_text": result["best_text"],
            "analysis": result.get("analysis"),
            "refs": result.get("refs") or [],
            "degraded": result.get("degraded", False),
            "model": result.get("model", ""),
            "saved": saved,
            "tone_count": self.store.tone_count(profile_id),
        })

    def _api_tone_add(self) -> None:
        """收一条「我真实发出去的话」当语气样本。

        前端在用户复制/发送候选后调用。未开启长期记忆时返回 saved=False 并说明原因，
        不静默丢弃——用户有权知道这条没被记住。
        """
        body = self._body()
        text = str(body.get("text") or "").strip()
        if not text:
            return self._error(400, "没有要记住的内容")
        profile_id = str(body.get("profile_id") or "default")
        saved = self.store.remember_tone(profile_id, text,
                                         source=str(body.get("source") or "sent"))
        # 同时把「我发的这句」补进对话历史，让下一轮的上下文是完整的
        if saved:
            try:
                self.store.append_history(profile_id, "me", text)
            except (OSError, ValueError, PermissionError):
                pass
        return self._json({
            "saved": saved,
            "tone_count": self.store.tone_count(profile_id),
            "message": "" if saved else "长期记忆没开启，这句话没有被记住",
        })

    def _api_tone_clear(self) -> None:
        body = self._body()
        profile_id = str(body.get("profile_id") or "default")
        return self._json({"cleared": self.store.forget_tone(profile_id),
                           "tone_count": self.store.tone_count(profile_id)})

    def _api_analyze(self) -> None:
        body = self._body()
        records = body.get("records")
        if isinstance(records, str):
            records = records.strip()
        if not records:
            return self._error(400, "先粘贴聊天记录，或导入 CSV")
        profile_id = str(body.get("profile_id") or "default")
        store_profile = body.get("profile") if isinstance(body.get("profile"), dict) else None
        if store_profile is None and profile_id != "default":
            store_profile = self.store.get(profile_id)
        result = engine.analyze_chat(
            records,
            relationship=str(body.get("relationship") or self.config.get("relationship") or ""),
            goal=str(body.get("goal") or self.config.get("goal") or ""),
            style=str(body.get("style") or self.config.get("style") or ""),
            tone_samples=self.store.tone_samples(profile_id),
            profile=store_profile,
            user_note=str(body.get("note") or ""),
            use_kb=bool(body.get("use_kb", self.config.get("use_kb", True))),
            model=str(body.get("model") or self.config.get("model") or api.DEFAULT_MODEL),
            api_key=str(self.config.get("api_key") or ""),
            base_url=str(self.config.get("base_url") or api.DEFAULT_BASE),
            kb=self.deps.get("kb"),
        )
        self._json(result)

    def _api_profile_save(self) -> None:
        body = self._body()
        return self._json({"profile": self.store.save(body),
                           "summary": self.store.summary()})

    def _api_profile_event(self) -> None:
        body = self._body()
        pid = str(body.get("id") or "").strip()
        if not pid:
            return self._error(400, "缺少档案 id")
        return self._json({"profile": self.store.add_event(
            pid, str(body.get("text") or ""), str(body.get("kind") or "note"))})

    def _api_consent(self) -> None:
        body = self._body()
        enabled = bool(body.get("enabled"))
        out = self.store.set_consent(enabled)
        return self._json({"enabled": out["enabled"], "summary": self.store.summary()})

    def _api_clear_all(self) -> None:
        return self._json(self.store.clear_all())

    def _api_history_clear(self) -> None:
        body = self._body()
        pid = str(body.get("profile_id") or "default")
        return self._json({"cleared": self.store.clear_history(pid)})

    def _api_trend(self) -> None:
        body = self._body()
        records = body.get("records")
        if isinstance(records, str) and not records.strip():
            return self._error(400, "没有可分析的记录")
        if records is None:
            records = []
        return self._json(trend_series.compute_series(records))

    # ---------- 静态文件 ----------

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = os.path.normpath(os.path.join(STATIC_DIR, rel))
        # 目录穿越防护：必须仍在 static 目录内
        if not target.startswith(os.path.abspath(STATIC_DIR) + os.sep) and \
                target != os.path.abspath(STATIC_DIR):
            return self._error(403, "禁止访问")
        if not os.path.isfile(target):
            # 前端是单页应用，未知路径一律回 index.html
            target = os.path.join(STATIC_DIR, "index.html")
            if not os.path.isfile(target):
                return self._error(404, "前端文件缺失")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        try:
            with open(target, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._error(404, "读不到文件")
        self._send(200, body, ctype)


def _int_arg(query: dict, name: str, default: int, low: int, high: int) -> int:
    try:
        value = int((query.get(name) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _sample_records() -> list[dict]:
    """内置示例记录，让用户第一眼就能看到 K 线长什么样（明确标注是示例）。"""
    from datetime import datetime, timedelta
    base = datetime(2026, 9, 1, 20, 0)
    warm = ["今天想你了，晚上一起吃饭吗？", "路上看到一家店，想到你", "在干嘛呀"]
    cool = ["嗯", "哦", "忙"]
    out = []
    for day in range(21):
        hot = day >= 7
        for i in range(8 if hot else 3):
            out.append({"from": "me",
                        "text": warm[i % len(warm)] if hot else "在吗",
                        "time": (base + timedelta(days=day, minutes=i)).strftime("%Y-%m-%d %H:%M")})
            out.append({"from": "her",
                        "text": warm[(i + 1) % len(warm)] if hot else cool[i % len(cool)],
                        "time": (base + timedelta(days=day, minutes=i + 20)).strftime("%Y-%m-%d %H:%M")})
    return out


def create_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                  data_dir: str | None = None):
    store = ProfileStore(data_dir)
    config = Config(store.data_dir)

    class _Handler(Handler):
        pass

    _Handler.config = config
    _Handler.store = store
    _Handler.deps = {"kb": default_kb()}

    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    # 挂出来方便测试和排查（服务本身只用 _Handler 上的那份）
    httpd.store = store          # type: ignore[attr-defined]
    httpd.config = config        # type: ignore[attr-defined]
    return httpd, store


def lan_ip() -> str:
    """猜一个局域网 IP 显示给用户（手机访问用）。失败就给 127.0.0.1。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="junshi-chat", description="军师 Chat：帮你回她 + 狗头军师内核")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址；0.0.0.0 允许局域网访问（默认只本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", default=None, help="配置与档案目录")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--mobile", action="store_true",
                        help="以移动版形态提示访问地址（界面自适应，仅影响提示文案）")
    args = parser.parse_args(argv)

    try:
        httpd, store = create_server(args.host, args.port, args.data_dir)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        print(f"端口 {args.port} 可能被占用，换一个：--port {args.port + 1}", file=sys.stderr)
        return 1

    # 必须用**实际绑定**的端口，不能用命令行传进来的那个：
    # --port 0 时系统会分配一个随机端口，照 args.port 打印会给出一个连不上的地址（踩过）。
    actual_port = int(getattr(httpd, "server_address", (None, args.port))[1] or args.port)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown_host}:{actual_port}/"
    print(f"军师 Chat v{__version__} 已启动")
    print(f"  本地访问：{url}")
    if args.host == "0.0.0.0":
        print(f"  手机访问：http://{lan_ip()}:{actual_port}/   （同一 Wi-Fi 下）")
    print(f"  数据目录：{store.data_dir}")
    print(f"  界面形态：{'移动版（窄屏自适应）' if args.mobile else '自适应（宽屏桌面版／窄屏移动版）'}")
    if not store.consent()["enabled"]:
        print("  长期记忆：未开启（开启后才会保存对象档案、对话记录与语气样本）")
    print("  Ctrl+C 停止")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
