"""topic_writer - 把 4 step 的结果合并为最终 topic .md 文件

输出契约（详见 docs/design.md 5.2）：
    ~/.ai-memory/raw/topics/YYYY-MM-DD/NNN-{scope}-{slug}.md

frontmatter 字段：
    type / date / session_id / ide / workspace / scope / project / domain
    / general_category / tags
    / knowledge_type / bug_category / correction_count
    / quality{has_conclusion,has_code,estimated_value}
    / source_msg_range
"""

from datetime import date as date_cls
from pathlib import Path

from .io_utils import slugify, write_text_atomic


def _quote_yaml_str(value) -> str:
    """简易 YAML 字符串转义；None → null；list → flow style"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_quote_yaml_str(x) for x in value)
        return f"[{items}]"
    s = str(value)
    # 含特殊字符则加双引号
    if any(c in s for c in (":", "#", "\n", "[", "]", "{", "}", "\"")):
        s_escaped = s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
        return f"\"{s_escaped}\""
    return s


def _build_frontmatter(meta: dict) -> str:
    """有序输出 frontmatter；保持 design.md 5.2 节字段顺序"""
    lines = ["---"]
    ordered = [
        "type", "date", "session_id", "ide", "workspace",
        "scope", "project", "domain", "general_category",
        "tags", "knowledge_type", "bug_category", "correction_count",
    ]
    for k in ordered:
        if k in meta:
            lines.append(f"{k}: {_quote_yaml_str(meta[k])}")
    # quality 子对象
    q = meta.get("quality", {})
    lines.append("quality:")
    lines.append(f"  has_conclusion: {_quote_yaml_str(q.get('has_conclusion', False))}")
    lines.append(f"  has_code: {_quote_yaml_str(q.get('has_code', False))}")
    lines.append(f"  estimated_value: {_quote_yaml_str(q.get('estimated_value', 'low'))}")
    # source_msg_range
    rng = meta.get("source_msg_range", [0, 0])
    lines.append(f"source_msg_range: [{rng[0]}, {rng[1]}]")
    lines.append("---")
    return "\n".join(lines)


def _build_body(
    title: str,
    dialogue_md: str,
    kept_snippets: list[dict],
    discarded_summary: str,
    coref_confidence: float,
    filter_confidence: float,
    layer_reasoning: str,
    corrections: list[dict] | None = None,
    decision_rationale: dict | None = None,
) -> str:
    parts = [f"# {title}\n"]

    # 决策推理链（如有）
    if decision_rationale:
        parts.append("## 💡 决策推理链\n")
        chosen = decision_rationale.get("chosen", "")
        why_chosen = decision_rationale.get("why_chosen", "")
        parts.append(f"**最终选择**：{chosen}\n")
        parts.append(f"**选择理由**：{why_chosen}\n")
        alternatives = decision_rationale.get("alternatives_considered", [])
        if alternatives:
            parts.append("**备选方案**：" + "、".join(alternatives) + "\n")
        why_rejected = decision_rationale.get("why_others_rejected", "")
        if why_rejected:
            parts.append(f"**否决理由**：{why_rejected}\n")

    # 用户纠正记录（如有）
    if corrections:
        parts.append("## ⚡ 用户纠正记录\n")
        for corr in corrections:
            wrong = corr.get("what_ai_got_wrong", "")
            correct = corr.get("correct_answer", "")
            note = corr.get("value_note", "")
            parts.append(f"- **AI 错误**：{wrong}")
            parts.append(f"  **正确答案**：{correct}")
            if note:
                parts.append(f"  **价值点**：{note}")
        parts.append("")

    parts.append("## 对话（已消解指代）\n")
    parts.append(dialogue_md.strip() + "\n")

    if kept_snippets:
        parts.append("## 关键代码\n")
        for snip in kept_snippets:
            tier = snip.get("tier", "decision")
            lang = snip.get("language") or "text"
            ann = snip.get("annotation", "")
            code = snip.get("code", "")
            pattern = snip.get("reusable_pattern")
            header = f"### [{tier}] {ann}"
            if pattern:
                header += f" `pattern:{pattern}`"
            parts.append(header + "\n")
            parts.append(f"```{lang}\n{code}\n```\n")

    if discarded_summary:
        parts.append("## 已丢弃过程性代码\n")
        parts.append(discarded_summary.strip() + "\n")

    parts.append("## distill 元信息\n")
    parts.append(f"- 指代消解置信度：{coref_confidence:.2f}")
    parts.append(f"- 代码筛选置信度：{filter_confidence:.2f}")
    if layer_reasoning:
        parts.append(f"- 分层判定理由：{layer_reasoning}")

    return "\n".join(parts) + "\n"


def write_topic_file(
    out_dir: Path,
    topic_idx: int,
    date_str: str,
    title: str,
    session: dict,
    topic: dict,
    coref: dict,
    code: dict,
    layer: dict,
) -> Path:
    """把 4 step 结果合并为最终 topic .md 文件，返回写出的路径"""
    scope = layer["scope"]
    slug = slugify(title) or "untitled"

    # 文件名：NNN-{scope}-{slug}.md
    name = f"{topic_idx:03d}-{scope}-{slug}.md"
    out_path = out_dir / name

    has_code = bool(code.get("kept_snippets"))
    has_conclusion = topic.get("estimated_value") in ("high", "medium")
    corrections = topic.get("corrections", []) or []
    decision_rationale = topic.get("decision_rationale")
    knowledge_type = topic.get("knowledge_type", "implementation")
    correction_count = len(corrections)

    meta = {
        "type": "distilled-topic",
        "date": date_str,
        "session_id": session.get("sessionId", ""),
        "ide": session.get("ide", ""),
        "workspace": session.get("workspace", ""),
        "scope": scope,
        "project": layer.get("project"),
        "domain": layer.get("domain"),
        "general_category": layer.get("general_category"),
        "tags": layer.get("tags", []) or [],
        "knowledge_type": knowledge_type,
        "bug_category": layer.get("bug_category"),
        "correction_count": correction_count,
        "quality": {
            "has_conclusion": has_conclusion,
            "has_code": has_code,
            "estimated_value": topic.get("estimated_value", "low"),
        },
        "source_msg_range": [
            topic.get("start_msg_idx", 0),
            topic.get("end_msg_idx", 0),
        ],
    }

    fm = _build_frontmatter(meta)
    body = _build_body(
        title=title,
        dialogue_md=coref.get("dialogue_md", ""),
        kept_snippets=code.get("kept_snippets", []),
        discarded_summary=code.get("discarded_summary", ""),
        coref_confidence=coref.get("coref_confidence", 0.5),
        filter_confidence=code.get("filter_confidence", 0.5),
        layer_reasoning=layer.get("reasoning", ""),
        corrections=corrections,
        decision_rationale=decision_rationale,
    )

    write_text_atomic(out_path, fm + "\n\n" + body)
    return out_path


def today_iso() -> str:
    return date_cls.today().isoformat()
