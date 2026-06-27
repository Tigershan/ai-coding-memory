# ai-coding-memory

> 跨 coding agent 的个人 / 项目 memory。
> 你和 AI 说过的每一句话都不会白费 —— 让开发经验跨会话、跨 Agent、跨开发者持续流动。

把每天与 **Cursor / Claude Code / Qoder** 的对话，自动沉淀成可读、可改、可召回的 markdown 笔记——任意 IDE 都能搜到。

---

## 心智模型：三个通道

```
你的对话                                你的 memory 库 (~/.ai-memory/)
──────────                              ─────────────────────────────
                                                                       
"记住这个 X"  ──── remember ─────────→  ⚡ 实时落盘到 personal/<id>.md  
                                          下一秒任意 IDE 可召回         
                                                                       
日常聊天     ──── lazy distill ──────→  🤖 IDE 启动时后台静默蒸馏     
(自动)                                    每次开新 chat 顺手消化 1 条
                                                                       
历史对话     ──── ai-memory init ────→  📦 一次性回溯（首装时）       
(7d/30d/all)                              生成任务包让 agent 慢慢消化   

读取通道：
  IDE 自动调 search_memory ←─────→ memory 库 ←─────→ 你随时
  (写代码时遇到相关问题)                              ai-memory ls/show/edit
  
                              ↓
                       AGENTS.md (兜底通道)
                       不支持 MCP 的 agent 也能从这读到摘要
```

**关键性质**：
- 三种写入通道**互不冲突**——你可以同时用：日常自动跑，重要的主动 `remember`，新装时 `init` 回溯
- 写入永远落盘到 markdown，**人随时可改**（`source: manual/edited` 的不会被 AI 覆盖）
- LLM 由你的 IDE 提供（默认 `host_agent` 模式）；想要后台 24/7 自动跑可选 `api` 模式

---

## 5 分钟上手

```bash
git clone <this-repo> ~/ai-coding-memory
cd ~/ai-coding-memory
./install.sh
```

`install.sh` 6 步交互安装：
1. 创建 `~/.ai-memory/` 数据目录
2. 检查 / 安装 Python 依赖（fastmcp + pyyaml）
3. 注入 MCP 配置到已装 IDE（Cursor / Claude Code / Qoder）
4. 安装统一 skill 包到 IDE skills 目录
5. **询问 LLM mode**（默认 host_agent 零成本；有 API key 可选 api 模式自动后台跑）
6. **询问是否现在 init**（推荐 last-7d；约 3 分钟，host_agent 模式只生成任务包不烧钱）

装完会自动把"重启 IDE 后第一句话"复制到剪贴板，提示你 Cmd+V 即可。

---

## 装完后的 3 件事

### 1. 验证装好了

在任意 IDE 里说一句：

> 我刚装了 ai-coding-memory，请验证一下能不能用

skill 会跑 onboarding 流程：MCP 连通性 → 写一条测试 memory → 召回验证 → 给出推荐下一步。30 秒走完。

### 2. 让 AI 记住一些事

```
> 记住这个： Redis 连接池 maxIdle 必须 ≥ 8，否则高峰会断连
```

AI 调 MCP `remember` 工具，立即落盘到 `~/.ai-memory/personal/`。下次切到 Aone Copilot 问相关问题，自动召回。

### 3. 让 AI 整理累积的对话

```
> 整理今日记忆（或：消化任务包 / 跑一遍 memory pipeline）
```

agent 走 skill 的批量消化流程

---

## 一些特性

| 特性 | 说明 |
|---|---|
| **跨 IDE 共享** | 一份数据，4 个 IDE 都能读写（Cursor / Claude Code / Qoder） |
| **项目自动隔离** | 按 git remote URL 归一化作 key，不同 repo 不串味 |
| **跨项目经验迁移** | 在新项目踩老坑，相关 memory 会被召回（tags 重合 + 标题相似度阈值） |
| **人改优先** | `source: manual/edited` 的 memory 永远不会被自动 pipeline 覆盖 |
| **冲突检测** | 同主题新旧 memory 自动标 `potential_conflicts`，召回时降权 |
| **AGENTS.md 双写** | 项目摘要同步到 `<project>/AGENTS.md` 的 marker 块，不破坏用户已有内容；不支持 MCP 的 agent 也能读到 |
| **零外部依赖** | `host_agent` 模式不需要任何 API key；纯 stdlib + fastmcp |

