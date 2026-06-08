"""编译层与 agents_md_sync 集成测试"""
from __future__ import annotations
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PROJECT_KEY = "gitlab.example.com/org/repo"


def _patch_dirs(tmp_path, monkeypatch):
    for mod in ("core.paths", "core.memory_store"):
        for name in ("PERSONAL_DIR", "PROJECTS_DIR", "ARCHIVE_DIR"):
            monkeypatch.setattr(f"{mod}.{name}", tmp_path / name.lower().replace("_dir", ""))
    for d in ("personal", "projects", "archive"):
        (tmp_path / d).mkdir(exist_ok=True)


def _setup_project(tmp_path, monkeypatch):
    _patch_dirs(tmp_path, monkeypatch)
    from core.project_key import _to_dir_name
    project_dir = tmp_path / "projects" / _to_dir_name(_PROJECT_KEY)
    project_dir.mkdir(parents=True)
    (project_dir / "mem-0001.md").write_text(
        f"---\nid: mem-0001\nscope: project\nproject_key: {_PROJECT_KEY}\n"
        "source: auto\nvalue: high\ncreated: 2026-06-01\nupdated: 2026-06-01\n"
        "tags: [test]\n_mtime_at_write: 0\n---\n"
        "# 原子笔记标题\n原子内容\n"
    )
    return project_dir


def test_build_summary_prefers_compiled_overview(tmp_path, monkeypatch):
    """build_summary 有 _compiled/overview.md 时应优先返回其内容"""
    project_dir = _setup_project(tmp_path, monkeypatch)
    compiled_dir = project_dir / "_compiled"
    compiled_dir.mkdir()
    (compiled_dir / "overview.md").write_text(
        "---\nsource: compiled\ncompiled_at: 2026-06-08\nmemory_count: 1\n_mtime_at_write: 0\n---\n"
        "# Repo 项目知识总览\n\n> 自动编译自 1 条原子笔记\n\n## 核心约定\n- 编译后的聚合内容\n"
    )

    from core.agents_md_sync import build_summary
    summary = build_summary(_PROJECT_KEY)
    assert "编译后的聚合内容" in summary
    assert "项目知识总览" in summary


def test_build_summary_falls_back_without_compiled(tmp_path, monkeypatch):
    """没有 _compiled/overview.md 时回退到逐条摘要"""
    _setup_project(tmp_path, monkeypatch)

    from core.agents_md_sync import build_summary
    summary = build_summary(_PROJECT_KEY)
    assert "项目记忆摘要" in summary
    assert "原子笔记标题" in summary


def test_project_context_compile_flow(tmp_path, monkeypatch):
    """完整流程：stale 检测 → 编译 → 清除 stale"""
    project_dir = _setup_project(tmp_path, monkeypatch)
    comp = project_dir / "_compiled"
    comp.mkdir()
    (comp / ".stale").write_text("1234567890")

    from core.compiler import is_stale, compile_project
    assert is_stale(_PROJECT_KEY)

    class FakeProvider:
        def is_synchronous(self):
            return True
        def run(self, prompt, *, system=""):
            return "# Test 项目知识总览\n\n## 核心约定\n- 编译触发成功\n"

    result = compile_project(_PROJECT_KEY, llm_provider=FakeProvider())
    assert result is not None
    assert not is_stale(_PROJECT_KEY)

    from core.agents_md_sync import build_summary
    summary = build_summary(_PROJECT_KEY)
    assert "编译触发成功" in summary
