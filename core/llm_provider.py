"""core.llm_provider - LLM 来源抽象层（按 redesign §6.0 / ADR-10）

三档来源按优先级：
    1. host_agent（默认）：宿主 agent 自跑（通过 MCP 任务包） — P3 落地
    2. api（可选加速）：OpenAI-compatible HTTP API
    3. local（远期）：Ollama 等

P2 范围内只实现 api 模式 + 抽象接口；host_agent 的"任务包写盘"留 P3。

接口契约：
    provider.is_synchronous()  -> bool   同步可调（api/local） vs 异步通过任务包
    provider.run(prompt)       -> str    同步：直接返回 LLM 输出
                                          异步：写任务包，抛 PendingTaskError

配置来源（按优先级）：
    1. AI_MEMORY_LLM_MODE / AI_MEMORY_LLM_API_KEY 等环境变量
    2. ~/.ai-memory/config.yml  llm: 段
    3. 自动检测：有 OPENAI_API_KEY/DASHSCOPE_API_KEY → api；否则 host_agent

注意：自动检测仅决定 mode；不会偷偷用 key——install.sh 必须显式询问用户（C 方案）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


# ==================== 异常 ====================

class PendingTaskError(Exception):
    """host_agent 模式下 run() 抛出：任务已写入 .pending/，等 agent 消化"""

    def __init__(self, task_id: str, task_path: str):
        super().__init__(f"task pending: {task_id} → {task_path}")
        self.task_id = task_id
        self.task_path = task_path


class LLMCallError(Exception):
    """api/local 模式下 run() 失败"""


# ==================== 配置 ====================

@dataclass
class LLMConfig:
    mode: str = "auto"                       # auto → 由 detect 决定；可被覆盖为 host_agent | api | local
    # ---- api 模式（OpenAI / DashScope / 兼容） ----
    api_provider: str = "dashscope"
    api_base: str = ""                       # 留空时用 provider 默认
    api_model: str = "qwen-plus"
    api_key: str = ""
    api_timeout_s: int = 60
    api_max_retries: int = 2
    api_concurrency: int = 4
    api_daily_budget_yuan: float = 0.0       # 0 = 无限制
    # ---- local 模式（Ollama 等本地推理） ----
    local_base: str = ""                     # 留空时用 LOCAL_DEFAULT_BASE
    local_model: str = ""                    # 留空时用 LOCAL_DEFAULT_MODEL
    local_timeout_s: int = 0                 # 留空时用 LOCAL_DEFAULT_TIMEOUT_S


_DEFAULTS = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}


def detect_mode_from_env(cfg: LLMConfig | None = None) -> str:
    """按优先级解析 mode（不读 config.yml；config.py 加载时会覆写本函数返回值）"""
    env_mode = os.environ.get("AI_MEMORY_LLM_MODE")
    if env_mode:
        return env_mode
    if cfg and cfg.mode and cfg.mode != "auto":
        return cfg.mode
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"):
        return "api"
    return "host_agent"


def load_config_from_env() -> LLMConfig:
    """从 env / config.yml 加载（P3 加 config.yml 完整支持；现在主要走 env）"""
    cfg = LLMConfig()

    # 1. 从 ~/.ai-memory/config.yml 加载（如果存在；用 frontmatter parser 复用）
    try:
        from .paths import USER_CONFIG_PATH
        from . import frontmatter as fm
        if USER_CONFIG_PATH.exists():
            text = USER_CONFIG_PATH.read_text(encoding="utf-8")
            # config.yml 不是 markdown，包成 frontmatter 复用 parser
            data = fm._parse_yaml(text)
            llm = data.get("llm") or {}
            cfg.mode = llm.get("mode") or cfg.mode
            api = llm.get("api") or {}
            cfg.api_provider = api.get("provider") or cfg.api_provider
            cfg.api_base = api.get("base") or cfg.api_base
            cfg.api_model = api.get("model") or cfg.api_model
            cfg.api_timeout_s = int(api.get("timeout_s") or cfg.api_timeout_s)
            cfg.api_max_retries = int(api.get("max_retries") or cfg.api_max_retries)
            cfg.api_concurrency = int(api.get("concurrency") or cfg.api_concurrency)
            cfg.api_daily_budget_yuan = float(api.get("daily_budget_yuan") or cfg.api_daily_budget_yuan)
            key_env = api.get("key_env") or "DASHSCOPE_API_KEY"
            cfg.api_key = os.environ.get(key_env, "")
            local = llm.get("local") or {}
            cfg.local_base = local.get("base") or cfg.local_base
            cfg.local_model = local.get("model") or cfg.local_model
            cfg.local_timeout_s = int(local.get("timeout_s") or cfg.local_timeout_s)
    except Exception:
        # 解析失败不阻塞，走 env 兜底
        pass

    # 2. env 直接覆盖
    cfg.mode = os.environ.get("AI_MEMORY_LLM_MODE", cfg.mode)
    cfg.api_model = os.environ.get("AI_MEMORY_LLM_MODEL", cfg.api_model)
    cfg.api_base = os.environ.get("AI_MEMORY_LLM_API_BASE", cfg.api_base)
    if not cfg.api_key:
        cfg.api_key = (
            os.environ.get("AI_MEMORY_LLM_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )

    # 3. mode=auto 解析
    if cfg.mode == "auto":
        cfg.mode = detect_mode_from_env(cfg)

    # 4. provider 默认 base url
    if cfg.mode == "api" and not cfg.api_base:
        cfg.api_base = _DEFAULTS.get(cfg.api_provider, _DEFAULTS["dashscope"])

    return cfg


# ==================== Provider 接口 ====================

class LLMProvider:
    """所有 provider 的基类"""

    def is_synchronous(self) -> bool:
        raise NotImplementedError

    def run(self, prompt: str, *, system: str = "") -> str:
        """同步：返回 LLM 文本输出。
        异步：抛 PendingTaskError（来自 host_agent provider）。
        失败：抛 LLMCallError。"""
        raise NotImplementedError

    @property
    def mode(self) -> str:
        raise NotImplementedError


# ==================== api 模式 ====================

class ApiProvider(LLMProvider):
    """OpenAI-compatible HTTP API"""

    def __init__(self, cfg: LLMConfig):
        if not cfg.api_key:
            raise LLMCallError(
                "api 模式需要 API key。请设置 DASHSCOPE_API_KEY / OPENAI_API_KEY，"
                "或在 ~/.ai-memory/config.yml 中配置 llm.api.key_env"
            )
        self.cfg = cfg

    @property
    def mode(self) -> str:
        return "api"

    def is_synchronous(self) -> bool:
        return True

    def run(self, prompt: str, *, system: str = "") -> str:
        url = self.cfg.api_base.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {
            "model": self.cfg.api_model,
            "messages": [
                *([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }

        last_err: Exception | None = None
        for attempt in range(self.cfg.api_max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.cfg.api_timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                payload = json.loads(raw)
                choices = payload.get("choices") or []
                if not choices:
                    raise LLMCallError(f"API 返回无 choices: {raw[:200]}")
                msg = choices[0].get("message") or {}
                content = msg.get("content") or ""
                if not content:
                    raise LLMCallError(f"API 返回空 content: {raw[:200]}")
                return content
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
                last_err = e
                if attempt < self.cfg.api_max_retries:
                    time.sleep(1.5 ** attempt)
                    continue
            except LLMCallError:
                raise
        raise LLMCallError(f"API 调用失败（已重试 {self.cfg.api_max_retries} 次）: {last_err}")


# ==================== host_agent 模式（P3 落地） ====================

class HostAgentProvider(LLMProvider):
    """通过任务包让宿主 agent 自跑（P3 落地）。

    run() 不直接调 LLM，而是把 prompt 写入 .pending/<id>.task 后抛 PendingTaskError。
    distill 主流程捕获该异常 → 仅记 stats（pending_task），不当成 error。
    宿主 agent 后续通过 MCP 工具消化任务包（见 task_pack.take_next + submit_result）。

    注意：调用方需通过 set_session_context() 提前注入当前 session/project_key，
    否则任务包元数据会缺失（仍可写入，但 agent 消化时会丢上下文）。
    """

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self._session: dict | None = None
        self._project_key: str | None = None
        self._batch_date: str | None = None

    @property
    def mode(self) -> str:
        return "host_agent"

    def is_synchronous(self) -> bool:
        return False

    def set_session_context(self, session: dict, project_key: str | None,
                            *, batch_date: str | None = None) -> None:
        """distill 主流程在每个 session 调 run() 之前调一下，注入元数据。

        batch_date: 任务对应的会话日期 YYYY-MM-DD（init/lazy_trigger 应当传入）。
            消化时按 batch_date 升序排队，让多天历史均摊到多天里慢慢消化。
        """
        self._session = session
        self._project_key = project_key
        self._batch_date = batch_date

    def run(self, prompt: str, *, system: str = "") -> str:
        from . import task_pack
        if self._session is None:
            # 兜底：用 minimum 元数据
            session = {"sessionId": "anonymous", "ide": "?", "workspace": ""}
        else:
            session = self._session
        full_prompt = (system + "\n\n" + prompt) if system else prompt
        task_id = task_pack.write_task(
            full_prompt, session, self._project_key,
            batch_date=self._batch_date,
        )
        raise PendingTaskError(
            task_id=task_id,
            task_path=str(task_pack.PENDING_DIR / f"{task_id}.task"),
        )


# ==================== local 模式（Ollama，本地推理） ====================

# Ollama OpenAI-compatible endpoint 默认值
LOCAL_DEFAULT_BASE = "http://localhost:11434/v1"
LOCAL_DEFAULT_MODEL = "qwen3:8b"
LOCAL_DEFAULT_TIMEOUT_S = 120  # 本地 8B 单次蒸馏 30-60s，留 2 倍余量


class LocalProvider(LLMProvider):
    """本地 LLM (Ollama)。用 Ollama native /api/chat（含 think:false 关 reasoning）。

    为什么用 native 而非 OpenAI-compatible /v1/chat/completions：
        qwen3 / qwen3.5 等系列默认开 reasoning mode，把 token budget 都耗在
        thinking chain 里（OpenAI endpoint 把 thinking 当成 content 一部分但
        外露到 message.content 时会被截掉，结果 content="finish_reason=length"）。
        Ollama native /api/chat 接受 think:false 直接禁用，输出立即落到
        message.content。OpenAI endpoint 当前不支持 think 参数。

    适合 init / 批量回溯场景：0 现金 / 0 IDE 配额。
    """

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        # base 形如 http://localhost:11434/v1，但我们要打 /api/chat —— 统一去掉 /v1
        base = (cfg.local_base or LOCAL_DEFAULT_BASE).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self._base = base
        self._model = cfg.local_model or LOCAL_DEFAULT_MODEL
        self._timeout = int(cfg.local_timeout_s or LOCAL_DEFAULT_TIMEOUT_S)

    @property
    def mode(self) -> str:
        return "local"

    def is_synchronous(self) -> bool:
        return True

    def run(self, prompt: str, *, system: str = "") -> str:
        url = self._base + "/api/chat"
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                *([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,  # 关 reasoning chain；distill 不需要中间思考过程
            "options": {
                "temperature": 0.2,
                "num_predict": 4096,  # 蒸馏 YAML 输出上限（足够 N 个 topic）
            },
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise LLMCallError(
                f"本地 Ollama 调用失败：{e}\n"
                f"  请确认: 1) `ollama serve` 在跑  2) `ollama pull {self._model}` 已完成\n"
                f"  当前 base={self._base}, model={self._model}"
            ) from e
        except (OSError, TimeoutError) as e:
            raise LLMCallError(
                f"本地 Ollama 超时（>{self._timeout}s）：{e}\n"
                f"  长 prompt + 大模型本来就慢；可加大 timeout 或换更小模型"
            ) from e
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LLMCallError(f"Ollama 返回非 JSON：{raw[:200]!r}") from e

        # /api/chat 返回结构：{"message": {"role": "assistant", "content": "..."}, "done": true, ...}
        msg = payload.get("message") or {}
        content = msg.get("content") or ""
        if not content:
            raise LLMCallError(
                f"Ollama 返回空 content（done_reason={payload.get('done_reason')}, "
                f"eval_count={payload.get('eval_count')}): {raw[:200]}"
            )
        return content


# ==================== 工厂 ====================

def make_provider(cfg: LLMConfig | None = None) -> LLMProvider:
    """根据 config 返回对应 provider 实例"""
    cfg = cfg or load_config_from_env()
    if cfg.mode == "api":
        return ApiProvider(cfg)
    if cfg.mode == "host_agent":
        return HostAgentProvider(cfg)
    if cfg.mode == "local":
        return LocalProvider(cfg)
    raise ValueError(f"未知 LLM mode: {cfg.mode!r}")


# ==================== 调试入口 ====================

def _debug() -> None:
    cfg = load_config_from_env()
    print(json.dumps({
        "mode": cfg.mode,
        "api_provider": cfg.api_provider,
        "api_base": cfg.api_base,
        "api_model": cfg.api_model,
        "api_key_present": bool(cfg.api_key),
        "api_key_source": _which_key_env(),
        "api_concurrency": cfg.api_concurrency,
    }, ensure_ascii=False, indent=2))


def _which_key_env() -> str | None:
    for name in ("AI_MEMORY_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            return name
    return None


if __name__ == "__main__":
    _debug()
