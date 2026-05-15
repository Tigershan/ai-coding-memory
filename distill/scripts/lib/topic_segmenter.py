"""topic_segmenter - Step 1: 主题切分

做什么：
    把一个 session（多轮对话）切分为若干自洽的 topic 块。
    本模块在 Agent 编排模式下扮演"任务包生成器 + 结果解析器"双向角色。

输入（plan 阶段）：
    session: dict   # collect 输出的单个 session
    out_dir: Path   # step1-segment 子目录

输出（plan 阶段）：
    list[dict]      # 任务清单条目，待写入 manifest.tasks

输入（assemble 阶段）：
    result_path: Path  # Agent 写好的 .result.json

输出（assemble 阶段）：
    list[dict]      # 已校验的 topic 列表，每项含 topic_id/title/start_msg_idx/...

失败模式：
    - 结果文件缺失 → raise FileNotFoundError
    - JSON 不合法 / 字段缺失 → raise ValueError（带文件路径）
    - 单条 topic 超出 msg 范围 → 自动夹紧到合法区间，记录 warning
"""

import json
from pathlib import Path

from .io_utils import write_text_atomic
from .paths_ext import STEP1_SUBDIR
from .prompt_renderer import render_prompt


REQUIRED_FIELDS = {
    "topic_id", "title", "start_msg_idx", "end_msg_idx",
    "summary", "estimated_value", "confidence",
}
VALID_VALUES = {"high", "medium", "low", "noise"}


def _format_messages(conversation: list[dict]) -> str:
    """把 conversation 渲染为带 [idx] 前缀的可读文本"""
    lines = []
    for idx, msg in enumerate(conversation):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        # 控制单条长度，避免 prompt 爆炸
        if len(content) > 4000:
            content = content[:4000] + "\n...[消息已截断]"
        lines.append(f"[{idx}] {role}:\n{content}\n")
    return "\n".join(lines)


def build_task(session: dict, session_idx: int, daily_root: Path) -> dict:
    """生成单个 session 的 step1 任务包

    返回 manifest.tasks 中的一条记录。
    """
    sid = session.get("sessionId", f"unknown-{session_idx}")
    ide = session.get("ide", "unknown")
    safe_sid = sid.replace("/", "_").replace(":", "_")[:60]

    rel_prompt = f"{STEP1_SUBDIR}/session-{ide}-{session_idx:03d}-{safe_sid}.prompt.md"
    rel_result = f"{STEP1_SUBDIR}/session-{ide}-{session_idx:03d}-{safe_sid}.result.json"

    prompt_path = daily_root / rel_prompt
    result_path = daily_root / rel_result

    variables = {
        "workspace": session.get("workspace", "unknown"),
        "session_start_time": session.get("createdAt", "unknown"),
        "messages_with_index": _format_messages(session.get("conversation", [])),
    }
    content = render_prompt(
        step_key="topic_segmentation",
        variables=variables,
        result_path=str(result_path),
        result_format="json",
    )
    write_text_atomic(prompt_path, content)

    return {
        "id": f"step1-{ide}-{session_idx:03d}",
        "step": "topic_segmentation",
        "session_id": sid,
        "session_index": session_idx,
        "ide": ide,
        "workspace": session.get("workspace", ""),
        "prompt_file": rel_prompt,
        "result_file": rel_result,
        "status": "pending",
        "depends_on": [],
    }


def parse_result(result_path: Path, conversation_len: int) -> list[dict]:
    """解析 Agent 写回的结果文件，返回校验后的 topics 列表

    校验项：
        - 字段完整
        - estimated_value 取值合法
        - msg_idx 区间夹紧到 [0, conversation_len-1]
    """
    if not result_path.exists():
        raise FileNotFoundError(f"step1 结果文件缺失: {result_path}")
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"step1 结果应为 JSON 数组: {result_path}")

    topics: list[dict] = []
    for item in raw:
        missing = REQUIRED_FIELDS - item.keys()
        if missing:
            raise ValueError(f"{result_path} 缺少字段 {missing}")
        if item["estimated_value"] not in VALID_VALUES:
            raise ValueError(
                f"{result_path} estimated_value 非法: {item['estimated_value']}"
            )
        # 夹紧 msg 区间
        start = max(0, int(item["start_msg_idx"]))
        end = min(conversation_len - 1, int(item["end_msg_idx"]))
        if end < start:
            end = start
        item["start_msg_idx"] = start
        item["end_msg_idx"] = end
        topics.append(item)

    if not topics:
        # 兜底：把整段对话作为单个 low-value topic（避免 session 整体丢失）
        topics = [{
            "topic_id": 1,
            "title": "未切分对话",
            "start_msg_idx": 0,
            "end_msg_idx": max(0, conversation_len - 1),
            "summary": "Agent 未返回任何 topic，自动兜底为整段保留",
            "estimated_value": "low",
            "confidence": 0.3,
            "reasoning": "fallback: empty segmentation result",
        }]
    return topics
