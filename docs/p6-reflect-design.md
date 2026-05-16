# ai-coding-memory · P6 设计：reflect / 合并机制

> 适用范围：P0–P5 已落地，作为 redesign.md §8 Phase 6（候选）的落地方案。
> 状态：**设计草案 v0.1**，待用户确认是否采纳推荐档位再实施。
> 关联：redesign.md §3.5 / §6.7 / §8 Phase 6 / §9；ADR-6 / ADR-10 / ADR-12。

---

## 1. 问题陈述

### 1.1 P5 已经做了什么

P5（commit `1124d1b`）落地了**轻量冲突检测**（ADR-12）：

- 写入时算 `find_conflict_candidates`（同 scope+project 内，tag 重合 ≥ 2 或 title token Jaccard ≥ 0.40）
- 双向标 `potential_conflicts` / `potentially_superseded_by`
- 召回时 `potentially_superseded_by` 不空 → `score × 0.6` 降权，并在结果里标 `⚠️ 可能已被 X 替代`
- 落盘永不阻塞（人改优先 / ADR-6）

P5 还有 **90 天 decay**（仅 source=auto/bootstrap，未召中即归档）和 **recall_log** 反馈。

### 1.2 P5 留下的 4 类局限

| 类别 | 现象 | 影响 |
|---|---|---|
| **L1 假阳性堆积** | 候选算法保守，但长期使用必然累积"标了但其实不冲突"的对 | 召回结果越来越多 `⚠️` 标记，狼来了 |
| **L2 真冲突未确认** | 老条目永远是"potentially superseded"，永远不进 archive | 召回结果里降权后仍占位、消耗 token |
| **L3 重复未识别** | 候选算法只看 tag/title，**同主题不同措辞**的两条（"用 redis pipeline" vs "redis 批量调用要 pipeline"）抓不到 | 召回 N 条说同一件事 |
| **L4 碎片未合并** | 同一主题分多次对话各落一条 memory（碎片），各自不完整 | 用户读到的是"切片视图"，无整体性 |

P5 的 `_mark_superseded` 只写标记不删，`auto_decay` 只看冷启动未召回，无人在中间做语义裁决。**这正是 reflect 要补的位置。**

### 1.3 为什么不在写入时直接做（继续走 ADR-12 路线就行）

写入路径是热路径：
- `remember(text)` MCP 工具同步返回
- `submit_distill_result` 是 agent loop 的一环
- 多 IDE 并发触发

热路径调 LLM 比对会：(a) 拖慢同步返回；(b) 触发 host_agent 模式下的**递归任务包**（distill 任务包正在跑时再写 reflect 任务包）；(c) 让"冲突即拒写"诱惑变大，违反 ADR-6。

所以 reflect 必须是**异步、批处理、可回退**的离线动作。

---

## 2. 三种开源方案要点对比

> 以下基于训练知识（截至 2026-01）。若需准确性，建议在落地前再 spot-check 一次最新版本。

### 2.1 Letta（前 MemGPT）——"sleep-time agents"

- **思想**：交互 agent 在和用户对话，背后有一个或多个 sleep-time agent 共享同一组 memory block，**离线时**遍历对话历史 + archival memory，调 `core_memory_replace` / `rethink_memory` 等工具修订 core memory
- **触发**：session 结束 / 定时 / 显式调用
- **作用域**：对 core memory 做**重写**（不是只标记）
- **可借鉴**：
  - "在线写入路径不调 LLM 做合并"的解耦思想 → 本工具早已遵守（ADR-12）
  - sleep-time agent 用**和交互 agent 同一个 LLM** 的设计，正好对应我们的 `host_agent` 模式：reflect 也是一个任务包让宿主 agent 跑
  - 工具粒度（rethink_memory / finish_rethinking_memory）的结构化输出
- **不照搬**：Letta 的 core memory 是单一 block，会被整体重写；我们是 N 条独立 .md 文件，要做的是**多条之间的关系决策**而不是单 block rewrite

