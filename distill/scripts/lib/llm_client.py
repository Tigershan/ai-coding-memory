"""llm_client - 轻量级 OpenAI-compatible API 客户端

做什么：
    为 distill --auto 模式提供 LLM 调用能力。
    使用 stdlib 的 urllib（无三方依赖），兼容 OpenAI / Dashscope / 任何
    OpenAI-compatible API。

设计要点：
    - 零三方依赖（只用 urllib + json）
    - 支持 base_url + api_key + model 三个参数
    - 自动区分 JSON / Markdown 结果格式
    - 超时 + 重试（最多 2 次）
    - 错误返回清晰的错误信息，不抛异常到上层

输入：
    prompt: str        完整 prompt 文本
    result_format: str  'json' | 'markdown'

输出：
    (content: str, error: str | None)
    成功时 error=None，失败时 content="" + error 包含原因
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import NamedTuple


class LLMResponse(NamedTuple):
    content: str
    error: str | None


# 默认配置（可通过 --llm-api / --llm-model / 环境变量覆盖）
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
DEFAULT_TIMEOUT = 120
MAX_RETRIES = 2


def call_llm(
    prompt: str,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> LLMResponse:
    """调用 OpenAI-compatible API

    Args:
        prompt: 完整 prompt 文本
        base_url: API base URL（默认 Dashscope）
        api_key: API key（默认从环境变量 DASHSCOPE_API_KEY 或 OPENAI_API_KEY 读取）
        model: 模型名（默认 qwen-plus）
        timeout: 超时秒数

    Returns:
        LLMResponse(content, error)
    """
    url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    key = api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return LLMResponse(
            "", "未找到 API key。请设置环境变量 DASHSCOPE_API_KEY 或 OPENAI_API_KEY，"
               "或通过 --llm-key 参数传入。"
        )

    model_name = model or DEFAULT_MODEL
    payload = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 8192,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                choices = body.get("choices", [])
                if not choices:
                    return LLMResponse("", f"API 返回无 choices: {body}")
                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    return LLMResponse("", f"API 返回空 content: {body}")
                return LLMResponse(content, None)
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            if attempt < MAX_RETRIES and e.code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            return LLMResponse("", f"HTTP {e.code}: {error_body[:500]}")
        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            return LLMResponse("", f"连接失败: {e.reason}")
        except Exception as e:
            return LLMResponse("", f"未知错误: {e}")

    return LLMResponse("", "超过最大重试次数")
