"""Aone Copilot 会话提取器

数据源：~/.aone_copilot/kv_storage/
  - index.json   全部条目元数据（lastAccess、fileName 等）
  - data/<file>  实际消息内容（每条消息一文件）

会话组装：按 storeSessionId 分组同一会话的 user / bot 消息，按 id 排序。

失败模式：
  - index.json 不存在 → 返回 []，warning 中说明
  - 单条消息文件读取失败 → 跳过
"""

import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path

from .cleaners import filter_and_clean_conversation


def extract_aone_copilot_sessions(
    kv_dir: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict], list[str]]:
    """从 Aone Copilot 的 kv_storage 提取会话

    Args:
        kv_dir: ~/.aone_copilot/kv_storage 路径
        start_ms: 时间窗口起（毫秒）
        end_ms: 时间窗口止（毫秒）

    Returns:
        (sessions, warnings)
    """
    warnings: list[str] = []
    sessions: list[dict] = []

    index_path = kv_dir / "index.json"
    data_dir = kv_dir / "data"

    if not index_path.exists():
        warnings.append(f"未找到 Aone Copilot 索引文件: {index_path}")
        return sessions, warnings

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        warnings.append(f"无法读取 Aone Copilot 索引文件: {exc}")
        return sessions, warnings

    entries = index_data.get("entries", {})

    # 收集时间窗口内的所有消息，按 storeSessionId 分组
    store_session_messages: dict[str, dict] = {}

    for entry_key, entry_meta in entries.items():
        if not isinstance(entry_meta, dict):
            continue

        last_access = entry_meta.get("lastAccess", 0)
        if not (start_ms <= last_access <= end_ms):
            continue

        if entry_key.endswith("-user"):
            role = "user"
        elif entry_key.endswith("-bot"):
            role = "assistant"
        else:
            continue

        file_name = entry_meta.get("fileName", "")
        if not file_name:
            continue

        msg_data = _read_aone_data_file(data_dir / file_name)
        if not msg_data:
            continue

        content = msg_data.get("content", "")
        if not content:
            continue

        store_session_id = msg_data.get("storeSessionId", "unknown")
        msg_id = msg_data.get("id", 0)
        msg_date = msg_data.get("date", "")

        store_session_messages.setdefault(
            store_session_id,
            {"messages": [], "lastAccess": 0},
        )
        store_session_messages[store_session_id]["messages"].append({
            "role": role,
            "content": content,
            "id": msg_id,
            "date": msg_date,
        })
        store_session_messages[store_session_id]["lastAccess"] = max(
            store_session_messages[store_session_id]["lastAccess"],
            last_access,
        )

    # 组装 session
    for store_id, group in store_session_messages.items():
        try:
            sorted_msgs = sorted(group["messages"], key=lambda m: m.get("id", 0))
            conversation = [
                {"role": m["role"], "content": m["content"]}
                for m in sorted_msgs
            ]
            cleaned_conversation = filter_and_clean_conversation(conversation)
            if not cleaned_conversation:
                continue

            last_access_dt = datetime.fromtimestamp(group["lastAccess"] / 1000)
            session_name = _infer_session_name(sorted_msgs)

            sessions.append({
                "ide": "aone-copilot",
                "sessionId": str(store_id),
                "name": session_name,
                "createdAt": last_access_dt.isoformat(),
                "lastUpdatedAt": last_access_dt.isoformat(),
                "status": "completed",
                "workspace": "",  # Aone Copilot 元数据不含 workspace，distill 阶段从内容推断
                "messageCount": len(cleaned_conversation),
                "conversation": cleaned_conversation,
            })

        except (IOError, json.JSONDecodeError, KeyError) as exc:
            warnings.append(f"无法处理 Aone Copilot 会话 {store_id}: {exc}")

    return sessions, warnings


def _infer_session_name(sorted_msgs: list[dict]) -> str:
    """用第一条 user 消息的前 60 字作为会话名"""
    for msg in sorted_msgs:
        if msg["role"] == "user" and msg["content"]:
            return msg["content"][:60].strip()
    return "Aone Copilot 会话"


def _read_aone_data_file(file_path: Path) -> dict | None:
    """读取并解析 Aone Copilot 单条消息文件

    文件格式：
        {"value": "<json string>"}   外层 JSON
        value 解开后是真正的消息 dict，content 字段做了 URL 编码。
    """
    if not file_path.exists():
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    value = data.get("value", "")
    if not isinstance(value, str):
        return None

    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    content = parsed.get("content", "")
    if content:
        try:
            parsed["content"] = urllib.parse.unquote(content)
        except (ValueError, UnicodeDecodeError):
            pass
    return parsed
