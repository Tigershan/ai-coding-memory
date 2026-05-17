#!/usr/bin/env python3
"""distill - 把 collect 的 sessions 蒸馏成 memory（按 redesign §6.2）

新版本特性（vs 旧 4-step）：
    - 1-step：单次 LLM 调用产出最终 markdown + value + should_keep
    - 启发式过滤前置（heuristic_filter）
    - 双模式：api（同步并发）/ host_agent（写任务包给宿主 agent，P3 落地）
    - 直接落盘到新数据模型 (~/.ai-memory/{personal,projects/<key>}/)
    - should_keep=false 的 topic **直接丢弃**（不再写 .cold/），仅日志记录

CLI:
    python3 distill/scripts/distill.py --range today [--dry-run] [--mode api|host_agent]
                                       [--concurrency 4] [--verbose]

输入：~/.ai-memory/raw/sessions/YYYY-MM-DD.json （由 collect 产出）
输出：
    ~/.ai-memory/{personal,projects/<key>}/<id>.md     should_keep=true 入库
    ~/.ai-memory/logs/distill-YYYY-MM-DD.log            执行日志（含 DROPPED 行）
    ~/.ai-memory/logs/filtered-YYYY-MM-DD.jsonl        启发式过滤记录

退出码：
    0 = 至少一条 memory 写入成功
    1 = 全部失败
    2 = 没有任何 session 可处理（noise 全砍光 / 当日没数据）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import frontmatter as fm                              # noqa: E402
from core import memory_store as ms                             # noqa: E402
from core import privacy_filter                                 # noqa: E402
from core.llm_provider import (                                 # noqa: E402
    LLMCallError,
    PendingTaskError,
    load_config_from_env,
    make_provider,
)
from core.memory_store import Memory                            # noqa: E402
from core.paths import (                                        # noqa: E402
    LOG_DIR,
    RAW_SESSIONS_DIR,
    ensure_data_dirs,
)

# distill.lib 路径（不在 core/ 里，独立维护启发式）
DISTILL_LIB = PROJECT_ROOT / "distill" / "scripts" / "lib"
if str(DISTILL_LIB.parent) not in sys.path:
    sys.path.insert(0, str(DISTILL_LIB.parent))
from lib.heuristic_filter import is_noise                       # noqa: E402

PROMPT_FILE = PROJECT_ROOT / "distill" / "prompts" / "01_distill_topic.md"


# ==================== 日期 / IO ====================

def resolve_date(arg: str) -> str:
    if arg == "today":
        return datetime.now().date().isoformat()
    if arg == "yesterday":
        return (datetime.now().date() - timedelta(days=1)).isoformat()
    datetime.strptime(arg, "%Y-%m-%d")  # 校验
    return arg


def load_sessions(date_key: str) -> dict | None:
    f = RAW_SESSIONS_DIR / f"{date_key}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def append_log(date_key: str, line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"distill-{date_key}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {line}\n")


def append_filtered_log(date_key: str, session: dict, reason: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"filtered-{date_key}.jsonl"
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session.get("sessionId"),
        "ide": session.get("ide"),
        "workspace": session.get("workspace"),
        "msg_count": len(session.get("conversation") or []),
        "reason": reason,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ==================== prompt 渲染 ====================

def render_prompt(session: dict, project_key: str | None) -> str:
    """把 session 内容填进 prompt 模板（写入前对每条 message 做 secret 脱敏）"""
    template = PROMPT_FILE.read_text(encoding="utf-8")
    convo_lines = []
    redact_total: dict[str, int] = {}
    for i, m in enumerate(session.get("conversation") or []):
        role = m.get("role", "?")
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        content, hits = privacy_filter.redact(content)
        for k, v in hits.items():
            redact_total[k] = redact_total.get(k, 0) + v
        convo_lines.append(f"[{i}] {role}: {content}")
    convo_text = "\n\n".join(convo_lines)
    if redact_total:
        try:
            _append_redact_log(session, redact_total)
        except Exception:
            pass

    return (
        template
        .replace("{workspace}", str(session.get("workspace") or "(unknown)"))
        .replace("{ide}", str(session.get("ide") or "?"))
        .replace("{session_id}", str(session.get("sessionId") or "?"))
        .replace("{project_key}", project_key or "null")
        .replace("{conversation}", convo_text)
    )


def _append_redact_log(session: dict, counts: dict[str, int]) -> None:
    """记录每个 session 的脱敏统计（不含原文）。供审计、不打扰用户。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_key = datetime.now().date().isoformat()
    log_file = LOG_DIR / f"redact-{date_key}.jsonl"
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session.get("sessionId"),
        "ide": session.get("ide"),
        "counts": counts,
        "total": sum(counts.values()),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ==================== LLM 输出解析 ====================

