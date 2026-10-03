# -*- coding: utf-8 -*-
"""知识库检索：把「当前问题」映射到最相关的 1–3 份参考文档。

纯标准库实现（BM25 + 中文 bigram），不依赖 jieba / numpy：
- 中文没有空格，用**字符 bigram** 当词项最省事，也天然覆盖「情绪价值」「依恋」这类短语；
- 拉丁字母和数字按单词切，再去掉停用词；
- 标题和各级标题加权，让「依恋」这种查询优先命中标题里带它的文档。

SKILL.md 的「按需加载」表要求默认只读 1–3 份，不批量加载整个知识库——
retrieve() 的 top_k 默认就是 3，调用方拿到的已经是裁剪过的正文。

索引在首次使用时惰性构建并缓存到 .index.json，改文档后按 mtime 自动失效。
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field

try:
    from ..paths import resource_path
except ImportError:  # 允许直接当脚本跑
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from junshi.paths import resource_path  # type: ignore

# 冻结成单文件 exe 后，这些资源在 PyInstaller 的解包目录里，必须走 resource_path
KB_DIR = resource_path("junshi", "kb")
REF_DIR = os.path.join(KB_DIR, "references")
SKILL_PATH = os.path.join(KB_DIR, "SKILL.md")
INDEX_PATH = os.path.join(KB_DIR, ".index.json")

# BM25 参数：k1 控制词频饱和，b 控制长度归一。经验值，短查询场景够用。
_K1 = 1.5
_B = 0.75
# 标题命中额外加权：标题是作者对这份文档主题最诚实的概括
_TITLE_BOOST = 3.0
_HEADING_BOOST = 1.5
# 正文只索引前这么多字符：文档都很短（最大 13KB），全量索引没有成本压力
_MAX_BODY = 20000

_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
# 中文停用词里最没信息量的一批；bigram 之后仍会剩噪声，这里在单字层面挡一层
_STOP = {
    "的", "了", "是", "在", "和", "我", "你", "他", "她", "它", "们", "这", "那", "有", "就",
    "不", "也", "都", "而", "及", "与", "或", "把", "被", "让", "给", "对", "从", "到", "为",
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "in", "on", "for", "with",
}


def tokenize(text: str) -> list[str]:
    """中文 → 字符 bigram（单字也保留一份，覆盖「爱」「气」这类单字查询）；拉丁 → 单词。"""
    text = (text or "").lower()
    tokens: list[str] = []
    for word in _LATIN.findall(text):
        if word not in _STOP and len(word) > 1:
            tokens.append(word)
    for run in _CJK.findall(text):
        if len(run) == 1:
            if run not in _STOP:
                tokens.append(run)
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg[0] in _STOP and bg[1] in _STOP:
                continue
            tokens.append(bg)
        # 单字只加不重复的一份，避免长文档被单字刷分
        for ch in set(run):
            if ch not in _STOP:
                tokens.append(ch)
    return tokens


@dataclass
class Doc:
    """一份参考文档。path 相对 references/，方便直接回给模型当引用来源。"""
    path: str
    title: str
    category: str          # knowledge | practical
    text: str              # 全文（截断到 _MAX_BODY）
    headings: list[str] = field(default_factory=list)
    length: int = 0
    tf: Counter = field(default_factory=Counter)

    def to_ref(self) -> str:
        """给模型看的载荷：带路径和标题，方便它引用来源。"""
        return f"## 参考：{self.title}\n来源：references/{self.path}\n\n{self.text.strip()}"


def _split_doc(raw: str, path: str) -> Doc:
    """Markdown → Doc。第一个 # 当标题；所有标题额外入索引。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    title = stem
    headings: list[str] = []
    body_lines: list[str] = []
    for line in raw.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            headings.append(text)
            if level == 1 and title == stem:
                title = text
            continue
        body_lines.append(line)
    text = "\n".join(body_lines).strip()[:_MAX_BODY]
    category = path.split(os.sep, 1)[0] if os.sep in path else "practical"
    return Doc(path=path, title=title, category=category, text=text, headings=headings)


def _iter_md(base: str):
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            if name.endswith(".md"):
                full = os.path.join(root, name)
                yield full, os.path.relpath(full, base)


