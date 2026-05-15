"""distill.lib - distill 阶段共享库

模块清单：
- paths_ext      ：distill 专属路径常量（基于 collect.lib.paths 扩展）
- prompt_renderer：加载 prompts/*.md 并填入变量
- task_builder   ：sessions → manifest.json + 任务包文件
- topic_segmenter / coreference_resolver / code_filter / layer_tagger
                 ：4 个 step 的"任务包生成 + 结果解析"双向适配器
- topic_writer   ：合并 4 step 结果 → 最终 topic .md
"""
