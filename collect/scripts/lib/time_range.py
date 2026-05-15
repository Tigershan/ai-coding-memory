"""时间范围计算

接受多种格式的时间范围关键字，返回带毫秒戳的时间窗口。

支持的格式：
    - 'today'                    当天 00:00 ~ 现在
    - 'yesterday'                昨天 00:00 ~ 23:59:59
    - '2026-04-25'               指定日期整天
    - '2026-04-20~2026-04-25'    指定日期范围（含两端）
    - 'last-7d'                  最近 7 天（含今天）
    - 'last-30d'                 最近 30 天（含今天）
"""

import re
from datetime import datetime, timedelta
from typing import TypedDict


class TimeRange(TypedDict):
    label: str
    start: str        # ISO 格式
    end: str
    start_ms: int
    end_ms: int


_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$")
_LAST_N_RE = re.compile(r"^last-(\d+)d$")


def compute_time_range(range_str: str) -> TimeRange:
    """根据范围关键字计算起止时间窗口

    Args:
        range_str: 支持 'today' | 'yesterday' | 'YYYY-MM-DD' |
                   'YYYY-MM-DD~YYYY-MM-DD' | 'last-Nd'

    Returns:
        TimeRange 字典，含人类可读 label 和毫秒戳。

    失败模式：
        - 未知 range_str → 静默 fallback 到 'today'
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 快捷关键字
    if range_str == "today":
        return _build(f"今天 ({now.strftime('%Y-%m-%d')})", today_start, now)

    if range_str == "yesterday":
        start = today_start - timedelta(days=1)
        end = today_start - timedelta(microseconds=1)
        return _build(f"昨天 ({start.strftime('%Y-%m-%d')})", start, end)

    # last-Nd 格式
    last_n_match = _LAST_N_RE.match(range_str)
    if last_n_match:
        days = int(last_n_match.group(1))
        start = today_start - timedelta(days=days - 1)
        label = f"最近 {days} 天 ({start.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')})"
        return _build(label, start, now)

    # YYYY-MM-DD~YYYY-MM-DD 范围格式
    range_match = _RANGE_RE.match(range_str)
    if range_match:
        start = _parse_date(range_match.group(1))
        end_date = _parse_date(range_match.group(2))
        end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        label = f"{range_match.group(1)} ~ {range_match.group(2)}"
        return _build(label, start, end)

    # YYYY-MM-DD 单日格式
    date_match = _DATE_RE.match(range_str)
    if date_match:
        start = _parse_date(range_str)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        return _build(range_str, start, end)

    # fallback → today
    return _build(f"今天 ({now.strftime('%Y-%m-%d')})", today_start, now)


def _build(label: str, start: datetime, end: datetime) -> TimeRange:
    return TimeRange(
        label=label,
        start=start.isoformat(),
        end=end.isoformat(),
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
    )


def _parse_date(date_str: str) -> datetime:
    """解析 YYYY-MM-DD 为当天 00:00:00 的 datetime"""
    return datetime.strptime(date_str, "%Y-%m-%d")


def date_label_for_filename(range_str: str) -> str:
    """根据范围关键字生成用于文件名的日期串（YYYY-MM-DD）

    today      → 今天的日期
    yesterday  → 昨天的日期
    YYYY-MM-DD → 原样返回
    范围/last-Nd → 返回 None（调用方需按天遍历）
    """
    now = datetime.now()
    if range_str == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if _DATE_RE.match(range_str):
        return range_str
    if range_str == "today":
        return now.strftime("%Y-%m-%d")
    # 范围型返回 None，调用方应使用 enumerate_dates() 按天处理
    return None


def enumerate_dates(range_str: str) -> list[str]:
    """将任意 range_str 展开为日期列表（YYYY-MM-DD），用于按天分文件输出。

    返回值按日期升序排列。
    """
    now = datetime.now()
    today = now.date()

    if range_str == "today":
        return [today.isoformat()]
    if range_str == "yesterday":
        return [(today - timedelta(days=1)).isoformat()]

    # last-Nd
    last_n_match = _LAST_N_RE.match(range_str)
    if last_n_match:
        days = int(last_n_match.group(1))
        return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]

    # YYYY-MM-DD~YYYY-MM-DD
    range_match = _RANGE_RE.match(range_str)
    if range_match:
        start = datetime.strptime(range_match.group(1), "%Y-%m-%d").date()
        end = datetime.strptime(range_match.group(2), "%Y-%m-%d").date()
        dates = []
        current = start
        while current <= end:
            dates.append(current.isoformat())
            current += timedelta(days=1)
        return dates

    # 单日
    if _DATE_RE.match(range_str):
        return [range_str]

    # fallback
    return [today.isoformat()]
