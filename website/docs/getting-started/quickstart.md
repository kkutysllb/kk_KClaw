---
sidebar_position: 1
title: "快速入门"
description: "KClaw Agent 的首次对话——从安装到 2 分钟内聊天"
---

# 快速入门

本指南引导您安装 KClaw Agent、设置提供商并进行首次对话。结束时，您将了解关键功能以及如何进一步探索。

## 1. 安装 KClaw Agent

运行一键安装程序：

```bash
# Linux / macOS / WSL2
curl -fsSL https://raw.githubusercontent.com/NousResearch/kclaw/main/scripts/install.sh | bash
```

:::tip Windows 用户
首先安装 [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install)，然后在 WSL2 终端内运行上面的命令。
:::

完成后，重新加载您的 shell：

```bash
source ~/.bashrc   # 或 source ~/.zshrc
```

## 2. 设置提供商

安装程序自动配置您的 LLM 提供商。以后要更改它，请使用以下命令之一：

```bash
kclaw model       # 选择您的 LLM 提供商和模型
kclaw tools       # 配置启用哪些工具
kclaw setup       # 或一次配置所有内容
```

`kclaw model` 引导您选择推理提供商：

| 提供商 | 内容 | 如何设置 |
|----------|-----------|------------|
| **Nous Portal** | 基于订阅，零配置 | 通过 `kclaw model` 进行 OAuth 登录 |
| **OpenAI Codex** | ChatGPT OAuth，使用 Codex 模型 | 通过 `kclaw model` 进行设备代码认证 |
| **Anthropic** | 直接使用 Claude 模型（Pro/Max 或 API 密钥） | 使用 Claude Code 认证的 `kclaw model`，或 Anthropic API 密钥 |
| **OpenRouter** | 跨多个模型的多元提供商路由 | 输入您的 API 密钥 |
| **Z.AI** | GLM / Zhipu 托管模型 | 设置 `GLM_API_KEY` / `ZAI_API_KEY` |
| **Kimi / Moonshot** | Moonshot 托管的编码和聊天模型 | 设置 `KIMI_API_KEY` |
| **MiniMax** | 国际 MiniMax 端点 | 设置 `MINIMAX_API_KEY` |
| **MiniMax China** | 中国区 MiniMax 端点 | 设置 `MINIMAX_CN_API_KEY` |
| **Alibaba Cloud** | 通过 DashScope 的 Qwen 模型 | 设置 `DASHSCOPE_API_KEY` |
| **Hugging Face** | 通过统一路由器访问 20+ 开放模型（Qwen、DeepSeek、Kimi 等） | 设置 `HF_TOKEN` |
| **Kilo Code** | KiloCode 托管的模型 | 设置 `KILOCODE_API_KEY` |
| **OpenCode Zen** | 按需访问精选模型 | 设置 `OPENCODE_ZEN_API_KEY` |
| **OpenCode Go** | 10 美元/月订阅开放模型 | 设置 `OPENCODE_GO_API_KEY` |
| **DeepSeek** | 直接 DeepSeek API 访问 | 设置 `DEEPSEEK_API_KEY` |
| **GitHub Copilot** | GitHub Copilot 订阅（GPT-5.x、Claude、Gemini 等） | 通过 `kclaw model` 进行 OAuth，或 `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` |
| **GitHub Copilot ACP** | Copilot ACP 代理后端（生成本地 `copilot` CLI） | `kclaw model`（需要 `copilot` CLI + `copilot login`） |
| **Vercel AI Gateway** | Vercel AI Gateway 路由 | 设置 `AI_GATEWAY_API_KEY` |
| **自定义端点** | VLLM、SGLang、Ollama 或任何 OpenAI 兼容 API | 设置基础 URL + API 密钥 |

