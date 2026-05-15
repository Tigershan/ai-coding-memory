#!/usr/bin/env python3
"""server.py - MCP Server 入口（Stage 4）

向 IDE（Cursor / Aone Copilot / Qoder 等）暴露三个工具，
让 IDE 在编码时按需召回个人编码知识库。

工具列表：
    1. search_memory(query, scope="auto") -> str
    2. read_page(path)                    -> str
    3. list_topics(scope="auto")          -> str

启动方式（被 IDE 自动拉起，stdio 协议）：
    python3 server.py
环境变量（可选）：
    AI_MEMORY_DATA_ROOT   覆盖默认数据根 (~/.ai-memory)
    AI_MEMORY_WORKSPACE   强制指定当前 workspace（最高优先级）

设计原则：
    - 工具 docstring = 模型看到的描述（务必含 TRIGGER / DON'T TRIGGER）
    - 任何工具失败都返回友好文本，不抛异常（避免污染 IDE UI）
    - 性能预算 < 1s（pure stdlib grep）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让 lib 可作为模块导入（无论是 `python3 server.py` 还是 IDE 拉起）
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 安全降级：fastmcp 缺失时给出可读提示而不是 ImportError
try:
    from fastmcp import FastMCP  # type: ignore
except ImportError:
    sys.stderr.write(
        "[FATAL] fastmcp 未安装。请运行：\n"
        "    pip3 install fastmcp pyyaml\n"
        "  或在 mcp-server 目录下运行：uv sync\n"
    )
    sys.exit(2)

from lib import config_loader, scope_resolver, searcher, workspace_detector  # noqa: E402
from lib.paths_ext import DATA_ROOT, WIKI_ROOT  # noqa: E402

# Load config once at startup (repo default.yml -> user override -> AI_MEMORY_* env)
_CFG = config_loader.load_config()


# ==================== 常量 ====================

MAX_PAGE_BYTES = _CFG.max_page_bytes      # read_page 单文件最大返回字节数（来自 default.yml）
MAX_SNIPPET_LEN = _CFG.max_snippet_len    # search 结果中单 snippet 最大字符数（来自 default.yml）

mcp = FastMCP(
    name="ai-coding-memory",
    instructions=(
        "Personal coding-memory recall over a layered local wiki "
        "(projects/<X> + domains/<Y> + general/<Z>). "
        "This user has a personal knowledge base containing their past coding experiences, "
        "project-specific decisions, debugging history, architecture designs, and domain knowledge. "
        "ALWAYS call search_memory when the user's question is about a specific project, feature, "
        "bug, config, or workflow that they may have encountered before — even if they don't "
        "explicitly say 'I remember' or 'last time'. "
        "When in doubt, prefer calling search_memory — it's cheap and fast (<1s)."
    ),
)


# ==================== 内部工具 ====================

def _safe_render_search_results(results: list[dict]) -> str:
    """把 search 结果渲染为 Markdown 文本（IDE 友好）"""
    if not results:
        return "_未在你的个人知识库中找到相关条目_"
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        snippet = r.get("snippet", "")
        if len(snippet) > MAX_SNIPPET_LEN:
            snippet = snippet[:MAX_SNIPPET_LEN] + "..."
        scope_name = Path(r.get("scope_path", "")).name or "?"
        tag = "📑 index" if r.get("source") == "index" else "📄 fulltext"
        lines.append(
            f"### {i}. [{tag}] `{scope_name}` (score={r['score']})\n"
            f"**path**: `{r['path']}` (line {r['line']})\n\n"
            f"```\n{snippet}\n```"
        )
    return "\n\n".join(lines)


def _is_path_inside_wiki(p: Path) -> bool:
    """安全检查：防止 read_page 读到 wiki 之外的文件"""
    try:
        resolved = p.resolve()
        wiki_resolved = WIKI_ROOT.resolve()
        return str(resolved).startswith(str(wiki_resolved) + "/") or resolved == wiki_resolved
    except OSError:
        return False


# ==================== MCP 工具 ====================

@mcp.tool()
def search_memory(query: str, scope: str = "auto") -> str:
    """搜索用户的个人编码知识库（按当前 IDE workspace 自动分层召回）。

    TRIGGER（满足任一即调用）：
        1. 用户提及回顾性表述：「以前」「上次」「之前」「我记得」「我做过」等
        2. 用户询问当前项目特定的配置、域名、环境、接口、流程、架构决策等
           （如「预发域名是什么」「R4保存失败原因」「物料匹配接口怎么调」）
        3. 用户提问涉及项目中特定功能/模块的历史问题、bug 排查、设计方案
           （如「XX功能为什么这样设计」「那个502问题后来怎么解决的」）
        4. 用户问题包含项目专有术语（如技能名、模块名、内部系统名等）
        5. 不确定时优先调用 — 搜索代价极低（<1s），宁可搜了没结果也不要漏召回

    DON'T TRIGGER：纯粹的通用编程知识问题（如「Java HashMap 是什么」「怎么写 for 循环」）—
            这些应直接由模型自身回答，不要调用本工具。

    参数：
        query : 用户的自然语言查询（直接传他原话即可，无需改写关键词）
        scope : 召回范围
            - "auto" (默认)       项目 + 领域 + 通用 全部
            - "current_project"   仅当前项目
            - "domain"            仅当前所属领域
            - "general"           仅通用层
            - "all"               整个知识库（用于全局搜索）

    返回：Markdown 格式的 Top 5 召回结果（含路径、行号、score、上下文片段）。
    """
    ws = workspace_detector.detect_workspace()
    scope_info = scope_resolver.resolve_scope(ws["workspace_path"], mode=scope)
    results = searcher.search_with_scope(
        query,
        scope_info["include_paths"],
        top_k=_CFG.top_k,
        snippet_context_lines=_CFG.snippet_context_lines,
        max_results_before_rerank=_CFG.max_results_before_rerank,
    )

    header_lines = [
        f"**workspace**: `{ws.get('project_name') or '(unknown)'}` "
        f"(detected via {ws['source']})",
        f"**scope**: `{scope_info['mode']}` "
        f"→ {len(scope_info['include_paths'])} sub-wikis",
    ]
    if scope_info.get("domain"):
        header_lines.append(f"**domain**: `{scope_info['domain']}`")
    if scope_info["warnings"]:
        header_lines.append("⚠️ " + "; ".join(scope_info["warnings"]))

    body = _safe_render_search_results(results)
    return "\n".join(header_lines) + "\n\n---\n\n" + body


@mcp.tool()
def read_page(path: str) -> str:
    """读取知识库中某个具体页面的完整内容。

    TRIGGER：当 search_memory 返回了 Top K 结果后，模型想看某条结果的完整内容时。
            或用户直接给出一个 wiki 内的文件路径（如 entity 页、topic 页）。

    DON'T TRIGGER：路径在 ~/.ai-memory/wiki/ 之外的文件（会被安全机制拒绝）。

    参数：
        path : 知识库内的绝对路径，必须位于 ~/.ai-memory/wiki/ 子树内

    返回：文件原始 Markdown 内容（超出 60KB 时截断并提示）。
    """
    p = Path(path).expanduser()
    if not _is_path_inside_wiki(p):
        return (
            f"❌ 拒绝读取：`{p}` 不在知识库根 `{WIKI_ROOT}` 内。\n"
            "出于安全考虑，本工具只允许读取 wiki 子树。"
        )
    if not p.exists():
        return f"❌ 文件不存在：`{p}`"
    if not p.is_file():
        return f"❌ 不是文件：`{p}`"
    try:
        data = p.read_bytes()
    except OSError as e:
        return f"❌ 读取失败：{e}"

    if len(data) > MAX_PAGE_BYTES:
        text = data[:MAX_PAGE_BYTES].decode("utf-8", errors="replace")
        return (
            f"⚠️ 文件超过 {MAX_PAGE_BYTES // 1024}KB，已截断（原始 {len(data)} bytes）。\n"
            f"完整路径：`{p}`\n\n---\n\n{text}\n\n... (truncated)"
        )
    return data.decode("utf-8", errors="replace")


@mcp.tool()
def list_topics(scope: str = "auto") -> str:
    """列出知识库主题清单（仅在用户主动询问时调用）。

    TRIGGER：用户问「我的知识库里有哪些主题」「列一下你能召回的内容」「show me topics」
            等明确的盘点意图时。

    DON'T TRIGGER：用户在编码或提问，没有主动盘点知识库的意图时。
            （平时编码场景应使用 search_memory 而非 list_topics，避免输出过长。）

    参数：
        scope : 与 search_memory 相同（auto / current_project / domain / general / all）

    返回：按 scope 分组的主题清单（每条含路径 + H1 标题）。
    """
    ws = workspace_detector.detect_workspace()
    scope_info = scope_resolver.resolve_scope(ws["workspace_path"], mode=scope)
    items = searcher.list_topic_files(scope_info["include_paths"])

    if not items:
        return (
            f"_当前 scope (`{scope_info['mode']}`) 下尚无 topic 文件_\n\n"
            f"workspace: `{ws.get('project_name') or '(unknown)'}`, "
            f"sub-wikis: {len(scope_info['include_paths'])}"
        )

    grouped: dict[str, list[dict]] = {}
    for it in items:
        grouped.setdefault(it["scope_name"], []).append(it)

    lines = [
        f"**workspace**: `{ws.get('project_name') or '(unknown)'}`  ",
        f"**scope**: `{scope_info['mode']}` "
        f"→ {len(grouped)} sub-wiki(s), {len(items)} topic(s)",
        "",
    ]
    for sub_name, sub_items in grouped.items():
        lines.append(f"### 📚 {sub_name} ({len(sub_items)})")
        for it in sub_items:
            lines.append(f"- **{it['title']}** — `{it['path']}`")
        lines.append("")
    return "\n".join(lines)


# ==================== 自检 / 调试 ====================

def _self_check() -> int:
    """python3 server.py --self-check：不启动 MCP，仅打印当前环境探测结果"""
    ws = workspace_detector.detect_workspace()
    scope_info = scope_resolver.resolve_scope(ws["workspace_path"], mode="auto")
    print(json.dumps({
        "data_root": str(DATA_ROOT),
        "wiki_root": str(WIKI_ROOT),
        "wiki_root_exists": WIKI_ROOT.exists(),
        "workspace": ws,
        "scope_auto": {
            "mode": scope_info["mode"],
            "project": scope_info["project"],
            "domain": scope_info["domain"],
            "include_paths": [str(p) for p in scope_info["include_paths"]],
            "warnings": scope_info["warnings"],
        },
        "config": {
            "top_k": _CFG.top_k,
            "performance_budget_ms": _CFG.performance_budget_ms,
            "snippet_context_lines": _CFG.snippet_context_lines,
            "max_results_before_rerank": _CFG.max_results_before_rerank,
            "max_page_bytes": _CFG.max_page_bytes,
            "max_snippet_len": _CFG.max_snippet_len,
            "sources": _CFG.sources,
            "warnings": _CFG.warnings,
        },
        "tools": ["search_memory", "read_page", "list_topics"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        raise SystemExit(_self_check())
    # 默认：启动 MCP（FastMCP 默认 stdio 协议）
    mcp.run()
