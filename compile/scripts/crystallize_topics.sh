#!/bin/bash
# crystallize_topics.sh - compile 阶段轻量 shell 入口
#
# 实际工作（路由 / 子库初始化 / manifest 生成）由 route_topics.py 完成；
# 本脚本只是给 workflow / 用户提供一个"传统命令式"入口，避免他们记 python 子命令。
#
# 用法：
#   ./crystallize_topics.sh                  # plan today
#   ./crystallize_topics.sh 2026-04-25       # plan 指定日期
#   ./crystallize_topics.sh today --verbose  # 透传 verbose
#   ./crystallize_topics.sh today --dry-run  # 透传 dry-run
#
# 失败模式：
#   - submodule 未拉取：route_topics.py 自身会给出明确指引并非零退出
#   - 当天无 topics：route_topics.py 退出码 0，本脚本同样不报错

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROUTE_SCRIPT="${SCRIPT_DIR}/route_topics.py"

if [ ! -f "$ROUTE_SCRIPT" ]; then
    echo "[ERROR] route_topics.py 缺失：$ROUTE_SCRIPT" >&2
    exit 2
fi

# 解析参数
DATE="today"
EXTRA_ARGS=()
for arg in "$@"; do
    case "$arg" in
        today|yesterday|20[0-9][0-9]-[0-1][0-9]-[0-3][0-9])
            DATE="$arg"
            ;;
        *)
            EXTRA_ARGS+=("$arg")
            ;;
    esac
done

echo "==> compile plan (date=$DATE)"
python3 "$ROUTE_SCRIPT" plan --date "$DATE" "${EXTRA_ARGS[@]}"

echo ""
echo "[NEXT] manifest 已生成，让 IDE 中的 AI Agent 按 compile/SKILL.md 指引："
echo "       1) 读取 ~/.ai-memory/wiki/.compile-manifest/${DATE}.json 中所有 pending task"
echo "       2) 对每个 task：cd 到 task.subwiki_path → 按 llm-wiki SKILL.md 的 ingest"
echo "          工作流消化 task.topic_file → 把 task.status 改为 completed"
echo "       3) python3 $ROUTE_SCRIPT status --date $DATE --verbose  查进度"