class KnowledgeBase:
    """懒加载 + 线程安全的检索器。第一次 query 时建索引，之后都走内存。"""

    def __init__(self, ref_dir: str = REF_DIR, index_path: str = INDEX_PATH):
        self.ref_dir = ref_dir
        self.index_path = index_path
        self._lock = threading.Lock()
        self._docs: list[Doc] = []
        self._df: Counter = Counter()
        self._avg_len = 0.0
        self._stamp: tuple = ()
        self._built = False

    # ---------- 索引 ----------

    def _fingerprint(self) -> tuple:
        stamp = []
        for full, rel in _iter_md(self.ref_dir):
            try:
                stamp.append((rel, os.path.getmtime(full), os.path.getsize(full)))
            except OSError:
                continue
        return tuple(stamp)

    def _build(self) -> None:
        docs: list[Doc] = []
        for full, rel in _iter_md(self.ref_dir):
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            doc = _split_doc(raw, rel)
            # 标题和各级标题按 boost 份数进词频，等于给它们加权
            tokens = tokenize(doc.title) * int(_TITLE_BOOST)
            for h in doc.headings:
                tokens += tokenize(h) * int(_HEADING_BOOST)
            tokens += tokenize(doc.text)
            doc.tf = Counter(tokens)
            doc.length = sum(doc.tf.values()) or 1
            docs.append(doc)

        df: Counter = Counter()
        for doc in docs:
            for term in doc.tf:
                df[term] += 1
        self._docs = docs
        self._df = df
        self._avg_len = (sum(d.length for d in docs) / len(docs)) if docs else 0.0
        self._stamp = self._fingerprint()
        self._built = True
        self._write_cache()

    def _write_cache(self) -> None:
        """缓存只为省启动时间，坏了也不影响正确性——失败一律忽略。"""
        try:
            payload = {
                "stamp": [list(s) for s in self._stamp],
                "docs": [
                    {"path": d.path, "title": d.title, "category": d.category,
                     "headings": d.headings, "length": d.length,
                     "tf": dict(d.tf), "text": d.text}
                    for d in self._docs
                ],
            }
            tmp = self.index_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.index_path)
        except OSError:
            pass

    def _load_cache(self) -> bool:
        try:
            with open(self.index_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return False
        if tuple(tuple(s) for s in payload.get("stamp", ())) != self._fingerprint():
            return False
        docs = []
        for d in payload.get("docs", ()):
            doc = Doc(path=d["path"], title=d["title"], category=d.get("category", ""),
                      text=d.get("text", ""), headings=list(d.get("headings", ())))
            doc.tf = Counter(d.get("tf") or {})
            doc.length = int(d.get("length") or 1)
            docs.append(doc)
        if not docs:
            return False
        self._docs = docs
        df: Counter = Counter()
        for doc in docs:
            for term in doc.tf:
                df[term] += 1
        self._df = df
        self._avg_len = sum(d.length for d in docs) / len(docs)
        self._stamp = tuple(tuple(s) for s in payload.get("stamp", ()))
        self._built = True
        return True

    def ensure(self) -> "KnowledgeBase":
        if self._built:
            return self
        with self._lock:
            if self._built:
                return self
            if not self._load_cache():
                self._build()
        return self

    # ---------- 检索 ----------

    def docs(self) -> list[Doc]:
        return self.ensure()._docs

    def doc_count(self) -> int:
        return len(self.docs())

    def by_path(self, path: str) -> Doc | None:
        """按路径或标题取文档——SKILL.md 的对照表用中文标题指路，两种都要认。"""
        want = (path or "").strip()
        if not want:
            return None
        for doc in self.docs():
            if doc.path == want or doc.title == want or doc.path.endswith(want):
                return doc
        stem = os.path.splitext(os.path.basename(want))[0]
        for doc in self.docs():
            if os.path.splitext(os.path.basename(doc.path))[0] == stem:
                return doc
        return None

    def search(self, query: str, top_k: int = 3) -> list[tuple[Doc, float]]:
        """返回 [(doc, 分数)]，按分数降序；全 0 分时返回空列表而不是硬凑。"""
        self.ensure()
        terms = Counter(tokenize(query))
        if not terms or not self._docs:
            return []
        n = len(self._docs)
        scored: list[tuple[Doc, float]] = []
        for doc in self._docs:
            score = 0.0
            for term, qtf in terms.items():
                tf = doc.tf.get(term)
                if not tf:
                    continue
                df = self._df.get(term, 0)
                # BM25 的 IDF，加 0.5 平滑，避免 df 很大时出现负分
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = tf + _K1 * (1 - _B + _B * doc.length / (self._avg_len or 1))
                score += idf * (tf * (_K1 + 1) / denom) * (1 + math.log(qtf))
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda x: (-x[1], x[0].path))
        return scored[:max(1, top_k)]

    def retrieve(self, query: str, top_k: int = 3, max_chars: int = 12000) -> str:
        """检索并拼成可直接塞进提示词的文本块。查不到就返回空串，让调用方明确说「没有可用参考」。"""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        chunks: list[str] = []
        used = 0
        for doc, _score in hits:
            body = doc.to_ref()
            if used and used + len(body) > max_chars:
                break
            chunks.append(body)
            used += len(body)
        return "\n\n---\n\n".join(chunks)

    def relevant_titles(self, query: str, top_k: int = 3) -> list[str]:
        return [doc.title for doc, _ in self.search(query, top_k=top_k)]


_DEFAULT = KnowledgeBase()


def default_kb() -> KnowledgeBase:
    return _DEFAULT


def retrieve(query: str, top_k: int = 3) -> str:
    return _DEFAULT.retrieve(query, top_k=top_k)


def load_skill() -> str:
    """SKILL.md 全文——军师人设与工作流的唯一来源，不在这里重复一遍。"""
    try:
        with open(SKILL_PATH, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return ""
    # 去掉 YAML front-matter：那段是给 skill 加载器看的，进提示词只是噪声
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            raw = parts[2]
    return raw.strip()


if __name__ == "__main__":
    kb = default_kb()
    print(f"docs: {kb.doc_count()}")
    assert kb.doc_count() >= 40, "知识库没索引到文档"
    for q in ("我该不该主动约她", "他总是冷淡不回消息", "被家暴了怎么办"):
        hits = kb.search(q, top_k=3)
        print(f"{q!r} -> {[d.title for d, _ in hits]}")
        assert hits, f"查询 {q!r} 没有命中任何文档"
    # 标题能反查回文档
    assert kb.by_path("长期记忆与关系档案") is not None
    assert kb.by_path("references/knowledge/03-依恋理论与情绪调节.md") is not None
    # 明显无关的词不该硬凑结果
    assert kb.search("qqqqzzzzxxxx") == []
    assert len(kb.retrieve("依恋 焦虑 情绪调节")) > 200
    assert "狗头军师" in load_skill()
    print("kb ok")
