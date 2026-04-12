#!/bin/bash
# ============================================================================
# KClaw Agent Installer
# ============================================================================
# Installation script for Linux and macOS.
# Uses uv for fast Python provisioning and package management.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.sh | bash
#
# Or with options:
#   curl -fsSL ... | bash -s -- --no-venv --skip-setup
#
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
REPO_URL_SSH="git@github.com:kkutysllb/kk_KClaw.git"
REPO_URL_HTTPS="https://github.com/kkutysllb/kk_KClaw.git"
KCLAW_HOME="$HOME/.kclaw"
INSTALL_DIR="${KCLAW_INSTALL_DIR:-$KCLAW_HOME/kclaw}"
PYTHON_VERSION="3.11"
NODE_VERSION="22"

# Options
USE_VENV=true
RUN_SETUP=true
BRANCH="main"

# Detect non-interactive mode (e.g. curl | bash)
# When stdin is not a terminal, read -p will fail with EOF,
# causing set -e to silently abort the entire script.
if [ -t 0 ]; then
    IS_INTERACTIVE=true
else
    IS_INTERACTIVE=false
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-venv)
            USE_VENV=false
            shift
            ;;
        --skip-setup)
            RUN_SETUP=false
            shift
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "KClaw Agent 安装程序"
            echo ""
            echo "用法: install.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --no-venv      不创建虚拟环境"
            echo "  --skip-setup   跳过交互式设置向导"
            echo "  --branch NAME  指定安装的 Git 分支 (默认: main)"
            echo "  --dir PATH     指定安装目录 (默认: ~/.kclaw/kclaw)"
            echo "  -h, --help     显示此帮助"
            exit 0
            ;;
        *)
            echo "未知选项: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
# Helper functions
# ============================================================================

print_banner() {
    echo ""
    echo -e "${MAGENTA}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│             ⚕ KClaw Agent 安装程序                    │"
    echo "├─────────────────────────────────────────────────────────┤"
    echo "│  由 kkutysllb 独立开发的开源 AI Agent。              │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
}

log_info() {
    echo -e "${CYAN}→${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

# ============================================================================
# System detection
# ============================================================================

detect_os() {
    case "$(uname -s)" in
        Linux*)
            OS="linux"
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                DISTRO="$ID"
            else
                DISTRO="unknown"
            fi
            ;;
        Darwin*)
            OS="macos"
            DISTRO="macos"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            OS="windows"
            DISTRO="windows"
            log_error "检测到 Windows 系统，请使用 PowerShell 安装程序:"
            log_info "  irm https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.ps1 | iex"
            exit 1
            ;;
        *)
            OS="unknown"
            DISTRO="unknown"
            log_warn "未知的操作系统"
            ;;
    esac

    log_success "检测到: $OS ($DISTRO)"
}

# ============================================================================
# Dependency checks
# ============================================================================

install_uv() {
    log_info "正在检查 uv 包管理器..."

    # Check common locations for uv
    if command -v uv &> /dev/null; then
        UV_CMD="uv"
        UV_VERSION=$($UV_CMD --version 2>/dev/null)
        log_success "已找到 uv ($UV_VERSION)"
        return 0
    fi

    # Check ~/.local/bin (default uv install location) even if not on PATH yet
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV_CMD="$HOME/.local/bin/uv"
        UV_VERSION=$($UV_CMD --version 2>/dev/null)
        log_success "已在 ~/.local/bin 找到 uv ($UV_VERSION)"
        return 0
    fi

    # Check ~/.cargo/bin (alternative uv install location)
    if [ -x "$HOME/.cargo/bin/uv" ]; then
        UV_CMD="$HOME/.cargo/bin/uv"
        UV_VERSION=$($UV_CMD --version 2>/dev/null)
        log_success "已在 ~/.cargo/bin 找到 uv ($UV_VERSION)"
        return 0
    fi

    # Install uv
    log_info "正在安装 uv (快速 Python 包管理器)..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; then
        # uv installs to ~/.local/bin by default
        if [ -x "$HOME/.local/bin/uv" ]; then
            UV_CMD="$HOME/.local/bin/uv"
        elif [ -x "$HOME/.cargo/bin/uv" ]; then
            UV_CMD="$HOME/.cargo/bin/uv"
        elif command -v uv &> /dev/null; then
            UV_CMD="uv"
        else
            log_error "uv 已安装但未在 PATH 中找到"
            log_info "请将 ~/.local/bin 添加到 PATH 后重新运行"
            exit 1
        fi
        UV_VERSION=$($UV_CMD --version 2>/dev/null)
        log_success "uv 安装成功 ($UV_VERSION)"
    else
        log_error "uv 安装失败"
        log_info "手动安装: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
}

