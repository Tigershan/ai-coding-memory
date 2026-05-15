"""io_utils - compile 阶段共享 IO 工具

设计原则：
    - 与 distill/scripts/lib/io_utils.py 接口一致（团队经验复用）
    - 所有写入是原子的（先写临时文件，再 rename）
    - manifest 状态机更新单点封装
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


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


def save_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(path, manifest)


def append_error_log(log_path: Path, scope: str, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {scope}: {message}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
