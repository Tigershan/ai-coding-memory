"""searcher - 召回引擎（redesign §6.6）

P4 版：切到新数据模型 + 跨项目经验迁移

数据布局：
    scope_path 是 ~/.ai-memory/personal/ 或 ~/.ai-memory/projects/<dir>/
    每个 .md 文件含 frontmatter + body

策略：
    1. 当前 scope（personal + 当前 project）：全文 grep（基础分）
       memory.value 加权：high × 1.5 / medium × 1.0 / low × 0.5
       memory.source 加权：manual × 1.3 / edited × 1.2 / auto × 1.0
       potentially_superseded_by 不空：× 0.6（过期降权）
    2. 跨项目候选（其他 projects）：仅当至少有以下信号才进 Top K
       a. tags 与 query 出现的 tag 重合 ≥ 2
       b. 标题 token Jaccard 与 query > 0.3
    3. Top K 重排（默认 K=5）

性能 < 1s：纯 grep + 文件 IO + 简单算分；不调 LLM、不建索引。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

_LIB_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _LIB_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.frontmatter import parse as parse_fm  # noqa: E402
from core.paths import PERSONAL_DIR, PROJECTS_DIR  # noqa: E402
from core.project_key import _to_dir_name  # noqa: E402


# 默认值（调用方可覆盖）
DEFAULT_TOP_K = 5
DEFAULT_SNIPPET_CONTEXT = 2
DEFAULT_MAX_RESULTS_BEFORE_RERANK = 200

# 跨项目相关性阈值
CROSS_PROJECT_TAG_OVERLAP_THRESHOLD = 2
CROSS_PROJECT_TITLE_JACCARD_THRESHOLD = 0.30
CROSS_PROJECT_SCORE_PENALTY = 0.7  # 跨项目结果整体降权（弱于直接命中）

# value / source 权重
VALUE_WEIGHTS = {"high": 1.5, "medium": 1.0, "low": 0.5}
SOURCE_WEIGHTS = {"manual": 1.3, "edited": 1.2, "auto": 1.0, "bootstrap": 1.0}


def search_with_scope(
    query: str,
    scope_paths: Iterable[Path],
    *,
    current_project_key: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    snippet_context_lines: int = DEFAULT_SNIPPET_CONTEXT,
    max_results_before_rerank: int = DEFAULT_MAX_RESULTS_BEFORE_RERANK,
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
    for scope_path in scope_paths:
        scope_path = Path(scope_path)
        if not scope_path.exists():
            continue

        # 判定这条 scope_path 是否"跨项目"
        is_cross_project = (
            scope_path != PERSONAL_DIR
            and current_project_dir is not None
            and scope_path != current_project_dir
            and scope_path.parent == PROJECTS_DIR
        )

        for md_file in scope_path.rglob("*.md"):
            if len(raw) >= max_results_before_rerank:
                break
            try:
                text = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm, body = parse_fm(text)

            # 跨项目过滤：必须满足 tags 重合 或 标题相似度阈值
            if is_cross_project:
                if not _cross_project_match(q, fm, body):
                    continue

            # grep
            matches = _grep_text(q, body, context_lines=snippet_context_lines)
            if not matches:
                continue

            value_w = VALUE_WEIGHTS.get(fm.get("value", "medium"), 1.0)
            source_w = SOURCE_WEIGHTS.get(fm.get("source", "auto"), 1.0)
            superseded_w = 0.6 if fm.get("potentially_superseded_by") else 1.0
            cross_w = CROSS_PROJECT_SCORE_PENALTY if is_cross_project else 1.0
            for m in matches:
                score = m["match_count"] * value_w * source_w * superseded_w * cross_w
                raw.append({
                    "source": "fulltext",
                    "path": str(md_file),
                    "id": fm.get("id", md_file.stem),
                    "title": _extract_h1(body) or fm.get("id", ""),
                    "snippet": m["snippet"],
                    "line": m["line"],
                    "score": round(score, 2),
                    "scope_path": str(scope_path),
                    "cross_project": is_cross_project,
                    "value": fm.get("value", "medium"),
                    "source_tag": fm.get("source", "auto"),
                })

    return _rerank_and_dedupe(raw)[:top_k]


# ==================== 内部 ====================

def _cross_project_match(query: str, fm: dict, body: str) -> bool:
    """跨项目命中判定：满足任一信号才返回 True"""
    # 信号 1：tags 与 query 中出现的"词块"有 ≥ 2 个重合
    fm_tags = set(t.lower() for t in (fm.get("tags") or []) if isinstance(t, str))
    if fm_tags:
        q_words = set(re.findall(r"[a-zA-Z0-9_\-]+", query.lower()))
        overlap = fm_tags & q_words
        if len(overlap) >= CROSS_PROJECT_TAG_OVERLAP_THRESHOLD:
            return True

    # 信号 2：标题 token Jaccard 与 query > 阈值
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


def _extract_h1(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def _grep_text(query: str, text: str, *, context_lines: int) -> list[dict]:
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches: list[dict] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        hits = pattern.findall(line)
        if not hits:
            continue
        ctx_start = max(0, idx - context_lines)
        ctx_end = min(len(lines), idx + context_lines + 1)
        snippet = "\n".join(lines[ctx_start:ctx_end])
        matches.append({
            "line": idx + 1,
            "snippet": snippet,
            "match_count": len(hits),
        })
    return matches


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
