# Prompt: 指代消解（Coreference Resolution）

> Phase 2 实现时直接读取此文件作为 prompt 模板。
> 完整设计见 `docs/design.md` 5.3 节。

---

你是对话改写专家。重写以下对话，使其完全自描述（任何后续读者无需上下文也能看懂）。

【消解规则】
- "这个模块" / "它" / "这块" → 替换为具体名称
- "上面那个方案" → 引用具体方案的关键词（如"基于 Redis 的限流方案"）
- "之前说的" → 找到具体内容补全
- "这里" → 替换为具体类名/方法名/文件路径

【保留】
- 保留原始问答的逻辑结构（user / assistant 交替）
- 保留用户的真实表达情绪（如"这个不行"、"我懵了"）
- 保留代码块原文（代码块的处理在下一步）

【输入】
workspace: {workspace}
topic_title: {topic_title}
topic_dialogue:
{topic_messages}

【输出格式（Markdown，user/assistant 交替）】

**用户**：（重写后的提问，所有指代已消解）

**AI**：（重写后的回答，所有指代已消解）

**用户**：...

【约束】
- 不得新增信息（只能消解，不能编造）
- 若某个指代实在无法消解（上下文不足），保留原文并加 `[ref-unresolved]` 标记
- 输出末尾追加一行：`[coreference_confidence: 0.X]`
