# -*- coding: utf-8 -*-
"""DeepSeek（OpenAI 兼容）调用层。只用标准库 urllib，不依赖 openai SDK。

为什么不用官方 SDK：这个项目要能在手机上用 proot 里的 Python 直接跑起来，
装不装第三方包都得能用。协议本身只有 POST /chat/completions 一个端点，手写足够，也更好测。

密钥的来源只有一个：用户在界面里填，存在本机配置文件（权限 0600）。
- 绝不打进日志、绝不回显给前端（回显永远是掩码）；
- 出错的异常消息统一过 redact()，避免 key 混进 traceback 或错误响应里。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Iterator, NoReturn

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
# 已知模型：拉取列表失败（比如自建中转站没实现 /models）时给用户一个可选项
FALLBACK_MODELS = ("deepseek-chat", "deepseek-reasoner")
MAX_RETRIES = 2


class LLMError(Exception):
    """所有网络/协议/鉴权错误统一成这一种，消息已经是人话，可直接显示给用户。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


_redact_values: set[str] = set()


def register_secret(value: str | None) -> None:
    """把当前生效的 key 登记进来，供 redact() 全局清洗。"""
    if value and len(value) >= 8:
        _redact_values.add(value.strip())


def redact(text: Any) -> str:
    """抹掉文本里的密钥。日志、错误消息、发给前端的 body 都必须先过这里。"""
    out = text if isinstance(text, str) else str(text)
    for secret in list(_redact_values):
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out


def mask(key: str | None) -> str:
    """给界面看的掩码：sk-abcd…wxyz。绝不返回原文。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 10:
        return k[:2] + "…" + k[-2:]
    return f"{k[:6]}…{k[-4:]}"


def _status_of(exc: Exception) -> int | None:
    for name in ("code", "status", "status_code"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


def _fail(exc: Exception, what: str) -> NoReturn:
    if isinstance(exc, LLMError):
        raise exc
    status = _status_of(exc)
    hint = {
        400: "请求格式被拒", 401: "API Key 无效或被拒", 402: "余额不足",
        403: "没有权限", 404: "模型或地址不对", 422: "请求被拒",
        429: "请求过于频繁，稍后再试", 500: "服务端错误", 503: "服务暂时不可用",
    }.get(status, "")
    detail = redact(str(exc)).strip()[:300]
    head = f"{what}失败（HTTP {status}）" if status else f"{what}失败"
    raise LLMError(f"{head}：{hint or detail or type(exc).__name__}", status) from None


def _endpoint(base: str, path: str) -> str:
    """把 base 和路径拼起来。用户可能填 https://api.deepseek.com 或 .../v1 或带尾斜杠。"""
    b = (base or DEFAULT_BASE).strip().rstrip("/")
    if b.endswith("/chat/completions"):
        b = b[: -len("/chat/completions")]
    if not b.endswith("/v1") and "deepseek.com" in b:
        # DeepSeek 官方两种写法都收，但显式补 /v1 更稳
        b = b + "/v1"
    return f"{b}{path}"


