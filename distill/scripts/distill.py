#!/usr/bin/env python3
"""distill.py - Stage 2: distill 主入口（Agent 编排 + auto 模式 + 快速通道）

做什么：
    本脚本默认不调用 LLM，而是把 collect 阶段产出的 sessions.json 拆解为
    "可被 Agent 逐步消化"的任务包文件，并维护 manifest.json 状态机。

    支持两种 topic 处理路径：
    - 完整流水线（high-value topic）：step1 → step2 → step3 → step4
    - 快速通道（medium/low-value topic）：step1 → stepF（单次 LLM 合并 step2+3+4）

    auto 子命令支持并发 LLM 调用，显著提升处理速度。

CLI 子命令：
    plan      读 sessions → 生成全部 step1 任务包 + 初始 manifest
    expand    读 manifest → 按 topic 价值分流展开后续任务
    status    打印 manifest 进度
    assemble  合并所有已完成 step 的结果为最终 topic .md 文件
    auto      一键执行完整流水线（plan → 并发消化 → assemble）

输入：
    --date    YYYY-MM-DD | today | yesterday   （默认 today）
    --verbose 详细日志
    --dry-run 仅打印将要做什么，不写文件

auto 模式额外参数：
    --llm-api      OpenAI-compatible API base URL（默认 Dashscope）
    --llm-key      API key
    --llm-model    模型名（默认 qwen-plus）
    --concurrency  并发 LLM 调用数（默认 4）
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import (  # noqa: E402
    code_filter,
    coreference_resolver,
    fast_track,
    layer_tagger,
    task_builder,
    topic_writer,
)
from lib.io_utils import (  # noqa: E402
    append_error_log,
    load_json,
    load_manifest,
    save_manifest,
    write_text_atomic,
)
from lib.paths_ext import (  # noqa: E402
    DATA_ROOT,
    RAW_SESSIONS_DIR,
    RAW_TOPICS_DIR,
    daily_task_dir,
    ensure_distill_dirs,
    manifest_path,
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

# ==================== 子命令实现 ====================

def cmd_plan(date: str, verbose: bool, dry_run: bool) -> int:
    """生成 step1 任务包"""
    sessions_path = RAW_SESSIONS_DIR / f"{date}.json"
    if not sessions_path.exists():
        print(f"[ERROR] sessions 文件不存在: {sessions_path}", file=sys.stderr)
        print("        请先跑 collect 阶段：python3 collect/scripts/extract_sessions.py "
              f"--range today", file=sys.stderr)
        return 2

    sessions_data = load_json(sessions_path)
    sessions = sessions_data.get("sessions", [])
    vlog(verbose, f"[plan] sessions={len(sessions)} from {sessions_path}")

    if not sessions:
        print(f"[INFO] {date} 没有任何 session，distill 跳过。")
        return 0

    daily_root = ensure_distill_dirs(date) if not dry_run else daily_task_dir(date)

    if dry_run:
        print({
            "date": date,
            "sessions": len(sessions),
            "would_create_step1_tasks": sum(
                1 for s in sessions if s.get("conversation")
            ),
            "daily_root": str(daily_root),
        })
        return 0

    manifest = task_builder.plan_step1(sessions_data, daily_root, date)
    save_manifest(manifest_path(date), manifest)

    progress = task_builder.manifest_progress(manifest)
    print({
        "stage": "plan",
        "date": date,
        "manifest": str(manifest_path(date)),
        "progress": progress,
        "next": f"让 Agent 按 distill/SKILL.md 消化 step1，"
                f"完成后跑：distill.py expand --date {date}",
    })
    return 0

def cmd_expand(date: str, verbose: bool, dry_run: bool,
               drop_threshold: str = "noise") -> int:
    """根据已完成的前序 step 展开后续任务"""
    mp = manifest_path(date)
    if not mp.exists():
        print(f"[ERROR] manifest 不存在: {mp}（请先 plan）", file=sys.stderr)
        return 2
    sessions_path = RAW_SESSIONS_DIR / f"{date}.json"
    sessions_data = load_json(sessions_path)
    manifest = load_manifest(mp)
    daily_root = daily_task_dir(date)
    domain_mapping_path = DATA_ROOT / "config" / "domain-mapping.yml"

    before = len(manifest["tasks"])
    manifest = task_builder.expand_downstream(
        manifest, sessions_data, daily_root, domain_mapping_path,
        drop_threshold=drop_threshold,
    )
    after = len(manifest["tasks"])
    new_tasks = after - before

    if not dry_run:
        save_manifest(mp, manifest)

    progress = task_builder.manifest_progress(manifest)
    print({
        "stage": "expand",
        "date": date,
        "new_tasks": new_tasks,
        "progress": progress,
    })
    return 0

def cmd_status(date: str, verbose: bool) -> int:
    mp = manifest_path(date)
    if not mp.exists():
        print(f"[ERROR] manifest 不存在: {mp}", file=sys.stderr)
        return 2
    manifest = load_manifest(mp)
    progress = task_builder.manifest_progress(manifest)

    print({
        "date": date,
        "manifest": str(mp),
        "progress": progress,
    })

    if verbose:
        for t in manifest["tasks"]:
            status = t.get("status", "pending")
            err = f" ERR={t.get('error')}" if t.get("error") else ""
            print(f"  [{status:9}] {t['id']:40} ({t['step']}){err}",
                  file=sys.stderr)
    return 0

def cmd_assemble(date: str, verbose: bool, dry_run: bool) -> int:
    """合并所有已完成 step 的结果为最终 topic .md 文件

    支持两种 task 类型：
    - step4 (layer_tagging) → 完整流水线的终点，取齐 step2/3 结果
    - stepF (fast_track) → 快速通道，单个 result 包含全部信息
    """
    mp = manifest_path(date)
    if not mp.exists():
        print(f"[ERROR] manifest 不存在: {mp}", file=sys.stderr)
        return 2

    sessions_path = RAW_SESSIONS_DIR / f"{date}.json"
    sessions_data = load_json(sessions_path)
    manifest = load_manifest(mp)
    daily_root = daily_task_dir(date)

    sessions_idx = {i: s for i, s in enumerate(sessions_data.get("sessions", []))}
    by_id = {t["id"]: t for t in manifest["tasks"]}

    out_dir = RAW_TOPICS_DIR / date
    out_dir.mkdir(parents=True, exist_ok=True)
    error_log = out_dir / "errors.log"

    written: list = []
    skipped: list = []
    failed: list = []
    topic_idx = 0

    # ---- A) 完整流水线：遍历 step4 (layer_tagging) 任务 ----
    for t4 in manifest["tasks"]:
        if t4["step"] != "layer_tagging":
            continue
        if t4.get("status") != "completed":
            skipped.append((t4["id"], f"status={t4.get('status')}"))
            continue

        sidx = t4["session_index"]
        tid = t4["topic_id"]
        ide = t4["ide"]
        session = sessions_idx.get(sidx)
        if not session:
            skipped.append((t4["id"], "session 索引丢失"))
            continue

        coref_id = f"step2-{ide}-{sidx:03d}-t{tid:02d}"
        code_id = f"step3-{ide}-{sidx:03d}-t{tid:02d}"
        coref_task = by_id.get(coref_id)
        code_task = by_id.get(code_id)
        if not (coref_task and code_task):
            skipped.append((t4["id"], "前序任务缺失"))
            continue

        topic_meta = t4.get("topic_meta") or coref_task.get("topic_meta")
        if not topic_meta:
            skipped.append((t4["id"], "topic_meta 丢失"))
            continue

        try:
            coref = coreference_resolver.parse_result(
                daily_root / coref_task["result_file"]
            )
            code = code_filter.parse_result(
                daily_root / code_task["result_file"]
            )
            layer = layer_tagger.parse_result(
                daily_root / t4["result_file"]
            )
        except Exception as e:  # noqa: BLE001
            failed.append((t4["id"], str(e)))
            append_error_log(error_log, t4["id"], str(e))
            continue

        topic_idx += 1
        title = topic_meta.get("title") or f"untitled-{topic_idx}"
        if dry_run:
            written.append((topic_idx, title, layer["scope"]))
            continue

        try:
            out_path = topic_writer.write_topic_file(
                out_dir=out_dir, topic_idx=topic_idx, date_str=date,
                title=title, session=session, topic=topic_meta,
                coref=coref, code=code, layer=layer,
            )
            written.append(str(out_path))
            vlog(verbose, f"[assemble] wrote {out_path}")
        except Exception as e:  # noqa: BLE001
            failed.append((t4["id"], f"write failed: {e}"))
            append_error_log(error_log, t4["id"], str(e))

    # ---- B) 快速通道：遍历 stepF (fast_track) 任务 ----
    for tf in manifest["tasks"]:
        if tf["step"] != "fast_track":
            continue
        if tf.get("status") != "completed":
            skipped.append((tf["id"], f"status={tf.get('status')}"))
            continue

        sidx = tf["session_index"]
        session = sessions_idx.get(sidx)
        if not session:
            skipped.append((tf["id"], "session 索引丢失"))
            continue

        topic_meta = tf.get("topic_meta")
        if not topic_meta:
            skipped.append((tf["id"], "topic_meta 丢失"))
            continue

        try:
            result = fast_track.parse_result(daily_root / tf["result_file"])
            coref = result["coref"]
            code = result["code"]
            layer = result["layer"]
        except Exception as e:  # noqa: BLE001
            failed.append((tf["id"], str(e)))
            append_error_log(error_log, tf["id"], str(e))
            continue

        topic_idx += 1
        title = topic_meta.get("title") or f"untitled-{topic_idx}"
        if dry_run:
            written.append((topic_idx, title, layer["scope"]))
            continue

        try:
            out_path = topic_writer.write_topic_file(
                out_dir=out_dir, topic_idx=topic_idx, date_str=date,
                title=title, session=session, topic=topic_meta,
                coref=coref, code=code, layer=layer,
            )
            written.append(str(out_path))
            vlog(verbose, f"[assemble] wrote {out_path} (fast_track)")
        except Exception as e:  # noqa: BLE001
            failed.append((tf["id"], f"write failed: {e}"))
            append_error_log(error_log, tf["id"], str(e))

    summary = {
        "stage": "assemble",
        "date": date,
        "out_dir": str(out_dir),
        "written_count": len(written),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
    }
    if verbose:
        summary["written"] = written
        summary["skipped"] = skipped
        summary["failed"] = failed
    print(summary)
    return 0


# ==================== auto 子命令 ====================

def _process_single_task(task, daily_root, llm_api, llm_key, llm_model, verbose):
    """处理单个 pending task 的 LLM 调用（用于并发执行）

    Returns:
        (task_id, status, error_or_none)
    """
    from lib.llm_client import call_llm

    task_id = task["id"]
    prompt_file = daily_root / task["prompt_file"]

    if not prompt_file.exists():
        return task_id, "failed", f"prompt 文件不存在: {prompt_file}"

    prompt_content = prompt_file.read_text(encoding="utf-8")

    response = call_llm(
        prompt_content,
        base_url=llm_api,
        api_key=llm_key,
        model=llm_model,
    )

    if response.error:
        return task_id, "failed", response.error[:500]

    result_path = daily_root / task["result_file"]
    result_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(result_path, response.content)
    return task_id, "completed", None


def cmd_auto(
    date: str,
    verbose: bool,
    dry_run: bool,
    llm_api: str | None,
    llm_key: str | None,
    llm_model: str | None,
    drop_threshold: str = "noise",
    concurrency: int = 4,
) -> int:
    """一键执行完整 distill 流水线（并发 LLM 调用）

    流程：plan → 并发消化 pending tasks → expand → 并发消化 → ... → assemble

    并发策略：同一轮内的所有 pending tasks 并发调用 LLM（最大并发数由
    --concurrency 控制），不同轮次间串行（因为后序 step 依赖前序结果）。
    """
    # Step 0: plan
    vlog(verbose, f"[auto] === Step 0: plan ({date}) ===")
    ret = cmd_plan(date, verbose, dry_run)
    if ret != 0:
        return ret

    mp = manifest_path(date)
    if not mp.exists():
        print(f"[INFO] {date} 无任务，跳过。")
        return 0

    sessions_path = RAW_SESSIONS_DIR / f"{date}.json"
    sessions_data = load_json(sessions_path)
    daily_root = daily_task_dir(date)
    domain_mapping_path = DATA_ROOT / "config" / "domain-mapping.yml"

    max_rounds = 8
    for round_num in range(max_rounds):
        manifest = load_manifest(mp)
        pending_tasks = [t for t in manifest["tasks"] if t.get("status") == "pending"]

        if not pending_tasks:
            vlog(verbose, f"[auto] 第 {round_num + 1} 轮：无 pending 任务，进入 assemble")
            break

        vlog(verbose, f"[auto] === 第 {round_num + 1} 轮：{len(pending_tasks)} 个 pending 任务"
                       f"（并发度 {min(concurrency, len(pending_tasks))}）===")

        if dry_run:
            vlog(verbose, f"[auto] (dry-run) 跳过 LLM 调用")
            break

        # 并发消化所有 pending 任务
        completed_count = 0
        failed_count = 0
        task_by_id = {t["id"]: t for t in manifest["tasks"]}

        effective_concurrency = min(concurrency, len(pending_tasks))
        with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
            futures = {
                executor.submit(
                    _process_single_task,
                    task, daily_root, llm_api, llm_key, llm_model, verbose
                ): task["id"]
                for task in pending_tasks
            }

            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    tid, status, error = future.result()
                    task_obj = task_by_id[tid]
                    task_obj["status"] = status
                    if error:
                        task_obj["error"] = error
                        failed_count += 1
                        print(f"[WARN] {tid} 失败: {error}", file=sys.stderr)
                    else:
                        completed_count += 1
                        vlog(verbose, f"[auto] ✓ {tid} 完成")
                except Exception as exc:  # noqa: BLE001
                    task_obj = task_by_id[task_id]
                    task_obj["status"] = "failed"
                    task_obj["error"] = str(exc)[:500]
                    failed_count += 1
                    print(f"[WARN] {task_id} 异常: {exc}", file=sys.stderr)

        save_manifest(mp, manifest)
        vlog(verbose, f"[auto] 本轮完成 {completed_count} 个，失败 {failed_count} 个")

        # expand 展开下一批任务
        manifest = load_manifest(mp)
        before = len(manifest["tasks"])
        manifest = task_builder.expand_downstream(
            manifest, sessions_data, daily_root, domain_mapping_path,
            drop_threshold=drop_threshold,
        )
        after = len(manifest["tasks"])
        save_manifest(mp, manifest)

        new_tasks = after - before
        vlog(verbose, f"[auto] expand 后新增 {new_tasks} 个任务")

        if new_tasks == 0 and completed_count == 0:
            vlog(verbose, "[auto] 无新任务也无完成任务，退出循环")
            break

    # Step final: assemble
    vlog(verbose, f"[auto] === Final: assemble ===")
    ret = cmd_assemble(date, verbose, dry_run)

    if mp.exists():
        manifest = load_manifest(mp)
        progress = task_builder.manifest_progress(manifest)
        print(json.dumps({
            "stage": "auto-complete",
            "date": date,
            "progress": progress,
        }, ensure_ascii=False, indent=2))

    return ret


# ==================== CLI ====================

def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--verbose", action="store_true",
                   help="详细日志（写到 stderr）")
    p.add_argument("--dry-run", action="store_true",
                   help="不写文件，仅打印将要做什么")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ai-coding-memory: distill stage (Agent-orchestrated + auto mode)",
    )
    _add_common_flags(parser)

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="生成 step1 任务包 + 初始 manifest")
    p_plan.add_argument("--date", default="today")
    _add_common_flags(p_plan)

    p_expand = sub.add_parser("expand", help="展开后续任务（按 topic 价值分流）")
    p_expand.add_argument("--date", default="today")
    p_expand.add_argument("--drop", default="noise",
                          choices=["noise", "low", "medium"],
                          help="丢弃 estimated_value 不高于此阈值的 topic")
    _add_common_flags(p_expand)

    p_status = sub.add_parser("status", help="打印 manifest 进度")
    p_status.add_argument("--date", default="today")
    _add_common_flags(p_status)

    p_asm = sub.add_parser("assemble", help="合并所有 step 结果为最终 topic .md")
    p_asm.add_argument("--date", default="today")
    _add_common_flags(p_asm)

    p_auto = sub.add_parser("auto",
                            help="一键执行完整流水线（并发 LLM 调用）")
    p_auto.add_argument("--date", default="today")
    p_auto.add_argument("--llm-api", default=None,
                        help="OpenAI-compatible API base URL（默认 Dashscope）")
    p_auto.add_argument("--llm-key", default=None,
                        help="API key（默认从 DASHSCOPE_API_KEY 或 OPENAI_API_KEY 读取）")
    p_auto.add_argument("--llm-model", default=None,
                        help="模型名（默认 qwen-plus）")
    p_auto.add_argument("--concurrency", type=int, default=4,
                        help="并发 LLM 调用数（默认 4）")
    p_auto.add_argument("--drop", default="noise",
                        choices=["noise", "low", "medium"],
                        help="丢弃 estimated_value 不高于此阈值的 topic")
    _add_common_flags(p_auto)

    args = parser.parse_args()
    date = resolve_date(args.date)

    if args.cmd == "plan":
        return cmd_plan(date, args.verbose, args.dry_run)
    if args.cmd == "expand":
        return cmd_expand(date, args.verbose, args.dry_run, args.drop)
    if args.cmd == "status":
        return cmd_status(date, args.verbose)
    if args.cmd == "assemble":
        return cmd_assemble(date, args.verbose, args.dry_run)
    if args.cmd == "auto":
        return cmd_auto(
            date, args.verbose, args.dry_run,
            llm_api=args.llm_api, llm_key=args.llm_key, llm_model=args.llm_model,
            drop_threshold=args.drop, concurrency=args.concurrency,
        )

    return 1

if __name__ == "__main__":
    raise SystemExit(main())
