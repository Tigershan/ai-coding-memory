# Prompt: 分层标注（Layer Tagging）

> Phase 2 实现时直接读取此文件作为 prompt 模板。
> 完整设计见 `docs/design.md` 5.3 节。

---

你是知识分类专家。判断以下 topic 的归属层级（project / domain / general）。

【三层定义】
- **project**: 仅对单一代码库有效
  - 含具体类名 / 字段名 / 业务规则 / 项目内部约定
  - 例：winterfell 的 OfferModel 字段映射
- **domain**: 跨项目但同业务领域有效
  - 含业务概念 / 团队约定 / 多个项目共享的设计模式
  - 例：导购搜索的排序公式（多个项目共用）
- **general**: 完全通用，跨项目跨领域
  - 标准 API 用法 / 编程模式 / 工具技巧
  - 例：Redis evalSha 用法、Java Stream 性能陷阱

【判定规则（优先级递减）】
1. workspace 路径自动判定 → 候选 project（project_name = basename(workspace)）
2. 检查 domain_mapping 表 → 若 project 属于某个 domain 且内容不含项目特有概念 → 提升为 domain
3. 内容关键判断：
   - 含具体业务实体类名 → project
   - 仅含框架/语言 API → general
   - 含业务领域概念但无具体类名 → domain
4. confidence < 0.6 → 兜底归 general（更安全：宁可放宽召回范围，不要锁死在错误项目）

【输入】
workspace: {workspace}
domain_mapping:
{domain_mapping_yaml}
topic_content:
{topic_md_full}

【输出格式（必须是合法 JSON）】
```json
{
  "scope": "project|domain|general",
  "project": "winterfell",
  "domain": null,
  "general_category": null,
  "tags": ["rate-limit", "redis"],
  "confidence": 0.9,
  "reasoning": "包含 winterfell 的具体业务实体 OfferModel"
}
```

【约束】
- general_category 必须是预定义类别之一：java / python / typescript / redis / mysql / debugging / ai-tools / git / shell / system-design / 其他归为 misc
- tags 用小写连字符（kebab-case）
- 当 scope=general 时，project 和 domain 必须为 null
