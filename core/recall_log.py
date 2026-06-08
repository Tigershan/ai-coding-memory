"""core.recall_log - 召回反馈日志 + 自动衰减归档（redesign §6.7 / P5）

日志格式（~/.ai-memory/logs/recall-YYYY-MM-DD.jsonl）：
    {"ts":"2026-05-16T22:01:03","event":"search","query":"redis","hits":[id1,id2,...]}
    {"ts":"2026-05-16T22:01:08","event":"read","id":"...","path":"..."}
    {"ts":"2026-05-16T22:01:15","event":"adopt","id":"..."}    # 可选，由 IDE/CLI 触发

衰减规则（仅 source=auto / bootstrap）：
    - 该 memory.id 90 天内既未被 read 也未被 hit → archive
    - source ∈ {manual, edited} 永不衰减
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .paths import LOG_DIR


def _log_path(date: str) -> Path:
    return LOG_DIR / f"recall-{date}.jsonl"


def log_event(event: str, **fields) -> None:
    """追加一条 jsonl"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    p = _log_path(datetime.now().date().isoformat())
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_search(query: str, hit_ids: list[str]) -> None:
    log_event("search", query=query, hits=hit_ids)


def log_read(memory_id: str, path: str) -> None:
    log_event("read", id=memory_id, path=path)


def log_adopt(memory_id: str) -> None:
    log_event("adopt", id=memory_id)


# ==================== 统计 ====================

def iter_logs(since_days: int | None = None):
    """按日期降序迭代所有 recall log 文件中的 record"""
    if not LOG_DIR.exists():
        return
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now() - timedelta(days=since_days)
    files = sorted(LOG_DIR.glob("recall-*.jsonl"), reverse=True)
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cutoff:
                    try:
                        ts = datetime.fromisoformat(rec.get("ts", ""))
                        if ts < cutoff:
                            continue
                    except ValueError:
                        pass
                yield rec
        except OSError:
            continue


def collect_stats(since_days: int = 30) -> dict:
    """聚合最近 N 天的 recall 数据"""
    n_search = 0
    n_read = 0
    n_adopt = 0
    hit_counts: dict[str, int] = {}     # id → 命中次数
    read_counts: dict[str, int] = {}    # id → 被打开次数
    for rec in iter_logs(since_days):
        ev = rec.get("event")
        if ev == "search":
            n_search += 1
            for hid in rec.get("hits") or []:
                hit_counts[hid] = hit_counts.get(hid, 0) + 1
        elif ev == "read":
            n_read += 1
            mid = rec.get("id")
            if mid:
                read_counts[mid] = read_counts.get(mid, 0) + 1
        elif ev == "adopt":
            n_adopt += 1
    return {
        "since_days": since_days,
        "n_search": n_search,
        "n_read": n_read,
        "n_adopt": n_adopt,
        "adoption_rate": (n_read / n_search) if n_search else 0.0,
        "top_hit_ids": sorted(hit_counts.items(), key=lambda x: -x[1])[:10],
        "top_read_ids": sorted(read_counts.items(), key=lambda x: -x[1])[:10],
        "active_ids": set(hit_counts) | set(read_counts),
    }


# ==================== 召回频率计数（searcher 用于 recall boost） ====================

_recall_counts_cache: dict | None = None
_recall_counts_ts: float = 0.0
_RECALL_COUNTS_TTL_S = 60.0  # 1 分钟 TTL


def get_recall_counts(days: int = 30, *, force_refresh: bool = False) -> dict[str, int]:
    """统计最近 N 天每个 memory ID 的召回次数（search hit + read）。

    结果带进程级 TTL 缓存（60s），避免每次搜索都扫日志。
    read 事件权重 ×2（用户主动打开 = 更强的价值信号）。

    返回 {memory_id: weighted_count}。
    """
    global _recall_counts_cache, _recall_counts_ts
    now = time.time()
    if (
        not force_refresh
        and _recall_counts_cache is not None
        and (now - _recall_counts_ts) < _RECALL_COUNTS_TTL_S
    ):
        return _recall_counts_cache

    counts: dict[str, int] = {}
    for rec in iter_logs(since_days=days):
        ev = rec.get("event")
        if ev == "search":
            for hid in rec.get("hits") or []:
                if hid:
                    counts[hid] = counts.get(hid, 0) + 1
        elif ev == "read":
            mid = rec.get("id")
            if mid:
                counts[mid] = counts.get(mid, 0) + 2
    _recall_counts_cache = counts
    _recall_counts_ts = now
    return counts


# ==================== 自动衰减 ====================

def auto_decay(*, days: int = 90, dry_run: bool = False) -> dict:
    """归档 N 天内未被任何 search 命中、未被 read 的 source=auto/bootstrap memory。

    返回 {"candidates": [id, ...], "archived": [id, ...]}
    """
    from . import memory_store as ms

    stats = collect_stats(since_days=days)
    active = stats["active_ids"]

    candidates: list[str] = []
    archived: list[str] = []
    for mem in ms.list_memories(include_archived=False):
        # 仅 source=auto / bootstrap
        if mem.source not in ("auto", "bootstrap"):
            continue
        if mem.id in active:
            continue
        # 创建日期太近的不动（避免新 memory 立即被衰减）
        try:
            created = datetime.fromisoformat(mem.created)
        except (ValueError, TypeError):
            continue
        if (datetime.now() - created).days < days:
            continue
        candidates.append(mem.id)
        if not dry_run:
            if ms.archive(mem.id) is not None:
                archived.append(mem.id)

    return {"candidates": candidates, "archived": archived}
