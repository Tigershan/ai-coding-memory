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
        # 双 mode：日常 vs 批量
        "daily_mode": "auto",        # 增量场景 (lazy_trigger / 主动 distill 单天)
        "batch_mode": "auto",        # 批量场景 (init last-Nd)；auto 时检测 Ollama 用 local，否则 host_agent
        "mode": "auto",              # 旧字段，兜底兼容；新字段优先
        "api": {
            "provider": "dashscope",
            "model": "qwen-plus",
            "key_env": "DASHSCOPE_API_KEY",
            "concurrency": 4,
            "daily_budget_yuan": 0,  # 0 = 无限制
        },
        "local": {
            "base": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "timeout_s": 120,
        },
    },
    "agents_md": {
        "enabled": True,
        "paths": ["AGENTS.md"],      # 可加 .claude/CLAUDE.md / .cursor/rules/...
        "max_size": 4096,
    },
    "lazy_trigger": {
        "enabled": True,
        "min_hour": 22,              # 当地小时 ≥ 此值才跑（avoid coding 高峰；host_agent 模式自动放宽）
        "min_interval_hours": 24,
    },
    "distill": {
        "daily_cap": 10,             # host_agent 模式下每日消化上限（保护宿主 IDE LLM 配额）
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


def detect_local_available(model: str = "qwen3:8b", timeout: float = 1.5) -> dict:
    """检查 Ollama 是否可用 + 指定模型是否已 pull。

    返回 {ollama: bool, model_ready: bool, error: str | None}。
    用于 install.sh 自动判断 batch_mode 默认值，以及 init / distill 启动前 sanity check。
    """
    import json as _json
    import urllib.error
    import urllib.request

    out = {"ollama": False, "model_ready": False, "error": None}
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        out["ollama"] = True
        models = [m.get("name", "") for m in (payload.get("models") or [])]
        # Ollama 返回的 name 可能是 "qwen3:8b" 或带 ":latest"，做宽松匹配
        target_base = model.split(":", 1)[0]
        out["model_ready"] = any(
            m == model or m.startswith(target_base + ":") or m == target_base
            for m in models
        )
    except urllib.error.URLError as e:
        out["error"] = f"Ollama 服务不可达 ({e})"
    except (TimeoutError, OSError) as e:
        out["error"] = f"Ollama 连接超时 ({e})"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Ollama 检测异常: {e}"
    return out


def resolve_mode(scope: str = "daily") -> str:
    """解析 daily / batch 场景下应用的 LLM mode。

    优先级：
      1. config.yml llm.<scope>_mode
      2. config.yml llm.mode（旧字段兜底）
      3. scope=batch 且 detect_local_available() ok → "local"
      4. 默认 "host_agent"

    "auto" 也走步骤 3+4 的 fallback。
    """
    if scope not in ("daily", "batch"):
        scope = "daily"
    cfg = load_user_config()
    llm = cfg.get("llm") or {}

    # 优先取 scope-specific mode
    raw = llm.get(f"{scope}_mode")
    if raw and raw != "auto":
        return raw

    # 兜底：旧 llm.mode 字段
    legacy = llm.get("mode")
    if legacy and legacy != "auto":
        return legacy

    # auto：batch 优先 local，daily 优先 host_agent
    if scope == "batch":
        local_cfg = llm.get("local") or {}
        model = local_cfg.get("model") or "qwen3:8b"
        if detect_local_available(model=model).get("model_ready"):
            return "local"
    return "host_agent"


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
