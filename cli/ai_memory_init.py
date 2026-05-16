"""cli.ai_memory_init - 首次回溯 init（redesign §6.5）

ai-memory init [--range last-7d|last-30d|all] [--budget-max N] [--resume]
               [--mode api|host_agent] [--ide IDE1,IDE2]
               [--yes]  # 跳过 confirm

三阶段（双模式）：
  Phase A：扫描 + 启发式过滤
  Phase B：用户 confirm + 预算估算
  Phase C：执行（api 模式：并发跑；host_agent 模式：批量生成任务包）

断点续跑：~/.ai-memory/.init-progress.json 记录已完成的 session_id
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import task_pack                                       # noqa: E402
from core.llm_provider import load_config_from_env, make_provider, LLMCallError, PendingTaskError  # noqa: E402
from core.paths import INIT_PROGRESS_PATH, RAW_SESSIONS_DIR, ensure_data_dirs  # noqa: E402


def _load_file_module(name: str, file_path: Path):
    """从绝对路径加载一个 .py 为模块（避免同名 lib 冲突）"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {file_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DISTILL_DIR = PROJECT_ROOT / "distill" / "scripts"
# distill.py 自己会 sys.path.insert distill/scripts 让 from lib.heuristic_filter 工作
_distill_mod = _load_file_module("ai_memory_distill", DISTILL_DIR / "distill.py")
distill_one_session = _distill_mod.distill_one_session
load_sessions = _distill_mod.load_sessions
is_noise = _load_file_module(
    "ai_memory_heuristic", DISTILL_DIR / "lib" / "heuristic_filter.py"
).is_noise

_time_range = _load_file_module(
    "ai_memory_time_range",
    PROJECT_ROOT / "collect" / "scripts" / "lib" / "time_range.py",
)
enumerate_dates = _time_range.enumerate_dates


# 估算：一次 LLM 调用 token 数（粗略）+ 单价
APPROX_INPUT_TOKENS_PER_SESSION = 2000
APPROX_OUTPUT_TOKENS_PER_SESSION = 400
# qwen-plus 价格估算（按 dashscope 公开价 ¥0.001 / 1k input, ¥0.002 / 1k output）
# 用户用其他模型时这个数字仅作参考
APPROX_YUAN_PER_SESSION = (
    APPROX_INPUT_TOKENS_PER_SESSION * 0.001 / 1000
    + APPROX_OUTPUT_TOKENS_PER_SESSION * 0.002 / 1000
)


# ==================== 进度断点 ====================

def load_progress() -> dict:
    if not INIT_PROGRESS_PATH.exists():
        return {"completed_session_ids": [], "started_at": None, "config": {}}
    try:
        return json.loads(INIT_PROGRESS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"completed_session_ids": [], "started_at": None, "config": {}}


def save_progress(progress: dict) -> None:
    ensure_data_dirs()
    tmp = INIT_PROGRESS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INIT_PROGRESS_PATH)


def clear_progress() -> None:
    if INIT_PROGRESS_PATH.exists():
        INIT_PROGRESS_PATH.unlink()


# ==================== Phase A: 扫描 ====================

def scan_sessions(range_arg: str, ide_filter: set[str] | None = None) -> list[dict]:
    """枚举范围内所有 sessions（已展开为 dict 列表）"""
    out: list[dict] = []
    for date_key in enumerate_dates(range_arg):
        raw = load_sessions(date_key)
        if raw is None:
            continue
        for s in raw.get("sessions") or []:
            if ide_filter and s.get("ide") not in ide_filter:
                continue
            out.append(s)
    return out


