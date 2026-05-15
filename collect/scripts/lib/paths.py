"""路径常量集中

集中所有跨模块共享的路径常量，避免散落到各处。
所有路径都是 absolute Path，已展开 ~。
"""

import os
from pathlib import Path

# ==== 数据根目录 ====
DATA_ROOT: Path = Path(os.path.expanduser("~/.ai-memory"))

# ==== collect 阶段 ====
RAW_SESSIONS_DIR: Path = DATA_ROOT / "raw" / "sessions"

# ==== distill 阶段 ====
RAW_TOPICS_DIR: Path = DATA_ROOT / "raw" / "topics"

# ==== compile 阶段 ====
WIKI_ROOT: Path = DATA_ROOT / "wiki"

# ==== 配置 ====
CONFIG_DIR: Path = DATA_ROOT / "config"
DOMAIN_MAPPING_PATH: Path = CONFIG_DIR / "domain-mapping.yml"

# ==== 日志 ====
LOG_DIR: Path = DATA_ROOT / "logs"

# ==== IDE 数据源路径 ====
CURSOR_DB_PATH: Path = Path(os.path.expanduser(
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
))
QODER_DB_PATH: Path = Path(os.path.expanduser(
    "~/Library/Application Support/Qoder/User/globalStorage/state.vscdb"
))
AONE_COPILOT_KV_DIR: Path = Path(os.path.expanduser(
    "~/.aone_copilot/kv_storage"
))
CLAUDE_CODE_PROJECTS_DIR: Path = Path(os.path.expanduser(
    "~/.claude/projects"
))

IDE_DB_PATHS: dict[str, Path] = {
    "cursor": CURSOR_DB_PATH,
    "qoder": QODER_DB_PATH,
}

def ensure_data_dirs() -> None:
    """确保所有运行时数据目录存在（幂等）"""
    for d in (RAW_SESSIONS_DIR, RAW_TOPICS_DIR, WIKI_ROOT, CONFIG_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