def _request(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    last: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx 重试没有意义（除了 429）；把响应体带出来当提示
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                raw = ""
            detail = redact(raw)[:400]
            last = LLMError(
                f"HTTP {exc.code}：{detail}" if detail else f"HTTP {exc.code}", exc.code)
            if exc.code == 429 and attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise last from None
        except (TimeoutError, socket.timeout) as exc:
            last = LLMError(f"请求超时（{timeout:g}s）")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
        except urllib.error.URLError as exc:
            last = LLMError(f"连接失败：{redact(getattr(exc, 'reason', exc))}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
        except (ValueError, UnicodeDecodeError) as exc:
            raise LLMError(f"响应不是合法 JSON：{redact(exc)}") from None
    raise last or LLMError("请求失败")


def chat(messages: list[dict], api_key: str, base_url: str = DEFAULT_BASE,
         model: str = DEFAULT_MODEL, *, temperature: float = 1.0, max_tokens: int = 2000,
         timeout: float = 120, response_json: bool = False) -> str:
    """发一轮对话，返回正文文本。messages 是标准的 [{role, content}]。"""
    if not (api_key or "").strip():
        raise LLMError("还没填 API Key。打开「设置」填入 DeepSeek 的 Key 再试。")
    payload: dict = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_json:
        # DeepSeek 支持 OpenAI 的 json_object 模式；解析失败的兜底在调用方
        payload["response_format"] = {"type": "json_object"}
    data = _request(_endpoint(base_url, "/chat/completions"), payload, api_key.strip(), timeout)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"响应结构异常：{redact(json.dumps(data, ensure_ascii=False))[:300]}") from None


def list_models(api_key: str, base_url: str = DEFAULT_BASE, timeout: float = 20) -> list[str]:
    """GET /models。失败返回内置列表——列不出模型不该挡住用户开始聊天。"""
    key = (api_key or "").strip()
    if not key:
        raise LLMError("还没填 API Key。")
    req = urllib.request.Request(
        _endpoint(base_url, "/models"),
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"拉取模型列表失败（HTTP {exc.code}）", exc.code) from None
    except (TimeoutError, socket.timeout):
        raise LLMError(f"拉取模型列表超时（{timeout:g}s）") from None
    except urllib.error.URLError as exc:
        raise LLMError(f"拉取模型列表失败：{redact(getattr(exc, 'reason', exc))}") from None
    except (ValueError, UnicodeDecodeError):
        raise LLMError("模型列表不是合法 JSON") from None
    ids = [str(m.get("id") or "").strip() for m in (data.get("data") or []) if isinstance(m, dict)]
    ids = sorted({i for i in ids if i})
    return ids or list(FALLBACK_MODELS)


def verify_key(api_key: str, base_url: str = DEFAULT_BASE, timeout: float = 20) -> tuple[bool, str]:
    """轻量连通性检查：拉一次模型列表。返回 (是否通过, 给人看的话)。"""
    try:
        models = list_models(api_key, base_url, timeout)
    except LLMError as exc:
        return False, redact(str(exc))
    return True, f"连接正常，可用模型 {len(models)} 个"


if __name__ == "__main__":
    # 不联网：只验「拼地址、脱敏、错误转人话」这几处最容易坏的地方。
    assert _endpoint("https://api.deepseek.com", "/chat/completions") == \
        "https://api.deepseek.com/v1/chat/completions"
    assert _endpoint("https://api.deepseek.com/v1/", "/models") == \
        "https://api.deepseek.com/v1/models"
    assert _endpoint("https://my.gateway/v1", "/models") == "https://my.gateway/v1/models"
    assert mask("sk-1234567890abcdef") == "sk-123…cdef"
    assert mask("") == "" and "…" in mask("shortkey")
    register_secret("sk-supersecret-value")
    assert "sk-supersecret-value" not in redact("err sk-supersecret-value boom")
    assert redact("err sk-supersecret-value boom") == "err [REDACTED] boom"

    try:
        chat([{"role": "user", "content": "hi"}], api_key="")
        raise SystemExit("应当抛错")
    except LLMError as e:
        assert "API Key" in str(e)

    # 用假 urlopen 验证 payload 组装与响应解析
    from unittest.mock import patch

    seen: dict = {}

    class _Resp:
        """urlopen 的返回值是上下文管理器，假对象必须也实现这两个方法。"""

        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _fake(req, timeout=None):
        seen["url"] = req.full_url
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        seen["auth"] = req.headers.get("Authorization")
        return _Resp(json.dumps({"choices": [{"message": {"content": "  你好呀  "}}]}).encode())

    with patch.object(urllib.request, "urlopen", _fake):
        out = chat([{"role": "user", "content": "hi"}], "sk-test-key-1234567890",
                   response_json=True, temperature=0.9)
    assert out == "你好呀"
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test-key-1234567890"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["payload"]["stream"] is False and seen["payload"]["temperature"] == 0.9

    def _fake_models(req, timeout=None):
        return _Resp(json.dumps({"data": [{"id": "deepseek-reasoner"},
                                          {"id": "deepseek-chat"}]}).encode())

    with patch.object(urllib.request, "urlopen", _fake_models):
        assert list_models("sk-x-1234567890") == ["deepseek-chat", "deepseek-reasoner"]
    with patch.object(urllib.request, "urlopen", _fake_models):
        ok, msg = verify_key("sk-x-1234567890")
    assert ok and "2" in msg
    def _boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     __import__("io").BytesIO(b'{"error":"bad key sk-leak-9999"}'))
    register_secret("sk-leak-9999")
    with patch.object(urllib.request, "urlopen", _boom):
        try:
            chat([{"role": "user", "content": "hi"}], "sk-leak-9999-abcdefg")
            raise SystemExit("应当抛错")
        except LLMError as e:
            assert "sk-leak-9999" not in str(e), "密钥泄漏进错误消息"
    print("api ok")
