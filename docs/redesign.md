# ai-coding-memory · 重构设计文档 (redesign v1.0)

> 本文档替代 `docs/design.md` v0.3，作为后续实施的唯一权威。
> design.md 保留为历史归档，不再维护。

---

## 0. 写在最前

`design.md` v0.3 的方案在愿景层（"零配置个人长期记忆"）和实现层（"Agent 每天手工编排 4N 次 LLM 调用 + 强依赖用户配置"）之间有一道大裂缝。重构方向不是修补，而是**重新对齐核心目的**：

> 解决跨 coding agent 的、个人级与项目级 memory 的**生成、管理与召回**。

围绕这个目的，本次重构同时做减法和加法：
- **减法**：砍掉 domain 层、4-step distill 流水线、llm-wiki fork、graph + Louvain
- **加法**：实时 `remember`、首次 bootstrap init、lazy trigger 自动定时、人手编辑保护、跨项目经验迁移、召回反馈、**LLM Provider 抽象层（host_agent / api / local 三档）**

### Changelog

- **v1.6**（本版本，借鉴 agentmemory 的 5 项检索/写入升级 + ADR-14）：
  - **召回升级**：grep → **BM25Okapi + 时间衰减**（仅 `auto/bootstrap` 衰减、`manual/edited` 永不衰减）+ 可选本地向量重排（默认关，需 `pip install '.[vector]'` 装 fastembed）。
    - `mcp-server/lib/bm25_index.py` 新增：rank_bm25 包装 + ASCII 词 / CJK bigram 双分词器；按 scope 维度的 mtime 指纹缓存
    - `mcp-server/lib/searcher.py` 重构：BM25 排序 → value/source/superseded/cross_project/decay 加权 → 可选向量重排。小语料下 Okapi IDF 可能为负（`fix(bm25): per-scope shift...`），按 scope 平移到 ≥ 0 后再加权，避免 manual+1.3 反向压低
    - `mcp-server/lib/vector_rerank.py` 新增：fastembed lazy import + 路径级 embedding 缓存（`~/.ai-memory/.cache/embed-index/`）；BM25 min-max 归一化 + cosine 线性融合
  - **隐私边界**：`core/privacy_filter.py` 在 distill / remember 写入前正则脱敏 9 类常见 secret（AWS / OpenAI/Anthropic / Slack / GitHub / JWT / JDBC password / RSA 私钥块 / 通用 key=value）。命中替换为 `<REDACTED:类型>`，审计日志 `~/.ai-memory/logs/redact-<date>.jsonl` 不含原文。
  - **Citation / provenance**：`origin` frontmatter 字段扩展 `distilled_at` / `remembered_at` / `msg_range`；`search_memory` 返回的每条结果末尾自动渲染 `📎 来自 <ide> · <date> · session <prefix>..` 行，老 memory 缺字段时静默跳过。
  - **配置**：`mcp_server.time_decay_half_life_days`（90）/ `time_decay_floor`（0.5）/ `vector_rerank_*` 系列加入 `config/default.yml`，对应 `Config` dataclass 增字段。
  - **依赖**：`rank_bm25>=0.2.2` 进主依赖（纯 Python 无 C 扩展）；`fastembed>=0.3.0` + `numpy` 进 `[vector]` 可选 extras。
  - **新增 ADR-14**：借鉴 agentmemory 的 5 项升级（BM25 / 隐私脱敏 / 时间衰减 / 可选向量 / citation），均与本仓库"markdown-first / 人随时改 / 零 API key"原则兼容；不抄的有"4 级合并层级 / 12-hook 全自动 / 多 agent 协调原语 / 自研 runtime（iii-engine）"。
  - 新增 28 个 pytest 单测（privacy_filter / decay / bm25 tokenizer），`tests/` 目录从无到有。

- **v1.5**（双 mode + 撤回 v1.4 的 "顺手 1 条" 引导）：
  - 实施 `LocalProvider`（Ollama OpenAI-compatible）。**init / 批量回溯默认走
    `batch_mode=local`**（如果用户装了 Ollama + qwen3:8b），0 现金 + 0 IDE 配额，
    单次 30-50s，~200 session 一晚跑完。日常增量保持 `daily_mode=host_agent`。
  - 拆 `llm.daily_mode` + `llm.batch_mode`（旧 `llm.mode` 兜底兼容）。
    `core/config.py:resolve_mode(scope)` + `detect_local_available()` 提供运行时判断。
  - **撤回 v1.4 引入的"每开新 chat 顺手 1 条任务包"机制**（修订 ADR-7 引导）：
    实测体感不佳——用户每次开 chat 多 ~5s 延迟，且 instructions 里"启动时主动调
    project_context"在不同 IDE 模型听话度不一。改为：增量场景**仅在用户主动**说
    "整理今日记忆"时走 skill 批量消化流程。`_build_pending_distill_hint` 简化为
    纯状态告知（不再注入 agent 行为指令）。
  - 新增 ADR-13：双 mode 拆分。
  - distill.py 加 `--mode-hint daily|batch` + 超长 session 兜底（`>28K tokens` 直接
    标 `task.failed` 防止本地小模型 OOM/截断）。
  - install.sh Step 5 重写：检测 Ollama 状态，提示用户**手动**装（不自动 brew/pull
    避免诊断成本），按检测结果自动设 `batch_mode=local|host_agent`。

- **v1.4**（UX hardening pass）：对照"用户第一次下载到第一次 aha moment"动线做整体 review，落地 6 项改进：
  - **阻塞 #1**：`scripts/inject_mcp_config.py` + `install.sh` 加 Claude Code (~/.claude.json) 自动注入分支 —— 之前 Anthropic 用户开箱不可用
  - **阻塞 #2 + 体验 #3**：`install.sh` 末尾换成 ASCII 重启提示框 + 启动咒语 + 自动 pbcopy 到剪贴板（macOS / Linux 兼容），用户重启 IDE 后 Cmd+V 就有清晰第一句话
  - **阻塞 #3**：`get_next_distill_task` docstring 改成"分批 5-10 + 停下问询"模式，明确告知 agent 不要无限循环；配合 user/linter 并行加的 `distill_quota`（每日上限 + 顺延次日）和 `_build_pending_distill_hint`（每次 `project_context` 顺手消化 1 条）
  - **体验 #4**：`search_memory` 库为空时自动给出可执行下一步（`ai-memory init` / `remember` / `pending` 提示）
  - **体验 #6**：`ai-memory stats` pending ≥ 50 黄色警告、≥ 100 红色警告 + 推荐动作
  - **体验 #7**：README 整体重写，加"三通道心智模型"ASCII 图，对齐 redesign（移除 llm-wiki / domain / qoderwork 等已废弃概念）
  - **新增 onboarding skill 流程**：`skill/SKILL.md` 加角色 A（Onboarding 验证者）和角色 B（批量消化器）双流程 + 严格 anti-loop 约束。同时把 SKILL.md 整体重写对齐 redesign（之前还停在 v0.3 的 4-step pipeline）

- **v1.3**：移除 `.cold/` 冷存储概念。should_keep=false 的 topic 直接丢弃，仅日志保留审计信息。修订 ADR-8。理由：实测一周数据 LLM 判 false 准确率高 + restore 路径几乎无人走 + raw/sessions 已是更可靠兜底，cold 是过度设计。
- **v1.2**：吸收开源方案借鉴。新增 AGENTS.md 双写通道（覆盖不支持 MCP 的 agent，ADR-11）；新增轻量冲突检测（避免"过期规则被召回，比没记忆更危险"，ADR-12）；召回引擎中间台阶改为 SQLite FTS5；reflect/合并机制列入待定（P6 候选）。
- **v1.1**：引入 LLM Provider 抽象层。LLM 不再是基础设施依赖，而是按优先级注入的能力。`host_agent` 模式（用宿主 agent 自跑，零配置）成为默认。修订 ADR-2/5/7，新增 ADR-10。distill 双模式落地（任务包 vs 后台 auto）。
- **v1.0**：首版，对齐"跨 agent 个人/项目 memory"核心目的，砍掉 domain/graph/llm-wiki fork。

---

## 1. 用户价值

### 1.1 谁是用户

**重度使用多个 coding agent 的工程师**。典型画像：
- 同时使用 2 个以上 IDE 的 AI 助手（Cursor / Aone Copilot / Qoder / Claude Code 任意组合）
- 在多个代码仓库间切换
- 工作中产生大量"和 AI 反复讨论"的对话流

### 1.2 用户的真实痛点

| 痛点 | 当前现状 |
|---|---|
| **重复踩坑** | 上次在 A IDE 和 AI 解决过的 bug，今天在 B IDE 又问一遍，AI 又给次优方案 |
| **跨 IDE 上下文丢失** | 在 Cursor 讨论了架构，切到 Aone 写代码 AI 一无所知 |
| **个人偏好/约定丢失** | 每个新 chat 都要重申"我喜欢 X 风格"、"项目 Y 用 Z 不用 W" |
| **项目专属知识需要反复输入** | 字段映射、内部接口、团队约定，AI 每次都要重学 |
| **新项目无法继承经验** | 几年踩过的坑、攒下的最佳实践，对 AI 来说是零经验 |
| **agent 越多记忆越散** | 每个 IDE 各有各的 memory 机制，互不相通 |
| **企业 IDE 记忆机制弱** | 内部 IDE 通常不像 Cursor 有完善 memory |

### 1.3 我们解决什么 / 不解决什么

**解决**：
- ✓ 跨 IDE 共享同一份 memory
- ✓ 项目自动隔离（按 git remote URL）
- ✓ 跨项目经验迁移（在 A 项目踩的坑，在 B 项目能召回）
- ✓ 实时主动写入（任意 IDE 说"记住"立即落盘，下一秒任意 IDE 可召回）
- ✓ 人对 memory 完全可见、可改、可删

**明确不做**：
- ✗ 团队级共享（这是另一个工具的事，不是个人 memory 的范畴）
- ✗ 替代 Cursor 自带 memory（互补，不竞争）
- ✗ 实时上下文管理（agent 短期 context 由 IDE 自己管）
- ✗ 通用知识检索（"Java HashMap 是什么"应由 LLM 自身回答）

### 1.4 一句话价值主张

> **你和 AI 说过的每一句话都不会白费——跨 IDE 共享，永久可召回，永远可改。**

并且：**装完即用，不依赖任何外部 API key**——宿主 agent 自己就是 LLM。

### 1.5 与其他记忆工具的差异

