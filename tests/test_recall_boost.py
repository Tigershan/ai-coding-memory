"""召回频率正反馈单测"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from core import recall_log
from lib import searcher


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """将 recall_log 重定向到临时目录"""
    monkeypatch.setattr(recall_log, "LOG_DIR", tmp_path)
    return tmp_path


def _write_log_entry(log_dir: Path, date: str, event: str, **fields):
    p = log_dir / f"recall-{date}.jsonl"
    record = {"ts": f"{date}T12:00:00", "event": event, **fields}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _fake_path(tmp_path: Path) -> Path:
    p = tmp_path / "fake.md"
    p.write_text("body", encoding="utf-8")
    return p


# ---- get_recall_counts 测试 ----

def test_get_recall_counts_empty(log_dir):
    counts = recall_log.get_recall_counts(days=30, force_refresh=True)
    assert counts == {}


def test_get_recall_counts_search_hits(log_dir):
    today = datetime.now().date().isoformat()
    _write_log_entry(log_dir, today, "search", query="redis", hits=["mem-a", "mem-b"])
    _write_log_entry(log_dir, today, "search", query="pool", hits=["mem-a", "mem-c"])
    counts = recall_log.get_recall_counts(days=30, force_refresh=True)
    assert counts["mem-a"] == 2
    assert counts["mem-b"] == 1
    assert counts["mem-c"] == 1


def test_get_recall_counts_read_stronger(log_dir):
    today = datetime.now().date().isoformat()
    _write_log_entry(log_dir, today, "read", id="mem-x", path="/tmp/x.md")
    counts = recall_log.get_recall_counts(days=30, force_refresh=True)
    assert counts["mem-x"] == 2


def test_get_recall_counts_cached(log_dir):
    today = datetime.now().date().isoformat()
    _write_log_entry(log_dir, today, "search", query="test", hits=["mem-y"])
    c1 = recall_log.get_recall_counts(days=30, force_refresh=True)
    _write_log_entry(log_dir, today, "search", query="test2", hits=["mem-y"])
    c3 = recall_log.get_recall_counts(days=30, force_refresh=True)
    assert c3["mem-y"] == 2


# ---- decay_weight + recall boost 测试 ----

def test_decay_with_recall_boost(tmp_path, log_dir):
    """高频召回的 memory 获得衰减加成"""
    today = datetime.now().date().isoformat()
    for _ in range(5):
        _write_log_entry(log_dir, today, "search", query="q", hits=["mem-boosted"])
    recall_log.get_recall_counts(days=30, force_refresh=True)

    now = time.time()
    fm = {"source": "auto", "id": "mem-boosted", "_mtime_at_write": now - 60 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.5,
    )
    assert w > 0.9, f"预期加成后权重 > 0.9，实际 {w}"


def test_decay_without_recall_no_boost(tmp_path, log_dir):
    """未被召回的 memory 无加成"""
    now = time.time()
    fm = {"source": "auto", "id": "mem-unseen", "_mtime_at_write": now - 60 * 86400}
    recall_log.get_recall_counts(days=30, force_refresh=True)
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.5,
    )
    assert 0.6 < w < 0.7, f"预期权重 ≈ 0.63，实际 {w}"


def test_recall_boost_capped_at_1_5(tmp_path, log_dir):
    """recall boost 上限为 1.5×"""
    today = datetime.now().date().isoformat()
    for _ in range(20):
        _write_log_entry(log_dir, today, "search", query="q", hits=["mem-hot"])
    recall_log.get_recall_counts(days=30, force_refresh=True)

    now = time.time()
    fm = {"source": "auto", "id": "mem-hot", "_mtime_at_write": now - 60 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.5,
    )
    assert w == pytest.approx(0.63 * 1.5, abs=0.05)


def test_manual_still_no_decay_with_boost(tmp_path, log_dir):
    """manual/edited source 仍然不衰减"""
    today = datetime.now().date().isoformat()
    _write_log_entry(log_dir, today, "search", query="q", hits=["mem-manual"])
    recall_log.get_recall_counts(days=30, force_refresh=True)

    now = time.time()
    fm = {"source": "manual", "id": "mem-manual", "_mtime_at_write": now - 365 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.5,
    )
    assert w == 1.0