def parse_llm_yaml(text: str) -> list[dict]:
    """从 LLM 输出中抽取 topics: 数组。
    LLM 偶尔会用 ``` 包裹，先尝试剥掉。"""
    s = text.strip()
    # 剥 ```yaml ... ``` 围栏
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    # 用 frontmatter._parse_yaml 复用解析（足够本场景用）
    parsed = fm._parse_yaml(s)
    topics = parsed.get("topics")
    if topics is None:
        # 兜底：尝试在文本中找 topics: 块
        idx = s.find("topics:")
        if idx >= 0:
            parsed = fm._parse_yaml(s[idx:])
            topics = parsed.get("topics")
    if not isinstance(topics, list):
        return []
    return topics


# ==================== 单 session 处理 ====================

def distill_one_session(
    session: dict,
    provider,
    *,
    project_key_resolver=None,
    dry_run: bool = False,
    verbose: bool = False,
    batch_date: str | None = None,
) -> dict:
    """处理一个 session，返回 stats dict
    {
        "session_id": str,
        "kept": int,         # 入库到 personal/projects 的 topic 数
        "dropped": int,      # LLM 判 should_keep=false 直接丢弃的 topic 数
        "error": str | None,
    }

    batch_date: 任务对应的会话日期 YYYY-MM-DD（init/lazy_trigger 应当传入）。
        host_agent 模式下会写入 task pack，消化排队按此字段均摊。
    """
    sid = session.get("sessionId", "?")
    workspace = session.get("workspace") or ""
    project_key = None
    if project_key_resolver and workspace:
        info = project_key_resolver(workspace)
        if info:
            project_key = info["key"]

    if dry_run:
        return {"session_id": sid, "kept": 0, "dropped": 0, "skipped_reason": "dry-run"}

    prompt = render_prompt(session, project_key)
    # 超长 session 兜底：粗估 tokens（chars/4），超过 28K 跳过避免 OOM / 截断垃圾输出
    # qwen3 / qwen2.5 系列 32K context；GPT-4 / Claude 即使更大也不该塞这种巨长 session
    MAX_INPUT_TOKENS = 28000
    est_tokens = len(prompt) // 4
    if est_tokens > MAX_INPUT_TOKENS:
        if verbose:
            print(f"  [{sid[:8]}] SKIPPED: prompt ~{est_tokens} tokens > {MAX_INPUT_TOKENS} cap",
                  file=sys.stderr)
        return {
            "session_id": sid, "kept": 0, "dropped": 0,
            "error": f"input too large (~{est_tokens} tokens, cap {MAX_INPUT_TOKENS})",
        }

    # host_agent 需要预先注入 session 上下文给任务包
    if hasattr(provider, "set_session_context"):
        try:
            provider.set_session_context(session, project_key, batch_date=batch_date)
        except TypeError:
            # 老 provider 不接受 batch_date kwarg —— 兼容兜底
            provider.set_session_context(session, project_key)
    try:
        out = provider.run(prompt)
    except PendingTaskError as e:
        # host_agent 模式：任务已写入 .pending/，agent 后续消化
        return {"session_id": sid, "kept": 0, "dropped": 0, "pending_task": e.task_path}
    except (LLMCallError, NotImplementedError) as e:
        return {"session_id": sid, "kept": 0, "dropped": 0, "error": str(e)}

    topics = parse_llm_yaml(out)
    if not topics:
        return {"session_id": sid, "kept": 0, "dropped": 0, "error": "LLM 输出无可解析 topics"}

    kept = 0
    dropped = 0
    for t in topics:
        try:
            if t.get("should_keep") is False:
                # 直接丢弃，不入库（cold 概念已废弃）
                dropped += 1
                if verbose:
                    print(f"  [{sid[:8]}] DROPPED title={(t.get('title') or '')[:40]!r} "
                          f"reason={(t.get('keep_reason') or '')[:60]!r}", file=sys.stderr)
                continue
            mem = _topic_to_memory(t, session, project_key)
            ms.save(mem, allow_overwrite_protected=False)
            kept += 1
            if verbose:
                print(f"  [{sid[:8]}] KEPT {mem.scope}/{mem.id} value={mem.value}",
                      file=sys.stderr)
        except PermissionError as pe:
            # 用户已手编辑过同 ID 文件，不覆盖
            return {"session_id": sid, "kept": kept, "dropped": dropped, "error": str(pe)}
        except Exception as e:
            return {"session_id": sid, "kept": kept, "dropped": dropped,
                    "error": f"save failed: {e}"}

    return {"session_id": sid, "kept": kept, "dropped": dropped, "error": None}


