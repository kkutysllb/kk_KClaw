---
sidebar_position: 10
title: "从 OpenClaw 迁移"
description: "将您的 OpenClaw / Clawdbot 设置迁移到 KClaw Agent 的完整指南——迁移什么、配置如何映射以及迁移后要检查什么。"
---

# 从 OpenClaw 迁移

`kclaw claw migrate` 将您的 OpenClaw（或旧版 Clawdbot/Moldbot）设置导入 KClaw。本指南涵盖准确迁移什么、配置键映射以及迁移后要验证什么。

## 快速开始

```bash
# 预览会发生什么（不更改任何文件）
kclaw claw migrate --dry-run

# 运行迁移（默认排除密钥）
kclaw claw migrate

# 包含 API 密钥的完整迁移
kclaw claw migrate --preset full
```

迁移默认从 `~/.openclaw/` 读取。如果您仍有旧版 `~/.clawdbot/` 或 `~/.moldbot/` 目录，它会自动被检测到。旧版配置文件名（`clawdbot.json`、`moldbot.json`）也是如此。

## 选项

| 选项 | 描述 |
|--------|-------------|
| `--dry-run` | 预览将迁移什么，不写任何内容。 |
| `--preset <name>` | `full`（默认，包含密钥）或 `user-data`（排除 API 密钥）。 |
| `--overwrite` | 覆盖冲突的现有 KClaw 文件（默认：跳过）。 |
| `--migrate-secrets` | 包含 API 密钥（默认与 `--preset full` 一起启用）。 |
| `--source <path>` | 自定义 OpenClaw 目录。 |
| `--workspace-target <path>` | 放置 `AGENTS.md` 的位置。 |
| `--skill-conflict <mode>` | `skip`（默认）、`overwrite` 或 `rename`。 |
| `--yes` | 跳过确认提示。 |

## 迁移什么

### 角色、记忆和指令

| 内容 | OpenClaw 源 | KClaw 目标 | 备注 |
|------|----------------|-------------------|-------|
| 角色 | `workspace/SOUL.md` | `~/.kclaw/SOUL.md` | 直接复制 |
| Workspace 指令 | `workspace/AGENTS.md` | `--workspace-target` 中的 `AGENTS.md` | 需要 `--workspace-target` 标志 |
| 长期记忆 | `workspace/MEMORY.md` | `~/.kclaw/memories/MEMORY.md` | 解析为条目，与现有合并，去重。使用 `§` 分隔符。 |
| 用户资料 | `workspace/USER.md` | `~/.kclaw/memories/USER.md` | 与记忆相同的条目合并逻辑。 |
| 每日记忆文件 | `workspace/memory/*.md` | `~/.kclaw/memories/MEMORY.md` | 所有每日文件合并到主记忆。 |

所有 workspace 文件也检查 `workspace.default/` 作为后备路径。

### 技能（4 个来源）

| 源 | OpenClaw 位置 | KClaw 目标 |
|--------|------------------|-------------------|
| Workspace 技能 | `workspace/skills/` | `~/.kclaw/skills/openclaw-imports/` |
| 托管/共享技能 | `~/.openclaw/skills/` | `~/.kclaw/skills/openclaw-imports/` |
| 个人跨项目 | `~/.agents/skills/` | `~/.kclaw/skills/openclaw-imports/` |
| 项目级共享 | `workspace/.agents/skills/` | `~/.kclaw/skills/openclaw-imports/` |

技能冲突由 `--skill-conflict` 处理：`skip` 保留现有 KClaw 技能，`overwrite` 替换它，`rename` 创建一个 `-imported` 副本。

### 模型和提供商配置

