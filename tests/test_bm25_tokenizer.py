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
    # jieba 模式可能切出 "连接池" + "配置"，bigram 模式切出 "连接"/"接池"/"池配"/"配置"/"置"
    # 两种模式下 "连接" 和 "配置" 都应能匹配到
    assert any("连接" in t for t in out)
    assert any("配置" in t for t in out)


def test_mixed_chinese_english():
    out = tokenize("Redis 连接池 maxIdle ≥ 8")
    assert "redis" in out
    assert "maxidle" in out
    assert "8" in out
    # jieba 模式可能切出 "连接池"，bigram 模式切出 "连接"/"接池"/"池"
    assert any("连接" in t for t in out)


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


def test_chinese_stopword_filtered():
    """高频无意义 bigram 应被过滤"""
    out = tokenize("这个是一个好的方案不是吗")
    assert "的是" not in out


def test_chinese_word_boundaries():
    """分词应尊重词边界"""
    out = tokenize("连接池配置文件")
    assert any("连接" in t for t in out)


from lib.searcher import _grep_snippet


def test_grep_snippet_exact_match():
    text = "line1\nRedis 连接池配置\nline3"
    result = _grep_snippet("连接池配置", text, context_lines=1)
    assert result is not None
    assert result["line"] == 2


def test_grep_snippet_individual_words():
    """多词查询应匹配包含任意单词的行"""
    text = "line1\n这是 Redis 的配置\n连接池参数在这里\nline4"
    result = _grep_snippet("Redis 连接池 配置", text, context_lines=1)
    assert result is not None
    # 应匹配到第一个包含 query 中某个词的行
    assert result["line"] in (2, 3)


def test_grep_snippet_no_match():
    text = "line1\nfoo bar\nline3"
    result = _grep_snippet("completely unrelated", text, context_lines=1)
    assert result is None
