"""core.memory_store - memory CRUD + source 保护 + archive/cold

按 redesign.md §5 数据模型 + §6.1 source 字段 + ADR-6 人改优先：

memory 文件结构：
    ~/.ai-memory/personal/<id>.md             scope=personal
    ~/.ai-memory/projects/<dir_name>/<id>.md   scope=project
    ~/.ai-memory/archive/<id>.md              软删除（用户手动 archive 或未来 reflect 归档）

注意：LLM 判 should_keep=false 的 topic 由 distill / submit_distill_result
**直接丢弃**（不再写 .cold/）。被丢弃的 topic 会在 logs/distill-*.log 留下一行
"DROPPED: <session_id> reason=should_keep_false"，便于审计但不占索引。

frontmatter source 字段语义：
    auto        : distill 生成；可被新 distill 覆盖（基于 mtime）
    bootstrap   : init 历史回溯生成；同 auto
    edited      : auto/bootstrap 文件被人编辑过（自动检测，永不再被覆盖）
    manual      : 人手新增（CLI add 或直接写文件）；永不被覆盖

mtime 检测自动升级（ADR-6）：
    每条 memory 在 frontmatter 里存 `_mtime_at_write`（最后一次"由 pipeline 写入"的时间戳）。
    读入时如果当前文件 mtime > _mtime_at_write + 容差（5s），说明被人改过 → 升级 source=edited。
    手编辑后保存时不会更新 _mtime_at_write，所以下次读入也仍能识别。
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import frontmatter as fm
from .paths import (
    ARCHIVE_DIR,
    PERSONAL_DIR,
    PROJECTS_DIR,
    ensure_data_dirs,
)


VALID_SCOPES = ("personal", "project")
VALID_SOURCES = ("auto", "bootstrap", "edited", "manual")
VALID_VALUES = ("high", "medium", "low")
PROTECTED_SOURCES = ("manual", "edited")  # 这些 source 永不被自动 pipeline 覆盖

MTIME_TOLERANCE_S = 5  # 写入到 stat 的延迟容差


# ==================== 数据类 ====================

@dataclass
class Memory:
    id: str
    scope: str                              # "personal" | "project"
    title: str
    body: str                               # frontmatter 之后的全部正文
    project_key: str | None = None          # scope=project 时填，归一化的 git remote URL
    source: str = "auto"                    # auto | bootstrap | edited | manual
    value: str = "medium"                   # high | medium | low
    created: str = ""                       # YYYY-MM-DD
    updated: str = ""                       # YYYY-MM-DD
    tags: list[str] = field(default_factory=list)
    origin: dict | None = None              # {ide, session_id, workspace, msg_range}
    potential_conflicts: list[str] = field(default_factory=list)            # ADR-12
    potentially_superseded_by: list[str] = field(default_factory=list)      # ADR-12
    archived: bool = False
    extra: dict = field(default_factory=dict)  # 兜底未识别字段

    # —— 运行时元 ——
    file_path: Path | None = field(default=None, repr=False)
    _mtime_at_write: float = 0.0                                           # source 升级用


# ==================== 路径解析 ====================

def memory_path(memory: Memory) -> Path:
    """根据 scope/project_key/id 计算落盘路径"""
    if memory.scope == "personal":
        return PERSONAL_DIR / f"{memory.id}.md"
    if memory.scope == "project":
        if not memory.project_key:
            raise ValueError(f"scope=project 但 project_key 为空: id={memory.id}")
        from .project_key import _to_dir_name
        return PROJECTS_DIR / _to_dir_name(memory.project_key) / f"{memory.id}.md"
    raise ValueError(f"未知 scope: {memory.scope!r}")


def archive_path(memory_id: str) -> Path:
    return ARCHIVE_DIR / f"{memory_id}.md"


# ==================== ID 生成 ====================

def make_id(title: str, prefix_date: str | None = None) -> str:
    """生成 ID：YYYY-MM-DD-slug-shorthex
    title 转 slug；末尾加 4 位 hex 防冲突。"""
    date = prefix_date or datetime.now().date().isoformat()
    slug = _slugify(title) or "memory"
    suffix = uuid.uuid4().hex[:4]
    # ID 长度控制
    return f"{date}-{slug[:50]}-{suffix}"


def _slugify(s: str) -> str:
    s = s.lower().strip()
    # 中文等非 ASCII 全部丢，只保留 ASCII 字母数字 / 空格 / -
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.ASCII)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# ==================== 读 ====================

def load(file_path: Path) -> Memory | None:
    """读单个 memory 文件。失败/不识别返回 None。"""
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm_dict, body = fm.parse(text)
    if not fm_dict.get("id") or not fm_dict.get("scope"):
        return None
    title = _extract_title_from_body(body) or fm_dict.get("title", "")

    mem = Memory(
        id=str(fm_dict["id"]),
        scope=str(fm_dict["scope"]),
        title=title,
        body=body,
        project_key=fm_dict.get("project_key"),
        source=fm_dict.get("source", "auto"),
        value=fm_dict.get("value", "medium"),
        created=str(fm_dict.get("created", "")),
        updated=str(fm_dict.get("updated", "")),
        tags=list(fm_dict.get("tags") or []),
        origin=fm_dict.get("origin"),
        potential_conflicts=list(fm_dict.get("potential_conflicts") or []),
        potentially_superseded_by=list(fm_dict.get("potentially_superseded_by") or []),
        archived=bool(fm_dict.get("archived", False)),
        file_path=file_path,
        _mtime_at_write=float(fm_dict.get("_mtime_at_write", 0.0) or 0.0),
        extra={k: v for k, v in fm_dict.items() if k not in {
            "id", "scope", "project_key", "source", "value", "created",
            "updated", "tags", "origin", "potential_conflicts",
            "potentially_superseded_by", "archived", "_mtime_at_write", "title",
        }},
    )

    # 自动升级 source: 如果文件被人改过（mtime > _mtime_at_write + tolerance），且 source=auto/bootstrap → edited
    if mem.source in ("auto", "bootstrap"):
        try:
            actual_mtime = file_path.stat().st_mtime
        except OSError:
            actual_mtime = 0.0
        if mem._mtime_at_write > 0 and actual_mtime > mem._mtime_at_write + MTIME_TOLERANCE_S:
            mem.source = "edited"
            # 不立即回写 —— 下次写入时自然会带上新 source

    return mem


def _extract_title_from_body(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


# ==================== 写 ====================

CONFLICT_TAG_OVERLAP_THRESHOLD = 2          # 触发冲突候选的 tag 重合度
CONFLICT_TITLE_SIM_THRESHOLD = 0.40         # 标题 token Jaccard 阈值（OR 关系）


def find_conflict_candidates(memory: Memory) -> list[str]:
    """轻量冲突检测（按 ADR-12，不调 LLM）：
    返回与 memory 候选冲突的现有 memory id 列表。

    规则：同 scope + 同 project，且满足任一信号：
        - tags 重合 ≥ CONFLICT_TAG_OVERLAP_THRESHOLD，或
        - 标题 token Jaccard > CONFLICT_TITLE_SIM_THRESHOLD
    （中文标题做 token 化后 jaccard 偏低，所以用 OR 不用 AND）
    """
    import re as _re
    candidates: list[str] = []
    a_tags = set(t.lower() for t in (memory.tags or []) if isinstance(t, str))
    a_title_tokens = set(_re.findall(r"\w+", (memory.title or "").lower()))
    for existing in _iter_all(include_archived=False):
        if existing.id == memory.id:
            continue
        if existing.scope != memory.scope:
            continue
        if memory.scope == "project" and existing.project_key != memory.project_key:
            continue
        # tag 重合
        b_tags = set(t.lower() for t in (existing.tags or []) if isinstance(t, str))
        tag_overlap = len(a_tags & b_tags) if (a_tags and b_tags) else 0
        # title 相似
        b_title_tokens = set(_re.findall(r"\w+", (existing.title or "").lower()))
        title_sim = 0.0
        if a_title_tokens and b_title_tokens:
            title_sim = len(a_title_tokens & b_title_tokens) / len(a_title_tokens | b_title_tokens)
        if tag_overlap >= CONFLICT_TAG_OVERLAP_THRESHOLD or title_sim >= CONFLICT_TITLE_SIM_THRESHOLD:
            candidates.append(existing.id)
    return candidates


def _mark_superseded(old_id: str, new_id: str) -> None:
    """把 old_id 标记 potentially_superseded_by += [new_id]"""
    old_path = _find_by_id(old_id)
    if old_path is None:
        return
    old_mem = load(old_path)
    if old_mem is None:
        return
    if old_mem.source in PROTECTED_SOURCES:
        # 即便是 manual/edited 也加标记（不算覆盖 body）
        pass
    existing = list(old_mem.potentially_superseded_by or [])
    if new_id in existing:
        return
    existing.append(new_id)
    old_mem.potentially_superseded_by = existing
    # 重写文件（allow_overwrite_protected 用于跳过 source 保护）
    try:
        save(old_mem, allow_overwrite_protected=True, _skip_conflict_check=True)
    except Exception:
        pass


def save(memory: Memory, *, allow_overwrite_protected: bool = False,
         _skip_conflict_check: bool = False) -> Path:
    """写一条 memory 到磁盘。
    - source=manual/edited 且文件已存在：默认拒绝（除非 allow_overwrite_protected=True，给 CLI edit 用）
    - 自动设置 _mtime_at_write 字段
    - 自动设置 updated 字段为今天
    """
    ensure_data_dirs()
    if memory.scope not in VALID_SCOPES:
        raise ValueError(f"非法 scope: {memory.scope!r}")
    if memory.source not in VALID_SOURCES:
        raise ValueError(f"非法 source: {memory.source!r}")
    if memory.value not in VALID_VALUES:
        raise ValueError(f"非法 value: {memory.value!r}")

    target = memory_path(memory)
    target.parent.mkdir(parents=True, exist_ok=True)

    # source 保护
    if target.exists() and not allow_overwrite_protected:
        existing = load(target)
        if existing and existing.source in PROTECTED_SOURCES:
            raise PermissionError(
                f"拒绝覆盖 source={existing.source} 的 memory: {target}\n"
                f"(redesign ADR-6 人改优先；如确需覆盖请用 allow_overwrite_protected=True)"
            )

    # 更新时间字段
    today = datetime.now().date().isoformat()
    if not memory.created:
        memory.created = today
    memory.updated = today

    fm_dict = _to_frontmatter_dict(memory)
    text = fm.dump(fm_dict, memory.body)
    _atomic_write(target, text)

    # 写完后立刻 stat 取 mtime 并改字段（再写一次回去——确保 _mtime_at_write 与文件 mtime 对齐）
    actual_mtime = target.stat().st_mtime
    fm_dict["_mtime_at_write"] = actual_mtime

    # 冲突检测（ADR-12）：仅对新写入做（_skip_conflict_check 防止递归）
    if not _skip_conflict_check:
        try:
            candidates = find_conflict_candidates(memory)
            if candidates:
                fm_dict["potential_conflicts"] = candidates
                memory.potential_conflicts = candidates
                # 反向标记：被冲突的旧 memory 加 potentially_superseded_by
                for old_id in candidates:
                    _mark_superseded(old_id, memory.id)
        except Exception:
            # 冲突检测失败不阻塞写入
            pass

    text = fm.dump(fm_dict, memory.body)
    _atomic_write(target, text)
    memory._mtime_at_write = actual_mtime
    memory.file_path = target
    return target


def archive(memory_id: str) -> Path | None:
    """软删除：把 memory 移到 archive/。返回新路径（找不到原件返回 None）"""
    src = _find_by_id(memory_id)
    if not src:
        return None
    ensure_data_dirs()
    dest = archive_path(memory_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def restore(memory_id: str) -> Path | None:
    """从 archive/ 恢复到原 scope。"""
    src = ARCHIVE_DIR / f"{memory_id}.md"
    if not src.exists():
        return None
    mem = load(src)
    if not mem:
        return None
    target = memory_path(mem)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    return target


# ==================== 查 ====================

def list_memories(
    scope: str | None = None,
    project_key: str | None = None,
    since: str | None = None,
    value: str | None = None,
    include_archived: bool = False,
) -> list[Memory]:
    """列出符合条件的 memory。"""
    out: list[Memory] = []
    for mem in _iter_all(include_archived=include_archived):
        if scope and mem.scope != scope:
            continue
        if project_key and mem.project_key != project_key:
            continue
        if since and mem.created < since:
            continue
        if value and mem.value != value:
            continue
        out.append(mem)
    # 按 updated 倒序
    out.sort(key=lambda m: m.updated or m.created, reverse=True)
    return out


def find_by_id(memory_id: str) -> Memory | None:
    p = _find_by_id(memory_id)
    if p is None:
        return None
    return load(p)


# ==================== 内部 ====================

def _iter_all(include_archived: bool) -> Iterable[Memory]:
    roots = [PERSONAL_DIR, PROJECTS_DIR]
    if include_archived:
        roots.append(ARCHIVE_DIR)
    for root in roots:
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            mem = load(md)
            if mem is not None:
                yield mem


def _find_by_id(memory_id: str) -> Path | None:
    for root in (PERSONAL_DIR, PROJECTS_DIR, ARCHIVE_DIR):
        if not root.exists():
            continue
        # 直接拼路径快路径（personal）
        direct = root / f"{memory_id}.md"
        if direct.exists():
            return direct
        # 项目目录在 root 下一层
        for md in root.rglob(f"{memory_id}.md"):
            return md
    return None


def _to_frontmatter_dict(memory: Memory) -> dict:
    """Memory → frontmatter dict（保持字段顺序）"""
    out: dict = {
        "id": memory.id,
        "scope": memory.scope,
    }
    if memory.scope == "project" and memory.project_key:
        out["project_key"] = memory.project_key
    out["source"] = memory.source
    out["value"] = memory.value
    if memory.created:
        out["created"] = memory.created
    if memory.updated:
        out["updated"] = memory.updated
    if memory.tags:
        out["tags"] = memory.tags
    if memory.origin:
        out["origin"] = memory.origin
    if memory.potential_conflicts:
        out["potential_conflicts"] = memory.potential_conflicts
    if memory.potentially_superseded_by:
        out["potentially_superseded_by"] = memory.potentially_superseded_by
    if memory.archived:
        out["archived"] = True
    out["_mtime_at_write"] = memory._mtime_at_write
    # extra 字段透传（除 _mtime_at_write 外）
    for k, v in memory.extra.items():
        if k not in out:
            out[k] = v
    return out


def _atomic_write(path: Path, text: str) -> None:
    """先写到临时文件再 rename，保证多 IDE 并发不损坏"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:6]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