:::tip
您可以随时通过 `kclaw model` 切换提供商——无需代码更改，无锁定。配置自定义端点时，KClaw 会提示上下文窗口大小，并在可能时自动检测。请参阅[上下文长度检测](../integrations/providers.md#context-length-detection)了解详细信息。
:::

## 3. 开始聊天

```bash
kclaw
```

就这样！您将看到欢迎横幅，显示您的模型、可用工具和技能。输入消息并按 Enter。

```
❯ 您可以帮我做什么？
```

代理可以访问用于 Web 搜索、文件操作、终端命令等的工具——开箱即用。

## 4. 尝试关键功能

### 让它使用终端

```
❯ 我的磁盘使用情况如何？显示前 5 个最大的目录。
```

代理将代表您运行终端命令并向您显示结果。

### 使用斜杠命令

输入 `/` 查看所有命令的自动完成下拉列表：

| 命令 | 功能 |
|---------|-------------|
| `/help` | 显示所有可用命令 |
| `/tools` | 列出可用工具 |
| `/model` | 交互式切换模型 |
| `/personality pirate` | 尝试有趣的人格 |
| `/save` | 保存对话 |

### 多行输入

按 `Alt+Enter` 或 `Ctrl+J` 添加新行。非常适合粘贴代码或编写详细提示。

### 中断代理

如果代理花费时间太长，只需输入新消息并按 Enter——它会中断当前任务并切换到您的新指令。`Ctrl+C` 也可以工作。

### 恢复会话

退出时，kclaw 会打印恢复命令：

```bash
kclaw --continue    # 恢复最近的会话
kclaw -c            # 简短形式
```

## 5. 进一步探索

以下是接下来可以尝试的一些内容：

### 设置沙盒终端

为了安全起见，在 Docker 容器或远程服务器上运行代理：

```bash
kclaw config set terminal.backend docker    # Docker 隔离
kclaw config set terminal.backend ssh       # 远程服务器
```

### 连接消息平台

通过 Telegram、Discord、Slack、WhatsApp、Signal、Email 或 Home Assistant 从手机或其他界面与 KClaw 聊天：

```bash
kclaw gateway setup    # 交互式平台配置
```

### 添加语音模式

想要在 CLI 中输入麦克风或在消息中获得语音回复吗？

```bash
pip install "kclaw[voice]"

# 对于免费本地语音转文本是可选的但推荐
pip install faster-whisper
```

然后启动 KClaw 并在 CLI 内启用它：

```text
/voice on
```

按 `Ctrl+B` 录制，或使用 `/voice tts` 让 KClaw 说回复。请参阅 [语音模式](../user-guide/features/voice-mode.md) 了解 CLI、Telegram、Discord 和 Discord 语音频道的完整设置。

### 计划自动化任务

```
❯ 每天早上 9 点检查 Hacker News 上的 AI 新闻，并在 Telegram 上给我发送摘要。
```

代理将设置一个通过网关自动运行的 cron 作业。

### 浏览和安装技能

```bash
kclaw skills search kubernetes
kclaw skills search react --source skills-sh
kclaw skills search https://mintlify.com/docs --source well-known
kclaw skills install openai/skills/k8s
kclaw skills install official/security/1password
kclaw skills install skills-sh/vercel-labs/json-render/json-render-react --force
```

提示：
- 使用 `--source skills-sh` 搜索公共 `skills.sh` 目录。
- 使用 `--source well-known` 和 docs/site URL 从 `/.well-known/skills/index.json` 发现技能。
- 仅在审查第三方技能后使用 `--force`。它可以覆盖非危险策略块，但不能覆盖危险扫描判定。

或在聊天中使用 `/skills` 斜杠命令。

### 通过 ACP 在编辑器中使用 KClaw

KClaw 还可以作为 ACP 服务器运行，适用于 ACP 兼容的编辑器，如 VS Code、Zed 和 JetBrains：

```bash
pip install -e '.[acp]'
kclaw acp
```

请参阅 [ACP 编辑器集成](../user-guide/features/acp.md) 了解设置详细信息。

### 尝试 MCP 服务器

通过模型上下文协议连接到外部工具：

```yaml
# 添加到 ~/.kclaw/config.yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_xxx"
```

---

## 快速参考

| 命令 | 描述 |
|---------|-------------|
| `kclaw` | 开始聊天 |
| `kclaw model` | 选择您的 LLM 提供商和模型 |
| `kclaw tools` | 配置每个平台启用哪些工具 |
| `kclaw setup` | 完整设置向导（一次配置所有内容） |
| `kclaw doctor` | 诊断问题 |
| `kclaw update` | 更新到最新版本 |
| `kclaw gateway` | 启动消息网关 |
| `kclaw --continue` | 恢复上一个会话 |

## 下一步

- **[CLI 指南](../user-guide/cli.md)** — 掌握终端界面
- **[配置](../user-guide/configuration.md)** — 自定义您的设置
- **[消息网关](../user-guide/messaging/index.md)** — 连接 Telegram、Discord、Slack、WhatsApp、Signal、Email 或 Home Assistant
- **[工具和工具集](../user-guide/features/tools.md)** — 探索可用功能
