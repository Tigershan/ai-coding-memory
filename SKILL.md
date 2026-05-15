# ai-coding-memory · 项目说明

> ⚠️ **本文件不是 IDE 加载的 Skill 入口**。
> IDE 触发的 Skill 在 `skill/SKILL.md`，由 `./install.sh` 安装到各 IDE 的 `~/.<ide>/skills/ai-coding-memory/`。
> 本文件仅作为项目根目录下的「概览说明」，方便人类开发者快速了解项目结构与用途。
>
> 若想深入了解：
> - **如何使用** → 读 `README.md`
> - **Agent 如何运行 pipeline** → 读 `skill/SKILL.md`（IDE Skill 入口，最权威的运行流程）
> - **完整架构与决策** → 读 `docs/design.md`

---

## 一句话定位

**把每天与 Aone Copilot / Cursor / Qoder 的 AI 对话，自动沉淀为分层、可召回的个人编码知识库。**

```
collect 📥 → distill 🧪 → compile 📚 → recall 🔌
采集对话      预处理切分    分层入库      MCP 召回
```

## 仓库目录速览

| 路径 | 角色 |
|---|---|
| `collect/` | Stage 1：从三个 IDE 的本地存储提取当日 AI 对话 |
| `distill/` | Stage 2：4 步 LLM 流水线把对话切成结构化 topic |
| `compile/` | Stage 3：按 scope 路由到 projects / domains / general 子知识库 |
| `mcp-server/` | Stage 4：常驻 MCP Server，给 IDE 提供召回工具 |
| `skill/` | **IDE Skill 包**（SKILL.md + references/）—— 由 install.sh 安装到 IDE skills 目录 |
| `workflows/` | QoderWork 定时调度配置 + 手动触发脚本 |
| `config/` | 默认配置模板（domain-mapping 等） |
| `docs/design.md` | 完整设计蓝图（1000+ 行，最权威） |

## 安装

```bash
git clone <this-repo> ~/ai-coding-memory
cd ~/ai-coding-memory
./install.sh
```

`install.sh` 共 7 步：submodule → 数据目录 → 配置 → Python 依赖 → 系统依赖 → MCP 注入 → Skill 安装。

## 触发方式

- **日常召回**：在 IDE 写代码时，MCP Server 自动按 workspace 召回相关历史经验，**无需手动触发**
- **每日沉淀**：在 IDE 里说一句「整理今天的记忆」「跑一遍 memory pipeline」，Skill 自动跑完整 pipeline
- **定时执行**：QoderWork 调用 `workflows/qoderwork-daily.yml` 每日定时跑

## 给 AI Agent 的提示

如果你（AI）正在阅读本文件并准备帮用户运行 pipeline，**不要按本文件操作**。
请去读 `skill/SKILL.md` —— 那是 Agent 编排手册，包含完整工作流、参数解析和错误恢复速查。
