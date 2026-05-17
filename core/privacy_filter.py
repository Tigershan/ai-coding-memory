"""core.privacy_filter - 写入前的 secret 脱敏

distill 与 mcp-server remember 在落盘前调用 redact()。设计原则：
    - 保守命中：宁可漏伤、不可误伤普通代码片段
    - 类型化占位符：替换为 <REDACTED:aws_key> 而非纯 ***，方便后期审计
    - 异常零中断：内部任何错误都不应阻塞 distill / remember；调用方按
      try / except 兜底即可，本模块自身也对每个 pattern 的 sub() 做防御

公开 API：
    redact(text) -> tuple[str, dict[str, int]]
        返回 (脱敏后文本, 命中类型计数)。
        计数值用于审计日志，不暴露原文。
"""

from __future__ import annotations

import re
from typing import Pattern


# ==================== 模式集合 ====================
# 每条 = (类型名, 正则, 替换函数)；替换函数接受 match 返回替换串。
# 顺序敏感：高特异度的放前面（命中后被替换为占位符，后续低特异度规则不再误伤）

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    r"[\s\S]+?"
    r"-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    re.MULTILINE,
)

_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

# AWS Secret 需要左侧上下文（key/secret/aws）以避免误伤普通 base64
_AWS_SECRET_CONTEXTUAL = re.compile(
    r"(?i)(aws[_\-]?secret[_\-]?access[_\-]?key|secret[_\-]?key)"
    r"\s*[:=]\s*['\"]?"
    r"([A-Za-z0-9/+=]{40})"
    r"['\"]?"
)

_OPENAI_TOKEN = re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b")

_SLACK_TOKEN = re.compile(r"\bxox[baprso]-[A-Za-z0-9\-]{10,}\b")

_GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")

_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\b")

# JDBC / 通用 conn-string 中的 password=...
_JDBC_PASSWORD = re.compile(
    r"((?:jdbc:|mysql://|postgres(?:ql)?://|mongodb(?:\+srv)?://)[^\s]*?password=)"
    r"([^\s&;'\"]+)",
    re.IGNORECASE,
)

# 通用 key=value / key: value — 命中常见 secret-y 字段名 + ≥6 字符值
_GENERIC_SECRET_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_\-]?key|access[_\-]?token|auth[_\-]?token)"
    r"\s*[:=]\s*"
    r"['\"]?"
    r"([^\s'\"]{6,})"
    r"['\"]?"
)


def _placeholder(kind: str) -> str:
    return f"<REDACTED:{kind}>"


def _sub_full(kind: str, pattern: Pattern, text: str, counts: dict[str, int]) -> str:
    def _r(_m):
        counts[kind] = counts.get(kind, 0) + 1
        return _placeholder(kind)
    try:
        return pattern.sub(_r, text)
    except Exception:
        return text


def _sub_value(kind: str, pattern: Pattern, text: str, counts: dict[str, int],
               value_group: int) -> str:
    """保留 prefix（如 `password=`），仅替换值部分"""
    def _r(m):
        counts[kind] = counts.get(kind, 0) + 1
        prefix = m.group(0)[: m.start(value_group) - m.start(0)]
        return prefix + _placeholder(kind)
    try:
        return pattern.sub(_r, text)
    except Exception:
        return text


def redact(text: str) -> tuple[str, dict[str, int]]:
    """对 text 做 secret 脱敏。

    返回 (脱敏后文本, {类型名: 命中数})。
    异常时降级为 (原文, {})，保证调用方 pipeline 不被打断。
    """
    if not isinstance(text, str) or not text:
        return text or "", {}
    counts: dict[str, int] = {}
    out = text
    try:
        out = _sub_full("private_key_block", _PRIVATE_KEY_BLOCK, out, counts)
        out = _sub_full("aws_access_key", _AWS_ACCESS_KEY, out, counts)
        out = _sub_value("aws_secret_key", _AWS_SECRET_CONTEXTUAL, out, counts, 2)
        out = _sub_full("openai_token", _OPENAI_TOKEN, out, counts)
        out = _sub_full("slack_token", _SLACK_TOKEN, out, counts)
        out = _sub_full("github_token", _GITHUB_TOKEN, out, counts)
        out = _sub_full("jwt", _JWT, out, counts)
        out = _sub_value("jdbc_password", _JDBC_PASSWORD, out, counts, 2)
        out = _sub_value("generic_secret_kv", _GENERIC_SECRET_KV, out, counts, 2)
    except Exception:
        return text, {}
    return out, counts


def total_hits(counts: dict[str, int]) -> int:
    return sum(counts.values()) if counts else 0
