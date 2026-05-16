"""core.project_key - 根据 workspace 路径解析稳定项目 key

按 redesign.md ADR-4：
    project_key = normalize(git remote get-url origin)

归一化结果形如 `github.com/owner/repo`，去掉协议前缀和 .git 后缀。
跨机器、跨 clone、跨 rename 都稳定。

无 git remote 的目录（临时 sandbox / 不在 git 中）→ 返回 None
调用方处理 None 时应归到 scope=personal。

文件名转换（redesign §5.4）：
    git remote 结果有 `/`，不能直接当目录名 → 用 `_` 替换
    e.g. "github.com/xxx/winterfell" → "github.com_xxx_winterfell"

输入：
    workspace_path : 任何 workspace 内的绝对路径

输出：
    {"key": "github.com/xxx/repo", "dir_name": "github.com_xxx_repo"} | None

失败模式：
    - 路径不是 git 仓库 → 返回 None
    - git 命令不存在 → 返回 None（系统未装 git）
    - remote 未配置 origin → 返回 None
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_REMOTE_PATTERNS = (
    # SSH: git@github.com:owner/repo.git
    re.compile(r"^git@(?P<host>[^:]+):(?P<path>.+?)(?:\.git)?$"),
    # HTTPS: https://github.com/owner/repo.git
    re.compile(r"^https?://(?:[^@]+@)?(?P<host>[^/]+)/(?P<path>.+?)(?:\.git)?$"),
    # SSH URL: ssh://git@github.com/owner/repo.git
    re.compile(r"^ssh://(?:[^@]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+?)(?:\.git)?$"),
    # git protocol: git://github.com/owner/repo.git
    re.compile(r"^git://(?P<host>[^/]+)/(?P<path>.+?)(?:\.git)?$"),
)


def resolve_project_key(workspace_path: str | Path) -> dict | None:
    """根据 workspace 解析稳定 project key。无 remote 返回 None。"""
    workspace = Path(workspace_path).expanduser()
    if not workspace.exists():
        return None

    git_root = _find_git_root(workspace)
    if git_root is None:
        return None

    remote_url = _read_origin_url(git_root)
    if not remote_url:
        return None

    key = _normalize_remote_url(remote_url)
    if not key:
        return None

    return {
        "key": key,
        "dir_name": _to_dir_name(key),
        "git_root": str(git_root),
        "remote_url": remote_url,
    }


def _find_git_root(start: Path) -> Path | None:
    """从 start 向上找 .git 目录，找到则返回该目录的父（即 repo root）"""
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for path in (cur, *cur.parents):
        if (path / ".git").exists():
            return path
    return None


def _read_origin_url(git_root: Path) -> str | None:
    """读 git remote get-url origin。失败返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(git_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    url = (result.stdout or "").strip()
    return url or None


def _normalize_remote_url(url: str) -> str | None:
    """把各种 git URL 归一化为 host/path 形式"""
    url = url.strip()
    for pattern in _REMOTE_PATTERNS:
        m = pattern.match(url)
        if not m:
            continue
        host = m.group("host").lower()
        path = m.group("path").strip("/")
        # 兜底再去一次 .git
        if path.endswith(".git"):
            path = path[:-4]
        if not host or not path:
            return None
        return f"{host}/{path}"
    return None


def _to_dir_name(key: str) -> str:
    """key → 文件系统安全的目录名"""
    # / → _，再去掉其它路径不安全字符
    safe = key.replace("/", "_")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", safe)
    return safe


def _debug() -> None:
    import json
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "."
    result = resolve_project_key(workspace)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _debug()
