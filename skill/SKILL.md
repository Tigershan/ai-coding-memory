---
name: ai-coding-memory
description: |
  跨 coding agent 的个人/项目 memory 工具：从 Cursor / Claude Code / Aone Copilot / Qoder 沉淀对话 → 蒸馏成可召回的 markdown → 任意 IDE 跨界召回。

  **此 Skill 处理两类场景**：

  1) **首次安装后的 onboarding 验证**（30 秒走通全链路）—— TRIGGER：用户说
     「我刚装了 ai-coding-memory」「测试 ai-memory」「ai-memory 怎么用」
     「验证 ai-memory」「memory 工具能用吗」时

  2) **批量消化 host_agent 模式的待蒸馏任务包**（last-7d/30d init 后产出的）—— TRIGGER：用户说
     「整理今日记忆」「消化任务包」「跑一遍 memory pipeline」「沉淀今天的对话」
     「distill 今日笔记」「批量整理记忆」「pending 任务」「整理 ai-memory」时

  **不通过此 Skill 触发**：
  - 日常 coding 时的「自动召回历史经验」由 ai-coding-memory MCP Server 直接对接 IDE
  - 用户单条主动 `remember` 也通过 MCP 工具直接落盘，无需 Skill 编排
  - 项目摘要 `project_context` 也是 MCP 工具，IDE 启动时自动调
---

# AI Coding Memory · Skill 操作手册

跨 coding agent 的个人 / 项目 memory：你（IDE 里的 agent）作为消化器和验证器，配合后端的 MCP server + CLI 把 7 天累积的对话沉淀成可召回的 markdown。

---

## 你（Agent）的两种角色

根据用户触发的语义，进入下面两条工作流之一。两条流程**互不嵌套**，跑完一条就停。

### 角色 A：Onboarding 验证者（用户刚装完）

目标：**30 秒内确认全链路通**，让用户看到一次完整的 write→search 闭环。

### 角色 B：批量消化器（用户说"整理今日记忆"等）

目标：批量消化 `.pending/` 里的任务包。**默认走 batch_mode**（host_agent 时按 ~10/批 + 询问继续；local 时可一次跑完不分批）。

---

## 流程 A：Onboarding 验证

> 触发示例："我刚装了 ai-coding-memory" / "测试一下 memory" / "ai-memory 怎么用"

### A.1 检查 MCP 连通性

调用：

```
mcp__ai-coding-memory__pending_distill_count
```

- 返回 "暂无待整理任务" 或 "📥 有 N 个待整理任务" → MCP 通畅 ✓
- 返回错误或工具不存在 → MCP 没装好，建议用户：
  - 检查 `~/.claude.json` 是否有 `mcpServers.ai-coding-memory` 字段
  - 重启 IDE
  - 重跑 `./install.sh`

### A.2 验证写入

调一次：

```
mcp__ai-coding-memory__remember(
  text="ai-coding-memory onboarding 测试 — 装好了",
  scope="personal",
  tags=["test", "onboarding"],
  workspace=<当前 workspace 绝对路径>,
  value="low"
)
```

返回应有 `✓ 已记住` + 文件路径。把路径拿给用户看。

### A.3 验证召回

紧接着调：

```
mcp__ai-coding-memory__search_memory(
  query="onboarding",
  scope="auto",
  workspace=<当前 workspace>
)
```

应该召回到刚才写的那条。**这是 aha moment**——告诉用户："看，刚才记的 X 已经在 personal/ 了，下次切到 Cursor / Aone 都能搜到。"

### A.4 报告状态 + 推荐下一步

根据 A.1 的 pending 数报告：

| 状态 | 推荐 |
|---|---|
| pending=0，库 < 5 条 | "记忆库还很空。要不要让 init 回溯最近 7 天对话？跑 `ai-memory init --range last-7d`" |
| pending > 0 | "你有 N 个待消化任务包。需要现在批量消化吗？说『整理今日记忆』我来跑（流程 B）。" |
| 库 ≥ 5 条 | "已经有 X 条 memory 在用了。日常说『记住 X』就能继续添加；问相关问题时我会自动召回。" |

### A.5 (可选) 清理测试数据

如果用户表示完成，问要不要 `forget` 掉测试条目。要就调：
```
mcp__ai-coding-memory__forget(memory_id=<A.2 返回的 id>)
```

**到此 onboarding 结束**——绝不要进入流程 B。

---

## 流程 B：批量消化任务包

> 触发示例："整理今日记忆" / "消化任务包" / "跑一遍 memory pipeline"

### B.0 检查 pending 数

```
mcp__ai-coding-memory__pending_distill_count
```

- "暂无待整理任务" → 直接告诉用户"没有任务包待整理"，结束。
- "📥 有 N 个" → 进入循环。

### B.1 循环消化（**节奏取决于 batch_mode**）

先确认当前 `llm.batch_mode`（用户在 install 时已选；CLI: `ai-memory config get llm.batch_mode`）：

- **batch_mode=local**（推荐，Ollama 本地推理）：单次 30-50s，0 现金 / 0 IDE 配额。
  可一次性跑到 `pending=0`，每 10 条汇报一次进度即可（不必停下询问）。
  N 条耗时 ≈ N × 40s；20 条 ≈ 13 分钟；可后台 nohup 跑。
