"""searcher - 召回引擎（BM25 + decay + 可选向量重排）

数据布局：
    scope_path 是 ~/.ai-memory/personal/ 或 ~/.ai-memory/projects/<dir>/
    每个 .md 文件含 frontmatter + body

策略（v2，借鉴 agentmemory 设计）：
    1. 当前 scope（personal + 当前 project）：BM25Okapi 评分
       - tokenizer 同时切 ASCII 词与 CJK bigram（mcp-server/lib/bm25_index.py）
       - memory.value 加权：high × 1.5 / medium × 1.0 / low × 0.5
       - memory.source 加权：manual × 1.3 / edited × 1.2 / auto × 1.0
       - potentially_superseded_by 不空：× 0.6
    2. 时间衰减（仅 source ∈ {auto, bootstrap}）：
       weight = max(floor, 0.5 ** (age_days / half_life))
       manual / edited 不衰减（ADR-6 人改优先）
    3. 跨项目候选（其他 projects）：仍走 tag/title 相关性把关
    4. 可选向量重排：BM25 Top N → fastembed cosine 融合

性能：BM25 索引按 scope 进程级缓存（mtime 指纹），首次构建后 < 100ms。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Iterable

_LIB_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _LIB_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.frontmatter import parse as parse_fm  # noqa: E402
from core.paths import PERSONAL_DIR, PROJECTS_DIR  # noqa: E402
from core.project_key import _to_dir_name  # noqa: E402
from core.recall_log import get_recall_counts  # noqa: E402

from . import bm25_index  # noqa: E402
from . import vector_rerank  # noqa: E402


# 默认值（调用方可覆盖）
DEFAULT_TOP_K = 5
DEFAULT_SNIPPET_CONTEXT = 2
DEFAULT_MAX_RESULTS_BEFORE_RERANK = 200
DEFAULT_HALF_LIFE_DAYS = 90
DEFAULT_DECAY_FLOOR = 0.5

# 跨项目相关性阈值
CROSS_PROJECT_TAG_OVERLAP_THRESHOLD = 2
CROSS_PROJECT_TITLE_JACCARD_THRESHOLD = 0.30
CROSS_PROJECT_SCORE_PENALTY = 0.7

# value / source 权重
VALUE_WEIGHTS = {"high": 1.5, "medium": 1.0, "low": 0.5}
SOURCE_WEIGHTS = {"manual": 1.3, "edited": 1.2, "auto": 1.0, "bootstrap": 1.0}

# 不衰减的 source（人明确动作过的笔记）
NON_DECAY_SOURCES = frozenset({"manual", "edited"})

# recall boost 参数
RECALL_BOOST_PER_HIT = 0.1   # 每次召回 +10%
RECALL_BOOST_CAP = 1.5       # 上限 +50%
RECALL_BOOST_DAYS = 30        # 统计窗口


def search_with_scope(
    query: str,
    scope_paths: Iterable[Path],
    *,
    current_project_key: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    snippet_context_lines: int = DEFAULT_SNIPPET_CONTEXT,
    max_results_before_rerank: int = DEFAULT_MAX_RESULTS_BEFORE_RERANK,
    half_life_days: int = DEFAULT_HALF_LIFE_DAYS,
    decay_floor: float = DEFAULT_DECAY_FLOOR,
    vector_rerank_enabled: bool = False,
    vector_rerank_model: str = "BAAI/bge-small-en-v1.5",
    vector_rerank_top_n: int = 50,
    vector_rerank_bm25_weight: float = 0.3,
) -> list[dict]:
    """主入口"""
    q = (query or "").strip()
    if not q:
        return []

    current_project_dir = (
        PROJECTS_DIR / _to_dir_name(current_project_key)
        if current_project_key else None
    )

    raw: list[dict] = []
    now = time.time()

    for scope_path in scope_paths:
        scope_path = Path(scope_path)
        if not scope_path.exists():
            continue

        is_cross_project = (
            scope_path != PERSONAL_DIR
            and current_project_dir is not None
            and scope_path != current_project_dir
            and scope_path.parent == PROJECTS_DIR
        )

        idx = bm25_index.get_index(scope_path)
        if idx is None:
            # 索引不可用（rank_bm25 未装 / scope 为空）：降级为旧 grep 路径
            raw.extend(
                _legacy_grep_collect(
                    q, scope_path, is_cross_project,
                    snippet_context_lines, max_results_before_rerank,
                    now=now, half_life_days=half_life_days,
                    decay_floor=decay_floor,
                )
            )
            continue

        scored = idx.scores(q)
        # BM25Okapi 在小语料下 IDF 可能为负（出现在 > 半数文档时）；先按原分排序，
        # 但对每个 scope 整体平移到 ≥ 0，避免 value/source 加权（>1）反而压低高分。
        scored.sort(key=lambda kv: -kv[1])
        if scored:
            min_s = min(s for _, s in scored)
            shift = (-min_s + 1.0) if min_s < 0 else 0.0
        else:
            shift = 0.0
        candidate_cap = int(max_results_before_rerank * 1.5)
        for md_file, bm25_score in scored[:candidate_cap]:
            # 仅把"完全无匹配 → 与 corpus 最低分相同 → 平移后 = shift（即 1.0 或 0.0）"
            # 的文档排除。这里用原始 bm25_score 是否 ≈ 0 判断
            if abs(float(bm25_score)) < 1e-9:
                continue
            adjusted_bm25 = float(bm25_score) + shift
            try:
                text = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm, body = parse_fm(text)

            # 跨项目相关性把关
            if is_cross_project and not _cross_project_match(q, fm, body):
                continue

            # 抽 snippet（grep 仅做展示，不再参与评分）
            snippet_match = _grep_snippet(q, body, context_lines=snippet_context_lines)
            if snippet_match is None:
                # 没匹配到任何字面词也展示首段（BM25 命中往往因为同义/相邻词）
                snippet_match = _head_snippet(body, snippet_context_lines * 2 + 2)

            value_w = VALUE_WEIGHTS.get(fm.get("value", "medium"), 1.0)
            source_w = SOURCE_WEIGHTS.get(fm.get("source", "auto"), 1.0)
            superseded_w = 0.6 if fm.get("potentially_superseded_by") else 1.0
            cross_w = CROSS_PROJECT_SCORE_PENALTY if is_cross_project else 1.0
            decay_w = _decay_weight(
                fm, md_file, now=now,
                half_life_days=half_life_days, floor=decay_floor,
            )

            score = adjusted_bm25 * value_w * source_w * superseded_w * cross_w * decay_w
            raw.append({
                "source": "bm25",
                "path": str(md_file),
                "id": fm.get("id", md_file.stem),
                "title": _extract_h1(body) or fm.get("id", ""),
                "snippet": snippet_match["snippet"],
                "line": snippet_match["line"],
                "score": round(score, 3),
                "bm25": round(float(bm25_score), 3),
                "decay": round(decay_w, 3),
                "scope_path": str(scope_path),
                "cross_project": is_cross_project,
                "value": fm.get("value", "medium"),
                "source_tag": fm.get("source", "auto"),
                "origin": fm.get("origin"),
            })

    deduped = _rerank_and_dedupe(raw)

    # 可选向量重排（默认关）
    if vector_rerank_enabled and deduped:
        deduped = vector_rerank.try_rerank(
            q, deduped,
            model_name=vector_rerank_model,
            top_n=vector_rerank_top_n,
            bm25_weight=vector_rerank_bm25_weight,
        )

    return deduped[:top_k]


# ==================== 时间衰减 ====================

def _decay_weight(
    fm: dict,
    md_file: Path,
    *,
    now: float,
    half_life_days: int,
    floor: float,
) -> float:
    """source ∈ {manual, edited} 不衰减；其它按半衰期衰减，高频召回获得正反馈加成。"""
    src = fm.get("source", "auto")
    if src in NON_DECAY_SOURCES:
        return 1.0
    if half_life_days <= 0:
        return 1.0
    mtime = fm.get("_mtime_at_write")
    if not isinstance(mtime, (int, float)) or mtime <= 0:
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            return 1.0
    age_days = max(0.0, (now - float(mtime)) / 86400.0)
    age_decay = 0.5 ** (age_days / float(half_life_days))

    # recall boost: 最近 30 天被命中越多，衰减越慢
    memory_id = fm.get("id", "")
    recall_counts = get_recall_counts(days=RECALL_BOOST_DAYS)
    recall_count = recall_counts.get(memory_id, 0)
    recall_boost = min(RECALL_BOOST_CAP, 1.0 + recall_count * RECALL_BOOST_PER_HIT)

    return max(float(floor), age_decay * recall_boost)


# ==================== 跨项目过滤 ====================

def _cross_project_match(query: str, fm: dict, body: str) -> bool:
    fm_tags = set(t.lower() for t in (fm.get("tags") or []) if isinstance(t, str))
    if fm_tags:
        q_words = set(re.findall(r"[a-zA-Z0-9_\-]+", query.lower()))
        overlap = fm_tags & q_words
        if len(overlap) >= CROSS_PROJECT_TAG_OVERLAP_THRESHOLD:
            return True

    title = _extract_h1(body) or ""
    jac = _token_jaccard(title.lower(), query.lower())
    if jac > CROSS_PROJECT_TITLE_JACCARD_THRESHOLD:
        return True

    return False


def _token_jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"\w+", a))
    tb = set(re.findall(r"\w+", b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ==================== Snippet 抽取 ====================

def _extract_h1(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def _grep_snippet(query: str, text: str, *, context_lines: int) -> dict | None:
    """找第一处字面命中，抽 ±context_lines 上下文。

    先尝试完整 query 匹配；失败则按空格拆词，找包含最多 query 词的行。
    """
    lines = text.splitlines()

    # 1) 完整 query 匹配
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    for idx, line in enumerate(lines):
        if pattern.search(line):
            ctx_start = max(0, idx - context_lines)
            ctx_end = min(len(lines), idx + context_lines + 1)
            return {
                "line": idx + 1,
                "snippet": "\n".join(lines[ctx_start:ctx_end]),
            }

    # 2) 按词拆分匹配：找包含最多 query 词的行
    words = [w for w in re.split(r"\s+", query.strip()) if len(w) >= 2]
    if not words:
        return None
    word_patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in words]
    best_idx = -1
    best_count = 0
    for idx, line in enumerate(lines):
        count = sum(1 for p in word_patterns if p.search(line))
        if count > best_count:
            best_count = count
            best_idx = idx
    if best_count == 0:
        return None
    ctx_start = max(0, best_idx - context_lines)
    ctx_end = min(len(lines), best_idx + context_lines + 1)
    return {
        "line": best_idx + 1,
        "snippet": "\n".join(lines[ctx_start:ctx_end]),
    }


def _head_snippet(body: str, lines_n: int) -> dict:
    """无字面命中时取 body 头部（跳过空行 / 标题）"""
    lines = body.splitlines()
    head: list[str] = []
    first_idx = 1
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if not head:
            first_idx = i + 1
        head.append(line)
        if len(head) >= lines_n:
            break
    return {"line": first_idx, "snippet": "\n".join(head)}


# ==================== 旧 grep 降级（rank_bm25 缺失时） ====================

def _legacy_grep_collect(
    query: str,
    scope_path: Path,
    is_cross_project: bool,
    snippet_context_lines: int,
    max_results_before_rerank: int,
    *,
    now: float,
    half_life_days: int,
    decay_floor: float,
) -> list[dict]:
    """rank_bm25 不可用时的兜底：纯 substring grep + 旧加权（保持 server 不挂）。"""
    out: list[dict] = []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    for md_file in scope_path.rglob("*.md"):
        if len(out) >= max_results_before_rerank:
            break
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = parse_fm(text)
        if is_cross_project and not _cross_project_match(query, fm, body):
            continue
        lines = body.splitlines()
        first: dict | None = None
        match_count = 0
        for idx, line in enumerate(lines):
            hits = pattern.findall(line)
            if not hits:
                continue
            match_count += len(hits)
            if first is None:
                cs = max(0, idx - snippet_context_lines)
                ce = min(len(lines), idx + snippet_context_lines + 1)
                first = {"line": idx + 1, "snippet": "\n".join(lines[cs:ce])}
        if first is None:
            continue

        value_w = VALUE_WEIGHTS.get(fm.get("value", "medium"), 1.0)
        source_w = SOURCE_WEIGHTS.get(fm.get("source", "auto"), 1.0)
        superseded_w = 0.6 if fm.get("potentially_superseded_by") else 1.0
        cross_w = CROSS_PROJECT_SCORE_PENALTY if is_cross_project else 1.0
        decay_w = _decay_weight(
            fm, md_file, now=now,
            half_life_days=half_life_days, floor=decay_floor,
        )
        score = match_count * value_w * source_w * superseded_w * cross_w * decay_w
        out.append({
            "source": "fulltext",
            "path": str(md_file),
            "id": fm.get("id", md_file.stem),
            "title": _extract_h1(body) or fm.get("id", ""),
            "snippet": first["snippet"],
            "line": first["line"],
            "score": round(score, 2),
            "bm25": 0.0,
            "decay": round(decay_w, 3),
            "scope_path": str(scope_path),
            "cross_project": is_cross_project,
            "value": fm.get("value", "medium"),
            "source_tag": fm.get("source", "auto"),
            "origin": fm.get("origin"),
        })
    return out


# ==================== Rerank / dedupe ====================

def _rerank_and_dedupe(results: list[dict]) -> list[dict]:
    """按 path 去重（同文件多条命中保留最高分），按 score 降序排"""
    seen: dict[str, dict] = {}
    for r in results:
        key = r["path"]
        if key not in seen or seen[key]["score"] < r["score"]:
            seen[key] = r
    return sorted(seen.values(), key=lambda x: -x["score"])


# ==================== list_topics ====================

def list_topic_files(scope_paths: Iterable[Path]) -> list[dict]:
    """列每个 scope_path 下的 memory 文件清单（带标题）"""
    out: list[dict] = []
    for scope_path in scope_paths:
        scope_path = Path(scope_path)
        if not scope_path.exists():
            continue
        scope_name = (
            scope_path.name if scope_path == PERSONAL_DIR
            else f"projects/{scope_path.name}"
        )
        for md_file in sorted(scope_path.rglob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm, body = parse_fm(text)
            title = _extract_h1(body) or fm.get("id", md_file.stem)
            out.append({
                "scope_path": str(scope_path),
                "scope_name": scope_name,
                "path": str(md_file),
                "title": title,
                "id": fm.get("id", md_file.stem),
                "value": fm.get("value", "medium"),
                "source": fm.get("source", "auto"),
            })
    return out