def filter_and_estimate(
    sessions: list[dict],
    completed_ids: set[str],
) -> dict:
    """启发式过滤 + 估算。返回：
    {
        "total": int,
        "already_done": int,
        "filtered": {reason: count},
        "survivors": [session, ...],
        "est_calls": int,
        "est_yuan": float,
        "est_minutes": float,
    }"""
    already_done = 0
    filtered: dict[str, int] = {}
    survivors: list[dict] = []
    for s in sessions:
        sid = s.get("sessionId", "")
        if sid in completed_ids:
            already_done += 1
            continue
        is_n, reason = is_noise(s)
        if is_n:
            filtered[reason] = filtered.get(reason, 0) + 1
            continue
        survivors.append(s)

    est_calls = len(survivors)
    est_yuan = est_calls * APPROX_YUAN_PER_SESSION
    # 估时按 ~3 秒/调用（含网络）+ 并发因子
    est_minutes = (est_calls * 3) / 60.0 / 4  # 默认并发 4

    return {
        "total": len(sessions),
        "already_done": already_done,
        "filtered": filtered,
        "survivors": survivors,
        "est_calls": est_calls,
        "est_yuan": est_yuan,
        "est_minutes": est_minutes,
    }


# ==================== Phase B/C ====================

def cmd_init(args: argparse.Namespace) -> int:
    ensure_data_dirs()

    # 1. resume 还是 fresh
    progress = load_progress() if args.resume else {"completed_session_ids": [], "config": {}}
    completed_ids = set(progress.get("completed_session_ids") or [])
    if args.resume and not completed_ids:
        print("⚠️  --resume 指定但 .init-progress.json 不存在或为空；改为新跑")
    if completed_ids:
        print(f"📂 找到 {len(completed_ids)} 条已完成 session（断点续跑）")

    # 2. IDE 过滤
    ide_filter = None
    if args.ide:
        ide_filter = {x.strip() for x in args.ide.split(",") if x.strip()}
        print(f"🎯 仅处理 IDE: {ide_filter}")

    # 3. Phase A: 扫描 + 过滤
    print(f"🔍 Phase A: 扫描 range={args.range} ...")
    sessions = scan_sessions(args.range, ide_filter)
    if not sessions:
        print(f"📭 范围内无任何 session ({args.range})")
        return 2

    info = filter_and_estimate(sessions, completed_ids)
    print(f"   总 session 数: {info['total']}")
    if info["already_done"]:
        print(f"   ⏭  已完成（断点）: {info['already_done']}")
    for reason, cnt in info["filtered"].items():
        print(f"   🚫 启发式过滤 {reason}: {cnt}")
    print(f"   ✓  待处理: {len(info['survivors'])}")

    if not info["survivors"]:
        print("📭 启发式过滤后无 session 可处理")
        return 2

    # 4. 决定模式
    cfg = load_config_from_env()
    if args.mode:
        cfg.mode = args.mode
    print(f"⚙️  LLM mode: {cfg.mode} (model={cfg.api_model})")

    # 5. Phase B: 估算 + confirm
    print()
    print("📊 估算（基于 qwen-plus 公开价；其他模型仅作参考）：")
    print(f"   LLM 调用数：~{info['est_calls']}")
    if cfg.mode == "api":
        print(f"   预计费用：~¥{info['est_yuan']:.2f}")
        print(f"   预计耗时：~{info['est_minutes']:.1f} 分钟（并发 {cfg.api_concurrency}）")
        if args.budget_max and info["est_yuan"] > args.budget_max:
            print(f"   ⚠️  超过 --budget-max ¥{args.budget_max:.2f}，将在跑满预算后停止")
    else:
        print(f"   host_agent 模式：将生成 {info['est_calls']} 个任务包到 .pending/")
        print(f"   消化方式：打开 IDE 后说『整理今日记忆』")

    if not args.yes:
        ans = input("\n继续？[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 1

    # 6. Phase C: 执行
    print(f"\n🚀 Phase C: 开始处理...")
    try:
        provider = make_provider(cfg)
    except Exception as e:
        print(f"❌ provider 初始化失败：{e}", file=sys.stderr)
        return 1

    # 进度文件先标记开始
    progress.setdefault("started_at", datetime.now().isoformat(timespec="seconds"))
    progress["config"] = {"range": args.range, "mode": cfg.mode, "model": cfg.api_model}
    save_progress(progress)

    from core.project_key import resolve_project_key

    total_kept = 0
    total_cold = 0
    total_pending = 0
    total_failed = 0
    spent_yuan = 0.0
    start = time.time()

    # 串行处理：保证 host_agent 模式 set_session_context 不串味
    # （api 模式可以并发，但 init 是一次性事件，串行简化逻辑）
    for i, s in enumerate(info["survivors"], 1):
        if args.budget_max and spent_yuan >= args.budget_max:
            print(f"  💰 已达预算上限 ¥{args.budget_max}，停止；progress 保留可续跑")
            break

        result = distill_one_session(
            s, provider,
            project_key_resolver=resolve_project_key,
            dry_run=False,
            verbose=False,
        )
        sid = result["session_id"]
        if "pending_task" in result:
            total_pending += 1
            marker = "pending"
        elif result.get("error"):
            total_failed += 1
            marker = f"err: {result['error'][:60]}"
        else:
            total_kept += result.get("kept", 0)
            total_cold += result.get("cold", 0)
            marker = f"kept={result.get('kept',0)} cold={result.get('cold',0)}"

        spent_yuan += APPROX_YUAN_PER_SESSION
        # 写进度
        progress["completed_session_ids"].append(sid)
        if i % 5 == 0 or i == len(info["survivors"]):
            save_progress(progress)

        elapsed = time.time() - start
        eta = (elapsed / i) * (len(info["survivors"]) - i) if i > 0 else 0
        print(f"  [{i}/{len(info['survivors'])}] {marker}  (eta {eta/60:.1f}min)")

    save_progress(progress)
    dur = time.time() - start

    print()
    print(f"✓ init done in {dur/60:.1f}min — kept={total_kept} cold={total_cold} "
          f"pending={total_pending} failed={total_failed}")
    if total_pending > 0:
        print(f"💡 {total_pending} 个任务包待消化。打开 IDE 说『整理今日记忆』agent 会消化。")
    if total_failed > 0:
        print(f"⚠️  {total_failed} 个 session 失败；详见 ~/.ai-memory/logs/")
    print(f"📂 progress 文件：{INIT_PROGRESS_PATH}（清理：ai-memory init --clear）")

    return 0 if (total_kept + total_pending) > 0 else 1


# ==================== pending CLI ====================

def cmd_pending(args: argparse.Namespace) -> int:
    """ai-memory pending [--clear-failed]"""
    if args.clear_failed:
        from core.paths import PENDING_DIR
        n = 0
        for f in PENDING_DIR.glob("*.task.failed"):
            f.unlink()
            n += 1
        print(f"✓ 清理 {n} 个 failed 任务")
        return 0

    pending = task_pack.list_pending(include_in_progress=False)
    in_prog = [t for t in task_pack.list_pending(include_in_progress=True)
               if t["status"] == "in_progress"]
    failed = task_pack.list_pending(include_failed=True, include_in_progress=False)
    failed = [t for t in failed if t["status"] == "failed"]

    print(f"📥 待消化: {len(pending)}")
    print(f"🔄 进行中: {len(in_prog)}")
    print(f"❌ 失败:   {len(failed)}")

    if pending:
        print()
        print(f"{'TASK_ID':<14} {'IDE':<14} {'AGE':<8} WORKSPACE")
        print("-" * 80)
        for t in pending[:20]:
            age_h = t["age_seconds"] // 3600
            age_str = f"{age_h}h" if age_h else f"{t['age_seconds']//60}m"
            ws = (t.get("workspace") or "")[:50]
            print(f"{t['task_id']:<14} {t.get('ide',''):<14} {age_str:<8} {ws}")
        if len(pending) > 20:
            print(f"... 还有 {len(pending) - 20} 个未显示")

    if in_prog:
        print(f"\n💡 {len(in_prog)} 个任务标记为进行中。如果 agent 已卡死/退出，")
        print(f"   可调 `python3 -c 'from core import task_pack; print(task_pack.reset_in_progress())'`")
        print(f"   重置回 pending。")
    return 0
