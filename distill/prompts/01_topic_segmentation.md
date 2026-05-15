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
- high: 满足以下任一：
  - 包含明确的技术决策（如"为什么选 A 方案不选 B"）
  - 解决了真实 bug 并找到了根因
  - 用户从 AI 学到了新知识（新 API、新模式、新工具用法）
  - 用户向 AI 提供了项目/环境特有知识（如预发域名、本地启动命令、部署约定、跳过某插件、特殊配置项、团队约定的命名规则等）——这类信息是最有价值的 project-level 记忆
- medium: 完成了具体编码任务，但未沉淀方法论
- low: 简单问答 / 文档查询 / 重复劳动
- noise: 闲聊 / 工具调试 / 无价值内容（直接丢弃）

特别注意：用户以祈使句/声明句告诉 AI 的项目事实（"预发域名是 xxx"、"本地启动需要跳过 xx 插件"、"我们用的是 xx 分支策略"）虽然对话轮次可能很短，但对于长期记忆来说价值极高，应评为 high。

【输入】
workspace: {workspace}
session_started_at: {session_start_time}
conversation:
{messages_with_index}

【knowledge_type 分类标准】
对每个 topic 判定其主要知识类型（单选）：
- **decision**: 包含技术方案选择、架构决策、"为什么用 A 不用 B"的讨论
- **bugfix**: 排查并解决了 bug，包含从症状到根因的分析过程
- **tribal_knowledge**: 用户提供了项目/环境/团队特有的事实（预发域名、部署约定、配置项等）
- **new_learning**: 用户或 AI 发现了新的 API 用法、工具技巧、编程模式
- **implementation**: 完成了具体编码任务，无明显决策或学习要素
- **qa**: 简单问答、文档查询

【user_correction 检测】
扫描对话，检测用户纠正 AI 的瞬间。特征信号：
- 用户说"不对"、"错了"、"不是这样"、"应该是..."、"你搞混了"、"这个不行"等否定 + 纠正
- 用户提供了 AI 不知道的项目事实来纠正 AI 的假设
- 用户指出 AI 给出的方案存在实际不可行的原因

如果检测到纠正，在 corrections 数组中记录。每条纠正是一个高价值知识点。

【decision_rationale 提取】
当 knowledge_type=decision 时，必须提取决策推理链：
- 考虑过哪些备选方案
- 最终选了什么，为什么
- 被否决的方案为什么不行

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
    "knowledge_type": "decision|bugfix|tribal_knowledge|new_learning|implementation|qa",
    "corrections": [
      {
        "msg_idx": 5,
        "what_ai_got_wrong": "AI 假设用 Guava RateLimiter",
        "correct_answer": "分布式场景必须用 Redisson，Guava 只支持单机",
        "value_note": "项目限流必须走 Redisson 分布式方案"
      }
    ],
    "decision_rationale": {
      "alternatives_considered": ["Guava RateLimiter", "Redisson RRateLimiter", "自研令牌桶"],
      "chosen": "Redisson RRateLimiter",
      "why_chosen": "支持分布式 + 集群模式下自动续期",
      "why_others_rejected": "Guava 仅单机；自研维护成本高"
    },
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
- corrections 数组：无纠正时为空数组 `[]`，不要省略该字段
- decision_rationale：仅当 knowledge_type=decision 时填写，其他类型填 `null`
- knowledge_type 必填，不能省略
