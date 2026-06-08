# tests/test_compiler.py
"""编译层测试"""
from __future__ import annotations
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_compiled_dir_returns_correct_path(tmp_path, monkeypatch):
    """compiled_dir 返回 projects/<dir_name>/_compiled/"""
    monkeypatch.setattr("core.paths.PROJECTS_DIR", tmp_path / "projects")
    from core.paths import compiled_dir
    result = compiled_dir("gitlab.example.com/org/repo")
    assert result == tmp_path / "projects" / "gitlab.example.com_org_repo" / "_compiled"


def test_stale_marker_path(tmp_path, monkeypatch):
    """stale_marker_path 返回 _compiled/.stale"""
    monkeypatch.setattr("core.paths.PROJECTS_DIR", tmp_path / "projects")
    from core.paths import stale_marker_path
    result = stale_marker_path("gitlab.example.com/org/repo")
    assert result.name == ".stale"
    assert result.parent.name == "_compiled"


# ==================== Task 2: BM25 索引排除 _compiled/ ====================

def test_bm25_index_excludes_compiled_dir(tmp_path):
    """BM25 索引不应包含 _compiled/ 下的 .md 文件"""
    normal = tmp_path / "normal.md"
    normal.write_text("---\nid: normal-1234\nscope: project\n---\n# Normal Memory\nSome content here")

    compiled = tmp_path / "_compiled"
    compiled.mkdir()
    (compiled / "overview.md").write_text("---\nsource: compiled\n---\n# Project Overview\nAggregated content")

    from lib.bm25_index import get_index
    idx = get_index(tmp_path)
    assert idx is not None
    indexed_names = [p.name for p in idx.paths]
    assert "normal.md" in indexed_names
    assert "overview.md" not in indexed_names


# ==================== Task 3: save() 标记 stale ====================

def _patch_dirs(tmp_path, monkeypatch):
    """共用的目录 monkeypatch"""
    for mod in ("core.paths", "core.memory_store"):
        for name in ("PERSONAL_DIR", "PROJECTS_DIR", "ARCHIVE_DIR"):
            monkeypatch.setattr(f"{mod}.{name}", tmp_path / name.lower().replace("_dir", ""))
    for d in ("personal", "projects", "archive"):
        (tmp_path / d).mkdir(exist_ok=True)


def test_save_marks_compiled_stale(tmp_path, monkeypatch):
    """memory_store.save() 对 scope=project 的写入应标记 _compiled/.stale"""
    _patch_dirs(tmp_path, monkeypatch)

    from core.memory_store import Memory, save
    mem = Memory(
        id="test-stale-0001",
        scope="project",
        title="Test Stale",
        body="# Test Stale\nSome content",
        project_key="gitlab.example.com/org/repo",
        source="auto",
        value="medium",
        tags=["test"],
    )
    save(mem)

    from core.paths import stale_marker_path
    stale = stale_marker_path("gitlab.example.com/org/repo")
    assert stale.exists(), "_compiled/.stale 应该在 save() 后被创建"


def test_save_personal_does_not_mark_stale(tmp_path, monkeypatch):
    """scope=personal 的写入不应触发 _compiled/.stale"""
    _patch_dirs(tmp_path, monkeypatch)

    from core.memory_store import Memory, save
    mem = Memory(
        id="test-personal-0001",
        scope="personal",
        title="Test Personal",
        body="# Test Personal\nSome content",
        source="auto",
        value="medium",
        tags=["test"],
    )
    save(mem)

    stale_files = list((tmp_path / "projects").rglob(".stale"))
    assert len(stale_files) == 0, "personal scope 不应创建 .stale 标记"
