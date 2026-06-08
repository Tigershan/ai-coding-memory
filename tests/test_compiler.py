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
