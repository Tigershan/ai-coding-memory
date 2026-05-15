"""workspace_detector - 识别当前 IDE workspace

MCP server 是一个常驻进程，多个 IDE 会复用它，所以"当前 workspace"必须在
**每次工具调用时**实时探测，不能在 server 启动时缓存。

三层兜底（优先级从高到低）：
    1. 环境变量 AI_MEMORY_WORKSPACE （由 install.sh / IDE MCP 配置注入）
    2. 进程 CWD（IDE 启动 MCP 时通常会切到当前 workspace）
    3. CWD 向上找 .git 目录（git rev-parse --show-toplevel 等价）

返回：
    {
        "workspace_path": str | None,   # 绝对路径，未找到则 None
        "project_name": str | None,     # 路径 basename
        "source": str,                  # 三层之一："env" | "cwd" | "git" | "none"
    }

不抛异常 —— workspace 探测失败时下游 scope_resolver 会回退到 general/all 范围。
"""

import os
import subprocess
from pathlib import Path


def _detect_via_env() -> str | None:
    val = os.environ.get("AI_MEMORY_WORKSPACE")
    if val and Path(val).expanduser().exists():
        return str(Path(val).expanduser().resolve())
    return None


def _detect_via_cwd() -> str | None:
    """CWD 含 .git 或 package.json 等典型项目标志才认为是 workspace 根"""
    cwd = Path.cwd().resolve()
    markers = (".git", "package.json", "pyproject.toml", "pom.xml", "Cargo.toml")
    for marker in markers:
        if (cwd / marker).exists():
            return str(cwd)
    return None


def _detect_via_git() -> str | None:
    """从 CWD 向上找 git root"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            top = result.stdout.strip()
            if top and Path(top).exists():
                return str(Path(top).resolve())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def detect_workspace() -> dict:
    """三层兜底探测当前 workspace；返回标准 dict"""
    for source, fn in (
        ("env", _detect_via_env),
        ("cwd", _detect_via_cwd),
        ("git", _detect_via_git),
    ):
        path = fn()
        if path:
            return {
                "workspace_path": path,
                "project_name": Path(path).name,
                "source": source,
            }
    return {"workspace_path": None, "project_name": None, "source": "none"}
