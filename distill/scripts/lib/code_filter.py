"""code_filter - Step 3: 代码筛选

做什么：
    分析 topic 对话中的所有代码片段，按 decision/educational/process 三级分类。
    decision 与 educational 保留，process 仅在 discarded_summary 中描述。

输入（plan 阶段）：
    依赖 step2 结果（已消解的 dialogue_md），所以 build_task 需要传入
    step2 的 result_path 来作为 prompt 的输入素材。

输出（plan 阶段）：
    任务清单条目（depends_on=step2 对应任务）

输入（assemble 阶段）：
    result_path: Path

输出（assemble 阶段）：
    dict {
        "kept_snippets": [{tier, language, code, annotation, source_msg_idx}, ...],
        "discarded_summary": str,
        "filter_confidence": float,
    }

失败模式：
    - 结果不合法 / 缺字段 → ValueError
    - 完全无代码 → kept_snippets 为空数组（合法）
"""

import json
from pathlib import Path

from .io_utils import write_text_atomic
from .paths_ext import STEP3_SUBDIR
from .prompt_renderer import render_prompt


VALID_TIERS = {"decision", "educational"}
REQUIRED_TOP = {"kept_snippets", "discarded_summary", "filter_confidence"}
REQUIRED_SNIPPET = {"tier", "language", "code", "annotation"}


def build_task(
    session: dict,
    session_idx: int,
    topic: dict,
    daily_root: Path,
    step2_dialogue: str,
) -> dict:
    sid = session.get("sessionId", f"unknown-{session_idx}")
    ide = session.get("ide", "unknown")
    tid = topic["topic_id"]

    base = f"topic-{ide}-{session_idx:03d}-t{tid:02d}"
    rel_prompt = f"{STEP3_SUBDIR}/{base}.prompt.md"
    rel_result = f"{STEP3_SUBDIR}/{base}.result.json"

    prompt_path = daily_root / rel_prompt
    result_path = daily_root / rel_result

    variables = {
        "topic_title": topic["title"],
        "dialogue_with_code_blocks": step2_dialogue,
    }
    content = render_prompt(
        step_key="code_filter",
        variables=variables,
        result_path=str(result_path),
        result_format="json",
    )
    write_text_atomic(prompt_path, content)

    return {
        "id": f"step3-{ide}-{session_idx:03d}-t{tid:02d}",
        "step": "code_filter",
        "session_id": sid,
        "session_index": session_idx,
        "topic_id": tid,
        "topic_meta": dict(topic) if isinstance(topic, dict) else {"topic_id": tid},
        "ide": ide,
        "prompt_file": rel_prompt,
        "result_file": rel_result,
        "status": "pending",
        "depends_on": [f"step2-{ide}-{session_idx:03d}-t{tid:02d}"],
    }


def parse_result(result_path: Path) -> dict:
    if not result_path.exists():
        raise FileNotFoundError(f"step3 结果文件缺失: {result_path}")
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"step3 结果应为 JSON 对象: {result_path}")

    missing = REQUIRED_TOP - raw.keys()
    if missing:
        raise ValueError(f"{result_path} 顶层缺字段 {missing}")

    cleaned_snippets = []
    for s in raw.get("kept_snippets", []) or []:
        if not isinstance(s, dict):
            continue
        if REQUIRED_SNIPPET - s.keys():
            # 字段不全的代码片段直接跳过，不阻塞
            continue
        if s["tier"] not in VALID_TIERS:
            continue
        cleaned_snippets.append({
            "tier": s["tier"],
            "language": s.get("language", "text"),
            "code": s["code"],
            "annotation": s["annotation"],
            "source_msg_idx": s.get("source_msg_idx"),
        })

    return {
        "kept_snippets": cleaned_snippets,
        "discarded_summary": raw.get("discarded_summary", "") or "",
        "filter_confidence": float(raw.get("filter_confidence", 0.5) or 0.5),
    }
