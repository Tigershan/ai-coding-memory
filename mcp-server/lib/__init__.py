"""mcp-server.lib - MCP server 共享库

模块清单：
- paths_ext           ：路径常量（跨包复用 collect/lib/paths）
- config_loader       ：分层加载 default.yml（仓库内置 + 用户覆盖 + 环境变量）
- workspace_detector  ：识别当前 IDE workspace（env / cwd / git 三层兜底）
- scope_resolver      ：workspace + domain-mapping → 召回路径列表
- searcher            ：分层 grep 召回 + 重排 Top K
"""
