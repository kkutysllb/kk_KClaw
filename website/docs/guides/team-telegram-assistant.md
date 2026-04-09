---
sidebar_position: 4
title: "教程：团队 Telegram 助手"
description: "设置供整个团队使用的 Telegram 机器人的分步指南——用于代码帮助、研究、系统管理等"
---

# 设置团队 Telegram 助手

本教程带您完成设置由 KClaw Agent 驱动的 Telegram 机器人，多个团队成员可以使用。到最后，您的团队将拥有一个共享的 AI 助手，他们可以发送消息寻求代码帮助、研究、系统管理等帮助——通过每用户授权保护。

## 我们正在构建什么

一个 Telegram 机器人，具有以下功能：

- **任何授权团队成员**都可以 DM 寻求帮助——代码审查、研究、shell 命令、调试
- **在您的服务器上运行**——具有完整工具访问权限：终端、文件编辑、网络搜索、代码执行
- **每用户会话**——每个人获得自己的对话上下文
- **默认安全**——只有批准的用户可以交互，有两种授权方法
- **计划任务**——每日站会、健康检查和提醒传递到团队频道

---

## 先决条件

在开始之前，请确保您拥有：

- **在服务器或 VPS 上安装 KClaw Agent**（不是您的笔记本电脑——机器人需要保持运行）。如果尚未安装，请遵循[安装指南](/docs/getting-started/installation)。
- **您自己的 Telegram 账户**（机器人所有者）
- **配置了 LLM 提供商**——至少需要在 `~/.kclaw/.env` 中设置 OpenAI、Anthropic 或其他受支持提供商的 API 密钥

:::tip
每月 $5 的 VPS 足以运行网关。KClaw 本身是轻量级的——LLM API 调用才是花钱的地方，那些是远程发生的。
:::

---

## 步骤 1：创建 Telegram 机器人

每个 Telegram 机器人从 **@BotFather** 开始——Telegram 官方用于创建机器人的工具。