check_python() {
    log_info "正在检查 Python $PYTHON_VERSION..."

    # Let uv handle Python — it can download and manage Python versions
    # First check if a suitable Python is already available
    if $UV_CMD python find "$PYTHON_VERSION" &> /dev/null; then
        PYTHON_PATH=$($UV_CMD python find "$PYTHON_VERSION")
        PYTHON_FOUND_VERSION=$($PYTHON_PATH --version 2>/dev/null)
        log_success "已找到 Python: $PYTHON_FOUND_VERSION"
        return 0
    fi

    # Python not found — use uv to install it (no sudo needed!)
    log_info "未找到 Python $PYTHON_VERSION，正在通过 uv 安装..."
    if $UV_CMD python install "$PYTHON_VERSION"; then
        PYTHON_PATH=$($UV_CMD python find "$PYTHON_VERSION")
        PYTHON_FOUND_VERSION=$($PYTHON_PATH --version 2>/dev/null)
        log_success "Python 安装成功: $PYTHON_FOUND_VERSION"
    else
        log_error "Python $PYTHON_VERSION 安装失败"
        log_info "请手动安装 Python $PYTHON_VERSION，然后重新运行此脚本"
        exit 1
    fi
}

check_git() {
    log_info "正在检查 Git..."

    if command -v git &> /dev/null; then
        GIT_VERSION=$(git --version | awk '{print $3}')
        log_success "已找到 Git $GIT_VERSION"
        return 0
    fi

    log_error "未找到 Git"
    log_info "请安装 Git:"

    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian)
                    log_info "  sudo apt update && sudo apt install git"
                    ;;
                fedora)
                    log_info "  sudo dnf install git"
                    ;;
                arch)
                    log_info "  sudo pacman -S git"
                    ;;
                *)
                    log_info "  请使用您的包管理器安装 git"
                    ;;
            esac
            ;;
        macos)
            log_info "  xcode-select --install"
            log_info "  或: brew install git"
            ;;
    esac

    exit 1
}

check_node() {
    log_info "正在检查 Node.js (浏览器工具所需)..."

    if command -v node &> /dev/null; then
        local found_ver=$(node --version)
        log_success "已找到 Node.js $found_ver"
        HAS_NODE=true
        return 0
    fi

    # Check our own managed install from a previous run
    if [ -x "$KCLAW_HOME/node/bin/node" ]; then
        export PATH="$KCLAW_HOME/node/bin:$PATH"
        local found_ver=$("$KCLAW_HOME/node/bin/node" --version)
        log_success "已找到 Node.js $found_ver (KClaw 管理)"
        HAS_NODE=true
        return 0
    fi

    log_info "未找到 Node.js — 正在安装 Node.js $NODE_VERSION LTS..."
    install_node
}

