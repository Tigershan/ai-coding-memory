"""searcher - 召回引擎（pure stdlib，无外部依赖）

[NOTE] P0 减法版：
    - 砍掉了 graph_data.json 图谱扩展（ADR-3）
    - 仍保留 wiki/{entities,topics,synthesis} 兼容扫描，给 P0/P1 过渡期使用
    - P4 召回升级时会全面切换到新数据模型 (~/.ai-memory/{personal,projects/<key>}/*.md)
      并加跨项目相关性、SQLite FTS5 阶梯演进 (§6.6)

策略：
    1. _index.md / index.md 摘要召回（命中 +10 分）
    2. 子目录 .md 全文 grep
    3. 按 (path, line) 去重 + score 降序，截 Top K（默认 5）

性能预算 < 1s：纯 grep + 文件 IO，不调 LLM、不建索引。

输入契约：
    query        : 用户原始查询字符串
    scope_paths  : list[Path]  来自 scope_resolver.resolve_scope().include_paths

输出：
    list[dict] - 每条结果包含：
        source     : "index" | "fulltext"
        path       : str
        snippet    : str       命中行 ±2 行的上下文
        line       : int
        score      : int
        scope_path : str       命中所在的 scope 子库根
"""

import re
from pathlib import Path
from typing import Iterable

# 兼容 llm-wiki 历史命名（index.md / _index.md）。新数据模型也用 _index.md。
INDEX_FILE_NAMES = ("index.md", "_index.md")
# 旧 llm-wiki 子目录（过渡期兼容）；新数据模型直接扫 scope_path 下的 *.md
LEGACY_SUBDIRS = ("entities", "topics", "synthesis")

# 默认值，调用方未传时用
DEFAULT_TOP_K = 5
DEFAULT_SNIPPET_CONTEXT = 2
DEFAULT_MAX_RESULTS_BEFORE_RERANK = 200


def search_with_scope(
    query: str,
    scope_paths: Iterable[Path],
    top_k: int = DEFAULT_TOP_K,
    snippet_context_lines: int = DEFAULT_SNIPPET_CONTEXT,
    max_results_before_rerank: int = DEFAULT_MAX_RESULTS_BEFORE_RERANK,
) -> list[dict]:
    """在多个 scope 路径下检索并合并结果"""
    q = (query or "").strip()
    if not q:
        return []

    raw: list[dict] = []
    for scope_path in scope_paths:
        scope_path = Path(scope_path)
        if not scope_path.exists():
            continue

        # 第 1 层：索引摘要召回（高分 +10）
        index_file = _find_index_file(scope_path)
        if index_file is not None:
            for m in _grep_in_file(q, index_file, context_lines=snippet_context_lines):
                raw.append({
                    "source": "index",
                    "path": str(index_file),
                    "snippet": m["snippet"],
                    "line": m["line"],
                    "score": 10 + m["match_count"],
                    "scope_path": str(scope_path),
                })

        # 第 2 层：全文 grep
        # 兼容两种数据布局：
        #   a) 旧 llm-wiki：scope_path/wiki/{entities,topics,synthesis}/*.md
        #   b) 新 redesign：scope_path/*.md（personal/ 或 projects/<key>/）
        wiki_root = scope_path / "wiki"
        if wiki_root.exists():
            for sub in LEGACY_SUBDIRS:
                sub_dir = wiki_root / sub
                if not sub_dir.exists():
                    continue
                _grep_in_dir(q, sub_dir, scope_path, raw, snippet_context_lines,
                             max_results_before_rerank)
        else:
            _grep_in_dir(q, scope_path, scope_path, raw, snippet_context_lines,
                         max_results_before_rerank)

    return _rerank_and_dedupe(raw)[:top_k]


def _find_index_file(scope_path: Path) -> Path | None:
    for name in INDEX_FILE_NAMES:
        candidate = scope_path / name
        if candidate.exists():
            return candidate
    return None


def _grep_in_dir(
    query: str,
    search_dir: Path,
    scope_path: Path,
    raw: list[dict],
    context_lines: int,
    max_results: int,
) -> None:
    """递归 grep search_dir 下所有 *.md，命中追加到 raw（in-place）"""
    for md_file in search_dir.rglob("*.md"):
        if len(raw) >= max_results:
            return
        # 跳过 index 文件，它已经在第 1 层被独立扫过
        if md_file.name in INDEX_FILE_NAMES:
            continue
        for m in _grep_in_file(query, md_file, context_lines=context_lines):
            raw.append({
                "source": "fulltext",
                "path": str(md_file),
                "snippet": m["snippet"],
                "line": m["line"],
                "score": m["match_count"],
                "scope_path": str(scope_path),
            })


def _grep_in_file(
    query: str,
    file_path: Path,
    context_lines: int = DEFAULT_SNIPPET_CONTEXT,
) -> list[dict]:
    """大小写不敏感 grep，返回 [{line, snippet, match_count}, ...]"""
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = text.splitlines()
    matches: list[dict] = []
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
    """按 (path, line) 去重，按 score 降序排"""
    seen: dict[tuple[str, int], dict] = {}
    for r in results:
        key = (r["path"], r["line"])
        if key not in seen or seen[key]["score"] < r["score"]:
            seen[key] = r
    return sorted(seen.values(), key=lambda x: -x["score"])


def list_topic_files(scope_paths: Iterable[Path]) -> list[dict]:
    """list_topics 工具的底层实现：列出每个 scope 的 .md 文件清单"""
    out: list[dict] = []
    for scope_path in scope_paths:
        scope_path = Path(scope_path)
        if not scope_path.exists():
            continue
        # 兼容两种布局
        wiki_topics = scope_path / "wiki" / "topics"
        scan_root = wiki_topics if wiki_topics.exists() else scope_path
        for md_file in sorted(scan_root.rglob("*.md")):
            if md_file.name in INDEX_FILE_NAMES:
                continue
            title = _extract_title(md_file)
            out.append({
                "scope_path": str(scope_path),
                "scope_name": scope_path.name,
                "path": str(md_file),
                "title": title,
            })
    return out


def _extract_title(file_path: Path) -> str:
    """从 markdown 提取首个 H1，找不到就用 stem"""
    try:
        with open(file_path, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return file_path.stem