| 维度 | Cursor 自带 memory | Aone Copilot rules | 本工具 |
|---|---|---|---|
| 跨 IDE | ✗ | ✗ | ✓ |
| 自动从对话沉淀 | 部分 | ✗ | ✓ |
| 项目级隔离 | ✓ | 部分 | ✓ |
| 跨项目经验迁移 | ✗ | ✗ | ✓ |
| 用户可手编辑 | ✓ | ✓ | ✓ |
| 数据本地 | ✓ | ✗ | ✓ |
| 历史回溯（init） | ✗ | ✗ | ✓ |

---

## 2. 用户体验

### 2.1 完整旅程（Day 0 → Month 3）

#### Day 0：安装

```
$ git clone https://github.com/xxx/ai-coding-memory.git
$ cd ai-coding-memory && ./install.sh

[1/5] 检测环境...
  ✓ Python 3.11.7
  ✓ 检测到 IDE: Cursor, Aone Copilot, Claude Code

[2/5] 安装依赖...
  ✓ fastmcp, pyyaml

[3/5] 创建数据目录...
  ✓ ~/.ai-memory/

[4/5] 注入 MCP 配置...
  ✓ ~/.cursor/mcp.json （已备份原配置）
  ✓ ~/.aone_copilot/mcp.json
  ✓ ~/.claude/mcp.json

[5/5] 初始化记忆库？

  扫描结果：发现 1247 个历史 session（Cursor 823, Aone 312, Claude 112）
  
  请选择：
    1) 处理最近 7 天   （32 session, ~¥0.8, ~3 分钟）  ← 推荐
    2) 处理最近 30 天  （186 session, ~¥4.5, ~15 分钟）
    3) 处理全部历史   （1247 session, ~¥30, ~90 分钟）
    4) 跳过，以后再说
  
  选择 [1]: _
```

#### Day 1：首次使用

打开 Cursor 写代码。和 AI 正常对话。
偶尔说一句"记住一点：winterfell 的 OfferModel 字段映射用 snake_case，不要用 camelCase。"
→ AI 调 `remember` 工具 → 返回 `✓ 已记住：personal/2026-05-16-offermodel-naming.md`
→ 用户看到了文件路径，知道随时可以打开看。

切到 Aone Copilot 继续 winterfell 项目。
问 AI："新加的字段叫什么命名？"
→ AI 调 `search_memory` → 召回上一句记忆 → 直接给出答案：snake_case。
→ **第一个 aha moment**：跨 IDE 真的通了。

#### Week 1：建立习惯

- 用户开始有意识地说"记住"
- 偶尔召回到一条不准的，`ai-memory edit <query>` 改了它
- 看 `ai-memory stats`：本周写入 12 条，召回 47 次，被采纳（read_page 打开过）23 次
- 第二个 aha moment：**我能掌控这套系统，它是我的笔记本，不是黑盒**

#### Month 1：信任建立

- memory 库 ~80 条
- 切换到一个新项目，问 AI 一个 Redis 用法 → AI 召回到 3 周前在另一个项目里讨论过的同类问题
- 第三个 aha moment：**跨项目经验真的复用了**
- 用户手写了几条"重要约定"放进 personal/，永远生效（这是 AI 蒸馏不出来的精华）

#### Month 3：长期价值

- memory 库 ~250 条
- 自动衰减归档了 60 条不被召中的旧 memory（不删，进 archive）
- 用户偶尔翻 `~/.ai-memory/personal/` 当成自己的编码笔记本
- 切换公司/项目时，过去经验依然可用

### 2.2 关键 Aha Moments

**aha #1：跨 IDE 共享真的通了**
触发：Day 1 在 Cursor `remember`，立刻在 Aone 召回到。
设计要点：`remember` 必须是同步落盘，不能延迟；召回必须命中。

**aha #2：召回到了几个月前的对话**
触发：init 跑完，第一次问相关问题召回到历史。
设计要点：init 默认要跑出"看得见"的量；召回结果带源对话日期。

**aha #3：跨项目经验复用**
触发：在新项目踩到老坑，被召回。
设计要点：召回默认 scope=auto **必须包含**跨项目高相关结果。

**aha #4：手编辑就生效**
触发：用户改了一条 memory，下次召回精准了。
设计要点：人改的文件永远不被自动覆盖；改完无需重启 server。

### 2.3 UX 设计原则

| 原则 | 具体实现 |
|---|---|
| **可见性**：用户始终知道系统在干什么 | `ai-memory stats` / 召回结果带 source 标识 / lazy trigger 写日志 |
| **可控性**：用户能改、能删、能恢复 | `source: manual` 永不覆盖 / `archive` 软删可 restore |
| **不烦人**：低频高质，不是高频骚扰 | search_memory docstring 写"事实而非指令"避免滥调 / project_context 注入要短 |
| **快速回报**：第一天就感受到价值 | install 集成 init / 首次 remember 后立即可在他 IDE 召回 |
| **信任建立**：召回带"为什么命中"，错误友好 | 召回结果显示 path + score + 命中原因 / 错误信息含可执行命令 |

### 2.4 反向：要避免的体验

| 反例 | 后果 | 规避手段 |
|---|---|---|
| 安装失败，stack trace 飞屏 | 卸载 | install.sh 全程友好提示，每步可重试 |
| 召回全是噪音，用户失信 | 卸载 | 启发式过滤 + LLM should_keep + 用户反馈衰减 |
| 不小心改了 memory 被覆盖 | 失信 | source 字段保护机制 |
| 想批量清理某段时期 memory 不知怎么做 | 弃用 | `ai-memory ls --since/--until` + `archive --batch` |
| AI 每个简单问题都先 search，烦人 | 关掉 MCP | docstring 强调"用户特定经验时才调"，不强迫 |
| 系统在干啥不知道（黑盒） | 信任流失 | 透明：stats / logs / 召回结果带源 |
| 隐私担忧：发现对话被发云 | 卸载 | 安装时明确告知 LLM provider；提供本地模型选项（远期） |

### 2.5 一个反向硬指标

**"用户连续 3 周每天都用，且 stats 显示采纳率 > 30%"** = 工具真的有用。
不达标就回头看哪个环节有问题，不要急着加功能。

---

## 3. 设计哲学

### 3.1 Memory 是人 + AI 共同维护的笔记本

AI 写第一稿，人随时可改，**人的修改永远优先**。
推论：
- 不需要复杂的实体合并算法（人会手动合）
- 不需要 confidence score（人改过的就是 1.0）
- 不需要 graph + Louvain（人会用 markdown 链接）

### 3.2 启发式 + LLM 双层审慎，不一锤定音

- 启发式只砍"几乎肯定无价值"的（5 条保守规则）
- LLM 判 should_keep=false 的直接丢弃（仅在日志和 dropped 字段保留 title+keep_reason 用于审计）
- 用户事后可审 logs 调阈值

### 3.3 文件即接口，零配置默认

- 数据格式：Markdown + frontmatter，纯文本，git-friendly
- 模块间通信：文件系统
- 配置：默认值能跑，`config.yml` 可选不可必

### 3.4 跨 agent 是核心，不是附加项

整个架构所有决策的优先级：跨 agent > 项目隔离 > 蒸馏质量 > 召回花活。
任何与"跨 agent"冲突的设计都让步。

### 3.5 复杂度对齐：生成和召回的复杂度必须匹配

旧方案在 distill 上做 4 步精细蒸馏，但召回侧只是 grep——前面再精细，召回侧用不上。
新方案：distill 简化为 1 步；召回侧未来可升级为 BM25 + embedding（数据规模触发时）。

### 3.6 LLM 是注入的能力，不是基础设施

工具自身**不绑定任何 LLM provider**。LLM 能力按优先级从三处来：

1. **宿主 agent 自己**（默认，零配置）：用户在 IDE 触发 skill 时，宿主 agent 当前对话的 LLM 直接消化任务包
2. **外部 API**（可选加速，需 env var）：lazy trigger 后台静默用
3. **本地模型**（远期，强隐私场景）：Ollama / llama.cpp 等

推论：
- "零配置可用"是硬约束——没有 API key 也能跑（用宿主 agent）
- 配了 API key 的用户获得"自动后台运行"的升级体验，但不是必需
- 隐私敏感的用户可以拒绝外部 API，仍能完整使用工具

这个原则让"采集自动化"的含义分裂成两层：
- **采集行为本身始终自动**（lazy trigger 始终在跑）
- **蒸馏行为按 LLM 来源决定**（api 模式后台跑；host_agent 模式只挂起任务包，等用户在 IDE 里说"整理"时由 agent 跑）

---

## 4. 架构总览

```
┌──────────────────────────────────────────────────────────┐
│  数据层  ~/.ai-memory/                                    │
│    personal/*.md           跨项目通用（人 + AI）          │
│    projects/<git-id>/*.md   项目专属（人 + AI）           │
│    archive/*.md             用户/系统软删（可 restore）    │
│    .pending/*.task          host_agent 模式下挂起的任务包  │
│    .last_distill            lazy trigger 时间戳           │
│    .distill.lock            多 IDE 并发写锁               │
│    .init-progress.json      bootstrap 断点               │
│    logs/                   静默后台日志 + 召回反馈         │
└──────────────────────────────────────────────────────────┘
       ↑ 写入                          ↑ 召回
   ┌───┼───────┬────────┬────────┐    ┌───┴────────┬───────────────┐
   │   │       │        │        │    │            │               │
 init  lazy   remember  add/edit  archive  search   read_page   project_context
 一次性 每日    MCP      CLI       软删     MCP      MCP         启动注入
 历史   batch  实时     人编辑                                   到 system prompt
 回溯   后台
   │     │     ↑
   ↓     ↓     │（不需要 LLM）
 ┌──────────────────────────────────────────┐
 │  LLM Provider 抽象层                      │
 │   ├─ host_agent  ← 默认，零配置           │
 │   │   通过 MCP 任务包，宿主 agent 自跑     │
 │   ├─ api         ← 可选，需 env var       │
 │   │   后台并发调 OpenAI-compatible API    │
 │   └─ local       ← 远期，Ollama 等        │
 └──────────────────────────────────────────┘
```

**关键架构性质**：写入侧的 init / lazy / distill 都通过 LLM Provider 抽象层；召回侧（search / read_page / project_context）和 remember/edit/archive 都**不需要 LLM**。这意味着即使没有任何 LLM provider，**召回 + 手动管理这条核心通路始终可用**。

### 写入三通道

