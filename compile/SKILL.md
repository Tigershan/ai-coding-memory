---
name: ai-coding-memory.compile
version: 0.1.0
description: |
  Stage 3：把 distill 输出的 topic 文件批量喂给 llm-wiki-skill 的 crystallize 流程。

  ⚠️ 当前为 Phase 1 占位状态，实现于 Phase 3 完成。

  TRIGGER：用户说"入库今日 topics"、"compile today"、"刷新知识库"。
---

# compile 模块（占位）

## 职责（设计已定）

1. 遍历 `~/.ai-memory/raw/topics/YYYY-MM-DD/` 下所有 topic 文件
2. 解析每个 topic 的 frontmatter `scope` 字段
3. 把 topic 分发到对应子知识库目录：
   - `scope=project`  → `wiki/projects/<project>/`
   - `scope=domain`   → `wiki/domains/<domain>/`
   - `scope=general`  → `wiki/general/<category>/`
4. 在每个子知识库内调用 fork 的 `llm-wiki-skill/scripts/crystallize.sh`

## 子模块结构

```
compile/
├── SKILL.md
├── llm-wiki-skill/        # 我们 fork 的 git submodule
│                          # （Phase 1 阶段先用上游占位，等用户 fork 后切换 UR