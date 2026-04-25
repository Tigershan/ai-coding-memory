---
name: ai-coding-memory.collect
version: 0.1.0
description: |
  Stage 1：采集 Aone Copilot / Cursor / Qoder 的 AI 对话记录到本地 JSON。

  TRIGGER：用户说"采集今日对话"、"拉一下 IDE 对话"、"collect today"。
  通常作为 ai-coding-memory pipeline 的第一步执行。
---

# collect 模块

## 职责

从三种 IDE 的本地存储中提取指定时间范围的会话，做噪声清洗、闲聊过滤、智能截断后输出统一格式 JSON。

## 输入

| 来源 | 路径 |
|---|---|
| Aone Copilot | `~/.aone_copilot/kv_storage/` |
| Cursor | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` |
| Qoder | `~/Library/Application Support/Qoder/User/globalStorage/state.vscdb` |

## 输出契约

文件路径：`~/.ai-memory/raw/sessions/YYYY-MM-DD.json`

```json
{
  "timeRange": {
    "label": "今天 (2026-04-25)",
    "start": "2026-04-25T00:00:00",
    "end": "2026-04-25T23:30:00"
  },
  "sessions": [
    {
      "ide": "aone-copilot|cursor|qoder",
      "sessionId": "唯一标识",
      "name": "会话名（自动推断）",
      "createdAt": "ISO 时间",
      "lastUpdatedAt": "ISO 时间",
      "status": "completed|unknown",
      "workspace": "/Users/.../winterfell 或空",
      "messageCount": 12,
      "conversation": [
        { "role": "user", "content": "..." },
        { "role": "assistant", "content": "..." }
      ]
    }
  ],
  "stats": {
    "totalSessions": 5,
    "totalCharacters": 35000,
    "needsMapReduce": false,
    "byIde": { "aone-copilot": 2, "cursor": 1, "qoder": 2 },
    "sessionsByWorkspace": { "/Users/.../winterfell": [0, 2] }
  },
  "warnings": ["..."]
}
```

## 用法

```bash
# 采集今天所有 IDE
python3 collect/scripts/extract_sessions.py --range today

# 仅采集昨天的 Cursor
python3 collect/scripts/extract_sessions.py --range yesterday --ide cursor

# 预演（不写文件）
python3 collect/scripts/extract_sessions.py --dry-run --verbose
```

## 模块结构

```
collect/scripts/
├── extract_sessions.py        # 主入口（编排 lib）
└── lib/
    ├── paths.py               # 所有路径常量
    ├── time_range.py          # 时间窗口计算
    ├── cleaners.py            # 噪声清洗 + 闲聊过滤 + 智能截断
    ├── cursor_extractor.py    # Cursor / Qoder（VSCode 系）SQLite 提取
    └── aone_extractor.py      # Aone Copilot KV 文件提取
```

## 失败模式

| 现象 | 原因 | 处理 |
|---|---|---|
| 输出 sessions 为空 | 该时段未使用 IDE，或时间未到 | 仍写出空文件，下游不阻塞 |
| 某 IDE 数据库/索引不存在 | IDE 未安装，或路径变更 | 该 IDE 跳过，写入 warning |
| 单条会话解析异常 | 数据格式异常 | 该会话跳过，写入 warning |
