---
name: ai-coding-memory.mcp-server
version: 0.2.0
description: |
  Stage 4：MCP Server，让 IDE 在编码时按需召回个人编码知识库。

  本模块**不通过 SKILL TRIGGER 触发**，而是作为常驻 MCP Server 被 IDE 自动拉起，
  通过 stdio 协议暴露三个工具给 IDE 模型按需调用。

  TRIGGER（针对模型本身）：当用户提及「以前」「上次」「之前怎么处理」「我记得」
  等回顾性表述，或问题涉及他特定项目/历史经验时，模型应主动调用 search_memory。
---

# mcp-server 模块 — 运行手册

## 职责

1. **常驻 MCP Server** —— 被 Cursor / Aone Copilot / Qoder / Claude Code 等 IDE 自动加载
2. **暴露三个工具**：
   - `search_memory(query, scope="auto")` — 分层召回个人知识库
   - `read_page(path)` — 读取知识库具体页面
   - `list_topics(scope="auto")` — 列出主题清单
3. **自动识别当前 IDE workspace**，按「分层规则」过滤召回范围：
   - `projects/<当前项目>/` —— 项目专属知识
   - `domains/<所属领域>/` —— 跨项目共享业务知识（按 `~/.ai-memory/config/domain-mapping.yml`）
   - `general/<分类>/` —— 通用编码知识

---

## 安装与启动

### 一键安装（推荐）

```bash
bash install.sh
```

`install.sh` 的 Step 4 + Step 6 会自动：
- 安装 Python 依赖（fastmcp + pyyaml）
- 把 server 配置注入到本机所有已安装的 IDE 的 `mcp.json`

### 手动启动 / 自检（不依赖 IDE）

```bash
# 自检：打印当前环境探测结果（不启动 MCP runtime）
python3 mcp-server/server.py --self-check

# 真正启动 MCP（IDE 一般会自动拉起，手动启动仅用于调试 stdio）
python3 mcp-server/server.py
```

### 手动注入到指定 IDE

```bash
# 自动扫描所有已知 IDE 配置目录
python3 scripts/inject_mcp_config.py --auto

# 注入到指定文件
python3 scripts/inject_mcp_config.py \
    --target ~/.cursor/mcp.json \
    --project-root $(pwd)
```

注入会**自动备份**原配置（`mcp.json.bak.<timestamp>`）。

---

## 三个工具的契约

### 1. `search_memory(query, scope="auto")`

| 字段 | 值 |
|---|---|
| **TRIGGER** | 用户回顾性表述（"以前"/"上次"/"我记得"），或涉及他特定项目/经验 |
| **DON'T TRIGGER** | 通用编程知识问题（如「Java HashMap 是什么」） |
| **scope 取值** | `auto`（默认） / `current_project` / `domain` / `general` / `all` |
| **返回** | Markdown：Top 5 召回，含 path / line / score / snippet |

### 2. `read_page(path)`

| 字段 | 值 |
|---|---|
| **TRIGGER** | 想看 `search_memory` 某条结果完整内容；或用户给出 wiki 内文件路径 |
| **安全** | path 必须在 `~/.ai-memory/wiki/` 子树内（路径穿越攻击防护） |
| **限制** | 文件 > 60KB 自动截断 |

### 3. `list_topics(scope="auto")`

| 字段 | 值 |
|---|---|
| **TRIGGER** | 用户主动盘点知识库（"列一下你有什么主题"） |
| **DON'T TRIGGER** | 平时编码场景（用 search 而不是 list，避免输出过长） |
| **返回** | 按子库分组的 H1 标题清单 |

---

## 模块结构

```
mcp-server/
├── SKILL.md            本手册
├── pyproject.toml      Python 依赖（fastmcp + pyyaml）
├── server.py           FastMCP 入口 + 三个工具实现
└── lib/
    ├── __init__.py
    ├── paths_ext.py             路径常量（跨包复用 collect/lib/paths）
    ├── workspace_detector.py    识别当前 workspace（env > cwd > git）
    ├── scope_resolver.py        workspace + domain-mapping → 召回路径
    └── searcher.py              _index.md 摘要 +10 + 全文 grep + 重排 Top K
```

---

## scope 解析逻辑

`workspace_detector` 三层兜底：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | `AI_MEMORY_WORKSPACE` 环境变量 | install / IDE 配置可强制指定 |
| 2 | 进程 CWD（含 `.git`/`package.json`/`pyproject.toml` 等） | IDE 启动 MCP 时通常在 workspace 根 |
| 3 | `git rev-parse --show-toplevel` | 从 CWD 向上找 git 根 |
| - | 都失败 → workspace=None | 降级到 `general` 范围（保证至少有结果） |

`scope_resolver` 路由：

```
auto              → projects/<当前> + domains/<所属> + general/*    （推荐默认）
current_project   → projects/<当前>
domain            → domains/<所属>（无映射时降级到 general）
general           → general/*
all               → wiki/                                            （全局搜索）
```

`domain-mapping.yml` 不存在或 PyYAML 缺失时，自动降级到「project + general」并返回 warning。

---

## 召回引擎策略

`searcher` 在每个 scope 路径下：

1. **第 1 层 _index.md / index.md 摘要召回**（命中 +10 分基础分，因为 _index 是 llm-wiki 的浓缩入口）
2. **第 2 层 wiki/{entities,topics,synthesis} 全文 grep 兜底**（按命中次数计分）
3. **跳过** `wiki/sources/`（噪声大，是原文素材）
4. **去重 + 重排 + 截 Top 5**（按 (path, line) 去重）

性能预算：< 1s（pure stdlib，无外部搜索引擎依赖）。

---

## 故障排查速查

| 现象 | 处理 |
|---|---|
| IDE 调 search_memory 返回 "未找到相关条目" | 跑 `python3 server.py --self-check` 看 wiki_root_exists / include_paths |
| `[FATAL] fastmcp 未安装` | `pip3 install --user fastmcp pyyaml` 或在 `mcp-server/` 跑 `uv sync` |
| workspace 识别成 `(unknown)` | 在 IDE 的 mcp.json 中给 server 加 `env.AI_MEMORY_WORKSPACE` 强制指定 |
| domain 永远是 null | 检查 `~/.ai-memory/config/domain-mapping.yml` 是否存在且配置了对应 project |
| `read_page` 返回「拒绝读取」 | 路径必须在 `~/.ai-memory/wiki/` 子树内（安全机制，符合预期） |
| 修改 server.py 后无效 | 重启 IDE（MCP server 是常驻进程，IDE 启动时拉起一次） |
| 想测试但又没真实数据 | 跑 collect → distill → compile 完整 pipeline，或手动放 mock topic 文件到 `~/.ai-memory/wiki/general/test/wiki/topics/foo.md` |

---

## 输出契约（IDE 模型视角）

工具的 docstring 会**直接被模型读到**，相当于 prompt 的一部分。
本模块的 docstring 已经写好 TRIGGER / DON'T TRIGGER 提示，
模型会自动判断是否调用，**用户无需手动在 prompt 里指挥**。

---

## 完整设计参考

- 设计蓝图：`docs/design.md` §8（MCP Server）
- IDE 注入工具：`scripts/inject_mcp_config.py`
- 关联模块：`compile/SKILL.md`（compile 产出的 wiki 是 mcp 召回数据源）
