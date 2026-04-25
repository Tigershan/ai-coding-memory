"""时间范围计算

接受 'today' / 'yesterday' 等关键字，返回带毫秒戳的时间窗口。
"""

from datetime import datetime, timedelta
from typing import TypedDict


class TimeRange(TypedDict):
    label: str
    start: str        # ISO 格式
    end: str
    start_ms: int
    end_ms: int


def compute_time_range(range_str: str) -> TimeRange:
    """根据范围关键字计算起止时间窗口

    Args:
        range_str: 'today' | 'yesterday'

    Returns:
        TimeRange 字典，含人类可读 label 和毫秒戳。

    失败模式：
        - 未知 range_str → 静默 fallback 到 'today'
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    range_map = {
        "today": (today_start, now),
        "yesterday": (
            today_start - timedelta(days=1),
            today_start - timedelta(microseconds=1),
        ),
    }
    start, end = range_map.get(range_str, range_map["today"])

    label_map = {
        "today": f"今天 ({now.strftime('%Y-%m-%d')})",
        "yesterday": f"昨天 ({(now - timedelta(days=1)).strftime('%Y-%m-%d')})",
    }

    return TimeRange(
        label=label_map.get(range_str, label_map["today"]),
        start=start.isoformat(),
        end=end.isoformat(),
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
    )


def date_label_for_filename(range_str: str) -> str:
    """根据范围关键字生成用于文件名的日期串（YYYY-MM-DD）

    today  → 今天的日期
    yesterday → 昨天的日期
    """
    now = datetime.now()
    if range_str == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")
