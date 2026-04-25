---
name: ai-coding-memory
version: 0.1.0
description: |
  AI 编码记忆系统：自动采集 IDE 对话、清洗成结构化知识、分层入库、按需召回。

  TRIGGER 场景：
  1. 手动触发：用户说"distill 今日笔记"、"编译我的知识库"、"摄入今日对话"、"刷新我的记忆"、"跑一遍 memory pipeline"
  2. 定时触发：QoderWork 调用 workflows/qoderwork-daily.yml

  注意：日常 coding 时的"自动召回"由 MCP Server 完成，不通过此 skill 触发。
---

# AI Coding Memory

> 个人 AI 编码记忆系统。把每天与 Aone Copilot / Cursor / Qoder 的对话沉淀为结构化、分层、可召回的个人知识库。

## 架构概览

```
collect (📥)  → distill (🧪)  → compile (📚)  → recall (🔌)
  采集对话      预处理切分      llm-wiki 入库    MCP 召回
```

详细设计见 `docs/design.md`。

## 完整 pipeline（每日定时执行）

| Stage | 入口脚本 | 输入 | 输出 |
|---|---|---|---|
| 1. collect | `collect/scripts/extract_sessions.py --range today` | 三个 IDE 的本地存储 | `~/.ai-memory/raw/sessions/YYYY-MM-DD.json` |
| 2. distill | `distill/scripts/distill.py --date today` | sessions JSON | `~/.ai-memory/raw/topics/YYYY-MM-DD/*.md` |
| 3. compile | `compile/scripts/crystallize_topics.sh today` | topics MD | `~/.ai-memory/wiki/{projects,domains,general}/.../**.md` |
| 4. recall | `mcp-server/server.py`（常驻进程） | wiki | MCP 工具：`search_memory` / `read_page` / `list_topics` |

## 手动触发

- "采集今日对话" → 仅 Step 1
- "清洗今日数据" → 仅 Step 2
- "入库今日 topics" → 仅 Step 3
- "完整跑一遍" → 三步串联（参见 `workflows/manual-trigger.sh`）

## 安装

```bash
cd /path/to/ai-coding-memory
./install.sh
```

详见 `README.md`。
