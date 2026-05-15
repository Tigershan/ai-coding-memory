"""scope_router - 按 frontmatter 决定目标子知识库路径

输入：frontmatter dict（来自 lib.frontmatter.parse_topic_file）

输出：
    {
        "scope": "project|domain|general",
        "subwiki_path": Path,         # WIKI_ROOT/<scope>/<safe_name>/
        "subwiki_name": str,          # 安全规整后的名字
        "wiki_topic_label": str,      # 调 init-wiki.sh 时使用的人类可读主题
        "language": "中文" | "English",
        "warnings": list[str],
    }

路由规则（与 docs/design.md §6 / §7 对齐）：
    scope=project  → projects/<safe_project>/
    scope=domain   → domains/<safe_domain>/
    scope=general  → general/<safe_general_category>/
    其他 / 缺失     → general/misc/  + warning

名称规整规则（避免文件系统问题）：
    - 删除路径分隔符 / 控制字符
    - 保留中文 / 英文 / 数字 / 连字符 / 下划线
    - 其他字符替换为 -
    - 首尾去 -, 全空兜底为 unknown
"""

import re
from pathlib import Path
from typing import Any

from .paths_ext import WIKI_ROOT


VALID_SCOPES = ("project", "domain", "general")
GENERAL_FALLBACK_CATEGORY = "misc"

# scope → wiki 根下的子目录名（注意 general 不带 s，与 docs/design.md §7 对齐）
SCOPE_DIR_NAME = {
    "project": "projects",
    "domain": "domains",
    "general": "general",
}


_UNSAFE_CHAR_RE = re.compile(r"[^\w\u4e00-\u9fff\-]")


def sanitize_name(raw: str | None, fallback: str = "unknown") -> str:
    """把 frontmatter 里的 project/domain/general_category 规整为安全目录名"""
    if not raw or not str(raw).strip():
        return fallback
    s = str(raw).strip()
    # 替换路径分隔符、控制字符等不安全字符
    s = _UNSAFE_CHAR_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-_")
    if not s:
        return fallback
    # 防止"."、".."、空名等
    if s in (".", ".."):
        return fallback
    return s[:60]


def _detect_language(workspace: str | None) -> str:
    """简单启发式：workspace 路径含中文则用中文，否则中文（默认中文，与 distill 输出语言一致）

    distill 阶段输出的 topic .md 正文以中文为主，
    所以子知识库默认用中文初始化（init-wiki.sh 第三个参数）。
    """
    return "中文"


def route(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """根据 frontmatter 计算目标子知识库路径"""
    warnings: list[str] = []
    scope = frontmatter.get("scope")

    if scope not in VALID_SCOPES:
        warnings.append(
            f"scope 非法或缺失（got={scope!r}），归入 general/{GENERAL_FALLBACK_CATEGORY}"
        )
        scope = "general"
        subwiki_name = GENERAL_FALLBACK_CATEGORY
        wiki_topic_label = f"{GENERAL_FALLBACK_CATEGORY} 通用知识"
    elif scope == "project":
        raw = frontmatter.get("project")
        if not raw:
            warnings.append("scope=project 但 project 字段缺失，降级到 general/misc")
            scope = "general"
            subwiki_name = GENERAL_FALLBACK_CATEGORY
            wiki_topic_label = f"{GENERAL_FALLBACK_CATEGORY} 通用知识"
        else:
            subwiki_name = sanitize_name(raw)
            wiki_topic_label = f"{raw} 项目知识库"
    elif scope == "domain":
        raw = frontmatter.get("domain")
        if not raw:
            warnings.append("scope=domain 但 domain 字段缺失，降级到 general/misc")
            scope = "general"
            subwiki_name = GENERAL_FALLBACK_CATEGORY
            wiki_topic_label = f"{GENERAL_FALLBACK_CATEGORY} 通用知识"
        else:
            subwiki_name = sanitize_name(raw)
            wiki_topic_label = f"{raw} 领域知识库"
    else:  # general
        raw = frontmatter.get("general_category") or GENERAL_FALLBACK_CATEGORY
        subwiki_name = sanitize_name(raw, fallback=GENERAL_FALLBACK_CATEGORY)
        wiki_topic_label = f"{raw} 通用知识"

    subwiki_path = WIKI_ROOT / SCOPE_DIR_NAME[scope] / subwiki_name
    language = _detect_language(frontmatter.get("workspace"))

    return {
        "scope": scope,
        "subwiki_path": subwiki_path,
        "subwiki_name": subwiki_name,
        "wiki_topic_label": wiki_topic_label,
        "language": language,
        "warnings": warnings,
    }


def is_subwiki_initialized(subwiki_path: Path) -> bool:
    """判断子库是否已经被 init-wiki.sh 初始化过"""
    return (subwiki_path / ".wiki-schema.md").exists()