1. **打开 Telegram** 并搜索 `@BotFather`，或前往 [t.me/BotFather](https://t.me/BotFather)

2. **发送 `/newbot`** — BotFather 会问您两个问题：
   - **显示名称** — 用户看到的（例如 `Team KClaw Assistant`）
   - **用户名** — 必须以 `bot` 结尾（例如 `myteam_kclaw_bot`）

3. **复制机器人令牌** — BotFather 回复类似：
   ```
   Use this token to access the HTTP API:
   7123456789:AAH1bGciOiJSUzI1NiIsInR5cCI6Ikp...
   ```
   保存此令牌——您下一步需要它。

4. **设置描述**（可选但推荐）：
   ```
   /setdescription
   ```
   选择您的机器人，然后输入类似：
   ```
   Team AI assistant powered by KClaw Agent. DM me for help with code, research, debugging, and more.
   ```

5. **设置机器人命令**（可选——为用户提供命令菜单）：
   ```
   /setcommands
   ```
   选择您的机器人，然后粘贴：
   ```
   new - Start a fresh conversation
   model - Show or change the AI model
   status - Show session info
   help - Show available commands
   stop - Stop the current task
   ```

:::warning
保持您的机器人令牌秘密。任何拥有令牌的人都可以控制机器人。如果泄露，使用 `/revoke` 在 BotFather 中生成新的。
:::

---

## 步骤 2：配置网关

您有两个选项：交互式设置向导（推荐）或手动配置。

### 选项 A：交互式设置（推荐）

```bash
kclaw gateway setup
```

这将带您使用方向键选择完成一切。选择 **Telegram**，粘贴您的机器人令牌，并在提示时输入您的用户 ID。

### 选项 B：手动配置

将这些行添加到 `~/.kclaw/.env`：

```bash
# Telegram 机器人令牌（来自 BotFather）
TELEGRAM_BOT_TOKEN=7123456789:AAH1bGciOiJSUzI1NiIsInR5cCI6Ikp...

# 您的 Telegram 用户 ID（数字）
TELEGRAM_ALLOWED_USERS=123456789
```

### 查找您的用户 ID

您的 Telegram 用户 ID 是一个数字值（不是您的用户名）。要找到它：

1. 在 Telegram 上消息 [@userinfobot](https://t.me/userinfobot)
2. 它会立即回复您的数字用户 ID
3. 将该数字复制到 `TELEGRAM_ALLOWED_USERS`

:::info
Telegram 用户 ID 是像 `123456789` 这样的永久数字。它们与您的 `@username` 不同，后者可以更改。始终使用数字 ID 进行允许列表。
:::

---

## 步骤 3：启动网关

### 快速测试

首先在前台运行网关以确保一切正常：

```bash
kclaw gateway
```

您应该看到类似以下的输出：

```
[Gateway] Starting KClaw Gateway...
[Gateway] Telegram adapter connected
[Gateway] Cron scheduler started (tick every 60s)
```

打开 Telegram，找到您的机器人并发送消息。如果它回复，您就成功了。按 `Ctrl+C` 停止。

### 生产环境：安装为服务

对于在重启后持续存在的持久部署：

```bash
kclaw gateway install
sudo kclaw gateway install --system   # 仅 Linux：启动时系统服务
```

这会创建一个后台服务：在 Linux 上默认为用户级 **systemd** 服务，在 macOS 上为 **launchd** 服务，或者如果您传递 `--system` 则为启动时 Linux 系统服务。

```bash
# Linux — 管理默认用户服务
kclaw gateway start
kclaw gateway stop
kclaw gateway status

# 查看实时日志
journalctl --user -u kclaw-gateway -f

# SSH 登出后保持运行
sudo loginctl enable-linger $USER

# Linux 服务器 — 明确的系统服务命令
sudo kclaw gateway start --system
sudo kclaw gateway status --system
journalctl -u kclaw-gateway -f
```

```bash
# macOS — 管理服务
kclaw gateway start
kclaw gateway stop
tail -f ~/.kclaw/logs/gateway.log
```

:::tip macOS PATH
launchd plist 在安装时捕获您的 shell PATH，以便网关子进程可以找到 Node.js 和 ffmpeg 等工具。如果以后安装新工具，请重新运行 `kclaw gateway install` 以更新 plist。
:::

### 验证它正在运行

```bash
kclaw gateway status
```

然后在 Telegram 上向您的机器人发送测试消息。您应该在几秒钟内收到回复。

---

## 步骤 4：设置团队访问

现在让我们给您的队友访问权限。有两种方法。

### 方法 A：静态允许列表

收集每个团队成员的 Telegram 用户 ID（让他们消息 [@userinfobot](https://t.me/userinfobot)）并以逗号分隔列表添加：

```bash
# 在 ~/.kclaw/.env 中
TELEGRAM_ALLOWED_USERS=123456789,987654321,555555555
```

更改后重启网关：

```bash
kclaw gateway stop && kclaw gateway start
```

### 方法 B：DM 配对（推荐用于团队）

DM 配对更灵活——您不需要预先收集用户 ID。以下是它的工作方式：

1. **队友 DM 机器人** — 由于他们不在允许列表中，机器人回复一次性配对码：
   ```
   🔐 Pairing code: XKGH5N7P
   Send this code to the bot owner for approval.
   ```

2. **队友向您发送代码**（通过任何渠道——Slack、email、当面）

3. **您在服务器上批准**：
   ```bash
   kclaw pairing approve telegram XKGH5N7P
   ```

4. **他们进来了** — 机器人立即开始回复他们的消息

**管理配对用户：**

```bash
# 查看所有待处理和已批准的用户
kclaw pairing list

# 撤销某人的访问权限
kclaw pairing revoke telegram 987654321

# 清除过期的待处理代码
kclaw pairing clear-pending
```

:::tip
DM 配对是团队的理想选择，因为您在添加新用户时不需要重启网关。批准立即生效。
:::

### 安全注意事项

- **永远不要在具有终端访问的机器人上设置 `GATEWAY_ALLOW_ALL_USERS=true`** — 任何找到您的机器人的人都可能在您的服务器上运行命令
- 配对码在 **1 小时**后过期，使用加密随机性
- 速率限制防止暴力攻击：每个用户每 10 分钟 1 次请求，每个平台最多 3 个待处理代码
- 5 次失败的批准尝试后，平台进入 1 小时锁定
- 所有配对数据以 `chmod 0600` 权限存储

---

## 步骤 5：配置机器人

### 设置主频道

**主频道**是机器人传递 cron 作业结果和主动消息的地方。没有主频道，计划任务就没有地方发送输出。

**选项 1：** 在机器人所在的任何 Telegram 群组或聊天中使用 `/sethome` 命令。

**选项 2：** 在 `~/.kclaw/.env` 中手动设置：

```bash
TELEGRAM_HOME_CHANNEL=-1001234567890
TELEGRAM_HOME_CHANNEL_NAME="Team Updates"
```

要查找频道 ID，请将 [@userinfobot](https://t.me/userinfobot) 添加到群组——它将报告群组的聊天 ID。

### 配置工具进度显示

控制在使用工具时机器人显示多少细节。在 `~/.kclaw/config.yaml` 中：

```yaml
display:
  tool_progress: new    # off | new | all | verbose
```

| 模式 | 您看到的内容 |
|------|-------------|
| `off` | 仅干净响应——无工具活动 |
| `new` | 每个新工具调用的简要状态（推荐用于消息传递） |
| `all` | 每个工具调用及其详情 |
| `verbose` | 完整工具输出，包括命令结果 |

用户也可以在聊天中使用 `/verbose` 命令更改此每会话设置。

### 使用 SOUL.md 设置人格

通过编辑 `~/.kclaw/SOUL.md` 自定义机器人的沟通方式：

有关完整指南，请参见[在 KClaw 中使用 SOUL.md](/docs/guides/use-soul-with-kclaw)。

```markdown
# Soul
You are a helpful team assistant. Be concise and technical.
Use code blocks for any code. Skip pleasantries — the team
values directness. When debugging, always ask for error logs
before guessing at solutions.
```

### 添加项目上下文

如果您的团队从事特定项目，请创建上下文文件以便机器人了解您的技术栈：

```markdown
<!-- ~/.kclaw/AGENTS.md -->
# Team Context
- We use Python 3.12 with FastAPI and SQLAlchemy
- Frontend is React with TypeScript
- CI/CD runs on GitHub Actions
- Production deploys to AWS ECS
- Always suggest writing tests for new code
```

:::info
上下文文件被注入每个会话的系统提示中。保持它们简洁——每个字符都计入您的令牌预算。
:::

---

## 步骤 6：设置计划任务

网关运行后，您可以安排将结果传递到团队频道的重复任务。

### 每日站会摘要

在 Telegram 上向机器人发送消息：

```
Every weekday at 9am, check the GitHub repository at
github.com/myorg/myproject for:
1. Pull requests opened/merged in the last 24 hours
2. Issues created or closed
3. Any CI/CD failures on the main branch
Format as a brief standup-style summary.
```

代理自动创建 cron 作业并将结果传递到您询问的聊天（或主频道）。

### 服务器健康检查

```
Every 6 hours, check disk usage with 'df -h', memory with 'free -h',
and Docker container status with 'docker ps'. Report anything unusual —
partitions above 80%, containers that have restarted, or high memory usage.
```

### 管理计划任务

```bash
# 从 CLI
kclaw cron list          # 查看所有计划作业
kclaw cron status        # 检查调度器是否正在运行

# 从 Telegram 聊天
/cron list                # 查看作业
/cron remove <job_id>     # 删除作业
```

:::warning
Cron 作业提示在完全新的会话中运行，没有之前对话的记忆。确保每个提示包含代理所需的**所有**上下文——文件路径、URL、服务器地址和清晰指令。
:::

---

## 生产提示

### 使用 Docker 以确保安全

在共享团队机器人上，使用 Docker 作为终端后端，以便代理命令在容器中运行而不是在您的主机上：

```bash
# 在 ~/.kclaw/.env 中
TERMINAL_BACKEND=docker
TERMINAL_DOCKER_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20
```

或在 `~/.kclaw/config.yaml` 中：

```yaml
terminal:
  backend: docker
  container_cpu: 1
  container_memory: 5120
  container_persistent: true
```

这样，即使有人要求机器人运行破坏性内容，您的主机系统也受到保护。

### 监控网关

```bash
# 检查网关是否正在运行
kclaw gateway status

# 查看实时日志（Linux）
journalctl --user -u kclaw-gateway -f

# 查看实时日志（macOS）
tail -f ~/.kclaw/logs/gateway.log
```

### 保持 KClaw 更新

从 Telegram，向机器人发送 `/update` — 它将拉取最新版本并重启。或者从服务器：

```bash
kclaw update
kclaw gateway stop && kclaw gateway start
```

### 日志位置

| 内容 | 位置 |
|------|----------|
| 网关日志 | `journalctl --user -u kclaw-gateway`（Linux）或 `~/.kclaw/logs/gateway.log`（macOS） |
| Cron 作业输出 | `~/.kclaw/cron/output/{job_id}/{timestamp}.md` |
| Cron 作业定义 | `~/.kclaw/cron/jobs.json` |
| 配对数据 | `~/.kclaw/pairing/` |
| 会话历史 | `~/.kclaw/sessions/` |

---

## 进一步探索

您现在有一个可用的团队 Telegram 助手。以下是接下来可以探索的步骤：

- **[安全指南](/docs/user-guide/security)** — 深入了解授权、容器隔离和命令批准
- **[消息网关](/docs/user-guide/messaging)** — 网关架构、会话管理和聊天命令的完整参考
- **[Telegram 设置](/docs/user-guide/messaging/telegram)** — 特定平台详情，包括语音消息和 TTS
- **[计划任务](/docs/user-guide/features/cron)** — 具有交付选项和 cron 表达式的高级 cron 调度
- **[上下文文件](/docs/user-guide/features/context-files)** — 用于项目知识的 AGENTS.md、SOUL.md 和 .cursorrules
- **[人格](/docs/user-guide/features/personality)** — 内置人格预设和自定义 persona 定义
- **添加更多平台** — 同一网关可以同时运行 [Discord](/docs/user-guide/messaging/discord)、[Slack](/docs/user-guide/messaging/slack) 和 [WhatsApp](/docs/user-guide/messaging/whatsapp)

---

*有问题或问题？在 GitHub 上打开 issue——欢迎贡献。*
