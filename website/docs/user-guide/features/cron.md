---
sidebar_position: 5
title: "计划任务（Cron）"
description: "使用自然语言安排自动化任务，使用一个 cron 工具管理它们，并附加一个或多个技能"
---

# 计划任务（Cron）

使用自然语言或 cron 表达式安排任务自动运行。KClaw 通过单一的 `cronjob` 工具暴露 cron 管理，使用操作式而不是单独的 schedule/list/remove 工具。

## cron 现在能做什么

Cron 作业可以：

- 安排一次性或循环任务
- 暂停、恢复、编辑、触发和删除作业
- 附加零个、一个或多个技能到作业
- 将结果返回到原始聊天、本地文件或已配置的平台目标
- 在具有正常静态工具列表的新代理会话中运行

:::warning
Cron 运行的会话不能递归创建更多 cron 作业。KClaw 在 cron 执行内部禁用 cron 管理工具，以防止失控的调度循环。
:::

## 创建计划任务

### 在聊天中使用 `/cron`

```bash
/cron add 30m "提醒我检查构建"
/cron add "every 2h" "检查服务器状态"
/cron add "every 1h" "总结新 feed 项目" --skill blogwatcher
/cron add "every 1h" "使用两个技能并组合结果" --skill blogwatcher --skill find-nearby
```

### 从独立 CLI

```bash
kclaw cron create "every 2h" "检查服务器状态"
kclaw cron create "every 1h" "总结新 feed 项目" --skill blogwatcher
kclaw cron create "every 1h" "使用两个技能并组合结果" \
  --skill blogwatcher \
  --skill find-nearby \
  --name "技能组合"
```

### 通过自然对话

正常询问 KClaw：

```text
每天早上 9 点，检查 Hacker News 上的 AI 新闻并在 Telegram 上给我发送摘要。
```

KClaw 将在内部使用统一的 `cronjob` 工具。

## 技能支持的 cron 作业

Cron 作业可以在运行提示之前加载一个或多个技能。

### 单个技能

```python
cronjob(
    action="create",
    skill="blogwatcher",
    prompt="检查配置的 feed 并总结任何新内容。",
    schedule="0 9 * * *",
    name="晨间 feed",
)
```

### 多个技能

技能按顺序加载。提示成为叠加在这些技能之上的任务指令。

```python
cronjob(
    action="create",
    skills=["blogwatcher", "find-nearby"],
    prompt="查找新的本地活动和相关附近地点，然后将它们组合成一份简短的简报。",
    schedule="every 6h",
    name="本地简报",
)
```

当您希望计划代理继承可重用工作流而不必将完整技能文本塞入 cron 提示本身时，这很有用。

## 编辑作业

您不需要仅仅为了更改而删除和重新创建作业。

### 聊天

```bash
/cron edit <job_id> --schedule "every 4h"
/cron edit <job_id> --prompt "使用修订后的任务"
/cron edit <job_id> --skill blogwatcher --skill find-nearby
/cron edit <job_id> --remove-skill blogwatcher
/cron edit <job_id> --clear-skills
```

### 独立 CLI

```bash
kclaw cron edit <job_id> --schedule "every 4h"
kclaw cron edit <job_id> --prompt "使用修订后的任务"
kclaw cron edit <job_id> --skill blogwatcher --skill find-nearby
kclaw cron edit <job_id> --add-skill find-nearby
kclaw cron edit <job_id> --remove-skill blogwatcher
kclaw cron edit <job_id> --clear-skills
```

备注：

- 重复的 `--skill` 替换作业附加的技能列表
- `--add-skill` 追加到现有列表而不替换它
- `--remove-skill` 移除特定的附加技能
- `--clear-skills` 移除所有附加技能

## 生命周期操作

Cron 作业现在有比创建/删除更完整的生命周期。

### 聊天

```bash
/cron list
/cron pause <job_id>
/cron resume <job_id>
/cron run <job_id>
/cron remove <job_id>
```

### 独立 CLI

```bash
kclaw cron list
kclaw cron pause <job_id>
kclaw cron resume <job_id>
kclaw cron run <job_id>
kclaw cron remove <job_id>
kclaw cron status
kclaw cron tick
```

它们的作用：

- `pause` — 保留作业但停止调度它
- `resume` — 重新启用作业并计算下次运行时间
- `run` — 在下一个调度器 tick 触发作业
- `remove` — 完全删除它

## 工作原理

**Cron 执行由网关守护程序处理。** 网关每 60 秒 tick 一次调度器，在隔离的代理会话中运行任何到期的作业。

```bash
kclaw gateway install     # 安装为用户服务
sudo kclaw gateway install --system   # Linux: 启动时系统服务（用于服务器）
kclaw gateway             # 或在前台运行

kclaw cron list
kclaw cron status
```

### 网关调度器行为

在每个 tick，KClaw：

1. 从 `~/.kclaw/cron/jobs.json` 加载作业
2. 检查 `next_run_at` 与当前时间
3. 为每个到期的作业启动一个新的 `AIAgent` 会话
4. 可选地将一个或多个附加技能注入那个新会话
5. 运行提示直到完成
6. 交付最终响应
7. 更新运行元数据和下次计划时间

`~/.kclaw/cron/.tick.lock` 处的文件锁防止重叠的调度器 tick 双运行同一批作业。

## 交付选项

