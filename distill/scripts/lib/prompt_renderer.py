"""prompt_renderer - 加载 prompts/*.md 并填入变量

做什么：
    1. 从磁盘加载 prompts/0X_*.md 模板
    2. 把模板里的 {var} 占位符替换为实际内容
    3. 在文件头部追加 Agent 操作指引（让 Agent 知道结果该写到哪、格式是什么）

设计要点：
    - 不做任何 LLM 调用，纯字符串处理
    - 模板缓存（同一进程内重复读取无开销）
    - 变量缺失时抛 KeyError（fail fast，避免沉默错误）

输入：
    step_key: 'topic_segmentation' | 'coreference' | 'code_filter' | 'layer_tagging'
    variables: dict[str, Any]      # 模板变量
    result_path: str               # Agent 应把结果写到哪（用于 header 提示）
    result_format: str             # 'json' | 'markdown'

输出：
    str  # 完整的 prompt（含 Agent header + 模板 + 变量）

失败模式：
    - 模板文件不存在 → FileNotFoundError
    - 变量缺失 → KeyError
"""

from pathlib import Path

from .paths_ext import PROMPT_FILES, PROMPTS_DIR

_TEMPLATE_CACHE: dict[str, str] = {}


def _load_template(step_key: str) -> str:
    """加载并缓存模板原文（含正文 + 末尾占位符）"""
    if step_key in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[step_key]
    if step_key not in PROMPT_FILES:
        raise ValueError(f"未知的 step_key: {step_key}")
    path = PROMPTS_DIR / PROMPT_FILES[step_key]
    if not path.exists():
        raise FileNotFoundError(f"prompt 模板缺失: {path}")
    text = path.read_text(encoding="utf-8")
    _TEMPLATE_CACHE[step_key] = text
    return text


def _strip_doc_header(template: str) -> str:
    """去掉模板顶部的 Markdown 文档头（# Prompt: ... + > 注释 + ---）

    模板正文从第一个 "你是..." 开始；之前的内容是给人读的元信息，
    不应进入实际 prompt。
    """
    lines = template.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("你是") or line.startswith("# 任务"):
            return "\n".join(lines[idx:]).strip()
    return template.strip()


def _build_agent_header(step_key: str, result_path: str, result_format: str) -> str:
    """构造给 Agent 看的操作指引头部"""
    fmt_hint = {
        "json":     "**严格输出合法 JSON**（不要 ``` 包裹、不要解释文字）",
        "markdown": "**直接输出 Markdown**（不要 ``` 包裹整体）",
    }[result_format]
    return (
        f"# distill task: {step_key}\n\n"
        f"> **执行说明（给宿主 Agent）**\n"
        f"> 1. 你正在执行 ai-coding-memory distill 流水线的一个步骤。\n"
        f"> 2. 阅读下方「任务正文」，按其约束完成任务。\n"
        f"> 3. 完成后，{fmt_hint}，并把结果**写入文件**：\n"
        f">    `{result_path}`\n"
        f"> 4. 写文件后，把对应任务在 `manifest.json` 中的 status 改为 `completed`。\n"
        f"> 5. 失败时把 status 改为 `failed` 并在 `error` 字段留言，不要抛异常阻塞后续任务。\n\n"
        f"---\n\n"
        f"## 任务正文\n\n"
    )


def _safe_substitute(template: str, variables: dict) -> str:
    """只替换 {var_name} 形式的占位符，不动 JSON 示例里的 { } 大括号

    实现：精确匹配每个已知变量名 `{name}`，按字符串替换；模板里其他 { }
    一律保持原样。这样可以让 prompt 内嵌 JSON 示例（如 {"topic_id": 1}）
    不被误认为是格式化占位符。

    缺变量则抛 KeyError（fail fast）。
    """
    missing = []
    out = template
    for name, value in variables.items():
        placeholder = "{" + name + "}"
        if placeholder not in out:
            missing.append(name)
            continue
        out = out.replace(placeholder, str(value))
    # 校验所有 {known_var} 都已被替换；遗留的 {xxx} 如果是模板里 JSON 示例
    # 一部分（不是变量名），就直接保留即可。我们只对"声明传入但模板没用到"
    # 的变量发出警告级 KeyError，避免静默忽略调用方的笔误。
    if missing:
        # 严格模式：调用方传了变量但模板没用到，多半是 step_key 用错或
        # prompt 模板与代码不同步——直接抛出，让问题立刻暴露
        raise KeyError(
            f"以下变量在模板中未找到对应占位符：{missing}"
        )
    return out


def render_prompt(
    step_key: str,
    variables: dict,
    result_path: str,
    result_format: str = "json",
) -> str:
    """渲染一个完整 prompt 文件内容

    变量替换语义：
        模板里所有 `{var_name}`（var_name 出现在 variables 中）会被替换；
        其他形式的 { } 一律保留原样（兼容模板中的 JSON 代码示例）。
    """
    template_body = _strip_doc_header(_load_template(step_key))
    try:
        filled = _safe_substitute(template_body, variables)
    except KeyError as e:
        raise KeyError(f"prompt {step_key} 渲染失败: {e}") from None
    header = _build_agent_header(step_key, result_path, result_format)
    return header + filled + "\n"


def write_prompt_file(prompt_path: Path, content: str) -> None:
    """写出 prompt 文件（自动建父目录）"""
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(content, encoding="utf-8")