install_node() {
    local arch=$(uname -m)
    local node_arch
    case "$arch" in
        x86_64)        node_arch="x64"    ;;
        aarch64|arm64) node_arch="arm64"  ;;
        armv7l)        node_arch="armv7l" ;;
        *)
            log_warn "不支持的架构 ($arch)，无法自动安装 Node.js"
            log_info "手动安装: https://nodejs.org/en/download/"
            HAS_NODE=false
            return 0
            ;;
    esac

    local node_os
    case "$OS" in
        linux) node_os="linux"  ;;
        macos) node_os="darwin" ;;
        *)
            log_warn "不支持的操作系统，无法自动安装 Node.js"
            HAS_NODE=false
            return 0
            ;;
    esac

    # Resolve the latest v22.x.x tarball name from the index page
    local index_url="https://nodejs.org/dist/latest-v${NODE_VERSION}.x/"
    local tarball_name
    tarball_name=$(curl -fsSL "$index_url" \
        | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.xz" \
        | head -1)

    # Fallback to .tar.gz if .tar.xz not available
    if [ -z "$tarball_name" ]; then
        tarball_name=$(curl -fsSL "$index_url" \
            | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.gz" \
            | head -1)
    fi

    if [ -z "$tarball_name" ]; then
        log_warn "找不到 Node.js $NODE_VERSION 的 $node_os-$node_arch 二进制包"
        log_info "手动安装: https://nodejs.org/en/download/"
        HAS_NODE=false
        return 0
    fi

    local download_url="${index_url}${tarball_name}"
    local tmp_dir
    tmp_dir=$(mktemp -d)

    log_info "正在下载 $tarball_name..."
    if ! curl -fsSL "$download_url" -o "$tmp_dir/$tarball_name"; then
        log_warn "下载失败"
        rm -rf "$tmp_dir"
        HAS_NODE=false
        return 0
    fi

    log_info "正在解压到 ~/.kclaw/node/..."
    if [[ "$tarball_name" == *.tar.xz ]]; then
        tar xf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    else
        tar xzf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    fi

    local extracted_dir
    extracted_dir=$(ls -d "$tmp_dir"/node-v* 2>/dev/null | head -1)

    if [ ! -d "$extracted_dir" ]; then
        log_warn "解压失败"
        rm -rf "$tmp_dir"
        HAS_NODE=false
        return 0
    fi

    # Place into ~/.kclaw/node/ and symlink binaries to ~/.local/bin/
    rm -rf "$KCLAW_HOME/node"
    mkdir -p "$KCLAW_HOME"
    mv "$extracted_dir" "$KCLAW_HOME/node"
    rm -rf "$tmp_dir"

    mkdir -p "$HOME/.local/bin"
    ln -sf "$KCLAW_HOME/node/bin/node" "$HOME/.local/bin/node"
    ln -sf "$KCLAW_HOME/node/bin/npm"  "$HOME/.local/bin/npm"
    ln -sf "$KCLAW_HOME/node/bin/npx"  "$HOME/.local/bin/npx"

    export PATH="$KCLAW_HOME/node/bin:$PATH"

    local installed_ver
    installed_ver=$("$KCLAW_HOME/node/bin/node" --version 2>/dev/null)
    log_success "Node.js $installed_ver 已安装到 ~/.kclaw/node/"
    HAS_NODE=true
}

install_system_packages() {
    # Detect what's missing
    HAS_RIPGREP=false
    HAS_FFMPEG=false
    local need_ripgrep=false
    local need_ffmpeg=false

    log_info "正在检查 ripgrep (快速文件搜索)..."
    if command -v rg &> /dev/null; then
        log_success "已找到 $(rg --version | head -1)"
        HAS_RIPGREP=true
    else
        need_ripgrep=true
    fi

    log_info "正在检查 ffmpeg (TTS 语音消息)..."
    if command -v ffmpeg &> /dev/null; then
        local ffmpeg_ver=$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')
        log_success "已找到 ffmpeg $ffmpeg_ver"
        HAS_FFMPEG=true
    else
        need_ffmpeg=true
    fi

    # Nothing to install — done
    if [ "$need_ripgrep" = false ] && [ "$need_ffmpeg" = false ]; then
        return 0
    fi

    # Build a human-readable description + package list
    local desc_parts=()
    local pkgs=()
    if [ "$need_ripgrep" = true ]; then
        desc_parts+=("ripgrep 用于加速文件搜索")
        pkgs+=("ripgrep")
    fi
    if [ "$need_ffmpeg" = true ]; then
        desc_parts+=("ffmpeg 用于 TTS 语音消息")
        pkgs+=("ffmpeg")
    fi
    local description
    description=$(IFS=" and "; echo "${desc_parts[*]}")

    # ── macOS: brew ──
    if [ "$OS" = "macos" ]; then
        if command -v brew &> /dev/null; then
            log_info "正在通过 Homebrew 安装 ${pkgs[*]}..."
            if brew install "${pkgs[@]}"; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep 安装成功"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg 安装成功"
                return 0
            fi
        fi
        log_warn "无法自动安装 (未找到 brew 或安装失败)"
        log_info "手动安装: brew install ${pkgs[*]}"
        return 0
    fi

    # ── Linux: resolve package manager command ──
    local pkg_install=""
    case "$DISTRO" in
        ubuntu|debian) pkg_install="apt install -y"   ;;
        fedora)        pkg_install="dnf install -y"   ;;
        arch)          pkg_install="pacman -S --noconfirm" ;;
    esac

    if [ -n "$pkg_install" ]; then
        local install_cmd="$pkg_install ${pkgs[*]}"

        # Prevent needrestart/whiptail dialogs from blocking non-interactive installs
        case "$DISTRO" in
            ubuntu|debian) export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a ;;
        esac

        # Already root — just install
        if [ "$(id -u)" -eq 0 ]; then
            log_info "正在安装 ${pkgs[*]}..."
            if $install_cmd; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep 安装成功"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg 安装成功"
                return 0
            fi
        # Passwordless sudo — just install
        elif command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
            log_info "正在安装 ${pkgs[*]}..."
            if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep 安装成功"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg 安装成功"
                return 0
            fi
        # sudo needs password — ask once for everything
        elif command -v sudo &> /dev/null; then
            if [ "$IS_INTERACTIVE" = true ]; then
                echo ""
                log_info "sudo 仅用于通过包管理器安装可选系统软件包 (${pkgs[*]})。"
                log_info "KClaw Agent 本身不需要也不会保留 root 权限。"
                read -p "是否安装 ${description}? (需要 sudo) [y/N] " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd; then
                        [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep 安装成功"
                        [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg 安装成功"
                        return 0
                    fi
                fi
            elif [ -e /dev/tty ]; then
                # Non-interactive (e.g. curl | bash) but a terminal is available.
                # Read the prompt from /dev/tty (same approach the setup wizard uses).
                echo ""
                log_info "sudo 仅用于通过包管理器安装可选系统软件包 (${pkgs[*]})。"
                log_info "KClaw Agent 本身不需要也不会保留 root 权限。"
                read -p "是否安装 ${description}? [Y/n] " -n 1 -r < /dev/tty
                echo
                if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
                    if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd < /dev/tty; then
                        [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep 安装成功"
                        [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg 安装成功"
                        return 0
                    fi
                fi
            else
                log_warn "非交互模式且无可用终端 — 无法安装系统软件包"
                log_info "安装完成后手动安装: sudo $install_cmd"
            fi
        fi
    fi

    # ── Fallback for ripgrep: cargo ──
    if [ "$need_ripgrep" = true ] && [ "$HAS_RIPGREP" = false ]; then
        if command -v cargo &> /dev/null; then
            log_info "正在尝试通过 cargo 安装 ripgrep (无需 sudo)..."
            if cargo install ripgrep; then
                log_success "ripgrep 已通过 cargo 安装"
                HAS_RIPGREP=true
            fi
        fi
    fi

    # ── Show manual instructions for anything still missing ──
    if [ "$HAS_RIPGREP" = false ] && [ "$need_ripgrep" = true ]; then
        log_warn "ripgrep 未安装 (文件搜索将使用 grep 备选方案)"
        show_manual_install_hint "ripgrep"
    fi
    if [ "$HAS_FFMPEG" = false ] && [ "$need_ffmpeg" = true ]; then
        log_warn "ffmpeg 未安装 (TTS 语音消息功能将受限)"
        show_manual_install_hint "ffmpeg"
    fi
}

show_manual_install_hint() {
    local pkg="$1"
    log_info "手动安装 $pkg:"
    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian) log_info "  sudo apt install $pkg" ;;
                fedora)        log_info "  sudo dnf install $pkg" ;;
                arch)          log_info "  sudo pacman -S $pkg"   ;;
                *)             log_info "  请使用您的包管理器安装，或访问项目主页" ;;
            esac
            ;;
        macos) log_info "  brew install $pkg" ;;
    esac
}

