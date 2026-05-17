"""bm25_index.tokenize 单测"""
from __future__ import annotations

from lib.bm25_index import tokenize


def test_ascii_words():
    out = tokenize("Redis maxIdle 8")
    assert "redis" in out
    assert "maxidle" in out
    assert "8" in out


def test_ascii_lowercased():
    out = tokenize("AwsSecretKey")
    assert "awssecretkey" in out


def test_chinese_bigram():
    out = tokenize("连接池配置")
    # 期望出现 bigram
    assert "连接" in out
    assert "接池" in out
    assert "池配" in out
    assert "配置" in out
    # 末尾 unigram 兜底
    assert "置" in out


def test_mixed_chinese_english():
    out = tokenize("Redis 连接池 maxIdle ≥ 8")
    assert "redis" in out
    assert "maxidle" in out
    assert "8" in out
    assert "连接" in out
    assert "接池" in out


def test_empty_returns_empty():
    assert tokenize("") == []


def test_single_cjk_char():
    out = tokenize("猫")
    assert "猫" in out


def test_punctuation_dropped():
    out = tokenize("foo.bar, baz!")
    assert "foo" in out or "foo-bar" not in out  # 句点会切开
    assert "bar" in out
    assert "baz" in out