def _topic_to_memory(topic: dict, session: dict, project_key: str | None) -> Memory:
    title = (topic.get("title") or "untitled").strip()
    body = topic.get("body") or f"# {title}\n"
    scope = topic.get("scope") or "personal"
    if scope not in ("personal", "project"):
        scope = "personal"
    # scope=project 但 project_key 不可用 → 兜底 personal
    if scope == "project" and not project_key:
        scope = "personal"
    pkey = project_key if scope == "project" else None

    value = topic.get("value") or "medium"
    if value not in ("high", "medium", "low"):
        value = "medium"

    tags = topic.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags][:6]

    origin = {
        "ide": session.get("ide"),
        "session_id": session.get("sessionId"),
        "workspace": session.get("workspace"),
    }

    mem = Memory(
        id=ms.make_id(title),
        scope=scope,
        title=title,
        body=body,
        project_key=pkey,
        source="auto",
        value=value,
        tags=tags,
        origin=origin,
        extra={
            "summary": topic.get("summary") or "",
            "keep_reason": topic.get("keep_reason") or "",
        },
    )
    return mem


# ==================== 主流程 ====================

def cmd_distill(args: argparse.Namespace) -> int:
    ensure_data_dirs()
    date_key = resolve_date(args.range)

    raw = load_sessions(date_key)
    if raw is None:
        print(f"❌ 找不到 sessions：{RAW_SESSIONS_DIR}/{date_key}.json", file=sys.stderr)
        print(f"   先跑：python3 collect/scripts/extract_sessions.py --range {args.range}", file=sys.stderr)
        return 2

    sessions = raw.get("sessions") or []
    if not sessions:
        print(f"📭 当日无 session（{date_key}）")
        return 2

    # 启发式过滤
    survivors: list[dict] = []
    filter_stats: dict[str, int] = {}
    for s in sessions:
        is_n, reason = is_noise(s)
        if is_n:
            filter_stats[reason] = filter_stats.get(reason, 0) + 1
            append_filtered_log(date_key, s, reason)
        else:
            survivors.append(s)

    print(f"📊 启发式过滤: {len(sessions)} 总数 → 保留 {len(survivors)} (砍 {len(sessions) - len(survivors)})")
    if filter_stats:
        for reason, cnt in filter_stats.items():
            print(f"   {reason}: {cnt}")

    if not survivors:
        print("📭 启发式过滤后无 session 可处理")
        return 2

    if args.dry_run:
        print(f"🔬 dry-run：将处理 {len(survivors)} 个 session（不调 LLM、不写入）")
        # 简单估算：1 个 session ≈ 1 次 LLM 调用 ≈ ~2k token
        est_calls = len(survivors)
        print(f"   预计 LLM 调用次数：~{est_calls}")
        return 0

    # 解析 project_key
    from core.project_key import resolve_project_key

    # 准备 LLM provider
    cfg = load_config_from_env()
    if args.mode:
        cfg.mode = args.mode
    elif getattr(args, "mode_hint", None):
        # 没显式 --mode，按 hint 走 daily_mode / batch_mode
        from core.config import resolve_mode
        cfg.mode = resolve_mode(scope=args.mode_hint)
    if args.concurrency:
        cfg.api_concurrency = args.concurrency
    if cfg.mode == "local":
        model_label = cfg.local_model or "qwen3:8b"
        print(f"⚙️  LLM mode: local (model={model_label}, base={cfg.local_base or 'http://localhost:11434/v1'})")
    else:
        print(f"⚙️  LLM mode: {cfg.mode} (model={cfg.api_model}, concurrency={cfg.api_concurrency})")

    try:
        provider = make_provider(cfg)
    except Exception as e:
        print(f"❌ LLM provider 初始化失败：{e}", file=sys.stderr)
        return 1

    # 同步路径（api / local）
    if provider.is_synchronous():
        return _run_sync(survivors, provider, date_key, resolve_project_key,
                         concurrency=cfg.api_concurrency, verbose=args.verbose)

    # 异步路径（host_agent）：写任务包到 .pending/，等宿主 agent 通过 MCP 消化
    # 必须串行（task_pack.write_task 非线程安全且每次都改 fs），且要顺序保留 session 上下文
    return _run_sync(survivors, provider, date_key, resolve_project_key,
                     concurrency=1, verbose=args.verbose)


