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
    print(f"❌ 未在 archive/ 中找到 {args.id}", file=sys.stderr)
    return 1


def cmd_distill(args: argparse.Namespace) -> int:
    """P2 落地：调 distill/scripts/distill.py 主入口"""
    # 直接 import 模块函数（避免子进程开销）
    from distill.scripts import distill as distill_mod  # noqa: E402

    forwarded = ["--range", args.range]
    if args.mode:
        forwarded += ["--mode", args.mode]
    if getattr(args, "mode_hint", None):
        forwarded += ["--mode-hint", args.mode_hint]
    if args.concurrency:
        forwarded += ["--concurrency", str(args.concurrency)]
    if args.dry_run:
        forwarded += ["--dry-run"]
    if args.verbose:
        forwarded += ["--verbose"]
    return distill_mod.main(forwarded)


def cmd_init(args: argparse.Namespace) -> int:
    """P3 init - 首次回溯"""
    from cli.ai_memory_init import cmd_init as _init_impl
    return _init_impl(args)


def cmd_pending(args: argparse.Namespace) -> int:
    """P3 pending - 看任务包状态"""
    from cli.ai_memory_init import cmd_pending as _pending_impl
    return _pending_impl(args)


def cmd_config(args: argparse.Namespace) -> int:
    """P3 config get/set/show"""
    from core import config as ucfg
    action = args.action
    if action == "show":
        cfg = ucfg.load_user_config()
        if not cfg:
            print(f"（{ucfg.USER_CONFIG_PATH} 不存在或为空）")
        else:
            import json
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0
    if action == "get":
        if not args.key:
            print("❌ 缺少 KEY，用法: ai-memory config get llm.mode", file=sys.stderr)
            return 1
        v = ucfg.get_value(args.key)
        if v is None:
            print(f"（{args.key} 未设置）")
            return 1
        print(v)
        return 0
    if action == "set":
        if not args.key or args.value is None:
            print("❌ 用法: ai-memory config set llm.mode api", file=sys.stderr)
            return 1
        # 简易类型推断
        val: object = args.value
        if val == "true":
            val = True
        elif val == "false":
            val = False
        elif val == "null":
            val = None
        else:
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
        p = ucfg.set_value(args.key, val)
        print(f"✓ {args.key} = {val}")
        print(f"  写入: {p}")
        return 0
    return 1


def cmd_stats(args: argparse.Namespace) -> int:
    """P5 - 写入 / 召回 / 采纳统计"""
    from core import memory_store as ms
    from core import recall_log
    from core import task_pack
    from core.paths import ARCHIVE_DIR, LOG_DIR

    # 1. memory 总量
    all_mems = ms.list_memories(include_archived=False)
    by_scope: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_value: dict[str, int] = {}
    with_conflicts = 0
    with_superseded = 0
    for m in all_mems:
        by_scope[m.scope] = by_scope.get(m.scope, 0) + 1
        by_source[m.source] = by_source.get(m.source, 0) + 1
        by_value[m.value] = by_value.get(m.value, 0) + 1
        if m.potential_conflicts:
            with_conflicts += 1
        if m.potentially_superseded_by:
            with_superseded += 1

    # 2. archive
    archive_count = len(list(ARCHIVE_DIR.glob("*.md"))) if ARCHIVE_DIR.exists() else 0

    # 3. 任务包
    pending = task_pack.count_pending()

    # 4. 召回反馈
    stats = recall_log.collect_stats(since_days=args.since_days)

    # 5. 过滤日志摘要（仅过去 7 天）
    filter_count = 0
    if LOG_DIR.exists():
        for f in LOG_DIR.glob("filtered-*.jsonl"):
            try:
                filter_count += sum(1 for _ in open(f, encoding="utf-8"))
            except OSError:
                pass

    print(f"📊 ai-memory stats (recall window = past {args.since_days} days)")
    print()
    print(f"📚 Memory 总览（不含 archived）")
    print(f"   总数：{len(all_mems)}")
    print(f"   by scope:   {dict(sorted(by_scope.items()))}")
    print(f"   by source:  {dict(sorted(by_source.items()))}")
    print(f"   by value:   {dict(sorted(by_value.items()))}")
    if with_conflicts or with_superseded:
        print(f"   ⚠️ 含 potential_conflicts: {with_conflicts}  "
              f"被标 superseded: {with_superseded}")
    print()
    pending_marker = "📥"
    pending_extra = ""
    if pending >= 100:
        pending_marker = "🔴 pending"
        pending_extra = "  ⚠️ 堆积过多！打开 IDE 让 agent 消化（说『整理今日记忆』）；7 天未消化会自动清理"
    elif pending >= 50:
        pending_marker = "🟡 pending"
        pending_extra = "  💡 可在 IDE 里说『整理今日记忆』让 agent 消化"
    print(f"📦 archive: {archive_count}    "
          f"{pending_marker} tasks: {pending}    🚫 启发式过滤累计: {filter_count}")
    if pending_extra:
        print(pending_extra)
    print()
    print(f"🔍 召回（过去 {stats['since_days']} 天）")
    print(f"   search 调用: {stats['n_search']}")
    print(f"   read_page (强采纳信号): {stats['n_read']}")
    print(f"   read/search 比: {stats['adoption_rate']:.1%}")
    if stats["top_hit_ids"]:
        print()
        print(f"   🔥 最常召回的 memory:")
        for mid, cnt in stats["top_hit_ids"][:5]:
            print(f"     {cnt}× {mid}")
    return 0


