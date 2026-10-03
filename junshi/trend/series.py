# -*- coding: utf-8 -*-
"""关系趋势（K 线）：把聊天记录按天聚合成「每日关系指数」，画成曲线。

计算口径要说清楚，避免被当成关系质量或爱意分数：
- 每一天先算当天的**净倾向** = (双方各自倾向 - 0.5) 的均值，范围 ±0.5；
- 再乘上**当天活跃度权重** min(1, 当天条数 / 参考条数)，条数少的日子自动被压缩；
- 最后做滚动平滑，映射到 0–100 的指数。
所以指数只表示「这段时间互动的方向与力度」，**不代表感情好坏，更不是成功率**。

支持两种输入：
1. 结构化记录 [{from, text, time}]，time 可以是 Unix 秒、"2026-09-01 21:03"、日期串；
2. CSV 文本／文件，列名认 from/text/time 的常见变体（sender、speaker、内容、时间…）。
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime, timedelta

try:
    from ..core import parsing
except ImportError:  # 允许直接当脚本跑
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from junshi.core import parsing  # type: ignore

# 参考条数：一天聊到这个数量，活跃度权重就满了
_REF_MSGS = 20
# 滚动平滑窗口（天）
_SMOOTH = 3
# 内置示例走势（离线可用，不需要任何数据）
PRESET_CASES = {
    "mutual_warming": ("双向升温", [42, 45, 44, 50, 53, 57, 55, 62, 68, 72, 71, 78, 82, 85]),
    "hot_then_cool": ("热聊后降温", [55, 62, 70, 78, 84, 80, 66, 58, 50, 46, 43, 41, 40, 39]),
    "conflict_then_repair": ("冲突后修复", [62, 65, 60, 48, 35, 28, 26, 30, 38, 47, 55, 61, 66, 70]),
    "busy_but_reliable": ("忙但仍兑现", [58, 52, 45, 40, 42, 46, 50, 47, 44, 48, 53, 57, 60, 63]),
    "clear_boundary": ("明确边界后收线", [60, 64, 66, 70, 73, 76, 72, 68, 60, 52, 44, 38, 33, 30]),
}

_TIME_PATTERNS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M", "%Y/%m/%d", "%m-%d %H:%M", "%m/%d %H:%M", "%m-%d", "%m/%d",
    "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
)
# CSV 列名别名：不同导出工具的写法差别很大，这里尽量收全
_COL_ALIASES = {
    "from": ("from", "sender", "speaker", "role", "who", "user", "direction", "发送者", "说话人", "方向", "角色"),
    "text": ("text", "content", "message", "msg", "body", "内容", "消息", "文本"),
    "time": ("time", "timestamp", "date", "datetime", "created_at", "时间", "日期"),
    "name": ("name", "nickname", "display", "昵称", "姓名"),
}
_ME_WORDS = {"me", "i", "self", "我", "本人", "自己", "mine", "out", "sent", "发送"}
_HER_WORDS = {"her", "him", "ta", "对方", "他", "她", "them", "other", "in", "received", "接收"}


def parse_time(value, default: datetime | None = None) -> datetime:
    """把各种时间写法统一成 datetime。认不出来就用 default（默认现在）。"""
    fallback = default or datetime.now()
    if value is None or value == "":
        return fallback
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 1e11:  # 毫秒时间戳
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return fallback
    text = str(value).strip()
    if re.fullmatch(r"\d{10}(\.\d+)?", text):  # Unix 秒
        try:
            return datetime.fromtimestamp(float(text))
        except (OverflowError, OSError, ValueError):
            return fallback
    if re.fullmatch(r"\d{13}", text):  # Unix 毫秒
        try:
            return datetime.fromtimestamp(float(text) / 1000.0)
        except (OverflowError, OSError, ValueError):
            return fallback
    for pat in _TIME_PATTERNS:
        try:
            got = datetime.strptime(text, pat)
        except ValueError:
            continue
        if "%Y" not in pat:  # 没有年份的格式按今年补
            got = got.replace(year=fallback.year)
        return got
    return fallback


def _side(record: dict) -> str:
    """判定一条记录是谁说的：me / her / other。"""
    who = parsing.as_text(record.get("from") or record.get("role") or
                          record.get("sender") or record.get("direction")).lower()
    if who in _ME_WORDS:
        return "me"
    if who in _HER_WORDS or record.get("name"):
        return "her"
    return "other"


def _tendency(text: str) -> float:
    """一条消息的热度倾向，0–1。0.5 是中性。

    只看**可观察的表面信号**（长度、是否问句、是否带动情绪词、是否只回一个字），
    不假装读懂语气。所以它是粗糙的，图表说明里也这么写。
    """
    body = (text or "").strip()
    if not body:
        return 0.5
    score = 0.5
    length = len(body)
    if length <= 2:
        score -= 0.14          # 「嗯」「哦」「行」这类短回
    elif length <= 6:
        score -= 0.04
    elif length >= 40:
        score += 0.12          # 愿意写长
    elif length >= 18:
        score += 0.06
    if re.search(r"[?？]", body):
        score += 0.05          # 有来有回的提问
    if re.search(r"[!！~～]", body):
        score += 0.03
    if re.search(r"[哈嘿嘻呵]|笑|233|hhh", body, re.I):
        score += 0.05
    if re.search(r"想你|喜欢|爱你|在意|开心|好想|抱|晚安|早安|么么|亲亲", body):
        score += 0.12
    if re.search(r"随便|无所谓|算了|别烦|不想说|没事|哦$|嗯$", body):
        score -= 0.10
    return max(0.0, min(1.0, score))


def compute_series(records: list[dict] | str, *, ref_msgs: int = _REF_MSGS,
                   smooth: int = _SMOOTH) -> dict:
    """records → 每日指数序列。返回 {points, summary, warnings}。

    points: [{date, value, me, her, total, me_msgs, her_msgs}]
    空记录返回 points=[]，由调用方提示用户。
    """
    rows = _normalize(records)
    if not rows:
        return {"points": [], "summary": {}, "warnings": ["没有可用的记录"]}

    by_day: dict[str, dict] = {}
    undated = 0
    for rec in rows:
        when = rec.get("_dt")
        if when is None:
            undated += 1
            continue
        day = when.strftime("%Y-%m-%d")
        bucket = by_day.setdefault(day, {"me": [], "her": [], "other": []})
        bucket[rec["_side"]].append(_tendency(rec.get("text", "")))

    if not by_day:
        return {"points": [], "summary": {}, "warnings": ["记录里没有任何可识别的时间"]}

    days = sorted(by_day)
    # 补齐中间的空档：断联的那几天必须显示出来，否则曲线会骗人
    start = datetime.strptime(days[0], "%Y-%m-%d")
    end = datetime.strptime(days[-1], "%Y-%m-%d")
    if (end - start).days > 400:
        return {"points": [], "summary": {},
                "warnings": ["时间跨度超过 400 天，先按范围裁剪或检查时间列"]}

    raw: list[dict] = []
    cursor = start
    while cursor <= end:
        day = cursor.strftime("%Y-%m-%d")
        bucket = by_day.get(day) or {"me": [], "her": [], "other": []}
        me_vals, her_vals = bucket["me"], bucket["her"]
        # 只有一方说话时，另一方按「沉默」计入倾向 0.5 的下方（0.42），
        # 否则单方面输出的日子看起来会跟双向互动一样热
        me_score = sum(me_vals) / len(me_vals) if me_vals else 0.42
        her_score = sum(her_vals) / len(her_vals) if her_vals else 0.42
        total = len(me_vals) + len(her_vals)
        if total == 0:
            net = -0.18  # 完全断联的一天：明确往下压
        else:
            net = ((me_score - 0.5) + (her_score - 0.5)) / 2.0
            net *= min(1.0, total / max(1, ref_msgs))
        raw.append({"date": day, "net": net, "me_msgs": len(me_vals), "her_msgs": len(her_vals),
                    "total": total, "me": me_score, "her": her_score})
        cursor += timedelta(days=1)

    smoothed = _smooth([r["net"] for r in raw], smooth)
    points = []
    for row, net in zip(raw, smoothed):
        value = int(round(max(0.0, min(100.0, 50 + net * 100))))
        points.append({"date": row["date"], "value": value, "net": round(net, 4),
                       "me": round(row["me"], 3), "her": round(row["her"], 3),
                       "me_msgs": row["me_msgs"], "her_msgs": row["her_msgs"],
                       "total": row["total"]})

    warnings: list[str] = []
    if undated:
        warnings.append(f"{undated} 条记录没有可识别的时间，未计入")
    if len(points) < 4:
        warnings.append("跨度太短（少于 4 天），曲线只能当参考")
    if sum(p["total"] for p in points) < 12:
        warnings.append("样本太少（不足 12 条消息），不建议据此判断走势")
    return {"points": points, "summary": _summary(points), "warnings": warnings}


def _normalize(records: list[dict] | str) -> list[dict]:
    """统一成 [{from, text, _dt, _side}]。CSV 字符串先解析成记录。"""
    if isinstance(records, str):
        rows = parse_csv(records)
    elif isinstance(records, list):
        rows = [r for r in records if isinstance(r, dict)]
    else:
        return []
    out = []
    for rec in rows:
        text = parsing.as_text(rec.get("text") or rec.get("content") or rec.get("message"))
        when_raw = rec.get("time") or rec.get("timestamp") or rec.get("date") or rec.get("datetime")
        # 没有时间的记录不参与按天聚合，避免把全部记录挤到「今天」
        dt = None
        if when_raw not in (None, ""):
            dt = parse_time(when_raw, default=None) if _looks_like_time(when_raw) else None
        out.append({"from": rec.get("from") or rec.get("role") or "",
                    "text": text, "_dt": dt, "_side": _side(rec)})
    return out


def _looks_like_time(value) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    if isinstance(value, datetime):
        return True
    text = str(value or "").strip()
    if not text:
        return False
    if re.fullmatch(r"\d{10}(\.\d+)?|\d{13}", text):
        return True
    return any(_try(text, pat) for pat in _TIME_PATTERNS)


def _try(text: str, pat: str) -> bool:
    try:
        datetime.strptime(text, pat)
        return True
    except ValueError:
        return False


def _smooth(values: list[float], window: int) -> list[float]:
    """末尾对齐的滑动平均：只用当天及之前的数据，不偷看未来。"""
    if window <= 1:
        return list(values)
    out = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        chunk = values[lo:i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def _summary(points: list[dict]) -> dict:
    if not points:
        return {}
    values = [p["value"] for p in points]
    first, last = values[0], values[-1]
    peak = max(points, key=lambda p: p["value"])
    trough = min(points, key=lambda p: p["value"])
    delta = last - first
    # 滚动平滑会把整体涨幅压小，所以不能只看 delta 的绝对值：
    # 拿它跟序列自身的波动（标准差）比，再决定是「温和」还是「持平」。
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    noise = max(std, 6.0)  # 6 分以内视为噪声，别把抖动说成趋势
    if delta >= 20:
        trend = "整体明显升温"
    elif delta >= 10:
        trend = "整体在升温"
    elif delta >= 0.6 * noise and delta > 0:
        trend = "小幅升温"
    elif delta <= -20:
        trend = "整体明显降温"
    elif delta <= -10:
        trend = "整体在降温"
    elif delta <= -0.6 * noise and delta < 0:
        trend = "小幅降温"
    else:
        trend = "整体大致持平"
    return {
        "days": len(points),
        "messages": sum(p["total"] for p in points),
        "first": first, "last": last, "delta": delta,
        "trend": trend,
        "peak_date": peak["date"], "peak": peak["value"],
        "low_date": trough["date"], "low": trough["value"],
        "me_msgs": sum(p["me_msgs"] for p in points),
        "her_msgs": sum(p["her_msgs"] for p in points),
    }


def parse_csv(text_or_path: str, *, is_path: bool | None = None) -> list[dict]:
    """CSV → 记录列表。列名认常见的几种别名，认不出就按位置取前几列。

    is_path=None 时自动判断：含换行就当内容，否则先看文件是否存在。
    """
    raw = text_or_path
    if is_path is None:
        is_path = ("\n" not in (text_or_path or "")) and os.path.isfile(text_or_path or "")
    if is_path:
        with open(text_or_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            raw = fh.read()
    raw = (raw or "").lstrip("\ufeff")
    if not raw.strip():
        return []

    # 用 csv 模块解析，顺带兼容制表符和分号分隔
    sample = raw[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []

    header = [ (c or "").strip().lower() for c in rows[0] ]
    mapping: dict[str, int] = {}
    for field, aliases in _COL_ALIASES.items():
        for idx, col in enumerate(header):
            if col in aliases:
                mapping[field] = idx
                break
    has_header = bool(mapping.get("text") is not None or mapping.get("from") is not None)
    data_rows = rows[1:] if has_header else rows
    if not mapping:
        # 完全认不出表头：按 from, text, time 的顺序赌一把
        mapping = {"from": 0, "text": 1, "time": 2}

    out = []
    for row in data_rows:
        def get(field: str) -> str:
            idx = mapping.get(field)
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()
        text = get("text")
        if not text:
            continue
        out.append({"from": get("from"), "text": text, "time": get("time"),
                    "name": get("name")})
    return out


def presets() -> list[dict]:
    """内置示例走势，界面上的下拉框直接用。"""
    return [{"key": key, "label": label,
             "points": [{"date": f"D{i + 1}", "value": v} for i, v in enumerate(values)]}
            for key, (label, values) in PRESET_CASES.items()]


def preset_series(key: str) -> dict:
    entry = PRESET_CASES.get(key)
    if not entry:
        raise KeyError("没有这个示例走势")
    label, values = entry
    points = [{"date": f"D{i + 1}", "value": v, "me": 0.0, "her": 0.0,
               "me_msgs": 0, "her_msgs": 0, "total": 0} for i, v in enumerate(values)]
    return {"points": points, "summary": {"...": None, "trend": label,
                                          "days": len(points), "messages": 0},
            "warnings": ["这是内置示例走势，不是你的聊天数据"]}


if __name__ == "__main__":
    # 时间解析：各种写法都要认
    assert parse_time("2026-09-01 21:03").strftime("%Y-%m-%d %H:%M") == "2026-09-01 21:03"
    assert parse_time("2026/09/01").day == 1
    assert parse_time(1756731780).year == 2025
    assert parse_time(1756731780000).year == 2025
    assert parse_time("2026-09-01").month == 9
    assert parse_time("").year >= 2024
    assert parse_time("不是时间").year >= 2024
    assert _looks_like_time("2026-09-01") and not _looks_like_time("你好")
    assert _looks_like_time(1756731780)

    # 倾向：短回低、长回高、带情感词更高
    assert _tendency("嗯") < 0.5
    assert _tendency("今天想你了，晚上一起吃饭吗？") > 0.6
    assert _tendency("") == 0.5
    assert _tendency("随便") < 0.5
    assert 0.0 <= _tendency("x" * 200) <= 1.0

    # 结构化记录 → 曲线
    recs = []
    base = datetime(2026, 9, 1, 20, 0)
    for day in range(6):
        for i in range(6):
            recs.append({"from": "me", "text": "今天想你了，一起吃饭吗？",
                         "time": (base + timedelta(days=day, minutes=i)).strftime("%Y-%m-%d %H:%M")})
            recs.append({"from": "her", "text": "好呀，几点？" if day > 2 else "嗯",
                         "time": (base + timedelta(days=day, minutes=i + 30)).strftime("%Y-%m-%d %H:%M")})
    got = compute_series(recs)
    assert len(got["points"]) == 6, got["points"]
    assert got["summary"]["trend"] == "小幅升温", got["summary"]
    assert got["points"][0]["total"] == 12
    assert all(0 <= p["value"] <= 100 for p in got["points"])

    # 走势判读要能区分方向，而不是一律「持平」
    flat = compute_series([{"from": "me", "text": "在吗在吗在吗", "time": f"2026-09-0{i} 10:00"}
                           for i in range(1, 8)])
    assert flat["summary"]["trend"] in ("整体大致持平", "小幅升温", "小幅降温"), flat["summary"]
    cool = []
    for i in range(1, 15):
        text = "今天想你了，晚上一起吃饭吗？" if i <= 4 else "嗯"
        cool.append({"from": "her", "text": text, "time": f"2026-09-{i:02d} 20:00"})
    assert compute_series(cool)["summary"]["delta"] < 0

    # 中间断联的日子要出现在曲线上，且是低点
    gap = [{"from": "me", "text": "在吗在吗在吗", "time": "2026-09-01 10:00"},
           {"from": "her", "text": "在的，怎么了呀", "time": "2026-09-01 10:05"},
           {"from": "me", "text": "想你了", "time": "2026-09-05 10:00"}]
    g = compute_series(gap)
    dates = [p["date"] for p in g["points"]]
    assert dates == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"], dates
    assert g["points"][2]["total"] == 0
    assert g["points"][2]["value"] < g["points"][0]["value"]

    # 没有时间列的记录：一条都不该被硬塞进今天
    assert compute_series([{"from": "me", "text": "在吗"}])["points"] == []
    assert compute_series([])["points"] == []
    assert compute_series("")["points"] == []

    # CSV：表头别名、无表头、分号分隔
    csv_text = ("sender,content,time\n"
                "我,在吗,2026-09-01 20:00\n"
                "对方,在的,2026-09-01 20:01\n"
                "我,今天好累,2026-09-02 21:00\n")
    rows = parse_csv(csv_text)
    assert len(rows) == 3 and rows[0]["from"] == "我" and rows[1]["text"] == "在的"
    cs = compute_series(csv_text)
    assert len(cs["points"]) == 2 and cs["points"][0]["me_msgs"] == 1
    assert parse_csv("我,你好,2026-09-01\n")[0]["text"] == "你好"
    assert len(parse_csv("sender;content;time\n我;在吗;2026-09-01 20:00\n")) == 1
    assert parse_csv("") == []

    # 示例走势齐全
    ps = presets()
    assert len(ps) == 5 and all(len(p["points"]) >= 10 for p in ps)
    assert preset_series("mutual_warming")["points"][0]["value"] == 42
    try:
        preset_series("nope")
        raise SystemExit("应当抛错")
    except KeyError:
        pass
    print("trend ok")
