---
name: ai-coding-memory.distill
version: 0.1.0
description: |
  Stage 2：把 collect 阶段的对话流清洗为"自描述、可复用"的 topic 块。

  ⚠️ 当前为 Phase 1 占位状态，实现于 Phase 2 完成。

  TRIGGER：用户说"清洗今日对话"、"distill today"、"提炼笔记"。
---

# distill 模块（占位）

## 职责（设计已定）

distill **不做知识抽取**，只做四个预处理步骤，让 llm-wiki crystallize 能高质量消化：

1. **主题切分** —— 把单个 session 切分成自洽的 topic 块
2. **指代消解** —— 把"这个/它/上面那个"替换为具体名称
3. **代码筛选** —— 三级分类（decision / educational / process）
4. **分层标注** —— 给每个 topic 打 project / domain / general 标签

## 输入

`~/.ai-memory/raw/sessions/YYYY-MM-DD.json`（collect 输出）

## 输出契约

`~/.ai-memory/raw/topics/YYYY-MM-DD/NNN-{scope}-{slug}.md`

每个 topic 文件含 frontmatter：
```yaml
---
type: distilled-topic
scope: project | domain | general
project: <project_name>
domain: <domain_name>
general_category: <category>
tags: [keyword-list]
quality:
  has_conclusion: true
  has_code: true
  estimated_value: high|medium|low
source_msg_range: [start_idx, end_idx]
---
```

## 完整设计

详见 `docs/design.md` 第 5 节。

## 当前状态

- ✅ 设计文档完成
- ✅ Prompt 模板已写入 `docs/design.md` 5.3 节
- ✅ 编排逻辑伪代码已写入 `docs/design.md` 5.4 节
- ⏳ Phase 2 实现：`prompts/01-04_*.md` + `scripts/distill.py` + `scripts/lib/*.py`