### 2.2 Mem0——"ADD / UPDATE / DELETE / NONE" 决策

- **两阶段**：
  1. extract：从新 messages 抽 candidate facts
  2. update：对每条 fact + top-k 相似存量 memory 一起喂 LLM，输出 action ∈ {ADD, UPDATE, DELETE, NONE}
- **关键点**：决策**在写入路径上**做，每条 memory 都过一遍；UPDATE 会重写 existing memory 的文本
- **可借鉴**：
  - **4 个动作的枚举**就是 reflect 的天然输出 schema（我们扩成 5 个：KEEP_BOTH / SUPERSEDE / MERGE / DROP_NEW / FALSE_POSITIVE）
  - 让 LLM 看"候选对"而不是全量库——和我们 ADR-12 算出的 candidates 天然契合
- **不照搬**：
  - 写入路径调 LLM 违反 ADR-12 / ADR-10（host_agent 无法同步）
  - UPDATE 直接覆盖 body 在 source=manual/edited 时会破坏 ADR-6 → 需加保护

### 2.3 Hindsight（hindsight-ai 或同类 reflect-on-trajectory 项目）

> 我对该具体仓库的细节把握度低。如下是对"reflect 阶段"这类项目的通用描述。

- **思想**：对完成的 agent 轨迹做"事后诸葛亮"式总结，提炼 lesson learned，可能合并成更高层的 memory
- **可借鉴**：
  - 把 reflect 视为**和原对话同等地位的"二次蒸馏"**——这条思路若走 C 档（全量重生成）刚好对应
- **不照搬**：本工具的 memory 已经过 1-step distill，对单条再 reflect 边际收益低；只有"跨条" reflect 才有意义

### 2.4 三家方法学的共同结构

```
candidate_pairs ──▶ LLM 决策 ──▶ {keep / update / merge / drop} ──▶ store mutation
                                                                       ↑
                                                          需 dry-run + 审计日志
```

我们的 P6 就照这个骨架走，只是**把"candidate 发现"完全复用 P5 已经写好的 `find_conflict_candidates`**（零外部依赖），把"LLM 决策"塞进 host_agent 任务包通道。

---

## 3. 三档落地路径

### 路径 A：仅"确认 superseded"（最小代价，~1 天）

**一句话**：让 LLM 看 P5 已标好的 `(new_id, old_id)` 候选对，二选一确定关系 → 老的归档 or 撕掉标记。**不动 body**。

#### 3.A.1 触发时机

- 主动：CLI `ai-memory reflect [--dry-run] [--scope ...] [--max-pairs N]`
- 半自动：lazy trigger 每周触发一次（条件：`pending_superseded_pairs > 5` 且距上次 reflect > 7 天）
- MCP：新增 `pending_reflect_count()` / `get_next_reflect_task()` / `submit_reflect_result()` 三件套（仿 distill 任务包）
- 不在 `remember` / `submit_distill_result` 热路径触发

#### 3.A.2 候选对枚举

```
扫所有 memory，收集 (new_id, old_id) ∈ {(m.id, x) | m.potential_conflicts has x}
按 (新 ID 创建时间, 旧 ID 创建时间) 排序，截 top-N 装入任务包
去重：同一对只生成一个 task
排除：已存在 reflect_log 里 verdict ∈ {SUPERSEDED, FALSE_POSITIVE} 的对
```

#### 3.A.3 任务包 prompt 草案

```
你是「coding memory 冲突仲裁助手」。下面是两条被自动算法标为「可能冲突」的 memory，
请判断它们的关系。

[NEW] (created={...}, value={...}, source={...})
---
<新 memory 完整 frontmatter + body>
---

[OLD] (created={...}, value={...}, source={...})
---
<旧 memory 完整 frontmatter + body>
---

判定枚举（必选一个）：
- SUPERSEDED       : 新条目确实替代旧条目（旧的结论/方案/字段已过期）
- FALSE_POSITIVE   : 算法误判，两条说的是不同事
- COMPLEMENTARY    : 都成立但角度不同（如规则 vs 示例），保留两条
- UNCERTAIN        : 信息不足，需人裁

输出（YAML，外层只允许 verdict 字段）：
verdict: SUPERSEDED | FALSE_POSITIVE | COMPLEMENTARY | UNCERTAIN
reason : "≤ 60 字，给人看的"
confidence: high | medium | low
```