| 内容 | OpenClaw 配置路径 | KClaw 目标 | 备注 |
|------|---------------------|-------------------|-------|
| 默认模型 | `agents.defaults.model` | `config.yaml` → `model` | 可以是字符串或 `{primary, fallbacks}` 对象 |
| 自定义提供商 | `models.providers.*` | `config.yaml` → `custom_providers` | 映射 `baseUrl`、`apiType`（"openai"→"chat_completions"、"anthropic"→"anthropic_messages"） |
| 提供商 API 密钥 | `models.providers.*.apiKey` | `~/.kclaw/.env` | 需要 `--migrate-secrets`。请参阅下面的 [API 密钥解析](#api-key-resolution)。 |

### 代理行为

| 内容 | OpenClaw 配置路径 | KClaw 配置路径 | 映射 |
|------|---------------------|-------------------|---------|
| 最大轮次 | `agents.defaults.timeoutSeconds` | `agent.max_turns` | `timeoutSeconds / 10`，上限为 200 |
| 详细模式 | `agents.defaults.verboseDefault` | `agent.verbose` | "off" / "on" / "full" |
| 推理努力 | `agents.defaults.thinkingDefault` | `agent.reasoning_effort` | "always"/"high" → "high"，"auto"/"medium" → "medium"，"off"/"low"/"none"/"minimal" → "low" |
| 压缩 | `agents.defaults.compaction.mode` | `compression.enabled` | "off" → false，其他任何内容 → true |
| 压缩模型 | `agents.defaults.compaction.model` | `compression.summary_model` | 直接字符串复制 |
| 人类延迟 | `agents.defaults.humanDelay.mode` | `human_delay.mode` | "natural" / "custom" / "off" |
| 人类延迟时间 | `agents.defaults.humanDelay.minMs` / `.maxMs` | `human_delay.min_ms` / `.max_ms` | 直接复制 |
| 时区 | `agents.defaults.userTimezone` | `timezone` | 直接字符串复制 |
| 执行超时 | `tools.exec.timeoutSec` | `terminal.timeout` | 直接复制（字段是 `timeoutSec`，不是 `timeout`） |
| Docker 沙盒 | `agents.defaults.sandbox.backend` | `terminal.backend` | "docker" → "docker" |
| Docker 镜像 | `agents.defaults.sandbox.docker.image` | `terminal.docker_image` | 直接复制 |

### 会话重置策略

| OpenClaw 配置路径 | KClaw 配置路径 | 备注 |
|---------------------|-------------------|-------|
| `session.reset.mode` | `session_reset.mode` | "daily"、"idle" 或两者 |
| `session.reset.atHour` | `session_reset.at_hour` | 每日重置的小时（0-23） |
| `session.reset.idleMinutes` | `session_reset.idle_minutes` | 不活动分钟数 |

注意：OpenClaw 也有 `session.resetTriggers`（一个简单的字符串数组如 `["daily", "idle"]`）。如果不存在结构化的 `session.reset`，迁移会回退到从 `resetTriggers` 推断。

### MCP 服务器

| OpenClaw 字段 | KClaw 字段 | 备注 |
|----------------|-------------|-------|
| `mcp.servers.*.command` | `mcp_servers.*.command` | Stdio 传输 |
| `mcp.servers.*.args` | `mcp_servers.*.args` | |
| `mcp.servers.*.env` | `mcp_servers.*.env` | |
| `mcp.servers.*.cwd` | `mcp_servers.*.cwd` | |
| `mcp.servers.*.url` | `mcp_servers.*.url` | HTTP/SSE 传输 |
| `mcp.servers.*.tools.include` | `mcp_servers.*.tools.include` | 工具过滤 |
| `mcp.servers.*.tools.exclude` | `mcp_servers.*.tools.exclude` | |

### TTS（文本转语音）

TTS 设置从**两个** OpenClaw 配置位置读取，优先级如下：

1. `messages.tts.providers.{provider}.*`（规范位置）
2. 顶级 `talk.providers.{provider}.*`（后备）
3. 旧版平面键 `messages.tts.{provider}.*`（最旧格式）

| 内容 | KClaw 目标 |
|------|-------------------|
| 提供商名称 | `config.yaml` → `tts.provider` |
| ElevenLabs 语音 ID | `config.yaml` → `tts.elevenlabs.voice_id` |
| ElevenLabs 模型 ID | `config.yaml` → `tts.elevenlabs.model_id` |
| OpenAI 模型 | `config.yaml` → `tts.openai.model` |
| OpenAI 语音 | `config.yaml` → `tts.openai.voice` |
| Edge TTS 语音 | `config.yaml` → `tts.edge.voice` |
| TTS 资产 | `~/.kclaw/tts/`（文件复制） |

### 消息平台

| 平台 | OpenClaw 配置路径 | KClaw `.env` 变量 | 备注 |
|----------|---------------------|----------------------|-------|
| Telegram | `channels.telegram.botToken` | `TELEGRAM_BOT_TOKEN` | 令牌可以是字符串或 [SecretRef](#secretref-handling) |
| Telegram | `credentials/telegram-default-allowFrom.json` | `TELEGRAM_ALLOWED_USERS` | 从 `allowFrom[]` 数组用逗号连接 |
| Discord | `channels.discord.token` | `DISCORD_BOT_TOKEN` | |
| Discord | `channels.discord.allowFrom` | `DISCORD_ALLOWED_USERS` | |
| Slack | `channels.slack.botToken` | `SLACK_BOT_TOKEN` | |
| Slack | `channels.slack.appToken` | `SLACK_APP_TOKEN` | |
| Slack | `channels.slack.allowFrom` | `SLACK_ALLOWED_USERS` | |
| WhatsApp | `channels.whatsapp.allowFrom` | `WHATSAPP_ALLOWED_USERS` | 通过 Baileys QR 配对进行身份验证（不是令牌） |
| Signal | `channels.signal.account` | `SIGNAL_ACCOUNT` | |
| Signal | `channels.signal.httpUrl` | `SIGNAL_HTTP_URL` | |
| Signal | `channels.signal.allowFrom` | `SIGNAL_ALLOWED_USERS` | |
| Matrix | `channels.matrix.botToken` | `MATRIX_ACCESS_TOKEN` | 通过 deep-channels 迁移 |
| Mattermost | `channels.mattermost.botToken` | `MATTERMOST_BOT_TOKEN` | 通过 deep-channels 迁移 |

### 其他配置

| 内容 | OpenClaw 路径 | KClaw 路径 | 备注 |
|------|-------------|-------------|-------|
| 批准模式 | `approvals.exec.mode` | `config.yaml` → `approvals.mode` | "auto"→"off"、"always"→"manual"、"smart"→"smart" |
| 命令允许列表 | `exec-approvals.json` | `config.yaml` → `command_allowlist` | 模式合并和去重 |
| 浏览器 CDP URL | `browser.cdpUrl` | `config.yaml` → `browser.cdp_url` | |
| 浏览器无头 | `browser.headless` | `config.yaml` → `browser.headless` | |
| Brave 搜索密钥 | `tools.web.search.brave.apiKey` | `.env` → `BRAVE_API_KEY` | 需要 `--migrate-secrets` |
| 网关 auth 令牌 | `gateway.auth.token` | `.env` → `KCLAW_GATEWAY_TOKEN` | 需要 `--migrate-secrets` |
| 工作目录 | `agents.defaults.workspace` | `.env` → `MESSAGING_CWD` | |

### 已归档（无直接 KClaw 等效项）

这些保存到 `~/.kclaw/migration/openclaw/<timestamp>/archive/` 供手动审查：

| 内容 | 归档文件 | 如何在 KClaw 中重新创建 |
|------|-------------|--------------------------|
| `IDENTITY.md` | `archive/workspace/IDENTITY.md` | 合并到 `SOUL.md` |
| `TOOLS.md` | `archive/workspace/TOOLS.md` | KClaw 有内置工具指令 |
| `HEARTBEAT.md` | `archive/workspace/HEARTBEAT.md` | 使用 cron 作业进行周期性任务 |
| `BOOTSTRAP.md` | `archive/workspace/BOOTSTRAP.md` | 使用上下文文件或技能 |
| Cron 作业 | `archive/cron-config.json` | 使用 `kclaw cron create` 重新创建 |
| 插件 | `archive/plugins-config.json` | 请参阅 [插件指南](/docs/user-guide/features/hooks) |
| 钩子/Webhooks | `archive/hooks-config.json` | 使用 `kclaw webhook` 或网关钩子 |
| 记忆后端 | `archive/memory-backend-config.json` | 通过 `kclaw honcho` 配置 |
| 技能注册表 | `archive/skills-registry-config.json` | 使用 `kclaw skills config` |
| UI/身份 | `archive/ui-identity-config.json` | 使用 `/skin` 命令 |
| 日志记录 | `archive/logging-diagnostics-config.json` | 在 config.yaml 日志部分设置 |
| 多代理列表 | `archive/agents-list.json` | 使用 KClaw profiles |
| 频道绑定 | `archive/bindings.json` | 每个平台手动设置 |
| 复杂频道 | `archive/channels-deep-config.json` | 手动平台配置 |

## API 密钥解析

当启用 `--migrate-secrets` 时，API 密钥按优先级从**三个来源**收集：

1. **配置值** — `openclaw.json` 中的 `models.providers.*.apiKey` 和 TTS 提供商密钥
2. **环境文件** — `~/.openclaw/.env`（密钥如 `OPENROUTER_API_KEY`、`ANTHROPIC_API_KEY` 等）
3. **Auth 配置文件** — `~/.openclaw/agents/main/agent/auth-profiles.json`（每个代理的凭证）

配置值优先。`.env` 填补任何空白。Auth 配置文件填补剩余部分。

### 支持的密钥目标

`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`、`GEMINI_API_KEY`、`ZAI_API_KEY`、`MINIMAX_API_KEY`、`ELEVENLABS_API_KEY`、`TELEGRAM_BOT_TOKEN`、`VOICE_TOOLS_OPENAI_KEY`

此白名单中没有的密钥永远不会被复制。

## SecretRef 处理

令牌和 API 密钥的 OpenClaw 配置值可以有三种格式：

```json
// 纯字符串
"channels": { "telegram": { "botToken": "123456:ABC-DEF..." } }

// 环境模板
"channels": { "telegram": { "botToken": "${TELEGRAM_BOT_TOKEN}" } }

// SecretRef 对象
"channels": { "telegram": { "botToken": { "source": "env", "id": "TELEGRAM_BOT_TOKEN" } } }
```

迁移解析所有三种格式。对于 `source: "env"` 的环境模板和 SecretRef 对象，它在 `~/.openclaw/.env` 中查找值。对于 `source: "file"` 或 `source: "exec"` 的 SecretRef 对象无法自动解析——这些值必须在迁移后手动添加到 KClaw。

## 迁移后

1. **检查迁移报告** — 完成后打印，包含已迁移、已跳过和冲突项目的计数。

2. **审查归档文件** — `~/.kclaw/migration/openclaw/<timestamp>/archive/` 中的任何内容都需要手动处理。

3. **验证 API 密钥** — 运行 `kclaw status` 检查提供商身份验证。

4. **测试消息** — 如果您迁移了平台令牌，重启网关：`systemctl --user restart kclaw-gateway`

5. **检查会话策略** — 验证 `kclaw config get session_reset` 符合您的预期。

6. **重新配对 WhatsApp** — WhatsApp 使用 QR 码配对（Baileys），不是令牌迁移。运行 `kclaw whatsapp` 配对。

## 故障排除

### "找不到 OpenClaw 目录"

迁移检查 `~/.openclaw/`，然后是 `~/.clawdbot/`，然后是 `~/.moldbot/`。如果您的安装在其他地方，请使用 `--source /path/to/your/openclaw`。

### "找不到提供商 API 密钥"

密钥可能在您的 `.env` 文件中而不是 `openclaw.json` 中。迁移检查两者——确保 `~/.openclaw/.env` 存在且包含密钥。如果密钥使用 `source: "file"` 或 `source: "exec"` SecretRefs，则无法自动解析。

### 迁移后技能未显示

导入的技能位于 `~/.kclaw/skills/openclaw-imports/`。启动新会话以使其生效，或运行 `/skills` 验证它们已加载。

### TTS 语音未迁移

OpenClaw 在两个地方存储 TTS 设置：`messages.tts.providers.*` 和顶级 `talk` 配置。迁移检查两者。如果您的语音 ID 是通过 OpenClaw UI 设置的（存储在不同路径），您可能需要手动设置：`kclaw config set tts.elevenlabs.voice_id YOUR_VOICE_ID`。
