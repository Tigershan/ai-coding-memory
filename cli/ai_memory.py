#!/usr/bin/env python3
"""ai-memory - 用户级 CLI

按 redesign.md §6.8 的命令清单实现 P1 范围内：
    add / edit / ls / show / archive / restore

未实现（标 TODO）的命令在调用时友好报错：
    distill / init / pending / config / stats / sync-agents-md / rebuild-index
    这些会在 P2/P3/P4/P5 phase 加入。

用法：
    ai-memory add [--scope personal|project] [--tags a,b] [--value high|medium|low]
    ai-memory edit <id-or-substring>
    ai-memory ls [--scope ...] [--project <key>] [--since YYYY-MM-DD] [--value ...]
    ai-memory show <id>
    ai-memory archive <id>
    ai-memory restore <id>
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 让 `from core import ...` 可用，无论从哪儿调用
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import memory_store as ms          # noqa: E402
from core.memory_store import Memory         # noqa: E402
from core.project_key import resolve_project_key  # noqa: E402


# ==================== 辅助 ====================

def _editor() -> str:
    return os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"


def _open_in_editor(path: Path) -> None:
    cmd = [_editor(), str(path)]
    subprocess.run(cmd, check=False)


def _read_multiline_stdin(prompt: str) -> str:
    """从 stdin 读多行（用空行结束）"""
    print(f"{prompt}（输入 EOF / 单独空行结束）：")
    lines = []
    try:
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass
    return "\n".join(lines)


def _resolve_to_memory(query: str) -> Memory | None:
    """支持完整 ID 或 ID 子串匹配（取第一个）"""
    direct = ms.find_by_id(query)
    if direct:
        return direct
    # 子串匹配
    for mem in ms.list_memories(include_archived=True):
        if query.lower() in mem.id.lower() or query.lower() in mem.title.lower():
            return mem
    return None


# ==================== 命令实现 ====================

def cmd_add(args: argparse.Namespace) -> int:
    """交互式新增 memory"""
    scope = args.scope or "personal"
    project_key = None
    if scope == "project":
        # 自动从 cwd 推 git remote
        info = resolve_project_key(args.workspace or Path.cwd())
        if not info:
            print("⚠️  当前目录不在 git 仓库中或没有 origin remote。", file=sys.stderr)
            print("   建议改用 --scope personal，或先 cd 到目标项目。", file=sys.stderr)
            return 1
        project_key = info["key"]
        print(f"  project_key = {project_key}")

    title = args.title or input("标题：").strip()
    if not title:
        print("❌ 标题不能为空", file=sys.stderr)
        return 1

    tags = []
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    body = args.body
    if not body:
        body = _read_multiline_stdin("正文（Markdown）")
    if not body.strip():
        body = f"# {title}\n"
    elif not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"

    mem = Memory(
        id=ms.make_id(title),
        scope=scope,
        title=title,
        body=body,
        project_key=project_key,
        source="manual",
        value=args.value or "medium",
        tags=tags,
    )
    path = ms.save(mem)
    print(f"✓ 已写入：{path}")
    print(f"  id={mem.id}  scope={mem.scope}  source={mem.source}  value={mem.value}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """用 $EDITOR 打开匹配的 memory 文件"""
    mem = _resolve_to_memory(args.query)
    if not mem:
        print(f"❌ 未找到匹配「{args.query}」的 memory", file=sys.stderr)
        return 1
    print(f"打开：{mem.file_path}  (id={mem.id})")
    _open_in_editor(mem.file_path)
    # 不在这里更新 _mtime_at_write —— 让 load() 下次自动检测升级 source=edited
    print(f"✓ 编辑完成。下次读入时会自动升级 source=edited（如果你确实改了内容）")
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    """列出 memory"""
    mems = ms.list_memories(
        scope=args.scope,
        project_key=args.project,
        since=args.since,
        value=args.value,
        include_archived=args.include_archived,
    )
    if not mems:
        print("（无匹配 memory）")
        return 0
    # 简洁表格输出
    print(f"{'ID':<46} {'SCOPE':<8} {'SRC':<8} {'VAL':<6} {'UPDATED':<11} TITLE")
    print("-" * 100)
    for m in mems:
        title = (m.title or "(无标题)")[:40]
        print(f"{m.id[:46]:<46} {m.scope:<8} {m.source:<8} "
              f"{m.value:<6} {m.updated:<11} {title}")
    print(f"\n共 {len(mems)} 条")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """打印一条 memory 全文"""
    mem = _resolve_to_memory(args.id)
    if not mem:
        print(f"❌ 未找到 memory：{args.id}", file=sys.stderr)
        return 1
    print(f"# 文件：{mem.file_path}\n")
    print(mem.file_path.read_text(encoding="utf-8"))
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    mem = _resolve_to_memory(args.id)
    if not mem:
        print(f"❌ 未找到 memory：{args.id}", file=sys.stderr)
        return 1
    p = ms.archive(mem.id)
    if p:
        print(f"✓ 已归档至：{p}")
        print(f"  恢复命令：ai-memory restore {mem.id}")
        return 0
    print(f"❌ 归档失败", file=sys.stderr)
    return 1


def cmd_restore(args: argparse.Namespace) -> int:
    p = ms.restore(args.id)
    if p:
        print(f"✓ 已恢复至：{p}")
        return 0
    print(f"❌ 未在 archive/.cold 中找到 {args.id}", file=sys.stderr)
    return 1


def cmd_not_implemented(args: argparse.Namespace) -> int:
    """P2-P5 phase 才实现的命令"""
    name = args._cmd_name
    phase = {
        "distill": "P2",
        "init": "P3",
        "pending": "P3",
        "config": "P3",
        "stats": "P5",
        "sync-agents-md": "P4",
        "rebuild-index": "P5",
    }.get(name, "未来 phase")
    print(f"⚠️  `ai-memory {name}` 计划在 {phase} 实现", file=sys.stderr)
    print(f"   当前 P1 范围：add / edit / ls / show / archive / restore", file=sys.stderr)
    return 2


# ==================== argparse ====================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-memory",
        description="ai-coding-memory CLI - 跨 coding agent 的个人/项目 memory",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="新增一条 memory（source=manual）")
    p_add.add_argument("--scope", choices=["personal", "project"])
    p_add.add_argument("--title")
    p_add.add_argument("--body")
    p_add.add_argument("--tags", help="逗号分隔")
    p_add.add_argument("--value", choices=["high", "medium", "low"])
    p_add.add_argument("--workspace", help="用于推断 project_key（默认 cwd）")
    p_add.set_defaults(func=cmd_add)

    # edit
    p_edit = sub.add_parser("edit", help="$EDITOR 打开匹配的 memory")
    p_edit.add_argument("query", help="完整 ID 或 ID/title 子串")
    p_edit.set_defaults(func=cmd_edit)

    # ls
    p_ls = sub.add_parser("ls", help="列出 memory")
    p_ls.add_argument("--scope", choices=["personal", "project"])
    p_ls.add_argument("--project", help="project_key 精确匹配")
    p_ls.add_argument("--since", help="YYYY-MM-DD")
    p_ls.add_argument("--value", choices=["high", "medium", "low"])
    p_ls.add_argument("--include-archived", action="store_true")
    p_ls.set_defaults(func=cmd_ls)

    # show
    p_show = sub.add_parser("show", help="打印 memory 全文")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    # archive
    p_arch = sub.add_parser("archive", help="软删除（移到 archive/）")
    p_arch.add_argument("id")
    p_arch.set_defaults(func=cmd_archive)

    # restore
    p_rst = sub.add_parser("restore", help="从 archive/.cold 恢复")
    p_rst.add_argument("id")
    p_rst.set_defaults(func=cmd_restore)

    # 占位命令（P2-P5）
    for name, helptext in [
        ("distill", "[P2] 蒸馏当日 sessions 为 memory"),
        ("init", "[P3] 首次回溯历史对话"),
        ("pending", "[P3] 看 host_agent 模式的待消化任务包"),
        ("config", "[P3] 读写 LLM mode 等用户配置"),
        ("stats", "[P5] 召回反馈 / 写入 / 采纳统计"),
        ("sync-agents-md", "[P4] 把项目摘要同步到 AGENTS.md"),
        ("rebuild-index", "[P5] 升级到 SQLite FTS5 索引"),
    ]:
        ph = sub.add_parser(name, help=helptext)
        ph.add_argument("rest", nargs=argparse.REMAINDER)
        ph.set_defaults(func=cmd_not_implemented, _cmd_name=name)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
