# ai-coding-memory 设计文档 v0.3 [DEPRECATED]

> ⚠️ **本文档已废弃，仅作历史归档参考。**
> 当前权威设计：[`docs/redesign.md`](redesign.md) (v1.2 起)
>
> 主要废弃原因：
> - 5 stage / 3 层 / 4-step distill / Agent 编排手工消化 等设计与"零配置跨 agent memory"目标不匹配
> - 强依赖 LLM provider，无法兼容企业内/无 API key 用户
> - llm-wiki fork + graph + Louvain 复杂度远超个人量级所需
>
> 详细对照见 redesign.md §10。

## 0. 变更说明（vs v0.2）

| 关键变化 | 说明 |
|---|---|
| **职责重新划分** | distill 降级为"预处理器"，不做知识抽取（交给 llm-wiki） |
| **集成方式变更** | 从 git submodule 改为 **git fork + submodule**（需要改造分层目录支持） |
| **复用 crystallize** | distill 输出的 topic 块通过 llm-wiki 的 `crystallize` 工作流入库（不重复造轮子） |
| **数据流简化** | distill → topic 文件 → llm-wiki crystallize → wiki 页面 |

---

## 1. 设计目标

打造**零配置、自动化、跨 IDE、有分层意识**的个人 AI 编码记忆系统。

**核心理念**：不是记录"做了什么"，而是沉淀"学到了什么"。

---

## 2. 核心设计原则

| 原则 | 说明 |
|---|---|
| **不重复造轮子** | llm-wiki 已有的能力（知识抽取/实体管理/图谱/缓存）直接复用 |
| **职责单一边界清晰** | 我们只做 llm-wiki 不做的：采集、对话预处理、分层召回 |
| **三层知识分层** | project / domain / general，召回时按 IDE 上下文过滤 |
| **代码三级处理** | 决策性保留 / 教学性保留+注释 / 过程性丢弃（在 distill 阶段做） |
| **写读解耦** | 写入靠定时任务，读取靠 MCP Server，通过文件系统通信 |

---

## 3. 整体架构

```
┌──────────────────────────────────────────────────────┐
│  调度层：QoderWork 每日定时任务 (22:00)              │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│  Stage 1: collect 📥  (我们实现，复用 daily-summary) │
│  - 扫描 Aone Copilot / Cursor / Qoder 对话           │
│  - 输出：~/.ai-memory/raw/sessions/YYYY-MM-DD.json   │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│  Stage 2: distill 🧪  (我们实现，预处理器定位)       │
│  - Step 2.1: 主题切分（session → 多个 topic 块）     │
│  - Step 2.2: 指代消解（"这个" → 具体名称）           │
│  - Step 2.3: 代码筛选（决策性/教学性/过程性）        │
│  - Step 2.4: 分层标注（project/domain/general）      │
│  - 输出：~/.ai-memory/raw/topics/YYYY-MM-DD/         │
│           ├── 001-{scope}-{title}.md                 │
│           └── 002-{scope}-{title}.md                 │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│  Stage 3: ingest 📚  (llm-wiki-skill fork)           │
│  - 对每个 topic 文件调用 crystallize 工作流          │
│  - 根据 topic 文件 frontmatter 的 scope 决定写入路径 │
│  - 输出：~/.ai-memory/wiki/                          │
│    ├── projects/winterfell/{entities,topics,...}     │
│    ├── domains/guidance-search/...                   │
│    └── general/java/...                              │
└──────────────────────┬───────────────────────────────┘
                       ↓
                  ~/.ai-memory/wiki/
                       ↑
                       │ MCP 召回（带分层过滤）
                       │
┌──────────────────────────────────────────────────────┐
│  Stage 4: recall 🔌  (我们实现，MCP Server)          │
│  - 自动识别当前 IDE workspace                        │
│  - 按分层规则过滤召回                                │
│  - 工具：search_memory / read_page / list_topics     │
└──────────────────────┬───────────────────────────────┘
                       ↑
            ┌──────────┼──────────┬──────────┐
            │          │          │          │
         Cursor   Aone Copilot  Qoder   Claude Code
```

---

## 4. 完整目录结构

