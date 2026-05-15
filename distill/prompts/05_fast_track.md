# Prompt: 快速通道（Fast Track — 合并消解 + 筛选 + 标注 + 方案提炼）

> 针对 medium/low 价值 topic，一次性完成指代消解、解决方案提炼、代码筛选、分层标注。
> 完整设计见 `docs/design.md` 5.3 节。

---

你是对话知识提炼专家。对以下 AI 对话片段完成四步处理，并输出一个合并的 JSON 结果。

## 步骤 1：指代消解

重写对话，使其完全自描述。

消解规则：
- "这个/它/这块" → 替换为具体名称
- "上面那个方案" → 引用具体关键词
- "之前说的" → 找到具体内容补全
- 保留原始问答结构（user / assistant 交替）
- 保留代码块原文
- 若无法消解，保留原文并加 [ref-unresolved] 标记

## 步骤 2：解决方案 / 知识点提炼

在 dialogue_md 末尾追加结构化总结（如适用）：

如果对话包含"探索 → 解决问题"的过程，追加：
```
---
## 💡 解决方案摘要
**问题**：（一句话）
**根因**：（为什么）
**解决方案**：（最终方案）
**踩坑记录**：（失败的尝试）
```

如果用户向 AI 传授了项目/环境特有知识，追加：
```
---
## 📌 项目知识点
- （知识点 1）
- （知识点 2）
```

如果都不涉及，跳过此部分。

## 步骤 3：代码筛选

分析对话中的代码片段，按复用价值三级分类：
- **decision**（保留）：最终方案 / 关键决策的实现
- **educational**（保留 + 加注释）：API 用法示范 / 设计模式实现，需加 `// 关键点：xxx` 注释
- **process**（丢弃）：中间尝试 / 已否决草稿，仅在 discarded_summary 中描述

## 步骤 4：分层标注

判断 topic 归属层级：
- **project**：仅对单一代码库有效（含具体类名/字段名/业务规则）
- **domain**：跨项目但同业务领域有效（含业务概念/团队约定）
- **general**：完全通用（标准 API / 编程模式 / 工具技巧）

判定规则：
1. workspace 路径 → 候选 project
2. domain_mapping 检查 → 若属于某 domain 且无项目特有概念 → 提升为 domain
3. confidence < 0.6 → 兜底归 general

general_category 预定义类别：java / python / typescript / redis / mysql / debugging / ai-tools / git / shell / system-design / misc

## 输入

workspace: {workspace}
domain_mapping:
{domain_mapping_yaml}
topic_title: {topic_title}
topic_dialogue:
{topic_messages}

## 输出格式（必须是合法 JSON）

```json
{
  "dialogue_md": "**用户**：...\n\n**AI**：...\n\n---\n## 💡 解决方案摘要\n...",
  "coref_confidence": 0.85,
  "kept_snippets": [
    {
      "tier": "decision",
      "language": "java",
      "code": "实际代码...",
      "annotation": "最终方案描述",
      "source_msg_idx": 5
    }
  ],
  "discarded_summary": "丢弃了 N 段过程性代码：...",
  "filter_confidence": 0.8,
  "scope": "project",
  "project": "my-project",
  "domain": null,
  "general_category": null,
  "tags": ["redis", "rate-limit"],
  "scope_confidence": 0.9,
  "reasoning": "包含具体业务类名 XxxService"
}
```

## 约束

- dialogue_md 必须完整保留消解后的对话，不可省略
- 解决方案摘要和项目知识点追加在 dialogue_md 末尾（作为 Markdown 的一部分）
- educational 类代码必须加 `// 关键点：xxx` 注释
- process 类不输出代码，只在 discarded_summary 中描述
- tags 用 kebab-case 小写
- scope=general 时 project 和 domain 必须为 null