#### 3.A.4 提交后的 store 变更

| verdict | 动作 |
|---|---|
| SUPERSEDED | `archive(old_id)`（移到 `archive/`，frontmatter `archived: true`，新条目 frontmatter 的 `potential_conflicts` 移除 old_id；写 reflect_log） |
| FALSE_POSITIVE | 新条目 `potential_conflicts -= [old]`；旧条目 `potentially_superseded_by -= [new]`；reflect_log 标 false_positive 防止下次再问 |
| COMPLEMENTARY | 同上去标记，并在两条 body 各追加一行 `> 关联：见 <对方 id>` |
| UNCERTAIN | **不动数据**，只写 reflect_log，pending 人手裁决 |

保护：`source ∈ {manual, edited}` 的条目永不被 archive；verdict=SUPERSEDED 时若旧条目是 manual/edited → 自动降级为 COMPLEMENTARY，并加 reflect_log warning。

#### 3.A.5 与现有模块的集成点

- **memory_store.py**：新增 `clear_superseded_pair(new_id, old_id) -> bool`、`apply_reflect_verdict(pair, verdict, reason) -> ReflectResult`；archive 复用已有 `archive(memory_id)`
- **task_pack.py**：泛化文件后缀，加 `.reflect.task`；`take_next()` 加 type filter；或新增并列模块 `reflect_pack.py` 走 `.reflect/` 子目录（推荐——隔离更清晰）
- **server.py**：新增 `pending_reflect_count` / `get_next_reflect_task` / `submit_reflect_result` 三个 tool
- **llm_provider.py**：api 模式时直接 sync 跑；host_agent 模式写任务包

#### 3.A.6 代价 vs 收益

- 代价：~1 天工时（~250 行代码 + 1 个 prompt）；零 body 改写风险
- 收益：消化 L2（真冲突未确认）；通过 FALSE_POSITIVE 反馈把 L1（假阳性堆积）也清掉
- **不解决** L3（重复未识别）和 L4（碎片未合并）——这两个是 B/C 档的事

---

### 路径 B：主动合并（中代价，~3 天）

**一句话**：周期性扫**所有** memory，用语义相似（不止 tag/title）找重复簇，让 LLM 把每簇合并成"更全的一条"。

#### 3.B.1 触发时机

- 主动 only：CLI `ai-memory reflect --mode merge [--scope ...] [--budget N]`
- 节奏建议:人手月度 / 累计 > 200 条新增 / 用户感到"搜出来重复了"
- **不入 lazy trigger**——成本高、风险大、要用户在场

#### 3.B.2 簇发现（不调 LLM）

P5 的两信号在召回侧已经够用，但合并必须更准。两条阶梯：

1. **当前阶梯（FTS5 / grep 之上）**：扩展 `find_conflict_candidates`：
   - 同 scope + 同 project（personal 跨项目）
   - tag 重合 ≥ 1 **且** title token Jaccard ≥ 0.30
   - **加** body 前 200 字的 trigram-Jaccard ≥ 0.25（新增）
2. **远期阶梯**：等召回引擎升级到 embedding 后，复用 embedding 算 cosine ≥ 0.80

簇 = 用 union-find 把候选对连通。每簇上限 5 条（超过裁掉低 value 的或最旧的），防止 prompt 爆掉。

#### 3.B.3 任务包 prompt 草案

