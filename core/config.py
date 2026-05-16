"""core.config - 用户配置加载/保存（~/.ai-memory/config.yml）

按 redesign §6.0 / ADR-10：
    config.yml 包含 llm.mode / llm.api.* / agents_md.* 等配置
    完全可选；不存在时使用默认值

接口：
    load_user_config() -> dict
    save_user_config(cfg: dict) -> None
    get_value(key_path, default=None) -> Any   # "llm.mode" 风格点路径
    set_value(key_path, value) -> None
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import frontmatter as fm
from .paths import CONFIG_DIR, USER_CONFIG_PATH


# 默认配置（仅文档作用，实际默认在各模块 hardcode）
DEFAULT_CONFIG: dict = {
    "llm": {
        "mode": "auto",              # auto | host_agent | api | local
        "api": {
            "provider": "dashscope",
            "model": "qwen-plus",
            "key_env": "DASHSCOPE_API_KEY",
            "concurrency": 4,
            "daily_budget_yuan": 0,  # 0 = 无限制
        },
    },
    "agents_md": {
        "enabled": True,
        "paths": ["AGENTS.md"],      # 可加 .claude/CLAUDE.md / .cursor/rules/...
        "max_size": 4096,
    },
    "lazy_trigger": {
        "enabled": True,
        "min_hour": 22,              # 当地小时 ≥ 此值才跑（避免 coding 高峰）
        "min_interval_hours": 24,
    },
}


def load_user_config() -> dict:
    """读 ~/.ai-memory/config.yml，不存在返回 {}（不抛错）"""
    if not USER_CONFIG_PATH.exists():
        return {}
    try:
        text = USER_CONFIG_PATH.read_text(encoding="utf-8")
        return fm._parse_yaml(text)
    except Exception:
        return {}


def save_user_config(cfg: dict) -> Path:
    """写 ~/.ai-memory/config.yml（覆写，原子 write）"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(fm._dump_yaml(cfg)) + "\n"
    tmp = USER_CONFIG_PATH.with_suffix(".yml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(USER_CONFIG_PATH)
    return USER_CONFIG_PATH


def get_value(key_path: str, default: Any = None) -> Any:
    """点路径读：get_value('llm.mode') / get_value('agents_md.paths')"""
    cfg = load_user_config()
    cur: Any = cfg
    for part in key_path.split("."):
        if not isinstance(cur, dict):
            return default
        if part not in cur:
            return default
        cur = cur[part]
    return cur


def set_value(key_path: str, value: Any) -> Path:
    """点路径写。中间层不存在自动创建嵌套 dict。"""
    cfg = load_user_config()
    parts = key_path.split(".")
    cur = cfg
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    return save_user_config(cfg)


def remove_value(key_path: str) -> Path | None:
    """点路径删；中间层缺失返回 None。"""
    cfg = load_user_config()
    parts = key_path.split(".")
    cur = cfg
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return None
    del cur[parts[-1]]
    return save_user_config(cfg)