| 通道 | 触发 | 实现 | 是否要用户配置 |
|---|---|---|---|
| **显式实时** | 用户在 IDE 说"记住 X" | MCP `remember(text)` | ✗ |
| **显式实时** | 用户用 CLI | `ai-memory add` | ✗ |
| **手编辑** | 用户改 .md 文件 | `$EDITOR` | ✗ |
| **隐式批量** | 每日首次 IDE 启动 | MCP server 内置 lazy trigger | ✗ |
| **首次回溯** | install 后 / 任意时刻 | `ai-memory init` | ✗ |
| **手动批量** | 用户主动 | `ai-memory distill` | ✗ |

**关键**：整个写入链路无任何外部 scheduler / cron / launchd 依赖。

### 召回三通道

| 通道 | 触发 | 模式 | 用途 |
|---|---|---|---|
| **search_memory** | LLM 主动调 | pull | 查具体问题 |
| **project_context** | IDE 启动时 | push | 把项目 _index 注入 system prompt |
| **read_page** | LLM 看完 search 想看全文 | pull | 详细查阅 |

### 管理通道

| 操作 | 接口 |
|---|---|
| 列出 | `ai-memory ls [--scope ...] [--since ...]` |
| 软删 | `ai-memory archive <id>` 或 MCP `forget(id)` |
| 恢复 | `ai-memory restore <id>` |
| 反馈 | 召回时自动记日志（query / hits / read_page 调用） |
| 衰减 | 90 天未召中 + source=auto 自动归档 |
| 统计 | `ai-memory stats` |

---

## 5. 数据模型

### 5.1 Frontmatter Schema

```yaml
---
id: 2026-05-16-winterfell-rate-limit          # 稳定 ID（人不会改），用作 forget/read_page 的 key
scope: project                                # personal | project
project_key: github.com/xxx/winterfell        # 仅 scope=project 时填，git remote URL
source: auto                                  # auto | edited | manual | bootstrap
value: high                                   # high | medium | low（影响召回排序）
created: 2026-05-16
updated: 2026-05-16
tags: [rate-limit, redis]                     # 用于跨项目相关性匹配
origin:                                       # 来源追溯（自 v1.6 字段更全）
  ide: aone-copilot                             # 写入路径：mcp-remember / 各 IDE
  session_id: abc123                            # auto/bootstrap 才有；用于召回时 citation 渲染
  workspace: /Users/tiger/winterfell
  msg_range: [10, 22]                           # auto/bootstrap 蒸馏的对话消息区间
  distilled_at: 2026-05-17                      # auto/bootstrap：蒸馏完成日期
  remembered_at: 2026-05-17T14:30:01            # manual：用户主动 remember 的精确时间
---

# 限流方案：Redis 分布式 vs Guava 单机

## 结论
最终采用 Redisson 的 RRateLimiter，因 winterfell 是多实例部署。

## 关键代码
（保留的代码片段）

## 关联
- [[2026-04-22-winterfell-redis-cluster]]
- [[redis-evalsha-pattern]]
```

**正文格式不强制**——上面的 H2 结构是模板默认，人可以全删重写。
唯一硬约束：必须有 `id` 和 `scope`。

### 5.2 source 字段语义（决定保护级别）

| source | 含义 | 自动 distill 是否覆盖 |
|---|---|---|
| `auto` | distill 生成，未被人动过 | ✓ 可被新版覆盖（基于 mtime 判断） |
| `bootstrap` | init 历史回溯生成 | ✓ 可被覆盖（同 auto） |
| `edited` | auto 文件被人编辑过（mtime 变化检测自动升级） | ✗ 永不覆盖 |
| `manual` | 人手新增（CLI add 或直接写文件） | ✗ 永不覆盖 |

### 5.3 项目 Key：为什么用 git remote URL

| 候选 | 缺点 |
|---|---|
| `workspace basename` | 同名仓库冲突；rename 后失联 |
| `workspace 绝对路径` | 移动目录后失联；多人不一致 |
| **`git remote get-url origin`** | 稳定、唯一、可跨机器 |

实现：`project_key = normalize(git remote get-url origin)`，去掉 `.git` 后缀和协议前缀，统一成 `github.com/xxx/yyy` 形式。
没有 git remote 的目录（如临时 sandbox）→ 归到 `personal`。

### 5.4 文件命名与目录布局

```
~/.ai-memory/
├── personal/
│   ├── 2026-05-16-redis-evalsha.md
│   └── 2026-05-15-git-rebase-onto.md
├── projects/
│   ├── github.com_xxx_winterfell/                 # / 替换为 _
│   │   ├── _index.md                              # 自动生成的项目摘要（用于 push 注入）
│   │   ├── 2026-05-16-offermodel-naming.md
│   │   └── ...
│   └── github.com_xxx_other-repo/
├── archive/                                        # 用户/系统归档（手动 archive 或未来 reflect）
│   └── 2026-05-16-redis-evalsha.md                # 同名移过来即可
├── logs/
│   ├── distill-2026-05-16.log
│   ├── filtered-2026-05-16.jsonl                  # 启发式过滤记录
│   └── recall-2026-05-16.jsonl                    # 召回反馈
├── .last_distill
├── .distill.lock
├── .init-progress.json
└── config.yml                                      # 可选用户配置
```

---

## 6. 各模块设计

### 6.0 LLM Provider 抽象层（基础设施）

**职责**：把"调用 LLM"这个动作从 distill / init 里解耦，按用户配置切换三种来源。

**接口契约**：

```python
class LLMProvider(Protocol):
    mode: str  # "host_agent" | "api" | "local"
    
    def is_synchronous(self) -> bool:
        """同步可调（api/local）= True；异步通过任务包（host_agent）= False"""
    
    def run(self, prompt: str) -> str:
        """同步模式：直接调 LLM 拿结果。
        异步模式：写任务包到 .pending/，抛 PendingTaskError，等 agent 来取。"""
```

#### 三种 mode 的实际行为

| mode | is_synchronous | run() 行为 | 用户感知 |
|---|---|---|---|
| `host_agent` | False | 写任务包到 `.pending/<task_id>.task`，返回任务 id | 用户在 IDE 里说"整理今日记忆"时由 agent 消化 |
| `api` | True | 调 OpenAI-compatible API，返回结果 | 后台静默跑完 |
| `local` | True | 调本地模型，返回结果 | 后台静默跑完，无外网 |

#### 自动检测：`mode: auto`（默认）

```python
def detect_mode():
    if env("AI_MEMORY_LLM_MODE"):
        return env("AI_MEMORY_LLM_MODE")           # 用户显式设置最优先
    if config.llm.mode != "auto":
        return config.llm.mode                      # config.yml 显式设置
    if env("OPENAI_API_KEY") or env("DASHSCOPE_API_KEY"):
        return "api"                                # 检测到 key 自动启用
    return "host_agent"                             # 兜底
```

但这个"自动检测 → api"的逻辑不会**默默**生效，必须经过下一节的 install 询问。

#### install.sh 显式询问（C 方案）

```
[5/6] LLM 配置

  检测结果：
    ✓ 发现 OPENAI_API_KEY 环境变量

  你想怎么用 LLM？
    1) 仅用宿主 agent（推荐，零成本，零配置）
       工作方式：你在 IDE 里说"整理今日记忆"时，agent 用自己的 LLM 跑
       优点：免费、隐私好；缺点：需要手动触发
    
    2) 启用 api 模式（自动后台运行，会产生账单）
       工作方式：每天打开 IDE 时自动后台跑 distill
       优点：完全自动；缺点：消耗你的 API 配额
    
    3) 我自己看着办（写 config.yml 配置）
  
  选择 [1]: _
```

选 2 后会写入 `~/.ai-memory/config.yml`：
```yaml
llm:
  mode: api
  api:
    provider: dashscope          # 或 openai
    model: qwen-plus
    key_env: DASHSCOPE_API_KEY
    concurrency: 4
    daily_budget_yuan: 5         # 软上限，超过停 lazy trigger
```

**没有 OPENAI_API_KEY 也没有 DASHSCOPE_API_KEY 的用户**：跳过这一步，默认 host_agent，install 完直接可用。

#### 配置切换：随时可改

```bash
ai-memory config set llm.mode host_agent     # 临时关掉 api 自动跑
ai-memory config set llm.mode api            # 重新启用
ai-memory config show                         # 看当前配置
```

---

### 6.1 启发式过滤（heuristic_filter.py）

**5 条保守规则**（`coding agent` 场景特化，不误杀单轮 QA）：

```python
def is_noise(session) -> tuple[bool, str | None]:
    user_msgs = [m for m in session.messages if m.role == "user"]
    asst_msgs = [m for m in session.messages if m.role == "assistant"]

    # 1. 没有任何实质交互
    if not user_msgs or not asst_msgs:
        return True, "no-real-interaction"

    # 2. user 总输入 < 10 字符（误触/测试）
    if sum(len(m.content) for m in user_msgs) < 10:
        return True, "user-input-too-short"

    # 3. assistant 全是工具调用，文本 < 10%
    if asst_text_ratio(asst_msgs) < 0.1:
        return True, "all-tool-calls-no-thinking"

    # 4. 重复内容（user 反复贴同一段 3+ 次）
    if has_duplicate_user_msgs(user_msgs, threshold=3):
        return True, "repeated-stuck-pattern"

    # 5. AI 拒答未追问
    if asst_msgs[-1].is_refusal() and len(user_msgs) == len(asst_msgs):
        return True, "refused-no-followup"

    return False, None
```

**核心原则**：保守，宁可放过也不冤杀。预期砍掉 15-25% 的 session。
被砍掉的写 `logs/filtered-YYYY-MM-DD.jsonl`，可审计。

### 6.2 distill：1-step prompt + 双模式执行

**输入**：通过启发式的 session
**输出**：1 个或多个 markdown 文件（一个 session 可能拆出多个 topic）

#### 6.2.1 共用的 1-step prompt

无论 host_agent 还是 api 模式，**prompt 是同一份**——这是关键，避免双份维护。

单次 LLM 调用产出：
```yaml
topics:
  - id: 2026-05-16-winterfell-rate-limit
    title: 限流方案：Redis vs Guava
    summary: 最终采用 Redisson...
    scope: project | personal
    project_key: github.com/xxx/winterfell  # scope=project 时
    tags: [rate-limit, redis]
    value: high | medium | low
    should_keep: true | false              # ⭐ LLM 自评是否值得入库
    keep_reason: "包含明确技术决策..."
    content: |                              # 完整 markdown 正文
      # ...
```

落盘策略（两模式相同）：
- `should_keep: true` → 入库 `personal/` 或 `projects/<key>/`
- `should_keep: false` → **直接丢弃**，仅在 distill 日志记录 title+keep_reason 用于审计

**不再有 4 step**：指代消解、代码筛选、分层标注合并到这一次调用的 prompt 里。

