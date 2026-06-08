"""CJK 分词模块：jieba 可用时用 jieba，否则 bigram + 停用词过滤。

bm25_index.tokenize() 调用本模块处理 CJK 字符部分。
ASCII 词切分仍在 bm25_index 里完成。
"""
from __future__ import annotations

import re

_CJK_RE = re.compile(r"[一-鿿]+")

# 高频无意义 bigram 停用表（中文虚词 + 高频组合）
_STOP_BIGRAMS = frozenset({
    "的是", "不是", "是一", "一个", "了一", "在一", "的一",
    "是在", "了的", "的了", "在了", "不了", "也是", "就是",
    "但是", "还是", "只是", "可以", "这个", "那个", "什么",
    "没有", "已经", "因为", "所以", "如果", "虽然", "然后",
    "或者", "以及", "而且", "不过",
})

# jieba 懒加载
_jieba = None
_jieba_checked = False


def _try_load_jieba():
    global _jieba, _jieba_checked
    if _jieba_checked:
        return _jieba is not None
    _jieba_checked = True
    try:
        import jieba
        jieba.setLogLevel(20)  # 抑制 jieba 的 INFO 级日志
        _jieba = jieba
        return True
    except ImportError:
        return False


def _bigram_with_stopwords(cjk_text: str) -> list[str]:
    """CJK bigram 分词 + 停用词过滤 + 末尾 unigram。

    兜底模式：当 jieba 不可用时使用。
    """
    chars = list(cjk_text)
    if not chars:
        return []
    if len(chars) == 1:
        return chars
    tokens: list[str] = []
    for i in range(len(chars) - 1):
        bg = chars[i] + chars[i + 1]
        if bg not in _STOP_BIGRAMS:
            tokens.append(bg)
    tokens.append(chars[-1])
    return tokens


def _jieba_cut(cjk_text: str) -> list[str]:
    """用 jieba 精确模式切分。"""
    words = list(_jieba.cut(cjk_text, cut_all=False))
    return [w for w in words if len(w) >= 1 and w.strip()]


def tokenize_cjk(text: str) -> list[str]:
    """从混合文本中提取 CJK 片段并分词。

    返回值仅包含 CJK token（ASCII 词由调用方处理）。
    """
    segments = _CJK_RE.findall(text)
    if not segments:
        return []
    use_jieba = _try_load_jieba()
    tokens: list[str] = []
    for seg in segments:
        if use_jieba:
            tokens.extend(_jieba_cut(seg))
        else:
            tokens.extend(_bigram_with_stopwords(seg))
    return tokens
