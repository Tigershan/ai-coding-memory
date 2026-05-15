"""scope_resolver - workspace → 召回路径列表

[NOTE] P0 减法版：
    - 砍掉了 domain 层（ADR-1，domain-mapping.yml 不再使用）
    - 暂时仍用旧 wiki/{projects,general} 目录布局
    - P4 召回升级时会切换到新数据模型（personal/ + projects/<git_remote_key>/）
      并加跨项目经验迁移（ADR 见 redesign §6.6）

输入：
    workspace_path : 当前 IDE workspace 绝对路径（由 workspace_detector 提供）
    mode           : "auto" | "current_project" | "general" | "all"

输出：
    {
        "include_paths": [Path, ...],   # 真实存在的子库路径列表
        "project": str | None,
        "mode": str,                    # 实际生效的模式
        "warnings": [str, ...],
    }

模式语义：
    auto             ：project + general（默认）
    current_project  ：仅当前 project
    general          ：仅通用层
    all              ：整个 wiki 根（用于"全局搜索"场景）

失败模式：
    - workspace 为空 → 返回 general 兜底
    - 子库目录不存在 → 静默过滤
"""

import sys
from pathlib import Path
from typing import Any

from .paths_ext import SCOPE_DIR_NAME, WIKI_ROOT


VALID_MODES = ("auto", "current_project", "general", "all")


def resolve_scope(workspace_path: str | None, mode: str = "auto") -> dict[str, Any]:
    """根据 workspace + mode 解析 include_paths"""
    warnings: list[str] = []

    if mode not in VALID_MODES:
        warnings.append(f"非法 mode={mode!r}，回退到 auto")
        mode = "auto"

    # all 模式：直接返回 wiki 根
    if mode == "all":
        paths = [WIKI_ROOT] if WIKI_ROOT.exists() else []
        return {
            "include_paths": paths,
            "project": None,
            "mode": mode,
            "warnings": warnings,
        }

    # workspace 缺失：降级到 general
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
            "mode": "general",
            "warnings": warnings,
        }

    project_name = Path(workspace_path).name
    candidates: list[Path] = []

    if mode in ("auto", "current_project"):
        candidates.append(WIKI_ROOT / SCOPE_DIR_NAME["project"] / project_name)

    if mode in ("auto", "general"):
        general_root = WIKI_ROOT / SCOPE_DIR_NAME["general"]
        if general_root.exists():
            # general 下每个分类目录作为独立 scope path
            candidates.extend(d for d in general_root.iterdir() if d.is_dir())

    # 静默过滤不存在的路径
    include_paths = [p for p in candidates if p.exists()]

    return {
        "include_paths": include_paths,
        "project": project_name,
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