# ============================================================================
# Installation
# ============================================================================

clone_repo() {
    log_info "正在安装到 $INSTALL_DIR..."

    if [ -d "$INSTALL_DIR" ]; then
        if [ -d "$INSTALL_DIR/.git" ]; then
            log_info "发现已有安装，正在更新..."
            cd "$INSTALL_DIR"

            local autostash_ref=""
            if [ -n "$(git status --porcelain)" ]; then
                local stash_name
                stash_name="kclaw-install-autostash-$(date -u +%Y%m%d-%H%M%S)"
                log_info "检测到本地修改，更新前正在暂存..."
                git stash push --include-untracked -m "$stash_name"
                autostash_ref="$(git rev-parse --verify refs/stash)"
            fi

            git fetch origin
            git checkout "$BRANCH"
            git pull --ff-only origin "$BRANCH"

            if [ -n "$autostash_ref" ]; then
                local restore_now="yes"
                if [ -t 0 ] && [ -t 1 ]; then
                    echo
                    log_warn "本地修改在更新前已暂存。"
                    log_warn "恢复这些修改可能会将本地自定义内容重新应用到更新后的代码上。"
                    printf "是否立即恢复本地修改? [Y/n] "
                    read -r restore_answer
                    case "$restore_answer" in
                        ""|y|Y|yes|YES|Yes) restore_now="yes" ;;
                        *) restore_now="no" ;;
                    esac
                fi

                if [ "$restore_now" = "yes" ]; then
                    log_info "正在恢复本地修改..."
                    if git stash apply "$autostash_ref"; then
                        git stash drop "$autostash_ref" >/dev/null
                        log_warn "本地修改已恢复到更新后的代码上。"
                        log_warn "如果 KClaw 运行异常，请查看 git diff / git status。"
                    else
                        log_error "更新成功，但恢复本地修改失败。您的修改仍保存在 git stash 中。"
                        log_info "手动恢复: git stash apply $autostash_ref"
                        exit 1
                    fi
                else
                    log_info "已跳过恢复本地修改。"
                    log_info "您的修改仍保存在 git stash 中。"
                    log_info "手动恢复: git stash apply $autostash_ref"
                fi
            fi
        else
            log_error "目录已存在但不是 Git 仓库: $INSTALL_DIR"
            log_info "请删除该目录或使用 --dir 指定其他目录"
            exit 1
        fi
    else
        # Try SSH first (for private repo access), fall back to HTTPS
        # GIT_SSH_COMMAND disables interactive prompts and sets a short timeout
        # so SSH fails fast instead of hanging when no key is configured.
        log_info "正在尝试 SSH 克隆..."
        if GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5" \
           git clone --branch "$BRANCH" "$REPO_URL_SSH" "$INSTALL_DIR" 2>/dev/null; then
            log_success "已通过 SSH 克隆"
        else
            rm -rf "$INSTALL_DIR" 2>/dev/null  # Clean up partial SSH clone
            log_info "SSH 失败，正在尝试 HTTPS..."
            if git clone --branch "$BRANCH" "$REPO_URL_HTTPS" "$INSTALL_DIR"; then
                log_success "已通过 HTTPS 克隆"
            else
                log_error "克隆仓库失败"
                exit 1
            fi
        fi
    fi

    cd "$INSTALL_DIR"

    log_success "仓库就绪"
}