- **batch_mode=host_agent**（fallback）：单次 3-5s，但占用宿主 IDE 实时 LLM 配额。
  必须分批：默认每 10 条停下问"继续吗？还剩 M"，避免爆 chat 上下文 + 配额。
- **batch_mode=api**（用户配了 key）：3-10s/条 + 真金白银。按用户偏好走（默认每 10 条问一次）。

每次循环的核心步骤：

```
batch_mode == "local":
    BATCH_SIZE = pending_count  (一次跑完，每 10 条 echo 进度即可)
batch_mode == "host_agent" / "api":
    BATCH_SIZE = 10  (跑完询问"继续吗")

for i in range(BATCH_SIZE):
    1. resp = mcp__ai-coding-memory__get_next_distill_task()
       若 resp 含 "暂无待整理任务" → break
       host_agent 模式下若 resp 含 "今日额度已用尽" → break（不要传 force=True）
       否则解析出 TASK_ID 和 PROMPT_START..PROMPT_END 之间的 prompt

    2. 用 batch_mode 对应的 LLM 跑这段 prompt，产出 YAML 结果
       - batch_mode=local：宿主 agent 通过 MCP 调用本地 Ollama；但当前 skill 设计是
         "agent 自跑 prompt"——也就是说，仍然由你（agent，使用你的 LLM）跑。
         若用户希望走真正的本地 Ollama，建议直接 `ai-memory init`/`distill`（命令行
         调 LocalProvider，绕过 skill 编排）。
       - batch_mode=host_agent：你（agent）自跑（即 IDE 自带 LLM）
       YAML schema：外层只允许一个 `topics:` 数组。要点：
         - body 用 `body: |` 块字符串
         - scope=project 仅当 prompt 中 project_key 不为 null
         - 没价值的 topic 写 `should_keep: false`（会被丢弃，不入库）

    3. mcp__ai-coding-memory__submit_distill_result(task_id=TASK_ID, result_yaml=<你的 YAML>)
       - written 非空 = 成功落盘到 personal/ 或 projects/
       - dropped 非空 = LLM 自判低价值丢弃（不入库）
       - errors 非空 = YAML 格式问题 → 任务包已自动转为 .task.failed，不要重试本批

    4. 累计 (id / title / value)，每 10 条 echo 一次进度："已消化 N/total"
```

### B.2 汇总报告

一批跑完后输出表格：

```
✓ 已消化 5 条任务包：
  | # | id-prefix | title | value | scope |
  |---|---|---|---|---|
  ...
  
还剩 M 个未消化。继续吗？(y/N)
```

用户说继续 → 再跑一批。说停 / N / 没说 → 结束。

### B.3 失败处理

- 单个 task submit 报 errors 时：**不要**重试，把它当成已经失败（已被工具自动移到 `.task.failed`），继续下一个
- 触发"今日额度已用尽"返回 → 停下报告："今日量已满（X/cap），明天会自动继续。如要强制跑，传 `force=true`"——**默认不要**强制
- 如果一批 5 个里有 ≥ 3 个 errors，停下汇报："连续失败多，可能是 prompt 格式有问题。要继续还是先看 logs？"

---

## 不要做的事

- ❌ 不要在 onboarding 流程 A 里进入流程 B（A 末尾不主动开始消化）
- ❌ 不要在流程 B 里 `while pending > 0` 无限循环（爆 context + 烧配额）
- ❌ 不要直接编辑 `~/.ai-memory/raw/sessions/*.json`（只读快照）
- ❌ 不要直接 mv / rm `.pending/` 里的文件（用 MCP 工具）
- ❌ 不要把 prompt（PROMPT_START..PROMPT_END 之间的内容）原文发给用户
- ❌ 不要在每个 task 之间长篇汇报中间过程；保持紧凑批处理 + 一次性表格汇总

---

## 错误恢复速查

| 现象 | 处理 |
|---|---|
| MCP 工具不存在 | 用户没重启 IDE 或 install 时 MCP 注入失败；检查 `~/.claude.json` mcpServers |
| `pending_distill_count` 永远是 0 但用户期望有 | 用户没跑过 `ai-memory init`；建议 `ai-memory init --range last-7d` |
| `submit_distill_result` 报 "topics 字段缺失" | LLM 输出没用 YAML 格式或被 ``` 围栏破坏；下次注意输出原生 YAML 不加围栏 |
| `submit_distill_result` 报 "protected: 拒绝覆盖" | 同 ID memory 已被用户手编辑（source=manual/edited），不能覆盖；让 LLM 改个 title 让 ID 不冲突 |
| 显示"今日额度已用尽" | host_agent 模式有 daily quota；明天再跑或传 `force=true`（吃用户当日 IDE 配额） |

---

## 数据布局参考（debugging 用，不要主动操作）

```
~/.ai-memory/
├── personal/<id>.md            should_keep=true 的 memory（人 + AI 共写）
├── projects/<git-id>/<id>.md   项目专属 memory
├── archive/<id>.md             用户/系统软删（可 restore）
├── .pending/<task_id>.task     等待你（agent）消化的任务包
├── raw/sessions/<date>.json    原始会话（collect 阶段产出，只读）
├── logs/                       distill / filtered / recall 日志
└── config/config.yml           用户配置（llm.mode 等）
```

完整设计：`docs/redesign.md`，未来路线（reflect/合并）：`docs/p6-reflect-design.md`。