---

## 数据布局

```
~/.ai-memory/
├── personal/<id>.md            跨项目通用（人 + AI 共写）
├── projects/<git-id>/<id>.md   项目专属
├── archive/<id>.md             用户/系统软删（可 restore）
├── .pending/<task_id>.task     host_agent 模式下挂起的任务包
├── raw/sessions/<date>.json    原始会话（collect 阶段产出）
├── logs/                       distill / filtered / recall 日志
└── config/config.yml           用户配置
```

每个 `.md` 是带 frontmatter 的纯 markdown，可以直接 `$EDITOR` 打开编辑——人改后 source 字段自动升级为 `edited`，从此不被 AI 覆盖。

---

## CLI 速查

```bash
ai-memory add [--scope personal|project] [--tags ...]   # 手动新增
ai-memory edit <id-or-substring>                        # $EDITOR 编辑
ai-memory ls [--scope ...] [--value high]               # 列出
ai-memory show <id>                                     # 全文
ai-memory archive <id>                                  # 软删
ai-memory restore <id>                                  # 从 archive 恢复

ai-memory distill [--range today]                       # 蒸馏当日
ai-memory init [--range last-7d|last-30d|all] [--yes]   # 首次回溯
ai-memory pending                                       # 看任务包状态
ai-memory config show / get / set                       # LLM mode 等
ai-memory stats                                         # 写入/召回/采纳统计
ai-memory sync-agents-md                                # 同步项目摘要到 AGENTS.md
```

CLI 的入口实际是 `python3 cli/ai_memory.py`，你可以在 `~/.bashrc` 加 alias：
```bash
alias ai-memory="python3 ~/ai-coding-memory/cli/ai_memory.py"
```

---

## 隐私

- 所有 memory 数据**仅存本地** `~/.ai-memory/`
- `host_agent` 模式：LLM 调用走宿主 IDE 自己的 LLM 通道（你怎么用 IDE 就怎么用，不额外向第三方上行）
- `api` 模式：调用你配置的 OpenAI-compatible API（DashScope / OpenAI / 等），相关对话片段会发往该 API
- **写入前脱敏**（v1.6）：distill 与 `remember` 落盘前对 9 类 secret（AWS / OpenAI / Slack / GitHub token / JWT / JDBC password / RSA 私钥块 / 通用 `password=...`）做正则替换为 `<REDACTED:类型>`；命中数写入 `~/.ai-memory/logs/redact-<date>.jsonl`（**不含原文**，仅审计计数）
- `domain-mapping.yml` 这类用户私有配置不入 git

---

## 文档导航

| 文件 | 何时读 |
|---|---|
| `README.md`（本文）| 第一次了解工具 |
| `docs/redesign.md` | 完整设计文档（含所有 ADR） |
| `docs/p6-reflect-design.md` | 未来路线：reflect / 合并机制 |
| `skill/SKILL.md` | Agent 视角的 onboarding + 批量消化操作手册 |
| `docs/design.legacy.md` | 已废弃的 v0.3 设计（仅历史归档） |

---

## 现状

- ✅ P0–P5 已实施（commits `65f3359` ~ `1124d1b`）
- ✅ 多轮 bug fix（frontmatter block scalar / read_page security / submit silent loss / 等）
- ✅ 在用户真实数据 last-7d 上验证全链路（67 任务包 → 27 条 memory + 12 条丢弃）
- ✅ P7 完成（v1.6，commits `54df105` + `df57123`）：借鉴 agentmemory 的 5 项升级——BM25 + CJK bigram 替代 grep、时间衰减软重排、可选本地向量重排（`pip install '.[vector]'`）、写入前 9 类 secret 脱敏、frontmatter origin 扩展 + 召回 citation 渲染。详见 `docs/redesign.md` ADR-14
- 🔬 P6 (reflect) 已有设计草案，等 P5 跑 1-2 月数据再决定是否上