```
你是「coding memory 合并助手」。下面是 N 条被算法判为同主题的 memory，
请决定如何合并。

[MEMORIES]
- [M1] id=... created=... value=... source=...
       <body>
- [M2] ...
- [Mk] ...

约束：
- 若任一条 source ∈ {manual, edited}：禁止合并，verdict=KEEP_ALL
- 优先保留信息密度大、有具体代码/字段名的条目
- 时间新的优先（除非旧条目明显更完整）
- 合并后 body 不超过原 max(len(Mi)) × 1.2

输出（YAML）：
verdict: MERGE | KEEP_ALL | UNCERTAIN
keep_ids:   [...]   # 合并时空；KEEP_ALL 时全列出
archive_ids:[...]   # 被合并掉的旧 id
merged:
  title: ...
  body:  |
    ...
  tags:  [...]
  value: high|medium|low
  source_ids: [...]   # 这条 merged 是从哪些原条目来的（审计）
reason: "..."
```

#### 3.B.4 提交后的 store 变更（最重要——必须 dry-run）

```
verdict=MERGE:
  1. 写新 memory(source="auto", merged_from=archive_ids, body=merged.body, ...)
  2. 对每个 archive_id：archive() + 在 archive frontmatter 加 merged_into=new_id
  3. 写 reflect_log {action: merge, in: [...], out: new_id, agent_reason: ...}

verdict=KEEP_ALL:
  撕掉簇内所有 (a, b) 的 potential_conflicts / potentially_superseded_by 标记
  写 reflect_log {action: keep_all, ids: [...]}

verdict=UNCERTAIN:
  打 reflect_log {action: uncertain}，下次同簇不再问（人裁决）
```

新增 frontmatter 字段：
- `merged_from: [<id>, ...]`（在新合并条上）
- `merged_into: <id>`（在被归档条上）
- `reflected_at: 2026-05-16T22:01:00`（任何被 reflect 处理过的条目都打）

#### 3.B.5 dry-run（强制）

`ai-memory reflect --mode merge --dry-run` 必须先跑一遍，输出：

```
将处理 12 个簇（含 35 条 memory）:
  簇#1: 4 条 → 1 条 (archive 3)
    will merge:
      - 2026-04-22-redis-pipeline-old   (auto, medium)
      - 2026-04-30-redis-batch-tips     (auto, medium)
      ...
  ...
预估 LLM 消耗: ~12 次调用, ~25K tokens
继续执行？[y/N]
```

#### 3.B.6 与现有模块的集成点

- 新增 `core/reflect.py`（簇发现 + verdict 应用 + dry-run renderer）
- `memory_store.py`：新增 `apply_merge_verdict(pair_or_cluster, verdict_payload) -> MergeResult`
- 新 frontmatter 字段需登记到 `_to_frontmatter_dict`
- `reflect_pack.py` 任务包（同 A 但 prompt 不同）
- 新增 CLI 子命令 `reflect`：`--dry-run / --mode supersede|merge / --max-pairs N / --budget ...`

#### 3.B.7 代价 vs 收益

- 代价：~3 天工时；**有风险**（合并写错会丢信息，必须 dry-run + reflect_log 可回退）
- 收益：消化 L3 + L4，body 一致性提升
- 风险：LLM 编造 merged.body 中没出现过的内容；mitigation：要求 source_ids 必须覆盖所有 archive_ids，且新 body 字符数 ≤ Σ原 body × 0.9（合并应是压缩而不是扩写）

---

### 路径 C：全量重生成（最大代价，~5+ 天，**不推荐为 P6 首选**）

**一句话**：把一段时期（如一个 scope+project 下近 N 条）当作素材，让 LLM 重新组织出一份"更好"的 memory 集合替换全部。

#### 3.C.1 思路

- 输入：某 project 下所有 source=auto 的 N 条（≤ 50） + 所有 manual/edited 作为"硬约束"
- LLM 输出：重组后的 M 条新 memory，每条标注 source_ids
- 结果原子替换该 scope+project 下所有 auto 条目；manual/edited 全保留

#### 3.C.2 为什么不推荐为 P6 首选

