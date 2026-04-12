#!/bin/bash
# ============================================================================
# KClaw Agent 安装脚本
# ============================================================================
# 适用于手动克隆仓库的开发者的快速安装方式。
# 使用 uv 进行快速 Python 配置和包管理。
#
# 使用方法:
#   ./setup-kclaw.sh
#
# 此脚本执行以下操作:
# 1. 安装 uv（如未安装）
# 2. 通过 uv 使用 Python 3.11 创建虚拟环境
# 3. 安装所有依赖（主包 + 子模块）
# 4. 从模板创建 .env 文件（如不存在）
# 5. 将 'kclaw' CLI 命令符号链接到 ~/.local/bin
# 6. 运行安装向导（可选）
# ============================================================================

set -e

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_VERSION="3.11"

echo ""
echo -e "${CYAN}⚕ KClaw Agent 安装脚本${NC}"
echo ""

# ============================================================================
# 安装 / 定位 uv
# ============================================================================

echo -e "${CYAN}→${NC} 检查 uv 是否存在..."

UV_CMD=""
if command -v uv &> /dev/null; then
    UV_CMD="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV_CMD="$HOME/.local/bin/uv"
elif [ -x "$HOME/.cargo/bin/uv" ]; then
    UV_CMD="$HOME/.cargo/bin/uv"
fi

if [ -n "$UV_CMD" ]; then
    echo -e "${GREEN}✓${NC} uv 已找到 ($UV_VERSION)"
else
    echo -e "${CYAN}→${NC} 正在安装 uv..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; then
        if [ -x "$HOME/.local/bin/uv" ]; then
            UV_CMD="$HOME/.local/bin/uv"
        elif [ -x "$HOME/.cargo/bin/uv" ]; then
            UV_CMD="$HOME/.cargo/bin/uv"
        fi
        
        if [ -n "$UV_CMD" ]; then
            UV_VERSION=$($UV_CMD --version 2>/dev/null)
            echo -e "${GREEN}✓${NC} uv 已安装 ($UV_VERSION)"
        else
            echo -e "${RED}✗${NC} uv 已安装但未找到。请将 ~/.local/bin 添加到 PATH 后重试。"
            exit 1
        fi
    else
        echo -e "${RED}✗${NC} uv 安装失败。请访问 https://docs.astral.sh/uv/"
        exit 1
    fi
fi

# ============================================================================
# Python 检查 (uv 可自动配置)
# ============================================================================

echo -e "${CYAN}→${NC} 检查 Python $PYTHON_VERSION..."

if $UV_CMD python find "$PYTHON_VERSION" &> /dev/null; then
    PYTHON_PATH=$($UV_CMD python find "$PYTHON_VERSION")
    PYTHON_FOUND_VERSION=$($PYTHON_PATH --version 2>/dev/null)
    echo -e "${GREEN}✓${NC} $PYTHON_FOUND_VERSION 已找到"
else
    echo -e "${CYAN}→${NC} Python $PYTHON_VERSION 未找到，正在通过 uv 安装..."
    $UV_CMD python install "$PYTHON_VERSION"
    PYTHON_PATH=$($UV_CMD python find "$PYTHON_VERSION")
    PYTHON_FOUND_VERSION=$($PYTHON_PATH --version 2>/dev/null)
    echo -e "${GREEN}✓${NC} $PYTHON_FOUND_VERSION 已安装"
fi

# ============================================================================
# 虚拟环境
# ============================================================================

echo -e "${CYAN}→${NC} 设置虚拟环境..."

if [ -d "venv" ]; then
    echo -e "${CYAN}→${NC} 移除旧的虚拟环境..."
    rm -rf venv
fi

$UV_CMD venv venv --python "$PYTHON_VERSION"
echo -e "${GREEN}✓${NC} 虚拟环境已创建 (Python $PYTHON_VERSION)"