#### 6.2.2 api 模式：纯 auto 脚本

```python
def distill_api_mode(date):
    sessions = collect_sessions(date)
    sessions = [s for s in sessions if not is_noise(s)[0]]
    
    with ThreadPoolExecutor(max_workers=cfg.api.concurrency) as ex:
        futures = [ex.submit(run_llm, build_prompt(s)) for s in sessions]
        for f in as_completed(futures):
            write_topics(parse_yaml(f.result()))
```

适合：lazy trigger 后台运行、init 大批量回溯、用户已配 API key。

#### 6.2.3 host_agent 模式：任务包 + agent 消化

```python
def distill_host_agent_mode(date):
    sessions = collect_sessions(date)
    sessions = [s for s in sessions if not is_noise(s)[0]]
    
    for s in sessions:
        task_id = uuid()
        write_task_file(f".pending/{task_id}.task", {
            "session": s,
            "prompt": build_prompt(s),
            "created_at": now(),
        })
    
    # 不调 LLM，直接返回。等宿主 agent 通过 MCP 工具消化。
    return {"pending": len(sessions), "mode": "host_agent"}
```

任务包格式 `.pending/<task_id>.task`（YAML）：
```yaml
task_id: 7d3f9c1e
session_id: abc123
ide: cursor
workspace: /Users/tiger/winterfell
created_at: 2026-05-16T22:01:00
prompt: |
  你是 coding 对话蒸馏助手。请把以下对话蒸馏为...
  [完整 prompt + 原始对话]
```

宿主 agent 通过 MCP 工具循环消化（见 §6.3）：
1. `pending_distill_count()` → "你有 32 个待整理"
2. `get_next_distill_task()` → 拿到 prompt
3. agent 用自己的 LLM 跑 → 得到 YAML 结果
4. `submit_distill_result(task_id, yaml)` → 服务端落盘
5. 重复 2-4 直到 pending=0

**任务包的生命周期**：
- 创建：`.pending/<id>.task`
- 进行中：`.pending/<id>.task.in_progress`（agent 取走时改名）
- 完成：删除 `.pending/<id>.task.in_progress`
- 失败：移到 `.pending/failed/<id>.task` + 错误日志

**任务包过期**：超过 7 天未消化的自动清理（避免 .pending 无限累积）。

### 6.3 MCP Server（8 个工具）

按用途分三组：召回 / 写入 / distill 任务包消化。

#### 召回组（不需要 LLM）

```python
@mcp.tool()
def search_memory(query: str, scope: str = "auto", workspace: str = None) -> str:
    """召回个人编码知识库。

    workspace 必传 —— 由 IDE 在调用时显式传入当前打开的工作区路径。
    缺失时降级到全局召回 + 返回 warning。

    scope:
        auto    : personal + 当前 project + 跨项目高相关  ← 默认
        personal: 仅 personal
        project : 仅当前 project
        all     : 全部
    """

@mcp.tool()
def read_page(id: str) -> str:
    """按 ID 读取完整 memory 文件。"""

@mcp.tool()
def project_context(workspace: str) -> str:
    """返回当前 project 的 _index.md 浓缩摘要，
    用于 IDE 在 chat 启动时注入到 system prompt。"""
```

#### 写入组（不需要 LLM）

```python
@mcp.tool()
def remember(text: str, scope: str = "auto", tags: list[str] = None,
             workspace: str = None) -> str:
    """让用户在任意 IDE 把当前对话片段固化为 memory，立即落盘。

    TRIGGER：用户说「记住这个」「这个要记下来」「永远不要再这样做」「以后都按 X」等。
    返回：写入的文件路径，让用户知道去哪改。
    """

@mcp.tool()
def forget(id: str) -> str:
    """软删除（移到 archive/，可 restore）。"""
```

#### distill 任务包组（host_agent 模式专用）

```python
@mcp.tool()
def pending_distill_count() -> str:
    """返回待蒸馏任务数和最早任务的等待时间。

    TRIGGER：
      - 用户说「整理今天的记忆」「整理一下」「distill」「跑一遍 pipeline」
      - 用户开始新 chat 时，主动检查一次（如果 > 0，告知用户『有 N 个待整理』）

    返回示例："有 32 个待整理任务（最早的等了 2 小时）"
            或 "暂无待整理任务"
    """

@mcp.tool()
def get_next_distill_task() -> str:
    """取下一个待蒸馏任务，返回完整 prompt（含原始对话）。

    使用方式：
      1. 调本工具拿到 prompt 和 task_id
      2. 用你（宿主 agent）自己的 LLM 跑这个 prompt
      3. 把 YAML 结果通过 submit_distill_result 提交
      4. 循环直到 pending_distill_count 返回 0

    服务端会把任务标记为 in_progress，避免被其他 agent 重复领取。
    """

@mcp.tool()
def submit_distill_result(task_id: str, result_yaml: str) -> str:
    """提交某个 distill 任务的结果。

    result_yaml 必须是合法 YAML，含 topics 数组（schema 见 §6.2.1）。
    服务端会：
      - 落盘 should_keep=true 的 topic 到 personal/ 或 projects/<key>/
      - 直接丢弃 should_keep=false 的 topic（仅日志保留 title+keep_reason 审计）
      - 删除 .pending/<task_id>.task.in_progress

    返回：写入的文件路径列表。
    """
```

#### Docstring 风格原则

写**事实**而非**指令**：
- ✗ "宁可搜了没结果也不要漏召回" → 在小模型上变成无脑搜
- ✓ "用户提到他特定项目/历史经验时使用；通用编程问题不需要"

特别地，distill 任务包组的 docstring 要写清"循环消化模式"，宿主 agent 才知道这是个三步流程，不是一次性调用。

### 6.4 Lazy Trigger（采集自动化的核心）

```
MCP server 启动（每次 IDE 拉起就执行）
  ↓
读 ~/.ai-memory/.last_distill 时间戳
  ↓
距上次 > 24h ?
  ├─ 否 → 跳过
  └─ 是 → 尝试拿文件锁 ~/.ai-memory/.distill.lock (fcntl.flock)
            ├─ 拿不到（其他 IDE 已在跑）→ 跳过
            └─ 拿到 → 按 LLM mode 分两路：
                       ├─ api 模式：fork detached 子进程跑 distill --range last-Nd
                       │             ↓
                       │             并发调 LLM，跑完更新 .last_distill，释放锁
                       │
                       └─ host_agent 模式：fork 子进程做"无 LLM"的部分
                                            ├─ collect 当日 sessions
                                            ├─ 启发式过滤
                                            ├─ 生成任务包到 .pending/
                                            ├─ 更新 .last_distill（标记"已挂起"）
                                            └─ 释放锁
                                          —— 不调 LLM ——
                                          等用户在 IDE 里说"整理"时由 agent 消化
                                          （见 §6.3 distill 任务包组）

并行兜底：
  search_memory 调用时也做同样检查（覆盖"启动后开很久不动"场景）

提示通道（host_agent 模式专用）：
  当 .pending/ 非空时，project_context 工具的返回里会附带一条提示：
    "💡 你有 N 个待整理的对话（最早 2 小时前），说『整理今日记忆』即可"
  这会被 IDE 注入到 system prompt，agent 主动告知用户。
```

**关键工程细节**：
- 文件锁用 stdlib `fcntl.flock`（Unix）/ `msvcrt.locking`（Windows）
- detach 子进程：`subprocess.Popen(..., start_new_session=True)`，关 IDE 不影响
- 失败静默写日志，不弹给 IDE
- 默认 ≥ 22:00 才跑（避免 coding 高峰抢 LLM 配额或抢 agent 上下文），可配置
- host_agent 模式下 lazy trigger 很轻（无 LLM 调用），所以可以更激进地跑（每次启动都跑也可以）

### 6.5 Bootstrap Init（首次回溯）

按 LLM mode 走两条路。

#### 6.5.1 api 模式（自动跑完）

三阶段：

```
ai-memory init [--range last-7d|last-30d|all] [--budget-max 10]
  │
  ├─ Phase A：扫描 + 启发式过滤（< 30s，纯本地）
  │    输出：候选数 / 预估 LLM 调用 / 预估费用 / 预估耗时
  │
  ├─ Phase B：用户 confirm
  │    "将处理 N 个 session（其中 M 个高价值），
  │     向 <LLM API> 发送约 X 次请求，预计 ¥Y，耗时 ~Z 分钟。
  │     继续？[y/N]"
  │
  └─ Phase C：并发 distill + 写盘
       ├─ 并发 4-8 个 LLM 调用
       ├─ checkpoint 写到 .init-progress.json
       ├─ Ctrl+C 安全：状态原子写
       └─ 跑完写 .last_distill=now（避免 lazy trigger 重扫）
```

#### 6.5.2 host_agent 模式（任务包 + 用户在 IDE 里消化）

```
ai-memory init [--range ...] [--batch-size 20]
  │
  ├─ Phase A：扫描 + 启发式过滤（同 api 模式）
  │
  ├─ Phase B：用户 confirm
  │    "将创建 N 个待蒸馏任务包（不消耗外部 API）。
  │     创建后请在任意 IDE 里说『整理今日记忆』，agent 会逐个消化。
  │     继续？[Y/n]"
  │
  └─ Phase C：批量生成 .pending/<task_id>.task
       ├─ 不调 LLM
       ├─ 几秒钟完成
       └─ 提示：『下一步：打开 IDE，说『开始 init』』
```

随后用户在 IDE 里说"开始 init" / "整理记忆"：
- agent 调 `pending_distill_count` → "你有 N 个待整理"
- 用户确认（或 agent 直接开始）
- agent 循环 `get_next_distill_task → 跑 LLM → submit_distill_result`
- 进度可见：每消化 10 个，agent 报告一次"已完成 X/N"
- 中断后下次再说"继续 init"即可（任务包还在 `.pending/`）

**优点**：完全免费、无外部依赖、不用预算估算。
**缺点**：消耗用户在 IDE 里的等待时间和 agent 上下文配额；大量任务时一次 chat 装不下，要分多次。

为了避免一次 chat 撑爆 agent 上下文，CLI 提供 `--batch-size`（默认 20）：每批最多 20 个任务，agent 跑完后告知"还剩 X，下次继续"。

#### 6.5.3 共用兜底

**workspace 已不存在的兜底**：历史 session 的 workspace 可能已删/改名 → 归到 `scope: personal`，不乱猜 project。

**断点续跑**：`ai-memory init --resume` 读 progress 跳过已 done 的。

#### 6.5.4 install.sh 集成

