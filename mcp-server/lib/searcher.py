"""searcher - 分层召回引擎（pure stdlib，无外部搜索依赖）

策略（与 design.md §8.3 对齐 + graph 增强）：
    1. _index.md 摘要召回（命中 +10 分，因为 _index 是 llm-wiki 的浓缩入口）
    2. wiki/{entities,topics,synthesis} 全文 grep 兜底
    3. 跳过 wiki/sources（噪声大，是原文素材）
    4. graph-data.json 图谱扩展：对初筛命中的实体，沿边扩展 1-2 跳邻居节点
    5. 按分数重排去重，返回 Top K（默认 5）

性能预算 < 1s：纯 grep + JSON 图遍历 + 文件 IO，不调 LLM、不建索引。

输入契约：
    query        : 用户原始查询字符串
    scope_paths  : list[Path]  来自 scope_resolver.resolve_scope().include_paths

输出：
    list[dict] - 每条结果包含：
        source     : "index" | "fulltext" | "graph"
        path       : str
        snippet    : str       命中行 ±2 行的上下文
        line       : int
        score      : int
        scope_path : str       命中所在的 scope 子库根
"""

import json
import re
from pathlib import Path
from typing import Iterable

# llm-wiki 实际生成 `index.md`（见 llm-wiki-skill/SKILL.md 工作流第 11 步、README 目录树）；
# 部分文档/历史版本写作 `_index.md`，作为兼容别名保留。
INDEX_FILE_NAME = "index.md"
INDEX_ALIAS_FILE_NAMES = ("_index.md",)
SEARCHABLE_SUBDIRS = ("entities", "topics", "synthesis", "sources")
GRAPH_DATA_FILE = "graph-data.json"

# 以下默认值仅在调用方未传入参数时使用；正常路径由 server.py 从 config_loader
# 加载 default.yml 中的 mcp_server 段，再透传到本模块的函数参数里。
DEFAULT_TOP_K = 5
DEFAULT_SNIPPET_CONTEXT = 2
DEFAULT_MAX_RESULTS_BEFORE_RERANK = 200
GRAPH_NEIGHBOR_HOPS = 1
GRAPH_NEIGHBOR_BONUS = 3
GRAPH_BRIDGE_BONUS = 5
GRAPH_COMMUNITY_BONUS = 2


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
        index_file: Path | None = None
        for candidate_name in (INDEX_FILE_NAME, *INDEX_ALIAS_FILE_NAMES):
            candidate = scope_path / candidate_name
            if candidate.exists():
                index_file = candidate
                break
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

        # 第 2 层：分层目录全文检索（wiki/entities|topics|synthesis）
        wiki_root = scope_path / "wiki"
        search_root = wiki_root if wiki_root.exists() else scope_path
        hit_entity_names: set[str] = set()

        for sub in SEARCHABLE_SUBDIRS:
            sub_dir = search_root / sub
            if not sub_dir.exists():
                continue
            for md_file in sub_dir.rglob("*.md"):
                if len(raw) >= max_results_before_rerank:
                    break
                for m in _grep_in_file(q, md_file, context_lines=snippet_context_lines):
                    raw.append({
                        "source": "fulltext",
                        "path": str(md_file),
                        "snippet": m["snippet"],
                        "line": m["line"],
                        "score": m["match_count"],
                        "scope_path": str(scope_path),
                    })
                    hit_entity_names.add(md_file.stem)

        # 第 3 层：graph 图谱扩展（沿边扩展命中实体的邻居）
        if hit_entity_names:
            graph_results = _graph_expand(
                scope_path, search_root, hit_entity_names, q
            )
            raw.extend(graph_results)

    return _rerank_and_dedupe(raw)[:top_k]