setup_venv() {
    if [ "$USE_VENV" = false ]; then
        log_info "跳过虚拟环境 (--no-venv)"
        return 0
    fi

    log_info "正在创建 Python $PYTHON_VERSION 虚拟环境..."

    if [ -d "venv" ]; then
        log_info "虚拟环境已存在，正在重新创建..."
        rm -rf venv
    fi

    # uv creates the venv and pins the Python version in one step
    $UV_CMD venv venv --python "$PYTHON_VERSION"

    log_success "虚拟环境就绪 (Python $PYTHON_VERSION)"
}

install_deps() {
    log_info "正在安装依赖..."

    if [ "$USE_VENV" = true ]; then
        # Tell uv to install into our venv (no need to activate)
        export VIRTUAL_ENV="$INSTALL_DIR/venv"
    fi

    # On Debian/Ubuntu (including WSL), some Python packages need build tools.
    # Check and offer to install them if missing.
    if [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
        local need_build_tools=false
        for pkg in gcc python3-dev libffi-dev; do
            if ! dpkg -s "$pkg" &>/dev/null; then
                need_build_tools=true
                break
            fi
        done
        if [ "$need_build_tools" = true ]; then
            log_info "某些构建工具可能需要用于 Python 包..."
            if command -v sudo &> /dev/null; then
                if sudo -n true 2>/dev/null; then
                    sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq build-essential python3-dev libffi-dev >/dev/null 2>&1 || true
                    log_success "构建工具安装成功"
                else
                    log_info "sudo 仅用于通过 apt 安装构建工具 (build-essential, python3-dev, libffi-dev)。"
                    log_info "KClaw Agent 本身不需要也不会保留 root 权限。"
                    read -p "是否安装构建工具? [Y/n] " -n 1 -r < /dev/tty
                    echo
                    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
                        sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq build-essential python3-dev libffi-dev >/dev/null 2>&1 || true
                        log_success "构建工具安装成功"
                    fi
                fi
            fi
        fi
    fi

    # Install the main package in editable mode with all extras.
    # Try [all] first, fall back to base install if extras have issues.
    ALL_INSTALL_LOG=$(mktemp)
    if ! $UV_CMD pip install -e ".[all]" 2>"$ALL_INSTALL_LOG"; then
        log_warn "完整安装 (.[all]) 失败，正在尝试基础安装..."
        log_info "原因: $(tail -5 "$ALL_INSTALL_LOG" | head -3)"
        rm -f "$ALL_INSTALL_LOG"
        if ! $UV_CMD pip install -e "."; then
            log_error "包安装失败。"
            log_info "请确认构建工具已安装: sudo apt install build-essential python3-dev"
            log_info "然后重新运行: cd $INSTALL_DIR && uv pip install -e '.[all]'"
            exit 1
        fi
    else
        rm -f "$ALL_INSTALL_LOG"
    fi

    log_success "主包安装成功"

    # tinker-atropos (RL training) is optional — skip by default.
    # To enable RL tools: git submodule update --init tinker-atropos && uv pip install -e "./tinker-atropos"
    if [ -d "tinker-atropos" ] && [ -f "tinker-atropos/pyproject.toml" ]; then
        log_info "检测到 tinker-atropos 子模块 — 跳过安装 (可选，用于 RL 训练)"
        log_info "  安装命令: $UV_CMD pip install -e \"./tinker-atropos\""
    fi

    log_success "所有依赖安装完成"
}

setup_path() {
    log_info "正在设置 kclaw 命令..."

    if [ "$USE_VENV" = true ]; then
        KCLAW_BIN="$INSTALL_DIR/venv/bin/kclaw"
    else
        KCLAW_BIN="$(which kclaw 2>/dev/null || echo "")"
        if [ -z "$KCLAW_BIN" ]; then
            log_warn "安装后未在 PATH 中找到 kclaw"
            return 0
        fi
    fi

    # Verify the entry point script was actually generated
    if [ ! -x "$KCLAW_BIN" ]; then
        log_warn "未在 $KCLAW_BIN 找到 kclaw 入口点"
        log_info "这通常意味着 pip 安装未成功完成。"
        log_info "请尝试: cd $INSTALL_DIR && uv pip install -e '.[all]'"
        return 0
    fi

    # Create symlink in ~/.local/bin (standard user binary location, usually on PATH)
    mkdir -p "$HOME/.local/bin"
    ln -sf "$KCLAW_BIN" "$HOME/.local/bin/kclaw"
    log_success "已创建符号链接 kclaw → ~/.local/bin/kclaw"

    # Check if ~/.local/bin is on PATH; if not, add it to shell config.
    # Detect the user's actual login shell (not the shell running this script,
    # which is always bash when piped from curl).
    if ! echo "$PATH" | tr ':' '\n' | grep -q "^$HOME/.local/bin$"; then
        SHELL_CONFIGS=()
        LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
        case "$LOGIN_SHELL" in
            zsh)
                [ -f "$HOME/.zshrc" ] && SHELL_CONFIGS+=("$HOME/.zshrc")
                [ -f "$HOME/.zprofile" ] && SHELL_CONFIGS+=("$HOME/.zprofile")
                # If neither exists, create ~/.zshrc (common on fresh macOS installs)
                if [ ${#SHELL_CONFIGS[@]} -eq 0 ]; then
                    touch "$HOME/.zshrc"
                    SHELL_CONFIGS+=("$HOME/.zshrc")
                fi
                ;;
            bash)
                [ -f "$HOME/.bashrc" ] && SHELL_CONFIGS+=("$HOME/.bashrc")
                [ -f "$HOME/.bash_profile" ] && SHELL_CONFIGS+=("$HOME/.bash_profile")
                ;;
            *)
                [ -f "$HOME/.bashrc" ] && SHELL_CONFIGS+=("$HOME/.bashrc")
                [ -f "$HOME/.zshrc" ] && SHELL_CONFIGS+=("$HOME/.zshrc")
                ;;
        esac
        # Also ensure ~/.profile has it (sourced by login shells on
        # Ubuntu/Debian/WSL even when ~/.bashrc is skipped)
        [ -f "$HOME/.profile" ] && SHELL_CONFIGS+=("$HOME/.profile")

        PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

        for SHELL_CONFIG in "${SHELL_CONFIGS[@]}"; do
            if ! grep -v '^[[:space:]]*#' "$SHELL_CONFIG" 2>/dev/null | grep -qE 'PATH=.*\.local/bin'; then
                echo "" >> "$SHELL_CONFIG"
                echo "# KClaw Agent — ensure ~/.local/bin is on PATH" >> "$SHELL_CONFIG"
                echo "$PATH_LINE" >> "$SHELL_CONFIG"
                log_success "已将 ~/.local/bin 添加到 PATH ($SHELL_CONFIG)"
            fi
        done

        if [ ${#SHELL_CONFIGS[@]} -eq 0 ]; then
            log_warn "无法检测 shell 配置文件以添加 ~/.local/bin 到 PATH"
            log_info "手动添加: $PATH_LINE"
        fi
    else
        log_info "~/.local/bin 已在 PATH 中"
    fi

    # Export for current session so kclaw works immediately
    export PATH="$HOME/.local/bin:$PATH"

    log_success "kclaw 命令就绪"
}

copy_config_templates() {
    log_info "正在设置配置文件..."

    # Create ~/.kclaw directory structure (config at top level, code in subdir)
    mkdir -p "$KCLAW_HOME"/{cron,sessions,logs,pairing,hooks,image_cache,audio_cache,memories,skills,whatsapp/session}

    # Create .env at ~/.kclaw/.env (top level, easy to find)
    if [ ! -f "$KCLAW_HOME/.env" ]; then
        if [ -f "$INSTALL_DIR/.env.example" ]; then
            cp "$INSTALL_DIR/.env.example" "$KCLAW_HOME/.env"
            log_success "已从模板创建 ~/.kclaw/.env"
        else
            touch "$KCLAW_HOME/.env"
            log_success "已创建 ~/.kclaw/.env"
        fi
    else
        log_info "~/.kclaw/.env 已存在，保留不变"
    fi

    # Create config.yaml at ~/.kclaw/config.yaml (top level, easy to find)
    if [ ! -f "$KCLAW_HOME/config.yaml" ]; then
        if [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
            cp "$INSTALL_DIR/cli-config.yaml.example" "$KCLAW_HOME/config.yaml"
            log_success "已从模板创建 ~/.kclaw/config.yaml"
        fi
    else
        log_info "~/.kclaw/config.yaml 已存在，保留不变"
    fi

    # Create SOUL.md if it doesn't exist (global persona file)
    if [ ! -f "$KCLAW_HOME/SOUL.md" ]; then
        cat > "$KCLAW_HOME/SOUL.md" << 'SOUL_EOF'
# KClaw Agent Persona

<!--
This file defines the agent's personality and tone.
The agent will embody whatever you write here.
Edit this to customize how KClaw communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->
SOUL_EOF
        log_success "已创建 ~/.kclaw/SOUL.md (编辑以自定义个性)"
    fi

    log_success "配置目录就绪: ~/.kclaw/"

    # Seed bundled skills into ~/.kclaw/skills/ (manifest-based, one-time per skill)
    log_info "正在同步内置技能到 ~/.kclaw/skills/ ..."
    if "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/tools/skills_sync.py" 2>/dev/null; then
        log_success "技能已同步到 ~/.kclaw/skills/"
    else
        # Fallback: simple directory copy if Python sync fails
        if [ -d "$INSTALL_DIR/skills" ] && [ ! "$(ls -A "$KCLAW_HOME/skills/" 2>/dev/null | grep -v '.bundled_manifest')" ]; then
            cp -r "$INSTALL_DIR/skills/"* "$KCLAW_HOME/skills/" 2>/dev/null || true
            log_success "技能已复制到 ~/.kclaw/skills/"
        fi
    fi
}

install_node_deps() {
    if [ "$HAS_NODE" = false ]; then
        log_info "跳过 Node.js 依赖 (未安装 Node.js)"
        return 0
    fi

    if [ -f "$INSTALL_DIR/package.json" ]; then
        log_info "正在安装 Node.js 依赖 (浏览器工具)..."
        cd "$INSTALL_DIR"
        npm install --silent 2>/dev/null || {
            log_warn "npm install 失败 (浏览器工具可能不可用)"
        }
        log_success "Node.js 依赖安装完成"

        # Install Playwright browser + system dependencies.
        # Playwright's install-deps only supports apt/dnf/zypper natively.
        # For Arch/Manjaro we install the system libs via pacman first.
        log_info "正在安装浏览器引擎 (Playwright Chromium)..."
        case "$DISTRO" in
            arch|manjaro)
                if command -v pacman &> /dev/null; then
                    log_info "检测到 Arch/Manjaro — 正在通过 pacman 安装 Chromium 系统依赖..."
                    if command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
                        sudo NEEDRESTART_MODE=a pacman -S --noconfirm --needed \
                            nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib >/dev/null 2>&1 || true
                    elif [ "$(id -u)" -eq 0 ]; then
                        pacman -S --noconfirm --needed \
                            nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib >/dev/null 2>&1 || true
                    else
                        log_warn "无法在没有 sudo 的情况下安装浏览器依赖。手动运行:"
                        log_warn "  sudo pacman -S nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib"
                    fi
                fi
                cd "$INSTALL_DIR" && npx playwright install chromium 2>/dev/null || true
                ;;
            *)
                log_info "Playwright 可能需要 sudo 来安装浏览器系统依赖 (共享库)。"
                log_info "这是标准的 Playwright 设置 — KClaw 本身不需要 root 权限。"
                cd "$INSTALL_DIR" && npx playwright install --with-deps chromium 2>/dev/null || true
                ;;
        esac
        log_success "浏览器引擎安装完成"
    fi

    # Install WhatsApp bridge dependencies
    if [ -f "$INSTALL_DIR/scripts/whatsapp-bridge/package.json" ]; then
        log_info "正在安装 WhatsApp 桥接依赖..."
        cd "$INSTALL_DIR/scripts/whatsapp-bridge"
        npm install --silent 2>/dev/null || {
            log_warn "WhatsApp 桥接 npm install 失败 (WhatsApp 可能不可用)"
        }
        log_success "WhatsApp 桥接依赖安装完成"
    fi
}

run_setup_wizard() {
    if [ "$RUN_SETUP" = false ]; then
        log_info "跳过设置向导 (--skip-setup)"
        return 0
    fi

    # The setup wizard reads from /dev/tty, so it works even when the
    # install script itself is piped (curl | bash). Only skip if no
    # terminal is available at all (e.g. Docker build, CI).
    if ! [ -e /dev/tty ]; then
        log_info "设置向导已跳过 (无可用终端)。安装后请运行 'kclaw setup'。"
        return 0
    fi

    echo ""
    log_info "正在启动设置向导..."
    echo ""

    cd "$INSTALL_DIR"

    # Run kclaw setup using the venv Python directly (no activation needed).
    # Redirect stdin from /dev/tty so interactive prompts work when piped from curl.
    if [ "$USE_VENV" = true ]; then
        "$INSTALL_DIR/venv/bin/python" -m kclaw_cli.main setup < /dev/tty
    else
        python -m kclaw_cli.main setup < /dev/tty
    fi
}

maybe_start_gateway() {
    # Check if any messaging platform tokens were configured
    ENV_FILE="$KCLAW_HOME/.env"
    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi

    HAS_MESSAGING=false
    for VAR in TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN SLACK_BOT_TOKEN SLACK_APP_TOKEN WHATSAPP_ENABLED; do
        VAL=$(grep "^${VAR}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
        if [ -n "$VAL" ] && [ "$VAL" != "your-token-here" ]; then
            HAS_MESSAGING=true
            break
        fi
    done

    if [ "$HAS_MESSAGING" = false ]; then
        return 0
    fi

    echo ""
    log_info "检测到消息平台令牌!"
    log_info "网关需要运行才能让 KClaw 收发消息。"

    # If WhatsApp is enabled and no session exists yet, run foreground first for QR scan
    WHATSAPP_VAL=$(grep "^WHATSAPP_ENABLED=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
    WHATSAPP_SESSION="$KCLAW_HOME/whatsapp/session/creds.json"
    if [ "$WHATSAPP_VAL" = "true" ] && [ ! -f "$WHATSAPP_SESSION" ]; then
        if [ "$IS_INTERACTIVE" = true ]; then
            echo ""
            log_info "WhatsApp 已启用但尚未配对。"
            log_info "正在运行 'kclaw whatsapp' 以通过二维码配对..."
            echo ""
            read -p "是否现在配对 WhatsApp? [Y/n] " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
                KCLAW_CMD="$HOME/.local/bin/kclaw"
                [ ! -x "$KCLAW_CMD" ] && KCLAW_CMD="kclaw"
                $KCLAW_CMD whatsapp || true
            fi
        else
            log_info "WhatsApp 配对已跳过 (非交互模式)。运行 'kclaw whatsapp' 进行配对。"
        fi
    fi

    if ! [ -e /dev/tty ]; then
        log_info "网关设置已跳过 (无可用终端)。稍后运行 'kclaw gateway install'。"
        return 0
    fi

    echo ""
    read -p "是否将网关安装为后台服务? [Y/n] " -n 1 -r < /dev/tty
    echo

    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        KCLAW_CMD="$HOME/.local/bin/kclaw"
        if [ ! -x "$KCLAW_CMD" ]; then
            KCLAW_CMD="kclaw"
        fi

        if command -v systemctl &> /dev/null; then
            log_info "正在安装 systemd 服务..."
            if $KCLAW_CMD gateway install 2>/dev/null; then
                log_success "网关服务已安装"
                if $KCLAW_CMD gateway start 2>/dev/null; then
                    log_success "网关已启动! 您的机器人现在在线。"
                else
                    log_warn "服务已安装但启动失败。请尝试: kclaw gateway start"
                fi
            else
                log_warn "systemd 安装失败。您可以手动启动: kclaw gateway"
            fi
        else
            log_info "systemd 不可用 — 正在后台启动网关..."
            nohup $KCLAW_CMD gateway > "$KCLAW_HOME/logs/gateway.log" 2>&1 &
            GATEWAY_PID=$!
            log_success "网关已启动 (PID $GATEWAY_PID)。日志: ~/.kclaw/logs/gateway.log"
            log_info "停止: kill $GATEWAY_PID"
            log_info "稍后重启: kclaw gateway"
        fi
    else
        log_info "已跳过。稍后启动网关: kclaw gateway"
    fi
}

print_success() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│              ✓ 安装完成!                              │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo ""

    # Show file locations
    echo -e "${CYAN}${BOLD}📁 您的文件 (全部位于 ~/.kclaw/):${NC}"
    echo ""
    echo -e "   ${YELLOW}配置文件:${NC}    ~/.kclaw/config.yaml"
    echo -e "   ${YELLOW}API 密钥:${NC}  ~/.kclaw/.env"
    echo -e "   ${YELLOW}数据:${NC}        ~/.kclaw/cron/, sessions/, logs/"
    echo -e "   ${YELLOW}代码:${NC}        ~/.kclaw/kclaw/"
    echo ""

    echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
    echo ""
    echo -e "${CYAN}${BOLD}🚀 常用命令:${NC}"
    echo ""
    echo -e "   ${GREEN}kclaw${NC}              开始对话"
    echo -e "   ${GREEN}kclaw setup${NC}        配置 API 密钥和设置"
    echo -e "   ${GREEN}kclaw config${NC}       查看/编辑配置"
    echo -e "   ${GREEN}kclaw config edit${NC}  在编辑器中打开配置"
    echo -e "   ${GREEN}kclaw gateway install${NC} 安装网关服务 (消息 + 定时任务)"
    echo -e "   ${GREEN}kclaw update${NC}       更新到最新版本"
    echo ""

    echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
    echo ""
    echo -e "${YELLOW}⚡ 请重新加载 shell 以使用 'kclaw' 命令:${NC}"
    echo ""
    LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
    if [ "$LOGIN_SHELL" = "zsh" ]; then
        echo "   source ~/.zshrc"
    elif [ "$LOGIN_SHELL" = "bash" ]; then
        echo "   source ~/.bashrc"
    else
        echo "   source ~/.bashrc   # 或 ~/.zshrc"
    fi
    echo ""

    # Show Node.js warning if auto-install failed
    if [ "$HAS_NODE" = false ]; then
        echo -e "${YELLOW}"
        echo "注意: 无法自动安装 Node.js。"
        echo "浏览器工具需要 Node.js。手动安装:"
        echo "  https://nodejs.org/en/download/"
        echo -e "${NC}"
    fi

    # Show ripgrep note if not installed
    if [ "$HAS_RIPGREP" = false ]; then
        echo -e "${YELLOW}"
        echo "注意: 未找到 ripgrep (rg)。文件搜索将使用"
        echo "grep 作为备选方案。如需在大型代码库中更快搜索，"
        echo "安装 ripgrep: sudo apt install ripgrep (或 brew install ripgrep)"
        echo -e "${NC}"
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    print_banner

    detect_os
    install_uv
    check_python
    check_git
    check_node
    install_system_packages

    clone_repo
    setup_venv
    install_deps
    install_node_deps
    setup_path
    copy_config_templates
    run_setup_wizard
    maybe_start_gateway

    print_success
}

main
