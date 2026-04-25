---
name: ai-coding-memory.mcp-server
version: 0.1.0
description: |
  Stage 4：MCP Server，让 IDE 在编码时按需召回个人知识库。

  ⚠️ 当前为 Phase 1 占位状态，实现于 Phase 4 完成。

  此模块不通过 SKILL TRIGGER 触发，而是作为常驻 MCP Server 被 IDE 自动加载。
---

# mcp-server 模块（占位）

## 职责（设计已定）

1. 作为 MCP Server 常驻进程，被 Cursor / Aone Copilot / Qoder 加载
2. 暴露三个工具给 IDE：
   - `search_memory(query, scope="auto")` —— 检索个人知识库
   - `read_page(path)` —— 读取知识库具体页面
   - `list_topics(scope="auto")` —— 列出主题索引
3. 自动识别当前 IDE workspace，按"分层规则"过滤召回：
   - `projects/<当前项目>/`
   - `domains/<所属领域>/`
   - `general/`

## 模块结构

```
mcp-server/
├── SKILL.md
├── server.py              # FastMCP 入口
├── pyproject.toml         # Python 依赖（fastmcp, pyyaml）
└── lib/
    ├── searcher.py        # grep + _index.md 摘要召回
    ├── scope_resolver.py  # 根据 workspace 解析召回路径
    └── workspace_detector.py  # 识别当前 IDE workspace
```

## 完整设计

详见 `docs/design.md` 第 8 节。

## 当前状态

- ✅ 设计文档完成
- ✅ `scope_resolver.py` 完整代码已写入 `docs/design.md` 8.2 节
- ✅ `searcher.py` 完整代码已写入 `docs/design.md` 8.3 节
- ⏳ Phase 4 实现：`server.py` + `lib/*.py` + 三个 IDE 的 MCP 配置注入
