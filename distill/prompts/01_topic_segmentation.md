# Prompt: 主题切分（Topic Segmentation）

> Phase 2 实现时直接读取此文件作为 prompt 模板。
> 完整设计见 `docs/design.md` 5.3 节。

---

你是对话分析专家。请分析以下编码对话，识别"话题切换点"，把单个 session 切分为若干自洽的 topic 块。

【判定切分的标准】
- 用户问题转向了完全不同的技术领域 → 切分
- 用户开始处理新文件/新模块 → 切分
- 时间间隔 > 30 分钟且话题无关联 → 切分
- 长度超过 8 轮但话题连贯 → 不切分

【estimated_value 评级标准】
- high: 包含明确的技术决策 / 解决了真实 bug / 学到新知识
- medium: 完成了具体编码任务，但未沉淀方法论
- low: 简单问答 / 文档查询 / 重复劳动
- noise: 闲聊 / 工具调试 / 无价值内容（直接丢弃）

【输入】
workspace: {workspace}
session_started_at: {session_start_time}
conversation:
{messages_with_index}

【输出格式（必须是合法 JSON 数组）】
```json
[
  {
    "topic_id": 1,
    "title": "≤ 20 字的话题概括",
    "start_msg_idx": 0,
    "end_msg_idx": 12,
    "summary": "一句话概括（≤ 50 字）",
    "estimated_value": "high|medium|low|noise",
    "confidence": 0.85,
    "reasoning": "切分理由"
  }
]
```

【约束】
- topic_id 从 1 开始连续递增
- start_msg_idx / end_msg_idx 必须覆盖所有消息（不重叠、不遗漏）
- 单个 session 至少 1 个 topic
- 所有 estimated_value=noise 的 topic 在后续步骤会被直接丢弃
