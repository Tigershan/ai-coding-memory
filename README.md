# ai-coding-memory

> 个人 AI 编码记忆系统。零配置、自动化、跨 IDE、有分层意识。

把每天与 **Aone Copilot / Cursor / Qoder** 的对话沉淀为结构化、分层、可召回的个人知识库——让 AI 真正"记得"你做过什么、踩过什么坑、学到过什么。

## 核心理念

> 不是记录"做了什么"，而是沉淀"学到了什么"。

灵感来源：[llm-wiki 方法论](https://github.com/sdyckjq-lab/llm-wiki-skill)（知识编译一次、持续维护，而非每次重新检索）。

## 5 分钟上手

```bash
git clone <this-repo> ~/ai-coding-memory
cd ~/ai-coding-memory
./install.sh
```

`install.sh` 会自动完成：
1. 拉取 fork 的 `llm-wiki-skill` submodule
2. 创建数据目录 `~/.ai-memory/{raw,wiki,config}`
3. 安装 Python 依赖
4. 把 MCP 配置注入 Cursor / Aone Copilot / Qoder

之后：
- 编辑 `~/.ai-memory/config/domain-mapping.yml` 配置你的领域映射
- 在 QoderWork 中导入 `workflows/qoderwork-daily.yml` 设置每日 22:00 定时任务
- 重启 IDE 让 MCP Server 生效

## 架构

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  collect 📥  │ →  │  distill 🧪  │ →  │  compile 📚  │ →  │  recall 🔌   │
│  采集对话     │    │  预处理切分   │    │  llm-wiki    │    │  MCP 召回    │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
       ↓                   ↓                    ↓                    ↑
  raw/sessions/       raw/topics/             wiki/               IDE 上下文
```

完整设计见 [`docs/design.md`](docs/design.md)。

## 三层知识分层

| 层级 | 适用范围 | 例子 |
|---|---|---|
| **project** | 单一代码库 | winterfell 的 `OfferModel` 字段映射 |
| **domain** | 跨项目同业务 | 导购搜索的排序公式 |
| **general** | 完全通用 | Java Stream 性能陷阱 / Redis evalSha 用法 |

召回时根据当前 IDE workspace **自动过滤**，避免无关知识打扰。

## 模块说明

| 模块 | 状态 | 说明 |
|---|---|---|
| `collect/` | ✅ Phase 1 | 复用 `daily-coding-summary` 的提取逻辑 |
| `distill/` | 🚧 Phase 2 | 主题切分 + 指代消解 + 代码筛选 + 分层标注 |
| `compile/` | 🚧 Phase 3 | 包装 fork 的 llm-wiki-skill |
| `mcp-server/` | 🚧 Phase 4 | FastMCP 实现 |
| `workflows/` | 🚧 Phase 5 | QoderWork 定时任务 |

## 设计原则（Harness Engineering）

1. **单一入口、清晰契约**：每个模块独立 `SKILL.md` + JSON/Markdown 数据契约
2. **文件即接口**：模块间通过文件系统通信
3. **可观测性**：所有脚本支持 `--verbose` / `--dry-run`
4. **容错优雅降级**：LLM 失败 → 兜底直入；单 session 失败不阻塞
5. **AI 友好**：动词化函数名、类型注解、配置外置、路径常量集中
6. **团队推广**：`install.sh` 一键搞定

详见 `docs/design.md` 第 12 节。

## 数据目录结构

```
~/.ai-memory/
├── raw/sessions/YYYY-MM-DD.json        # collect 输出
├── raw/topics/YYYY-MM-DD/*.md          # distill 输出
├── wiki/                                # compile 输出
│   ├── projects/<project>/
│   ├── domains/<domain>/
│   └── general/<category>/
├── config/domain-mapping.yml           # 用户配置
└── logs/                                # 各 stage 日志
```

## 隐私与安全

- 所有数据**仅存本地**（`~/.ai-memory/`）
- 不上传任何对话到云端
- LLM 调用复用当前 IDE 的能力（不需要额外 API key）
- `domain-mapping.yml` 不入 git

## 许可

内部项目，按需使用。
