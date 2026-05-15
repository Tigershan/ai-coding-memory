---
name: ai-coding-memory.compile
version: 0.2.0
description: |
  Stage 3：把 distill 输出的 topic 文件按 scope 路由到分层子知识库，
  并由宿主 Agent 调用 llm-wiki-skill 的 ingest 工作流完成入库。
  采用 Agent 编排模式：route_topics.py 只做路由 + 子库初始化 + 任务清单，
  实际的"知识结晶化/入库"由你（Agent）按本手册逐 topic 执行。

  TRIGGER：用户说「入库今日 topics」「compile today」「刷新知识库」
           「把 distill 的 topic 入到 wiki 里」「跑一下 compile」时。
---

# compile 模块 — Agent 操作手册

## 你（Agent）在 compile 流水线中的角色

compile 把 distill 产出的 `~/.ai-memory/raw/topics/<date>/*.md` 入库到分层 wiki：

```
~/.ai-memory/raw/topics/2026-04-25/                 ~/.ai-memory/wiki/
  001-project-winterfell-rate-limit.md       ─→     projects/winterfell/
  002-domain-guidance-ranking.md             ─→     domains/guidance-search/
  003-general-java-stream.md                 ─→     general/java/
```

每个 `projects/<X>/`、`domains/<Y>/`、`general/<Z>/` 都是一个**独立的 llm-wiki 知识库**，
有自己的 `.wiki-schema.md` / `purpose.md` / `wiki/` 目录。

`route_topics.py` 不调用 LLM、不直接执行 ingest。它做：
1. 扫描 topics 目录
2. 解析 frontmatter 决定每个 topic 的目标子库
3. 子库未初始化（无 `.wiki-schema.md`）→ 调 `compile/llm-wiki-skill/scripts/init-wiki.sh` 自动建库
4. 写出 `~/.ai-memory/wiki/.compile-manifest/<date>.json`，列出所有「待 ingest 的 topic 任务」

剩下的「逐 topic 入库」交给你按本手册执行。

---

## 标准工作流（按顺序执行）

### Step 0 ：前置检查

submodule 必须已拉取（首次安装时由 install.sh 完成）：
```bash
ls compile/llm-wiki-skill/scripts/init-wiki.sh
```
缺失则跑：
```bash
git submodule update --init --recursive
```

distill 必须已 assemble 出 topics：
```bash
ls ~/.ai-memory/raw/topics/$(date +%Y-%m-%d)/
```
为空则先去跑 `distill` 流水线（见 `distill/SKILL.md`）。

### Step 1 ：plan（路由 + 自动初始化子库）

```bash
# 简洁入口（推荐）
bash compile/scripts/crystallize_topics.sh today --verbose

# 等价命令
python3 compile/scripts/route_topics.py plan --date today --verbose
```

执行后会：
- 自动初始化所有未建的子库（调 `init-wiki.sh`）
- 生成 `~/.ai-memory/wiki/.compile-manifest/<date>.json`

manifest 结构（精简）：
```json
{
  "date": "2026-04-25",
  "subwikis": [
    {"path": "/Users/.../wiki/projects/winterfell", "label": "winterfell 项目知识库",
     "language": "中文", "initialized_this_run": true, "topic_count": 1}
  ],
  "tasks": [
    {"id": "compile-000",
     "topic_file": "/Users/.../raw/topics/2026-04-25/001-project-winterfell-rate-limit.md",
     "topic_filename": "001-project-winterfell-rate-limit.md",
     "scope": "project", "subwiki_name": "winterfell",
     "subwiki_path": "/Users/.../wiki/projects/winterfell",
     "wiki_topic_label": "winterfell 项目知识库",
     "tags": ["rate-limit", "redis"], "estimated_value": "high",
     "status": "pending"}
  ]
}
```

### Step 2 ：消化 pending tasks（你的工作）

读取 manifest，对**每一个** `status=pending` 的 task **依次**执行：

1. **`cd` 到 `task.subwiki_path`**
   这是关键 —— llm-wiki SKILL.md 的所有工作流都用 CWD 判断当前知识库，
   不切目录会污染其他子库或主目录。

2. **按 llm-wiki 的 `ingest` 工作流消化 `task.topic_file`**
   - 完整规范见 `compile/llm-wiki-skill/SKILL.md` 的「工作流 2: ingest」
   - 简短版：把 topic .md 当作素材，提取实体/主题，更新 `wiki/entities/`、
     `wiki/topics/`、`wiki/sources/`、`index.md`、`log.md`
   - **跳过**：网络抓取、缓存检查（topic 是 distill 已经清洗好的本地素材，
     直接当 `notes/` 类型素材处理即可）
   - **跳过**：隐私自查提示（distill 已经做过敏感信息筛查）

3. **更新 manifest task 状态**
   推荐用脚本兜底命令（避免破坏 manifest 其他字段）：
   ```bash
   python3 compile/scripts/route_topics.py mark --date today \
       --id compile-000 --status completed
   ```
   失败：
   ```bash
   python3 compile/scripts/route_topics.py mark --date today \
       --id compile-000 --status failed --error "ingest failed: ..."
   ```

4. **继续下一个 task** 直到全部 completed/failed

### Step 3 ：status（查进度 / 复盘）

```bash
python3 compile/scripts/route_topics.py status --date today --verbose
```

---

## 子库初始化策略（自动完成，仅作了解）

