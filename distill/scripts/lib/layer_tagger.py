"""layer_tagger - Step 4: 分层标注

做什么：
    判断 topic 的归属层级（project / domain / general），并产出 tags。
    依赖 step2 的已消解 dialogue 与 step3 的代码筛选结果，作为完整 topic_content。

输入（plan 阶段）：
    需要 step2 dialogue 与 step3 kept_snippets 拼接成 topic_content；
    需要读取 ~/.ai-memory/config/domain-mapping.yml（不存在则降级为空映射）。

输入（assemble 阶段）：
    result_path: Path

输出（assemble 阶段）：
    dict {
        "scope": "project|domain|general",
        "project": str | None,
        "domain": str | None,
        "general_category": str | None,
        "tags": list[str],
        "confidence": float,
        "reasoning": str,
    }

失败模式：
    - confidence 低于阈值 → assemble 阶段会兜底改为 general/misc
    - scope 非法 → 兜底为 general/misc
"""

import json
from pathlib import Path

from .io_utils import write_text_atomic
from .paths_ext import STEP4_SUBDIR
from .prompt_renderer import render_prompt


VALID_SCOPES = {"project", "domain", "general"}
VALID_GENERAL_CATEGORIES = {
    "java", "python", "typescript", "redis", "mysql",
    "debugging", "ai-tools", "git", "shell", "system-design", "misc",
}


def _load_domain_mapping_yaml(mapping_path: Path) -> str:
    """读取 domain-mapping.yml 原文（YAML 直接当文本喂给 LLM 即可）"""
    if not mapping_path.exists():
        return "domains: {}\n# (未配置 domain 映射，所有 project 不会被提升到 domain)"
    return mapping_path.read_text(encoding="utf-8")


def _compose_topic_content(
    topic_title: str,
    dialogue_md: str,
    kept_snippets: list[dict],
    discarded_summary: str,
) -> str:
    """拼出最终 topic 内容供 LLM 判定（与 topic_writer 输出尽量同构）"""
    parts = [f"# {topic_title}\n"]
    parts.append("## 对话\n\n" + dialogue_md + "\n")
    if kept_snippets:
        parts.append("## 关键代码\n")
        for snip in kept_snippets:
            parts.append(
                f"- **[{snip['tier']}]** {snip['annotation']}\n"
                f"```{snip.get('language', 'text')}\n{snip['code']}\n```\n"
            )
    if discarded_summary:
        parts.append(f"## 已丢弃过程性代码\n{discarded_summary}\n")
    return "\n".join(parts)


def build_task(
    session: dict,
    session_idx: int,
    topic: dict,
    daily_root: Path,
    dialogue_md: str,
    kept_snippets: list[dict],
    discarded_summary: str,
    domain_mapping_path: Path,
) -> dict:
    sid = session.get("sessionId", f"unknown-{session_idx}")
    ide = session.get("ide", "unknown")
    tid = topic["topic_id"]

    base = f"topic-{ide}-{session_idx:03d}-t{tid:02d}"
    rel_prompt = f"{STEP4_SUBDIR}/{base}.prompt.md"
    rel_result = f"{STEP4_SUBDIR}/{base}.result.json"

    prompt_path = daily_root / rel_prompt
    result_path = daily_root / rel_result

    variables = {
        "workspace": session.get("workspace", "unknown"),
        "domain_mapping_yaml": _load_domain_mapping_yaml(domain_mapping_path),
        "topic_md_full": _compose_topic_content(
            topic["title"], dialogue_md, kept_snippets, discarded_summary
        ),
    }
    content = render_prompt(
        step_key="layer_tagging",
        variables=variables,
        result_path=str(result_path),
        result_format="json",
    )
    write_text_atomic(prompt_path, content)

    return {
        "id": f"step4-{ide}-{session_idx:03d}-t{tid:02d}",
        "step": "layer_tagging",
        "session_id": sid,
        "session_index": session_idx,
        "topic_id": tid,
        "topic_meta": dict(topic) if isinstance(topic, dict) else {"topic_id": tid},
        "ide": ide,
        "prompt_file": rel_prompt,
        "result_file": rel_result,
        "status": "pending",
        "depends_on": [f"step3-{ide}-{session_idx:03d}-t{tid:02d}"],
    }


def parse_result(result_path: Path, scope_confidence_threshold: float = 0.6) -> dict:
    if not result_path.exists():
        raise FileNotFoundError(f"step4 结果文件缺失: {result_path}")
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"step4 结果应为 JSON 对象: {result_path}")

    scope = raw.get("scope")
    confidence = float(raw.get("confidence", 0.0) or 0.0)

    # 兜底：scope 非法 / 置信度过低 → 归 general/misc
    if scope not in VALID_SCOPES or confidence < scope_confidence_threshold:
        return {
            "scope": "general",
            "project": None,
            "domain": None,
            "general_category": "misc",
            "tags": list(raw.get("tags") or []),
            "confidence": confidence,
            "reasoning": (raw.get("reasoning") or "")
                         + f" [fallback: scope={scope}, conf={confidence}]",
        }

    project = raw.get("project") if scope == "project" else None
    domain = raw.get("domain") if scope == "domain" else None
    general_cat = raw.get("general_category") if scope == "general" else None
    if scope == "general":
        if general_cat not in VALID_GENERAL_CATEGORIES:
            general_cat = "misc"

    return {
        "scope": scope,
        "project": project,
        "domain": domain,
        "general_category": general_cat,
        "tags": [str(t).lower() for t in (raw.get("tags") or [])][:6],
        "confidence": confidence,
        "reasoning": raw.get("reasoning", "") or "",
    }
