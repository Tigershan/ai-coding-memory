"""scope_resolver - workspace + domain-mapping → 召回路径列表

输入：
    workspace_path : 当前 IDE workspace 绝对路径（由 workspace_detector 提供）
    mode           : "auto" | "current_project" | "domain" | "general" | "all"

输出：
    {
        "include_paths": [Path, ...],   # 真实存在的子库路径列表
        "project": str | None,
        "domain": str | None,
        "mode": str,                    # 实际生效的模式
        "warnings": [str, ...],
    }

模式语义（与 design.md §8.2 对齐）：
    auto             ：project + domain + general 全开（推荐默认）
    current_project  ：仅当前 project
    domain           ：仅当前 domain（无映射时降级到 general）
    general          ：仅通用层
    all              ：整个 wiki 根（用于"全局搜索"场景）

失败模式：
    - workspace 为空 → 返回 general 兜底（不要返回空，否则 IDE 召回什么都没有）
    - domain-mapping.yml 不存在 → project + general，warning
    - domain-mapping.yml 解析失败 → 同上 + warning
    - 子库目录不存在 → 静默过滤（IDE 不需要看到"找不到的路径"）
"""

import sys
from pathlib import Path
from typing import Any

# yaml 是可选依赖：缺失时 domain 映射降级
try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from .paths_ext import (
    DOMAIN_MAPPING_PATH,
    SCOPE_DIR_NAME,
    WIKI_ROOT,
)


VALID_MODES = ("auto", "current_project", "domain", "general", "all")


def _load_domain_mapping() -> tuple[dict, list[str]]:
    """读取 domain mapping 并返回 (mapping_dict, warnings)"""
    warnings: list[str] = []
    if not DOMAIN_MAPPING_PATH.exists():
        warnings.append(
            f"domain-mapping.yml 不存在 ({DOMAIN_MAPPING_PATH}); "
            "domain 层将不可用"
        )
        return {"domains": {}}, warnings
    if not _HAS_YAML:
        warnings.append(
            "PyYAML 未安装，无法解析 domain-mapping.yml; "
            "请运行: pip3 install pyyaml"
        )
        return {"domains": {}}, warnings
    try:
        with open(DOMAIN_MAPPING_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 - mapping 解析失败不应让 server 崩
        warnings.append(f"domain-mapping.yml 解析失败: {e}")
        return {"domains": {}}, warnings
    if "domains" not in data:
        data["domains"] = {}
    return data, warnings


def _find_domain_for_project(project_name: str, mapping: dict) -> str | None:
    for d_name, d_config in mapping.get("domains", {}).items():
        projects = (d_config or {}).get("projects") or []
        if project_name in projects:
            return d_name
    return None


def resolve_scope(workspace_path: str | None, mode: str = "auto") -> dict[str, Any]:
    """根据 workspace + mode 解析 include_paths"""
    warnings: list[str] = []

    if mode not in VALID_MODES:
        warnings.append(f"非法 mode={mode!r}，回退到 auto")
        mode = "auto"

    # all 模式：直接返回 wiki 根（如果存在）
    if mode == "all":
        paths = [WIKI_ROOT] if WIKI_ROOT.exists() else []
        return {
            "include_paths": paths,
            "project": None,
            "domain": None,
            "mode": mode,
            "warnings": warnings,
        }

    # workspace 缺失：降级到 general（保证 IDE 至少能搜到通用知识）
    if not workspace_path:
        warnings.append("workspace 未识别，降级到 general 范围")
        general_root = WIKI_ROOT / SCOPE_DIR_NAME["general"]
        paths = (
            [d for d in general_root.iterdir() if d.is_dir()]
            if general_root.exists()
            else []
        )
        return {
            "include_paths": paths,
            "project": None,
            "domain": None,
            "mode": "general",
            "warnings": warnings,
        }

    project_name = Path(workspace_path).name
    mapping, m_warnings = _load_domain_mapping()
    warnings.extend(m_warnings)
    domain = _find_domain_for_project(project_name, mapping)

    candidates: list[Path] = []

    if mode in ("auto", "current_project"):
        candidates.append(WIKI_ROOT / SCOPE_DIR_NAME["project"] / project_name)

    if mode in ("auto", "domain"):
        if domain:
            candidates.append(WIKI_ROOT / SCOPE_DIR_NAME["domain"] / domain)
        elif mode == "domain":
            warnings.append(
                f"project={project_name} 在 domain-mapping 中未配置 domain; "
                "回退到 general 范围"
            )
            general_root = WIKI_ROOT / SCOPE_DIR_NAME["general"]
            if general_root.exists():
                candidates.extend(d for d in general_root.iterdir() if d.is_dir())

    if mode in ("auto", "general"):
        general_root = WIKI_ROOT / SCOPE_DIR_NAME["general"]
        if general_root.exists():
            # general 是「分类目录的集合」，把每个分类作为独立 scope path
            candidates.extend(d for d in general_root.iterdir() if d.is_dir())

    # 静默过滤不存在的路径
    include_paths = [p for p in candidates if p.exists()]

    return {
        "include_paths": include_paths,
        "project": project_name,
        "domain": domain,
        "mode": mode,
        "warnings": warnings,
    }


def _debug() -> None:
    """python3 -m lib.scope_resolver 时打印当前解析结果（调试用）"""
    import json

    workspace = sys.argv[1] if len(sys.argv) > 1 else str(Path.cwd())
    mode = sys.argv[2] if len(sys.argv) > 2 else "auto"
    result = resolve_scope(workspace, mode)
    result["include_paths"] = [str(p) for p in result["include_paths"]]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _debug()
