"""distill.heuristic_filter - 启发式噪音过滤（按 redesign §6.1）

5 条保守规则，专为 coding agent 场景设计，**不误杀单轮 QA**：
    1. no-real-interaction       既无 user 又无 assistant
    2. user-input-too-short      user 总输入 < 10 字符（误触/测试）
    3. all-tool-calls            assistant 全是工具调用，文本占比 < 10%
    4. repeated-stuck            user 反复贴同一段内容 3+ 次（卡死场景）
    5. refused-no-followup       AI 拒答且 user 没追问

预期砍掉 15-25%，剩下交给 LLM should_keep 二次过滤。
被过滤的 session 写到 ~/.ai-memory/logs/filtered-YYYY-MM-DD.jsonl 可审计。

输入：
    session : dict 形如 collect 输出的 session
        {
            "ide": "cursor",
            "sessionId": "...",
            "workspace": "/abs/path",
            "conversation": [{"role": "user|assistant", "content": "..."}, ...],
        }

输出：
    (is_noise: bool, reason: str | None)
"""

from __future__ import annotations

import re
from collections import Counter

# 阈值（保守，宁可放过）
MIN_USER_TOTAL_CHARS = 10
ASST_TEXT_RATIO_FLOOR = 0.10
DUPLICATE_THRESHOLD = 3

ABORT_KEYWORDS = (
    "撤销", "取消", "停", "算了", "不对", "重来",
    "stop", "cancel", "undo", "restart", "abort", "nevermind", "never mind",
)

REFUSAL_PATTERNS = (
    r"\b(I cannot|I can't|I'm sorry|I am sorry|I'm unable|I am unable)\b",
    r"\b(无法回答|无法满足|不便回答|抱歉.{0,5}(无法|不能|不便))",
    r"\b(refuse|cannot help|cannot assist)\b",
)

TOOL_CALL_MARKERS = (
    "<tool_use>", "</tool_use>", "<function_call",
    "<tool_call", "tool_calls:",
)


def is_noise(session: dict) -> tuple[bool, str | None]:
    """主入口：判断 session 是否为噪音。返回 (是否噪音, 理由)。
    任意规则命中即返回 True，否则 False。"""
    convo = session.get("conversation") or []
    user_msgs = [m for m in convo if m.get("role") == "user"]
    asst_msgs = [m for m in convo if m.get("role") == "assistant"]

    # 1. 无实质交互
    if not user_msgs or not asst_msgs:
        return True, "no-real-interaction"

    # 2. user 总输入过短
    user_total = sum(len(_text_of(m)) for m in user_msgs)
    if user_total < MIN_USER_TOTAL_CHARS:
        return True, "user-input-too-short"

    # 3. assistant 全是工具调用，文本占比 < 10%
    if _asst_text_ratio(asst_msgs) < ASST_TEXT_RATIO_FLOOR:
        return True, "all-tool-calls-no-thinking"

    # 4. 用户反复贴同一段
    if _has_duplicate_user_msgs(user_msgs, threshold=DUPLICATE_THRESHOLD):
        return True, "repeated-stuck-pattern"

    # 5. AI 拒答未追问
    if _is_refused_no_followup(user_msgs, asst_msgs):
        return True, "refused-no-followup"

    return False, None


# ==================== 内部规则 ====================

def _text_of(msg: dict) -> str:
    """提取消息文本（content 总是 str；做兜底）"""
    c = msg.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        # block 列表（如 anthropic 的 content blocks），把所有 text block 拼起来
        return "\n".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _asst_text_ratio(asst_msgs: list[dict]) -> float:
    """计算 assistant 消息中"实质文本"占比（剥掉工具调用块的字符数 / 总字符数）"""
    total = 0
    text_only = 0
    for m in asst_msgs:
        text = _text_of(m)
        total += len(text)
        text_only += len(_strip_tool_call_blocks(text))
    if total == 0:
        return 0.0
    return text_only / total


def _strip_tool_call_blocks(text: str) -> str:
    """粗略剥掉工具调用相关 XML/JSON 块"""
    out = text
    # 去掉 <tool_use>...</tool_use> 等成对标签
    out = re.sub(r"<tool_use[^>]*>.*?</tool_use>", "", out, flags=re.DOTALL)
    out = re.sub(r"<tool_call[^>]*>.*?</tool_call>", "", out, flags=re.DOTALL)
    out = re.sub(r"<function_call[^>]*>.*?</function_call>", "", out, flags=re.DOTALL)
    # 去掉孤立标记行
    for marker in TOOL_CALL_MARKERS:
        out = out.replace(marker, "")
    # 去掉 markdown 代码块中标 json/xml 的部分（保守：只去明显的 tool_calls JSON 数组）
    out = re.sub(
        r"```(?:json|xml)\s*\n\s*[\"']?tool_calls[\"']?\s*[:=].*?```",
        "",
        out,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return out.strip()


def _has_duplicate_user_msgs(user_msgs: list[dict], threshold: int) -> bool:
    """user 消息中是否有任何一条文本被重复 ≥ threshold 次"""
    if len(user_msgs) < threshold:
        return False
    # 短消息不算（"thanks"重复 5 次不应被视为卡死，得有内容）
    counter = Counter(
        _text_of(m).strip() for m in user_msgs
        if len(_text_of(m).strip()) >= 20
    )
    return any(count >= threshold for count in counter.values())


def _is_refused_no_followup(user_msgs: list[dict], asst_msgs: list[dict]) -> bool:
    """末位 assistant 拒答 + user 没有追问"""
    if not asst_msgs:
        return False
    last_asst = _text_of(asst_msgs[-1])
    if not _looks_like_refusal(last_asst):
        return False
    # user / assistant 数量相同 = 用户没在拒答后追问
    return len(user_msgs) == len(asst_msgs)


def _looks_like_refusal(text: str) -> bool:
    if not text:
        return False
    for pat in REFUSAL_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True
    return False


# ==================== 调试入口 ====================

def _debug() -> None:
    import json
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("usage: heuristic_filter.py <sessions.json>", file=sys.stderr)
        sys.exit(1)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sessions = data.get("sessions", [])
    kept = 0
    filtered: dict[str, int] = {}
    for s in sessions:
        is_noise_, reason = is_noise(s)
        if is_noise_:
            filtered[reason] = filtered.get(reason, 0) + 1
        else:
            kept += 1
    print(json.dumps({
        "total": len(sessions),
        "kept": kept,
        "filtered": filtered,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _debug()
