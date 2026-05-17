"""searcher._decay_weight 单测"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lib import searcher


def _fake_path(tmp_path: Path) -> Path:
    p = tmp_path / "fake.md"
    p.write_text("body", encoding="utf-8")
    return p


def test_manual_no_decay(tmp_path: Path):
    fm = {"source": "manual", "_mtime_at_write": time.time() - 365 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=time.time(),
        half_life_days=90, floor=0.5,
    )
    assert w == 1.0


def test_edited_no_decay(tmp_path: Path):
    fm = {"source": "edited", "_mtime_at_write": time.time() - 1000 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=time.time(),
        half_life_days=90, floor=0.5,
    )
    assert w == 1.0


def test_auto_at_t0(tmp_path: Path):
    now = time.time()
    fm = {"source": "auto", "_mtime_at_write": now}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.5,
    )
    assert w == pytest.approx(1.0, abs=1e-6)


def test_auto_at_half_life(tmp_path: Path):
    now = time.time()
    fm = {"source": "auto", "_mtime_at_write": now - 90 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.0,
    )
    # 半衰期点应≈0.5
    assert w == pytest.approx(0.5, abs=0.01)


def test_auto_clamped_by_floor(tmp_path: Path):
    now = time.time()
    fm = {"source": "auto", "_mtime_at_write": now - 1000 * 86400}
    w = searcher._decay_weight(
        fm, _fake_path(tmp_path), now=now,
        half_life_days=90, floor=0.5,
    )
    assert w == 0.5  # 老到接近 0，floor 兜底到 0.5


def test_missing_mtime_uses_file_stat(tmp_path: Path):
    p = _fake_path(tmp_path)
    fm = {"source": "auto"}  # 没有 _mtime_at_write
    w = searcher._decay_weight(
        fm, p, now=time.time(),
        half_life_days=90, floor=0.0,
    )
    # 文件刚创建，mtime ≈ 现在，权重应 ≈ 1.0
    assert w == pytest.approx(1.0, abs=0.01)
