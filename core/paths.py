"""core.paths - 项目级路径常量（redesign v1.2 §5.4 数据布局）

按 redesign.md §4 架构总览 + §5.4 数据目录布局：

    ~/.ai-memory/
    ├── personal/                     跨项目通用 memory（人 + AI）
    ├── projects/<key>/               项目专属 memory
    ├── .cold/                        冷存储（LLM 判低价值但保留）
    ├── .pending/                     host_agent 模式下挂起的 distill 任务包
    ├── archive/                      用户/系统归档
    ├── raw/sessions/                 collect 阶段产出
    ├── wiki/                         旧 llm-wiki 布局（过渡期保留）
    ├── config/
    │   └── config.yml                用户配置（LLM mode 等，P3）
    └── logs/

环境变量：
    AI_MEMORY_DATA_ROOT    覆盖 DATA_ROOT（便于多机/测试）
"""

import os
from pathlib import Path


def _resolve_data_root() -> Path:
    env = os.environ.get("AI_MEMORY_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path("~/.ai-memory").expanduser()


# ==================== 数据根 ====================

DATA_ROOT: Path = _resolve_data_root()

# ==================== 新数据模型（P1 起） ====================

PERSONAL_DIR: Path = DATA_ROOT / "personal"
PROJECTS_DIR: Path = DATA_ROOT / "projects"
COLD_DIR: Path = DATA_ROOT / ".cold"
PENDING_DIR: Path = DATA_ROOT / ".pending"
ARCHIVE_DIR: Path = DATA_ROOT / "archive"

# ==================== 旧 llm-wiki 布局（过渡期，P4 召回切换后可移除） ====================

WIKI_ROOT: Path = DATA_ROOT / "wiki"

# ==================== collect 阶段 ====================

RAW_SESSIONS_DIR: Path = DATA_ROOT / "raw" / "sessions"

# ==================== 配置 / 状态文件 ====================

CONFIG_DIR: Path = DATA_ROOT / "config"
USER_CONFIG_PATH: Path = CONFIG_DIR / "config.yml"
LAST_DISTILL_PATH: Path = DATA_ROOT / ".last_distill"
DISTILL_LOCK_PATH: Path = DATA_ROOT / ".distill.lock"
INIT_PROGRESS_PATH: Path = DATA_ROOT / ".init-progress.json"

# ==================== 日志 ====================

LOG_DIR: Path = DATA_ROOT / "logs"


def ensure_data_dirs() -> None:
    """幂等创建所有运行时数据目录"""
    for d in (
        PERSONAL_DIR, PROJECTS_DIR, COLD_DIR, PENDING_DIR, ARCHIVE_DIR,
        WIKI_ROOT, RAW_SESSIONS_DIR, CONFIG_DIR, LOG_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
