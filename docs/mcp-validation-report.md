# MCP 工具端到端验证报告

> 验证对象：commit `1124d1b` 之后的 9 个 MCP 工具
> 验证方式：不启动真实 IDE / fastmcp stdio，直接调用 `server.py` 里的工具函数（`@mcp.tool()` 装饰后仍是普通函数，可直接 `from server import ...` 调用）+ 等价调用底层 `core.*` / `task_pack` / `recall_log` 模块
> 数据隔离：全程 `AI_MEMORY_DATA_ROOT=$(mktemp -d -t ai-mem-mcp-test-)`，未污染 `~/.ai-memory/`
> 测试驱动：`scripts/_mcp_validation_runner.py`（独立 harness，运行后可删）
> 日期：2026-05-16

---

## 1. 测试矩阵

| # | 场景 | 涉及的 MCP 工具 / 底层模块 | 结果 | 备注 |
|---|------|----------------------------|------|------|
| 0 | 自检：env 隔离、目录懒创建 | `core.paths` | PASS | `AI_MEMORY_DATA_ROOT` 被 `core.paths._resolve_data_root()` 正确读到 |
| 1 | 跨 IDE remember(personal/project) → 跨 IDE search 召回 | `remember`, `search_memory` | PASS | personal redis 在 ws=nogit 里仍可召回；project 写入到正确 `projects/<dir>/` |
| 2 | 跨项目相关性（tag overlap ≥2 + 字面子串） | `search_memory` | PASS | `auth middleware` 从 repo-b 召回 repo-a 的 auth 记忆，score=1.36（含 cross-project 0.7 罚分） |
| 3 | `scope="current_project"` 排除跨项目 | `search_memory` | PASS | 同样的 query 仅扫当前 project_dir → 空 |
| 4 | source=manual 文件不被自动覆盖 | `memory_store.save` | PASS | `save(Memory(source='auto'))` 同 id 抛 `PermissionError` |
| 5 | 写第二条同 tag 触发双向冲突标记（ADR-12） | `remember`（写） + `memory_store.save` | PASS | 新条目获得 `potential_conflicts=[old_id]`；旧条目获得 `potentially_superseded_by=[new_id]` |
| 6 | forget → archive，search 不再返回，restore 回原位 | `forget`, `search_memory`, `core.memory_store.restore` | PASS | archive 文件落在 `archive/<id>.md` |
| 7 | read_page 边界（合法 / `/etc/passwd` / 不存在） | `read_page` | **FAIL (BUG-1)** | **safety check 用错根目录，所有 personal/projects 文件都被拒绝** |
| 8 | project_context + AGENTS.md marker 块（保留用户内容） | `project_context`, `core.agents_md_sync` | PASS | marker 内外文本独立；nogit / 空 repo 均返回友好提示 |
| 9 | list_topics(scope=all) 分组 | `list_topics` | PASS | 按 `personal` / `projects/<dir>` 分组、含 H1 标题 |
| 10 | host_agent 全流程：count → take → submit → 落盘 | `pending_distill_count`, `get_next_distill_task`, `submit_distill_result`, `core.task_pack` | PASS | should_keep=true 落 `projects/`；false 落 `.cold/`；task 文件被消费 |
| 11 | submit 用错误 task_id | `submit_distill_result` | PASS（行为）/ NIT（UX） | 返回错误正确，但**首行仍带 ✓**（见 UX-1） |
| 12 | 错误路径：empty text / missing id / 空 workspace / dir 作为 path | `remember`, `forget`, `project_context`, `read_page` | 部分 PASS | 前 3 个正常；`read_page` on dir 被 BUG-1 短路 |
| 13 | recall_log.jsonl 真的被写入 | `search_memory`, `read_page`, `core.recall_log` | PARTIAL | search 事件写入 OK；**read 事件 0 条（BUG-1 级联）** |
| 14 | `ai-memory stats` CLI 在隔离数据上运行 | CLI subprocess | PASS | 正确反映 by scope/source/value、冲突计数、采纳率 |
| 15 | mtime > _mtime_at_write+tol → source 自动升级为 edited | `memory_store.load` | PASS | `ms.load()` 内存中升级为 edited |
| 16 | search 是否按 source=edited 加权 1.2 | `search_memory` | PARTIAL（设计权衡） | searcher 直读 frontmatter（仍是 auto），不会感知 load() 的运行时升级（见 UX-3） |
| 17 | read_page 大文件截断 | `read_page` | UNREACHABLE | 触发不了 truncation 分支，被 BUG-1 拦在前面 |
| 18 | 只有 in_progress 的 pending_distill_count | `pending_distill_count` | PASS | "暂无新待整理任务；N 个任务正在消化中" |
| 19 | 空 project 上的 list_topics(current_project) | `list_topics` | PASS | "当前 scope 下尚无 topic 文件" |
| 20 | project_key 归一化：ssh vs https 同 repo | `core.project_key.resolve_project_key` | PASS | `git@github.com:acme/repo-a.git` 与 `https://github.com/acme/repo-a.git` 都得 `github.com/acme/repo-a` |
| 21 | scope=auto 且未传 workspace（CWD 兜底） | `search_memory`, `mcp-server/lib/workspace_detector` | PASS | 兜底到 CWD 的 git root；无 origin → 只 personal |
| 22 | scope=all 输出 | `search_memory` | PASS | 包含 personal + 所有 projects 子目录 |
| 23 | get_next_distill_task 在没有 pending 时 | `get_next_distill_task` | PASS | 返回字符串 `"暂无待整理任务"` |
| 24 | `remember(scope='project', workspace=None)` | `remember` | **FAIL (BUG-2)** | 抛 `ValueError` → 返回 `❌ remember 落盘失败：scope=project 但 project_key 为空`，docstring 暗示应回退 personal |
| 25 | load 时 source 升级未反映到 searcher | `memory_store.load` vs `searcher.search_with_scope` | NIT | 同 #16，行为可观察 |
| 26 | write_task 序列化 | `core.task_pack.write_task` | PASS | JSON 格式正确，`project_key` 缺省时存字符串 `"null"`（与 submit_result 反序列化对齐） |
| 27 | forget 已 archived 的 id（幂等性） | `forget` | NIT | 重复 forget 仍返回 ✓，因为 `_find_by_id` 会扫 ARCHIVE_DIR，`shutil.move(同路径)` 不报错 |
| 28 | search 含正则元字符 `(parens)` 的 query | `search_memory` | PASS | 因 `re.escape(query)` 处理安全 |
| 29 | submit 提交格式坏的 / topics 为空的 YAML | `submit_distill_result`, `core.task_pack.submit_result` | **FAIL (BUG-3)** | **任务被静默消费、无错误反馈** |

