#!/usr/bin/env python3
"""route_topics.py - Stage 3: compile 主入口（Agent 编排模式）

做什么：
    本脚本不调用 LLM，也不直接执行 llm-wiki 的 ingest 工作流。它只做：
        1. 扫描 distill 输出（~/.ai-memory/raw/topics/<date>/*.md）
        2. 解析 frontmatter，按 scope 路由到 wiki/{projects|domains|general}/<name>/
        3. 子库未初始化（无 .wiki-schema.md）→ 调 fork 仓的 init-wiki.sh 初始化
        4. 写出 compile-manifest.json：列出所有「待让 Agent 消化的 topic 任务」
    宿主 Agent（Cursor / Aone Copilot / Qoder 等）按 compile/SKILL.md 指引，
    逐个 cd 到子库并按 llm-wiki SKILL.md 的 ingest 工作流消化 topic。

CLI 子命令：
    plan       扫描 topics → 初始化子库（自动）→ 生成 compile-manifest.json
    status     打印 manifest 进度
    mark       手动把某个 task 标为 completed/failed（Agent 通常自己改 manifest，
               这里做兜底命令）

入口前置：
    必须在 collect + distill 完成之后跑（依赖 ~/.ai-memory/raw/topics/<date>/*.md）
    必须在 git submodule update --init 之后跑（依赖 compile/llm-wiki-skill/）

失败模式：
    - submodule 未拉取 → 友好提示 + 非零退出
    - topics 目录不存在 / 为空 → 退出码 0（不是错误，今天没东西可入库）
    - 单个 topic frontmatter 解析失败 → 标记 failed，记入 errors.log，不阻塞
    - init-wiki.sh 调用失败 → 标记该 task failed，记入 errors.log
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 让 lib 可作为模块导入
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import frontmatter, scope_router  # noqa: E402
from lib.io_utils import (  # noqa: E402
    append_error_log,
    load_json,
    save_manifest,
)
from lib.paths_ext import (  # noqa: E402
    COMPILE_ERRORS_LOG,
    LLM_WIKI_INIT_SCRIPT,
    LLM_WIKI_SKILL_FILE,
    RAW_TOPICS_DIR,
    daily_manifest_path,
    ensure_compile_dirs,
    llm_wiki_available,
)


# ==================== 通用 ====================

def resolve_date(arg: str) -> str:
    if arg == "today":
        return datetime.now().date().isoformat()
    if arg == "yesterday":
        return (datetime.now().date() - timedelta(days=1)).isoformat()
    datetime.strptime(arg, "%Y-%m-%d")
    return arg


def vlog(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def _check_submodule() -> str | None:
    """submodule 健康检查；返回 None 表示 OK，否则返回错误文案"""
    if not llm_wiki_available():
        return (
            f"找不到 llm-wiki-skill 的 init-wiki.sh：{LLM_WIKI_INIT_SCRIPT}\n"
            f"        请先运行：git submodule update --init --recursive"
        )
    return None


def _ensure_subwiki(
    subwiki_path: Path,
    wiki_topic_label: str,
    language: str,
    verbose: bool,
) -> tuple[bool, str | None]:
    """如果子库未初始化，调 init-wiki.sh 初始化。

    返回 (ok, error_msg)
    """
    if scope_router.is_subwiki_initialized(subwiki_path):
        return True, None
    subwiki_path.mkdir(parents=True, exist_ok=True)
    cmd = [
        "bash",
        str(LLM_WIKI_INIT_SCRIPT),
        str(subwiki_path),
        wiki_topic_label,
        language,
    ]
    vlog(verbose, f"[init-subwiki] {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"init-wiki.sh 调用异常: {e}"
    if result.returncode != 0:
        return False, (
            f"init-wiki.sh 退出码 {result.returncode}; "
            f"stderr={result.stderr.strip()[:300]}"
        )
    return True, None


# ==================== 子命令实现 ====================

def cmd_plan(date: str, verbose: bool, dry_run: bool) -> int:
    """扫描 topics → 初始化子库 → 生成 compile-manifest.json"""
    err = _check_submodule()
    if err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 2

    topics_dir = RAW_TOPICS_DIR / date
    if not topics_dir.exists():
        print(f"[INFO] {topics_dir} 不存在，今天没有可入库的 topic，跳过。")
        return 0

    topic_files = sorted(p for p in topics_dir.glob("*.md") if p.is_file())
    if not topic_files:
        print(f"[INFO] {topics_dir} 为空，今天没有可入库的 topic，跳过。")
        return 0

    ensure_compile_dirs()

    tasks: list[dict] = []
    init_warnings: list[str] = []
    parsed_subwikis: dict[str, dict] = {}  # subwiki_path → {label, language, topic 数}

    for idx, tp in enumerate(topic_files):
        try:
            fm = frontmatter.parse_topic_file(tp)
        except (FileNotFoundError, ValueError) as e:
            init_warnings.append(f"{tp.name}: frontmatter 解析失败 - {e}")
            tasks.append({
                "id": f"compile-{idx:03d}",
                "topic_file": str(tp),
                "status": "failed",
                "error": f"frontmatter parse failed: {e}",
            })
            append_error_log(COMPILE_ERRORS_LOG, tp.name, str(e))
            continue

        routing = scope_router.route(fm)
        for w in routing["warnings"]:
            init_warnings.append(f"{tp.name}: {w}")

        sub_path = routing["subwiki_path"]
        sub_key = str(sub_path)

        # 子库初始化（每个子库只 init 一次）
        if sub_key not in parsed_subwikis:
            # 必须在 _ensure_subwiki 调用之前快照状态，否则 init 完之后再查永远是 True
            was_initialized_before = scope_router.is_subwiki_initialized(sub_path)
            if dry_run:
                # dry-run 不真正调脚本，仅记录意图
                parsed_subwikis[sub_key] = {
                    "label": routing["wiki_topic_label"],
                    "language": routing["language"],
                    "initialized_in_this_run": not was_initialized_before,
                    "topics": 0,
                }
            else:
                ok, err = _ensure_subwiki(
                    sub_path, routing["wiki_topic_label"],
                    routing["language"], verbose,
                )
                parsed_subwikis[sub_key] = {
                    "label": routing["wiki_topic_label"],
                    "language": routing["language"],
                    "initialized_in_this_run": ok and not was_initialized_before,
                    "topics": 0,
                }
                if not ok:
                    msg = f"{tp.name}: 子库 {sub_path} 初始化失败 - {err}"
                    init_warnings.append(msg)
                    tasks.append({
                        "id": f"compile-{idx:03d}",
                        "topic_file": str(tp),
                        "subwiki_path": sub_key,
                        "status": "failed",
                        "error": err,
                    })
                    append_error_log(COMPILE_ERRORS_LOG, tp.name, err)
                    continue

        parsed_subwikis[sub_key]["topics"] += 1

        tasks.append({
            "id": f"compile-{idx:03d}",
            "topic_file": str(tp),
            "topic_filename": tp.name,
            "scope": routing["scope"],
            "subwiki_name": routing["subwiki_name"],
            "subwiki_path": sub_key,
            "wiki_topic_label": routing["wiki_topic_label"],
            "tags": fm.get("tags") or [],
            "estimated_value": (fm.get("quality") or {}).get("estimated_value"),
            "status": "pending",
        })

    manifest = {
        "version": 1,
        "date": date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "topics_dir": str(topics_dir),
        "subwikis": [
            {
                "path": p,
                "label": meta["label"],
                "language": meta["language"],
                "initialized_this_run": meta["initialized_in_this_run"],
                "topic_count": meta["topics"],
            }
            for p, meta in parsed_subwikis.items()
        ],
        "tasks": tasks,
        "warnings": init_warnings,
    }

    mp = daily_manifest_path(date)
    if not dry_run:
        save_manifest(mp, manifest)

    summary = {
        "stage": "plan",
        "date": date,
        "manifest": str(mp) if not dry_run else f"(dry-run, would-write) {mp}",
        "topics_total": len(topic_files),
        "tasks_pending": sum(1 for t in tasks if t["status"] == "pending"),
        "tasks_failed": sum(1 for t in tasks if t["status"] == "failed"),
        "subwikis_touched": len(parsed_subwikis),
        "next": (
            "让 Agent 按 compile/SKILL.md 消化 manifest 中所有 pending task；"
            "完成后把对应 task.status 改为 completed 并查 status"
        ),
    }
    if verbose:
        summary["warnings"] = init_warnings
        summary["subwikis"] = manifest["subwikis"]
    print(summary)
    return 0


def cmd_status(date: str, verbose: bool) -> int:
    mp = daily_manifest_path(date)
    if not mp.exists():
        print(f"[ERROR] manifest 不存在: {mp}（请先 plan）", file=sys.stderr)
        return 2
    manifest = load_json(mp)
    tasks = manifest.get("tasks", [])
    by_status: dict[str, int] = {}
    for t in tasks:
        s = t.get("status", "pending")
        by_status[s] = by_status.get(s, 0) + 1

    print({
        "date": date,
        "manifest": str(mp),
        "total": len(tasks),
        "by_status": by_status,
        "subwikis": [s["path"] for s in manifest.get("subwikis", [])],
    })
    if verbose:
        for t in tasks:
            err = f" ERR={t.get('error')}" if t.get("error") else ""
            print(f"  [{t.get('status', 'pending'):9}] {t['id']} → "
                  f"{t.get('subwiki_name', '?')} | {t.get('topic_filename', t.get('topic_file', ''))}"
                  f"{err}", file=sys.stderr)
    return 0


def cmd_mark(date: str, task_id: str, status: str,
             error: str | None, verbose: bool) -> int:
    mp = daily_manifest_path(date)
    if not mp.exists():
        print(f"[ERROR] manifest 不存在: {mp}", file=sys.stderr)
        return 2
    if status not in ("pending", "completed", "failed"):
        print(f"[ERROR] 非法 status: {status}", file=sys.stderr)
        return 2
    manifest = load_json(mp)
    found = False
    for t in manifest.get("tasks", []):
        if t["id"] == task_id:
            t["status"] = status
            if error is not None:
                t["error"] = error
            elif status != "failed":
                t.pop("error", None)
            found = True
            break
    if not found:
        print(f"[ERROR] 找不到 task_id={task_id}", file=sys.stderr)
        return 2
    save_manifest(mp, manifest)
    vlog(verbose, f"[mark] {task_id} → {status}")
    print({"updated": task_id, "status": status})
    return 0


# ==================== CLI ====================

def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--verbose", action="store_true",
                   help="详细日志（写到 stderr）")
    p.add_argument("--dry-run", action="store_true",
                   help="不真正写文件 / 不调 init-wiki.sh，仅打印将要做什么")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ai-coding-memory: compile stage (Agent-orchestrated routing)",
        epilog=(
            "典型流程：plan → [Agent 按 SKILL.md 逐 topic 消化并改 manifest 状态] → status\n"
            f"llm-wiki SKILL.md 路径：{LLM_WIKI_SKILL_FILE}"
        ),
    )
    _add_common_flags(parser)

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="扫描 topics → 初始化子库 → 生成 manifest")
    p_plan.add_argument("--date", default="today")
    _add_common_flags(p_plan)

    p_status = sub.add_parser("status", help="打印 manifest 进度")
    p_status.add_argument("--date", default="today")
    _add_common_flags(p_status)

    p_mark = sub.add_parser("mark", help="手动标记任务状态（兜底接口）")
    p_mark.add_argument("--date", default="today")
    p_mark.add_argument("--id", required=True, help="task id（如 compile-001）")
    p_mark.add_argument("--status", required=True,
                        choices=["pending", "completed", "failed"])
    p_mark.add_argument("--error", default=None, help="failed 时的错误描述")
    _add_common_flags(p_mark)

    args = parser.parse_args()
    date = resolve_date(args.date)

    if args.cmd == "plan":
        return cmd_plan(date, args.verbose, args.dry_run)
    if args.cmd == "status":
        return cmd_status(date, args.verbose)
    if args.cmd == "mark":
        return cmd_mark(date, args.id, args.status, args.error, args.verbose)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