安排作业时，您可以指定输出去向：

| 选项 | 描述 | 示例 |
|--------|-------------|---------|
| `"origin"` | 返回到创建作业的地方 | 消息平台上的默认值 |
| `"local"` | 仅保存到本地文件（`~/.kclaw/cron/output/`） | CLI 上的默认值 |
| `"telegram"` | Telegram 主页频道 | 使用 `TELEGRAM_HOME_CHANNEL` |
| `"telegram:123456"` | 特定 Telegram 聊天（按 ID） | 直接交付 |
| `"telegram:-100123:17585"` | 特定 Telegram 主题 | `chat_id:thread_id` 格式 |
| `"discord"` | Discord 主页频道 | 使用 `DISCORD_HOME_CHANNEL` |
| `"discord:#engineering"` | 特定 Discord 频道 | 按频道名称 |
| `"slack"` | Slack 主页频道 | |
| `"whatsapp"` | WhatsApp 主页 | |
| `"signal"` | Signal | |
| `"matrix"` | Matrix 主页房间 | |
| `"mattermost"` | Mattermost 主页频道 | |
| `"email"` | 电子邮件 | |
| `"sms"` | 通过 Twilio 的 SMS | |
| `"homeassistant"` | Home Assistant | |
| `"dingtalk"` | DingTalk | |
| `"feishu"` | Feishu/Lark | |
| `"wecom"` | WeCom | |
| `"bluebubbles"` | BlueBubbles (iMessage) | |

代理的最终响应会自动交付。您不需要在 cron 提示中调用 `send_message`。

### 响应包装

默认情况下，交付的 cron 输出被包装有标题和页脚，以便接收者知道它来自计划任务：

```
Cronjob Response: Morning feeds
-------------

<agent output here>

Note: The agent cannot see this message, and therefore cannot respond to it.
```

要交付没有包装器的原始代理输出，请将 `cron.wrap_response` 设置为 `false`：

```yaml
# ~/.kclaw/config.yaml
cron:
  wrap_response: false
```

### 静默抑制

如果代理的最终响应以 `[SILENT]` 开头，交付被完全抑制。输出仍然保存到本地用于审计（在 `~/.kclaw/cron/output/` 中），但不会发送到交付目标。

这对于只应在出错时报告的监控作业很有用：

```text
检查 nginx 是否正在运行。如果一切健康，只响应 [SILENT]。
否则，报告问题。
```

失败的作业总是会交付，不管 `[SILENT]` 标记如何——只有成功的运行可以被静默。

## 计划格式

代理的最终响应自动交付——您**不需要**在 cron 提示中包含 `send_message` 以获得相同的目标。如果 cron 运行调用 `send_message` 到调度器已经交付的确切目标，KClaw 会跳过重复发送并告诉模型将面向用户的内容放在最终响应中。仅对额外或不同的目标使用 `send_message`。

### 相对延迟（一次性）

```
30m     → 30 分钟后运行一次
2h      → 2 小时后运行一次
1d      → 1 天后运行一次
```

### 间隔（循环）

```
every 30m    → 每 30 分钟
every 2h     → 每 2 小时
every 1d     → 每天
```

### Cron 表达式

```
0 9 * * *       → 每天上午 9:00
0 9 * * 1-5     → 工作日上午 9:00
0 */6 * * *     → 每 6 小时
30 8 1 * *      → 每月 1 日上午 8:30
0 0 * * 0       → 每周日午夜
```

### ISO 时间戳

```
2026-03-15T09:00:00    → 2026 年 3 月 15 日上午 9:00 一次性
```

## 重复行为

| 计划类型 | 默认重复 | 行为 |
|--------------|----------------|----------|
| 一次性（`30m`，时间戳） | 1 | 运行一次 |
| 间隔（`every 2h`） | 永远 | 运行直到被删除 |
| Cron 表达式 | 永远 | 运行直到被删除 |

您可以覆盖它：

```python
cronjob(
    action="create",
    prompt="...",
    schedule="every 2h",
    repeat=5,
)
```

## 以编程方式管理作业

代理面向的 API 是一个工具：

```python
cronjob(action="create", ...)
cronjob(action="list")
cronjob(action="update", job_id="...")
cronjob(action="pause", job_id="...")
cronjob(action="resume", job_id="...")
cronjob(action="run", job_id="...")
cronjob(action="remove", job_id="...")
```

对于 `update`，传递 `skills=[]` 以移除所有附加技能。

## 作业存储

作业存储在 `~/.kclaw/cron/jobs.json` 中。作业运行的输出保存到 `~/.kclaw/cron/output/{job_id}/{timestamp}.md`。

存储使用原子文件写入，因此中断的写入不会留下部分写入的作业文件。

## 自包含提示仍然重要

:::warning 重要
Cron 作业在完全新的代理会话中运行。提示必须包含代理需要的、不已被附加技能提供的所有内容。
:::

**错误：** `"检查那个服务器问题"`

**正确：** `"SSH 登录到服务器 192.168.1.100（用户 'deploy'），检查 nginx 是否使用 'systemctl status nginx' 运行，并验证 https://example.com 返回 HTTP 200。"`

## 安全

计划任务提示在创建和更新时会被扫描提示注入和凭证泄露模式。包含不可见 Unicode 技巧、SSH 后门尝试或明显密钥泄露负载的提示会被阻止。