```text
ai-coding-memory/                       # 我们的主仓库
├── SKILL.md                            # 主入口
├── README.md                           # 团队使用文档
├── install.sh                          # 一键安装脚本
├── .gitmodules                         # 注册 fork 的 llm-wiki-skill
│
├── config/
│   ├── default.yml                     # 默认配置
│   └── domain-mapping.example.yml      # domain 映射示例
│
├── collect/                            # 📥 Stage 1
│   ├── SKILL.md
│   └── scripts/
│       ├── extract_sessions.py         # 复用 daily-coding-summary 逻辑
│       └── lib/
│           ├── aone_extractor.py
│           ├── cursor_extractor.py
│           └── qoder_extractor.py
│
├── distill/                            # 🧪 Stage 2（预处理器）
│   ├── SKILL.md
│   ├── prompts/
│   │   ├── 01_topic_segmentation.md
│   │   ├── 02_coreference.md
│   │   ├── 03_code_filter.md
│   │   └── 04_layer_tagging.md
│   └── scripts/
│       ├── distill.py                  # 编排脚本
│       └── lib/
│           ├── topic_segmenter.py
│           ├── coreference_resolver.py
│           ├── code_filter.py
│           └── layer_tagger.py
│
├── compile/                            # 📚 Stage 3
│   ├── SKILL.md
│   ├── llm-wiki-skill/                 # ⭐ git submodule (我们的 fork)
│   │                                   # fork 自 sdyckjq-lab/llm-wiki-skill
│   │                                   # 改造点：支持分层目录扫描
│   └── scripts/
│       └── crystallize_topics.sh       # 遍历 distilled topics 调用 crystallize
│
├── mcp-server/                         # 🔌 Stage 4
│   ├── server.py                       # FastMCP 入口
│   ├── pyproject.toml
│   ├── README.md
│   └── lib/
│       ├── searcher.py                 # grep + index 召回
│       ├── scope_resolver.py           # 分层过滤
│       └── workspace_detector.py       # 识别当前 IDE workspace
│
└── workflows/
    ├── qoderwork-daily.yml             # 定时任务模板
    └── manual-trigger.sh               # 手动触发脚本
```

---

## 5. distill 模块详细设计

### 5.1 关键定位

distill **不做知识抽取**。它只负责让对话流变成 llm-wiki 能高质量消化的素材。

### 5.2 输入输出契约

**输入**：`raw/sessions/YYYY-MM-DD.json`

```json
{
  "timeRange": { "start": "...", "end": "..." },
  "sessions": [
    {
      "ide": "aone-copilot",
      "sessionId": "abc",
      "workspace": "/Users/tiger/winterfell",
      "conversation": [
        { "role": "user", "content": "..." },
        { "role": "assistant", "content": "..." }
      ]
    }
  ]
}
```

**输出**：`raw/topics/YYYY-MM-DD/001-{scope}-{slug}.md`

```markdown
---
type: distilled-topic
date: 2026-04-25
session_id: abc
ide: aone-copilot
workspace: /Users/tiger/winterfell
scope: project
project: winterfell
domain: null
general_category: null
tags: [rate-limit, redis]
knowledge_type: decision
bug_category: null
correction_count: 1
quality:
  has_conclusion: true
  has_code: true
  estimated_value: high
source_msg_range: [10, 22]
---

# 限流方案选型与 Redis lua 脚本优化

## 💡 决策推理链
（备选方案、最终选择及理由、否决理由）

## ⚡ 用户纠正记录
（AI 犯错 → 用户纠正 → 正确答案）

## 对话（已消解指代）
（指代消解后的完整自描述对话，纠正处有 ⚡ CORRECTION 标记）

## 关键代码
（保留的 decision/educational 代码，附 reusable_pattern 标签）

## 已丢弃过程性代码
（丢弃方案 → 失败原因）
```

**新增字段说明**：

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `knowledge_type` | enum | Step 1 | 知识类型：decision / bugfix / tribal_knowledge / new_learning / implementation / qa |
| `bug_category` | enum \| null | Step 4 | 仅 bugfix 类填写：concurrency / config / null-handling / api-misuse / performance 等 |
| `correction_count` | int | Step 1 + Step 2 | 用户纠正 AI 的次数，0 表示无纠正 |

**新增正文段落**：

| 段落 | 来源 | 说明 |
|---|---|---|
| `## 💡 决策推理链` | Step 1 decision_rationale | 仅 knowledge_type=decision 时生成 |
| `## ⚡ 用户纠正记录` | Step 1 corrections | 仅 correction_count > 0 时生成 |
| `> ⚡ CORRECTION:` 标记 | Step 2 coreference | 在对话正文中标记纠正发生位置 |
| `reusable_pattern` 标签 | Step 3 code_filter | 附在关键代码标题后，如 `pattern:distributed-rate-limiting` |
| 失败原因增强 | Step 3 discarded_summary | 丢弃的代码必须说明"为什么失败/被放弃" |

### 5.3 四个 Prompt 的完整设计

每个 prompt 都是可直接 copy 到 LLM 的完整模板。所有 prompt 共享如下约束：
- 输出必须是合法 JSON（除非显式说明输出 Markdown）
- 所有判断附带 `confidence`（0.0-1.0）和 `reasoning`（一句话理由）
- 不确定时优先保守（estimated_value=low / scope=general）

#### `01_topic_segmentation.md`（主题切分）

