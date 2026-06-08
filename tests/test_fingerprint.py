# tests/test_fingerprint.py
"""BM25 索引指纹 + 缓存测试"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lib import bm25_index


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清空进程缓存"""
    bm25_index._cache.clear()
    bm25_index._fingerprint_cache.clear()
    yield
    bm25_index._cache.clear()
    bm25_index._fingerprint_cache.clear()


def _create_md(directory: Path, name: str, content: str = "# Test\nbody"):
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


def test_fingerprint_changes_on_new_file(tmp_path):
    _create_md(tmp_path, "a.md")
    idx1 = bm25_index.get_index(tmp_path)
    assert idx1 is not None
    assert len(idx1.paths) == 1

    _create_md(tmp_path, "b.md", "# New\nnew body")
    # 清除指纹 TTL 缓存以立即检测
    bm25_index._fingerprint_cache.clear()
    idx2 = bm25_index.get_index(tmp_path)
    assert idx2 is not None
    assert len(idx2.paths) == 2
    assert idx2 is not idx1


def test_fingerprint_changes_on_modified_file(tmp_path):
    p = _create_md(tmp_path, "a.md")
    idx1 = bm25_index.get_index(tmp_path)

    time.sleep(0.05)  # 确保 mtime 变化
    p.write_text("# Updated\nnew content", encoding="utf-8")
    bm25_index._fingerprint_cache.clear()
    idx2 = bm25_index.get_index(tmp_path)
    assert idx2 is not idx1


def test_cache_hit_returns_same_index(tmp_path):
    _create_md(tmp_path, "a.md")
    idx1 = bm25_index.get_index(tmp_path)
    idx2 = bm25_index.get_index(tmp_path)
    assert idx1 is idx2


def test_empty_scope_returns_none(tmp_path):
    assert bm25_index.get_index(tmp_path) is None