| 维度 | C 的痛点 |
|---|---|
| 复杂度对齐（§3.5） | 召回侧仍是 grep/FTS5，做这种级别的重组前面再精细召回侧用不上 |
| ADR-6 | manual/edited 一旦数量上涨，"硬约束"就让重组空间趋零，等于白干 |
| ADR-10 | host_agent 模式下，一个任务包可能 50+ 条 memory，超出常见 IDE agent 单轮上下文上限 |
| 回退 | 整批替换出错时，回退依赖完整快照（archive_replace_set），实现成本高 |
| 边际价值 | 大多数收益（去重/合并）B 档已覆盖；C 档多给的是"全局重排"，对个人 memory 来说价值低 |

可考虑作为 P7+ 远期选项，等到：
- 召回侧升级到 embedding
- 用户主动反馈"我的 memory 整体乱了，想推倒重来某 scope"

---

## 4. 推荐方案：先 A，再考虑 B（不做 C）

**P6.1（强推荐，必做）= 路径 A**：

理由：
1. **最贴合 ADR-12 增量**：P5 已经写好 candidate 算法，A 只是"把 superseded 标记升级成确认归档"，是 P5 的最小自然延伸
2. **最贴合 ADR-6 + ADR-10**：只动归档/标记，不改 body；host_agent 任务包是 1-对-1 prompt，单轮上下文绰绰有余
3. **回退便宜**：archive 是 reversible 的（`restore <id>` 已存在）
4. **闭环 P5 的判断标准**：redesign.md §8 写明"如果 90 天后 potential_conflicts 累积超过 10% memory 总数 → 上 reflect"——A 直接消化这个累积量
5. **能做 stats 决策**：reflect_log 跑 1-2 个月后看 (FALSE_POSITIVE 占比) 决定要不要调 P5 候选算法阈值

**P6.2（条件触发，待 A 跑 1-2 月后看数据再定）= 路径 B**：

只有当下列任一指标命中才上：
- A 跑完后，库里仍有 > 15% memory 是"同 tag + 高 title 相似"但 A 判 COMPLEMENTARY 的（说明真有"碎片"需要合并）
- 用户主动反馈"搜出来的太碎"
- 召回引擎已经升级到 FTS5 或 embedding（B 的簇发现质量依赖更强的相似度信号）

**路径 C 不进 P6 路线图**，记入 §9 待定列表。

---

## 5. 与 P5 冲突检测的衔接

### 5.1 不重复的工作

| P5 已有 | P6 复用 |
|---|---|
| `find_conflict_candidates` (memory_store.py:186) | **直接复用**：reflect 候选源 = 所有 `m.potential_conflicts` 不空的对 |
| 双向标记 `potential_conflicts` / `potentially_superseded_by` | **直接读取**：不再算第二次 |
| 召回降权 `score × 0.6` | **保持**：reflect 后 archive 的自然消失；FALSE_POSITIVE 撕标记后恢复原权重 |
| `_mark_superseded` 写入 | **不改**：reflect 是消费者不是生产者 |
| `auto_decay`（90 天未召中 + auto/bootstrap） | **互补**：decay 看"没人用就删"，reflect 看"语义已被替代"；两者顺序应是 reflect 先跑（消化标记）→ decay 再跑（清未用尾巴） |

### 5.2 需要新加的数据字段

| 字段 | 写在哪 | 含义 | 兼容性 |
|---|---|---|---|
| `reflected_at: <iso>` | 任何被 reflect 处理过的 memory | 防止重复 reflect 同一条；统计用 | 老条目无此字段不影响 |
| `reflect_verdict: SUPERSEDED \| ...` | 仅被 archive 的（在 archive/ 下） | 审计：为什么进 archive | 老 archive 无此字段不影响 |
| `merged_from: [<id>, ...]` | B 档生成的合并条 | 溯源 | A 档不用 |
| `merged_into: <id>` | B 档被合并掉的（在 archive/ 下） | 反向溯源 | A 档不用 |

A 档只用 `reflected_at` + `reflect_verdict`，对 frontmatter schema 影响最小。

### 5.3 reflect_log（新增）