```text
你是对话分析专家。请分析以下编码对话，识别"话题切换点"，把单个 session 切分为若干自洽的 topic 块。

【判定切分的标准】
- 用户问题转向了完全不同的技术领域 → 切分
- 用户开始处理新文件/新模块 → 切分
- 时间间隔 > 30 分钟且话题无关联 → 切分
- 长度超过 8 轮但话题连贯 → 不切分

【estimated_value 评级标准】
- high: 包含明确的技术决策 / 解决了真实 bug / 学到新知识
- medium: 完成了具体编码任务，但未沉淀方法论
- low: 简单问答 / 文档查询 / 重复劳动
- noise: 闲聊 / 工具调试 / 无价值内容（直接丢弃）

【输入】
workspace: {workspace}
session_started_at: {session_start_time}
conversation: 
{messages_with_index}

【输出格式（必须是合法 JSON 数组）】
[
  {
    "topic_id": 1,
    "title": "≤ 20 字的话题概括",
    "start_msg_idx": 0,
    "end_msg_idx": 12,
    "summary": "一句话概括（≤ 50 字）",
    "estimated_value": "high|medium|low|noise",
    "confidence": 0.85,
    "reasoning": "切分理由"
  }
]

【约束】
- topic_id 从 1 开始连续递增
- start_msg_idx / end_msg_idx 必须覆盖所有消息（不重叠、不遗漏）
- 单个 session 至少 1 个 topic
- 所有 estimated_value=noise 的 topic 在后续步骤会被直接丢弃
```

#### `02_coreference.md`（指代消解）

```text
你是对话改写专家。重写以下对话，使其完全自描述（任何后续读者无需上下文也能看懂）。

【消解规则】
- "这个模块" / "它" / "这块" → 替换为具体名称
- "上面那个方案" → 引用具体方案的关键词（如"基于 Redis 的限流方案"）
- "之前说的" → 找到具体内容补全
- "这里" → 替换为具体类名/方法名/文件路径

【保留】
- 保留原始问答的逻辑结构（user / assistant 交替）
- 保留用户的真实表达情绪（如"这个不行"、"我懵了"）
- 保留代码块原文（代码块的处理在下一步）

【输入】
workspace: {workspace}
topic_title: {topic_title}
topic_dialogue:
{topic_messages}

【输出格式（Markdown，user/assistant 交替）】
**用户**：（重写后的提问，所有指代已消解）

**AI**：（重写后的回答，所有指代已消解）

**用户**：...

【约束】
- 不得新增信息（只能消解，不能编造）
- 若某个指代实在无法消解（上下文不足），保留原文并加 `[ref-unresolved]` 标记
- 输出末尾追加一行：`[coreference_confidence: 0.X]`
```

#### `03_code_filter.md`（代码筛选）

```text
你是代码筛选专家。分析对话中的所有代码片段，按"未来可复用价值"做三级分类。

【三级分类标准】

🟢 **decision（决策性，保留全文）**
满足以下任一：
- 是最终采用的方案代码
- 体现了关键技术决策（如"为什么用 A 不用 B"的实现）
- 是修复 bug 的最终方案
- 长度 < 50 行

🟡 **educational（教学性，保留 + 加注释）**
满足以下任一：
- API 用法示范（标准库 / 框架的典型用法）
- 设计模式的具体实现示例
- 长度 < 30 行
- 必须为代码添加 `// 关键点：xxx` 注释，说明该片段的"教学价值"

🔴 **process（过程性，丢弃）**
满足以下任一：
- 中间尝试方案（已被否决）
- AI 给出的草稿后被纠正
- 长度 > 50 行的实现细节（强制压缩为摘要）

【输入】
topic_title: {topic_title}
topic_dialogue: {dialogue_with_code_blocks}

【输出格式（必须是合法 JSON）】
{
  "kept_snippets": [
    {
      "tier": "decision",
      "language": "java",
      "code": "RateLimiter limiter = ...",
      "annotation": "最终采用：基于 Redisson 的分布式限流",
      "source_msg_idx": 18
    },
    {
      "tier": "educational",
      "language": "java",
      "code": "// 关键点：复用 sha 避免每次重新加载脚本\nString sha = redisConnection.scriptLoad(luaScript);\nredisConnection.evalSha(sha, ...);",
      "annotation": "Redis evalSha 标准用法",
      "source_msg_idx": 14
    }
  ],
  "discarded_summary": "丢弃了 2 段过程性代码：（1）初版同步限流方案（被并发问题推翻）；（2）AI 草稿，使用 Guava RateLimiter 但不支持分布式",
  "filter_confidence": 0.9
}

【约束】
- educational 类必须为代码加 `// 关键点：xxx` 注释
- process 类不输出代码，只在 discarded_summary 中描述"做了什么 + 关键 API"
- 若所有代码都是 process 类，kept_snippets 为空数组
```

#### `04_layer_tagging.md`（分层标注）

```text
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
{
  "scope": "project|domain|general",
  "project": "winterfell",          // 当 scope=project 时必填，否则 null
  "domain": null,                    // 当 scope=domain 时必填，否则 null
  "general_category": null,          // 当 scope=general 时必填（如 "java" / "redis" / "debugging"）
  "tags": ["rate-limit", "redis"],   // 3-5 个搜索关键词
  "confidence": 0.9,
  "reasoning": "包含 winterfell 的具体业务实体 OfferModel"
}

