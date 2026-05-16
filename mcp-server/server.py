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

from lib import config_loader, lazy_trigger, scope_resolver, searcher, workspace_detector  # noqa: E402
from lib.paths_ext import DATA_ROOT, WIKI_ROOT  # noqa: E402

# 让 core.* 可导入（项目根目录）
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import agents_md_sync, memory_store as ms, task_pack  # noqa: E402
from core.memory_store import Memory  # noqa: E402
from core.project_key import resolve_project_key  # noqa: E402

# Load config once at startup (repo default.yml -> user override -> AI_MEMORY_* env)
_CFG = config_loader.load_config()


# ==================== 常量 ====================

MAX_PAGE_BYTES = _CFG.max_page_bytes      # read_page 单文件最大返回字节数（来自 default.yml）
MAX_SNIPPET_LEN = _CFG.max_snippet_len    # search 结果中单 snippet 最大字符数（来自 default.yml）

mcp = FastMCP(
    name="ai-coding-memory",
    instructions=(
        "Personal coding-memory recall over a layered local wiki "
        "(projects/<X> + general/<Z>). "
        "This user has a personal knowledge base containing their past coding experiences, "
        "project-specific decisions, debugging history, and architecture designs. "
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
def search_memory(query: str, scope: str = "auto", workspace: str = None) -> str:
    """搜索用户的个人编码知识库（按 workspace 自动分层召回 + 跨项目经验迁移）。

    TRIGGER（满足任一即调用）：
        1. 用户提及回顾性表述：「以前」「上次」「之前」「我记得」「我做过」等
        2. 用户询问特定项目/技术栈的配置、接口、决策、踩坑等
           （即使没说"以前"，只要明显是用户特有经验，就该召回）
        3. 用户在新项目里问看似通用的问题（跨项目经验可能命中）
        4. 不确定时优先调用 — 搜索代价极低（<1s）

    DON'T TRIGGER：纯粹的通用编程知识问题（如「Java HashMap 是什么」「for 循环怎么写」）

    参数：
        query     : 用户的自然语言查询（直接传他原话即可）
        scope     : 召回范围
            - "auto" (默认)       personal + 当前 project + 跨项目高相关
            - "current_project"   仅当前 project
            - "personal"          仅 personal（跨项目通用记忆）
            - "all"               整个知识库
        workspace : IDE 当前打开的工作区绝对路径（强烈推荐传入！）
                    未传则尝试用环境变量 / CWD 兜底，可能不准确

    返回：Markdown 格式的 Top 5 召回结果（含 path / score / snippet）。
    """
    if workspace:
        effective_ws = workspace
        ws_source = "param"
    else:
        ws_info = workspace_detector.detect_workspace()
        effective_ws = ws_info["workspace_path"]
        ws_source = ws_info["source"]
    scope_info = scope_resolver.resolve_scope(effective_ws, mode=scope)
    results = searcher.search_with_scope(
        query,
        scope_info["include_paths"],
        current_project_key=scope_info.get("project_key"),
        top_k=_CFG.top_k,
        snippet_context_lines=_CFG.snippet_context_lines,
        max_results_before_rerank=_CFG.max_results_before_rerank,
    )

    project_key = scope_info.get("project_key")
    header_lines = [
        f"**workspace**: `{effective_ws or '(unknown)'}` (via {ws_source})",
        f"**scope**: `{scope_info['mode']}` "
        f"→ {len(scope_info['include_paths'])} path(s)"
        + (f", project_key=`{project_key}`" if project_key else ""),
    ]
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
def list_topics(scope: str = "auto", workspace: str = None) -> str:
    """列出知识库主题清单（仅在用户主动询问时调用）。

    TRIGGER：用户问「我的知识库里有哪些主题」「列一下你能召回的内容」「show me topics」
            等明确的盘点意图时。

    DON'T TRIGGER：用户在编码或提问，没有主动盘点知识库的意图时。
            （平时编码场景应使用 search_memory 而非 list_topics，避免输出过长。）

    参数：
        scope     : 与 search_memory 相同（auto / current_project / personal / all）
        workspace : IDE 当前打开的工作区路径（推荐传入）

    返回：按 scope 分组的主题清单（每条含路径 + H1 标题）。
    """
    if workspace:
        effective_ws = workspace
    else:
        ws_info = workspace_detector.detect_workspace()
        effective_ws = ws_info["workspace_path"]
    scope_info = scope_resolver.resolve_scope(effective_ws, mode=scope)
    items = searcher.list_topic_files(scope_info["include_paths"])

    if not items:
        return (
            f"_当前 scope (`{scope_info['mode']}`) 下尚无 topic 文件_\n\n"
            f"workspace: `{effective_ws or '(unknown)'}`, "
            f"paths: {len(scope_info['include_paths'])}"
        )

    grouped: dict[str, list[dict]] = {}
    for it in items:
        grouped.setdefault(it["scope_name"], []).append(it)

    lines = [
        f"**workspace**: `{effective_ws or '(unknown)'}`  ",
        f"**scope**: `{scope_info['mode']}` "
        f"→ {len(grouped)} path(s), {len(items)} topic(s)",
        "",
    ]
    for sub_name, sub_items in grouped.items():
        lines.append(f"### 📚 {sub_name} ({len(sub_items)})")
        for it in sub_items:
            lines.append(f"- **{it['title']}** — `{it['path']}`")
        lines.append("")
    return "\n".join(lines)


# ==================== 写入组（不需要 LLM） ====================

@mcp.tool()
def remember(text: str, scope: str = "auto", tags: list[str] = None,
             workspace: str = None, value: str = "medium") -> str:
    """让用户在任意 IDE 把当前对话片段固化为 memory，立即落盘下一秒任意 IDE 可召回。

    TRIGGER（典型用户表述）：
        - "记住这个 X"
        - "这个要记下来"
        - "永远不要再这样做"
        - "以后都按 X 处理"
        - "把这个加到 memory 里"
        - "save this as a rule"

    参数：
        text      : 用户要记住的内容（原话或你（agent）总结后的精炼版本）。
                    内容首行没有 # 标题时，会自动从前 30 字生成标题。
        scope     : "auto"（推荐）/ "personal" / "project"
                    auto: workspace 在 git 仓库内 → project；否则 personal
        tags      : 3-5 个 kebab-case 关键词，用于跨项目相关性匹配
        workspace : IDE 当前打开的工作区路径（必传以保证 scope 准确）
        value     : "high" | "medium"（默认） | "low"

    返回：写入的文件路径（用户可直接打开编辑）+ memory id。
    用户体验：明确告诉用户保存在哪里，建立可控感（人改优先原则 ADR-6）。
    """
    if not text or not text.strip():
        return "❌ remember 失败：text 为空"

    # 解析 scope
    effective_scope = scope
    project_key = None
    if scope == "auto":
        if workspace:
            info = resolve_project_key(workspace)
            if info:
                project_key = info["key"]
                effective_scope = "project"
            else:
                effective_scope = "personal"
        else:
            effective_scope = "personal"
    elif scope == "project":
        if workspace:
            info = resolve_project_key(workspace)
            if info:
                project_key = info["key"]
            else:
                # scope=project 但 workspace 不在 git 中：兜底 personal
                effective_scope = "personal"
    # personal: 不需要 project_key

    # 标题提取：用户写了 # 就用第一行；否则用前 30 字
    body = text.strip()
    title = ""
    first_line = body.splitlines()[0] if body else ""
    if first_line.startswith("#"):
        title = first_line.lstrip("#").strip()
    else:
        title = first_line[:30].strip() or "memory"
        body = f"# {title}\n\n{body}"

    if value not in ("high", "medium", "low"):
        value = "medium"

    mem = Memory(
        id=ms.make_id(title),
        scope=effective_scope,
        title=title,
        body=body,
        project_key=project_key,
        source="manual",
        value=value,
        tags=[t for t in (tags or []) if isinstance(t, str)][:6],
        origin={"ide": "mcp-remember", "workspace": workspace or ""},
    )
    try:
        path = ms.save(mem)
    except Exception as e:
        return f"❌ remember 落盘失败：{e}"

    return (
        f"✓ 已记住：`{mem.id}`\n"
        f"  scope=`{mem.scope}`{', project_key=`' + project_key + '`' if project_key else ''}\n"
        f"  value=`{mem.value}` tags=`{mem.tags}`\n"
        f"  📄 文件：`{path}`\n"
        f"  💡 用户可随时 `$EDITOR` 打开此文件修改，下次 pipeline 不会覆盖（人改优先）。"
    )


@mcp.tool()
def project_context(workspace: str) -> str:
    """返回当前 project 的浓缩记忆摘要（用于 IDE 在 chat 启动时注入到 system prompt）。

    TRIGGER：
        - IDE 启动一个新 chat 时主动调一次（提前给 agent 项目背景）
        - 用户问『这个项目我有什么记下的吗』『回顾下项目知识』时
        - 不要在每次 search_memory 之前调（用 search_memory 就够，避免 context 重复）

    参数：
        workspace : IDE 当前打开的 workspace 路径（必传；用来定位 project_key）

    返回：Markdown 摘要（≤ 4KB），含 manual/edited 优先 + high value memory 列表。
    如果 workspace 不在 git 仓库中或 project 无 memory，返回提示。

    💡 服务端会同时把摘要同步到 <project_root>/AGENTS.md 等位置（ADR-11），
        不支持 MCP 的 agent 也能从那里读取。
    """
    if not workspace:
        return "❌ project_context 需要 workspace 参数"
    info = resolve_project_key(workspace)
    if not info:
        return f"_workspace `{workspace}` 不在 git 仓库中，无 project 记忆_"
    project_key = info["key"]
    summary = agents_md_sync.build_summary(project_key)
    if not summary:
        return f"_project `{project_key}` 暂无 memory（用 remember 工具或 ai-memory distill 添加）_"

    # 同步到 AGENTS.md（zero-MCP 兜底，最佳努力 —— 失败不影响本调用返回）
    try:
        written = agents_md_sync.sync_for_workspace(workspace)
        sync_hint = (
            f"\n\n_已同步到：{', '.join(str(p) for p in written)}_"
            if written else ""
        )
    except Exception:
        sync_hint = ""

    return summary + sync_hint


@mcp.tool()
def forget(memory_id: str) -> str:
    """软删除一条 memory（移到 archive/，可 restore）。

    TRIGGER：用户说「忘掉那条 X」「这条已经过时了」「archive 这个 memory」时。

    参数：
        memory_id : memory 的完整 id（如 2026-05-16-redis-evalsha-abcd）。
                    用户给出部分 id 时，请通过 search_memory 先确认完整 id。

    返回：归档路径 + 恢复命令。
    安全：不删除文件，只移到 archive/，可通过 ai-memory restore 恢复。
    """
    p = ms.archive(memory_id)
    if p is None:
        return f"❌ 未找到 memory id：`{memory_id}`"
    return (
        f"✓ 已归档：`{memory_id}`\n"
        f"  📄 archive 路径：`{p}`\n"
        f"  💡 恢复命令：`ai-memory restore {memory_id}`"
    )


# ==================== distill 任务包组（host_agent 模式专用） ====================

@mcp.tool()
def pending_distill_count() -> str:
    """返回 host_agent 模式下待蒸馏的任务包数（不调 LLM、毫秒级）。

    TRIGGER：
        - 用户说「整理今日记忆」「跑一遍 pipeline」「distill 一下」时，先调本工具看是否有待消化
        - 用户开始新 chat 时，可主动调一次；如果 > 0 应告知用户「你有 N 个待整理」
        - 用户问「有什么要整理的吗」时

    返回：人类可读的状态描述。示例：
        "有 32 个待整理任务（最早 2 小时前）" 或 "暂无待整理任务"
    """
    items = task_pack.list_pending(include_in_progress=False)
    if not items:
        in_prog = [x for x in task_pack.list_pending(include_in_progress=True)
                   if x["status"] == "in_progress"]
        if in_prog:
            return f"暂无新待整理任务；{len(in_prog)} 个任务正在消化中。"
        return "暂无待整理任务"
    n = len(items)
    oldest = items[0]
    age_min = oldest["age_seconds"] // 60
    age_desc = f"{age_min} 分钟前" if age_min < 60 else f"{age_min // 60} 小时前"
    return (
        f"📥 有 {n} 个待整理任务（最早 {age_desc}）。\n"
        f"  消化流程：\n"
        f"  1) 调 get_next_distill_task() 拿到 prompt + task_id\n"
        f"  2) 用你（宿主 agent）自己的 LLM 跑这个 prompt\n"
        f"  3) 把 YAML 结果通过 submit_distill_result(task_id, result) 提交\n"
        f"  4) 重复 1-3 直到返回 \"暂无待整理任务\""
    )


@mcp.tool()
def get_next_distill_task() -> str:
    """取下一个待蒸馏任务（原子操作，多 agent 并发安全），返回完整 prompt。

    使用步骤（loop 模式）：
      1. 调本工具 → 拿到 `task_id` 和 `prompt`
      2. 用你（宿主 agent）自己的 LLM 跑这个 prompt，得到 YAML 输出
      3. 调 submit_distill_result(task_id, result_yaml) 提交
      4. 循环直到 pending_distill_count 返回 "暂无待整理任务"

    返回格式：
        TASK_ID: <12 位 hex>
        SESSION_META: <ide / workspace / project_key 摘要>
        PROMPT_START
        <完整 prompt 内容>
        PROMPT_END

    不要把 PROMPT_START/PROMPT_END 标记之间的内容发回给 user —— 那是给你自跑用的。
    """
    task = task_pack.take_next()
    if task is None:
        return "暂无待整理任务"
    return (
        f"TASK_ID: {task['task_id']}\n"
        f"SESSION_META: ide={task.get('ide','?')} workspace={task.get('workspace','')} "
        f"project_key={task.get('project_key','null')}\n"
        f"PROMPT_START\n"
        f"{task.get('prompt','')}\n"
        f"PROMPT_END"
    )


@mcp.tool()
def submit_distill_result(task_id: str, result_yaml: str) -> str:
    """提交某个 distill 任务的 YAML 结果，服务端落盘到 memory 库。

    result_yaml 必须是 1-step distill prompt 规定的格式（外层 `topics:` 数组）。
    服务端会：
      - 按 should_keep 落盘到 personal/ projects/ 或 .cold/
      - 写完后删除 .pending/<task_id>.task.in_progress
      - 已被人手编辑（source=manual/edited）的同 ID 文件会被保护不覆盖

    返回：写入文件路径列表 + 错误（如有）。
    """
    if not task_id or not result_yaml:
        return "❌ 缺少 task_id 或 result_yaml"
    result = task_pack.submit_result(task_id, result_yaml)
    written = result.get("written") or []
    cold = result.get("cold") or []
    errors = result.get("errors") or []
    lines = [
        f"✓ submit_distill_result({task_id}):",
        f"  📝 写入 memory: {len(written)}",
    ]
    for p in written[:5]:
        lines.append(f"    - {p}")
    if cold:
        lines.append(f"  🧊 进冷存储: {len(cold)}")
        for p in cold[:3]:
            lines.append(f"    - {p}")
    if errors:
        lines.append(f"  ⚠️  错误: {len(errors)}")
        for e in errors[:3]:
            lines.append(f"    - {e}")
    return "\n".join(lines)


# ==================== 自检 / 调试 ====================

def _self_check() -> int:
    """python3 server.py --self-check：不启动 MCP，仅打印当前环境探测结果"""
    ws = workspace_detector.detect_workspace()
    scope_info = scope_resolver.resolve_scope(ws["workspace_path"], mode="auto")
    from core.paths import PERSONAL_DIR, PROJECTS_DIR
    print(json.dumps({
        "data_root": str(DATA_ROOT),
        "personal_dir_exists": PERSONAL_DIR.exists(),
        "projects_dir_exists": PROJECTS_DIR.exists(),
        "wiki_root_legacy": str(WIKI_ROOT) if WIKI_ROOT.exists() else "(none)",
        "workspace": ws,
        "scope_auto": {
            "mode": scope_info["mode"],
            "project_key": scope_info.get("project_key"),
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
        "tools": [
            # 召回组
            "search_memory", "read_page", "list_topics", "project_context",
            # 写入组
            "remember", "forget",
            # distill 任务包组（host_agent 模式）
            "pending_distill_count", "get_next_distill_task", "submit_distill_result",
        ],
        "pending_distill_count": task_pack.count_pending(),
    }, ensure_ascii=False, indent=2))
    return 0


def _maybe_trigger_lazy_distill() -> None:
    """启动时静默触发 lazy distill（失败不阻塞 MCP 启动）"""
    try:
        result = lazy_trigger.maybe_trigger_background(range_arg="yesterday")
        if result["triggered"]:
            sys.stderr.write(
                f"[ai-coding-memory] lazy distill triggered (pid={result['pid']})\n"
            )
    except Exception as e:
        # 不让 lazy trigger 失败阻塞 MCP 启动
        sys.stderr.write(f"[ai-coding-memory] lazy trigger skipped: {e}\n")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        raise SystemExit(_self_check())
    # 启动时尝试 lazy distill（fork-and-forget）
    _maybe_trigger_lazy_distill()
    # 默认：启动 MCP（FastMCP 默认 stdio 协议）
    mcp.run()