def _graph_expand(
    scope_path: Path,
    search_root: Path,
    hit_entity_names: set[str],
    query: str,
) -> list[dict]:
    """利用 graph-data.json 扩展检索：沿边找到命中实体的邻居节点

    策略：
    - 加载 graph-data.json
    - 找到与 hit_entity_names 匹配的节点
    - 沿边扩展 1 跳邻居
    - 对 bridge 节点额外加分
    - 对同社区节点额外加分
    - 返回邻居节点对应的 wiki 文件片段
    """
    graph_path = search_root / GRAPH_DATA_FILE
    if not graph_path.exists():
        graph_path = scope_path / "wiki" / GRAPH_DATA_FILE
    if not graph_path.exists():
        return []

    graph = _load_graph_data(graph_path)
    if not graph:
        return []

    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    insights = graph.get("insights", {})

    # 找到命中节点的 ID
    hit_node_ids: set[str] = set()
    for node_id, node in nodes_by_id.items():
        node_label = node.get("label", "")
        if node_id in hit_entity_names or node_label in hit_entity_names:
            hit_node_ids.add(node_id)

    if not hit_node_ids:
        return []

    # 收集命中节点的社区
    hit_communities: set[str] = set()
    for node_id in hit_node_ids:
        node = nodes_by_id.get(node_id)
        if node and node.get("community"):
            hit_communities.add(node["community"])

    # 找到 bridge 节点 ID 集合
    bridge_node_ids: set[str] = set()
    for bridge in insights.get("bridge_nodes", []):
        bridge_node_ids.add(bridge.get("id", ""))

    # 沿边扩展 1 跳邻居
    neighbor_ids: set[str] = set()
    for edge in edges:
        from_id = edge.get("from", "")
        to_id = edge.get("to", "")
        if from_id in hit_node_ids and to_id not in hit_node_ids:
            neighbor_ids.add(to_id)
        elif to_id in hit_node_ids and from_id not in hit_node_ids:
            neighbor_ids.add(from_id)

    # 为邻居节点生成结果
    results: list[dict] = []
    for neighbor_id in neighbor_ids:
        neighbor = nodes_by_id.get(neighbor_id)
        if not neighbor:
            continue

        # 计算加分
        score = GRAPH_NEIGHBOR_BONUS
        if neighbor_id in bridge_node_ids:
            score += GRAPH_BRIDGE_BONUS
        neighbor_community = neighbor.get("community", "")
        if neighbor_community and neighbor_community in hit_communities:
            score += GRAPH_COMMUNITY_BONUS

        # 尝试找到对应的 wiki 文件
        node_type = neighbor.get("type", "entity")
        subdir_map = {"entity": "entities", "topic": "topics", "source": "sources"}
        subdir = subdir_map.get(node_type, "entities")
        label = neighbor.get("label", neighbor_id)

        candidate_paths = [
            search_root / subdir / f"{label}.md",
            search_root / subdir / f"{neighbor_id}.md",
        ]
        found_path: Path | None = None
        for candidate in candidate_paths:
            if candidate.exists():
                found_path = candidate
                break

        if found_path:
            # 读取文件开头作为 snippet
            snippet = _read_file_head(found_path, max_lines=5)
            results.append({
                "source": "graph",
                "path": str(found_path),
                "snippet": f"[图谱关联] {snippet}",
                "line": 1,
                "score": score,
                "scope_path": str(scope_path),
            })
        else:
            # 没有对应文件，但图谱数据中有 content
            content = neighbor.get("content", "")
            if content:
                snippet = content[:300]
                results.append({
                    "source": "graph",
                    "path": f"graph:{neighbor_id}",
                    "snippet": f"[图谱关联] {snippet}",
                    "line": 0,
                    "score": score,
                    "scope_path": str(scope_path),
                })

    return results


def _load_graph_data(graph_path: Path) -> dict | None:
    """加载 graph-data.json，失败返回 None"""
    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _read_file_head(file_path: Path, max_lines: int = 5) -> str:
    """读取文件开头几行作为摘要"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line.rstrip())
            return "\n".join(lines)
    except (OSError, UnicodeDecodeError):
        return ""


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
    """list_topics 工具的底层实现：列出每个 scope 的 topics/ 文件清单"""
    out: list[dict] = []
    for scope_path in scope_paths:
        scope_path = Path(scope_path)
        wiki_root = scope_path / "wiki"
        topics_dir = (wiki_root / "topics") if wiki_root.exists() else (scope_path / "topics")
        if not topics_dir.exists():
            continue
        for md_file in sorted(topics_dir.rglob("*.md")):
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
