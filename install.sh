#!/bin/bash
# install.sh - ai-coding-memory 一键安装脚本（redesign v1.2）
#
# 步骤：
#   1. 创建数据目录（~/.ai-memory/...）
#   2. 检查/安装 Python 依赖（FastMCP、PyYAML）
#   3. 注入 MCP 配置到 Cursor / Aone Copilot / Claude Code
#   4. 安装统一 skill 包到 IDE skills 目录
#   5. 询问 LLM mode（host_agent 默认 / api 可选；ADR-10 C 方案）
#   6. 询问是否现在初始化记忆库（三档预设）
#
# 跨平台：macOS / Linux 通用；Windows 暂未支持
# 非交互：传 INSTALL_NONINTERACTIVE=1 跳过 step 5/6 的提示
# 失败模式：
#   - pip 安装失败 → 提示用户切换镜像源
#   - MCP 配置注入失败（IDE 未安装）→ 仅警告，不退出

set -e

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- 路径常量 ----
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="${HOME}/.ai-memory"

info "🚀 Installing ai-coding-memory at ${PROJECT_ROOT}"
echo ""

# ---- Step 1: 创建数据目录 ----
info "📁 Step 1/6: Creating data directories at ${DATA_ROOT}..."
mkdir -p "${DATA_ROOT}/personal"
mkdir -p "${DATA_ROOT}/projects"
mkdir -p "${DATA_ROOT}/.pending"
mkdir -p "${DATA_ROOT}/archive"
mkdir -p "${DATA_ROOT}/raw/sessions"
mkdir -p "${DATA_ROOT}/wiki"          # 旧布局，过渡期保留
mkdir -p "${DATA_ROOT}/config"
mkdir -p "${DATA_ROOT}/logs"
info "  ✓ data root ready"

# ---- Step 2: 安装 Python 依赖（智能跳过：已具备 → 不重装） ----
info "🐍 Step 2/6: Checking Python environment..."

REQUIRED_PY_MAJOR=3
REQUIRED_PY_MINOR=10
# pyyaml 用于 P3+ 的 config.yml 加载；fastmcp 是 MCP runtime
REQUIRED_PKGS=(fastmcp yaml)   # yaml 来自 pyyaml 包；import 名是 yaml

# 4.1 检查 python3 是否存在
if ! command -v python3 >/dev/null 2>&1; then
    error "未找到 python3。请先安装：brew install python@3.11"
    exit 1
fi

# 4.2 检查 python3 版本 ≥ 3.10
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c "import sys; ok = sys.version_info >= (${REQUIRED_PY_MAJOR}, ${REQUIRED_PY_MINOR}); print('1' if ok else '0')")
if [ "$PY_OK" != "1" ]; then
    error "python3 版本过低（当前 ${PY_VERSION}，要求 >=${REQUIRED_PY_MAJOR}.${REQUIRED_PY_MINOR}）"
    error "请安装新版本：brew install python@3.11"
    exit 1
fi
info "  python3 ${PY_VERSION} ✓ (≥${REQUIRED_PY_MAJOR}.${REQUIRED_PY_MINOR})"

# 4.3 探测必要包是否已可导入；只列出"缺失"的包
MISSING_PKGS=()
for pkg in "${REQUIRED_PKGS[@]}"; do
    if python3 -c "import ${pkg}" 2>/dev/null; then
        info "  python -c 'import ${pkg}' ✓ (已具备，无需安装)"
    else
        MISSING_PKGS+=("$pkg")
    fi
done

# 4.4 仅在缺包时才动 pip / uv（避免无谓的网络请求和打扰）
if [ ${#MISSING_PKGS[@]} -eq 0 ]; then
    info "  所有 Python 依赖已具备，跳过安装步骤 ✨"
elif [ ! -f "${PROJECT_ROOT}/mcp-server/pyproject.toml" ]; then
    warn "  缺包 [${MISSING_PKGS[*]}] 但找不到 mcp-server/pyproject.toml，跳过自动安装"
else
    # import 名 → pip 包名映射
    PIP_PKGS=()
    for pkg in "${MISSING_PKGS[@]}"; do
        case "$pkg" in
            yaml)    PIP_PKGS+=("pyyaml") ;;
            fastmcp) PIP_PKGS+=("fastmcp") ;;
            *)       PIP_PKGS+=("$pkg") ;;
        esac
    done
    info "  缺少包：${PIP_PKGS[*]}，准备安装..."
    if command -v uv >/dev/null 2>&1; then
        if (cd "${PROJECT_ROOT}/mcp-server" && uv sync); then
            info "  uv sync 完成"
        else
            warn "  uv sync 失败，请手动运行：cd mcp-server && uv sync"
        fi
    else
        if pip3 install --user "${PIP_PKGS[@]}" 2>/dev/null; then
            info "  pip 依赖安装完成（${PIP_PKGS[*]}）"
        else
            warn "  pip 安装失败，请手动运行：pip3 install --user ${PIP_PKGS[*]}"
        fi
    fi
