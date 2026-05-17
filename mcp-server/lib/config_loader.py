"""config_loader - 加载 ai-coding-memory 配置（带分层覆盖）

加载顺序（后者覆盖前者）：
    1. <project_root>/config/default.yml   ← 仓库内置默认值（version-controlled）
    2. ~/.ai-memory/config/default.yml     ← 用户覆盖（如果存在）
    3. 环境变量 AI_MEMORY_<UPPER_KEY>      ← 临时调试

约束：
    - PyYAML 缺失时降级为内置硬默认值（不让 server 崩）
    - 任何字段缺失时回退到内置默认值
    - 仅暴露 mcp-server 实际会用到的字段；其他模块（distill/collect/compile）
      用自己的入口（CLI 参数或独立 loader），避免本模块成为巨型上帝对象

公开 API：
    load_config() -> Config  (单例缓存)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .paths_ext import CONFIG_DIR

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# 内置硬默认值：在 yaml 缺失或字段缺失时使用
_DEFAULTS = {
    "top_k": 5,
    "performance_budget_ms": 1000,
    "snippet_context_lines": 2,
    "max_results_before_rerank": 200,
    "max_page_bytes": 60_000,
    "max_snippet_len": 600,
    # 时间衰减（仅作用于 source ∈ {auto, bootstrap}）
    "time_decay_half_life_days": 90,
    "time_decay_floor": 0.5,
    # 向量重排（默认关；需 pip install '.[vector]'）
    "vector_rerank_enabled": False,
    "vector_rerank_model": "BAAI/bge-small-en-v1.5",
    "vector_rerank_top_n": 50,
    "vector_rerank_bm25_weight": 0.3,
}


@dataclass(frozen=True)
class Config:
    """mcp-server 实际生效的配置（不可变快照）"""
    top_k: int
    performance_budget_ms: int
    snippet_context_lines: int
    max_results_before_rerank: int
    max_page_bytes: int
    max_snippet_len: int
    time_decay_half_life_days: int
    time_decay_floor: float
    vector_rerank_enabled: bool
    vector_rerank_model: str
    vector_rerank_top_n: int
    vector_rerank_bm25_weight: float
    sources: list[str]    # 已加载的配置文件路径（self-check 用）
    warnings: list[str]   # 加载过程中的告警


# 单例缓存：MCP server 是常驻进程，配置在启动时一次性加载即可
_cached: Config | None = None


def _load_yaml(path: Path) -> tuple[dict, str | None]:
    """读取并解析 yaml；返回 (data, error_msg)"""
    if not path.exists():
        return {}, None
    if not _HAS_YAML:
        return {}, f"PyYAML 未安装，无法解析 {path}"
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}, f"{path} 顶层不是 dict（实际 {type(data).__name__}），忽略"
        return data, None
    except Exception as e:  # noqa: BLE001 - 配置解析失败不应让 server 崩
        return {}, f"{path} 解析失败: {e}"


def _extract_mcp_section(data: dict) -> dict:
    """从 default.yml 抽取 mcp_server 段（容忍缺失）"""
    section = data.get("mcp_server")
    return section if isinstance(section, dict) else {}


def _coerce_int(val, fallback: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(val, fallback: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return fallback


def _coerce_bool(val, fallback: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
    return fallback


def _coerce_str(val, fallback: str) -> str:
    if isinstance(val, str) and val.strip():
        return val
    return fallback


def _project_default_yml() -> Path:
    """仓库内置 default.yml：mcp-server/lib/config_loader.py → ../../config/default.yml"""
    return Path(__file__).resolve().parent.parent.parent / "config" / "default.yml"


def load_config(force_reload: bool = False) -> Config:
    """加载配置；默认使用单例缓存。force_reload=True 时重新加载（测试用）"""
    global _cached
    if _cached is not None and not force_reload:
        return _cached

    warnings: list[str] = []
    sources: list[str] = []
    merged: dict = dict(_DEFAULTS)

    # 1. 仓库内置 default.yml
    repo_yml = _project_default_yml()
    repo_data, err = _load_yaml(repo_yml)
    if err:
        warnings.append(err)
    if repo_data:
        sources.append(str(repo_yml))
        merged.update(_extract_mcp_section(repo_data))

    # 2. 用户覆盖 ~/.ai-memory/config/default.yml
    user_yml = CONFIG_DIR / "default.yml"
    user_data, err = _load_yaml(user_yml)
    if err:
        warnings.append(err)
    if user_data:
        sources.append(str(user_yml))
        merged.update(_extract_mcp_section(user_data))

    # 3. 环境变量覆盖（仅 int 字段）
    for key in _DEFAULTS:
        env_key = f"AI_MEMORY_{key.upper()}"
        if env_key in os.environ:
            merged[key] = os.environ[env_key]
            sources.append(f"env:{env_key}")

    cfg = Config(
        top_k=_coerce_int(merged.get("top_k"), _DEFAULTS["top_k"]),
        performance_budget_ms=_coerce_int(
            merged.get("performance_budget_ms"), _DEFAULTS["performance_budget_ms"]
        ),
        snippet_context_lines=_coerce_int(
            merged.get("snippet_context_lines"), _DEFAULTS["snippet_context_lines"]
        ),
        max_results_before_rerank=_coerce_int(
            merged.get("max_results_before_rerank"),
            _DEFAULTS["max_results_before_rerank"],
        ),
        max_page_bytes=_coerce_int(
            merged.get("max_page_bytes"), _DEFAULTS["max_page_bytes"]
        ),
        max_snippet_len=_coerce_int(
            merged.get("max_snippet_len"), _DEFAULTS["max_snippet_len"]
        ),
        time_decay_half_life_days=_coerce_int(
            merged.get("time_decay_half_life_days"),
            _DEFAULTS["time_decay_half_life_days"],
        ),
        time_decay_floor=_coerce_float(
            merged.get("time_decay_floor"), _DEFAULTS["time_decay_floor"]
        ),
        vector_rerank_enabled=_coerce_bool(
            merged.get("vector_rerank_enabled"), _DEFAULTS["vector_rerank_enabled"]
        ),
        vector_rerank_model=_coerce_str(
            merged.get("vector_rerank_model"), _DEFAULTS["vector_rerank_model"]
        ),
        vector_rerank_top_n=_coerce_int(
            merged.get("vector_rerank_top_n"), _DEFAULTS["vector_rerank_top_n"]
        ),
        vector_rerank_bm25_weight=_coerce_float(
            merged.get("vector_rerank_bm25_weight"),
            _DEFAULTS["vector_rerank_bm25_weight"],
        ),
        sources=sources,
        warnings=warnings,
    )
    _cached = cfg
    return cfg