```
是否现在初始化记忆库？

[根据 LLM mode 显示不同选项]

—— mode=api 时 ——
  1) 处理最近 7 天   （~32 session, ~¥0.8, ~3 分钟）  ← 推荐
  2) 处理最近 30 天  （~186 session, ~¥4.5, ~15 分钟）
  3) 处理全部历史   （先估算再确认）
  4) 跳过

—— mode=host_agent 时 ——
  1) 准备最近 7 天的任务包  （~32 个，免费，需要在 IDE 里消化 ~5 分钟）  ← 推荐
  2) 准备最近 30 天的任务包 （~186 个，需要分多次在 IDE 里消化）
  3) 准备全部历史的任务包  （先看具体数量再确认）
  4) 跳过
```

### 6.6 召回引擎

#### 6.6.1 当前实现（v1.6：BM25 + 时间衰减 + 可选向量重排）

```python
def search(query, scope="auto", workspace=None):
    paths = resolve_scope(scope, workspace)
    results = []
    for scope_path in paths:
        idx = bm25_index.get_index(scope_path)        # 进程级缓存，mtime 指纹
        for md_file, bm25_score in idx.scores(query)[:300]:
            fm, body = parse_fm(md_file.read_text())
            adjusted = bm25_score + per_scope_shift   # 处理小语料负 IDF
            score = (
                adjusted
                * VALUE_W[fm.value]            # high 1.5 / medium 1.0 / low 0.5
                * SOURCE_W[fm.source]          # manual 1.3 / edited 1.2 / auto 1.0
                * (0.6 if fm.potentially_superseded_by else 1.0)
                * (0.7 if cross_project else 1.0)
                * decay_weight(fm)             # 见下文
            )
    if cfg.vector_rerank_enabled:
        results = vector_rerank.try_rerank(results)   # 可选；fastembed 缺失时 no-op
    return rerank_topk(results, k=5)
```

**分词器**（`bm25_index.tokenize`）：ASCII 词正则 `[A-Za-z0-9_\-]+` + CJK 字符 bigram（"连接池配置" → `连接 / 接池 / 池配 / 配置`）。纯 stdlib，无 jieba 依赖。

**时间衰减**（`searcher._decay_weight`）：
- `source ∈ {manual, edited}` → 永远 1.0（ADR-6 人改优先）
- 否则 `weight = max(floor=0.5, 0.5 ** (age_days / half_life_days))`，默认半衰期 90 天
- 老笔记不删除、不归档，只在排序时降权；用户搜得到、靠后

**可选向量重排**：装了 `pip install '.[vector]'` 且 config 开启时启用。流程：BM25 Top 50 → fastembed encode（路径级缓存）→ cosine 与 BM25 min-max 归一化线性融合（默认 30% BM25 + 70% cosine）。

#### 6.6.2 跨项目经验迁移（关键差异点）

scope=auto 默认包含：
1. `personal/` 全部
2. `projects/<当前 project>/` 全部
3. **跨项目高相关**：其他 projects 中 tags 重合 ≥ 2 或标题 token 相似度高 的条目（仍由 `_cross_project_match` 把关，BM25 阶段后置乘 0.7 全局降权）

实现：第 3 步先用倒排（tags → file），再用 token Jaccard。BM25 阶段用同一索引；过滤在加权前完成。

#### 6.6.3 召回引擎演进路径

v1.6 一次性把 BM25 装上后，原"grep → SQLite FTS5 → embedding"三档大幅简化：

| 文件数 | 引擎 | 依赖 | 说明 |
|---|---|---|---|
| < 5000 | **rank_bm25 + 时间衰减**（当前 §6.6.1） | 纯 Python rank_bm25 + 进程级缓存 | 单次召回 < 200ms；CJK bigram 分词覆盖中文 |
| 长查询 / 语义检索 | + 本地向量重排 | `pip install '.[vector]'` → fastembed (~30MB ONNX) | 用户主动开；不绑外部 API、零账单 |
| > 5000 或 vector 缓存太大 | 增量持久化 BM25 索引 / 改 SQLite FTS5 | 实测触发后再做 | 目前未到这个量级，按需推进 |

**关键**：BM25 用 fingerprint 缓存（`scope_path` × 文件 mtime 元组哈希），fastmcp server 常驻进程一次性建索引，单文件 mtime 变化触发该 scope 整体重建（< 1k 文件级别开销可忽略）。`rank_bm25` 是纯 Python 没 C 扩展，安装零成本。

#### 6.6.4 AGENTS.md 同步（覆盖不支持 MCP 的 agent）

并不是所有 coding agent 都支持 MCP（或用户不愿配 MCP），但几乎所有主流 agent 都会读 `AGENTS.md` / `.claude/CLAUDE.md` / `.cursor/rules/`。

**做法**：把 `project_context` 工具的内容**双写**——既给 MCP 返回，也定期写入项目内的标准文件。

```
触发：
  - 项目内 distill 完成后
  - 或 lazy trigger 周期性同步（每天一次）

写入位置（按 config.yml 配置）：
  默认 enabled，写入 <project_root>/AGENTS.md
  可选额外目标：.claude/CLAUDE.md / .cursor/rules/memory.md / 自定义路径

写入方式：
  在用户文件中插入 marker 块，不覆盖 marker 外的内容
  ─────────────────────────────────────
  <!-- ai-coding-memory:start v1 -->
  ## 📚 项目记忆摘要 (auto by ai-coding-memory)
  
  > 来源：~/.ai-memory/projects/<key>/_index.md
  > 最后同步：2026-05-16 22:00:00
  
  ### 关键约定
  - winterfell 用 OfferModel，不要用 OldOfferModel
  - Redis 连接池 maxIdle ≥ 8
  
  ### 高频参考
  - 限流方案：Redisson RRateLimiter（见 ai-memory show 2026-05-16-...）
  
  > 完整记忆：用 MCP search_memory 工具，或 ai-memory ls
  <!-- ai-coding-memory:end -->
  ─────────────────────────────────────
```

**关键性质**：
- **降级通道**：用户即使没装 MCP 也能让 agent 看到项目摘要
- **零侵入**：marker 块外的用户内容完全不动
- **可禁用**：`config.yml` 里 `agents_md.enabled: false` 完全关闭
- **多文件**：可同时同步到 `AGENTS.md` + `.claude/CLAUDE.md` + `.cursor/rules/`，覆盖各 agent 的不同约定

config 示例：
```yaml
agents_md:
  enabled: true
  paths:                                # 可多选
    - AGENTS.md
    - .claude/CLAUDE.md
  max_size: 4096                        # 摘要最大字节数
  include:                              # 选哪些 memory 进摘要
    - source: manual                    # 用户手写的总是包含
    - source: edited
    - value: high                       # auto 的只取 high value
```

### 6.7 冲突检测（避免"过期规则被召回，比没记忆更危险"）

**问题**：长期使用后必然出现矛盾的 memory：
- 老 memory：`winterfell 用 OldOfferModel`
- 新 memory：`OldOfferModel 已废弃，改用 OfferModel`

如果召回时两条都返回，agent 会困惑甚至按老规则建议——这比没有记忆更糟。

#### 6.7.1 检测时机

三处写入都做检测：
- `remember(text)` 工具
- distill 落盘 should_keep=true 的 topic
- CLI `ai-memory add`

#### 6.7.2 候选冲突算法（轻量、不调 LLM）

```python
def find_conflict_candidates(new_memory) -> list[id]:
    candidates = []
    for existing in same_scope_memories(new_memory.scope, new_memory.project_key):
        # 三个信号同时命中才算候选
        if tags_overlap(existing, new_memory) >= 2 \
           and title_similarity(existing, new_memory) > 0.6 \
           and existing.id != new_memory.id:
            candidates.append(existing.id)
    return candidates
```

不强制判定"是否真冲突"——那需要 LLM。我们只标 candidates，由用户/agent 在召回时决定。

#### 6.7.3 落盘行为

新 memory 的 frontmatter 里加：
```yaml
potential_conflicts: [2026-04-22-winterfell-offermodel-old]
```

同时反向更新老 memory：
```yaml
potentially_superseded_by: [2026-05-16-winterfell-offermodel-new]
```

**不阻止写入**——避免误判。

#### 6.7.4 召回时的处理

`search_memory` 召回结果中：
- 如果命中带 `potentially_superseded_by` 的旧 memory → 在结果里标 `⚠️ 可能已被 X 替代`
- 如果命中新 memory，附带返回它的 `potential_conflicts` 列表 → 让 agent 知道"还有同主题旧条目，但被认为可能过期"

排序加权：`potentially_superseded_by` 不空的，score × 0.6 降权。

#### 6.7.5 LLM 自动确认（可选，host_agent 模式异步）

当 `potential_conflicts` 列表存在时，可以让宿主 agent 在空闲时调一个新工具：
```python
@mcp.tool()
def review_conflict(new_id: str, old_id: str) -> str:
    """让 agent 比对两条 memory，判定关系：
       - superseded（确认替代，老的归 archive）
       - complementary（互补，保留两条）
       - false_positive（误判，去掉 potential_conflicts 标记）
    """
```

这是个**完全可选**的清理动作，不做也不影响系统正常运行——只是堆积 potential 标记会让召回结果略乱。

### 6.8 CLI

```bash
# 写入
ai-memory add [--scope project|personal] [--tags ...]    # 交互式新增
ai-memory edit <id|query>                                # $EDITOR 打开

# 查询
ai-memory ls [--scope ...] [--project ...] [--since ...] [--value ...]
ai-memory show <id>                                      # cat 单条
ai-memory search <query>                                 # 同 MCP search_memory

# 管理
ai-memory archive <id>                                   # 软删
ai-memory restore <id>                                   # 从 archive/.cold 恢复
ai-memory stats                                          # 写入/召回/采纳统计

# 批量任务
ai-memory init [--range ...] [--budget-max ...] [--resume]
ai-memory distill [--range today|yesterday|...]

# 任务包消化（host_agent 模式）
ai-memory pending                        # 看 .pending/ 里有多少待消化
ai-memory pending --clear-failed         # 清理失败的任务包

# 配置
ai-memory config get [key]
ai-memory config set llm.mode host_agent|api
ai-memory config show

# 索引（数据规模触发时用）
ai-memory rebuild-index                  # 升级到 SQLite FTS5 引擎
ai-memory index-stats                    # 看当前用的引擎和索引大小

# AGENTS.md 同步
ai-memory sync-agents-md                 # 立即把项目摘要写入 AGENTS.md
ai-memory sync-agents-md --dry-run       # 预览要写的内容
```

### 6.9 隐私脱敏（v1.6 新增）

