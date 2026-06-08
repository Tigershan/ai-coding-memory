"""向量重排配置默认值测试"""
from __future__ import annotations

import os
import pytest


def test_config_defaults_vector_enabled():
    """默认配置应开启 vector rerank"""
    from lib.config_loader import load_config
    cfg = load_config(force_reload=True)
    assert cfg.vector_rerank_enabled is True


def test_config_defaults_multilingual_model():
    """默认模型应为中文友好的多语言模型"""
    from lib.config_loader import load_config
    cfg = load_config(force_reload=True)
    assert "multilingual" in cfg.vector_rerank_model.lower() or "zh" in cfg.vector_rerank_model.lower()


def test_env_override_disables_vector():
    """环境变量可关闭 vector rerank"""
    os.environ["AI_MEMORY_VECTOR_RERANK_ENABLED"] = "false"
    try:
        from lib.config_loader import load_config
        cfg = load_config(force_reload=True)
        assert cfg.vector_rerank_enabled is False
    finally:
        os.environ.pop("AI_MEMORY_VECTOR_RERANK_ENABLED", None)
