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


# ==================== Task 5: 编译引擎 ====================

_PROJECT_KEY = "gitlab.example.com/org/repo"


def _make_project_dir(tmp_path, monkeypatch):
    """创建带 monkeypatch 的项目目录，返回 project_dir"""
    _patch_dirs(tmp_path, monkeypatch)
    from core.project_key import _to_dir_name
    project_dir = tmp_path / "projects" / _to_dir_name(_PROJECT_KEY)
    project_dir.mkdir(parents=True)
    return project_dir


def _write_mem(project_dir, mem_id, title, value="high", body_extra=""):
    (project_dir / f"{mem_id}.md").write_text(
        f"---\nid: {mem_id}\nscope: project\nproject_key: {_PROJECT_KEY}\n"
        f"source: auto\nvalue: {value}\ncreated: 2026-06-01\nupdated: 2026-06-01\n"
        f"tags: [test]\n_mtime_at_write: 0\n---\n"
        f"# {title}\n\n## 结论\n{title} content\n{body_extra}"
    )


class FakeProvider:
    def is_synchronous(self):
        return True
    def run(self, prompt, *, system=""):
        return "# Test 项目知识总览\n\n> 自动编译\n\n## 核心约定\n- compiled result\n"


def test_build_compile_prompt_includes_all_entries(tmp_path, monkeypatch):
    """build_compile_prompt 应包含所有 memory 条目，按 value 排序"""
    project_dir = _make_project_dir(tmp_path, monkeypatch)
    _write_mem(project_dir, "mem-high-0001", "Redis 连接池配置", "high")
    _write_mem(project_dir, "mem-low-0002", "调试日志", "low")

    from core.compiler import build_compile_prompt
    prompt = build_compile_prompt(_PROJECT_KEY)
    assert "Redis 连接池配置" in prompt
    assert "调试日志" in prompt
    assert "mem-high-0001" in prompt
    assert prompt.index("Redis 连接池配置") < prompt.index("调试日志")


def test_compile_writes_overview(tmp_path, monkeypatch):
    """compile_project 应写入 _compiled/overview.md 并删除 .stale"""
    project_dir = _make_project_dir(tmp_path, monkeypatch)
    _write_mem(project_dir, "mem-0001", "Test Memory")

    comp = project_dir / "_compiled"
    comp.mkdir()
    (comp / ".stale").write_text("1234567890")

    from core.compiler import compile_project
    result = compile_project(_PROJECT_KEY, llm_provider=FakeProvider())
    assert result is not None
    assert (comp / "overview.md").exists()
    assert not (comp / ".stale").exists()
    content = (comp / "overview.md").read_text()
    assert "项目知识总览" in content


def test_compile_skips_edited_overview(tmp_path, monkeypatch):
    """overview.md 已被人编辑（source: edited）时应跳过不覆盖"""
    project_dir = _make_project_dir(tmp_path, monkeypatch)
    _write_mem(project_dir, "mem-0001", "Test Memory")

    comp = project_dir / "_compiled"
    comp.mkdir()
    (comp / "overview.md").write_text(
        "---\nsource: edited\n_mtime_at_write: 0\n---\n# 我手动编辑过的总览\n自定义内容\n"
    )
    (comp / ".stale").write_text("1234567890")

    from core.compiler import compile_project
    result = compile_project(_PROJECT_KEY, llm_provider=FakeProvider())
    assert result is None
    content = (comp / "overview.md").read_text()
    assert "我手动编辑过的总览" in content


def test_compile_no_memories_returns_none(tmp_path, monkeypatch):
    """项目无 memory 时 compile_project 返回 None"""
    project_dir = _make_project_dir(tmp_path, monkeypatch)

    from core.compiler import compile_project
    result = compile_project(_PROJECT_KEY, llm_provider=None)
    assert result is None


def test_read_compiled_overview(tmp_path, monkeypatch):
    """read_compiled_overview 读取已编译文档正文"""
    project_dir = _make_project_dir(tmp_path, monkeypatch)
    comp = project_dir / "_compiled"
    comp.mkdir()
    (comp / "overview.md").write_text(
        "---\nsource: compiled\n_mtime_at_write: 0\n---\n# 项目总览\n内容段落\n"
    )

    from core.compiler import read_compiled_overview
    body = read_compiled_overview(_PROJECT_KEY)
    assert body is not None
    assert "项目总览" in body
    assert "内容段落" in body


def test_read_compiled_overview_returns_none_when_missing(tmp_path, monkeypatch):
    """无 overview.md 时返回 None"""
    _make_project_dir(tmp_path, monkeypatch)

    from core.compiler import read_compiled_overview
    assert read_compiled_overview(_PROJECT_KEY) is None


def test_is_stale(tmp_path, monkeypatch):
    """is_stale 检测 .stale 标记"""
    project_dir = _make_project_dir(tmp_path, monkeypatch)

    from core.compiler import is_stale
    assert not is_stale(_PROJECT_KEY)

    comp = project_dir / "_compiled"
    comp.mkdir()
    (comp / ".stale").write_text("1")
    assert is_stale(_PROJECT_KEY)