**职责**：在 distill / `remember` 落盘前对每段文本做 secret 脱敏，避免用户对话中粘贴的 token / 连接串被持久化到 markdown。**写入侧防御**，不影响已落盘内容（老文件按需用户手改）。

**入口**：`core/privacy_filter.py:redact(text) -> (text, counts)`，被以下两处调用：
- `distill/scripts/distill.py:render_prompt`：每条 conversation message 调一次；session 级命中数追加到 `~/.ai-memory/logs/redact-<date>.jsonl`（仅 type / count，**不含原文**）
- `mcp-server/server.py:remember`：用户 `text` 入参调一次；命中时返回串末尾追加 `⚠️ 已自动脱敏 N 处疑似 secret（aws_access_key, ...）`

**模式集（保守、低误伤）**：

| 类型 | 模式 | 替换为 |
|---|---|---|
| `private_key_block` | `-----BEGIN ... PRIVATE KEY-----` 整段 | `<REDACTED:private_key_block>` |
| `aws_access_key` | `AKIA[0-9A-Z]{16}` / `ASIA...` | `<REDACTED:aws_access_key>` |
| `aws_secret_key` | 40 字符 base64-like 但要求左侧含 `secret\|aws\|key` 上下文 | 保留 prefix + 占位符 |
| `openai_token` | `sk-...` / `sk-ant-...` ≥ 20 字符 | `<REDACTED:openai_token>` |
| `slack_token` / `github_token` / `jwt` | 标准前缀正则 | 同类型占位符 |
| `jdbc_password` | `(jdbc\|mysql\|postgres)://...password=VALUE` | 保留 prefix + `password=<REDACTED>` |
| `generic_secret_kv` | `(password\|secret\|api_key\|token) = VALUE` | 保留 prefix + 占位符 |

**异常零中断**：每条 pattern 的 `re.sub` 包 try/except；整体外层兜底；任何错误降级返回原文 + 空 counts，**绝不阻塞写入**。

**误伤控制**：
- AWS Secret 仅在左侧出现 `secret\|aws\|key` 上下文时命中（普通 base64 / commit sha 不动）
- generic key=value 仅命中确切的安全字段名集合（`password\|passwd\|pwd\|secret\|api_key\|access_token\|auth_token`，不含 `user_id` / `request_id` 等）
- 测试矩阵：`tests/test_privacy_filter.py` 含 11 个正例（每类 token 必命中）+ 5 个反例（commit sha / UUID / 普通代码 / 空字符串 不应被改）

**审计**：`~/.ai-memory/logs/redact-<date>.jsonl` 一行一 session，方便事后排查。**永远不写原文**——即使脱敏失败也不留底，避免审计日志反而成了泄漏面。

---

## 7. 关键决策记录（ADR）

### ADR-1：砍掉 domain 层

**问题**：domain 层依赖用户维护 yaml，与"零配置"承诺矛盾。
**决策**：只保留 personal + project 两层。
**理由**：
- 个人用户的"领域"高度重合（导购搜索 / 推荐 / 排序在用户眼里是一个事）
- 用户大概率不会维护映射表
- 跨项目召回完全可以靠 tags + 标题相似度，不需要静态 domain 表

### ADR-2：砍掉 4-step distill 流水线（但保留 Agent 编排作为执行通道）

**问题**：4 步精细处理产出的"指代消解"、"代码筛选"等中间字段，召回侧根本用不上。
**决策**：
- 合并为 1 step，LLM 一次产出最终 markdown + value + should_keep
- **保留** Agent 编排作为 host_agent LLM 模式下的执行通道（任务包形式）
- 砍掉的是"4 步分阶段消化"，不是"agent 自跑"

**理由**：
- 复杂度对齐原则：生成和召回的复杂度必须匹配
- 90% 的算力本来就花在中间产物上
- 单次调用更易并发、更易复用 prompt cache
- **修订**：早期我把"Agent 编排"整体批为反模式是单边的——只在"已有 API key"场景下成立。当用户没有 LLM provider 时，让宿主 agent 自跑是唯一合理选择。所以保留这条通道，只是把"4N 个 step"压成"N 个 task"。

### ADR-3：砍掉 llm-wiki fork

**问题**：fork 三个文件支持递归扫描，长期 rebase 债务。
**决策**：取消 fork，改用 adapter 包装上游。
**理由**：
- 改造原因（递归扫描）外部 wrapper 可解
- 去掉 fork 同时去掉 jq + node 依赖
- 我们的数据模型简化后，本来就不需要 entity / topic / source 三类 wiki 概念

### ADR-4：项目 key 用 git remote URL

**问题**：workspace basename 不稳定（同名/rename）；绝对路径不稳定（移动目录）。
**决策**：`project_key = normalize(git remote get-url origin)`，无 remote 则归 personal。
**理由**：唯一稳定标识。

### ADR-5：lazy trigger 而非 launchd/cron

**问题**：cron / launchd / QoderWork 都要用户配置且跨平台麻烦。
**决策**：MCP server 进程内做 lazy trigger，IDE 启动时检查上次跑过的时间。按 LLM mode 分两路：api 模式后台跑完整链路；host_agent 模式只跑"无 LLM"部分（collect + 过滤 + 任务包），等用户在 IDE 触发消化。
**理由**：
- memory 是 coding 行为的副产物——没 coding 就不需要 memory，IDE 启动是最自然的触发点
- 文件锁解决多 IDE 并发
- 跨平台一份代码（fcntl + Popen detach）
- 用户体验：装完即用，零配置
- host_agent 模式下 lazy trigger 的"挂任务包"动作非常轻量（无 LLM 调用），可以高频跑而不担心成本

### ADR-6：人改优先级最高

**问题**：人编辑的文件被自动 distill 覆盖会失信。
**决策**：`source: manual / edited` 永不被自动 pipeline 覆盖。
**理由**：人写的一条 ≥ AI 蒸馏的 10 条。

### ADR-7：MCP 不是唯一通道

**问题**：MCP 是 pull 模型，模型决定调不调，且 workspace 上下文脆弱。
**决策**：MCP（pull） + IDE 启动注入 system prompt（push） + CLI（人手）三通道并存。
**理由**：单一通道无法覆盖所有场景；多通道互相兜底。
**补充（host_agent 模式特有）**：MCP 还承担"任务包流转"角色——把 distill 任务从后台进程交给宿主 agent。这让 MCP 不只是召回工具，还是"借用宿主 LLM"的桥。

### ADR-8：should_keep=false 直接丢弃（修订自 v1.0：原"进冷存储"）

**问题**：LLM 一锤定音容易误杀。
**v1.0 决策**：LLM 判 false 的写 `.cold/`，不进召回索引但保留可 restore。
**v1.3 修订**（实施后调整）：直接丢弃，**不再有 `.cold/` 目录**。仅在
`logs/distill-*.log` 和 `submit_distill_result` 的 `dropped` 字段保留 title +
keep_reason 用于审计。
**修订理由**：
- 实测一周数据：12 条 cold 中 LLM 判低价值的依据看下来都合理（纯查询、文档可
  查、对话被截断、已有同主题更详细 memory），没有明显冤杀
- "用户事后会去 restore"是假设，但 cold 文件不进召回索引、不在 ls 默认输出，
  用户其实根本看不见，restore 路径接近永不被走
- restore 路径自身有 bug（`save_to_cold` 未记 `_orig_scope`，restore 后 scope 丢失），
  这条路径维护成本 > 收益
- "原始 session 始终保留在 raw/sessions/<date>.json"已是更可靠的兜底——任何
  时候都可对该 session 重新 distill 出新版本，无需 cold 备份
- 简化数据布局（少一个目录、少一个 scope 概念、stats 输出更短）

**取舍**：失去"用户后悔了想找回 LLM 判 false 的那条"这个能力。但 raw 兜底已经
覆盖；若用户真有此需求，可设 `--keep-cold` 这种 flag 或写工具从 raw 重蒸馏。

### ADR-9：默认 init range 是 dry-run 估算让用户选

**问题**：默认 7d 太保守，30d 部分用户犹豫，all 可能吓到。
**决策**：install.sh 里给三档预设（7d / 30d / all）+ 跳过；显示精确数字而非默认数字。
**理由**：透明优先于"懒人路径"——首次面对账单决策必须让用户看真实数字。

### ADR-10：LLM 是"注入的能力"，不是基础设施依赖

**问题**：把 LLM provider 当必备依赖，与"零配置"承诺直接冲突——大量用户没有 API key、不愿配 key、或在企业内只有 IDE 自带的 LLM。
**决策**：抽象 LLMProvider 接口，三档来源按优先级降级：
1. `host_agent`（默认）：宿主 agent 自跑（通过 MCP 任务包）
2. `api`（可选）：外部 OpenAI-compatible API
3. `local`（远期）：Ollama 等

`mode: auto` 的自动检测仅做一次（install 时），且**显式询问用户**（C 方案），不偷偷用 key。
**理由**：
- 零配置是硬约束，否则失去大部分目标用户
- 显式询问避免账单意外（用户可能有 key 但不想用于此场景）
- 抽象层让未来加 local 模式无需改 distill 逻辑
- 召回/写入/手编辑这条核心通路完全不依赖 LLM——即使三档全没有，工具仍然有用

### ADR-11：兼容 AGENTS.md 作为零 MCP 兜底通道

**问题**：MCP 是当前最好的跨 agent 接入层，但**不是所有 coding agent 都支持 MCP**（或用户不愿配 MCP）。如果只走 MCP，会失去一大批用户/场景。
**决策**：`project_context` 内容**双写**——既给 MCP 返回，也定期同步到 `<project>/AGENTS.md` 等标准文件，用 marker 块保护。
**理由**：
- AGENTS.md / `.claude/CLAUDE.md` / `.cursor/rules/` 是当前最广泛被自动读取的格式（开源生态共识）
- 双写是**降级冗余**：MCP 不可用时仍有摘要可读
- marker 块设计保证不破坏用户已有内容
- 这条让"跨 coding agent"承诺真正落地——不挑 agent，不挑 MCP 支持
**取舍**：增加一个写入路径要维护一致性；用 lazy trigger 周期同步（默认每天），不是每次写入都同步。

### ADR-12：轻量冲突检测，避免"过期规则被召回"

