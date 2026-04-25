#!/usr/bin/env python3
"""extract_sessions.py - Stage 1: collect 主入口

做什么：
    采集指定时间范围内、指定 IDE 的所有 AI 对话，
    清洗噪声、过滤闲聊、智能截断后写入 raw/sessions/YYYY-MM-DD.json。

输入：
    --range  today | yesterday      时间范围（默认 today）
    --ide    cursor | qoder | aone-copilot | all   IDE（默认 all）
    --output 自定义输出路径（默认 ~/.ai-memory/raw/sessions/<date>.json）
    --dry-run  不写文件，仅打印统计
    --verbose  详细日志

输出：
    JSON 文件，结构见本项目 docs/design.md 5.2 节"输入契约"。

失败模式：
    - 单个 IDE 数据库/索引缺失 → 跳过，warning 记录到输出
    - 单条会话解析失败 → 跳过，warning 记录到输出
    - 时间范围内无任何会话 → 仍写出空 sessions 文件（保证下游可执行）
"""

import argparse
import json
import sys
from pathlib import Path

# 让 lib 可以被作为模块导入（本脚本可独立运行）
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.paths import (  # noqa: E402
    AONE_COPILOT_KV_DIR,
    IDE_DB_PATHS,
    RAW_SESSIONS_DIR,
    ensure_data_dirs,
)
from lib.time_range import compute_time_range, date_label_for_filename  # noqa: E402
from lib.cleaners import MAPREDUCE_THRESHOLD  # noqa: E402
from lib.cursor_extractor import extract_vscode_sessions  # noqa: E402
from lib.aone_extractor import extract_aone_copilot_sessions  # noqa: E402

ALL_IDES = ("cursor", "qoder", "aone-copilot")


def collect_for_ide(
    ide: str,
    start_ms: int,
    end_ms: int,
    verbose: bool = False,
) -> tuple[list[dict], list[str]]:
    """单 IDE 采集（路由到对应提取器）"""
    if ide in ("cursor", "qoder"):
        sessions, warnings = extract_vscode_sessions(
            ide, IDE_DB_PATHS[ide], start_ms, end_ms
        )
    elif ide == "aone-copilot":
        sessions, warnings = extract_aone_copilot_sessions(
            AONE_COPILOT_KV_DIR, start_ms, end_ms
        )
    else:
        return [], [f"未知 IDE: {ide}"]

    if verbose:
        print(f"[{ide}] sessions={len(sessions)} warnings={len(warnings)}",
              file=sys.stderr)
    return sessions, warnings


def build_output(
    time_range: dict,
    sessions: list[dict],
    warnings: list[str],
    enabled_ides: list[str],
) -> dict:
    """组装最终输出结构"""
    sessions.sort(key=lambda s: s.get("createdAt", ""))

    total_characters = sum(
        sum(len(msg.get("content", "")) for msg in s.get("conversation", []))
        for s in sessions
    )

    by_ide: dict[str, int] = {ide: 0 for ide in enabled_ides}
    sessions_by_workspace: dict[str, list[int]] = {}
    for idx, s in enumerate(sessions):
        by_ide[s["ide"]] = by_ide.get(s["ide"], 0) + 1
        ws = s.get("workspace", "") or "未知项目"
        sessions_by_workspace.setdefault(ws, []).append(idx)

    return {
        "timeRange": {
            "label": time_range["label"],
            "start": time_range["start"],
            "end": time_range["end"],
        },
        "sessions": sessions,
        "stats": {
            "totalSessions": len(sessions),
            "totalCharacters": total_characters,
            "needsMapReduce": total_characters > MAPREDUCE_THRESHOLD,
            "byIde": by_ide,
            "sessionsByWorkspace": sessions_by_workspace,
        },
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ai-coding-memory: collect stage")
    parser.add_argument("--range", choices=["today", "yesterday"], default="today")
    parser.add_argument(
        "--ide",
        choices=[*ALL_IDES, "all"],
        default="all",
        help="目标 IDE，'all' 表示三个都跑",
    )
    parser.add_argument("--output", type=Path, default=None,
                        help="自定义输出路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="不写文件，仅打印统计")
    parser.add_argument("--verbose", action="store_true",
                        help="详细日志（写到 stderr）")
    args = parser.parse_args()

    ensure_data_dirs()

    time_range = compute_time_range(args.range)
    target_ides = list(ALL_IDES) if args.ide == "all" else [args.ide]

    all_sessions: list[dict] = []
    all_warnings: list[str] = []
    for ide in target_ides:
        sessions, warnings = collect_for_ide(
            ide, time_range["start_ms"], time_range["end_ms"], args.verbose
        )
        all_sessions.extend(sessions)
        all_warnings.extend(warnings)

    output = build_output(time_range, all_sessions, all_warnings, target_ides)

    if args.dry_run:
        print(json.dumps(output["stats"], ensure_ascii=False, indent=2))
        if args.verbose:
            for w in all_warnings:
                print(f"[WARN] {w}", file=sys.stderr)
        return 0

    out_path = args.output or (
        RAW_SESSIONS_DIR / f"{date_label_for_filename(args.range)}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "output": str(out_path),
        "stats": output["stats"],
        "warnings_count": len(all_warnings),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
