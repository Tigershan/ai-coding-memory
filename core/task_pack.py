"""core.task_pack - host_agent 模式下的任务包流转

按 redesign §6.2.3：

任务包生命周期（文件命名约定）：
    .pending/<task_id>.task               待消化
    .pending/<task_id>.task.in_progress   宿主 agent 取走时改名
    .pending/<task_id>.task.failed        失败移动
    （成功 → 删除文件）

任务包格式（JSON，机器间通信用 JSON 比 YAML 更健壮，多行字符串无需 escape）：
    {
      "task_id": "<uuid hex>",
      "session_id": "<从 session.sessionId 来>",
      "ide": "cursor | aone-copilot | qoder | claude-code",
      "workspace": "/abs/path",
      "project_key": "github.com/xxx/yyy | null",
      "created_at": "2026-05-16T22:01:00",
      "prompt": "<完整 LLM prompt，含 session 对话原文，含真实换行>"
    }

接口：
    write_task(prompt, session, project_key) -> task_id
    list_pending() -> list[dict]   每项含 {task_id, age_seconds, ide, workspace}
    take_next() -> dict | None     原子拿一个 → 改名 in_progress
    submit_result(task_id, result_yaml) -> {written, dropped, errors}
        written: 落盘的 memory 路径列表
        dropped: should_keep=false 直接丢弃的 topic 元信息（仅审计用，不入库）
        errors:  解析/写入失败的描述
    mark_failed(task_id, error) -> None
    cleanup_old(max_age_days=7) -> int   过期清理
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from .paths import PENDING_DIR, ensure_data_dirs


SUFFIX_PENDING = ".task"
SUFFIX_IN_PROGRESS = ".task.in_progress"
SUFFIX_FAILED = ".task.failed"


# ==================== 写 ====================

def write_task(prompt: str, session: dict, project_key: str | None,
               *, batch_date: str | None = None) -> str:
    """把一个待蒸馏任务写入 .pending/，返回 task_id

    batch_date: 任务对应的会话日期 YYYY-MM-DD（init/lazy_trigger 应当传入）。
        消化时按 batch_date 升序排队，让多天历史能均摊到多天里慢慢消化，
        避免 60 条历史在同一天涌入宿主 IDE 的实时 LLM 配额。
        没传时记空字符串，排序时视作"最旧"（与"未知"等价，先消化掉避免堆积过久）。
    """
    ensure_data_dirs()
    task_id = uuid.uuid4().hex[:12]
    payload = {
        "task_id": task_id,
        "session_id": session.get("sessionId", ""),
        "ide": session.get("ide", ""),
        "workspace": session.get("workspace", ""),
        "project_key": project_key or "null",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "batch_date": batch_date or "",
        "prompt": prompt,
    }
    target = PENDING_DIR / f"{task_id}{SUFFIX_PENDING}"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return task_id


# ==================== 看 ====================

def list_pending(include_in_progress: bool = True,
                 include_failed: bool = False) -> list[dict]:
    """列出所有任务包（按 created_at 升序）"""
    if not PENDING_DIR.exists():
        return []
    out: list[dict] = []
    suffixes = [SUFFIX_PENDING]
    if include_in_progress:
        suffixes.append(SUFFIX_IN_PROGRESS)
    if include_failed:
        suffixes.append(SUFFIX_FAILED)
    now = time.time()
    for f in PENDING_DIR.iterdir():
        if not f.is_file():
            continue
        if not any(f.name.endswith(s) for s in suffixes):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        st = "pending"
        if f.name.endswith(SUFFIX_IN_PROGRESS):
            st = "in_progress"
        elif f.name.endswith(SUFFIX_FAILED):
            st = "failed"
        out.append({
            "task_id": data.get("task_id", f.stem),
            "session_id": data.get("session_id", ""),
            "ide": data.get("ide", ""),
            "workspace": data.get("workspace", ""),
            "project_key": data.get("project_key", ""),
            "created_at": data.get("created_at", ""),
            "batch_date": data.get("batch_date", ""),
            "age_seconds": int(now - f.stat().st_mtime),
            "status": st,
            "_path": str(f),
        })
    # 涓流策略：按 batch_date 升序（最老的会话先消化）+ created_at 升序（同日 FIFO）
    # 没有 batch_date 的旧任务排在最前（视作"未知/最老"，先清理掉避免堆积过久）
    out.sort(key=lambda x: (x["batch_date"] or "0000-00-00", x["created_at"]))
    return out


def count_pending() -> int:
    """快速计数（不解析任务内容）"""
    if not PENDING_DIR.exists():
        return 0
    n = 0
    for f in PENDING_DIR.iterdir():
        if f.is_file() and f.name.endswith(SUFFIX_PENDING):
            n += 1
    return n


# ==================== 取 ====================

def take_next() -> dict | None:
    """原子取一个 pending 任务 → 改名 in_progress，返回 task 数据。
    多 agent 并发安全：os.rename 是原子的；改名失败说明被别的 agent 抢走

    排队策略（涓流）：(batch_date 升序, mtime 升序)
    最老会话日期的任务先出队，让历史 init 的 N 天数据能按天均摊。
    """
    if not PENDING_DIR.exists():
        return None

    def _read_batch_date(p: Path) -> str:
        """读取任务包的 batch_date 字段；解析失败 / 缺字段一律视作 '0000-00-00'（最旧）"""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("batch_date") or "0000-00-00"
        except (json.JSONDecodeError, OSError):
            return "0000-00-00"

    candidates = sorted(
        (f for f in PENDING_DIR.iterdir()
         if f.is_file() and f.name.endswith(SUFFIX_PENDING)),
        key=lambda p: (_read_batch_date(p), p.stat().st_mtime),
    )
    for src in candidates:
        dst = src.with_suffix(src.suffix + ".in_progress")
        try:
            # rename 是原子的；如果 dst 已存在会被覆盖，但 candidates 列表里不会有 in_progress 后缀的
            os.rename(src, dst)
        except FileNotFoundError:
            continue  # 已被别的 agent 拿走
        try:
            data = json.loads(dst.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 文件读不出来，移到 failed 跳过
            try:
                os.rename(dst, dst.parent / dst.name.replace(SUFFIX_IN_PROGRESS, SUFFIX_FAILED))
            except OSError:
                pass
            continue
        return {
            **data,
            "_path": str(dst),
        }
    return None


# ==================== 提交结果 ====================

def submit_result(task_id: str, result_yaml: str) -> dict:
    """提交某个 in_progress 任务的结果。
    返回 {written: [paths], dropped: [{title, reason}], errors: [str]}

    should_keep=false 的 topic **直接丢弃**（不再写 .cold/），
    只在 dropped 字段里留 title+keep_reason 便于审计/统计。"""
    in_progress = PENDING_DIR / f"{task_id}{SUFFIX_IN_PROGRESS}"
    pending = PENDING_DIR / f"{task_id}{SUFFIX_PENDING}"
    src = in_progress if in_progress.exists() else (pending if pending.exists() else None)
    if src is None:
        return {"written": [], "dropped": [], "errors": [f"task_id 未找到：{task_id}"]}

    # 读 task 元信息
    try:
        meta = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"written": [], "dropped": [], "errors": [f"读 task 失败：{e}"]}

    # 解析 LLM 输出
    from .frontmatter import _parse_yaml
    try:
        # 复用 distill 的 parse_llm_yaml 逻辑（剥 ``` 围栏）
        s = result_yaml.strip()
        if s.startswith("```"):
            lines = s.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines)
        parsed = _parse_yaml(s)
    except Exception as e:
        # 不删 task；让 agent 看到错误重试
        return {"written": [], "dropped": [], "errors": [f"YAML 解析失败：{e}"]}

    # 严格检查：必须含 topics key（即使是空 list）。
    # 缺失说明 LLM 输出不合规或解析器没找到该字段（如 ``` 围栏外有杂文本），
    # 此时**不删 task**——保留给 agent 重试，避免 silent data loss。
    if "topics" not in parsed:
        snippet = (s[:300] + "...") if len(s) > 300 else s
        # 标记任务为 failed 而不是简单跳过，便于 ai-memory pending 看到
        if src.name.endswith(SUFFIX_IN_PROGRESS):
            try:
                failed_path = src.parent / src.name.replace(SUFFIX_IN_PROGRESS, SUFFIX_FAILED)
                os.rename(src, failed_path)
            except OSError:
                pass
        return {
            "written": [], "dropped": [],
            "errors": [
                "topics 字段缺失：LLM 输出可能未按要求格式。任务包标记为 failed。",
                f"输出前 300 字预览：{snippet}",
            ],
        }

    topics = parsed.get("topics")
    if not isinstance(topics, list):
        # 显式非 list 也算 LLM 格式错误 → 不删 task
        if src.name.endswith(SUFFIX_IN_PROGRESS):
            try:
                os.rename(src, src.parent / src.name.replace(SUFFIX_IN_PROGRESS, SUFFIX_FAILED))
            except OSError:
                pass
        return {
            "written": [], "dropped": [],
            "errors": [f"topics 不是 list（实际类型: {type(topics).__name__}）"],
        }

    # 落盘
    from . import memory_store as ms
    written: list[str] = []
    dropped: list[dict] = []   # should_keep=false 的 topic，不入库，仅记录元信息
    errors: list[str] = []

    project_key = meta.get("project_key")
    if project_key == "null":
        project_key = None

    fake_session = {
        "ide": meta.get("ide"),
        "sessionId": meta.get("session_id"),
        "workspace": meta.get("workspace"),
    }

    for t in topics:
        try:
            if t.get("should_keep") is False:
                # LLM 自判低价值 → 直接丢弃。记一行 keep_reason 用于审计
                dropped.append({
                    "title": (t.get("title") or "")[:80],
                    "reason": (t.get("keep_reason") or "")[:120],
                })
                continue
            mem = _topic_to_memory(t, fake_session, project_key)
            p = ms.save(mem)
            written.append(str(p))
        except PermissionError as pe:
            errors.append(f"protected: {pe}")
        except Exception as e:
            errors.append(f"save failed: {e}")

    # 删除任务包仅当：parsed 含 topics key 且至少有一条成功落盘
    # 或：topics: [] 或全部 should_keep=false（LLM 明确判定无价值，合法选择，不算失败）
    something_done = bool(written) or bool(dropped)
    intentional_empty = len(topics) == 0
    if something_done or intentional_empty:
        try:
            src.unlink()
        except OSError:
            pass
    else:
        # 有 topics 但全部 save 失败 → 标 failed 留诊断
        if src.name.endswith(SUFFIX_IN_PROGRESS):
            try:
                os.rename(src, src.parent / src.name.replace(SUFFIX_IN_PROGRESS, SUFFIX_FAILED))
            except OSError:
                pass

    return {"written": written, "dropped": dropped, "errors": errors}


def _topic_to_memory(topic: dict, session: dict, project_key: str | None):
    """与 distill.py 中的 _topic_to_memory 等价（避免循环 import 而独立一份）"""
    from . import memory_store as ms
    from .memory_store import Memory

    title = (topic.get("title") or "untitled").strip()
    body = topic.get("body") or f"# {title}\n"
    scope = topic.get("scope") or "personal"
    if scope not in ("personal", "project"):
        scope = "personal"
    if scope == "project" and not project_key:
        scope = "personal"
    pkey = project_key if scope == "project" else None
    value = topic.get("value") or "medium"
    if value not in ("high", "medium", "low"):
        value = "medium"
    tags = topic.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags][:6]
    origin = {
        "ide": session.get("ide"),
        "session_id": session.get("sessionId"),
        "workspace": session.get("workspace"),
        "distilled_at": datetime.now().date().isoformat(),
    }
    msg_range = topic.get("source_msg_range")
    if isinstance(msg_range, list) and len(msg_range) == 2:
        origin["msg_range"] = msg_range
    return Memory(
        id=ms.make_id(title),
        scope=scope,
        title=title,
        body=body,
        project_key=pkey,
        source="auto",
        value=value,
        tags=tags,
        origin=origin,
        extra={
            "summary": topic.get("summary") or "",
            "keep_reason": topic.get("keep_reason") or "",
        },
    )


# ==================== 失败 / 清理 ====================

def mark_failed(task_id: str, error: str) -> bool:
    """标记 task 失败（in_progress → failed），保留诊断信息"""
    in_progress = PENDING_DIR / f"{task_id}{SUFFIX_IN_PROGRESS}"
    if not in_progress.exists():
        return False
    failed = PENDING_DIR / f"{task_id}{SUFFIX_FAILED}"
    try:
        os.rename(in_progress, failed)
    except OSError:
        return False
    # 在文件末尾追加 error
    try:
        with open(failed, "a", encoding="utf-8") as f:
            f.write(f"\n# error: {error}\n# failed_at: {datetime.now().isoformat(timespec='seconds')}\n")
    except OSError:
        pass
    return True


def cleanup_old(max_age_days: int = 7) -> int:
    """清理超过 max_age_days 的所有 task 文件（不区分 status）"""
    if not PENDING_DIR.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    n = 0
    for f in PENDING_DIR.iterdir():
        if not f.is_file():
            continue
        if not any(f.name.endswith(s) for s in (SUFFIX_PENDING, SUFFIX_IN_PROGRESS, SUFFIX_FAILED)):
            continue
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


# ==================== 重置（管理用） ====================

def reset_in_progress() -> int:
    """把所有 in_progress 任务恢复为 pending（agent 卡死/重启场景）"""
    if not PENDING_DIR.exists():
        return 0
    n = 0
    for f in PENDING_DIR.iterdir():
        if not f.is_file() or not f.name.endswith(SUFFIX_IN_PROGRESS):
            continue
        target = f.with_suffix("")  # 去掉 .in_progress，留 .task
        try:
            os.rename(f, target)
            n += 1
        except OSError:
            pass
    return n
