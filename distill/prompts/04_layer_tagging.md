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

【bug_category 标注（仅当 topic 的 knowledge_type=bugfix 时填写）】
如果该 topic 是一个 bug 修复过程，请判定 bug 的根因类别：
- **concurrency**: 并发/竞态条件/死锁/线程安全
- **serialization**: 序列化/反序列化/编解码问题
- **config**: 配置缺失/配置错误/环境差异
- **null-handling**: 空指针/空值/Optional 处理
- **type-mismatch**: 类型不匹配/类型转换失败
- **api-misuse**: API 误用/参数传错/版本不兼容
- **performance**: 性能问题/内存泄漏/慢查询
- **logic-error**: 业务逻辑错误/边界条件遗漏
- **dependency**: 依赖冲突/版本冲突/classpath 问题
- **network**: 网络超时/连接失败/DNS 解析
- **other**: 不属于以上任何类别

【输出格式（必须是合法 JSON）】
```json
{
  "scope": "project|domain|general",
  "project": "winterfell",
  "domain": null,
  "general_category": null,
  "tags": ["rate-limit", "redis"],
  "bug_category": null,
  "confidence": 0.9,
  "reasoning": "包含 winterfell 的具体业务实体 OfferModel"
}
```

【约束】
- general_category 必须是预定义类别之一：java / python / typescript / redis / mysql / debugging / ai-tools / git / shell / system-design / 其他归为 misc
- tags 用小写连字符（kebab-case）
- 当 scope=general 时，project 和 domain 必须为 null
- bug_category：仅当该 topic 的 knowledge_type=bugfix 时填写，否则为 `null`
