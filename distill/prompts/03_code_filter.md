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

🔴 **process（过程性，丢弃）**
满足以下任一：
- 中间尝试方案（已被否决）
- AI 给出的草稿后被纠正
- 长度 > 50 行的实现细节（强制压缩为摘要）

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
      "source_msg_idx": 18
    },
    {
      "tier": "educational",
      "language": "java",
      "code": "// 关键点：复用 sha 避免每次重新加载脚本\nString sha = redisConnection.scriptLoad(luaScript);\nredisConnection.evalSha(sha, ...);",
      "annotation": "Redis evalSha 标准用法",
      "source_msg_idx": 14
    }
  ],
  "discarded_summary": "丢弃了 2 段过程性代码：（1）初版同步限流方案（被并发问题推翻）；（2）AI 草稿，使用 Guava RateLimiter 但不支持分布式",
  "filter_confidence": 0.9
}
```

【约束】
- educational 类必须为代码加 `// 关键点：xxx` 注释
- process 类不输出代码，只在 discarded_summary 中描述"做了什么 + 关键 API"
- 若所有代码都是 process 类，kept_snippets 为空数组