# 告诉 uv 安装到此虚拟环境（uv 无需激活即可使用）
export VIRTUAL_ENV="$SCRIPT_DIR/venv"

# ============================================================================
# 依赖安装
# ============================================================================

echo -e "${CYAN}→${NC} 安装依赖..."

# 优先使用 uv sync + lockfile（哈希验证安装）
# 回退到 pip install 以保证兼容性或当 lockfile 过时
if [ -f "uv.lock" ]; then
    echo -e "${CYAN}→${NC} 使用 uv.lock 进行哈希验证安装..."
    UV_PROJECT_ENVIRONMENT="$SCRIPT_DIR/venv" $UV_CMD sync --all-extras --locked 2>/dev/null && \
        echo -e "${GREEN}✓${NC} 依赖已安装（lockfile 验证通过）" || {
        echo -e "${YELLOW}⚠${NC} Lockfile 安装失败（可能已过期），回退到 pip install..."
        $UV_CMD pip install -e ".[all]" || $UV_CMD pip install -e "."
        echo -e "${GREEN}✓${NC} 依赖已安装"
    }
else
    $UV_CMD pip install -e ".[all]" || $UV_CMD pip install -e "."
    echo -e "${GREEN}✓${NC} 依赖已安装"
fi

# ============================================================================
# 子模块（终端后端 + 强化学习训练）
# ============================================================================

echo -e "${CYAN}→${NC} 安装可选子模块..."

# tinker-atropos (强化学习训练后端)
if [ -d "tinker-atropos" ] && [ -f "tinker-atropos/pyproject.toml" ]; then
    $UV_CMD pip install -e "./tinker-atropos" && \
        echo -e "${GREEN}✓${NC} tinker-atropos 已安装" || \
        echo -e "${YELLOW}⚠${NC} tinker-atropos 安装失败（强化学习工具可能无法使用）"
else
    echo -e "${YELLOW}⚠${NC} tinker-atropos 未找到（运行: git submodule update --init --recursive）"
fi

# ============================================================================
# 可选: ripgrep（用于更快的文件搜索）
# ============================================================================

echo -e "${CYAN}→${NC} 检查 ripgrep（可选，用于更快的搜索）..."

if command -v rg &> /dev/null; then
    echo -e "${GREEN}✓${NC} ripgrep 已找到"
else
    echo -e "${YELLOW}⚠${NC} ripgrep 未找到（文件搜索将使用 grep 回退方案）"
    read -p "安装 ripgrep 以加快搜索速度？[Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        INSTALLED=false
        
        # 检查 sudo 是否可用
        if command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
            if command -v apt &> /dev/null; then
                sudo apt install -y ripgrep && INSTALLED=true
            elif command -v dnf &> /dev/null; then
                sudo dnf install -y ripgrep && INSTALLED=true
            fi
        fi
        
        # 尝试 brew（无需 sudo）
        if [ "$INSTALLED" = false ] && command -v brew &> /dev/null; then
            brew install ripgrep && INSTALLED=true
        fi
        
        # 尝试 cargo（无需 sudo）
        if [ "$INSTALLED" = false ] && command -v cargo &> /dev/null; then
            echo -e "${CYAN}→${NC} 尝试 cargo install（无需 sudo）..."
            cargo install ripgrep && INSTALLED=true
        fi
        
        if [ "$INSTALLED" = true ]; then
            echo -e "${GREEN}✓${NC} ripgrep 已安装"
        else
            echo -e "${YELLOW}⚠${NC} 自动安装失败。可选安装方式："
            echo "    sudo apt install ripgrep     # Debian/Ubuntu"
            echo "    brew install ripgrep         # macOS"
            echo "    cargo install ripgrep        # 使用 Rust（无需 sudo）"
            echo "    https://github.com/BurntSushi/ripgrep#installation"
        fi
    fi
fi

# ============================================================================
# 环境变量文件
# ============================================================================

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}✓${NC} 已从模板创建 .env 文件"
    fi