【约束】
- general_category 必须是预定义类别之一：java / python / typescript / redis / mysql / debugging / ai-tools / git / shell / system-design / 其他归为 misc
- tags 用小写连字符（kebab-case）
- 当 scope=general 时，project 和 domain 必须为 null
```

### 5.4 distill 编排逻辑

`distill/scripts/distill.py` 主流程：

```python
"""
distill.py - 把 raw/sessions/{date}.json 清洗为 raw/topics/{date}/*.md

输入：  ~/.ai-memory/raw/sessions/YYYY-MM-DD.json
输出：  ~/.ai-memory/raw/topics/YYYY-MM-DD/NNN-{scope}-{slug}.md
失败模式：
  - LLM 调用失败 → 该 session 整体跳过，记录到 errors.log
  - JSON 解析失败 → 重试 2 次，仍失败则按低置信度输出原始素材
  - 单个 step 失败 → 标记 confidence=0.3，仍输出（避免阻塞）
"""

from lib.topic_segmenter import segment_topics
from lib.coreference_resolver import resolve_coreference
from lib.code_filter import filter_code
from lib.layer_tagger import tag_layer
from lib.paths import RAW_SESSIONS_DIR, RAW_TOPICS_DIR
from lib.llm_client import LLMClient
from lib.io_utils import load_json, write_topic_file, append_error_log

def distill_daily(date: str, dry_run: bool = False, verbose: bool = False) -> dict:
    """主入口：清洗指定日期的所有 session"""
    raw_path = RAW_SESSIONS_DIR / f"{date}.json"
    out_dir = RAW_TOPICS_DIR / date
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = load_json(raw_path)
    llm = LLMClient()
    topic_idx = 1
    stats = {"total_sessions": 0, "total_topics": 0, "skipped_noise": 0, "errors": 0}

    for session in raw["sessions"]:
        stats["total_sessions"] += 1
        try:
            topics = segment_topics(session, llm)
            for topic in topics:
                if topic["estimated_value"] == "noise":
                    stats["skipped_noise"] += 1
                    continue

                resolved = resolve_coreference(topic, session["workspace"], llm)
                code_result = filter_code(resolved, llm)
                layer = tag_layer(resolved, session["workspace"], llm)

                if not dry_run:
                    write_topic_file(
                        out_dir=out_dir,
                        topic_idx=topic_idx,
                        layer=layer,
                        title=topic["title"],
                        dialogue=resolved,
                        code_snippets=code_result["kept_snippets"],
                        discarded=code_result["discarded_summary"],
                        metadata={
                            "session_id": session["sessionId"],
                            "ide": session["ide"],
                            "workspace": session["workspace"],
                            "source_msg_range": [topic["start_msg_idx"], topic["end_msg_idx"]],
                            "estimated_value": topic["estimated_value"],
                        },
                    )
                topic_idx += 1
                stats["total_topics"] += 1
        except Exception as e:
            stats["errors"] += 1
            append_error_log(out_dir / "errors.log", session["sessionId"], e)
            if verbose:
                print(f"[ERROR] session {session['sessionId']}: {e}")
            continue  # 单个 session 失败不阻塞

    return stats
```

---

## 6. compile 模块详细设计

### 6.1 职责

把 distill 产出的 topic 文件批量喂给 llm-wiki-skill 的 `crystallize` 工作流。

### 6.2 关键脚本：`crystallize_topics.sh`

```bash
#!/bin/bash
# crystallize_topics.sh - 把 distilled topics 批量喂给 llm-wiki crystallize
#
# 用法：./crystallize_topics.sh [YYYY-MM-DD]
# 不传日期则默认今天。
#
# 失败模式：
#   - 单个 topic 失败不阻塞其他（continue）
#   - 失败信息追加到 ~/.ai-memory/wiki/.compile-errors.log

set -e
DATE="${1:-$(date +%Y-%m-%d)}"
DATA_ROOT="${HOME}/.ai-memory"
TOPICS_DIR="${DATA_ROOT}/raw/topics/${DATE}"
WIKI_ROOT="${DATA_ROOT}/wiki"
ERROR_LOG="${WIKI_ROOT}/.compile-errors.log"
LLM_WIKI_DIR="$(cd "$(dirname "$0")/.." && pwd)/llm-wiki-skill"

if [ ! -d "$TOPICS_DIR" ]; then
    echo "[INFO] No topics for $DATE, skip."
    exit 0
fi

mkdir -p "$WIKI_ROOT"

