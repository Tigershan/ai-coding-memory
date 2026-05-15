#!/bin/bash
# install.sh - ai-coding-memory 一键安装脚本
#
# 做什么：
#   1. 初始化 git submodule（fork 的 llm-wiki-skill）
#   2. 创建数据目录（~/.ai-memory/...）
#   3. 复制默认配置
#   4. 安装 Python 依赖（FastMCP、PyYAML 等）
#   5. 检查 llm-wiki-skill 的系统依赖（jq、node）
#   6. 把 MCP 配置注入到 Cursor / Aone Copilot / Qoder
#
# 失败模式：
#   - submodule 拉取失败 → 提示用户检查 git/网络（不退出，便于用户先开发）
#   - pip 安装失败 → 提示用户切换镜像源
#   - 系统依赖缺失（jq/node）→ 给出 brew 安装命令但不退出
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
DEFAULT_CONFIG="${PROJECT_ROOT}/config/domain-mapping.example.yml"
USER_CONFIG="${DATA_ROOT}/config/domain-mapping.yml"

info "🚀 Installing ai-coding-memory at ${PROJECT_ROOT}"

# ---- Step 1: 初始化 submodule ----
info "📦 Step 1/7: Initializing submodules (forked llm-wiki-skill)..."
cd "$PROJECT_ROOT"
if [ -f .gitmodules ]; then
    if ! git submodule update --init --recursive; then
        warn "submodule 拉取失败，请检查 .gitmodules 中的 fork URL 和网络"
        warn "（继续后续步骤，submodule 可稍后手动初始化）"
    fi
else
    warn ".gitmodules 不存在，跳过（首次开发阶段正常，待 fork llm-wiki-skill 后补上）"
fi

# ---- Step 2: 创建数据目录 ----
info "📁 Step 2/7: Creating data directories at ${DATA_ROOT}..."
mkdir -p "${DATA_ROOT}/raw/sessions"
mkdir -p "${DATA_ROOT}/raw/topics"
mkdir -p "${DATA_ROOT}/wiki"
mkdir -p "${DATA_ROOT}/config"
mkdir -p "${DATA_ROOT}/logs"

# ---- Step 3: 复制默认配置 ----
info "📝 Step 3/7: Setting up config..."
if [ ! -f "$USER_CONFIG" ]; then
    if [ -f "$DEFAULT_CONFIG" ]; then
        cp "$DEFAULT_CONFIG" "$USER_CONFIG"
        info "  Created default config: ${USER_CONFIG}（请按需编辑）"
    else
        warn "默认配置模板不存在：${DEFAULT_CONFIG}"
    fi
else
    info "  配置已存在，跳过：${USER_CONFIG}"
fi

# ---- Step 4: 安装 Python 依赖（智能跳过：已具备 → 不重装） ----
info "🐍 Step 4/7: Checking Python environment..."

REQUIRED_PY_MAJOR=3
REQUIRED_PY_MINOR=10
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

# ---- Step 5: 检查系统依赖 ----
info "🔍 Step 5/7: Checking system dependencies..."
all_ok=1
for cmd in jq node python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        warn "缺少 ${cmd}，请运行: brew install ${cmd}"
        all_ok=0
    fi
done
if [ $all_ok -eq 1 ]; then
    info "  系统依赖齐全（jq, node, python3）"
fi

# ---- Step 6: 注入 MCP 配置 ----
info "🔌 Step 6/7: Configuring MCP servers..."
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

# ---- Step 7: 把统一 skill 包安装到 IDE skills 目录 ----
info "🧠 Step 7/7: Installing unified skill package to IDE skills directories..."
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

echo ""
info "✅ Installation complete!"
echo ""
echo "📋 Next steps:"
echo "  1. 编辑 ${USER_CONFIG} 配置你的领域映射"
echo "  2. 在 QoderWork 中导入 workflows/qoderwork-daily.yml 设置定时任务"
echo "  3. 重启 IDE 让 MCP Server + skill 生效"
echo "  4. 在 IDE 里说一句「整理今天的记忆」让 AI 自动跑完整 pipeline"
echo "  5. 也可手动试运行：python3 ${PROJECT_ROOT}/collect/scripts/extract_sessions.py --range today --verbose"
