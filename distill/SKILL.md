---
name: ai-coding-memory.distill
version: 0.2.0
description: |
  Stage 2：把 collect 阶段的对话流清洗为「自描述、可复用」的 topic 块。
  采用 Agent 编排模式：脚本只生成提示词与状态机，由宿主 Agent（你）逐步消化，
  把 LLM 输出回写到任务包文件。

  TRIGGER：用户说「清洗今日对话」「distill today」「提炼笔记」「跑一下 distill」
           「继续昨天的 distill」「整理今天的 AI 编码记忆」时。
---

# distill 模块 — Agent 操作手册

## 你（Agent）在 distill 流水线中的角色

distill 流水线把每天的多轮对话经过 4 个 step 转化为分层的 topic 文件：

| Step | 名称 | 输入 → 输出格式 |
|------|------|----------|
| 1 | 主题切分 topic_segmentation | session → JSON 数组 |
| 2 | 指代消解 coreference         | topic 对话 → Markdown |
| 3 | 代码筛选 code_filter          | step2 dialogue → JSON 对象 |
| 4 | 分层标注 layer_tagging        | step2 + step3 → JSON 对象 |

**`distill.py` 不直接调用 LLM**。它生成「任务包」（prompt 文件 + manifest.json），
然后让你按本手册逐个消化。

---

## 标准工作流（按顺序执行）

### Step 0 ：前置检查

确保 collect 阶段已经跑过：

```bash
ls ~/.ai-memory/raw/sessions/$(date +%Y-%m-%d).json
```

不存在则先跑：
```bash
python3 collect/scripts/extract_sessions.py --range today --verbose
```

### Step 1 ：plan（生成 step1 任务包）

```bash
python3 distill/scripts/distill.py plan --date today --verbose
```

它会创建：
```
~/.ai-memory/raw/distill-tasks/YYYY-MM-DD/
  manifest.json                # 任务清单（status=pending）
  step1-segment/
    session-{ide}-NNN-*.prompt.md
```

### Step 2 ：消化 step1 任务（你的工作）

读取 manifest，找到所有 `step="topic_segmentation"` 且 `status="pending"` 的任务，
**逐个执行**：

1. `read_file` 任务的 `prompt_file`
2. 把 prompt **正文**（去掉顶部「执行说明」blockquote）作为 LLM 输入，调用你自己的 LLM
3. **写出结果文件**到 `result_file`：必须是合法 JSON（`[]` 数组），不要 ``` 包裹
4. 更新 manifest.json：把对应任务的 `status` 改为 `completed`；失败则改为 `failed` 并写入 `error` 字段
5. 用 `file_replace` 修改 manifest.json 的状态字段（**不要重写整个文件**，避免破坏其他任务）

#### 样例 result.json（step1）
```json
[
  {
    "topic_id": 1,
    "title": "限流方案选型",
    "start_msg_idx": 0,
    "end_msg_idx": 12,
    "summary": "对比 Guava 和 Redis 限流",
    "estimated_value": "high",
    "confidence": 0.9,
    "reasoning": "包含明确技术决策"
  }
]
```

### Step 3 ：expand（让脚本展开 step2 任务）

```bash
python3 distill/scripts/distill.py expand --date today --verbose
```

`expand` 是**幂等的**：它扫描所有 `status=completed` 的前序 step，按需生成下游任务。
此时 `step2-coref/` 里会出现 `topic-*.prompt.md`。

### Step 4 ：消化 step2 任务（指代消解）

同 Step 2 的流程，但注意：
- result 文件后缀是 **`.result.md`**（Markdown，不是 JSON）
- 输出末尾必须保留 `[coreference_confidence: 0.X]` 一行
- 不要用 ``` 包裹整个 Markdown

### Step 5 ：再 expand → 消化 step3 → 再 expand → 消化 step4

```bash
python3 distill/scripts/distill.py expand --date today
# 消化 step3-code/ 下的 .prompt.md → 写 .result.json

python3 distill/scripts/distill.py expand --date today
# 消化 step4-layer/ 下的 .prompt.md → 写 .result.json
```

中途随时可查进度：
```bash
python3 distill/scripts/distill.py status --date today --verbose
```

### Step 6 ：assemble（合并为最终 topic .md）

```bash
python3 distill/scripts/distill.py assemble --date today --verbose
```

输出：`~/.ai-memory/raw/topics/YYYY-MM-DD/NNN-{scope}-{slug}.md`，已含完整 frontmatter。

---

## 任务状态机

每个任务在 `manifest.json` 里的 `status` 取值：

| status | 含义 | 触发动作 |
|---|---|---|
| `pending`   | 待消化  | 你应该处理它 |
| `completed` | 已完成  | 下一次 expand 会展开其下游 |
| `failed`    | 失败    | 下一次 expand 会跳过其下游；你可修复后改回 pending 重试 |

## 错误恢复速查

| 现象 | 处理 |
|---|---|
| `manifest 不存在` | 先跑 `plan` |
| `sessions.json 不存在` | 先跑 collect |
| `plan` 报告 0 sessions | 当天没有 AI 对话，正常退出，不需要 distill |
| 某个 result 解析失败 | 检查 LLM 输出是否有 ``` 包裹/解释文字；删掉重新生成或直接修 result 文件 |
| `assemble` 报 `topic_meta 丢失` | 该 step4 task 是历史 manifest 残留，可手动从 manifest 删除该 task |
| 想重跑某天 | 删除 `~/.ai-memory/raw/distill-tasks/YYYY-MM-DD/` 后重新 plan |

---

## 输出契约（最终 topic .md）

文件位置：`~/.ai-memory/raw/topics/YYYY-MM-DD/NNN-{scope}-{slug}.md`

frontmatter 字段（详见 `docs/design.md` §5.2）：

```yaml
---
type: distilled-topic
date: 2026-04-25
session_id: <id>
ide: aone-copilot | cursor | qoder
workspace: /abs/path
scope: project | domain | general
project: <name> | null
domain: <name> | null
general_category: java|python|...|misc | null
tags: [kebab-case, ...]
quality:
  has_conclusion: true
  has_code: true
  estimated_value: high|medium|low
source_msg_range: [start_idx, end_idx]
---
```

正文结构：标题 → 已消解对话 → 关键代码（按 tier 分组）→ 已丢弃过程性代码摘要
→ distill 元信息（confidence + 分层判定理由）。

---

## 不要做的事

- ❌ 不要直接编辑 `~/.ai-memory/raw/sessions/*.json`（collect 的输入快照）
- ❌ 不要绕过 manifest 直接生成 topic .md（会丢失分层 / 代码筛选信息）
- ❌ result 文件不要带 markdown 代码围栏 ``` 包裹整体
- ❌ 不要把 4 个 step 合并成一个大 prompt 一次性喂给 LLM；分步是为了在每步都让你校验输出质量

---

## 完整设计参考

- 设计蓝图：`docs/design.md` §5
- prompt 原文：`distill/prompts/01_*.md ~ 04_*.md`
- 主入口源码：`distill/scripts/distill.py`
- 任务调度逻辑：`distill/scripts/lib/task_builder.py`