success=0
failed=0
for topic_file in "$TOPICS_DIR"/*.md; do
    [ -f "$topic_file" ] || continue

    # 解析 frontmatter（取 --- 之间的内容）
    scope=$(awk '/^---$/{f=!f;next} f && /^scope:/{print $2; exit}' "$topic_file")
    project=$(awk '/^---$/{f=!f;next} f && /^project:/{print $2; exit}' "$topic_file")
    domain=$(awk '/^---$/{f=!f;next} f && /^domain:/{print $2; exit}' "$topic_file")
    general_cat=$(awk '/^---$/{f=!f;next} f && /^general_category:/{print $2; exit}' "$topic_file")

    # 决定目标知识库子目录
    case "$scope" in
        project)
            sub_root="$WIKI_ROOT/projects/$project"
            ;;
        domain)
            sub_root="$WIKI_ROOT/domains/$domain"
            ;;
        general)
            sub_root="$WIKI_ROOT/general/$general_cat"
            ;;
        *)
            echo "[WARN] $topic_file 缺少有效的 scope，归入 general/misc"
            sub_root="$WIKI_ROOT/general/misc"
            ;;
    esac

    mkdir -p "$sub_root"

    # 若该子知识库未初始化，先 init（llm-wiki-skill 的初始化脚本）
    if [ ! -f "$sub_root/.wiki-schema.md" ]; then
        echo "[INFO] init sub-wiki: $sub_root"
        bash "$LLM_WIKI_DIR/scripts/init-wiki.sh" "$sub_root" || true
    fi

    # 调用 llm-wiki crystallize（cd 到对应子目录，让它把当前目录作为知识库根）
    pushd "$sub_root" > /dev/null
    if bash "$LLM_WIKI_DIR/scripts/crystallize.sh" "$topic_file"; then
        success=$((success + 1))
    else
        failed=$((failed + 1))
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED: $topic_file" >> "$ERROR_LOG"
    fi
    popd > /dev/null
done

echo "[DONE] success=$success failed=$failed (date=$DATE)"
```

### 6.3 llm-wiki-skill 的改造点（fork 后修改）

需要修改 fork 中的以下文件，支持分层目录递归扫描：

| 文件 | 改造点 |
|---|---|
| `scripts/lint-runner.sh` | 把 `wiki/{entities,topics,sources}/*.md` 改为 `find wiki -type d -name entities -o -name topics -o -name sources` 后递归扫描 |
| `scripts/build-graph-data.sh` | 把硬编码路径替换为 `find` 递归 + 在节点 metadata 中携带 `scope` 字段 |
| `scripts/source-signal-coverage.js` | Node.js 脚本，把 `fs.readdirSync(WIKI_DIR)` 改为递归遍历 |

改造工作量：约 3 小时。**改造原则**：保持上游 API 不变，仅扩展扫描范围，便于未来 rebase 上游。

---

## 7. 三层数据存储结构

```
~/.ai-memory/
├── raw/
│   ├── sessions/                    # collect 输出
│   │   └── 2026-04-25.json
│   └── topics/                      # distill 输出
│       └── 2026-04-25/
│           ├── 001-project-winterfell-rate-limit.md
│           ├── 002-domain-guidance-ranking.md
│           └── 003-general-java-stream.md
│
├── wiki/                            # llm-wiki crystallize 产出
│   ├── projects/
│   │   ├── winterfell/
│   │   │   ├── _index.md
│   │   │   ├── purpose.md
│   │   │   ├── .wiki-schema.md
│   │   │   ├── entities/
│   │   │   ├── topics/
│   │   │   ├── sources/
│   │   │   └── synthesis/sessions/
│   │   └── other-project/
│   ├── domains/
│   │   └── guidance-search/
│   │       ├── _index.md
│   │       ├── entities/
│   │       └── ...
│   └── general/
│       ├── java/
│       │   ├── _index.md
│       │   ├── entities/
│       │   └── ...
│       ├── debugging/
│       └── ai-tools/
│
└── config/
    └── domain-mapping.yml
```

**关键设计**：每个 `projects/X/`、`domains/Y/`、`general/Z/` 都是一个**独立的 llm-wiki 知识库**，有自己的 schema 和 purpose。

---

## 8. MCP Server 详细设计

### 8.1 工具列表

```python
@mcp.tool()
def search_memory(query: str, scope: str = "auto") -> str:
    """搜索个人编码知识库。
    
    TRIGGER: 用户提及"以前"、"上次"、"我记得"、"之前怎么处理"，
            或问题涉及他特定的项目/经验时。
    
    DON'T TRIGGER: 通用编程知识问题（如"Java HashMap 是什么"）。
    """

@mcp.tool()
def read_page(path: str) -> str:
    """读取知识库具体页面的完整内容。"""

@mcp.tool()
def list_topics(scope: str = "auto") -> str:
    """列出知识库主题索引（仅在用户主动询问时调用）。"""
```

### 8.2 自动 scope 解析（完整实现）

```python
"""
scope_resolver.py - 根据当前 IDE workspace 解析召回范围

输入：workspace 绝对路径（如 /Users/tiger/winterfell）
输出：dict{include_paths, project, domain}
失败模式：
  - workspace 为空 → 返回 all（召回全部）
  - domain_mapping.yml 不存在 → 仅返回 project + general
"""

from pathlib import Path
import yaml
from .paths import WIKI_ROOT, DOMAIN_MAPPING_PATH

def resolve_scope(workspace_path: str | None, mode: str = "auto") -> dict:
    """根据 workspace 和 mode 解析召回路径列表"""
    if mode == "all":
        return {"include_paths": [str(WIKI_ROOT)], "project": None, "domain": None}

    if not workspace_path:
        return {"include_paths": [str(WIKI_ROOT / "general")], "project": None, "domain": None}

    project_name = Path(workspace_path).name
    mapping = _load_domain_mapping()

    # 在 domain mapping 表里查找当前 project 的归属
    domain = None
    for d_name, d_config in mapping.get("domains", {}).items():
        if project_name in d_config.get("projects", []):
            domain = d_name
            break

    paths = []
    if mode in ("auto", "current_project"):
        paths.append(str(WIKI_ROOT / "projects" / project_name))
    if mode in ("auto", "domain") and domain:
        paths.append(str(WIKI_ROOT / "domains" / domain))
    if mode in ("auto", "general"):
        paths.append(str(WIKI_ROOT / "general"))

    return {
        "include_paths": [p for p in paths if Path(p).exists()],
        "project": project_name,
        "domain": domain,
    }


def _load_domain_mapping() -> dict:
    """加载 domain mapping，不存在时返回空配置"""
    if not DOMAIN_MAPPING_PATH.exists():
        return {"domains": {}}
    with open(DOMAIN_MAPPING_PATH) as f:
        return yaml.safe_load(f) or {"domains": {}}
```

### 8.3 检索引擎（完整实现）

```python
"""
searcher.py - 在指定 scope 路径下做分层召回

策略：
  1. 优先在每个 scope 的 _index.md 摘要中召回（高分 +10）
  2. 全文 grep 兜底（仅 entities/ 和 topics/，避免 sources 噪声）
  3. 按分数重排去重，返回 Top 5

性能预算：< 1s（pure grep + 文件 IO，无 LLM 调用）
"""

import re
import subprocess
from pathlib import Path

INDEX_FILE_NAME = "_index.md"
SEARCHABLE_SUBDIRS = ("entities", "topics", "synthesis")
TOP_K = 5


def search_with_scope(query: str, scope_paths: list[str]) -> list[dict]:
    """在多个 scope 路径下检索并合并结果"""
    if not query.strip():
        return []

    results = []

    # 第一层：_index.md 摘要召回（高优先级）
    for scope_path in scope_paths:
        index_file = Path(scope_path) / INDEX_FILE_NAME
        if index_file.exists():
            for match in _grep_in_file(query, index_file):
                results.append({
                    "source": "index",
                    "path": str(index_file),
                    "snippet": match["snippet"],
                    "line": match["line"],
                    "score": 10 + match["match_count"],
                })

    # 第二层：entities / topics / synthesis 全文 grep
    for scope_path in scope_paths:
        for sub in SEARCHABLE_SUBDIRS:
            sub_dir = Path(scope_path) / sub
            if not sub_dir.exists():
                continue
            for md_file in sub_dir.rglob("*.md"):
                for match in _grep_in_file(query, md_file):
                    results.append({
                        "source": "fulltext",
                        "path": str(md_file),
                        "snippet": match["snippet"],
                        "line": match["line"],
                        "score": match["match_count"],
                    })

    return _rerank_and_dedupe(results)[:TOP_K]


def _grep_in_file(query: str, file_path: Path) -> list[dict]:
    """在单个文件内做大小写不敏感 grep，返回每个匹配行及上下文"""
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches = []
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    for idx, line in enumerate(lines):
        hits = pattern.findall(line)
        if not hits:
            continue
        # 上下文窗口 ±2 行
        ctx_start = max(0, idx - 2)
        ctx_end = min(len(lines), idx + 3)
        snippet = "\n".join(lines[ctx_start:ctx_end])
        matches.append({
            "line": idx + 1,
            "snippet": snippet,
            "match_count": len(hits),
        })
    return matches


def _rerank_and_dedupe(results: list[dict]) -> list[dict]:
    """按 (path, line) 去重，按 score 降序排"""
    seen = {}
    for r in results:
        key = (r["path"], r["line"])
        if key not in seen or seen[key]["score"] < r["score"]:
            seen[key] = r
    return sorted(seen.values(), key=lambda x: -x["score"])
```

---

## 9. install.sh 完整实现

```bash
#!/bin/bash
# install.sh - ai-coding-memory 一键安装脚本
#
# 做什么：
#   1. 初始化 git submodule（fork 的 llm-wiki-skill）
#   2. 创建数据目录（~/.ai-memory/...）
#   3. 复制默认配置
#   4. 安装 Python 依赖（FastMCP、PyYAML 等）
#   5. 检查 llm-wiki-skill 的系统依赖（jq、node）
#   6. 把 MCP 配置注入到 Cursor / Aone Copilot / Qoder
#
# 失败模式：
#   - submodule 拉取失败 → 提示用户检查 git/网络
#   - pip 安装失败 → 提示用户切换镜像源
#   - 系统依赖缺失（jq/node）→ 给出 brew 安装命令但不退出
#   - MCP 配置注入失败（IDE 未安装）→ 仅警告，不退出

set -e

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- 路径常量 ----
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="${HOME}/.ai-memory"
DEFAULT_CONFIG="${PROJECT_ROOT}/config/domain-mapping.example.yml"
USER_CONFIG="${DATA_ROOT}/config/domain-mapping.yml"

info "🚀 Installing ai-coding-memory at ${PROJECT_ROOT}"

# ---- Step 1: 初始化 submodule ----
info "📦 Initializing submodules (forked llm-wiki-skill)..."
cd "$PROJECT_ROOT"
if [ -f .gitmodules ]; then
    git submodule update --init --recursive || {
        error "submodule 拉取失败，请检查 .gitmodules 中的 fork URL 和网络"
        exit 1
    }
else
    warn ".gitmodules 不存在，跳过（首次开发阶段正常）"
fi

# ---- Step 2: 创建数据目录 ----
info "📁 Creating data directories at ${DATA_ROOT}..."
mkdir -p "${DATA_ROOT}/raw/sessions"
mkdir -p "${DATA_ROOT}/raw/topics"
mkdir -p "${DATA_ROOT}/wiki"
mkdir -p "${DATA_ROOT}/config"
mkdir -p "${DATA_ROOT}/logs"

# ---- Step 3: 复制默认配置 ----
if [ ! -f "$USER_CONFIG" ]; then
    if [ -f "$DEFAULT_CONFIG" ]; then
        cp "$DEFAULT_CONFIG" "$USER_CONFIG"
        info "📝 Created default config: ${USER_CONFIG}（请按需编辑）"
    else
        warn "默认配置模板不存在：${DEFAULT_CONFIG}"
    fi
else
    info "📝 配置已存在，跳过：${USER_CONFIG}"
fi

# ---- Step 4: 安装 Python 依赖 ----
info "🐍 Installing Python dependencies..."
if [ -f "${PROJECT_ROOT}/mcp-server/pyproject.toml" ]; then
    if command -v uv >/dev/null 2>&1; then
        (cd "${PROJECT_ROOT}/mcp-server" && uv sync) || warn "uv sync 失败，请手动运行"
    else
        pip3 install --user pyyaml fastmcp || warn "pip 安装失败，请手动运行 pip3 install pyyaml fastmcp"
    fi
fi

# ---- Step 5: 检查系统依赖 ----
info "🔍 Checking system dependencies..."
for cmd in jq node python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        warn "缺少 ${cmd}，请运行: brew install ${cmd}"
    fi
done

# ---- Step 6: 注入 MCP 配置 ----
info "🔌 Configuring MCP servers..."
if [ -f "${PROJECT_ROOT}/scripts/inject_mcp_config.py" ]; then
    for ide_config in \
        "${HOME}/.cursor/mcp.json" \
        "${HOME}/.aone_copilot/mcp.json" \
        "${HOME}/Library/Application Support/Qoder/User/mcp.json"; do
        ide_dir=$(dirname "$ide_config")
        if [ -d "$ide_dir" ]; then
            if python3 "${PROJECT_ROOT}/scripts/inject_mcp_config.py" \
                --target "$ide_config" \
                --project-root "$PROJECT_ROOT" 2>/dev/null; then
                info "  ✓ Configured: $ide_config"
            else
                warn "  ✗ 注入失败: $ide_config"
            fi
        fi
    done
else
    warn "MCP 注入脚本未实现（Phase 4），跳过"
fi

echo ""
info "✅ Installation complete!"
echo ""
echo "📋 Next steps:"
echo "  1. 编辑 ${USER_CONFIG} 配置你的领域映射"
echo "  2. 在 QoderWork 中导入 workflows/qoderwork-daily.yml 设置定时任务"
echo "  3. 重启 IDE 让 MCP Server 生效"
echo "  4. 试运行：python3 ${PROJECT_ROOT}/collect/scripts/extract_sessions.py --range today"
```

---

## 10. 开发实施计划

### Phase 1: 骨架 + collect（本周末，3 小时）
- [x] 创建目录结构
- [ ] Fork sdyckjq-lab/llm-wiki-skill 到自己的 GitHub
- [ ] 把 fork 加入 submodule
- [ ] 实现 collect 模块（移植 daily-coding-summary 的 extract_sessions.py）
- [ ] 写 install.sh 基础版本

### Phase 2: distill 核心（下周，4 小时）
- [ ] 实现 4 个 prompt 模板
- [ ] 实现 distill.py 编排脚本
- [ ] 验证：用昨日数据手动跑

### Phase 3: compile 集成 + llm-wiki fork 改造（下周，3 小时）
- [ ] 改造 fork 的 lint-runner.sh 支持递归扫描
- [ ] 改造 fork 的 build-graph-data.sh 支持递归
- [ ] 改造 fork 的 source-signal-coverage.js 支持递归
- [ ] 实现 crystallize_topics.sh
- [ ] 端到端跑通：collect → distill → compile

### Phase 4: MCP Server（再下周，3 小时）
- [ ] 实现 FastMCP server.py
- [ ] 实现 scope_resolver 和 searcher
- [ ] 写 inject_mcp_config.py 配置注入脚本
- [ ] 在 Cursor/Aone Copilot/Qoder 中验证

### Phase 5: 自动化 + 文档（一周后，2 小时）
- [ ] QoderWork 定时任务模板
- [ ] 写 README.md 团队文档

---

## 11. 关键风险与应对

| 风险 | 应对 |
|---|---|
| **distill 用 IDE LLM，定时任务时 IDE 不在线** | 通过 QoderWork 唤起 IDE 执行；或手动触发 |
| **LLM 抽取质量不稳定** | prompt 强约束 + JSON Schema 校验 + 复用 llm-wiki 的 confidence 机制 |
| **代码片段误丢** | 保留 `source_msg_range` 链接到原对话，可回溯 |
| **分层判定错误** | confidence < 0.6 时归入 general 兜底 |
| **llm-wiki 上游更新冲突** | fork 定期 rebase，改造点尽量集中在少数几个文件 |
| **MCP Server 性能** | _index.md 摘要召回 + 全文 grep 兜底，控制 < 1s |

---

## 12. Harness Engineering 原则（项目级约束）

本项目所有代码必须遵循以下原则，以保证可维护性和 AI 友好：

### 12.1 单一入口、清晰契约
- 每个模块有独立的 `SKILL.md` 描述能力和触发条件
- 模块间数据通过文件系统通信（JSON / Markdown），契约写在 `SKILL.md` 中

### 12.2 文件即接口
```
collect ─→ raw/sessions/YYYY-MM-DD.json
distill ─→ raw/topics/YYYY-MM-DD/*.md
compile ─→ wiki/{projects,domains,general}/.../**.md
```

### 12.3 可观测性
- 所有脚本支持 `--verbose` 输出详细日志
- 所有脚本支持 `--dry-run` 预演
- 每个 stage 有结构化错误归类（not_installed / runtime_failed / empty_result）

### 12.4 容错优雅降级
- LLM 调用失败 → 回退到原始素材直接 ingest
- 单个 session 失败不阻塞其他 session
- distill 任一 step 失败 → 标记低置信度但仍输出

### 12.5 AI 友好的代码组织
- 每个 .py / .sh 文件头部有简明 docstring：做什么 / 输入 / 输出 / 失败模式
- 函数命名动词化：`segment_topics` / `resolve_coreference` / `filter_code` / `tag_layer`
- 配置外置：`config/default.yml` + `config/domain-mapping.yml`
- 路径常量集中：`lib/paths.py` 避免散落
- 类型注解：所有 Python 函数签名带类型注解

### 12.6 团队推广导向
- `install.sh` 一键完成所有配置
- 错误信息友好可操作（"请运行 X 命令修复"）
- README 给"5 分钟上手"路径

---

## 13. 附录：关键决策记录

| 决策点 | 选择 | 理由 |
|---|---|---|
| 项目命名 | `ai-coding-memory` | 简洁清晰，团队成员一眼懂 |
| llm-wiki 集成方式 | git fork + submodule | 需要改造扫描脚本支持分层目录 |
| MCP Server 语言 | Python + FastMCP | 生态最成熟，与现有脚本统一 |
| 检索引擎 | grep + _index.md 摘要 | 零依赖，团队推广友好 |
| distill 定位 | 预处理器（不做知识抽取） | 不重复造 llm-wiki 的轮子 |
| 对话沉淀方式 | 调用 llm-wiki crystallize | 复用现成能力 |
| LLM 调用方式 | 复用当前 IDE 的 LLM | 零额外配置，无需 API key |
| 分层判定 | workspace 自动 + LLM 校准 + domain 映射表 | 精度+可控 |
| 代码处理策略 | 三级分类（decision/educational/process） | 避免噪声污染知识库 |