fi

# ---- Step 3: 注入 MCP 配置 ----
info "🔌 Step 3/6: Configuring MCP servers..."
if [ -f "${PROJECT_ROOT}/scripts/inject_mcp_config.py" ]; then
    for ide_config in \
        "${HOME}/.cursor/mcp.json" \
        "${HOME}/.aone_copilot/mcp.json" \
        "${HOME}/Library/Application Support/Qoder/User/mcp.json"; do
        ide_dir=$(dirname "$ide_config")
        if [ -d "$ide_dir" ]; then
            if python3 "${PROJECT_ROOT}/scripts/inject_mcp_config.py" \
                --target "$ide_config" \
                --project-root "$PROJECT_ROOT" 2>/dev/null; then
                info "  ✓ Configured: $ide_config"
            else
                warn "  ✗ 注入失败: $ide_config"
            fi
        fi
    done
else
    info "  MCP 注入脚本未实现，跳过"
fi

# ---- Step 4: 把统一 skill 包安装到 IDE skills 目录 ----
info "🧠 Step 4/6: Installing unified skill package to IDE skills directories..."
SKILL_SRC="${PROJECT_ROOT}/skill"
SKILL_NAME="ai-coding-memory"

if [ ! -f "$SKILL_SRC/SKILL.md" ]; then
    warn "  找不到 ${SKILL_SRC}/SKILL.md，跳过 skill 安装"
elif [ ! -d "$SKILL_SRC/references" ]; then
    warn "  找不到 ${SKILL_SRC}/references/，skill 引用的子文档将不可用，跳过"
else
    # 已知 IDE 的 skills 父目录候选（macOS）：
    #   - 父目录存在（说明 IDE 已安装）→ 我们 mkdir -p .../skills 后安装
    #   - 父目录不存在 → 该 IDE 未安装，跳过
    declare -a IDE_NAMES=(
        "Aone Copilot"
        "Claude Code"
        "Cursor"
    )
    declare -a IDE_PARENTS=(
        "${HOME}/.aone_copilot"
        "${HOME}/.claude"
        "${HOME}/.cursor"
    )
    any_done=0
    for i in "${!IDE_PARENTS[@]}"; do
        ide_name="${IDE_NAMES[$i]}"
        parent="${IDE_PARENTS[$i]}"
        if [ ! -d "$parent" ]; then
            info "  · ${ide_name} 未安装（${parent} 不存在），跳过"
            continue
        fi
        skills_dir="${parent}/skills"
        # 主动创建 skills 目录（IDE 安装了但还没用过 skill 时，该子目录可能不存在）
        if ! mkdir -p "$skills_dir" 2>/dev/null; then
            warn "  ✗ ${ide_name}: 无法创建 ${skills_dir}（权限不足？），跳过"
            continue
        fi
        target="${skills_dir}/${SKILL_NAME}"
        # 已是指向同一源的 symlink → 幂等跳过
        if [ -L "$target" ] && [ "$(readlink "$target")" = "$SKILL_SRC" ]; then
            info "  ✓ ${ide_name}: 已链接到最新源，跳过 (${target})"
            any_done=1
            continue
        fi
        # 备份其他形式的旧 skill（普通目录、其他位置的 symlink、损坏的 symlink）
        if [ -e "$target" ] || [ -L "$target" ]; then
            ts=$(date +%Y%m%d-%H%M%S)
            if mv "$target" "${target}.bak.${ts}" 2>/dev/null; then
                info "  · ${ide_name}: 备份旧 skill → ${target}.bak.${ts}"
            else
                warn "  ✗ ${ide_name}: 备份旧 skill 失败，跳过 ($target)"
                continue
            fi
        fi
        # 优先 symlink（开发期跟随仓库实时更新），失败回退到 cp -R
        if ln -s "$SKILL_SRC" "$target" 2>/dev/null; then
            info "  ✓ ${ide_name}: Symlinked ${target} → ${SKILL_SRC}"
            any_done=1
        elif cp -R "$SKILL_SRC" "$target" 2>/dev/null; then
            warn "  ⚠ ${ide_name}: 已用 cp 复制（非 symlink）到 ${target}"
            warn "    仓库更新后需重跑 ./install.sh 才能同步到该 IDE"
            any_done=1
        else
            warn "  ✗ ${ide_name}: 安装失败 (${target})"
        fi
    done
    if [ $any_done -eq 0 ]; then
        warn "  未向任何 IDE 安装 skill。如需手动安装："
        warn "    ln -s ${SKILL_SRC} ~/<your-ide-config-dir>/skills/${SKILL_NAME}"
    fi