---

## 2. Bug 详情 + Reproducer

### BUG-1（**Critical**）：`read_page` 安全根目录用错，所有 memory 文件都被拒绝

**文件**：`/Users/tiger/skills/ai-coding-memory/mcp-server/server.py:47, 103-110, 190-195`

**根因**：
```python
from lib.paths_ext import DATA_ROOT, WIKI_ROOT  # WIKI_ROOT = DATA_ROOT/"wiki"

def _is_path_inside_wiki(p: Path) -> bool:
    resolved = p.resolve()
    wiki_resolved = WIKI_ROOT.resolve()        # ←  WIKI_ROOT 是旧布局
    return str(resolved).startswith(str(wiki_resolved) + "/") or ...
```
P0 已经做减法把数据搬到 `~/.ai-memory/{personal,projects/<key>}/`，但 `read_page` 的安全检查仍只放行 `~/.ai-memory/wiki/`。结果就是 **任何由 `remember` / `distill` 写入的 memory 都不可被 `read_page` 读取**。

**Reproducer**（用 harness 第 7 个 scenario，节录实测输出）：
```
read_page valid (personal/*.md): ❌ 拒绝读取：`/private/var/.../personal/2026-05-16-redis-eval-alternative-05ec.md`
                                  不在知识库根 `/private/var/.../wiki` 内。
                                  出于安全考虑，本工具只允许读取 wiki 子树。
read_page valid (projects/*.md): ❌ 拒绝读取：`/private/var/.../projects/github.com_acme_repo-a/2026-05-16-...md`
                                  不在知识库根 `/private/var/.../wiki` 内。
```

**级联影响**：
- IDE 的"先 search 再 read 全文"工作流被破坏；agent 只能用 search snippet
- `recall_log.log_read()` 在 read_page 返回拒绝时不会触发 → 采纳率（read/search 比）永远 0%（stats CLI 实测 `read/search 比: 0.0%`）
- `read_page` 后续分支（"文件不存在"、"不是文件"、"截断"）对所有真实路径都不可达，无法被覆盖率统计
- AGENTS.md 同步通道成了唯一备份的全文获取手段

**建议修复**（不要在本验证中改 .py，仅给方向）：
```python
# 把根目录从 WIKI_ROOT 改成 DATA_ROOT；并允许 personal/ projects/ archive/ .cold/ 任一子目录
def _is_path_inside_data_root(p: Path) -> bool:
    resolved = p.resolve()
    root = DATA_ROOT.resolve()
    return str(resolved).startswith(str(root) + "/")
```
另外考虑只允许特定子目录白名单（personal / projects / archive / .cold），把 `raw/sessions/` 排除以防误读原始对话。

