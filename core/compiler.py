"""core.compiler - 编译层引擎：原子条目 → 聚合文档

把某个项目的全部原子 memory 聚合为一篇结构化的 overview.md。
触发时机：project_context 检测到 _compiled/.stale 且有同步 LLM 可用。

编译保护（ADR-6 延伸）：
    overview.md 被人编辑后（frontmatter source=edited 或 mtime 检测），
    不再自动覆盖。

公开 API：
    build_compile_prompt(project_key) -> str
    compile_project(project_key, llm_provider) -> Path | None
    read_compiled_overview(project_key) -> str | None
    is_stale(project_key) -> bool
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from . import frontmatter as fm
from . import memory_store as ms
from .paths import compiled_dir, stale_marker_path
from .project_key import _to_dir_name


PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "distill" / "prompts" / "02_compile_overview.md"
)

VALUE_ORDER = {"high": 0, "medium": 1, "low": 2}
MTIME_TOLERANCE_S = 5


def build_compile_prompt(project_key: str) -> str:
    """读取项目全部 memory，构造编译 prompt。"""
    mems = ms.list_memories(scope="project", project_key=project_key)
    if not mems:
        return ""

    mems.sort(key=lambda m: (VALUE_ORDER.get(m.value, 1), m.updated or m.created))

    entries_parts: list[str] = []
    for i, m in enumerate(mems, 1):
        source_tag = f" [source={m.source}]" if m.source in ("manual", "edited") else ""
        entries_parts.append(
            f"--- entry {i}/{len(mems)} (id={m.id}, value={m.value}{source_tag}) ---\n"
            f"{m.body.strip()}\n"
        )

    entries_text = "\n".join(entries_parts)

    dates = [m.created for m in mems if m.created]
    date_range = f"{min(dates)} ~ {max(dates)}" if dates else "unknown"

    project_dir_name = _to_dir_name(project_key)
    project_name = project_key.split("/")[-1] if "/" in project_key else project_key

    template = _load_prompt_template()
    prompt = template.format(
        project_key=project_key,
        project_name=project_name,
        project_dir=project_dir_name,
        memory_count=len(mems),
        date_range=date_range,
        compile_date=datetime.now().date().isoformat(),
        entries=entries_text,
    )
    return prompt


def compile_project(
    project_key: str,
    llm_provider=None,
) -> Path | None:
    """编译项目的 overview.md。

    返回写入路径。以下情况返回 None：
      - 项目无 memory
      - overview.md 已被人编辑（source=edited / mtime 检测）
      - LLM provider 不可用或不同步
    """
    mems = ms.list_memories(scope="project", project_key=project_key)
    if not mems:
        return None

    comp_dir = compiled_dir(project_key)
    overview_path = comp_dir / "overview.md"

    if _is_human_edited(overview_path):
        _clear_stale_marker(project_key)
        return None

    prompt = build_compile_prompt(project_key)
    if not prompt:
        return None

    if llm_provider is None:
        return None
    if not llm_provider.is_synchronous():
        return None

    try:
        output = llm_provider.run(prompt)
    except Exception:
        return None

    if not output or not output.strip():
        return None

    comp_dir.mkdir(parents=True, exist_ok=True)

    overview_fm = {
        "source": "compiled",
        "compiled_at": datetime.now().date().isoformat(),
        "memory_count": len(mems),
        "_mtime_at_write": 0,
    }
    text = fm.dump(overview_fm, output.strip() + "\n")
    _atomic_write(overview_path, text)
    overview_fm["_mtime_at_write"] = overview_path.stat().st_mtime
    text = fm.dump(overview_fm, output.strip() + "\n")
    _atomic_write(overview_path, text)

    _clear_stale_marker(project_key)
    return overview_path


def read_compiled_overview(project_key: str) -> str | None:
    """读已编译的 overview.md 正文。不存在返回 None。"""
    overview_path = compiled_dir(project_key) / "overview.md"
    if not overview_path.exists():
        return None
    try:
        text = overview_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    _, body = fm.parse(text)
    return body.strip() if body.strip() else None


def is_stale(project_key: str) -> bool:
    """检查项目编译层是否过时。"""
    return stale_marker_path(project_key).exists()


def _is_human_edited(overview_path: Path) -> bool:
    """检测 overview.md 是否被人编辑过。"""
    if not overview_path.exists():
        return False
    try:
        text = overview_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm_dict, _ = fm.parse(text)
    if fm_dict.get("source") == "edited":
        return True
    mtime_at_write = float(fm_dict.get("_mtime_at_write", 0) or 0)
    if mtime_at_write <= 0:
        return False
    try:
        actual_mtime = overview_path.stat().st_mtime
    except OSError:
        return False
    return actual_mtime > mtime_at_write + MTIME_TOLERANCE_S


def _clear_stale_marker(project_key: str) -> None:
    try:
        stale_marker_path(project_key).unlink(missing_ok=True)
    except OSError:
        pass


def _load_prompt_template() -> str:
    try:
        return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return (
            "把以下 {memory_count} 条 memory 合并为项目知识全景文档。\n"
            "project: {project_key}\n\n{entries}"
        )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:6]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _cli_compile() -> None:
    """CLI 入口：python -m core.compiler <project_key>"""
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m core.compiler <project_key>")
        print("  例: python -m core.compiler gitlab.example.com/org/repo")
        sys.exit(1)

    project_key = sys.argv[1]
    mems = ms.list_memories(scope="project", project_key=project_key)
    if not mems:
        print(f"❌ 项目 {project_key} 无 memory")
        sys.exit(1)

    print(f"📝 正在编译 {project_key}（{len(mems)} 条 memory）...")

    try:
        from .llm_provider import load_config_from_env, make_provider
        cfg = load_config_from_env()
        provider = make_provider(cfg)
    except Exception as e:
        print(f"❌ LLM provider 初始化失败: {e}")
        sys.exit(1)

    if not provider.is_synchronous():
        print("❌ 当前 LLM 模式 (host_agent) 不支持同步编译。请配置 api 或 local 模式。")
        sys.exit(1)

    result = compile_project(project_key, llm_provider=provider)
    if result is None:
        overview = compiled_dir(project_key) / "overview.md"
        if overview.exists():
            print(f"⏭ overview.md 已被人编辑，跳过覆盖: {overview}")
        else:
            print("❌ 编译失败（LLM 无输出）")
            sys.exit(1)
    else:
        print(f"✅ 编译完成: {result}")


if __name__ == "__main__":
    _cli_compile()
