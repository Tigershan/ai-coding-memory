"""task_builder - 把 sessions.json 拆解为分阶段任务清单

设计要点：
    distill 流水线的 4 个 step 中，只有 step1 的任务能在不依赖 LLM 输出时
    全量生成；step2/3/4 都依赖前序 step 的实际输出（topic 数 / dialogue 内容
    / kept_snippets）。

    step1 完成后按 estimated_value 分流：
    - high → 完整流水线（step2→3→4，三次 LLM 调用）
    - medium / low → 快速通道（stepF，单次 LLM 调用合并 step2+3+4）
    - noise → 直接丢弃

    本模块提供两个入口：
    - plan_step1(sessions, daily_root)
        → 全量生成 step1 任务，写 manifest（status=pending）
    - expand_downstream(manifest, sessions_index, daily_root, domain_mapping_path)
        → 扫描 step1/2/3 的 result 状态，按需展开 step2/3/4 或 stepF 任务
        （幂等：已存在的任务不重复添加）

数据来源：
    sessions: 完整 collect 输出 dict（含 timeRange、sessions、stats）

manifest schema 见 docs/design.md 设计与本文件 _new_manifest 函数。
"""

from datetime import datetime
from pathlib import Path

from . import code_filter, coreference_resolver, fast_track, layer_tagger, topic_segmenter
from .io_utils import load_json, save_manifest

# 快速通道适用的 estimated_value 等级
FAST_TRACK_VALUES = {"medium", "low"}
# 仅 high 走完整流水线
FULL_PIPELINE_VALUES = {"high"}

def _new_manifest(date: str, sessions_summary: list[dict]) -> dict:
    return {
        "version": 1,
        "date": date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sessions": sessions_summary,
        "tasks": [],
    }

def _summarize_session(session: dict, idx: int) -> dict:
    return {
        "session_index": idx,
        "session_id": session.get("sessionId", ""),
        "ide": session.get("ide", ""),
        "workspace": session.get("workspace", ""),
        "message_count": len(session.get("conversation", [])),
        "created_at": session.get("createdAt", ""),
    }

def _has_task(manifest: dict, task_id: str) -> bool:
    return any(t["id"] == task_id for t in manifest["tasks"])

def plan_step1(
    sessions_data: dict,
    daily_root: Path,
    date: str,
) -> dict:
    """生成 step1 任务包 + 初始 manifest"""
    sessions = sessions_data.get("sessions", [])
    summary = [_summarize_session(s, i) for i, s in enumerate(sessions)]
    manifest = _new_manifest(date, summary)

    for idx, session in enumerate(sessions):
        if not session.get("conversation"):
            continue
        task = topic_segmenter.build_task(session, idx, daily_root)
        manifest["tasks"].append(task)

    return manifest

def _completed(task: dict) -> bool:
    return task.get("status") == "completed"

def _index_sessions(sessions_data: dict) -> dict[int, dict]:
    return {i: s for i, s in enumerate(sessions_data.get("sessions", []))}

