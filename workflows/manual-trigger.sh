#!/bin/bash
# manual-trigger.sh - 手动触发完整 pipeline（collect → distill → compile）
#
# 用法：
#   ./workflows/manual-trigger.sh                  完整 pipeline（today）
#   ./workflows/manual-trigger.sh yesterday        指定日期
#   ./workflows/manual-trigger.sh today --collect-only      只跑 collect
#   ./workflows/manual-trigger.sh today --skip-collect      跳过 collect
#   ./workflows/manual-trigger.sh today --verbose           详细日志
#
# 失败模式：
#   - 任一 stage 失败 → 立即退出（set -e）
#   - distill / compile 未实现时 → 给出明确提示

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- 解析参数 ----
RANGE="today"
COLLECT_ONLY=0
SKIP_COLLECT=0
VERBOSE_FLAG=""

for arg in "$@"; do
    case "$arg" in
        today|yesterday)         RANGE="$arg" ;;
        --collect-only)          COLLECT_ONLY=1 ;;
        --skip-collect)          SKIP_COLLECT=1 ;;
        --verbose)               VERBOSE_FLAG="--verbose" ;;
        *) echo "[WARN] 未知参数：$arg" ;;
    esac
done

DATE_KEY=$(python3 -c "
from datetime import datetime, timedelta
d = datetime.now() - (timedelta(days=1) if '$RANGE' == 'yesterday' else timedelta())
print(d.strftime('%Y-%m-%d'))
")

echo "==> Pipeline target date: $DATE_KEY (range=$RANGE)"

# ---- Stage 1: collect ----
if [ $SKIP_COLLECT -eq 0 ]; then
    echo ""
    echo "==> Stage 1: collect"
    python3 "$PROJECT_ROOT/collect/scripts/extract_sessions.py" \
        --range "$RANGE" $VERBOSE_FLAG
fi

if [ $COLLECT_ONLY -eq 1 ]; then
    echo ""
    echo "[DONE] collect-only mode, exiting."
    exit 0
fi

# ---- Stage 2: distill ----
echo ""
echo "==> Stage 2: distill"
DISTILL_SCRIPT="$PROJECT_ROOT/distill/scripts/distill.py"
if [ -f "$DISTILL_SCRIPT" ]; then
    python3 "$DISTILL_SCRIPT" --date "$DATE_KEY" $VERBOSE_FLAG
else
    echo "[SKIP] distill 未实现（Phase 2），跳过"
fi

# ---- Stage 3: compile ----
echo ""
echo "==> Stage 3: compile"
COMPILE_SCRIPT="$PROJECT_ROOT/compile/scripts/crystallize_topics.sh"
if [ -f "$COMPILE_SCRIPT" ]; then
    bash "$COMPILE_SCRIPT" "$DATE_KEY"
else
    echo "[SKIP] compile 未实现（Phase 3），跳过"
fi

echo ""
echo "[DONE] pipeline finished for $DATE_KEY"
