"""ai-coding-memory core - 项目核心共享库

按 redesign.md v1.2 §6 / §7 实现。

模块清单：
    paths           ：路径常量（数据根 / 子目录 / 配置文件）
    frontmatter     ：零依赖 YAML frontmatter 解析（最小可用）
    project_key     ：根据 workspace 路径解析稳定项目 key（git remote URL 归一化）
    memory_store    ：memory CRUD + source 保护机制 + archive/cold storage

设计原则：
    - 零外部依赖（stdlib only），保留 ADR-10 "零配置可用"承诺
    - 跨模块共享：collect / distill / mcp-server / cli 都从 core 导入
    - 入口脚本通过 sys.path.insert 把项目根加入路径，再 `from core import ...`
"""
