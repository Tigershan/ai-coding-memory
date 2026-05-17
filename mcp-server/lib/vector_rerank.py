"""vector_rerank - 可选本地向量重排（默认关）

启用前提：
    1) 配置 mcp_server.vector_rerank_enabled: true
    2) pip install '.[vector]'        装 fastembed + numpy

策略：
    - 拿 BM25 阶段的 Top N 候选 → 文档向量（路径级缓存）+ query 向量
    - cosine 与归一化 BM25 线性融合：
        final = bm25_weight * bm25_norm + (1 - bm25_weight) * cosine
    - bm25_norm 按本批 min-max 缩放到 [0, 1]

降级：
    fastembed / numpy 缺失 / 模型加载失败 → 静默 no-op，候选原样返回。
    任何阶段抛错都被吞掉，不影响检索主流程。
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_LIB_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _LIB_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.paths import DATA_ROOT  # noqa: E402

CACHE_DIR = DATA_ROOT / ".cache" / "embed-index"


# ==================== 懒加载 ====================

_loaded = False
_HAS_DEPS = False
_TextEmbedding = None  # type: ignore
_np = None  # type: ignore


def _lazy_load() -> bool:
    """首次调用时尝试导入 fastembed + numpy。返回是否可用。"""
    global _loaded, _HAS_DEPS, _TextEmbedding, _np
    if _loaded:
        return _HAS_DEPS
    _loaded = True
    try:
        from fastembed import TextEmbedding  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        _HAS_DEPS = False
        return False
    _TextEmbedding = TextEmbedding
    _np = np
    _HAS_DEPS = True
    return True


# ==================== 模型 / 缓存 ====================

@dataclass
class _ModelHandle:
    model_name: str
    instance: object  # fastembed.TextEmbedding


_model_cache: dict[str, _ModelHandle] = {}


def _get_model(model_name: str) -> _ModelHandle | None:
    cached = _model_cache.get(model_name)
    if cached is not None:
        return cached
    try:
        inst = _TextEmbedding(model_name=model_name)  # type: ignore
    except Exception:
        return None
    handle = _ModelHandle(model_name=model_name, instance=inst)
    _model_cache[model_name] = handle
    return handle


def _embed_texts(model: _ModelHandle, texts: list[str]):
    """统一 embed 接口；返回 numpy 2D array (n, dim)。"""
    try:
        embeds = list(model.instance.embed(texts))  # type: ignore[attr-defined]
        return _np.asarray(embeds, dtype="float32")  # type: ignore[union-attr]
    except Exception:
        return None


def _file_emb_cache_path(model_name: str, file_path: Path, content_hash: str) -> Path:
    safe_model = model_name.replace("/", "_")
    name = f"{safe_model}__{content_hash[:16]}.npy"
    return CACHE_DIR / safe_model / name


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _load_or_compute_doc_vec(model: _ModelHandle, file_path: Path):
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    h = _hash_text(text)
    cache_path = _file_emb_cache_path(model.model_name, file_path, h)
    if cache_path.exists():
        try:
            return _np.load(cache_path)  # type: ignore[union-attr]
        except Exception:
            pass
    arr = _embed_texts(model, [text])
    if arr is None or len(arr) == 0:
        return None
    vec = arr[0]
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _np.save(cache_path, vec)  # type: ignore[union-attr]
    except OSError:
        pass
    return vec


def _cosine(a, b) -> float:
    try:
        na = float(_np.linalg.norm(a))  # type: ignore[union-attr]
        nb = float(_np.linalg.norm(b))  # type: ignore[union-attr]
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(_np.dot(a, b) / (na * nb))  # type: ignore[union-attr]
    except Exception:
        return 0.0


# ==================== 主入口 ====================

def try_rerank(
    query: str,
    candidates: list[dict],
    *,
    model_name: str,
    top_n: int,
    bm25_weight: float,
) -> list[dict]:
    """对 BM25 候选做向量重排。

    candidates: searcher 产出的 list[dict]，必须有 path / bm25 / score。
    返回新的 list[dict]，按 final_score 降序；不可用时原样返回。
    """
    if not candidates:
        return candidates
    if not _lazy_load():
        return candidates
    if len(candidates) > top_n:
        candidates = candidates[:top_n]

    model = _get_model(model_name)
    if model is None:
        return candidates

    qvec_arr = _embed_texts(model, [query])
    if qvec_arr is None or len(qvec_arr) == 0:
        return candidates
    qvec = qvec_arr[0]

    # 归一化 bm25 分到 [0, 1]
    bm25_vals = [float(c.get("bm25", 0.0)) for c in candidates]
    if bm25_vals:
        bmin, bmax = min(bm25_vals), max(bm25_vals)
        span = (bmax - bmin) or 1.0
    else:
        bmin, span = 0.0, 1.0

    out: list[dict] = []
    for c in candidates:
        path = Path(c.get("path", ""))
        if not path.exists():
            continue
        dvec = _load_or_compute_doc_vec(model, path)
        if dvec is None:
            cosine = 0.0
        else:
            cosine = _cosine(qvec, dvec)
        bm25_norm = (float(c.get("bm25", 0.0)) - bmin) / span
        final = bm25_weight * bm25_norm + (1.0 - bm25_weight) * cosine
        # 保留原始 grep / value / source 加权后的 score；新增 vector_score / final_score
        nc = dict(c)
        nc["cosine"] = round(cosine, 3)
        nc["bm25_norm"] = round(bm25_norm, 3)
        nc["final_score"] = round(final, 3)
        # 用 final_score 重排时仍写回 score 字段，方便 server 渲染层不改
        nc["score"] = nc["final_score"]
        out.append(nc)
    out.sort(key=lambda x: -x.get("final_score", 0.0))
    return out


def is_available() -> bool:
    """供 self-check / 诊断用：当前是否能跑向量重排"""
    return _lazy_load()
