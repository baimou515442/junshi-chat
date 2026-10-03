# -*- coding: utf-8 -*-
"""长期关系档案、对话记录与语气样本（本地存储，用户可控）。

对应 SKILL.md 的「长期记忆」一节，规则照搬，不自行放宽：
- **首次明确同意后才启用**（consent=True 才写入任何东西）；
- 只保存会影响未来建议的有限字段和关键事件，**不保存整份聊天记录**；
- 每次变更向用户提示，并给出撤销方式（删除按钮 / 「清空全部」）；
- 只按当前对象召回，不跨对象外推；
- 没有记忆或写入失败时，必须明确说「没有保存」，**不能假装记得**。

存储位置默认在项目 data/ 下，可用 JUNSHI_DATA_DIR 覆盖。写入是
「临时文件 + 原子替换」，避免手机被杀进程时留下半个 JSON。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid

try:
    from ..core import parsing
except ImportError:  # 允许直接当脚本跑
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from junshi.core import parsing  # type: ignore

try:
    from ..paths import data_path
except ImportError:  # 允许直接当脚本跑
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from junshi.paths import data_path  # type: ignore

# 可写数据：冻结成 exe 后放 exe 同级的 data/，源码模式放项目根的 data/
DATA_DIR = data_path()
PROFILE_FILE = "profiles.json"
CONSENT_FILE = "consent.json"
# 单个对象的记录条数上限：手机上不需要无限增长，也避免把上下文撑爆
MAX_EVENTS_PER_PROFILE = 200
# 语气样本上限：手机上不需要无限攒，太多也会稀释提示词
MAX_TONE_SAMPLES = 200
# 对话记录里的角色。这是**真实对话**，不是 AI 生成的内容：
#   her = 对方发来的（用户粘进来的原话）
#   me  = 用户实际发出去的话（他点了「我发的是这句」）
#   assistant = 历史遗留值，只有早期版本写过，读的时候按 AI 内容处理
HISTORY_ROLES = ("her", "me", "assistant")
_ID_SAFE = re.compile(r"[^0-9a-zA-Z_-]+")


def _now() -> float:
    return time.time()


def _atomic_write(path: str, payload) -> None:
    """临时文件 + os.replace，尽量做到「要么旧内容、要么新内容」。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


