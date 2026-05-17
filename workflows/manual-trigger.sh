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

# ---- Stage 2: distill (Agent 编排模式) ----
# distill 脚本本身不调用 LLM，只生成"任务包"+ manifest，由宿主 Agent
# 按 distill/SKILL.md 指引逐步消化（可参见 status / expand / assemble 子命令）
echo ""
echo "==> Stage 2: distill plan (Agent-orchestrated)"
DISTILL_SCRIPT="$PROJECT_ROOT/distill/scripts/distill.py"
if [ -f "$DISTILL_SCRIPT" ]; then
    python3 "$DISTILL_SCRIPT" plan --date "$DATE_KEY" $VERBOSE_FLAG
    echo ""
    echo "[NEXT] 任务包已生成在 ~/.ai-memory/raw/distill-tasks/$DATE_KEY/"
    echo "       让 IDE 中的 AI Agent 按 distill/SKILL.md 指引消化任务："
    echo "         1) 处理 step1-segment/*.prompt.md → 写出 *.result.json"
    echo "         2) python3 $DISTILL_SCRIPT expand --date $DATE_KEY"
    echo "         3) 处理 step2-coref/ → expand → step3-code/ → expand → step4-layer/"
    echo "         4) python3 $DISTILL_SCRIPT assemble --date $DATE_KEY"
    echo "       随时查看进度： python3 $DISTILL_SCRIPT status --date $DATE_KEY --verbose"
else
    echo "[SKIP] distill 未实现，跳过"
fi

# ---- Stage 3: compile (Agent 编排模式) ----
# compile 依赖 distill 已 assemble 出 topic 文件；如果上一步 Agent 还没消化完，
# topics 目录为空，compile 会被跳过（这是预期行为）。
# crystallize_topics.sh 只做"路由 + 自动建子库 + 生成任务清单"，
# 实际的逐 topic 入库由宿主 Agent 按 compile/SKILL.md 接力执行。
echo ""
echo "==> Stage 3: compile route (Agent-orchestrated)"
COMPILE_SCRIPT="$PROJECT_ROOT/compile/scripts/crystallize_topics.sh"
TOPICS_DIR="${HOME}/.ai-memory/raw/topics/$DATE_KEY"
LLM_WIKI_INIT="$PROJECT_ROOT/compile/llm-wiki-skill/scripts/init-wiki.sh"

if [ ! -d "$TOPICS_DIR" ] || [ -z "$(ls -A "$TOPICS_DIR" 2>/dev/null)" ]; then
    echo "[SKIP] $TOPICS_DIR 为空，等 distill assemble 完成后再跑 compile"
elif [ ! -f "$LLM_WIKI_INIT" ]; then
    echo "[SKIP] compile/llm-wiki-skill 未拉取（缺 init-wiki.sh）"
    echo "       请先运行： (cd $PROJECT_ROOT && git submodule update --init --recursive)"
elif [ -f "$COMPILE_SCRIPT" ]; then
    bash "$COMPILE_SCRIPT" "$DATE_KEY" $VERBOSE_FLAG
else
    echo "[SKIP] compile 未实现，跳过"
fi

echo ""
echo "[DONE] pipeline finished for $DATE_KEY"
