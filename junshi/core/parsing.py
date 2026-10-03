# -*- coding: utf-8 -*-
"""模型输出的容错解析与字段校验。

DeepSeek 开了 json_object 模式后大体是好的 JSON，但仍然会：
- 套一层 ```json 围栏；前后带一句「好的，这是结果：」；把数组包在 {"replies": [...]} 里；
- 字段名大小写不一、数值给成字符串、该给列表的地方给单值；
- 该给 3 条候选只给 2 条，或在候选里塞进带引号的成品句。

这些都属于「格式小病」，不该让整轮对话失败。这里把清洗集中到一处，
两条引擎链路共用，也方便单测覆盖。
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class ParseError(ValueError):
    """连兜底都救不回来的输出。调用方据此决定是重试还是降级。"""


def extract_json(raw: str) -> Any:
    """从模型输出里抠出 JSON 值。

    优先用 json.JSONDecoder.raw_decode —— 它按 JSON 语法扫描，字符串里的 `}`／`]`
    不会被误当成结构边界（手写括号平衡扫描在这里必翻车，已踩过）。
    顺序：整体 → 代码围栏内 → 正文里第一个 `{` 或 `[` 开始的合法值。
    """
    text = (raw or "").strip()
    if not text:
        raise ParseError("模型返回了空内容")

    decoder = json.JSONDecoder()
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except ValueError:
            pass
        # 前后还挂着解释文字时，从第一个 { 或 [ 起用 raw_decode 扫一个完整值
        for start, ch in enumerate(candidate):
            if ch not in "{[":
                continue
            try:
                value, _end = decoder.raw_decode(candidate[start:])
                return value
            except ValueError:
                continue
    raise ParseError(f"解析不出 JSON：{text[:200]}")


def _json_candidates(text: str):
    yield text
    for block in _FENCE.findall(text):
        block = block.strip()
        if block:
            yield block


def as_text(value: Any) -> str:
    """任何东西 → 干净的单行字符串。None 变空串。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(as_text(v) for v in value if v is not None).strip()
    if isinstance(value, dict):
        return as_text(list(value.values()))
    return str(value).strip()


def as_str_list(value: Any, limit: int = 0) -> list[str]:
    """单值 / 列表 / 逗号分隔的字符串统一成字符串列表，丢掉空项。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = [p.strip() for p in re.split(r"[\n;；]+", value)]
    elif isinstance(value, (list, tuple, set)):
        items = [as_text(v) for v in value]
    else:
        items = [as_text(value)]
    out = [i for i in items if i]
    return out[:limit] if limit else out


def pick(data: dict, *names: str, default: Any = None) -> Any:
    """按候选名取字段，大小写和前后缀都不敏感——模型经常把 advice 写成 Advice。"""
    if not isinstance(data, dict):
        return default
    lowered = {str(k).strip().lower().replace("-", "_"): v for k, v in data.items()}
    for name in names:
        key = name.strip().lower().replace("-", "_")
        if key in lowered and lowered[key] not in (None, "", [], {}):
            return lowered[key]
    return default


def as_int(value: Any, default: int = 0, low: int | None = None, high: int | None = None) -> int:
    """数值字段容错：字符串数字、带小数、带「分」字都能收，越界夹到范围内。"""
    if isinstance(value, bool):
        return default
    try:
        if isinstance(value, str):
            m = re.search(r"-?\d+(?:\.\d+)?", value)
            if not m:
                return default
            num = int(round(float(m.group())))
        else:
            num = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    if low is not None:
        num = max(low, num)
    if high is not None:
        num = min(high, num)
    return num


def as_float(value: Any, default: float = 0.0, low: float | None = None,
             high: float | None = None) -> float:
    """转浮点。low/high 是**归一化之后**才夹的上下限 —— 先夹再归一化会把 85 夹成 1（踩过）。"""
    if isinstance(value, bool):
        return default
    try:
        if isinstance(value, str):
            m = re.search(r"-?\d+(?:\.\d+)?", value)
            if not m:
                return default
            num = float(m.group())
        else:
            num = float(value)
    except (TypeError, ValueError):
        return default
    if low is not None:
        num = max(low, num)
    if high is not None:
        num = min(high, num)
    return num


def as_ratio(value: Any, default: float = 0.0) -> float:
    """置信度／概率：容忍 0.72、72、\"72%\"、\"85 %\" 四种写法，统一成 0–1。"""
    num = as_float(value, default=default)
    if num > 1.0:
        num = num / 100.0
    return min(1.0, max(0.0, num))


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "是", "对", "真"):
        return True
    if text in ("false", "0", "no", "n", "否", "不", "假"):
        return False
    return default


