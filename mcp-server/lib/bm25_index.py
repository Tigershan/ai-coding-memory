"""bm25_index - 按 scope 维护 BM25 索引（进程级缓存）

替代原 searcher 的 substring grep 评分：
    - rank_bm25.BM25Okapi 提供 idf + 文档长度归一
    - tokenizer 同时切 ASCII 词与 CJK 字符 bigram，无需新依赖
    - 索引 key = (scope_path, hash([(rel_path, mtime), ...]))；
      任一文件 mtime 变化即整体重建（< 1k 文件级别开销可忽略）

公开 API：
    get_index(scope_path) -> BM25Index | None    无文件时返回 None
    BM25Index.scores(query) -> list[tuple[Path, float]]
    tokenize(text) -> list[str]                  外部测试用

降级：
    rank_bm25 缺失 → get_index 返回 None；调用方应回退到旧 grep 路径。
    （pyproject.toml 已硬依赖；此分支只为开发期容错）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

try:
    from rank_bm25 import BM25Okapi  # type: ignore
    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False


# ASCII 词 + 数字 + 下划线 / 连字符
_WORD_RE = re.compile(r"[A-Za-z0-9_\-]+")
# CJK 字符（中日韩统一表意）
_CJK_RE = re.compile(r"[一-鿿]")


def tokenize(text: str) -> list[str]:
    """切 ASCII 词 + CJK bigram。

    例如 "Redis 连接池 maxIdle" → ["redis", "maxidle", "连接", "接池", "池", ...]
    """
    if not text:
        return []
    text_low = text.lower()
    tokens: list[str] = list(_WORD_RE.findall(text_low))
    cjk_chars: list[str] = _CJK_RE.findall(text)
    # bigram；最后一个单字也作为 unigram 保留以提高短词召回
    if len(cjk_chars) == 1:
        tokens.append(cjk_chars[0])
    elif len(cjk_chars) > 1:
        tokens.extend(cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1))
        tokens.append(cjk_chars[-1])
    return tokens


# ==================== 索引 ====================

class BM25Index:
    """单 scope 下的 BM25 索引。

    documents 同序对应 paths：scores(query) 返回 [(path, score), ...]
    """

    def __init__(self, paths: list[Path], documents: list[list[str]]):
        if not _HAS_BM25:
            raise RuntimeError("rank_bm25 未安装")
        self.paths = paths
        # rank_bm25 不允许空语料；至少给一个空文档兜底
        corpus = documents if documents else [[]]
        self._bm25 = BM25Okapi(corpus)

    def scores(self, query: str) -> list[tuple[Path, float]]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.paths:
            return []
        try:
            arr = self._bm25.get_scores(q_tokens)
        except Exception:
            return []
        return list(zip(self.paths, arr))


# ==================== 进程级缓存 ====================

# key = (str(scope_path), fingerprint) → BM25Index
_cache: dict[tuple[str, int], BM25Index] = {}


def _fingerprint(scope_path: Path, files: list[Path]) -> int:
    """按 (rel_path, mtime) 的有序元组哈希：任一文件改动即指纹变化"""
    items: list[tuple[str, float]] = []
    for f in files:
        try:
            items.append((str(f.relative_to(scope_path)), f.stat().st_mtime))
        except (OSError, ValueError):
            continue
    items.sort()
    return hash(tuple(items))


def _read_body(file_path: Path) -> str:
    """读取 markdown 全文用于 BM25 语料（含 frontmatter；分词器对 yaml 不敏感）"""
    try:
        return file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def get_index(scope_path: Path) -> BM25Index | None:
    """获取 scope_path 下所有 .md 的 BM25 索引；无文件返回 None。

    rank_bm25 缺失时返回 None（调用方应回退）。
    """
    if not _HAS_BM25:
        return None
    if not scope_path.exists():
        return None
    files = sorted(scope_path.rglob("*.md"))
    if not files:
        return None
    fp = _fingerprint(scope_path, files)
    key = (str(scope_path), fp)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    documents = [tokenize(_read_body(f)) for f in files]
    idx = BM25Index(files, documents)
    # 同 scope 老指纹的缓存清掉，防止内存膨胀
    for old_key in [k for k in _cache if k[0] == str(scope_path) and k != key]:
        _cache.pop(old_key, None)
    _cache[key] = idx
    return idx


def has_bm25() -> bool:
    return _HAS_BM25
