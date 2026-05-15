---
name: ai-coding-memory
description: |
  自动把每天与 Aone Copilot / Cursor / Qoder / Claude Code 的 AI 对话沉淀为分层、可召回的个人编码知识库（projects + domains + general 三层）。
  支持批量采集历史对话（last-7d / last-30d / 任意日期范围）、快速通道按价值分流、并发 auto 模式、图谱增强检索。
  TRIGGER when：用户说「整理今天的记忆」「跑一遍 memory pipeline」「沉淀今天的对话」「生成今日知识库」「distill 今日笔记」「编译我的知识库」「采集今日对话」「入库今日 topics」「刷新我的记忆」「更新我的 wiki」时；
  即使用户没有明确说「记忆」「pipeline」，只要他想把今天/昨天的 AI 对话沉淀成结构化知识、想生成可召回的 wiki、想运行 collect/distill/compile 中的任意阶段，都应使用此 Skill。
  另：日常 coding 时的「自动召回历史经验」由 ai-coding-memory MCP Server 直接对接 IDE 完成，**不**通过此 Skill 触发。
---

# AI Coding Memory

把每天的 AI 对话变成你的"长期记忆"。

```
collect 📥 → distill 🧪 → compile 📚 → recall 🔌
采集对话      预处理切分    分层入库      MCP 召回（含图谱扩展）
```

## 你（Agent）在本 Skill 中的角色

你是**整条 pipeline 的编排者**。本 Skill 的 4 个阶段中：
- **Stage 1 (collect)** 和 **Stage 3 (compile route)** 是纯脚本，你只需调命令
- **Stage 2 (distill)** 支持两种模式：
  - **Agent 编排模式**（默认）：你逐步消化 LLM 提示词包
  - **auto 模式**（`distill.py auto`）：自动并发调用 LLM API，适合定时任务
- **Stage 3 (compile ingest)** 是 Agent 编排模式，你需要逐 topic 调用 llm-wiki ingest 工作流
- **Stage 3.5 (graph build)** 是纯脚本，ingest 完成后构建知识图谱

每个 Stage 都有独立的详细操作手册（在 `references/` 下），需要时按需读取，**不要一次性全读完**。

## 📚 详细手册索引

| 想了解... | 读这个 |
|---|---|
| collect 怎么提取会话、支持哪些 IDE | `references/collect.md` |
| distill 的 4 步流水线、快速通道和 auto 模式 | `references/distill.md` |
| compile 怎么入库 + 图谱构建 | `references/compile.md` |
| MCP Server 工具契约（含图谱增强检索） | `references/mcp-server.md` |
| 完整架构设计 | `references/design.md` |

---

## 参数处理

本 Skill 接受可选参数：时间范围 + 阶段范围。参数通过 `$ARGUMENTS` 传入。

| 用户输入 | 行为 |
|---|---|
| 无参数 / `today` / `今天` | 完整 pipeline，date=today |
| `yesterday` / `昨天` | 完整 pipeline，date=yesterday |
| `collect today` | 仅 Stage 1 |
| `distill today` | 仅 Stage 2 |
| `compile today` | 仅 Stage 3 |
| `2026-04-25` | 完整 pipeline，date=2026-04-25 |
| `last-7d` | 完整 pipeline，采集最近 7 天 |
| `last-30d` | 完整 pipeline，采集最近 30 天 |
| `2026-04-20~2026-04-25` | 完整 pipeline，指定日期范围 |

---

## 标准工作流

### Step 0：环境定位

本 Skill 安装在 `~/.aone_copilot/skills/ai-coding-memory/`，但**真实代码**在用户的 ai-coding-memory git 仓库里（通常在 `~/ai-coding-memory/`）。

定位代码仓库根的优先级：
1. 环境变量 `AI_MEMORY_PROJECT_ROOT`
2. Skill 目录的真实路径（如果是 symlink）：`readlink -f $SKILL_DIR/..`
3. 默认路径：`~/ai-coding-memory`

### Step 1：collect（采集对话，纯脚本）

**何时执行**：`STAGE_FILTER=all` 或 `STAGE_FILTER=collect`。

支持的 IDE：**Aone Copilot / Cursor / Qoder / Claude Code**。

```bash
python3 "$PROJECT_ROOT/collect/scripts/extract_sessions.py" --range "$DATE_KEY" --verbose
```

`--range` 支持：`today` / `yesterday` / `2026-04-25` / `2026-04-20~2026-04-25` / `last-7d` / `last-30d`。
多日范围按天分文件输出。

### Step 2：distill（清洗对话）

**何时执行**：`STAGE_FILTER=all` 或 `STAGE_FILTER=distill`。

distill 按 topic 价值自动分流两条路径：