def strip_role_prefix(text: str) -> str:
    """去掉模型爱加的前缀：「我说：」「回复：」「me:」「reply_a:」「候选 1：」「A. 」。"""
    out = re.sub(
        r"^\s*(?:我(?:说|回|回复)?|回复|候选\s*\d*|reply[\s_-]*[abc\d]*|me|assistant|[abc])\s*[:：.)、]\s*",
        "", text.strip(), flags=re.I)
    # 整句被引号包起来也算前缀的一种
    out = out.strip()
    if len(out) >= 2 and out[0] in "\"'“”‘’「『" and out[-1] in "\"'“”‘’」』":
        out = out[1:-1].strip()
    return out


if __name__ == "__main__":
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('好的，结果如下：\n{"a": {"b": [1,2]}}\n希望有帮助') == {"a": {"b": [1, 2]}}
    assert extract_json('[1, 2, 3]') == [1, 2, 3]
    assert extract_json('前面 [{"x": "}"}] 后面') == [{"x": "}"}]
    # 字符串里的括号不能把平衡扫描带偏
    assert extract_json('{"s": "a{b}c", "n": 2}') == {"s": "a{b}c", "n": 2}
    for bad in ("", "   ", "完全不是 json"):
        try:
            extract_json(bad)
            raise SystemExit(f"应当抛错: {bad!r}")
        except ParseError:
            pass

    assert as_str_list(None) == [] and as_str_list("a\nb") == ["a", "b"]
    assert as_str_list([" a ", "", None, "b"]) == ["a", "b"]
    assert as_str_list(["a", "b", "c"], limit=2) == ["a", "b"]
    assert as_str_list({"k": "v"}) == ["v"]
    assert as_text(["a", "b"]) == "a b" and as_text(None) == ""
    assert pick({"Best_Reply": 1}, "best_reply") == 1
    assert pick({"advice": None}, "advice", default="d") == "d"
    assert pick({}, "x", default=7) == 7
    assert as_int("3 分") == 3 and as_int("abc", default=5) == 5
    assert as_int(11, low=0, high=10) == 10 and as_int(-3, low=0, high=10) == 0
    assert as_int(True, default=9) == 9
    assert as_float("0.62") == 0.62 and as_float(None, default=0.5) == 0.5
    assert as_float("150%", default=0.0) == 150.0
    assert as_float(150, low=0.0, high=1.0) == 1.0
    # 置信度：0.72 / 72 / "85%" 都归一到 0–1，且不因夹取而失真
    assert as_ratio(0.72) == 0.72 and as_ratio(72) == 0.72 and as_ratio("85%") == 0.85
    assert as_ratio(1.0) == 1.0 and as_ratio(0) == 0.0 and as_ratio(None, default=0.3) == 0.3
    assert as_ratio(150) == 1.0 and as_ratio(-5) == 0.0
    assert as_bool("是") and not as_bool("否") and as_bool(None, default=True)
    assert strip_role_prefix("我说：在吗") == "在吗"
    assert strip_role_prefix("reply_a: 嗨") == "嗨"
    assert strip_role_prefix("reply-b：嗨") == "嗨"
    assert strip_role_prefix("候选 2、您好") == "您好"
    assert strip_role_prefix("A. 第一条") == "第一条"
    assert strip_role_prefix("「好呀」") == "好呀"
    assert strip_role_prefix("就是单纯一句话") == "就是单纯一句话"
    print("parsing ok")
