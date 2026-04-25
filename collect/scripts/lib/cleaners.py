"""内容清洗与智能截断

负责把原始对话过滤成"高信号密度"的精简版本：
- 去除工具结果/base64/超长代码块等噪声
- 识别并剔除闲聊
- 智能截断：优先保留首尾轮次和含技术关键词的轮次

输入/输出：list[{"role", "content"}] → list[{"role", "content"}]
"""

import re

# ==== 配置常量 ====
MAX_CHARS_PER_SESSION: int = 15000
MAPREDUCE_THRESHOLD: int = 80000

# ==== 噪音清洗正则（编译后缓存）====
_CLEAN_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 工具结果保留 ≤5KB；超长才省略，避免丢失关键错误日志
    (re.compile(r"<tool_result>[\s\S]{5000,}?</tool_result>", re.DOTALL),
     "[工具执行结果已省略]"),
    (re.compile(r"<tool_output>[\s\S]{5000,}?</tool_output>", re.DOTALL),
     "[工具执行结果已省略]"),
    # base64 图片
    (re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+"),
     "[图片数据已省略]"),
    # 超长代码块
    (re.compile(r"```[\s\S]{5000,}?```"),
     "[长代码块已省略]"),
    # URL 编码数据
    (re.compile(r"(%[0-9A-Fa-f]{2}){50,}"),
     "[编码数据已省略]"),
]

# ==== 闲聊识别 ====
_TRIVIAL_PATTERN: re.Pattern = re.compile(
    r"^(好的|ok|嗯|继续|是的|对|明白|收到|谢谢|thanks|got it|sure|yes|no|"
    r"好|行|可以|没问题|了解|知道了|看看|试试|帮我|请|嗯嗯|okay)[\s。！!.\?\?]*$",
    re.IGNORECASE,
)

# ==== 技术关键词（用于判断核心交互价值）====
TECH_KEYWORDS: list[str] = [
    "```", "error", "Error", "ERROR", "Exception", "exception",
    "failed", "Failed", "FAILED", "报错", "问题", "修改", "添加",
    "创建", "删除", "重构", "优化", "bug", "Bug", "BUG", "fix",
    "Fix", "实现", "开发", "部署", "配置", "接口", "数据库",
    "import", "class ", "def ", "function", "return",
    "API", "api", "请求", "响应", "参数", "返回值",
    "测试", "test", "Test", "调试", "debug", "Debug",
    "性能", "缓存", "cache", "Cache",
    "安全", "security", "权限", "认证", "授权",
    "日志", "log", "监控", "monitor",
    "依赖", "dependency", "版本", "version",
    "架构", "设计", "模式", "pattern",
    "异常", "处理", "handler", "回调", "callback",
    "异步", "async", "同步", "sync",
    "事务", "transaction", "锁", "lock",
    "队列", "queue", "消息", "message",
    "序列化", "serialize", "解析", "parse",
    "查询", "query", "更新", "update", "插入", "insert",
    "重试", "retry", "超时", "timeout",
    "上传", "upload", "下载", "download",
    "迁移", "migrate", "升级", "upgrade",
    "兼容", "compatible", "适配", "adapt",
    "插件", "plugin", "钩子", "hook", "事件", "event",
    "发布", "publish", "订阅", "subscribe",
    "告警", "alert", "崩溃", "crash", "死锁", "deadlock",
]


def clean_content(text: str) -> str:
    """执行噪音清洗"""
    if not text:
        return ""
    for pattern, replacement in _CLEAN_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_core_interaction(user_msg: str) -> bool:
    """判断一轮 user 消息是否为核心交互（非闲聊）"""
    stripped = user_msg.strip()
    if not stripped:
        return False
    if _TRIVIAL_PATTERN.match(stripped):
        return False
    if len(stripped) < 50 and not any(kw in stripped for kw in TECH_KEYWORDS):
        return False
    return True


def smart_truncate(
    conversation: list[dict],
    max_chars: int = MAX_CHARS_PER_SESSION,
) -> list[dict]:
    """智能截断对话内容

    策略：按重要性打分（首尾轮次+技术关键词），按分数从高到低累加，超出预算则停。
    """
    if not conversation:
        return []

    scored_rounds = []
    for idx, msg in enumerate(conversation):
        content = msg.get("content", "")
        char_count = len(content)
        importance = sum(1 for kw in TECH_KEYWORDS if kw in content)
        # 首尾轮次显著提权
        if idx == 0 or idx == len(conversation) - 1:
            importance += 100
        scored_rounds.append((idx, msg, char_count, importance))

    scored_rounds.sort(key=lambda item: item[3], reverse=True)

    selected_indices = set()
    current_chars = 0
    for idx, msg, char_count, _importance in scored_rounds:
        if current_chars + char_count > max_chars:
            remaining = max_chars - current_chars
            if remaining > 200:
                truncated_msg = {
                    "role": msg["role"],
                    "content": msg["content"][:remaining] + "...[已截断]",
                }
                conversation[idx] = truncated_msg
                selected_indices.add(idx)
                current_chars += remaining
            break
        selected_indices.add(idx)
        current_chars += char_count

    return [msg for idx, msg in enumerate(conversation) if idx in selected_indices]


def filter_and_clean_conversation(conversation: list[dict]) -> list[dict]:
    """完整清洗流水线：噪声清洗 → 闲聊过滤 → 智能截断"""
    # 第一步：内容噪声清洗
    cleaned = []
    for msg in conversation:
        cleaned_content = clean_content(msg.get("content", ""))
        if cleaned_content:
            cleaned.append({
                "role": msg.get("role", "unknown"),
                "content": cleaned_content,
            })

    # 第二步：闲聊过滤（保留核心交互的 user+assistant 对）
    filtered = []
    idx = 0
    while idx < len(cleaned):
        msg = cleaned[idx]
        if msg["role"] == "user":
            if is_core_interaction(msg["content"]):
                filtered.append(msg)
                if idx + 1 < len(cleaned) and cleaned[idx + 1]["role"] == "assistant":
                    filtered.append(cleaned[idx + 1])
                    idx += 2
                    continue
            # 非核心 user → 跳过其后的 assistant
            if idx + 1 < len(cleaned) and cleaned[idx + 1]["role"] == "assistant":
                idx += 2
                continue
        else:
            # 兜底：长 assistant 消息（无对应 user）也保留
            if len(msg["content"]) > 100:
                filtered.append(msg)
        idx += 1

    # 第三步：智能截断
    return smart_truncate(filtered)