def _run_sync(
    sessions: list[dict],
    provider,
    date_key: str,
    project_key_resolver,
    *,
    concurrency: int,
    verbose: bool,
) -> int:
    start = time.time()
    total_kept = 0
    total_dropped = 0
    total_failed = 0
    total_pending = 0

    def task(s):
        return distill_one_session(
            s, provider,
            project_key_resolver=project_key_resolver,
            dry_run=False,
            verbose=verbose,
            batch_date=date_key,
        )

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(task, s): s for s in sessions}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result()
            except Exception as e:
                total_failed += 1
                tb = traceback.format_exc(limit=3)
                append_log(date_key, f"FATAL session task: {e}\n{tb}")
                continue

            kept = result.get("kept", 0)
            dropped = result.get("dropped", 0)
            err = result.get("error")
            pending_task = result.get("pending_task")
            total_kept += kept
            total_dropped += dropped
            if err:
                total_failed += 1
                append_log(date_key, f"FAILED {result['session_id']}: {err}")
                marker = " err"
            elif pending_task:
                total_pending += 1
                append_log(date_key, f"PENDING {result['session_id']} → {pending_task}")
                marker = " pending"
            else:
                append_log(date_key, f"OK {result['session_id']}: kept={kept} dropped={dropped}")
                marker = ""
            print(f"  [{i}/{len(sessions)}] kept={kept} dropped={dropped}{marker}")

    dur = time.time() - start
    summary = f"kept={total_kept} dropped={total_dropped} pending={total_pending} failed={total_failed}"
    print(f"\n✓ done in {dur:.1f}s — {summary}")
    if total_pending > 0:
        print(f"💡 {total_pending} 个任务包待消化。"
              f"在任意 IDE 里说『整理今日记忆』，agent 会通过 MCP 消化。")
    # 退出码：有 kept 或 pending 都算成功
    if total_kept > 0 or total_pending > 0:
        return 0
    return 1


# ==================== argparse ====================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="distill",
        description="蒸馏 sessions → memory（redesign §6.2）",
    )
    p.add_argument("--range", default="today",
                   help="today | yesterday | YYYY-MM-DD")
    p.add_argument("--mode", choices=["api", "host_agent", "local"],
                   help="覆盖 LLM mode（默认从 env / config.yml 推断）")
    p.add_argument("--mode-hint", choices=["daily", "batch"], dest="mode_hint",
                   help="场景提示：daily=增量（lazy_trigger / 主动当日蒸馏），"
                        "batch=批量（init / 历史回溯）。无 --mode 时按此挑 daily_mode / batch_mode；默认 daily")
    p.add_argument("--concurrency", type=int,
                   help="并发 LLM 调用数（仅 api 模式有意义）")
    p.add_argument("--dry-run", action="store_true",
                   help="只跑过滤 + 估算，不调 LLM、不写入")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc = cmd_distill(args)
    # 任何"实际跑过的"distill 都更新 .last_distill（lazy trigger 用）
    if not args.dry_run:
        try:
            from core.paths import LAST_DISTILL_PATH, DATA_ROOT
            DATA_ROOT.mkdir(parents=True, exist_ok=True)
            LAST_DISTILL_PATH.write_text(f"{time.time()}\n", encoding="utf-8")
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
