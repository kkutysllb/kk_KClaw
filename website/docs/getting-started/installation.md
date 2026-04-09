---
sidebar_position: 2
title: "安装"
description: "在 Linux、macOS 或 WSL2 上安装 KClaw Agent"
---

# 安装

使用一键安装程序在两分钟内启动并运行 KClaw Agent，或按照手动步骤进行完全控制。

## 快速安装

### Linux / macOS / WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/kclaw/main/scripts/install.sh | bash
```

:::warning Windows
原生 Windows **不受支持**。请先安装 [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install)，然后从那里运行 KClaw Agent。上面的安装命令在 WSL2 内部有效。
:::

### 安装程序的作用

安装程序自动处理一切——所有依赖项（Python、Node.js、ripgrep、ffmpeg）、仓库克隆、虚拟环境、全局 `kclaw` 命令设置和 LLM 提供商配置。结束时，您已准备好聊天。

### 安装后

重新加载 shell 并开始聊天：

```bash
source ~/.bashrc   # 或：source ~/.zshrc
kclaw             # 开始聊天！
```

以后要重新配置单个设置，请使用专用命令：

```bash
kclaw model          # 选择您的 LLM 提供商和模型
kclaw tools          # 配置启用哪些工具
kclaw gateway setup  # 设置消息平台
kclaw config set     # 设置单个配置值
kclaw setup          # 或运行完整设置向导一次配置所有内容
```

---

## 前置要求

唯一的前提条件是 **Git**。安装程序自动处理其他所有内容：

- **uv**（快速 Python 包管理器）
- **Python 3.11**（通过 uv，无需 sudo）
- **Node.js v22**（用于浏览器自动化和 WhatsApp 桥接）
- **ripgrep**（快速文件搜索）
- **ffmpeg**（TTS 音频格式转换）

:::info
您**不需要**手动安装 Python、Node.js、ripgrep 或 ffmpeg。安装程序检测缺失的内容并为您安装。只需确保 `git` 可用（`git --version`）。
:::

:::tip Nix 用户
如果您使用 Nix（NixOS、macOS 或 Linux），有一条专用设置路径，包含 Nix flake、声明式 NixOS 模块和可选容器模式。请参阅 **[Nix 和 NixOS 设置](./nix-setup.md)** 指南。
:::

---

## 手动安装

如果您喜欢完全控制安装过程，请按照以下步骤操作。

### 步骤 1：克隆仓库

使用 `--recurse-submodules` 克隆以拉取所需的子模块：

```bash
git clone --recurse-submodules https://github.com/NousResearch/kclaw.git
cd kclaw
```

如果已经克隆但没有 `--recurse-submodules`：
```bash
git submodule update --init --recursive
```

### 步骤 2：安装 uv 和创建虚拟环境

```bash
# 安装 uv（如果尚未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 使用 Python 3.11 创建 venv（uv 会下载它（如果不存在）——无需 sudo）
uv venv venv --python 3.11
```

:::tip
您**不需要**激活 venv 来使用 `kclaw`。入口点有一个硬编码的 shebang 指向 venv Python，因此一旦符号链接，它就可以全局工作。
:::

### 步骤 3：安装 Python 依赖

```bash
# 告诉 uv 要安装到哪个 venv
export VIRTUAL_ENV="$(pwd)/venv"