文件：`~/.ai-memory/logs/reflect-YYYY-MM-DD.jsonl`

```json
{"ts":"2026-05-16T22:01:03","action":"superseded","new":"...","old":"...","reason":"...","confidence":"high"}
{"ts":"2026-05-16T22:01:09","action":"false_positive","pair":["...","..."],"reason":"..."}
{"ts":"2026-05-16T22:05:00","action":"merge","in":["...","..."],"out":"...","reason":"..."}
{"ts":"2026-05-16T22:10:00","action":"uncertain","pair":["...","..."]}
```

用途：
- 防止再次问同一对（uncertain / false_positive 进黑名单）
- 回退依据（按 ts 反向 replay）
- `ai-memory stats --reflect` 展示

---

## 6. host_agent 模式下 reflect 怎么跑

### 6.1 是否新增 MCP 任务包类型

**是**。**新增 3 个 MCP tool**，与 distill 三件套并列（不混用 `.pending/`）：

```python
@mcp.tool()
def pending_reflect_count() -> str: ...
@mcp.tool()
def get_next_reflect_task() -> str: ...
@mcp.tool()
def submit_reflect_result(task_id: str, result_yaml: str) -> str: ...
```

理由：
- distill 任务包是"对话 → memory"的 1-to-N，reflect 任务包是"memory pair → verdict"的 1-to-1，schema 完全不同
- 让 agent 能分别说"我先消化 distill，再消化 reflect"
- task_pack.py 仍可复用，新增 `reflect_pack.py` 用独立目录 `.reflect-pending/` + 不同 suffix，避免污染 distill 队列

### 6.2 任务包流转（仿 distill）

```
.reflect-pending/<task_id>.task                待消化
.reflect-pending/<task_id>.task.in_progress    取走
.reflect-pending/<task_id>.task.failed         失败
（成功 → 删除）
```

任务包 JSON 增加 `task_type: "reflect_supersede" | "reflect_merge"`（B 档用 merge）；其余结构同 distill task。

### 6.3 user-visible 触发动线（host_agent）

```
$ ai-memory reflect --max-pairs 20
✓ 生成 12 个 reflect 任务包到 .reflect-pending/
→ 请在任意 IDE（Cursor/Claude Code/Aone）让宿主 agent 跑：
   "请运行 reflect 任务包"
  agent 会 loop 调用 get_next_reflect_task / submit_reflect_result 直到清空
```

可被 `pending_reflect_count` 在 `project_context` 启动注入里附带（"你有 12 个 reflect 待消化，问用户要不要顺手清"），但**不强制 auto-loop**——保持人对全过程可见。

### 6.4 api 模式（有 key 的用户）

api 模式同步跑：`ai-memory reflect` 直接调 LLM，无任务包，无 IDE 介入。复用 `llm_provider.run(prompt)`，并发受 `api_concurrency` 控制。

---

## 7. 触发节奏建议

| 触发方式 | 推荐档位 | 何时启用 |
|---|---|---|
| **手动 CLI** | 默认开 | 始终可用，主入口 |
| **每周 lazy trigger** | 仅 A 档 | 上线 1 个月后，确认 false-positive 率 < 20% |
| **累计阈值触发** | 仅 A 档 | `potential_conflicts` 对数 > 30 时，在 lazy trigger 跑时顺带生成任务包 |
| **用户手动 B 档** | always 手动 | B 永远只通过 CLI 显式触发，不自动 |
| **集成进 `project_context` 启动注入** | 仅"count 提示" | 不自动 run，只提醒"有 N 个待 reflect" |

**绝不**做：每次 `remember` / `submit_distill_result` 触发 reflect（违反 §1.3 决定）。

---

## 8. 失败与回滚

### 8.1 dry-run（强制 for B、可选 for A）

- A 档：`ai-memory reflect --dry-run` 只列将处理的对 + 预估 LLM 调用次数，**不写任务包**
- B 档：dry-run 是默认行为，必须显式加 `--apply` 才真跑，**且 --apply 仍要二次确认 y/N**