else
    echo -e "${GREEN}✓${NC} .env 已存在"
fi

# ============================================================================
# PATH 设置 — 将 kclaw 符号链接到 ~/.local/bin
# ============================================================================

echo -e "${CYAN}→${NC} 设置 kclaw 命令..."

KCLAW_BIN="$SCRIPT_DIR/venv/bin/kclaw"
mkdir -p "$HOME/.local/bin"
ln -sf "$KCLAW_BIN" "$HOME/.local/bin/kclaw"
echo -e "${GREEN}✓${NC} 已创建符号链接 kclaw → ~/.local/bin/kclaw"

# 确定合适的 shell 配置文件
SHELL_CONFIG=""
if [[ "$SHELL" == *"zsh"* ]]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
    SHELL_CONFIG="$HOME/.bashrc"
    [ ! -f "$SHELL_CONFIG" ] && SHELL_CONFIG="$HOME/.bash_profile"
else
    # 回退方案：检查现有文件
    if [ -f "$HOME/.zshrc" ]; then
        SHELL_CONFIG="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        SHELL_CONFIG="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
        SHELL_CONFIG="$HOME/.bash_profile"
    fi
fi

if [ -n "$SHELL_CONFIG" ]; then
    # 以防文件不存在但已被选中，先创建它
    touch "$SHELL_CONFIG" 2>/dev/null || true
    
    if ! echo "$PATH" | tr ':' '\n' | grep -q "^$HOME/.local/bin$"; then
        if ! grep -q '\.local/bin' "$SHELL_CONFIG" 2>/dev/null; then
            echo "" >> "$SHELL_CONFIG"
            echo "# KClaw Agent — 确保 ~/.local/bin 在 PATH 中" >> "$SHELL_CONFIG"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
            echo -e "${GREEN}✓${NC} 已在 $SHELL_CONFIG 中添加 ~/.local/bin 到 PATH"
        else
            echo -e "${GREEN}✓${NC} ~/.local/bin 已在 $SHELL_CONFIG 中"
        fi
    else
        echo -e "${GREEN}✓${NC} ~/.local/bin 已在 PATH 中"
    fi
fi

# ============================================================================
# 将捆绑的技能同步到 ~/.kclaw/skills/
# ============================================================================

KCLAW_SKILLS_DIR="${KCLAW_HOME:-$HOME/.kclaw}/skills"
mkdir -p "$KCLAW_SKILLS_DIR"

echo ""
echo "正在将捆绑的技能同步到 ~/.kclaw/skills/ ..."
if "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/tools/skills_sync.py" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} 技能已同步"
else
    # 回退：如果同步脚本失败则复制（缺少依赖等）
    if [ -d "$SCRIPT_DIR/skills" ]; then
        cp -rn "$SCRIPT_DIR/skills/"* "$KCLAW_SKILLS_DIR/" 2>/dev/null || true
        echo -e "${GREEN}✓${NC} 技能已复制"
    fi
fi

# ============================================================================
# 完成
# ============================================================================

echo ""
echo -e "${GREEN}✓ 安装完成！${NC}"
echo ""
echo "后续步骤："
echo ""
echo "  1. 重新加载 shell："
echo "     source $SHELL_CONFIG"
echo ""
echo "  2. 运行安装向导配置 API 密钥："
echo "     kclaw setup"
echo ""
echo "  3. 开始对话："
echo "     kclaw"
echo ""
echo "其他命令："
echo "  kclaw status        # 检查配置"
echo "  kclaw gateway install # 安装 gateway 服务（消息 + 定时任务）"
echo "  kclaw cron list     # 查看定时任务"
echo "  kclaw doctor        # 诊断问题"
echo ""

# 询问是否现在运行安装向导
read -p "是否现在运行安装向导？[Y/n] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    echo ""
    # 直接使用 venv Python 运行（无需激活）
    "$SCRIPT_DIR/venv/bin/python" -m kclaw_cli.main setup
fi