# 安装所有额外组件
uv pip install -e ".[all]"
```

如果只需要核心代理（无 Telegram/Discord/cron 支持）：
```bash
uv pip install -e "."
```

<details>
<summary><strong>可选额外组件细分</strong></summary>

| 额外组件 | 内容 | 安装命令 |
|-------|-------------|-----------------|
| `all` | 以下所有内容 | `uv pip install -e ".[all]"` |
| `messaging` | Telegram 和 Discord 网关 | `uv pip install -e ".[messaging]"` |
| `cron` | 计划任务的 Cron 表达式解析 | `uv pip install -e ".[cron]"` |
| `cli` | 设置向导的终端菜单 UI | `uv pip install -e ".[cli]"` |
| `modal` | Modal 云执行后端 | `uv pip install -e ".[modal]"` |
| `tts-premium` | ElevenLabs 高级语音 | `uv pip install -e ".[tts-premium]"` |
| `voice` | CLI 麦克风输入和音频播放 | `uv pip install -e ".[voice]"` |
| `pty` | PTY 终端支持 | `uv pip install -e ".[pty]"` |
| `honcho` | AI 原生记忆（Honcho 集成） | `uv pip install -e ".[honcho]"` |
| `mcp` | 模型上下文协议支持 | `uv pip install -e ".[mcp]"` |
| `homeassistant` | Home Assistant 集成 | `uv pip install -e ".[homeassistant]"` |
| `acp` | ACP 编辑器集成支持 | `uv pip install -e ".[acp]"` |
| `slack` | Slack 消息 | `uv pip install -e ".[slack]"` |
| `dev` | pytest 和测试工具 | `uv pip install -e ".[dev]"` |

您可以组合额外组件：`uv pip install -e ".[messaging,cron]"`

</details>

### 步骤 4：安装可选子模块（如需要）

```bash
# RL 训练后端（可选）
uv pip install -e "./tinker-atropos"
```

两者都是可选的——如果跳过它们，相应的工具集 simply 将不可用。

### 步骤 5：安装 Node.js 依赖（可选）

仅在**浏览器自动化**（Browserbase 支持）和**WhatsApp 桥接**时需要：

```bash
npm install
```

### 步骤 6：创建配置目录

```bash
# 创建目录结构
mkdir -p ~/.kclaw/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session}

# 复制示例配置文件
cp cli-config.yaml.example ~/.kclaw/config.yaml

# 创建一个空的 .env 文件用于 API 密钥
touch ~/.kclaw/.env
```

### 步骤 7：添加您的 API 密钥

打开 `~/.kclaw/.env` 并至少添加一个 LLM 提供商密钥：

```bash
# 必需——至少一个 LLM 提供商：
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# 可选——启用其他工具：
FIRECRAWL_API_KEY=fc-your-key          # Web 搜索和抓取（或自托管，参见文档）
FAL_KEY=your-fal-key                   # 图像生成（FLUX）
```

或通过 CLI 设置它们：
```bash
kclaw config set OPENROUTER_API_KEY sk-or-v1-your-key-here
```

### 步骤 8：将 `kclaw` 添加到您的 PATH

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/venv/bin/kclaw" ~/.local/bin/kclaw
```

如果 `~/.local/bin` 不在您的 PATH 上，请将其添加到您的 shell 配置中：

```bash
# Bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc

# Zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# Fish
fish_add_path $HOME/.local/bin
```

### 步骤 9：配置您的提供商

```bash
kclaw model       # 选择您的 LLM 提供商和模型
```

### 步骤 10：验证安装

```bash
kclaw version    # 检查命令是否可用
kclaw doctor     # 运行诊断以验证一切正常
kclaw status     # 检查您的配置
kclaw chat -q "你好！您有哪些可用工具？"
```

---

## 快速参考：手动安装（精简版）

对于只想要命令的人：

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆并进入
git clone --recurse-submodules https://github.com/NousResearch/kclaw.git
cd kclaw

# 使用 Python 3.11 创建 venv
uv venv venv --python 3.11
export VIRTUAL_ENV="$(pwd)/venv"

# 安装所有内容
uv pip install -e ".[all]"
uv pip install -e "./tinker-atropos"
npm install  # 可选，用于浏览器工具和 WhatsApp

# 配置
mkdir -p ~/.kclaw/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session}
cp cli-config.yaml.example ~/.kclaw/config.yaml
touch ~/.kclaw/.env
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key' >> ~/.kclaw/.env

# 使 kclaw 全局可用
mkdir -p ~/.local/bin
ln -sf "$(pwd)/venv/bin/kclaw" ~/.local/bin/kclaw

# 验证
kclaw doctor
kclaw
```

---

## 故障排除

| 问题 | 解决方案 |
|---------|----------|
| `kclaw: command not found` | 重新加载 shell（`source ~/.bashrc`）或检查 PATH |
| `API key not set` | 运行 `kclaw model` 配置您的提供商，或 `kclaw config set OPENROUTER_API_KEY your_key` |
| 更新后缺少配置 | 运行 `kclaw config check` 然后 `kclaw config migrate` |

要获取更多诊断信息，请运行 `kclaw doctor`——它会准确告诉您缺少什么以及如何修复。
