# Prompt: 代码筛选（Code Filtering）

> Phase 2 实现时直接读取此文件作为 prompt 模板。
> 完整设计见 `docs/design.md` 5.3 节。

---

你是代码筛选专家。分析对话中的所有代码片段，按"未来可复用价值"做三级分类。

【三级分类标准】

🟢 **decision（决策性，保留全文）**
满足以下任一：
- 是最终采用的方案代码
- 体现了关键技术决策（如"为什么用 A 不用 B"的实现）
- 是修复 bug 的最终方案
- 长度 < 50 行

🟡 **educational（教学性，保留 + 加注释）**
满足以下任一：
- API 用法示范（标准库 / 框架的典型用法）
- 设计模式的具体实现示例
- 长度 < 30 行
- 必须为代码添加 `// 关键点：xxx` 注释，说明该片段的"教学价值"

🔴 **process（过程性，丢弃但保留失败教训）**
满足以下任一：
- 中间尝试方案（已被否决）
- AI 给出的草稿后被纠正
- 长度 > 50 行的实现细节（强制压缩为摘要）

**重要**：被丢弃的 process 代码中，如果存在"尝试了但失败"的方案，必须在 `discarded_summary` 中记录**失败的原因**（不只是"做了什么"，还要说"为什么不行"）。这些失败教训本身就是高价值知识。

【输入】
topic_title: {topic_title}
topic_dialogue: {dialogue_with_code_blocks}

【输出格式（必须是合法 JSON）】
```json
{
  "kept_snippets": [
    {
      "tier": "decision",
      "language": "java",
      "code": "RateLimiter limiter = ...",
      "annotation": "最终采用：基于 Redisson 的分布式限流",
      "reusable_pattern": "distributed-rate-limiting",
      "source_msg_idx": 18
    },
    {
      "tier": "educational",
      "language": "java",
      "code": "// 关键点：复用 sha 避免每次重新加载脚本\nString sha = redisConnection.scriptLoad(luaScript);\nredisConnection.evalSha(sha, ...);",
      "annotation": "Redis evalSha 标准用法",
      "reusable_pattern": "redis-eval-sha",
      "source_msg_idx": 14
    }
  ],
  "discarded_summary": "丢弃了 2 段过程性代码：（1）初版同步限流方案 → 失败原因：并发场景下无法保证原子性；（2）Guava RateLimiter 方案 → 失败原因：只支持单机，分布式部署下各节点限流不互通",
  "filter_confidence": 0.9
}
```

【约束】
- educational 类必须为代码加 `// 关键点：xxx` 注释
- process 类不输出代码，在 discarded_summary 中描述"做了什么 + 关键 API + 为什么失败/被放弃"
- discarded_summary 格式示例：`（1）方案名 → 失败原因：xxx`，确保每个被丢弃方案都有失败/放弃原因
- 若所有代码都是 process 类，kept_snippets 为空数组
- reusable_pattern：用 kebab-case 标识该代码片段的可复用模式（如 `distributed-rate-limiting`、`redis-eval-sha`、`retry-with-backoff`），便于后续按 pattern 类型检索。如果代码不构成通用 pattern 则填 `null`
