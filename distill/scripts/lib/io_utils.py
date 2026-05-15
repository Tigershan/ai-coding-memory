"""io_utils - distill 阶段通用 IO 工具

集中管理：
    - JSON / Markdown 读写
    - manifest.json 读写 + 状态更新
    - 错误日志追加

设计原则：
    - 所有写入都是原子的（先写临时文件，再 rename）
    - JSON 解析失败抛清晰错误（带文件路径），不沉默
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


# ==== 通用 ====

def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"JSON 文件不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败 ({path}): {e}") from e


def write_json_atomic(path: Path, data: Any) -> None:
    """原子写 JSON（先写临时文件再 rename）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ==== manifest 操作 ====

def load_manifest(path: Path) -> dict:
    return load_json(path)


def save_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(path, manifest)


def update_task_status(
    manifest: dict,
    task_id: str,
    status: str,
    error: str | None = None,
) -> bool:
    """更新单个 task 状态，返回是否找到对应任务"""
    for task in manifest.get("tasks", []):
        if task["id"] == task_id:
            task["status"] = status
            if error is not None:
                task["error"] = error
            return True
    return False


# ==== 错误日志 ====

def append_error_log(log_path: Path, scope: str, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {scope}: {message}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


# ==== Markdown helpers ====

def slugify(text: str, max_len: int = 40) -> str:
    """把任意标题转换为安全的文件名 slug"""
    import re
    s = text.strip().lower()
    # 去掉 markdown 符号、emoji、标点
    s = re.sub(r"[`*_~#>\[\]()!?。，、：；！？（）【】「」“”‘’\"']", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w\u4e00-\u9fff-]", "", s)  # 保留中英数字+中文+连字符
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "untitled"
    return s[:max_len]
