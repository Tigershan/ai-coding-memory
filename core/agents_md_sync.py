"""core.agents_md_sync - 把项目摘要同步到 AGENTS.md（redesign §6.6.4 / ADR-11）

零 MCP 兜底通道：很多 coding agent 不支持 MCP 或用户不想配 MCP，但都会读
AGENTS.md / .claude/CLAUDE.md / .cursor/rules/。

做法：在用户文件中插入 marker 块，不破坏 marker 外的用户内容。

接口：
    build_summary(project_key, max_size=4096) -> str
        从 projects/<key>/*.md 抽取一段简短摘要（manual/edited 优先，high value 优先）
    sync_to_file(file_path, content) -> bool
        把 content 写入 file_path 的 marker 块；保留 marker 外内容
    sync_for_workspace(workspace, paths=None) -> list[Path]
        给定 workspace，自动解析 project_key 并同步到所有配置的 paths
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from . import memory_store as ms
from .memory_store import Memory
from .project_key import _to_dir_name, resolve_project_key


MARKER_START = "<!-- ai-coding-memory:start v1 -->"
MARKER_END = "<!-- ai-coding-memory:end -->"
MARKER_RE = re.compile(
    re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
    re.DOTALL,
)

DEFAULT_TARGETS = ("AGENTS.md",)
DEFAULT_MAX_SIZE = 4096


def build_summary(project_key: str, *, max_size: int = DEFAULT_MAX_SIZE) -> str:
    """从 projects/<key>/ 抽取摘要。优先使用 _compiled/overview.md（如存在）。

    回退规则（无编译层时）：
      1. source ∈ {manual, edited}
      2. value=high
      3. value=medium
    每条只取一行：- **title** — summary 或 摘要（首段 < 100 字）
    """
    from .compiler import read_compiled_overview
    compiled = read_compiled_overview(project_key)
    if compiled:
        header = (
            f"## 📚 项目知识总览 (auto-compiled by ai-coding-memory)\n\n"
            f"> 来源：~/.ai-memory/projects/{_to_dir_name(project_key)}/_compiled/overview.md\n"
            f"> Project: {project_key}\n\n"
        )
        body = header + compiled
        if len(body) > max_size:
            body = body[:max_size - 20] + "\n\n... (truncated)"
        return body

    mems = ms.list_memories(scope="project", project_key=project_key)
    if not mems:
        return ""

    manual_or_edited = [m for m in mems if m.source in ("manual", "edited")]
    high = [m for m in mems if m.source not in ("manual", "edited") and m.value == "high"]
    medium = [m for m in mems if m.source not in ("manual", "edited") and m.value == "medium"]

    lines: list[str] = []
    lines.append(f"## 📚 项目记忆摘要 (auto by ai-coding-memory)")
    lines.append("")
    lines.append(f"> 来源：~/.ai-memory/projects/{_to_dir_name(project_key)}/")
    lines.append(f"> Project: {project_key}")
    lines.append("")

    if manual_or_edited:
        lines.append("### 🖐 用户固化（manual / edited）")
        for m in manual_or_edited:
            lines.append(_summary_line(m))
        lines.append("")
    if high:
        lines.append("### ⭐ 高价值记忆")
        for m in high[:10]:
            lines.append(_summary_line(m))
        lines.append("")
    if medium:
        lines.append("### 📖 其它")
        for m in medium[:10]:
            lines.append(_summary_line(m))
        lines.append("")

    lines.append("> 完整记忆：MCP `search_memory` / `ai-memory ls` / `ai-memory show <id>`")

    body = "\n".join(lines)
    if len(body) > max_size:
        body = body[: max_size - 20] + "\n\n... (truncated)"
    return body


def sync_to_file(file_path: Path, summary_body: str) -> bool:
    """把 summary_body 写入 file_path 的 marker 块。
    保留 marker 外的用户内容。返回是否写入（无变更返回 False）。"""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    block = f"{MARKER_START}\n{summary_body}\n{MARKER_END}"

    if file_path.exists():
        original = file_path.read_text(encoding="utf-8")
        if MARKER_START in original:
            # 替换 marker 块
            new_text = MARKER_RE.sub(block, original)
        else:
            # 追加到末尾（与用户内容用 1 个空行隔开）
            sep = "" if original.endswith("\n\n") else ("\n" if original.endswith("\n") else "\n\n")
            new_text = original + sep + block + "\n"
        if new_text == original:
            return False
    else:
        new_text = block + "\n"

    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(file_path)
    return True


def sync_for_workspace(
    workspace: str | Path,
    *,
    paths: Iterable[str] | None = None,
    max_size: int = DEFAULT_MAX_SIZE,
) -> list[Path]:
    """给定 workspace：解析 project_key → 生成摘要 → 写入所有 target 路径"""
    info = resolve_project_key(workspace)
    if not info:
        return []  # 不是 git 仓库，跳过
    project_key = info["key"]
    git_root = Path(info["git_root"])

    summary = build_summary(project_key, max_size=max_size)
    if not summary:
        return []

    targets = list(paths) if paths else list(DEFAULT_TARGETS)
    written: list[Path] = []
    for rel in targets:
        target = git_root / rel
        if sync_to_file(target, summary):
            written.append(target)
    return written


# ==================== 内部 ====================

def _summary_line(m: Memory) -> str:
    """单条 memory → 一行摘要"""
    title = (m.title or m.id)[:60]
    desc = ""
    # 优先用 extra.summary（distill 输出的字段）
    if isinstance(m.extra, dict):
        desc = (m.extra.get("summary") or "").strip()
    if not desc:
        # 否则用 body 的首段（去 # 标题）
        for ln in m.body.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            desc = ln[:100]
            break
    if desc and len(desc) > 100:
        desc = desc[:97] + "..."
    return f"- **{title}** — {desc}" if desc else f"- **{title}**"