---

### BUG-2（**Functional**）：`remember(scope="project", workspace=None)` 抛异常而非回退 personal

**文件**：`/Users/tiger/skills/ai-coding-memory/mcp-server/server.py:303-323`

**根因**：`scope == "project"` 分支只在 **workspace 非空 + git 无 origin** 时回退到 personal；当 **workspace 本身为 None** 时既不进入回退分支，也不报错，直接造一个 `project_key=None` 的 Memory，`memory_store.save → memory_path` 抛 `ValueError`。

```python
elif scope == "project":
    if workspace:              # ← workspace=None 时这分支整个跳过
        info = resolve_project_key(workspace)
        if info: project_key = info["key"]
        else:
            effective_scope = "personal"
# 没有 else 分支处理 workspace=None
```

**Reproducer**（scenario 24 实测）：
```
remember(project, no workspace) output: ❌ remember 落盘失败：scope=project 但 project_key 为空: id=2026-05-16-project-scope-without-workspace-aa81
```

**建议**：把 `if workspace:` 改成 `if workspace: ... else: effective_scope = "personal"`；或对齐 `scope=auto` 的处理。

---

### BUG-3（**Silent Data Loss**）：`submit_distill_result` 对坏 YAML / 空 topics **静默吞掉任务**

**文件**：`/Users/tiger/skills/ai-coding-memory/core/task_pack.py:178-235`

**根因**：
1. 自家 `_parse_yaml` 异常容忍度极高（几乎不抛），坏输入会被解析成空 dict。
2. `topics = parsed.get("topics") or []` 拿到 `[]`，没有进入 `if not isinstance(topics, list)` 分支。
3. 函数末尾**无差别 `src.unlink()`**，于是哪怕 0 写入 0 错误，task 文件仍被删除。

**Reproducer**（scenario 29）：
```
yaml = "this is :: not :: valid : yaml\n[[["
out = submit_distill_result(task_id_d, yaml)
# 实测输出：
✓ submit_distill_result(a6dfb41181c2):
  📝 写入 memory: 0
# 错误数：0；但 task_id_d 对应的 .in_progress 文件被删除（验证：再 take 也拿不到）
```

**影响**：宿主 agent 给出毁坏的 YAML 时，pipeline 误以为"消化完了"，原始 prompt 永久丢失（没机会重试）。

**建议**：在 `topics == []` 时同时检查 `parsed` 是不是空 dict 或缺 `topics` 键，按错误处理：
```python
if not parsed or "topics" not in parsed:
    return {"written": [], "cold": [], "errors": ["YAML 解析后未找到 topics 字段，未消费任务"]}
# 或者：在 errors 非空时 mark_failed 而不是 unlink
```

---

## 3. 设计内行为 / UX 可优化点（非 bug）

### UX-1：`submit_distill_result` 永远以 ✓ 开头，即使 0 写入 + 全错误

**位置**：`/Users/tiger/skills/ai-coding-memory/mcp-server/server.py:513-527`
```python
lines = [
    f"✓ submit_distill_result({task_id}):",   # 永远是 ✓
    f"  📝 写入 memory: {len(written)}",
]
```
**实测**（scenario 11, bad task_id）：
```
✓ submit_distill_result(deadbeef0000):
  📝 写入 memory: 0
  ⚠️  错误: 1
    - task_id 未找到：deadbeef0000
```
**建议**：当 `len(written)+len(cold)==0 and len(errors)>0` 时换 ❌ 或 ⚠️ 前缀。

### UX-2：`read_page` 安全提示信息过于"内部"

提示文本是「不在知识库根 `~/.ai-memory/wiki` 内」。一旦 BUG-1 修了，仍建议把 `wiki` 这种实现细节换成 `~/.ai-memory/`（避免暴露/误导）。

### UX-3：`source=auto → edited` 升级对 search ranking **不可见**

**位置**：`memory_store.load`（升级在内存中）vs `mcp-server/lib/searcher.search_with_scope`（直读 `parse_fm(text)`，不走 load）。

实测（scenarios 15 + 25）：
```
on-disk source: auto
ms.load() reports source: edited  (升级仅在内存生效)
→ searcher 直读 frontmatter → boost (1.2) 不会被应用，直到下次 save()
```

`remember`/CLI `edit` 不会主动 save 一遍，所以即使被人手改了，IDE 召回排序也不会上浮。建议：
- 要么 searcher 用 `ms.load()`（多一次升级判定开销）
- 要么 load() 检测到升级后异步 rewrite 一次 frontmatter

