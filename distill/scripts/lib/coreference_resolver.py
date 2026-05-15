"""coreference_resolver - Step 2: 指代消解

做什么：
    针对 step1 切出的每个 topic，把对话中的"这个/它/上面那个"等指代
    替换为具体名称，让 topic 完全自描述。

输入（plan 阶段）：
    session, topic, daily_root, session_idx

输出（plan 阶段）：
    任务清单条目（写入 manifest.tasks）

输入（assemble 阶段）：
    result_path: Path  # Agent 写好的 .result.md（Markdown 格式）

输出（assemble 阶段）：
    dict {
        "dialogue_md": str,             # 改写后的 Markdown 对话
        "coref_confidence": float,      # 末尾标注的 confidence
    }

失败模式：
    - 结果文件缺失 → FileNotFoundError
    - 缺少 confidence 标注 → 默认 0.5 + warning
"""

import re
from pathlib import Path

from .io_utils import write_text_atomic
from .paths_ext import STEP2_SUBDIR
from .prompt_renderer import render_prompt


_CONF_PATTERN = re.compile(r"\[coreference_confidence:\s*([0-9.]+)\s*\]", re.IGNORECASE)


def _extract_topic_messages(session: dict, topic: dict) -> str:
    """切出 topic 范围内的对话，渲染为 user/assistant 交替的可读文本"""
    conv = session.get("conversation", [])
    start, end = topic["start_msg_idx"], topic["end_msg_idx"]
    pieces = []
    for idx in range(start, min(end + 1, len(conv))):
        msg = conv[idx]
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if len(content) > 4000:
            content = content[:4000] + "\n...[已截断]"
        pieces.append(f"[{idx}] **{role}**:\n{content}\n")
    return "\n".join(pieces)


def build_task(
    session: dict,
    session_idx: int,
    topic: dict,
    daily_root: Path,
) -> dict:
    sid = session.get("sessionId", f"unknown-{session_idx}")
    ide = session.get("ide", "unknown")
    tid = topic["topic_id"]

    base = f"topic-{ide}-{session_idx:03d}-t{tid:02d}"
    rel_prompt = f"{STEP2_SUBDIR}/{base}.prompt.md"
    rel_result = f"{STEP2_SUBDIR}/{base}.result.md"

    prompt_path = daily_root / rel_prompt
    result_path = daily_root / rel_result

    variables = {
        "workspace": session.get("workspace", "unknown"),
        "topic_title": topic["title"],
        "topic_messages": _extract_topic_messages(session, topic),
    }
    content = render_prompt(
        step_key="coreference",
        variables=variables,
        result_path=str(result_path),
        result_format="markdown",
    )
    write_text_atomic(prompt_path, content)

    # 把 topic 元信息冻结到 step2 task 中，让下游 step3/4/assemble 不再依赖
    # 重新解析 step1 result 文件（避免 result 被外部改动时产生漂移）
    topic_meta = {
        "topic_id": tid,
        "title": topic.get("title", ""),
        "summary": topic.get("summary", ""),
        "estimated_value": topic.get("estimated_value", "low"),
        "confidence": float(topic.get("confidence", 0.0) or 0.0),
        "start_msg_idx": int(topic.get("start_msg_idx", 0)),
        "end_msg_idx": int(topic.get("end_msg_idx", 0)),
    }

    return {
        "id": f"step2-{ide}-{session_idx:03d}-t{tid:02d}",
        "step": "coreference",
        "session_id": sid,
        "session_index": session_idx,
        "topic_id": tid,
        "topic_meta": topic_meta,
        "ide": ide,
        "prompt_file": rel_prompt,
        "result_file": rel_result,
        "status": "pending",
        "depends_on": [f"step1-{ide}-{session_idx:03d}"],
    }


def parse_result(result_path: Path) -> dict:
    if not result_path.exists():
        raise FileNotFoundError(f"step2 结果文件缺失: {result_path}")
    text = result_path.read_text(encoding="utf-8").strip()

    confidence = 0.5
    match = _CONF_PATTERN.search(text)
    if match:
        try:
            confidence = float(match.group(1))
        except ValueError:
            pass
        # 移除 confidence 行，避免污染最终 topic .md
        text = _CONF_PATTERN.sub("", text).strip()

    return {
        "dialogue_md": text,
        "coref_confidence": confidence,
    }