def cmd_decay(args: argparse.Namespace) -> int:
    """P5 - 90 天未召中且 source=auto 的自动归档"""
    from core import recall_log
    result = recall_log.auto_decay(days=args.days, dry_run=args.dry_run)
    cands = result["candidates"]
    archived = result["archived"]
    if args.dry_run:
        print(f"🔬 dry-run: 找到 {len(cands)} 个候选（90 天未召中 + source=auto/bootstrap）")
        for mid in cands[:20]:
            print(f"  - {mid}")
        if len(cands) > 20:
            print(f"  ... 还有 {len(cands) - 20} 个")
        return 0
    print(f"✓ 已归档 {len(archived)} 条（候选 {len(cands)}）")
    for mid in archived[:10]:
        print(f"  - {mid}")
    return 0


def cmd_sync_agents_md(args: argparse.Namespace) -> int:
    """P4 - 把 project 摘要同步到 AGENTS.md"""
    from core import agents_md_sync
    from core.project_key import resolve_project_key
    workspace = args.workspace or os.getcwd()
    info = resolve_project_key(workspace)
    if not info:
        print(f"❌ workspace `{workspace}` 不在 git 仓库或无 origin remote", file=sys.stderr)
        return 1
    project_key = info["key"]
    summary = agents_md_sync.build_summary(project_key)
    if not summary:
        print(f"_project `{project_key}` 暂无 memory_")
        return 1
    if args.dry_run:
        print("--- 将写入的内容（dry-run）---")
        print(summary)
        return 0
    targets = [t.strip() for t in args.targets.split(",")] if args.targets else None
    written = agents_md_sync.sync_for_workspace(workspace, paths=targets)
    if not written:
        print(f"（无变更或目标文件无法写入）")
        return 0
    for p in written:
        print(f"✓ 已同步：{p}")
    return 0


def cmd_not_implemented(args: argparse.Namespace) -> int:
    """P5 phase 才实现的命令"""
    name = args._cmd_name
    phase = {
        "stats": "P5",
        "rebuild-index": "P5",
    }.get(name, "未来 phase")
    print(f"⚠️  `ai-memory {name}` 计划在 {phase} 实现", file=sys.stderr)
    print(f"   当前可用：add / edit / ls / show / archive / restore / distill / init / pending / config / sync-agents-md", file=sys.stderr)
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
    p_rst = sub.add_parser("restore", help="从 archive/ 恢复")
    p_rst.add_argument("id")
    p_rst.set_defaults(func=cmd_restore)

    # distill - P2 已落地
    p_dist = sub.add_parser("distill", help="蒸馏当日 sessions 为 memory")
    p_dist.add_argument("--range", default="today",
                        help="today | yesterday | YYYY-MM-DD")
    p_dist.add_argument("--mode-hint", choices=["daily", "batch"], dest="mode_hint",
                        help="场景提示：daily=增量（默认），batch=批量回溯。无 --mode 时按此挑模式")
    p_dist.add_argument("--mode", choices=["api", "host_agent", "local"],
                        help="覆盖 LLM mode（默认 env / config.yml 推断）")
    p_dist.add_argument("--concurrency", type=int)
    p_dist.add_argument("--dry-run", action="store_true",
                        help="只跑过滤 + 估算，不调 LLM 不写入")
    p_dist.add_argument("--verbose", action="store_true")
    p_dist.set_defaults(func=cmd_distill)

    # init - P3 已落地
    p_init = sub.add_parser("init", help="首次回溯历史对话")
    p_init.add_argument("--range", default="last-7d",
                        help="last-7d | last-30d | all | YYYY-MM-DD~YYYY-MM-DD")
    p_init.add_argument("--mode", choices=["api", "host_agent", "local"])
    p_init.add_argument("--ide", help="逗号分隔仅处理某些 IDE")
    p_init.add_argument("--budget-max", type=float, help="api 模式费用上限（元）")
    p_init.add_argument("--resume", action="store_true", help="从 .init-progress 续跑")
    p_init.add_argument("--yes", "-y", action="store_true", help="跳过 confirm")
    p_init.set_defaults(func=cmd_init)

    # pending - P3 已落地
    p_pend = sub.add_parser("pending", help="看 host_agent 模式的待消化任务包")
    p_pend.add_argument("--clear-failed", action="store_true", help="清理失败的任务包")
    p_pend.set_defaults(func=cmd_pending)

    # config - P3 已落地
    p_cfg = sub.add_parser("config", help="读写 LLM mode 等用户配置")
    p_cfg.add_argument("action", choices=["show", "get", "set"])
    p_cfg.add_argument("key", nargs="?", help="如 llm.mode")
    p_cfg.add_argument("value", nargs="?", help="set 时必填")
    p_cfg.set_defaults(func=cmd_config)

    # sync-agents-md - P4 已落地
    p_sync = sub.add_parser("sync-agents-md", help="把项目摘要同步到 AGENTS.md")
    p_sync.add_argument("--workspace", help="目标项目根（默认 cwd）")
    p_sync.add_argument("--targets", help="逗号分隔目标文件（默认 AGENTS.md）")
    p_sync.add_argument("--dry-run", action="store_true", help="只打印不写入")
    p_sync.set_defaults(func=cmd_sync_agents_md)

    # stats - P5 已落地
    p_st = sub.add_parser("stats", help="写入/召回/采纳统计")
    p_st.add_argument("--since-days", type=int, default=30,
                      help="召回反馈窗口（默认 30 天）")
    p_st.set_defaults(func=cmd_stats)

    # decay - P5 已落地（非顶层命名，相对独立功能）
    p_dec = sub.add_parser("decay", help="90 天未召中的 auto memory 自动归档")
    p_dec.add_argument("--days", type=int, default=90)
    p_dec.add_argument("--dry-run", action="store_true")
    p_dec.set_defaults(func=cmd_decay)

    # 占位命令（远期）
    for name, helptext in [
        ("rebuild-index", "[远期] 升级到 SQLite FTS5 索引"),
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