### UX-4：`forget` 重复调用幂等但消息容易误导

**位置**：`/Users/tiger/skills/ai-coding-memory/core/memory_store.py:317-326` + `_find_by_id` 包含 ARCHIVE_DIR

第二次 `forget(id)` 仍返回：
```
✓ 已归档：`2026-05-16-redis-evalsha-fallback-d684`
```
但实际上文件本来就在 archive，相当于 no-op。建议：若 src 已在 ARCHIVE_DIR，提示「已经在 archive，无操作」。

### UX-5：`pending_distill_count` 第一次提示信息冗长

输出里同时塞了一段"消化流程 1-4"，每个 chat 启动都打印一次。建议：仅在第一次 IDE 启动时给详细 howto，后续只给数字。

### UX-6：`scope=current_project` 在 `workspace` 不在 git 时静默回退 personal

`scope_resolver.resolve_scope` 在 `current_project` + 无 project_key 时会写 warning（"workspace 不在 git 仓库中且无 origin remote，无法定位 project"），search_memory 头部会渲染 ⚠️。**但 list_topics 不渲染 warnings**（`server.py:240-254` 没把 `scope_info["warnings"]` 拼上去）。验证里 scenario 19 实测 list_topics 输出没有 warning，只有 "尚无 topic 文件"。

### UX-7：`AI_MEMORY_DATA_ROOT` 在不同模块的解析路径略有差异

- `core.paths._resolve_data_root()` 用 `.resolve()`（macOS 上 `/var/folders/...` → `/private/var/folders/...`）
- `mcp-server/lib/paths_ext._override_root()` 仅 `.expanduser()`（不 resolve）

实测两者结果指向同一物理目录所以不影响功能，但 self-check 输出会有两种风格的路径，对排障不利。

---

## 4. 已确认正常的关键行为（regression baseline）

1. `core.paths` 完全尊重 `AI_MEMORY_DATA_ROOT`，所有目录懒创建
2. `remember` 默认行为：text 无 `#` 时自动从前 30 字生成标题；tags 自动过滤非 str 并截断到 6
3. `memory_store.save` 写完会再写一次 frontmatter 以校准 `_mtime_at_write`
4. ADR-12 冲突检测：tag overlap ≥2 **或** title token Jaccard >0.4 触发；新条目记 `potential_conflicts`，老条目记 `potentially_superseded_by`，双向同步
5. `searcher` 按 `value × source × superseded × cross_project` 综合打分；query 用 `re.escape` 处理特殊字符
6. `scope_resolver` 在 `auto + 有 project_key` 时把所有 *其他* projects 子目录也加入候选（跨项目召回的基础）
7. `task_pack` 多 agent 并发的原子性：`os.rename(.task → .task.in_progress)`
8. `agents_md_sync.sync_to_file` 保留 marker 块外的用户内容（验证：手动在 AGENTS.md 头部加段落，二次同步后段落依旧）
9. `project_key.resolve_project_key` 把 `git@github.com:owner/repo.git`、`https://github.com/owner/repo.git`、`ssh://...` 全部归一为 `github.com/owner/repo`
10. `recall_log.log_search` 在召回结束后异步写 jsonl，命中 id 列表完整
11. `cli/ai_memory.py stats` 正确聚合：by scope / source / value、冲突计数、采纳率，且尊重 `--since-days`
12. lazy distill 自动触发仅在 `mcp.run()` 入口调用，**unit 测试 import server 不会**意外 fork 子进程

---

## 5. 复现 / 重跑指引

```bash
cd /Users/tiger/skills/ai-coding-memory
# harness 自创建 tempdir 并打印 DATA_ROOT
python3 scripts/_mcp_validation_runner.py
```

每次运行使用新的 `AI_MEMORY_DATA_ROOT`，对 `~/.ai-memory` 没有任何写入。

清理：`rm scripts/_mcp_validation_runner.py`（harness 不属于发布物，仅验证期工具）。

---

## 6. 结论

- **9 个 MCP 工具中 8 个核心行为符合预期**；唯一彻底失效的是 `read_page`（BUG-1：错把 `WIKI_ROOT` 当沙盒根）
- 1 个 functional bug（BUG-2：`remember scope=project` 缺 workspace 时回退缺失）
- 1 个 data-loss 风险（BUG-3：坏 YAML 静默吞 task）
- 数条 UX 可优化项（见第 3 节）

**优先级建议**：BUG-1 > BUG-3 > BUG-2 > UX-1 > UX-3 > 其他。
