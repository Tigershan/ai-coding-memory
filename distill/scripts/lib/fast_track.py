"""fast_track - 快速通道：合并 step2+3+4 为单次 LLM 调用

做什么：
    针对 medium/low 价值 topic，跳过 step2→3→4 的三次串行 LLM 调用，
    改为单次调用完成：指代消解 + 代码筛选 + 分层标注。
    减少约 66% 的 LLM 请求和等待时间。

输入（plan 阶段）：
    session, topic, daily_root, session_idx, domain_mapping_path

输出（plan 阶段）：
    任务清单条目（step="fast_track"），写入 manifest.tasks

输入（assemble 阶段）：
    result_path: Path  # Agent/auto 写好的 .result.json

输出（assemble 阶段）：
    dict {
        "coref": {dialogue_md, coref_confidence},
        "code":  {kept_snippets, discarded_summary, filter_confidence},
        "layer": {scope, project, domain, general_category, tags, confidence, reasoning},
    }
    三份结果与 step2/3/4 的 parse_result 输出同构，
    可直接传给 topic_writer.write_topic_file。

失败模式：
    - 结果 JSON 缺字段 → ValueError（带路径）
    - scope 非法 / confidence 过低 → 兜底到 general/misc（同 layer_tagger 逻辑）
"""

import json
from pathlib import Path

from .io_utils import write_text_atomic
from .paths_ext import STEPF_SUBDIR
from .prompt_renderer import render_prompt

VALID_SCOPES = {"project", "domain", "general"}
VALID_GENERAL_CATEGORIES = {
    "java", "python", "typescript", "redis", "mysql",
    "debugging", "ai-tools", "git", "shell", "system-design", "misc",
}
VALID_TIERS = {"decision", "educational"}
REQUIRED_SNIPPET = {"tier", "language", "code", "annotation"}


def _extract_topic_messages(session: dict, topic: dict) -> str:
    """切出 topic 范围内的对话，渲染为带 [idx] 前缀的可读文本"""
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


def _load_domain_mapping_yaml(mapping_path: Path) -> str:
    if not mapping_path.exists():
        return "domains: {}\n# (未配置 domain 映射)"
    return mapping_path.read_text(encoding="utf-8")


def build_task(
    session: dict,
    session_idx: int,
    topic: dict,
    daily_root: Path,
    domain_mapping_path: Path,
) -> dict:
    """生成快速通道任务包（合并 step2+3+4）"""
    sid = session.get("sessionId", f"unknown-{session_idx}")
    ide = session.get("ide", "unknown")
    tid = topic["topic_id"]

    base = f"topic-{ide}-{session_idx:03d}-t{tid:02d}"
    rel_prompt = f"{STEPF_SUBDIR}/{base}.prompt.md"
    rel_result = f"{STEPF_SUBDIR}/{base}.result.json"

    prompt_path = daily_root / rel_prompt
    result_path = daily_root / rel_result

    variables = {
        "workspace": session.get("workspace", "unknown"),
        "domain_mapping_yaml": _load_domain_mapping_yaml(domain_mapping_path),
        "topic_title": topic["title"],
        "topic_messages": _extract_topic_messages(session, topic),
    }
    content = render_prompt(
        step_key="fast_track",
        variables=variables,
        result_path=str(result_path),
        result_format="json",
    )
    write_text_atomic(prompt_path, content)

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
        "id": f"stepF-{ide}-{session_idx:03d}-t{tid:02d}",
        "step": "fast_track",
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


def parse_result(result_path: Path, scope_confidence_threshold: float = 0.6) -> dict:
    """解析快速通道结果，拆为与 step2/3/4 同构的三份输出

    Returns:
        {
            "coref":  同 coreference_resolver.parse_result 输出,
            "code":   同 code_filter.parse_result 输出,
            "layer":  同 layer_tagger.parse_result 输出,
        }
    """
    if not result_path.exists():
        raise FileNotFoundError(f"fast_track 结果文件缺失: {result_path}")
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"fast_track 结果应为 JSON 对象: {result_path}")

    # --- coref 部分 ---
    dialogue_md = raw.get("dialogue_md", "")
    if not dialogue_md:
        raise ValueError(f"{result_path} 缺少 dialogue_md 字段")
    coref_confidence = float(raw.get("coref_confidence", 0.5) or 0.5)

    # --- code 部分 ---
    cleaned_snippets = []
    for snippet in raw.get("kept_snippets", []) or []:
        if not isinstance(snippet, dict):
            continue
        if REQUIRED_SNIPPET - snippet.keys():
            continue
        if snippet["tier"] not in VALID_TIERS:
            continue
        cleaned_snippets.append({
            "tier": snippet["tier"],
            "language": snippet.get("language", "text"),
            "code": snippet["code"],
            "annotation": snippet["annotation"],
            "source_msg_idx": snippet.get("source_msg_idx"),
        })

    # --- layer 部分 ---
    scope = raw.get("scope")
    scope_confidence = float(raw.get("scope_confidence", 0.0) or 0.0)

    if scope not in VALID_SCOPES or scope_confidence < scope_confidence_threshold:
        layer = {
            "scope": "general",
            "project": None,
            "domain": None,
            "general_category": "misc",
            "tags": list(raw.get("tags") or []),
            "confidence": scope_confidence,
            "reasoning": (raw.get("reasoning") or "")
                         + f" [fast_track fallback: scope={scope}, conf={scope_confidence}]",
        }
    else:
        project = raw.get("project") if scope == "project" else None
        domain = raw.get("domain") if scope == "domain" else None
        general_cat = raw.get("general_category") if scope == "general" else None
        if scope == "general" and general_cat not in VALID_GENERAL_CATEGORIES:
            general_cat = "misc"

        layer = {
            "scope": scope,
            "project": project,
            "domain": domain,
            "general_category": general_cat,
            "tags": [str(t).lower() for t in (raw.get("tags") or [])][:6],
            "confidence": scope_confidence,
            "reasoning": raw.get("reasoning", "") or "",
        }

    return {
        "coref": {
            "dialogue_md": dialogue_md,
            "coref_confidence": coref_confidence,
        },
        "code": {
            "kept_snippets": cleaned_snippets,
            "discarded_summary": raw.get("discarded_summary", "") or "",
            "filter_confidence": float(raw.get("filter_confidence", 0.5) or 0.5),
        },
        "layer": layer,
    }