### 8.2 reflect 改错了怎么办

| 错误类型 | 回滚动作 |
|---|---|
| A 档 archive 错 | `ai-memory restore <id>` 已存在（memory_store.restore），从 archive/ 移回；同时撕掉新条目里的 `potential_conflicts` 中 stale 记录 |
| A 档撕错标记（FALSE_POSITIVE 误判） | `ai-memory reflect-undo <new_id> <old_id>`（新增）：从 reflect-log 找到上条记录，反向恢复 frontmatter 字段 |
| B 档 merge 编造内容 | 被 merge 掉的条目都在 archive/ 且 frontmatter 有 `merged_into=<new_id>`，可一键 `ai-memory reflect-undo --merge <new_id>` → archive 新条 + 全部 restore 旧条 |
| 整批错（如 LLM provider 切换后输出变差） | reflect-log 反向 replay：按 ts desc 把所有 action 逆向应用，回到 reflect 跑之前的状态 |

### 8.3 必备审计

- 每次 reflect 跑前打 snapshot：`~/.ai-memory/.reflect-snapshots/<ts>/` 存被影响 id 的当前完整文件副本
- 默认保留最近 3 次 snapshot；CLI `ai-memory reflect-snapshots` 看列表，`reflect-rollback <ts>` 整批回退

### 8.4 黑名单

- reflect_log 里的 `false_positive` 和 `uncertain` 对进黑名单 30 天，避免反复问 LLM 同一对
- 用户在期间手编辑了任一方 → 黑名单失效（重新走 reflect）

### 8.5 上限保护

- 单次 reflect 默认 `--max-pairs 30`、`--budget-llm-calls 50`，硬上限防 LLM 账单失控（呼应 redesign.md §9 风险）
- B 档单次默认 `--max-clusters 5`

---

## 9. 实施清单（仅 P6.1 = A 档）

按依赖顺序：

1. `core/reflect_pack.py`（仿 task_pack，独立目录与 suffix）—— ~120 行
2. `core/reflect.py`：候选对枚举 + verdict 应用 + 黑名单检查 + dry-run renderer —— ~200 行
3. `core/memory_store.py`：新增 `clear_superseded_pair / apply_reflect_verdict`；frontmatter 增 `reflected_at / reflect_verdict` 字段 —— ~50 行 diff
4. `core/recall_log.py` 同目录新增 `reflect_log.py`（或直接复用 LOG_DIR 加 `reflect_*.jsonl`）—— ~80 行
5. prompt：`distill/prompts/02_reflect_supersede.md` —— 单文件
6. `mcp-server/server.py`：3 个新 tool —— ~80 行
7. `cli/ai_memory.py`：`reflect` / `reflect-undo` / `reflect-snapshots` / `reflect-rollback` 4 个 subcommand —— ~150 行
8. `ai-memory stats` 加 reflect 段 —— ~30 行 diff
9. 文档：redesign.md §8 Phase 6 改"候选/待定"为"已实施 A 档"，§9 待决策勾选

**总估**：~700 行新增 + ~120 行 diff，**~1-1.5 天工时**（含测试和 dry-run 演练）。

---

## 10. 开放问题（留给实施前再决）

- Q1：A 档 verdict 是否引入 `CONFIRMED_OLD_STILL_VALID`（明确"旧的没被替代，新条目是新的另一件事"）作为 FALSE_POSITIVE 的细化？倾向不引入，FALSE_POSITIVE 已经够用。
- Q2：reflect 任务包是否要按 (新条目 value, 召回热度) 排序，让高价值对优先消化？倾向 P6.1 不做（按时间序），P6.2 再加。
- Q3：B 档若上，是否要"先 dry-run 跑一遍，结果给 user 看 → user 手动逐簇 approve"？强烈倾向是——B 档默认 interactive。
- Q4：reflect 是否要 personal scope 内"跨项目"找候选？倾向**不要**，personal 的跨项目去重是召回侧的 §6.6.2 已经在做的事，reflect 不重复造轮子。
