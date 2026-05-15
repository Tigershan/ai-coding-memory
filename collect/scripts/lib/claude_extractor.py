"""Claude Code 会话提取器

数据源：~/.claude/projects/
  - 每个项目一个子目录，目录名为路径编码（如 -Users-tiger-projects-foo）
  - 每个会话一个 .jsonl 文件，UUID 命名
  - 每行一个 JSON 对象，type 字段区分消息类型：
      user       用户消息（message.role=user, message.content=str）
      assistant  助手消息（message.role=assistant, message.content=list|str）
      attachment 附件（跳过）
      file-history-snapshot / permission-mode / system / last-prompt 等（跳过）

会话过滤：
  - isMeta=true 的 user 消息跳过（系统元数据）
  - isSidechain=true 的消息跳过（分支对话）
  - assistant 消息的 content 可能是 list[dict]，需提取 type=text 的部分

失败模式：
  - projects 目录不存在 → 返回 []，warning 中说明
  - 单个 .jsonl 文件解析失败 → 跳过
"""

import json
from datetime import datetime
from pathlib import Path

from .cleaners import filter_and_clean_conversation


def extract_claude_code_sessions(
    projects_dir: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict], list[str]]:
    """从 Claude Code 的 projects 目录提取会话

    Args:
        projects_dir: ~/.claude/projects 路径
        start_ms: 时间窗口起（毫秒）
        end_ms: 时间窗口止（毫秒）

    Returns:
        (sessions, warnings)
    """
    warnings: list[str] = []
    sessions: list[dict] = []

    if not projects_dir.exists():
        warnings.append(f"未找到 Claude Code 项目目录: {projects_dir}")
        return sessions, warnings

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        workspace = _decode_project_path(project_dir.name)

        for jsonl_file in project_dir.glob("*.jsonl"):
            # 跳过 subagents 目录下的文件
            if "subagents" in str(jsonl_file):
                continue

            try:
                session = _parse_session_file(
                    jsonl_file, workspace, start_ms, end_ms
                )
                if session:
                    sessions.append(session)
            except Exception as exc:
                warnings.append(
                    f"无法解析 Claude Code 会话 {jsonl_file.name}: {exc}"
                )

    return sessions, warnings


def _decode_project_path(dir_name: str) -> str:
    """将 Claude Code 的项目目录名解码为真实路径

    '-Users-tiger-projects-foo' → '/Users/tiger/projects/foo'
    """
    if dir_name.startswith("-"):
        return dir_name.replace("-", "/", 1).replace("-", "/")
    return dir_name


def _parse_session_file(
    jsonl_path: Path,
    workspace: str,
    start_ms: int,
    end_ms: int,
) -> dict | None:
    """解析单个 .jsonl 会话文件

    Returns:
        Session dict 或 None（不在时间窗口内或无有效消息）
    """
    messages: list[dict] = []
    session_id = jsonl_path.stem
    earliest_ts: float | None = None
    latest_ts: float | None = None

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            timestamp_str = entry.get("timestamp", "")

            # 解析时间戳（ISO 8601 格式）
            ts_ms = _parse_iso_timestamp_ms(timestamp_str)

            if ts_ms is not None:
                if earliest_ts is None or ts_ms < earliest_ts:
                    earliest_ts = ts_ms
                if latest_ts is None or ts_ms > latest_ts:
                    latest_ts = ts_ms

            # 只处理 user 和 assistant 消息
            if entry_type == "user":
                if entry.get("isMeta"):
                    continue
                if entry.get("isSidechain"):
                    continue
                msg = entry.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    messages.append({"role": "user", "content": content})

            elif entry_type == "assistant":
                if entry.get("isSidechain"):
                    continue
                msg = entry.get("message", {})
                content = _extract_assistant_content(msg.get("content", ""))
                if content:
                    messages.append({"role": "assistant", "content": content})

    if not messages:
        return None

    # 时间窗口过滤
    if earliest_ts is None:
        return None

    in_range = (
        (start_ms <= earliest_ts <= end_ms)
        or (latest_ts is not None and start_ms <= latest_ts <= end_ms)
        or (earliest_ts <= start_ms and latest_ts is not None and latest_ts >= end_ms)
    )
    if not in_range:
        return None

    cleaned_conversation = filter_and_clean_conversation(messages)
    if not cleaned_conversation:
        return None

    created_dt = datetime.fromtimestamp(earliest_ts / 1000)
    updated_dt = datetime.fromtimestamp((latest_ts or earliest_ts) / 1000)
    session_name = _infer_session_name(messages)

    return {
        "ide": "claude-code",
        "sessionId": session_id,
        "name": session_name,
        "createdAt": created_dt.isoformat(),
        "lastUpdatedAt": updated_dt.isoformat(),
        "status": "completed",
        "workspace": workspace,
        "messageCount": len(cleaned_conversation),
        "conversation": cleaned_conversation,
    }


def _extract_assistant_content(content) -> str:
    """提取 assistant 消息的文本内容

    content 可能是 str 或 list[dict]。
    list 格式时只提取 type=text 的部分，跳过 tool_use 等。
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
        return "\n".join(text_parts).strip()
    return ""


def _infer_session_name(messages: list[dict]) -> str:
    """用第一条 user 消息的前 60 字作为会话名"""
    for msg in messages:
        if msg["role"] == "user" and msg["content"]:
            return msg["content"][:60].strip()
    return "Claude Code 会话"


def _parse_iso_timestamp_ms(ts_str: str) -> float | None:
    """解析 ISO 8601 时间戳为毫秒数

    支持格式：2026-04-22T03:37:42.400Z
    """
    if not ts_str:
        return None
    try:
        # 处理 Z 结尾的 UTC 时间
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.timestamp() * 1000
    except (ValueError, OSError):
        return None