**问题**：长期使用后必然出现互相矛盾的 memory（如"用 OldOfferModel" vs "用 OfferModel"）。如果都被召回，agent 按老规则建议——这比没记忆更糟。
**决策**：写入时做轻量冲突检测（tags 重合 + 标题相似度，不调 LLM），双向标 `potential_conflicts` / `potentially_superseded_by`，召回时降权 + 提示。
**理由**：
- 不强制判定避免误判（人/agent 在召回时决定）
- 不调 LLM 让检测廉价、可同步、零账单
- LLM 自动确认（`review_conflict` 工具）作为可选清理动作
- 借鉴 agentmem 的"记忆可信度治理"思路，但简化为最小可用版本
**不做**：
- 不做"自动合并/重写"——那需要可靠的 LLM 判定，且容易破坏用户原意
- 不做"强制阻止冲突写入"——人改优先原则，写入永远成功，只标记可能冲突

### ADR-13：双 mode（daily / batch）拆分（v1.5 新增）

**问题**：单一 `llm.mode` 在以下场景产生矛盾：
- init 批量回溯（200+ session）：希望 0 配额成本 + 一次跑完 → local 最适合
- 日常增量（每天少量蒸馏）：希望即用即得 + 高质量 → host_agent 最适合
- 同一个 mode 字段被两种相反约束拉扯（v1.4 装上 daily_cap=10 + 顺手 1 条机制
  就是这种妥协的产物）

**决策**：拆 `llm.daily_mode` + `llm.batch_mode`：
- `daily_mode`（默认 `auto` → host_agent）：lazy_trigger / `ai-memory distill --range today`
  这种增量场景；优先质量与即时性
- `batch_mode`（默认 `auto` → 检测 Ollama → local，否则 host_agent）：`ai-memory init`
  这种回溯场景；优先成本与零干扰
- 旧的 `llm.mode` 字段仍可读，作为两个 mode 的兜底兼容

`distill.py` 加 `--mode-hint daily|batch`；`init` 总是 `--mode-hint batch`。
用户仍可 `--mode local|api|host_agent` 手动 override（覆盖 hint）。

**配套撤回**：v1.4 在 `mcp-server/server.py:instructions` 引入的 "**At the start of
each new chat session, call project_context ONCE before answering**" 引导废弃。
增量场景不再"顺手 1 条"，纯按需触发（用户喊『整理今日记忆』）。

**理由**：
- 一字段两场景 → 必有妥协；拆字段后两个场景各跑最适合的引擎
- v1.4 的"顺手 1 条"实测体感差（每开 chat 多 ~5s 延迟）+ instructions 听话度不一
- batch_mode=local 把"昂贵的 200 次蒸馏"从"白天的 IDE 配额"挪到"晚上的本地 CPU"，
  完美利用了 init 的"用户不急"特性

**取舍**：
- 用户首次 init 需装 Ollama + pull qwen3:8b（5.2GB）—— install.sh 仅检测，不自动装
- 16GB Mac 跑 qwen3:8b Q4_K_M 是边缘体验（M2/M3 上 25-40 tok/s，单次 30-50s），
  低于 host_agent 的 3-5s。但 batch 场景这个延迟可接受（一晚跑完）

### ADR-14：借鉴 agentmemory 的 5 项升级（v1.6 新增）

**背景**：rohitg00/agentmemory（10.6k stars，自研 iii-engine + SQLite + 三路融合检索）在召回质量、隐私边界、记忆衰减上有几个值得借鉴的点。本 ADR 决定**有选择地吸收**，而非整体复制——保持本仓库 markdown-first / 人随时改 / 零 API key 的核心承诺。

**借鉴清单（5 项必装 + 1 项可选 extras）**：

| 项 | agentmemory 方案 | 本仓库吸收形式 | 是否破坏第一性原则 |
|---|---|---|---|
| **BM25 检索** | rank_bm25 + 自家 tokenizer | rank_bm25 + ASCII/CJK bigram；零 C 扩展、纯 Python | 不破坏（取代 grep） |
| **隐私脱敏** | PostToolUse hook 内 regex strip | distill / remember 写入前 regex 脱敏（§6.9） | 不破坏（写入侧防御） |
| **时间衰减** | Ebbinghaus 自动 forget | 软重排：仅 auto/bootstrap 衰减、永不删文件、有 floor 兜底 | 不破坏（manual/edited 永远 1.0） |
| **可选向量重排** | 默认开启的 dense embedding | 默认关；`pip install '.[vector]'` 装 fastembed 后用户在 config 显式开启 | 不破坏（不绑外部 API） |
| **Citation / provenance** | SQLite citation table | frontmatter `origin` 扩展；召回结果末尾渲染 `📎 来自 ...`；缺字段静默跳过 | **强化**第一性原则（人可读、可改） |

**明确不抄**：
- ❌ **4 级合并层级（Working / Episodic / Semantic / Procedural）+ 自动矛盾消解**：复杂度爆炸；本仓库 ADR-12 的 `potentially_superseded_by` 简化版够用
- ❌ **12 个 Claude Code hook 全自动捕获**：违背 lazy distill 设计（v1.5 ADR-13 才刚刚撤回 v1.4 的"顺手 1 条"）；让用户付 IDE LLM quota
- ❌ **多 agent 协调 / P2P 同步 / 团队命名空间**：不是本仓库用户画像（个人 / 跨 IDE，不是团队 / 跨人）
- ❌ **绑死自研 runtime（iii-engine v0.11.2）**：作者自己在 README 标注 v0.11.6+ 沙箱模型变了"需要重构"，是典型"快速迭代的私有依赖把生态卡住"。本仓库坚持纯 Python + stdlib + 极少第三方包

**关键性质**：
- 5 项升级中 1/2/3/5 是必装项（默认开启），4 是可选 extras（默认关）
- 每一项都设计成"装了 / 配了才生效，不装不破坏现有行为"——升级路径平滑
- 引入新依赖：`rank_bm25>=0.2.2`（主依赖）、`fastembed>=0.3.0` + `numpy`（可选 extras）

**取舍**：
- BM25 在小语料下 Okapi IDF 可能为负——加 per-scope 平移逻辑解决（commit `df57123 fix(bm25)`）
- 隐私脱敏的正则集是保守而非详尽：宁可漏伤、不可误伤（普通代码片段 / commit sha / UUID 不命中）
- 时间衰减只对 auto/bootstrap 生效——**ADR-6 人改优先**的延伸：用户主动 remember 的笔记永远不会"自然遗忘"

---

## 8. 实施路线图

### Phase 0：减法（1 天）

砍掉：
- `compile/llm-wiki-skill/` submodule + 三个 fork 改造点
- `distill/` 4 step prompt + lib 中的 coreference / code_filter / layer_tagger
- `mcp-server/` 中的 graph 相关代码
- `compile/scripts/route_topics.py` 的 Agent 编排逻辑
- `config/` 中的 domain-mapping 相关
- design.md 标记为历史文档

退出标准：旧 pipeline 仍能跑（保留 sessions.json 输入兼容），目录瘦身。

### Phase 1：数据模型 + CLI 基础（1 天）

- 新 frontmatter schema
- `lib/project_key.py`（git remote 解析）
- `lib/memory_store.py`（读写、source 保护、archive、cold）
- CLI 4 命令：`add / edit / ls / archive`
- `source: manual` 永不覆盖逻辑

退出标准：人可脱离 distill，全 CLI 维护 memory；mtime 检测 edited 升级正确。

### Phase 2：distill 简化（1 天）

- 1-step prompt 合并
- `should_keep` + `.cold/` 逻辑
- 纯 auto，并发 LLM
- 错误重试 + 退避

退出标准：`ai-memory distill --range today` 一条命令端到端跑完。

### Phase 3：采集闭环（2.5 天）

- `lib/heuristic_filter.py`（5 条保守规则）
- `lib/llm_provider.py`（抽象层 + auto 检测 + host_agent / api 两档实现）
- MCP `remember / forget` 工具
- MCP distill 任务包组（`pending_distill_count / get_next_distill_task / submit_distill_result`）
- Lazy trigger：fcntl 锁 + detach Popen + .last_distill + 双模式分支
- `ai-memory init`：双模式（api 自动跑 / host_agent 生成任务包）
- install.sh 集成 LLM mode 询问 + 首次 init prompt
- `ai-memory config get/set`

退出标准：
- 装完即用，零外部 scheduler，零 API key 依赖
- 首次 init 在两种 mode 下都可跑通
- 多 IDE 并发不冲突
- agent 能循环消化 .pending/ 任务包

### Phase 4：召回升级（1.5 天）

- search_memory 强制 workspace 入参，缺失返回 warning
- 跨项目相关性算法（tags 重合 + 标题相似）
- `project_context` 工具
- IDE 启动时如何注入 system prompt 的对接文档
- **AGENTS.md 同步通道（ADR-11）**：marker 块写入；多目标支持；config.yml 配置
- `ai-memory sync-agents-md` CLI

退出标准：
- 在新项目能召回到老项目同主题 memory
- project_context 返回 < 2KB 浓缩摘要
- AGENTS.md 同步在不破坏用户内容前提下生效；不支持 MCP 的 agent 也能读到摘要

### Phase 5：长期管理（1 天）

- 召回反馈日志（query / hits / read_page 调用）
- 90 天衰减归档（仅 source=auto）
- `ai-memory stats`
- **轻量冲突检测（ADR-12）**：write 时算 candidates；双向标 frontmatter；召回降权 + 提示
- 可选 `review_conflict` MCP 工具

退出标准：
- 跑完 7 天 stats 数据合理
- 衰减不误伤 manual
- 冲突检测在制造矛盾的测试 case 下正确标记，不阻止写入

### Phase 6（候选，待定）：reflect / 合并

来自 Letta / Hindsight / Mem0 的概念：定期让 LLM 看一遍最近 memory，做去重/合并/升级。

目前不做，等 P5 反馈数据攒够、看真实噪音率再决定是否上。判断标准：
- 如果 90 天后 `potential_conflicts` 累积超过 10% memory 总数 → 上 reflect
- 否则人手定期 archive + 冲突检测已经够用

### Phase 7（已完成 v1.6）：借鉴 agentmemory 的检索 / 写入升级

对照 ADR-14 的 5 项借鉴清单：

- ✓ BM25Okapi + CJK bigram 分词替代 grep 评分（`mcp-server/lib/bm25_index.py` + `searcher.py`）
- ✓ 时间衰减软重排（半衰期 90d / floor 0.5；manual/edited 不衰减）
- ✓ 可选本地向量重排（fastembed / 默认关 / `pip install '.[vector]'`）
- ✓ 隐私脱敏 9 类 secret 正则（§6.9，distill + remember 写入前）
- ✓ Citation / provenance（origin 字段扩展 + 召回结果末尾 `📎 来自 ...`）
- ✓ 28 个 pytest 单测（privacy_filter / decay / bm25 tokenizer）

