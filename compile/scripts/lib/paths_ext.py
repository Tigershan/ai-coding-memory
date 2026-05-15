"""compile 专属路径常量

约定：
    ~/.ai-memory/wiki/                              # 知识库根，按 scope 分层
        projects/<safe_project>/                    # scope=project 子库
        domains/<safe_domain>/                      # scope=domain  子库
        general/<safe_general_category>/            # scope=general 子库
        .compile-manifest/<YYYY-MM-DD>.json         # compile 任务清单
        .compile-errors.log                         # 累计失败日志（追加）

    compile/llm-wiki-skill/                         # fork 的 git submodule
        scripts/init-wiki.sh                        # 唯一会被 compile 自动调用的脚本
        SKILL.md                                    # Agent 消化 topic 时遵循
"""

import importlib.util
from pathlib import Path

# 跨模块复用 collect/scripts/lib/paths.py 中的常量；
# 用 importlib 精确按文件加载，避免和本模块同级的 lib 包名冲突
_HERE = Path(__file__).resolve().parent
_COLLECT_PATHS_FILE = (
    _HERE.parent.parent.parent / "collect" / "scripts" / "lib" / "paths.py"
)
if not _COLLECT_PATHS_FILE.exists():
    raise ImportError(f"找不到 collect/scripts/lib/paths.py: {_COLLECT_PATHS_FILE}")

_spec = importlib.util.spec_from_file_location(
    "_collect_paths_for_compile", str(_COLLECT_PATHS_FILE)
)
_collect_paths = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_collect_paths)

DATA_ROOT: Path = _collect_paths.DATA_ROOT
RAW_TOPICS_DIR: Path = _collect_paths.RAW_TOPICS_DIR
WIKI_ROOT: Path = _collect_paths.WIKI_ROOT

# ==== compile 专属 ====
COMPILE_MANIFEST_DIR: Path = WIKI_ROOT / ".compile-manifest"
COMPILE_ERRORS_LOG: Path = WIKI_ROOT / ".compile-errors.log"

# ==== llm-wiki-skill submodule 入口 ====
PROJECT_ROOT: Path = _HERE.parent.parent.parent  # ai-coding-memory/
LLM_WIKI_DIR: Path = PROJECT_ROOT / "compile" / "llm-wiki-skill"
LLM_WIKI_INIT_SCRIPT: Path = LLM_WIKI_DIR / "scripts" / "init-wiki.sh"
LLM_WIKI_SKILL_FILE: Path = LLM_WIKI_DIR / "SKILL.md"


def daily_manifest_path(date: str) -> Path:
    """返回某日 compile manifest 文件路径（不强制创建父目录）"""
    return COMPILE_MANIFEST_DIR / f"{date}.json"


def ensure_compile_dirs() -> None:
    """幂等创建运行时目录骨架"""
    WIKI_ROOT.mkdir(parents=True, exist_ok=True)
    COMPILE_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)


def llm_wiki_available() -> bool:
    """submodule 是否已正确拉取（init-wiki.sh 存在即视为 OK）"""
    return LLM_WIKI_INIT_SCRIPT.exists()
