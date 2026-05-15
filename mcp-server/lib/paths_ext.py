"""mcp-server 路径常量（跨包复用 collect/lib/paths）

所有 mcp-server 写入/读取的目录：
    ~/.ai-memory/wiki/                      ← 召回数据源（compile 阶段写入）
    ~/.ai-memory/wiki/<scope>/<name>/_index.md
    ~/.ai-memory/wiki/<scope>/<name>/wiki/{entities,topics,synthesis}/*.md
    ~/.ai-memory/config/domain-mapping.yml  ← 用户编辑的领域映射

环境变量覆盖（便于多机器/测试）：
    AI_MEMORY_DATA_ROOT     覆盖 DATA_ROOT
    AI_MEMORY_WORKSPACE     强制指定当前 workspace（最高优先级）
"""

import importlib.util
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COLLECT_PATHS_FILE = (
    _HERE.parent.parent / "collect" / "scripts" / "lib" / "paths.py"
)
if not _COLLECT_PATHS_FILE.exists():
    raise ImportError(f"找不到 collect/scripts/lib/paths.py: {_COLLECT_PATHS_FILE}")

_spec = importlib.util.spec_from_file_location(
    "_collect_paths_for_mcp", str(_COLLECT_PATHS_FILE)
)
_collect_paths = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_collect_paths)


def _override_root(default: Path) -> Path:
    env = os.environ.get("AI_MEMORY_DATA_ROOT")
    return Path(env).expanduser() if env else default


DATA_ROOT: Path = _override_root(_collect_paths.DATA_ROOT)
WIKI_ROOT: Path = DATA_ROOT / "wiki"
CONFIG_DIR: Path = DATA_ROOT / "config"
DOMAIN_MAPPING_PATH: Path = CONFIG_DIR / "domain-mapping.yml"

# scope → wiki 子目录名（与 compile/lib/scope_router 保持一致）
SCOPE_DIR_NAME = {
    "project": "projects",
    "domain": "domains",
    "general": "general",
}
