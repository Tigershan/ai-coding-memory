"""scope_resolver - workspace → 召回路径列表（redesign §6.6.2）

新数据模型布局（P4 起）：
    ~/.ai-memory/personal/*.md                   跨项目通用
    ~/.ai-memory/projects/<git_remote_dir>/*.md  项目专属

scope_resolver 不再扫旧 wiki/{projects,general} 目录布局（已废弃）。
旧 wiki/ 下的内容由用户用 ai-memory CLI 手动迁移，或 P5 加 migration 脚本。

模式语义（保持向后兼容的命名）：
    auto             : personal + 当前 project + 跨项目高相关（推荐默认）
    current_project  : 仅当前 project
    personal         : 仅 personal（原 "general" 别名）
    general          : 同 personal（向后兼容）
    all              : 整个 ~/.ai-memory（personal + 所有 projects）

workspace 优先级（自上而下）：
    1. workspace 参数（由 IDE 调用 search_memory 时显式传入）
    2. AI_MEMORY_WORKSPACE 环境变量（install 时配的兜底）
    3. workspace_detector（CWD / git rev-parse）—— 仅在 1/2 都没时用，且会附 warning

输出：
    {
        "include_paths": [Path, ...],     # 真实存在的路径列表
        "project_key": str | None,        # 解析出的 git remote 归一化 key
        "mode": str,                      # 实际生效的 mode
        "warnings": [str, ...],
    }
"""

import os
import sys
from pathlib import Path
from typing import Any

# project_root 路径加到 sys.path（让 core.* 可 import）
_LIB_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _LIB_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.paths import PERSONAL_DIR, PROJECTS_DIR  # noqa: E402
from core.project_key import resolve_project_key, _to_dir_name  # noqa: E402


VALID_MODES = ("auto", "current_project", "personal", "general", "all")


def resolve_scope(workspace_path: str | None, mode: str = "auto") -> dict[str, Any]:
    """根据 workspace + mode 解析 include_paths"""
    warnings: list[str] = []

    if mode not in VALID_MODES:
        warnings.append(f"非法 mode={mode!r}，回退到 auto")
        mode = "auto"

    # all 模式：personal + 所有 projects 子目录
    if mode == "all":
        paths: list[Path] = []
        if PERSONAL_DIR.exists():
            paths.append(PERSONAL_DIR)
        if PROJECTS_DIR.exists():
            paths.extend(d for d in PROJECTS_DIR.iterdir() if d.is_dir())
        return {
            "include_paths": paths,
            "project_key": None,
            "mode": mode,
            "warnings": warnings,
        }

    # 决定 effective workspace
    ws = workspace_path or os.environ.get("AI_MEMORY_WORKSPACE")
    project_key = None
    if ws:
        info = resolve_project_key(ws)
        if info:
            project_key = info["key"]

    # workspace 无法解析为 git 仓库：scope=project / current_project 兜底 personal
    candidates: list[Path] = []

    if mode in ("auto", "personal", "general"):
        if PERSONAL_DIR.exists():
            candidates.append(PERSONAL_DIR)

    if mode in ("auto", "current_project"):
        if project_key:
            project_dir = PROJECTS_DIR / _to_dir_name(project_key)
            candidates.append(project_dir)  # 即便不存在也加，下面统一过滤
        else:
            if mode == "current_project":
                warnings.append("workspace 不在 git 仓库中且无 origin remote，无法定位 project")

    if mode == "auto" and project_key:
        # 跨项目候选（P4.2 跨项目经验迁移用，把所有其他 project 也放进 candidates）
        # 注意：scope=auto 默认包含其他 project；具体的相关性排序在 searcher 里做
        if PROJECTS_DIR.exists():
            current_dir_name = _to_dir_name(project_key)
            for d in PROJECTS_DIR.iterdir():
                if d.is_dir() and d.name != current_dir_name:
                    candidates.append(d)

    # 静默过滤不存在的
    include_paths = [p for p in candidates if p.exists()]

    return {
        "include_paths": include_paths,
        "project_key": project_key,
        "mode": mode,
        "warnings": warnings,
    }


def _debug() -> None:
    import json
    workspace = sys.argv[1] if len(sys.argv) > 1 else str(Path.cwd())
    mode = sys.argv[2] if len(sys.argv) > 2 else "auto"
    result = resolve_scope(workspace, mode)
    result["include_paths"] = [str(p) for p in result["include_paths"]]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _debug()
