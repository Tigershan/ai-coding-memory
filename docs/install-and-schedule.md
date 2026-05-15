# ai-coding-memory · 安装与定时任务配置指南

> 目标读者：第一次拿到这个仓库、想在自己机器上跑通完整链路（采集 → 蒸馏 → 入库 → 召回）+ 配置 QoderWork 每日定时任务的人。
>
> 本指南只写**已经在 2026-04-25 端到端跑通过**的步骤。任何"理论上应该可以"的内容都会显式标注。

---

## 0. 前置条件

| 项 | 要求 | 验证命令 |
|---|---|---|
| OS | macOS（Linux 也可，仅 IDE 配置路径不同） | `uname` |
| Python | ≥ 3.10 | `python3 --version` |
| git | 任意现代版本 | `git --version` |
| jq | 任意版本 | `jq --version` |
| node | 任意版本（llm-wiki-skill 内部某些脚本可能用到） | `node --version` |
| GitHub 账号 | 用于 fork `llm-wiki-skill` 仓库 | — |

缺什么补什么：

```bash
brew install python@3.11 jq node
```

---

## 1. fork llm-wiki-skill（一次性动作）

`compile/` 阶段把 fork 过来的 [`sdyckjq-lab/llm-wiki-skill`](https://github.com/sdyckjq-lab/llm-wiki-skill) 作为 git submodule 用。

1. 浏览器登录 GitHub，访问 `https://github.com/sdyckjq-lab/llm-wiki-skill`，点 **Fork**。
2. 记住你 fork 后的 URL，例如 `https://github.com/<你的用户名>/llm-wiki-skill.git`。

> 已知本机示例：`https://github.com/Tigershan/llm-wiki-skill`。

---

## 2. 克隆主仓库

```bash
git clone <ai-coding-memory 仓库地址> ~/ai-coding-memory
cd ~/ai-coding-memory
```

> 路径不强制是 `~/ai-coding-memory`，但 `workflows/qoderwork-daily.yml` 的 `env.PROJECT_ROOT` 默认指向这里；如果你装到别的位置，记得后面同步改。

---

## 3. 配置 `.gitmodules`（指向你的 fork）

仓库里默认带的是 `.gitmodules.template`，第一次安装要把它改成实际的 `.gitmodules`：

```bash
cp .gitmodules.template .gitmodules
# 用编辑器把里面的 <YOUR_GITHUB_USERNAME> 替换为你的 GitHub 用户名
$EDITOR .gitmodules
```

替换后内容应类似：

```ini
[submodule "compile/llm-wiki-skill"]
    path = compile/llm-wiki-skill
    url = https://github.com/Tigershan/llm-wiki-skill.git
    branch = main
```

> 如果仓库里已经有 `.gitmodules`（之前装过），跳过这一步。

---

## 4. 跑 `install.sh`

```bash
./install.sh
```

会按顺序做 **7 步**，每步都有彩色日志：

| Step | 做什么 | 失败容忍 |
|---|---|---|
| 1/7 | `git submodule update --init --recursive` 拉 llm-wiki-skill | 失败仅 WARN，不退出 |
| 2/7 | 创建 `~/.ai-memory/{raw/sessions,raw/topics,wiki,config,logs}` | — |
| 3/7 | 把 `config/domain-mapping.example.yml` 复制为 `~/.ai-memory/config/domain-mapping.yml` | 已存在则跳过 |
| 4/7 | 检查 Python 环境（≥3.10）+ 智能跳过：用 `python -c 'import fastmcp, yaml'` 探测；都已具备直接跳过 pip | 缺包才安装 |
| 5/7 | 检查 jq / node / python3 系统命令 | 缺只 WARN 不退出 |
| 6/7 | 把 MCP 配置注入到 Cursor / Aone Copilot / Qoder 的 `mcp.json` | 对应 IDE 没装则跳过 |
| 7/7 | 把 `skill/` 目录用 symlink 装到 `~/.<ide>/skills/ai-coding-memory/`（Aone Copilot / Claude Code / Cursor） | 单 IDE 失败仅 WARN |

**预期成功输出末尾**应包含：

```
✅ Installation complete!
```

> 想反复运行也安全：所有步骤都是幂等的（symlink 已对就跳过、配置已存在就跳过、Python 包已具备就跳过）。

---

## 5. 安装完成后的核对清单

对着下列命令逐条核对，**不通过的项必须先解决**再跑 pipeline：

```bash
# 5.1 数据目录
ls -d ~/.ai-memory/{raw/sessions,raw/topics,wiki,config,logs}

# 5.2 用户配置
test -f ~/.ai-memory/config/domain-mapping.yml && echo OK

# 5.3 fork 子模块拉下来了
test -f compile/llm-wiki-skill/scripts/init-wiki.sh && echo OK

# 5.4 Python 依赖
python3 -c "import fastmcp, yaml; print('OK')"

# 5.5 至少一个 IDE 的 MCP 配置已注入
grep -l ai-coding-memory \
  ~/.cursor/mcp.json \
  ~/.aone_copilot/mcp.json \
  "$HOME/Library/Application Support/Qoder/User/mcp.json" 2>/dev/null

# 5.6 至少一个 IDE 的 skill 已装
ls -l ~/.aone_copilot/skills/ai-coding-memory 2>/dev/null
ls -l ~/.cursor/skills/ai-coding-memory       2>/dev/null
ls -l ~/.claude/skills/ai-coding-memory       2>/dev/null

# 5.7 MCP server 自检通过
python3 mcp-server/server.py --self-check | jq .wiki_root_exists
# 期望输出: true
```

---

## 6. 编辑领域映射（可选，但推荐）

打开 `~/.ai-memory/config/domain-mapping.yml`，把你跨项目的「同业务」组合声明出来。例：

```yaml
domain_mapping:
  guidance-search:
    - icbu-buyer-basis-web
    - winterfell
    - guidance-search-engine

  trade:
    - icbu-trade-portal
    - icbu-trade-api
```

不在任何 domain 下的 project，会保持 project 层独立存在；不影响 pipeline 运行，只是无法升 domain 层共享。

---

## 7. 第一次手动跑通整条 pipeline

**强烈建议第一次先手动跑一遍**，确认链路通畅后再上定时任务。

### 7.1 在 IDE（Aone Copilot / Cursor / Qoder）里说一句话

进入任意一个项目，对 AI 说：

> "整理今天的记忆"

或者：

> "跑一遍 memory pipeline"

AI 会自动加载 `ai-coding-memory` skill，按 `skill/SKILL.md` 编排手册逐阶段执行：

1. **collect** — 跑 `collect/scripts/extract_sessions.py --range today`，把当日三个 IDE 的对话提取到 `~/.ai-memory/raw/sessions/<date>.json`
2. **distill** — 调 `distill/scripts/distill.py plan`，然后由 AI 自己接力消化 `step1-segment` → `step2-coref` → `step3-code` → `step4-layer`，最后 `assemble` 出 `~/.ai-memory/raw/topics/<date>/*.md`
3. **compile.route** — 跑 `compile/scripts/crystallize_topics.sh today`，按 topic frontmatter 路由到对应子库（projects / domains / general），自动 `init-wiki.sh` 建子库结构，写 manifest
4. **compile.ingest** — 由 AI 按 `compile/SKILL.md` 指引，逐 topic `cd` 到子库目录，调 llm-wiki ingest 工作流写 source / entity / index / log，最后用 `route_topics.py mark` 改 task 状态

每一步都有 `manifest.json` + `--verbose` 输出，**任何一步失败都可以从断点续跑**。

### 7.2 命令行版本（用于调试）

如果想脱开 IDE 单独跑：

```bash
./workflows/manual-trigger.sh today --verbose
```

注意：这个脚本只跑到 `compile.route`，因为 `distill` 和 `compile.ingest` 是 **Agent 编排模式**（脚本不直接调 LLM），最终入库还是要在 IDE 里让 AI 接力消化。

### 7.3 验证结果

```bash
# 当日采集了几个 session
jq .stats ~/.ai-memory/raw/sessions/$(date +%Y-%m-%d).json

# distill 出几个 topic
ls ~/.ai-memory/raw/topics/$(date +%Y-%m-%d)/

# 已经写了几个子库
ls ~/.ai-memory/wiki/projects/  ~/.ai-memory/wiki/general/  2>/dev/null

# 实际跑一次召回
python3 mcp-server/server.py --self-check | jq .scope_auto
```

> ✅ **2026-04-25 端到端实测**：5 sessions / 68183 字符 → 5 topic → 4 子库（5 source + 11 entity） → 9 召回测试 88% 命中。

---

## 8. 配置 QoderWork 每日定时任务

> ⚠️ **本节是用户即将测试的目标，作者本人尚未在 QoderWork 上跑过**。下方步骤基于 `workflows/qoderwork-daily.yml` 的设计意图给出，遇到问题请反馈以便后续完善。

### 8.1 模板文件位置

```
workflows/qoderwork-daily.yml
```

### 8.2 模板内容速览

包含 3 个 step：

| step | 命令 | 说明 |
|---|---|---|
| `collect` | `python3 ${PROJECT_ROOT}/collect/scripts/extract_sessions.py --range today --verbose` | 采集当日对话 |
| `distill-plan` | `python3 ${PROJECT_ROOT}/distill/scripts/distill.py plan --date today --verbose` | **只生成任务包**，不调 LLM |
| `compile-route` | `bash ${PROJECT_ROOT}/compile/scripts/crystallize_topics.sh today --verbose` | **只做路由 + 子库 init**，不调 LLM |

调度：`schedule: "0 22 * * *"`（每天 22:00）。

> ⚠️ **重点**：QoderWork 调度版**只**完成「采集 → distill 任务包 → compile 路由」三件事；**真正消化 distill task 和 ingest topic 需要在 IDE 里让 AI 接力**。设计如此是因为消化阶段必须复用 IDE 内的 LLM，不能脱机。
>
> 这意味着实际工作流是：
> - **22:00（QoderWork 自动）**：跑这三步，留下"已分好的任务包"
> - **第二天打开 IDE 时**：对 AI 说"消化下昨天的 memory tasks"，AI 自动续跑剩余环节

### 8.3 在 QoderWork 里导入

按 QoderWork 自己的"导入 yml"流程把 `workflows/qoderwork-daily.yml` 导进去。**导入前请检查并按需修改**：

```yaml
env:
  PROJECT_ROOT: ${HOME}/ai-coding-memory   # ← 如果你的安装位置不是 ~/ai-coding-memory，改这里
```

### 8.4 验证定时任务能跑通

第一次别等 22:00，**先在 QoderWork 里手动触发一次**。期望看到：

1. `collect` step 退出码 0，日志末尾有类似 `total_characters: NNN`
2. `distill-plan` step 退出码 0，日志包含 `step: plan`、`tasks_pending: N`
3. `compile-route` step 退出码 0，日志包含 `subwikis_touched: N`

3 个 step 全部 ✅ 后，到下方的"产物核对"里看实际文件是否生成。

### 8.5 产物核对

QoderWork 任务跑完后，应该看到：

```bash
# 当日 sessions
ls -la ~/.ai-memory/raw/sessions/$(date +%Y-%m-%d).json

# 当日 distill 任务包（pending 状态）
ls ~/.ai-memory/raw/distill-tasks/$(date +%Y-%m-%d)/

# 当日 compile manifest
ls ~/.ai-memory/wiki/.compile-manifest/$(date +%Y-%m-%d).json

# 子库目录（如果之前没出现过新的 project/domain，则不会新增）
ls ~/.ai-memory/wiki/projects/ ~/.ai-memory/wiki/general/ 2>/dev/null
```

### 8.6 续跑：第二天上午让 AI 在 IDE 里消化任务包

打开任意 IDE，对 AI 说：

> "消化昨天的 memory 任务"

或者：

> "把昨天的 distill 任务跑完并入库"

AI 会自动：
- 读 `~/.ai-memory/raw/distill-tasks/<昨日日期>/manifest.json` → 逐 step 消化 pending task → expand → assemble
- 读 `~/.ai-memory/wiki/.compile-manifest/<昨日日期>.json` → 逐 topic 入库 → mark completed

### 8.7 失败排查

| 现象 | 排查命令 | 常见原因 |
|---|---|---|
| `collect` 报错说"找不到 IDE 数据库" | `ls ~/.aone_copilot/kv_storage/`、`ls "~/Library/Application Support/Cursor/User/globalStorage/"` | 该 IDE 当天没用过；属正常情况，提取脚本会跳过 |
| `distill-plan` 报错"no sessions for today" | `cat ~/.ai-memory/raw/sessions/$(date +%Y-%m-%d).json \| jq .stats.totalSessions` | 当天对话为 0；安全跳过即可 |
| `compile-route` 报错"topics dir empty" | `ls ~/.ai-memory/raw/topics/$(date +%Y-%m-%d)/` | distill 还没 assemble 完；这是 **Agent 编排模式预期行为**，等 IDE 续跑即可 |
| QoderWork 找不到 python3 | `which python3`，把绝对路径写进 yml 的 `command` | QoderWork 的 PATH 可能与 shell 不同 |

---

## 9. 日常召回（无须任何动作）

只要：
- IDE 的 `mcp.json` 已注入
- IDE 已重启过

那么**正常写代码时，IDE 会通过 MCP 自动从 `~/.ai-memory/wiki/` 召回相关历史经验**，无需人工触发。

可以手动验证一次：

```bash
python3 mcp-server/server.py --self-check | jq .scope_auto
```

输出里 `include_paths` 应该包含「当前 workspace 对应的子库」+「所有 general/* 子库」。

---

## 10. 常见问题

### Q1：`install.sh` 第 7 步没装上 skill，IDE 里说"找不到 ai-coding-memory skill"

检查：

```bash
ls -l ~/.aone_copilot/skills/ai-coding-memory
```

如果返回 `No such file or directory`：

- 确认仓库里有 `skill/` 目录：`ls ~/ai-coding-memory/skill/SKILL.md`
- 重新跑 `./install.sh`，看 Step 7 的具体输出
- 兜底手动装：`ln -s ~/ai-coding-memory/skill ~/.aone_copilot/skills/ai-coding-memory`

### Q2：MCP server 启动失败 / IDE 里看不到 ai-coding-memory 工具

```bash
# 直接跑一次 self-check 看有没有报错
python3 mcp-server/server.py --self-check
```

如果报 `ModuleNotFoundError: fastmcp`，说明 Python 包没装：

```bash
cd mcp-server && pip3 install --user fastmcp pyyaml
```

如果报"wiki_root 不存在"：

```bash
mkdir -p ~/.ai-memory/wiki
```

### Q3：召回总是召不到东西

```bash
# 看 wiki 是不是空的
find ~/.ai-memory/wiki -name '*.md' | head

# 看 scope_resolver 帮你定位到了哪些子库
python3 mcp-server/server.py --self-check | jq .scope_auto
```

如果 `include_paths` 是空的，可能是当前 workspace 还没产生过任何 topic（projects 子库下没有这个项目目录），需要先跑一次 pipeline 让对应子库出现。

### Q4：`compile/scripts/route_topics.py` 里 `manifest.subwikis[*].initialized_this_run` 字段总是 false

**已知小瑕疵**（2026-04-25 发现），不影响功能。后续会修。审计场景临时用 `find ~/.ai-memory/wiki -maxdepth 3 -name index.md -newer /tmp/some-marker` 替代判断。

---

## 11. 卸载

```bash
# 1. 删数据
rm -rf ~/.ai-memory

# 2. 删 IDE skills
rm -rf ~/.aone_copilot/skills/ai-coding-memory
rm -rf ~/.cursor/skills/ai-coding-memory
rm -rf ~/.claude/skills/ai-coding-memory

# 3. 从 IDE 的 mcp.json 里手动删除 ai-coding-memory 段（用 jq 或编辑器）
$EDITOR ~/.aone_copilot/mcp.json
$EDITOR ~/.cursor/mcp.json
$EDITOR "$HOME/Library/Application Support/Qoder/User/mcp.json"

# 4. 删仓库
rm -rf ~/ai-coding-memory

# 5. 在 QoderWork 里删除 ai-coding-memory-daily 任务
```

---

## 附录 A：本指南覆盖范围

| 内容 | 状态 |
|---|---|
| 本机 macOS 上的安装 + 7 步 install.sh | ✅ 端到端实测过 |
| 手动跑 collect + distill + compile.route | ✅ 端到端实测过（2026-04-25） |
| 在 IDE 里让 AI 接力消化 distill / compile.ingest | ✅ 端到端实测过（2026-04-25） |
| MCP 召回（self-check + 真实 search） | ✅ 端到端实测过（9 查询命中 8） |
| QoderWork 真实部署 | ⏳ **未实测**，下方第 8 节是按设计意图写的，待用户首次部署后回填经验 |
| Linux / Windows 安装 | ⏳ 未测试 |