fi

# ---- Step 5: LLM mode 询问（redesign §6.0 / ADR-10 C 方案） ----
echo ""
info "⚙️  Step 5/6: LLM mode 配置"

# 检测已有的 API key 环境变量
DETECTED_KEY=""
for env_name in OPENAI_API_KEY DASHSCOPE_API_KEY AI_MEMORY_LLM_API_KEY; do
    if [ -n "${!env_name:-}" ]; then
        DETECTED_KEY="$env_name"
        break
    fi
done

if [ -n "$DETECTED_KEY" ]; then
    echo "    检测到环境变量 ${DETECTED_KEY}（值已忽略）"
fi

echo "    LLM 提供两档来源："
echo "      1) host_agent (推荐, 零成本, 零配置)"
echo "         IDE 自己的 AI 用它的 LLM 跑蒸馏 (说『整理今日记忆』触发)"
echo "      2) api (自动后台运行, 消耗 ${DETECTED_KEY:-API 配额})"
echo "         lazy trigger 用 OpenAI-compatible API 后台静默跑"

if [ "${INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
    LLM_CHOICE=1
    info "    NONINTERACTIVE: 默认选 host_agent"
else
    if [ -n "$DETECTED_KEY" ]; then
        printf "    选择 [1=host_agent / 2=api]: "
    else
        printf "    选择 [1=host_agent / 2=api(需先 export 一个 API key)]: "
    fi
    read -r LLM_CHOICE
    LLM_CHOICE="${LLM_CHOICE:-1}"
fi

case "$LLM_CHOICE" in
    2)
        if [ -z "$DETECTED_KEY" ]; then
            warn "    没检测到 API key 环境变量，仍然写入 api 模式；用前请 export DASHSCOPE_API_KEY 或 OPENAI_API_KEY"
        fi
        python3 "${PROJECT_ROOT}/cli/ai_memory.py" config set llm.mode api >/dev/null 2>&1
        info "    ✓ 已写入 llm.mode=api"
        ;;
    *)
        python3 "${PROJECT_ROOT}/cli/ai_memory.py" config set llm.mode host_agent >/dev/null 2>&1
        info "    ✓ 已写入 llm.mode=host_agent（零成本）"
        ;;
esac

# ---- Step 6: 首次 init 询问（redesign §6.5.4） ----
echo ""
info "🗂  Step 6/6: 是否现在初始化记忆库？"
echo "    扫描所有 IDE 历史会话，蒸馏成可召回的 memory。"
echo "    选项："
echo "      1) 最近 7 天   (推荐, 几分钟)"
echo "      2) 最近 30 天  (约 15-20 分钟)"
echo "      3) 全部历史   (先估算再确认)"
echo "      4) 跳过, 以后再说"

if [ "${INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
    INIT_CHOICE=4
    info "    NONINTERACTIVE: 跳过 init"
else
    printf "    选择 [1]: "
    read -r INIT_CHOICE
    INIT_CHOICE="${INIT_CHOICE:-1}"
fi

case "$INIT_CHOICE" in
    1)
        info "    跑 ai-memory init --range last-7d --yes ..."
        python3 "${PROJECT_ROOT}/cli/ai_memory.py" init --range last-7d --yes || warn "init 失败，可稍后手动重跑"
        ;;
    2)
        info "    跑 ai-memory init --range last-30d --yes ..."
        python3 "${PROJECT_ROOT}/cli/ai_memory.py" init --range last-30d --yes || warn "init 失败，可稍后手动重跑"
        ;;
    3)
        info "    跑 ai-memory init --range all（会先估算让你确认）..."
        python3 "${PROJECT_ROOT}/cli/ai_memory.py" init --range all || warn "init 取消或失败"
        ;;
    *)
        info "    已跳过 init。后续可随时跑：ai-memory init [--range last-7d]"
        ;;
esac

echo ""
info "✅ Installation complete!"
echo ""
echo "📋 Next steps:"
echo "  1. 重启 IDE，让 MCP Server + skill 生效"
echo "  2. 在 IDE 里说一句『记住这个：...』，验证 remember 工具"
echo "  3. 切到另一个 IDE 提相同问题，验证跨 IDE 召回"
echo ""
echo "📚 CLI 入口："
echo "  python3 ${PROJECT_ROOT}/cli/ai_memory.py --help"
echo "  常用：ls / show / edit / archive / distill / init / pending / config / stats"