`route_topics.py plan` 会对**当天涉及但尚未存在**的子库自动调：
```bash
bash compile/llm-wiki-skill/scripts/init-wiki.sh \
     <subwiki_path> "<wiki_topic_label>" "中文"
```

`wiki_topic_label` 命名规则：
- `projects/<X>` → `"<X> 项目知识库"`
- `domains/<Y>`  → `"<Y> 领域知识库"`
- `general/<Z>`  → `"<Z> 通用知识"`

---

## 错误恢复速查

| 现象 | 处理 |
|---|---|
| `找不到 init-wiki.sh` | `git submodule update --init --recursive` |
| `topics 目录不存在 / 为空` | 先跑 distill assemble |
| 某个 topic frontmatter 解析失败 | 已自动标 `failed` 写进 errors.log；修 distill 输出后重跑 plan |
| ingest 中途失败 | `mark --status failed`，不阻塞其他 task；修复后再 `mark --status pending` 重试 |
| 想重新入库某天 | 删除目标子库的对应 `wiki/sources/*` 条目 + cache 项后，重置 task 为 pending |
| `init-wiki.sh` 退出非零 | 看 errors.log；常见原因：bash/perl 缺失、模板路径异常 |

---

## 输出契约

| 路径 | 内容 |
|---|---|
| `~/.ai-memory/wiki/.compile-manifest/<date>.json` | 当日任务清单 + 状态机 |
| `~/.ai-memory/wiki/.compile-errors.log`           | 累计失败追加日志 |
| `~/.ai-memory/wiki/projects/<X>/wiki/...`         | 项目层结晶化结果 |
| `~/.ai-memory/wiki/domains/<Y>/wiki/...`          | 领域层结晶化结果 |
| `~/.ai-memory/wiki/general/<Z>/wiki/...`          | 通用层结晶化结果 |

---

## 不要做的事

- ❌ 不要在不 `cd` 到子库的情况下跑 ingest（会污染当前目录或其他子库）
- ❌ 不要绕过 `route_topics.py` 直接手动建子库目录（会跳过 init-wiki.sh 的 schema/purpose 生成）
- ❌ 不要修改 `~/.ai-memory/raw/topics/*.md`（distill 输出快照，应只读）
- ❌ 不要把多个不同 scope 的 topic 灌到同一个子库（manifest 已经按 scope 隔离，遵循即可）
- ❌ 不要在 ingest 时跳过 `purpose.md` 阅读 —— 子库的研究方向决定知识抽取的取舍

---

## 完整设计参考

- 设计蓝图：`docs/design.md` §6（compile）+ §7（分层数据存储）
- llm-wiki 工作流规范：`compile/llm-wiki-skill/SKILL.md`
- distill 输出契约：`distill/SKILL.md` + `docs/design.md` §5.2
- 主入口源码：`compile/scripts/route_topics.py`

---

## 历史子模块结构（供参考）

```
compile/
├── SKILL.md                          本手册
├── scripts/
│   ├── crystallize_topics.sh         shell 入口（包装 route_topics.py）
│   ├── route_topics.py               主入口：plan / status / mark
│   └── lib/
│       ├── paths_ext.py              compile 路径常量
│       ├── frontmatter.py            零依赖 YAML frontmatter 解析
│       ├── scope_router.py           scope → subwiki 路由
│       └── io_utils.py               原子写 + manifest 操作
└── llm-wiki-skill/                   git submodule (Tigershan/llm-wiki-skill fork)
---

## Step 4 ：构建知识图谱（graph 增强检索）

**何时执行**：所有 ingest 任务完成后（即 Step 2 全部 `status=completed`）。

每个子库 ingest 完所有 topic 后，需要追加构建 `graph-data.json`，
供 MCP Server 在检索时做图谱关联扩展（命中 A 实体后自动关联邻居 B、C）。

### 操作步骤

1. **`cd` 到子库目录**（与 ingest 阶段相同）

2. **执行 graph 构建脚本**：
   ```bash
   bash compile/llm-wiki-skill/scripts/build-graph-data.sh "$SUBWIKI_PATH"
   ```

   脚本会扫描 `wiki/{entities,topics,sources,synthesis}/*.md`，
   解析 `[[双向链接]]` 和 `<!-- confidence: ... -->` 注释，
   计算边权重（共引强度 / 来源重叠 / 类型亲和度）、Louvain 社区检测和规则 insights，
   写入 `wiki/graph-data.json`。

   依赖 `jq` + `node`（缺失时 `brew install jq node`）。

3. **验证输出**（可选）：
   ```bash
   jq '.meta' "$SUBWIKI_PATH/wiki/graph-data.json"
   ```
   检查 `total_nodes`、`total_edges` 是否合理。

### 示例

```bash
# 假设本日涉及 3 个子库
for subwiki in projects/winterfell domains/guidance-search general/java; do
    cd ~/.ai-memory/wiki/$subwiki
    bash ~/ai-coding-memory/compile/llm-wiki-skill/scripts/build-graph-data.sh .
done
```

### 注意事项

- graph 构建不需要 LLM 调用，是纯脚本（jq + node 本地计算）
- 每次 compile 后都应重新构建，因为新 topic 可能引入新的实体关联
- 构建失败不阻塞主流程（MCP Server 在 graph-data.json 不存在时 fallback 到纯 grep）
- 大规模知识库（>250 节点）时 insights 会自动降级，这是正常行为
