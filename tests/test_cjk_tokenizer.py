"""CJK 分词器单测（jieba 模式 + bigram 兜底模式）"""
from __future__ import annotations

import pytest

from lib.cjk_tokenizer import tokenize_cjk, _bigram_with_stopwords


def test_bigram_basic():
    """bigram 兜底基本功能"""
    tokens = _bigram_with_stopwords("连接池配置")
    assert "连接" in tokens
    assert "接池" in tokens
    assert "配置" in tokens


def test_bigram_stopwords_filtered():
    """高频无意义 bigram 应被过滤"""
    tokens = _bigram_with_stopwords("他的是一个好的方案")
    assert "的是" not in tokens


def test_bigram_single_char():
    """单字作为 unigram"""
    tokens = _bigram_with_stopwords("猫")
    assert "猫" in tokens


def test_tokenize_cjk_chinese():
    """中文分词（jieba 或 fallback）"""
    tokens = tokenize_cjk("Redis连接池配置优化")
    assert any("连接" in t for t in tokens)
    assert any("配置" in t for t in tokens)


def test_tokenize_cjk_empty():
    assert tokenize_cjk("") == []


def test_tokenize_cjk_no_cjk():
    """纯 ASCII 不应产生 CJK token"""
    tokens = tokenize_cjk("hello world")
    assert tokens == []


def test_tokenize_cjk_mixed():
    """中英混合只提取 CJK 部分"""
    tokens = tokenize_cjk("Redis连接池maxIdle配置")
    assert any("连接" in t for t in tokens)
    assert any("配置" in t for t in tokens)
    assert "redis" not in tokens
    assert "maxidle" not in tokens
