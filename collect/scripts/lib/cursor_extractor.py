"""Cursor / Qoder 会话提取器

数据源：~/Library/Application Support/{Cursor,Qoder}/User/globalStorage/state.vscdb（SQLite）
关键表：cursorDiskKV (key, value)
关键 key 模式：
  - composerData:<id>           主会话元数据
  - bubbleId:<composer>:<bubble>  对话气泡内容
  - composer.content.%<bubble>%   备用内容键
  - agentKv:blob:%<composer>%     agent KV blob

返回：list[Session]，Session 结构见 SCHEMA.md
失败模式：
  - 数据库不存在 → 返回 []，warning 中说明
  - 表结构异常 → 返回 []，warning 中说明
  - 单条会话解析失败 → 跳过，warning 中说明
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .cleaners import filter_and_clean_conversation


def extract_vscode_sessions(
    ide_name: str,
    db_path: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict], list[str]]:
    """从 Cursor / Qoder 的 SQLite 数据库提取会话

    Args:
        ide_name: 'cursor' 或 'qoder'
        db_path: state.vscdb 的绝对路径
        start_ms: 时间窗口起（毫秒）
        end_ms: 时间窗口止（毫秒）

    Returns:
        (sessions, warnings)
    """
    warnings: list[str] = []
    sessions: list[dict] = []

    if not db_path.exists():
        warnings.append(f"未找到 {ide_name} 数据库文件: {db_path}")
        return sessions, warnings

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA journal_mode=WAL")

        try:
            cursor = conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            )
            composer_rows = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            warnings.append(f"{ide_name} 数据库表结构异常: {exc}")
            conn.close()
            return sessions, warnings

        for key, value in composer_rows:
            try:
                if value is None:
                    continue
                data = json.loads(value)
                created_at = data.get("createdAt", 0)
                last_updated = data.get("lastUpdatedAt", created_at)

                # 时间窗口过滤（创建时间 OR 最后更新时间命中即收录）
                in_range = (
                    start_ms <= created_at <= end_ms
                    or start_ms <= last_updated <= end_ms
                )
                if not in_range:
                    continue

                composer_id = data.get(
                    "composerId",
                    key.replace("composerData:", "")
                )
                session_name = (
                    data.get("name", "")
                    or data.get("text", "")[:80]
                    or f"会话 {composer_id[:8]}"
                )

                conversation = _extract_vscode_conversation(conn, data)
                cleaned_conversation = filter_and_clean_conversation(conversation)

                if not cleaned_conversation:
                    continue

                workspace = _extract_workspace(data)

                sessions.append({
                    "ide": ide_name,
                    "sessionId": composer_id,
                    "name": session_name.strip(),
                    "createdAt": datetime.fromtimestamp(created_at / 1000).isoformat(),
                    "lastUpdatedAt": datetime.fromtimestamp(last_updated / 1000).isoformat(),
                    "status": data.get("status", "unknown"),
                    "workspace": workspace,
                    "messageCount": len(cleaned_conversation),
                    "conversation": cleaned_conversation,
                })

            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                warnings.append(f"无法解析 {ide_name} 会话 {key}: {exc}")

        conn.close()

    except sqlite3.DatabaseError as exc:
        warnings.append(f"无法读取 {ide_name} 数据库 ({db_path}): {exc}")

    return sessions, warnings


def _extract_workspace(composer_data: dict) -> str:
    """从 composer context 中粗粒度推断 workspace 路径

    取第一个 fileSelection 的 URI 路径前 4 段作为 workspace。
    若无法推断，返回空字符串（distill 阶段会再处理）。
    """
    context = composer_data.get("context", {})
    file_selections = context.get("fileSelections", [])
    if not file_selections:
        return ""
    first_file = file_selections[0].get("uri", {}).get("path", "")
    if not first_file:
        return ""
    parts = first_file.split("/")
    if len(parts) > 3:
        return "/".join(parts[:4])
    return ""


def _extract_vscode_conversation(
    conn: sqlite3.Connection,
    composer_data: dict,
) -> list[dict]:
    """从 composerData 提取对话内容（多种数据形态兜底）

    存储形态历经多次演进，依次尝试：
      1. conversationMap（旧版本内嵌）
      2. fullConversationHeadersOnly + bubbleId 关联查询
      3. agentKv:blob 兜底
      4. text 字段最后兜底
    """
    conversation: list[dict] = []

    # 方式 1：conversationMap 内嵌
    conversation_map = composer_data.get("conversationMap", {})
    if conversation_map:
        for _bubble_id, bubble_data in conversation_map.items():
            if not isinstance(bubble_data, dict):
                continue
            role = "user" if bubble_data.get("type", 0) == 1 else "assistant"
            text = bubble_data.get("text", "")
            if text:
                conversation.append({"role": role, "content": text})
        if conversation:
            return conversation

    # 方式 2：headers + 关联气泡查询
    headers = composer_data.get("fullConversationHeadersOnly", [])
    composer_id = composer_data.get("composerId", "")
    if headers and composer_id:
        for header in headers:
            bubble_id = header.get("bubbleId", "")
            if not bubble_id:
                continue
            role = "user" if header.get("type", 0) == 1 else "assistant"
            text = _query_bubble_text(conn, composer_id, bubble_id)
            if text:
                conversation.append({"role": role, "content": text})
        if conversation:
            return conversation

    # 方式 3：agentKv:blob 兜底
    if composer_id:
        try:
            blob_cursor = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key LIKE ?",
                (f"agentKv:blob:%{composer_id}%",),
            )
            for row in blob_cursor:
                if row[0] is None:
                    continue
                try:
                    blob_data = json.loads(row[0])
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(blob_data, dict):
                    continue
                role = blob_data.get("role", "unknown")
                text = blob_data.get("text", blob_data.get("content", ""))
                if text and role in ("user", "assistant"):
                    conversation.append({"role": role, "content": text})
        except sqlite3.OperationalError:
            pass

    # 方式 4：text 字段最后兜底
    if not conversation:
        text = composer_data.get("text", "")
        if text:
            conversation.append({"role": "user", "content": text})

    return conversation


def _query_bubble_text(
    conn: sqlite3.Connection,
    composer_id: str,
    bubble_id: str,
) -> str:
    """先查 bubbleId 直接键，失败再用 LIKE 查 composer.content"""
    # 尝试 1：精确键
    try:
        bubble_key = f"bubbleId:{composer_id}:{bubble_id}"
        row = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (bubble_key,),
        ).fetchone()
        if row and row[0]:
            try:
                bubble_data = json.loads(row[0])
                if isinstance(bubble_data, dict):
                    text = bubble_data.get("text", "")
                    if text:
                        return text
            except (json.JSONDecodeError, ValueError):
                pass
    except sqlite3.OperationalError:
        pass

    # 尝试 2：模糊键
    try:
        row = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key LIKE ?",
            (f"composer.content.%{bubble_id}%",),
        ).fetchone()
        if row and row[0]:
            content_text = row[0]
            try:
                content_data = json.loads(content_text)
                if isinstance(content_data, str):
                    return content_data
                if isinstance(content_data, dict):
                    return content_data.get(
                        "text",
                        content_data.get("content", str(content_data)),
                    )
                return str(content_data)
            except (json.JSONDecodeError, ValueError):
                return content_text
    except sqlite3.OperationalError:
        pass

    return ""