def expand_downstream(
    manifest: dict,
    sessions_data: dict,
    daily_root: Path,
    domain_mapping_path: Path,
    drop_threshold: str = "noise",
) -> dict:
    """根据 step1/2/3 的已完成结果，按需展开后续任务

    分流策略（step1 完成后）：
    - estimated_value = "high" → step2（完整流水线 step2→3→4）
    - estimated_value = "medium" / "low" → stepF（快速通道，单次 LLM 调用）
    - estimated_value = "noise" → 丢弃（不展开任何任务）

    幂等：重复调用不会产生重复任务。
    返回更新后的 manifest（调用方负责保存）。

    drop_threshold: 'noise' | 'low' | 'medium'
        丢弃 estimated_value <= 阈值的 topic（不展开任何任务）
    """
    drop_levels = {"noise": ["noise"],
                   "low": ["noise", "low"],
                   "medium": ["noise", "low", "medium"]}[drop_threshold]

    sessions_idx = _index_sessions(sessions_data)
    by_id: dict[str, dict] = {t["id"]: t for t in manifest["tasks"]}

    def _add_task(t: dict) -> None:
        if not _has_task(manifest, t["id"]):
            manifest["tasks"].append(t)
            by_id[t["id"]] = t

    # ---- 1) 扫描已完成的 step1 → 按 value 分流 ----
    for task in list(manifest["tasks"]):
        if task["step"] != "topic_segmentation" or not _completed(task):
            continue
        sidx = task["session_index"]
        session = sessions_idx.get(sidx)
        if not session:
            continue
        result_path = daily_root / task["result_file"]
        try:
            topics = topic_segmenter.parse_result(
                result_path, len(session.get("conversation", []))
            )
        except (FileNotFoundError, ValueError) as e:
            task["status"] = "failed"
            task["error"] = str(e)
            continue

        for topic in topics:
            value = topic["estimated_value"]
            if value in drop_levels:
                continue

            if value in FULL_PIPELINE_VALUES:
                # high → 完整流水线：展开 step2
                t2 = coreference_resolver.build_task(session, sidx, topic, daily_root)
                _add_task(t2)
            else:
                # medium / low → 快速通道：展开 stepF
                tf = fast_track.build_task(
                    session, sidx, topic, daily_root, domain_mapping_path
                )
                _add_task(tf)

    # ---- 2) 扫描已完成的 step2 → 展开 step3（仅 high-value 完整流水线）----
    for task in list(manifest["tasks"]):
        if task["step"] != "coreference" or not _completed(task):
            continue
        sidx = task["session_index"]
        session = sessions_idx.get(sidx)
        if not session:
            continue
        topic_meta = task.get("topic_meta")
        if not topic_meta:
            continue
        try:
            coref = coreference_resolver.parse_result(daily_root / task["result_file"])
        except (FileNotFoundError, ValueError) as e:
            task["status"] = "failed"
            task["error"] = str(e)
            continue

        t3 = code_filter.build_task(
            session, sidx, topic_meta, daily_root, coref["dialogue_md"]
        )
        _add_task(t3)

    # ---- 3) 扫描已完成的 step3 → 展开 step4（仅 high-value 完整流水线）----
    for task in list(manifest["tasks"]):
        if task["step"] != "code_filter" or not _completed(task):
            continue
        sidx = task["session_index"]
        tid = task["topic_id"]
        session = sessions_idx.get(sidx)
        if not session:
            continue
        topic_meta = task.get("topic_meta")
        coref_id = f"step2-{task['ide']}-{sidx:03d}-t{tid:02d}"
        coref_task = by_id.get(coref_id)
        if not (topic_meta and coref_task):
            continue
        try:
            coref = coreference_resolver.parse_result(
                daily_root / coref_task["result_file"]
            )
            code = code_filter.parse_result(daily_root / task["result_file"])
        except (FileNotFoundError, ValueError) as e:
            task["status"] = "failed"
            task["error"] = str(e)
            continue

        t4 = layer_tagger.build_task(
            session, sidx, topic_meta, daily_root,
            dialogue_md=coref["dialogue_md"],
            kept_snippets=code["kept_snippets"],
            discarded_summary=code["discarded_summary"],
            domain_mapping_path=domain_mapping_path,
        )
        _add_task(t4)

    return manifest

def manifest_progress(manifest: dict) -> dict:
    """返回任务进度统计"""
    by_status: dict[str, int] = {}
    by_step: dict[str, dict[str, int]] = {}
    for t in manifest["tasks"]:
        s = t.get("status", "pending")
        by_status[s] = by_status.get(s, 0) + 1
        step_stats = by_step.setdefault(t["step"], {})
        step_stats[s] = step_stats.get(s, 0) + 1
    total = len(manifest["tasks"])
    completed = by_status.get("completed", 0)
    return {
        "total": total,
        "completed": completed,
        "pending": by_status.get("pending", 0),
        "failed": by_status.get("failed", 0),
        "completion_rate": round(completed / total, 2) if total else 0.0,
        "by_step": by_step,
    }
