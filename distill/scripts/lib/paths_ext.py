"""distill 专属路径常量

设计说明：
    distill 阶段在 collect.lib.paths 的基础上新增"任务包"目录。
    任务包是 Agent 编排模式下的中间产物，每天一个子目录。

布局：
    ~/.ai-memory/raw/distill-tasks/YYYY-MM-DD/
        manifest.json              # 任务清单（含状态机）
        step1-segment/
            session-{ide}-{idx}.prompt.md
            session-{ide}-{idx}.result.json
        step2-coref/
            topic-{sid}-{tid}.prompt.md
            topic-{sid}-{tid}.result.md
        step3-code/
            topic-{sid}-{tid}.prompt.md
            topic-{sid}-{tid}.result.json
        step4-layer/
            topic-{sid}-{tid}.prompt.md
            topic-{sid}-{tid}.result.json
        stepF-fast/
            topic-{ide}-{idx}-t{tid}.prompt.md
            topic-{ide}-{idx}-t{tid}.result.json
"""

import importlib.util
from pathlib import Path

# 跨模块复用 collect/scripts/lib/paths.py 中的常量；
# 用 importlib.spec_from_file_location 直接按绝对路径加载，避免和
# distill/scripts/lib（同名 lib 包）的 sys.path 冲突。
_HERE = Path(__file__).resolve().parent
_COLLECT_PATHS_FILE = (
    _HERE.parent.parent.parent / "collect" / "scripts" / "lib" / "paths.py"
)
if not _COLLECT_PATHS_FILE.exists():
    raise ImportError(f"无法找到 collect/scripts/lib/paths.py: {_COLLECT_PATHS_FILE}")

_spec = importlib.util.spec_from_file_location(
    "_collect_paths", str(_COLLECT_PATHS_FILE)
)
_collect_paths = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_collect_paths)

DATA_ROOT: Path = _collect_paths.DATA_ROOT
RAW_SESSIONS_DIR: Path = _collect_paths.RAW_SESSIONS_DIR
RAW_TOPICS_DIR: Path = _collect_paths.RAW_TOPICS_DIR

# ==== distill 任务包根目录 ====
DISTILL_TASKS_DIR: Path = DATA_ROOT / "raw" / "distill-tasks"

# ==== 各 step 子目录名（相对 daily task 目录） ====
STEP1_SUBDIR = "step1-segment"
STEP2_SUBDIR = "step2-coref"
STEP3_SUBDIR = "step3-code"
STEP4_SUBDIR = "step4-layer"
STEPF_SUBDIR = "stepF-fast"

# ==== prompt 模板根目录 ====
PROMPTS_DIR: Path = _HERE.parent.parent / "prompts"

# ==== prompt 文件名映射 ====
PROMPT_FILES: dict[str, str] = {
    "topic_segmentation": "01_topic_segmentation.md",
    "coreference":        "02_coreference.md",
    "code_filter":        "03_code_filter.md",
    "layer_tagging":      "04_layer_tagging.md",
    "fast_track":         "05_fast_track.md",
}

def daily_task_dir(date: str) -> Path:
    """返回某日的任务包根目录（不强制创建）"""
    return DISTILL_TASKS_DIR / date

def manifest_path(date: str) -> Path:
    return daily_task_dir(date) / "manifest.json"

def ensure_distill_dirs(date: str) -> Path:
    """幂等创建当日任务包目录骨架，返回 daily root"""
    root = daily_task_dir(date)
    for sub in (STEP1_SUBDIR, STEP2_SUBDIR, STEP3_SUBDIR, STEP4_SUBDIR, STEPF_SUBDIR):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