退出标准（已达成）：
- 端到端 30 个 scenario 自检通过（`scripts/_mcp_validation_runner.py`）
- 真实 `~/.ai-memory/` 库召回排序符合预期（manual+新 > 老 auto > 老无 origin）
- BM25 小语料负 IDF 通过 per-scope 平移修复（`fix(bm25)` commit `df57123`）

**总工时 ~10 天**（Phase 4 + 0.5 天 AGENTS.md 同步；Phase 5 + 0.5 天冲突检测；Phase 7 ~1 天 5 项借鉴）。

---

## 9. 风险与开放问题

| 风险 | 影响 | 应对 |
|---|---|---|
| **MCP server 进程 CWD 不跟随 IDE 切换 workspace** | 召回串味 | 强制工具调用带 workspace 参数；缺失返回 warning 不猜 |
| **不同 IDE 对 MCP 工具描述的 prompt-injection 处理差异** | 调用频率不可控 | docstring 写"事实而非指令"；做 A/B 监控 |
| **bootstrap init 的 LLM 账单失控** | 吓退用户 | --budget-max 硬上限；启发式预过滤；显示精确估算 |
| **隐私：对话发往云 LLM** | 误用 / 卸载 | 文档明确告知；远期支持 Ollama 本地模型 |
| **多 IDE 并发写同一 memory 文件** | 数据损坏 | 写入用 atomic rename；ID 用日期 + slug 唯一 |
| **lazy trigger 在 IDE 长期不开时不跑** | 数据滞后 | 本质上是 feature 不是 bug；CLI 可手动补跑 |
| **跨项目相关性误召回** | 噪音 | 阈值保守；用户反馈降权 |
| **用户大量手编辑后 distill "想"覆盖却不能** | 用户困惑 | distill 日志明示"跳过 X 个 manual/edited 文件" |
| **host_agent 模式下 .pending/ 堆积** | 用户忘了消化，任务越积越多 | project_context 注入提示；> 7 天自动清理；CLI `ai-memory pending` 可看 |
| **host_agent 消化中途 agent context 爆掉** | 任务半截失败 | --batch-size 限制单次 agent 任务数；submit 是单条提交，已完成部分不丢 |
| **不同宿主 agent 的 LLM 质量差异大** | distill 质量不稳定 | host_agent 模式下结果质量随 agent 而变；用户感知到差异时可切 api 模式补救 |

### 待用户/后续决策

- [x] ~~LLM provider 默认值~~ → 已决：mode=auto，无 key 时 host_agent，有 key 时 install 时显式询问（C 方案，ADR-10）
- [x] ~~召回引擎演进路径~~ → 已决：grep → SQLite FTS5 → embedding 三档阶梯（§6.6.3）
- [x] ~~是否兼容 AGENTS.md~~ → 已决：兼容，作为零 MCP 兜底通道（ADR-11）
- [ ] **是否上 P6 reflect**：等 P5 上线后看冲突累积率决定
- [ ] `_index.md` 是 LLM 自动维护还是定期重建：维护成本 vs 实时性
- [ ] host_agent 模式下任务包是否要"按相似度排序"让 agent 先消化高价值的：可能优化但增复杂度
- [ ] AGENTS.md 同步是否要支持模板化（让用户控制摘要格式）：用户呼声决定

---

## 10. 与原 design.md 的对照

| 原 design.md | 本 redesign.md |
|---|---|
| 5 stage（collect/distill/compile/recall + Agent 编排） | 3 stage（采集 / 蒸馏 / 召回）+ LLM Provider 抽象层 |
| 3 层（project / domain / general） | 2 层（personal / project） |
| 4-step distill | 1-step distill |
| Agent 编排是默认且唯一通路 | host_agent 模式（任务包）+ api 模式（auto），按 LLM Provider 选择 |
| llm-wiki fork + submodule | 无 fork，去掉 wiki 概念栈 |
| graph + Louvain + jq + node | 无图谱，纯 grep + 跨项目相关性 |
| QoderWork 定时任务 | MCP 内置 lazy trigger（按 mode 分两路） |
| 无 remember 写入通道 | MCP `remember` 工具 |
| 无首次回溯 | `ai-memory init` 双模式 |
| 无人编辑保护 | source 字段 + mtime 升级 |
| 无召回反馈 | recall 日志 + BM25 + 时间衰减软重排（v1.6） |
| **强依赖 LLM provider** | **零依赖：宿主 agent 即 LLM** |
| 仅 MCP 一条召回通道 | MCP + AGENTS.md 双通道（兼容不支持 MCP 的 agent） |
| 无冲突治理 | 写入时轻量冲突检测 + 召回降权（避免过期规则反召回） |
| 纯 grep 召回 | BM25Okapi（CJK bigram 分词）+ 时间衰减 + 可选本地向量重排（v1.6，ADR-14） |
| 写入无脱敏 | distill / remember 写入前 9 类 secret 正则脱敏（v1.6，§6.9） |
| 召回无溯源 | 每条结果末尾渲染 `📎 来自 <ide> · <date> · session ...`（v1.6） |

---

## 附录 A：用户场景剧本

### 场景 1：跨 IDE 共享（aha #1）

```
用户在 Cursor:
> "我刚发现 winterfell 的 Redis 连接池配置必须 maxIdle ≥ 8，否则高峰会断。
   记住这个。"

AI: [调 remember]
> "✓ 已记住，保存为 projects/github.com_xxx_winterfell/2026-05-16-redis-pool-maxidle.md"

—— 30 分钟后切到 Aone Copilot ——

用户:
> "winterfell 的 Redis 连接池怎么配？"

AI: [自动调 search_memory（workspace=winterfell）]
> "📑 找到你 30 分钟前的记录：maxIdle 必须 ≥ 8，否则高峰会断（path: ...maxidle.md）"
```

### 场景 2：跨项目经验迁移（aha #3）

```
用户在新项目 falcon：
> "Spring Boot 怎么配 Redis 连接池？"

AI: [search_memory，scope=auto]
> "📑 你在 winterfell 项目里记过相关经验：maxIdle ≥ 8 否则断连
   📑 你在 redis-evalsha-pattern 通用笔记里记过：用 evalSha 不要 eval
   要应用这些经验到 falcon 吗？"

—— 用户 3 个月前在另一个项目踩的坑，今天在新项目自动避开了 ——
```

### 场景 3：人手编辑（aha #4）

```
$ ai-memory edit "winterfell offer model"
[$EDITOR 打开 projects/.../2026-04-22-offermodel-naming.md]

用户改了正文，加了一句"特别注意：discount 字段是 BigDecimal 不是 double"
保存退出

—— 系统检测到 mtime 变化，自动把 source: auto 升级为 source: edited ——
—— 之后任何 distill 都不会再覆盖这个文件 ——

下次 AI 问到 OfferModel 时召回，BigDecimal 这条会一同返回。
```

### 场景 4：首次安装（api 模式）

```
$ ./install.sh
... [省略前 4 步] ...

[5/6] LLM 配置

  检测到 OPENAI_API_KEY 环境变量。

  你想怎么用 LLM？
    1) 仅用宿主 agent（推荐，零成本，零配置）
    2) 启用 api 模式（自动后台运行，会产生账单）
    3) 我自己看着办

  选择 [1]: 2

  ✓ 已写入 ~/.ai-memory/config.yml （mode: api）

[6/6] 是否现在初始化记忆库？

  扫描结果：1247 个历史 session
    Cursor:        823
    Aone Copilot:  312
    Claude Code:   112

  请选择处理范围：
    1) 最近 7 天    → 32 session, 预计 ~¥0.8, ~3 分钟  [推荐]
    2) 最近 30 天   → 186 session, 预计 ~¥4.5, ~15 分钟
    3) 全部历史    → 先估算再确认
    4) 跳过

  选择 [1]: 1
  确认开始？[Y/n] Y

  [████████████████] 32/32  ✓ 2min 47s

  ✓ 写入 21 条 memory（personal: 8, projects: 13）
  ✓ 冷存储 11 条（LLM 判低价值，可 ai-memory restore）
```

### 场景 5：首次安装（host_agent 模式，零配置）

```
$ ./install.sh
... [省略前 4 步] ...

[5/6] LLM 配置

  未检测到任何 LLM API key。
  默认使用 host_agent 模式：你在 IDE 里说"整理今日记忆"时，
  IDE 自己的 AI 会用它的 LLM 跑蒸馏。
  ✓ 装完即用，零成本

[6/6] 是否现在初始化记忆库？

  扫描结果：1247 个历史 session

  请选择处理范围：
    1) 最近 7 天的任务包  （32 个，需要在 IDE 里消化 ~5 分钟）  [推荐]
    2) 最近 30 天的任务包 （186 个，需要分多次消化）
    3) 全部历史的任务包  （先看具体数量再确认）
    4) 跳过

  选择 [1]: 1
  ✓ 已生成 32 个待蒸馏任务包到 ~/.ai-memory/.pending/

  下一步：
    打开任意 IDE，开始一个新对话，说一句：
      "开始 init"  或  "整理今日记忆"

—— 用户打开 Claude Code ——

用户：整理今日记忆

Claude：[调 pending_distill_count]
        发现 32 个待整理任务（最早的 1 分钟前）。我开始消化第一批 20 个。
        
        [循环 20 次：get_next_distill_task → 跑 LLM → submit_distill_result]
        
        ✓ 已完成 20/32：
          写入 12 条 memory，冷存储 8 条
        还剩 12 个未处理，要继续吗？

用户：继续

Claude：[再消化 12 个]
        ✓ 全部完成！写入 21 条，冷存储 11 条。
        现在你可以在任意 IDE 问问题，看看召回效果。
```

### 场景 6：日常使用（host_agent 模式）

```
—— Day N+1 早上 ——

打开 Cursor （MCP server 启动 → lazy trigger 检查）
后台进程：collect 昨日 sessions → 启发式过滤 → 生成 8 个任务包
（不调 LLM，几秒完成）

—— 用户开始 coding ——

用户：帮我看看这个函数能不能优化
Cursor：[调 search_memory] [调 project_context]
        ...回答中...
        
        💡（来自 project_context 的提示）你有 8 个待整理的对话（昨晚生成），
            说一句"整理一下"我可以帮你消化。

用户：整理一下
Cursor：[8 次循环，每次调 get_next_distill_task → 自跑 → submit]
        ✓ 已整理完，新增 5 条 memory（其中 1 条标记为 high value）
```

---

*文档结束。*
*本设计完成后，design.md 将被归档为 design.legacy.md，仅作历史参考。*