class ProfileStore:
    """档案 + 同意状态 + 对话记录 + 语气样本。一个实例管一个数据目录。"""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.profile_path = os.path.join(self.data_dir, PROFILE_FILE)
        self.consent_path = os.path.join(self.data_dir, CONSENT_FILE)

    # ---------- 同意 ----------

    def consent(self) -> dict:
        data = _read_json(self.consent_path, {})
        if not isinstance(data, dict):
            data = {}
        return {"enabled": bool(data.get("enabled")),
                "updated_at": data.get("updated_at") or 0}

    def set_consent(self, enabled: bool) -> dict:
        """开启／撤销长期记忆。撤销**不删**已有数据（用户可能只是想先停），
        但会立刻停止写入；要清掉数据得显式调 clear_all()。"""
        payload = {"enabled": bool(enabled), "updated_at": _now()}
        _atomic_write(self.consent_path, payload)
        return payload

    # ---------- 档案 CRUD ----------

    def _load(self) -> list[dict]:
        data = _read_json(self.profile_path, [])
        if isinstance(data, dict):  # 兼容早期「单个对象」的写法
            data = [data] if data else []
        if not isinstance(data, list):
            return []
        return [p for p in data if isinstance(p, dict) and p.get("id")]

    def list_profiles(self) -> list[dict]:
        return sorted(self._load(), key=lambda p: p.get("updated_at") or 0, reverse=True)

    def get(self, profile_id: str) -> dict | None:
        if not profile_id:
            return None
        for p in self._load():
            if p.get("id") == profile_id:
                return p
        return None

    def save(self, payload: dict) -> dict:
        """新建或更新一个对象档案。没同意就不写，并明确抛错让上层告诉用户。"""
        if not self.consent()["enabled"]:
            raise PermissionError("还没开启长期记忆。先在「档案」页里明确同意，再保存。")
        data = payload if isinstance(payload, dict) else {}
        pid = (data.get("id") or "").strip()
        profiles = self._load()
        existing = next((p for p in profiles if p.get("id") == pid), None) if pid else None
        if existing is None:
            pid = pid or uuid.uuid4().hex[:12]
            # score 显式给 None：用户没填就是没填，绝不编一个分数出来
            profile = {"id": pid, "created_at": _now(), "events": [], "score": None}
        else:
            profile = dict(existing)
            profiles = [p for p in profiles if p.get("id") != pid]

        for field in ("codename", "label", "mbti", "status", "background", "notes", "goal"):
            if field in data:
                profile[field] = parsing.as_text(data.get(field))[:2000]
        if "score" in data:
            score = parsing.as_int(data.get("score"), default=-1, low=0, high=100)
            # -1 表示用户没填，宁可留空也不要编一个分数出来
            profile["score"] = score if score > 0 else None
        profile["updated_at"] = _now()
        profile.setdefault("events", [])
        profile.setdefault("score", None)
        profiles.append(profile)
        _atomic_write(self.profile_path, profiles)
        return profile

    def add_event(self, profile_id: str, text: str, kind: str = "note") -> dict:
        """记一条关键事件。这是「有限字段」里的关键事件，不是聊天全文。"""
        if not self.consent()["enabled"]:
            raise PermissionError("还没开启长期记忆，这条没有被保存。")
        body = parsing.as_text(text)[:1000]
        if not body:
            raise ValueError("事件内容是空的")
        profiles = self._load()
        target = next((p for p in profiles if p.get("id") == profile_id), None)
        if target is None:
            raise KeyError("找不到这个对象档案")
        events = list(target.get("events") or [])
        events.append({"at": _now(), "kind": parsing.as_text(kind) or "note", "text": body})
        target["events"] = events[-MAX_EVENTS_PER_PROFILE:]
        target["updated_at"] = _now()
        _atomic_write(self.profile_path, profiles)
        return target

    def delete(self, profile_id: str) -> bool:
        profiles = self._load()
        kept = [p for p in profiles if p.get("id") != profile_id]
        if len(kept) == len(profiles):
            return False
        _atomic_write(self.profile_path, kept)
        self._drop_history(profile_id)
        self.forget_tone(profile_id)
        return True

    def clear_all(self) -> dict:
        """一键清空：档案、同意状态、对话记录、语气样本全删。这是用户能拿到的最彻底的撤销。

        removed 列的是**清空动作覆盖到的目标**（存在的会被删掉），不是「实际删掉的文件」——
        用户关心的是「我点了一下，哪些东西没了／以后不会再有了」，而不是磁盘上原本有没有那个文件。
        """
        removed = []
        for path in (self.profile_path, self.consent_path):
            name = os.path.basename(path)
            try:
                os.remove(path)
            except OSError:
                pass  # 本来就不存在也算已清空
            removed.append(name)
        for sub in ("history", "tone"):
            sub_dir = os.path.join(self.data_dir, sub)
            if os.path.isdir(sub_dir):
                shutil.rmtree(sub_dir, ignore_errors=True)
            removed.append(f"{sub}/")
        return {"removed": removed}

    # ---------- 对话记录 ----------

    def _history_path(self, profile_id: str) -> str:
        safe = _ID_SAFE.sub("", profile_id or "default")[:40] or "default"
        return os.path.join(self.data_dir, "history", f"{safe}.json")

    def history(self, profile_id: str = "default", limit: int = 40) -> list[dict]:
        """最近的**真实对话**，按时间升序返回。

        her = 对方发来的，me = 用户实际发出去的话。
        AI 生成的候选不进这里——只有用户确认「我发的是这句」才会落成 me。
        """
        data = _read_json(self._history_path(profile_id), [])
        if not isinstance(data, list):
            return []
        rows = [r for r in data if isinstance(r, dict) and (r.get("text") or "").strip()]
        return rows[-limit:]

    def append_history(self, profile_id: str, role: str, text: str,
                       advice: dict | None = None, meta: dict | None = None) -> dict:
        """追加一条**真实对话**。role ∈ {"her", "me"}（"assistant" 只在读旧数据时出现）。

        这里存的是实际发生过的往返，不是 AI 生成的候选——
        所以 AI 给的三条草稿不会进这里，除非用户点了「我发的是这句」确认他真的发了。
        """
        body = parsing.as_text(text)[:8000]
        if not body:
            raise ValueError("内容为空")
        # 兼容早期调用方传的 user/assistant：user 当「我」，其余当对方/AI
        raw = (role or "").strip().lower()
        if raw in ("me", "user", "i", "self", "我"):
            normalized = "me"
        elif raw in ("her", "him", "ta", "other", "对方", "他", "她"):
            normalized = "her"
        else:
            normalized = "assistant" if raw == "assistant" else "her"
        path = self._history_path(profile_id)
        data = _read_json(path, [])
        if not isinstance(data, list):
            data = []
        row = {"at": _now(), "role": normalized, "text": body}
        if advice:
            row["advice"] = advice
        if meta:
            row["meta"] = meta
        data.append(row)
        # 只留最近 400 条，手机上不需要无限历史
        _atomic_write(path, data[-400:])
        return row

    def clear_history(self, profile_id: str) -> bool:
        try:
            os.remove(self._history_path(profile_id))
            return True
        except OSError:
            return False

    def _drop_history(self, profile_id: str) -> None:
        try:
            os.remove(self._history_path(profile_id))
        except OSError:
            pass


    # ---------- 语气记忆（决定「怎么说」） ----------

    def _tone_path(self, profile_id: str) -> str:
        safe = _ID_SAFE.sub("", profile_id or "default")[:40] or "default"
        return os.path.join(self.data_dir, "tone", f"{safe}.json")

    def tone_samples(self, profile_id: str = "default", limit: int = 16,
                     max_chars: int = 1200) -> list[str]:
        """「我」真实说过的话，按**近因加权**取样后返回。

        为什么要近因加权而不是直接取最后 N 条：人的说话习惯会变，
        而且同一个人对不同场景的语气也不同。近因加权能让最近的表达占更大的权重，
        同时保留一点早期的样本，避免只用最后两条就下结论。

        另外要控制总字符数——语气样本会直接进提示词，不限制会把上下文撑爆。
        """
        data = _read_json(self._tone_path(profile_id), [])
        if not isinstance(data, list):
            return []
        items = []
        for row in data:
            if isinstance(row, dict):
                text = parsing.as_text(row.get("text"))
                at = row.get("at") or 0
            else:
                text, at = parsing.as_text(row), 0
            text = text.strip()
            if text and len(text) <= 200:   # 超长的不是"说话习惯"，是作文
                items.append((text, float(at) if isinstance(at, (int, float)) else 0.0))
        if not items:
            return []
        items.sort(key=lambda x: x[1])      # 时间升序，越靠后越新

        # 近因加权：保留最近 limit 条，但确保早期也留几条（如果有）
        if len(items) > limit:
            recent = items[-limit:]
            # 从头再补 2 条老的，让样本不至于全是最近半小时
            older = items[: max(0, limit - len(recent))]
            items = (older[-2:] + recent)[-limit:]

        # 去重（按去掉空白标点后的形态），保留更短的那条（更像随口说的）
        seen: dict[str, str] = {}
        for text, _at in items:
            key = re.sub(r"[\s\W_]+", "", text).lower()
            if not key:
                continue
            if key not in seen or len(text) < len(seen[key]):
                seen[key] = text

        out: list[str] = []
        used = 0
        # 从新到旧填，保证最新的样本一定在里面
        for text in reversed(list(seen.values())):
            if used and used + len(text) > max_chars:
                continue
            out.append(text)
            used += len(text)
        return list(reversed(out))

    def remember_tone(self, profile_id: str, text: str, *, source: str = "sent") -> bool:
        """把「我」真实发出去的一句话收进语气记忆。

        注意：只该由**用户自己的话**调用，绝不把对方的原话塞进来——
        否则用户会慢慢变得像对方，这是另一种走样。
        未开启长期记忆时不写，并明确返回 False（调用方据此告诉用户"没保存"）。
        """
        if not self.consent()["enabled"]:
            return False
        body = parsing.as_text(text)[:200].strip()
        if not body:
            return False
        path = self._tone_path(profile_id)
        data = _read_json(path, [])
        if not isinstance(data, list):
            data = []
        # 同一句不重复记，只更新时间
        key = re.sub(r"[\s\W_]+", "", body).lower()
        kept = []
        for row in data:
            if not isinstance(row, dict):
                continue
            old = re.sub(r"[\s\W_]+", "", parsing.as_text(row.get("text"))).lower()
            if old == key:
                continue
            kept.append(row)
        kept.append({"at": _now(), "text": body, "source": parsing.as_text(source) or "sent"})
        _atomic_write(path, kept[-MAX_TONE_SAMPLES:])
        return True

    def forget_tone(self, profile_id: str) -> bool:
        """清掉某个对象的语气样本。"""
        try:
            os.remove(self._tone_path(profile_id))
            return True
        except OSError:
            return False

    def tone_count(self, profile_id: str = "default") -> int:
        data = _read_json(self._tone_path(profile_id), [])
        return len(data) if isinstance(data, list) else 0

    def summary(self) -> dict:
        """给界面顶部的状态条用。"""
        consent = self.consent()
        profiles = self._load()
        return {
            "consent": consent["enabled"],
            "profiles": len(profiles),
            "events": sum(len(p.get("events") or []) for p in profiles),
            "tone_samples": sum(self.tone_count(p["id"]) for p in profiles)
                            + self.tone_count("default"),
            "data_dir": self.data_dir,
        }


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = ProfileStore(tmp)
        # 未同意时不许写任何东西
        assert store.consent()["enabled"] is False
        try:
            store.save({"codename": "小美"})
            raise SystemExit("未同意就保存了")
        except PermissionError:
            pass
        try:
            store.add_event("x", "事件")
            raise SystemExit("未同意就记事件了")
        except PermissionError:
            pass
        # 语气记忆同样受门禁保护，而且未开启时要**明确返回 False**，
        # 让调用方能告诉用户「这条没保存」，而不是静默丢弃
        assert store.remember_tone("default", "在吗") is False
        assert store.tone_count("default") == 0
        assert store.tone_samples("default") == []
        assert store.list_profiles() == []

        store.set_consent(True)
        assert store.consent()["enabled"] is True
        p = store.save({"codename": "小美", "mbti": "INFJ", "score": 78,
                        "status": "ambiguity", "background": "同事介绍"})
        assert p["id"] and p["score"] == 78 and p["created_at"]
        # 分数填 0 或没填 → None，不编造分数
        assert store.save({"codename": "A", "score": 0})["score"] is None
        assert store.save({"codename": "B"})["score"] is None
        # 更新已有档案不会新建
        p2 = store.save({"id": p["id"], "mbti": "ENFP", "score": 300})
        assert p2["id"] == p["id"] and p2["mbti"] == "ENFP" and p2["score"] == 100
        assert len(store.list_profiles()) == 3

        ev = store.add_event(p["id"], "她主动约了周末", kind="event")
        assert len(ev["events"]) == 1 and ev["events"][0]["text"] == "她主动约了周末"
        try:
            store.add_event("不存在", "x")
            raise SystemExit("应当抛错")
        except KeyError:
            pass
        try:
            store.add_event(p["id"], "   ")
            raise SystemExit("应当抛错")
        except ValueError:
            pass

        # 对话记录：her/me 两档要分清；旧调用方传的 user/assistant 也要能读
        store.append_history("default", "her", "在吗")
        store.append_history("default", "me", "在")
        hist = store.history("default")
        assert len(hist) == 2, hist
        assert hist[0]["role"] == "her" and hist[0]["text"] == "在吗"
        assert hist[1]["role"] == "me" and hist[1]["text"] == "在"
        store.append_history("default", "user", "旧写法当「我」")
        assert store.history("default")[-1]["role"] == "me"
        store.append_history("default", "assistant", "旧写法当 AI")
        assert store.history("default")[-1]["role"] == "assistant"
        assert store.history("default", limit=1)[0]["role"] == "assistant"
        assert store.clear_history("default") is True
        assert store.history("default") == []
        # 路径穿越的 id 会被清洗掉，不会写到目录外
        assert ".." not in store._history_path("../../etc/passwd")
        store.append_history("../../evil", "user", "x")
        assert store.history("../../evil")  # 仍然落在 history/ 里

        assert store.delete(p["id"]) is True
        assert store.delete("不存在") is False
        assert store.get(p["id"]) is None

        # ---------- 语气记忆 ----------
        # （这一段跑的时候长期记忆已经是开启状态）
        assert store.tone_count("default") == 0
        assert store.remember_tone("default", "在吗") is True
        assert store.remember_tone("default", "行，那明天见") is True
        # 同一句不重复记
        store.remember_tone("default", "在吗")
        assert store.tone_count("default") == 2
        samples = store.tone_samples("default")
        assert "在吗" in samples and "行，那明天见" in samples
        # 空内容不记
        assert store.remember_tone("default", "   ") is False
        # 近因加权：样本很多时，最新的必须在里面
        for i in range(60):
            store.remember_tone("default", f"这是第{i}句测试话")
        got = store.tone_samples("default", limit=10)
        assert len(got) <= 10
        assert "这是第59句测试话" in got, got
        # 字符预算：不限制会把提示词撑爆
        assert sum(len(x) for x in store.tone_samples("default", limit=50, max_chars=100)) <= 200
        # 超长的不是「说话习惯」，不参与
        store.remember_tone("default", "长" * 300)
        assert all(len(x) <= 200 for x in store.tone_samples("default", limit=50))

        # 清掉某个对象的语气样本
        assert store.forget_tone("default") is True
        assert store.tone_count("default") == 0

        s = store.summary()
        assert s["consent"] is True and s["profiles"] == 2 and s["data_dir"] == tmp
        assert "tone_samples" in s
        out = store.clear_all()
        assert "profiles.json" in out["removed"] and "consent.json" in out["removed"]
        assert "tone/" in out["removed"]
        assert store.list_profiles() == [] and store.consent()["enabled"] is False
    print("profile ok")
