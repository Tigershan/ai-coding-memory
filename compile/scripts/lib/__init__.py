"""compile.lib - compile 阶段共享库

模块清单：
- paths_ext     ：compile 专属路径常量（基于 collect.lib.paths 扩展）
- frontmatter   ：解析 distill topic .md 的 YAML frontmatter
- scope_router  ：按 frontmatter 决定目标子知识库路径 + 名称规整
- io_utils      ：原子写 + manifest 读写（与 distill 共享接口风格）
"""
