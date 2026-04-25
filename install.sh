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
info "📦 Step 1/6: Initializing submodules (forked llm-wiki-skill)..."
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
info "📁 Step 2/6: Creating data directories at ${DATA_ROOT}..."
mkdir -p "${DATA_ROOT}/raw/sessions"
mkdir -p "${DATA_ROOT}/raw/topics"
mkdir -p "${DATA_ROOT}/wiki"
mkdir -p "${DATA_ROOT}/config"
mkdir -p "${DATA_ROOT}/logs"

# ---- Step 3: 复制默认配置 ----
info "📝 Step 3/6: Setting up config..."
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

# ---- Step 4: 安装 Python 依赖 ----
info "🐍 Step 4/6: Installing Python dependencies..."
if [ -f "${PROJECT_ROOT}/mcp-server/pyproject.toml" ]; then
    if command -v uv >/dev/null 2>&1; then
        if (cd "${PROJECT_ROOT}/mcp-server" && uv sync); then
            info "  uv sync 完成"
        else
            warn "uv sync 失败，请手动运行"
        fi
    else
        if pip3 install --user pyyaml fastmcp 2>/dev/null; then
            info "  pip 依赖安装完成（pyyaml, fastmcp）"
        else
            warn "pip 安装失败，请手动运行：pip3 install --user pyyaml fastmcp"
        fi
    fi
else
    info "  mcp-server/pyproject.toml 不存在（Phase 4 完成后才需要），跳过"
    # collect 模块仅用 Python 标准库，无需额外依赖
fi

# ---- Step 5: 检查系统依赖 ----
info "🔍 Step 5/6: Checking system dependencies..."
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
info "🔌 Step 6/6: Configuring MCP servers..."
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
    info "  MCP 注入脚本未实现（Phase 4），跳过"
fi

echo ""
info "✅ Installation complete!"
echo ""
echo "📋 Next steps:"
echo "  1. 编辑 ${USER_CONFIG} 配置你的领域映射"
echo "  2. 在 QoderWork 中导入 workflows/qoderwork-daily.yml 设置定时任务（Phase 5）"
echo "  3. 重启 IDE 让 MCP Server 生效（Phase 4 完成后）"
echo "  4. 试运行：python3 ${PROJECT_ROOT}/collect/scripts/extract_sessions.py --range today --verbose"