```
step1 (主题切分) → estimated_value?
                    ├── high    → step2 → step3 → step4（完整 3 次 LLM 调用）
                    ├── medium  → stepF（快速通道，1 次 LLM 调用合并 step2+3+4）
                    ├── low     → stepF（快速通道）
                    └── noise   → 丢弃
```

**快速通道**把指代消解 + 代码筛选 + 分层标注合并为单次 LLM 调用，减少 ~66% 请求数。
只有 high-value topic 走完整流水线以保证精度。

**两种执行模式：**

#### 模式 A：Agent 编排模式（默认）

```bash
python3 "$PROJECT_ROOT/distill/scripts/distill.py" plan --date "$DATE_KEY" --verbose
# Agent 消化 step1 → expand → 消化 step2/3/4/stepF → assemble
```

#### 模式 B：auto 一键模式（并发 LLM 调用）

```bash
python3 "$PROJECT_ROOT/distill/scripts/distill.py" auto --date "$DATE_KEY" --verbose \
    --concurrency 4 --llm-model qwen-plus
```

auto 模式参数：
- `--llm-api`：OpenAI-compatible API base URL（默认 Dashscope）
- `--llm-key`：API key（默认从 `DASHSCOPE_API_KEY` 或 `OPENAI_API_KEY` 读取）
- `--llm-model`：模型名（默认 `qwen-plus`）
- `--concurrency`：并发 LLM 调用数（默认 4，提升速度的关键参数）
- `--drop`：丢弃低价值 topic 的阈值

👉 完整手册：`references/distill.md`

### Step 3：compile（入库到分层 wiki）

**何时执行**：`STAGE_FILTER=all` 或 `STAGE_FILTER=compile`。

3.1 **路由阶段**（纯脚本）：
```bash
bash "$PROJECT_ROOT/compile/scripts/crystallize_topics.sh" "$DATE_KEY" --verbose
```

3.2 **逐 topic 入库**（Agent 编排）

3.3 **构建知识图谱**（纯脚本，ingest 完成后）：
```bash
bash "$PROJECT_ROOT/compile/llm-wiki-skill/scripts/build-graph-data.sh" "$SUBWIKI_PATH"
```

👉 完整手册：`references/compile.md`

### Step 4：（可选）召回验证

```bash
python3 "$PROJECT_ROOT/mcp-server/server.py" --self-check
```

MCP Server 检索策略（3 层）：index.md 摘要 → wiki 全文 grep → graph-data.json 图谱扩展。

👉 完整手册：`references/mcp-server.md`

---

## 输出格式

```markdown
## 🧠 AI Coding Memory Pipeline 执行报告 ({date_range})

### 📥 Stage 1: collect
- 提取会话数：{total_sessions}
- 各 IDE：{by_ide}

### 🧪 Stage 2: distill
- 已切分 topic 数：{topic_count}
- 完整流水线（high）：{full_pipeline_count}
- 快速通道（medium/low）：{fast_track_count}
- 跳过（noise）：{dropped_count}

### 📚 Stage 3: compile
- 已入库 topic：{ingested_count}
- 图谱构建：{graph_status}
- 失败：{failed_count}

### 🔌 Stage 4: recall
- MCP 自检：{self_check_status}
```

---

## 错误恢复速查

| 现象 | 处理 |
|---|---|
| `找不到 collect/scripts/extract_sessions.py` | 设置 `AI_MEMORY_PROJECT_ROOT` 指向你 clone 的仓库 |
| `python3 not found` | `brew install python@3.11`，再跑一次 install.sh |
| `import fastmcp 失败`（仅 Step 4） | `pip3 install --user fastmcp pyyaml` |
| 某个 IDE 数据库不存在 | 该 IDE 跳过，warnings 列出，不阻塞 |
| distill auto 模式 API key 缺失 | 设置 `DASHSCOPE_API_KEY` 或 `OPENAI_API_KEY` |
| distill 中 LLM 输出格式错误 | 按 `references/distill.md` 错误恢复速查 |
| compile 中子库 init 失败 | 按 `references/compile.md` 错误恢复速查 |

---

## 不要做的事

- ❌ 不要直接编辑 `~/.ai-memory/raw/sessions/*.json`（只读快照）
- ❌ 不要绕过 distill manifest 直接生成 topic .md
- ❌ 不要在 ingest 时不 cd 到对应子库（会污染其他子库）
- ❌ 不要把 high-value topic 的 4 步 合并为一次 LLM 调用（快速通道仅适用于 medium/low）
- ❌ 不要忽略失败 —— 一定告诉用户具体哪步失败、怎么恢复

---

## 完整设计参考

- `references/design.md` —— 完整设计蓝图
- `references/collect.md` —— Stage 1 详细手册
- `references/distill.md` —— Stage 2 详细手册（**Agent 编排必读**）
- `references/compile.md` —— Stage 3 详细手册（**Agent 编排 + 图谱构建必读**）
- `references/mcp-server.md` —— Stage 4 详细手册
