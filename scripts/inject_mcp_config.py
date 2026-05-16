#!/usr/bin/env python3
"""inject_mcp_config.py - 把 ai-coding-memory MCP server 注入到 IDE 配置

支持的 IDE / 配置路径（macOS）：
    Cursor       :  ~/.cursor/mcp.json                 （独立 mcp.json 文件）
    Aone Copilot :  ~/.aone_copilot/mcp.json           （独立 mcp.json 文件）
    Qoder        :  ~/Library/Application Support/Qoder/User/mcp.json
    Claude Code  :  ~/.claude.json                     （user profile，含 mcpServers 嵌套字段）

注入策略：
    - 已有 mcp.json / claude.json → 解析后合并（不覆盖其他 server / 不动 profile 中的其他字段）
    - 已有同名 server → 用最新版覆盖（version-bump 友好）
    - 写入前自动备份  → <target>.bak.<timestamp>
    - JSON 解析失败  → 报错退出，不破坏现有配置

CLI：
    inject_mcp_config.py --target <path> --project-root <path> [--server-name X]
    inject_mcp_config.py --auto    # 自动扫描所有已知 IDE 配置目录并注入

返回码：
    0 成功；2 参数错误；3 写入失败
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

SERVER_NAME_DEFAULT = "ai-coding-memory"

KNOWN_IDE_CONFIGS = {
    "cursor": "~/.cursor/mcp.json",
    "aone-copilot": "~/.aone_copilot/mcp.json",
    "qoder": "~/Library/Application Support/Qoder/User/mcp.json",
    # Claude Code 把 mcpServers 放在 user profile 里。_merge_server 已能识别"已有
    # mcpServers"分支，会只动该字段不破坏其他设置（theme / numStartups / 等）。
    "claude-code": "~/.claude.json",
}


def _build_server_entry(project_root: Path) -> dict:
    server_py = project_root / "mcp-server" / "server.py"
    if not server_py.exists():
        raise FileNotFoundError(f"找不到 server.py: {server_py}")
    return {
        "command": "python3",
        "args": [str(server_py)],
        "env": {
            # 让 server 知道自己绑定的项目根，便于 self-check 输出
            "AI_MEMORY_PROJECT_ROOT": str(project_root),
        },
    }


def _load_existing(target: Path) -> dict:
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"现有配置文件 JSON 解析失败 ({target}): {e}") from e
    except OSError as e:
        raise ValueError(f"读取现有配置失败 ({target}): {e}") from e


def _backup(target: Path) -> Path | None:
    if not target.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = target.with_suffix(target.suffix + f".bak.{ts}")
    shutil.copy2(target, bak)
    return bak


def _merge_server(existing: dict, server_name: str, entry: dict) -> dict:
    """合并 mcp.json，支持两种 schema 自动识别：

    A) 主流 schema（Cursor / Claude / Aone Copilot / Qoder 实际默认）:
       {"mcpServers": {<name>: {...}}}
    B) 极简 schema（少数自研 IDE）:
       {<name>: {...}}      —— 直接以 server name 为顶层 key

    判定规则：
        - 现有文件已有 "mcpServers" key             → 沿用 A
        - 现有文件为非空 dict 且**所有 value 都长得像 server entry**（含 command）
                                                    → 沿用 B（不破坏既有约定）
        - 其他情况（含完全空文件、空 dict）           → 默认走 A（主流）
    """
    out = dict(existing) if existing else {}

    if "mcpServers" in out:
        out["mcpServers"][server_name] = entry
        return out

    # 极简 schema 判定：必须是非空 dict 且每个 value 都是 dict + 含 "command"
    if out and all(
        isinstance(v, dict) and "command" in v for v in out.values()
    ):
        out[server_name] = entry
        return out

    # 默认（含空 dict）：主流 mcpServers schema
    out.setdefault("mcpServers", {})[server_name] = entry
    return out


def inject(target: Path, project_root: Path, server_name: str) -> dict:
    """对单个目标文件执行注入；返回执行报告 dict"""
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    entry = _build_server_entry(project_root)
    existing = _load_existing(target)
    bak_path = _backup(target)

    merged = _merge_server(existing, server_name, entry)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, target)

    return {
        "target": str(target),
        "backup": str(bak_path) if bak_path else None,
        "server_name": server_name,
        "ok": True,
    }


def cmd_auto(project_root: Path, server_name: str) -> int:
    """扫描所有已知 IDE 配置目录并尝试注入"""
    any_done = False
    for ide_name, rel in KNOWN_IDE_CONFIGS.items():
        target = Path(rel).expanduser()
        if not target.parent.exists():
            print(f"[SKIP] {ide_name}: 配置目录不存在 ({target.parent})")
            continue
        try:
            report = inject(target, project_root, server_name)
            any_done = True
            bak_note = f" (backup: {report['backup']})" if report["backup"] else ""
            print(f"[OK]   {ide_name}: {report['target']}{bak_note}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {ide_name}: {e}", file=sys.stderr)
    return 0 if any_done else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="ai-coding-memory 项目根（默认：本脚本所在仓库根）",
    )
    parser.add_argument(
        "--server-name", default=SERVER_NAME_DEFAULT,
        help=f"MCP server 注册名（默认：{SERVER_NAME_DEFAULT}）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--target", help="单个 mcp.json 路径（与 --auto 二选一）",
    )
    group.add_argument(
        "--auto", action="store_true",
        help="自动扫描所有已知 IDE 配置目录",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    if not (project_root / "mcp-server" / "server.py").exists():
        print(
            f"[ERROR] --project-root 不像 ai-coding-memory 仓库根（缺 mcp-server/server.py）: "
            f"{project_root}",
            file=sys.stderr,
        )
        return 2

    if args.auto:
        return cmd_auto(project_root, args.server_name)

    try:
        report = inject(Path(args.target), project_root, args.server_name)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {e}", file=sys.stderr)
        return 3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
