"""core.distill_quota - host_agent 模式下的每日消化上限治理

为什么需要：host_agent 模式下，每个任务包消化时要调一次宿主 IDE 的 LLM
（占用用户日常 coding 的实时配额）。批量历史 init 可能一次生成几十条任务包，
若一次性消化会瞬间吃光用户当日 IDE 配额，影响主业。

涓流策略：默认每天最多消化 10 条（config.yml: distill.daily_cap），
超出时 get_next_distill_task 返回拒绝消息「今日额度用尽，明天继续」。
用户可以喊『继续整理』传 force=True 突破。

数据：
    ~/.ai-memory/.daily-distill-counter.json
    {"date": "2026-05-16", "count": 7, "cap_used_at": "2026-05-16T15:42:11"}
    日期变更后自动重置（lazy 检查 today_str 不匹配就归零）。

接口：
    get_daily_cap() -> int                    读 config.yml
    get_today_count() -> int                  当日已消化数
    can_take() -> tuple[bool, int, int]       (allowed, used, cap)
    incr_today() -> int                       计数 +1，返回新值
    reset_today() -> None                     管理用：清今日计数
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config as user_config
from .paths import DATA_ROOT, ensure_data_dirs


COUNTER_PATH: Path = DATA_ROOT / ".daily-distill-counter.json"
DEFAULT_DAILY_CAP = 10


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read_counter() -> dict:
    if not COUNTER_PATH.exists():
        return {"date": "", "count": 0}
    try:
        data = json.loads(COUNTER_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"date": "", "count": 0}
        return data
    except (json.JSONDecodeError, OSError):
        return {"date": "", "count": 0}


def _write_counter(data: dict) -> None:
    ensure_data_dirs()
    tmp = COUNTER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(COUNTER_PATH)


def get_daily_cap() -> int:
    """读 config.yml 里的 distill.daily_cap；解析失败 / 缺失时用默认 10。"""
    try:
        v = user_config.get_value("distill.daily_cap")
        if v is None:
            return DEFAULT_DAILY_CAP
        n = int(v)
        return n if n > 0 else DEFAULT_DAILY_CAP
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP


def get_today_count() -> int:
    """当日已消化条数；跨日自动归零。"""
    data = _read_counter()
    if data.get("date") != _today_str():
        return 0
    try:
        return int(data.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def can_take() -> tuple[bool, int, int]:
    """是否可继续消化下一条。返回 (allowed, today_used, cap)。"""
    used = get_today_count()
    cap = get_daily_cap()
    return (used < cap, used, cap)


def incr_today() -> int:
    """当日计数 +1，返回新计数。跨日时先归零再 +1。"""
    today = _today_str()
    data = _read_counter()
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    try:
        cur = int(data.get("count") or 0)
    except (TypeError, ValueError):
        cur = 0
    data["count"] = cur + 1
    data["last_at"] = datetime.now().isoformat(timespec="seconds")
    _write_counter(data)
    return data["count"]


def reset_today() -> None:
    """管理用：把今日计数清零（保留日期），方便手动测试或纠错。"""
    today = _today_str()
    _write_counter({"date": today, "count": 0, "last_at": datetime.now().isoformat(timespec="seconds")})
