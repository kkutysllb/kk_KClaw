---
name: agentmail
description: 通过 AgentMail 赋予代理自己的专用电子邮件收件箱。使用代理拥有的电子邮件地址（例如 kclaw@agentmail.to）自主发送、接收和管理电子邮件。
version: 1.0.0
metadata:
  kclaw:
    tags: [电子邮件, 通信, agentmail, mcp]
    category: email
---

# AgentMail — 代理自有电子邮件收件箱

## 需求

- **AgentMail API 密钥**（必需）— 在 https://console.agentmail.to 注册（免费层：3 个收件箱，3,000 封邮件/月；付费计划 $20/月起）
- Node.js 18+（用于 MCP 服务器）

## 何时使用
当您需要以下情况时使用此技能：
- 赋予代理自己的专用电子邮件地址
- 代表代理自主发送电子邮件
- 接收和阅读收到的电子邮件
- 管理电子邮件线程和对话
- 通过电子邮件注册服务或进行身份验证
- 通过电子邮件与其他代理或人类通信

这**不是**用于阅读用户的个人电子邮件（为此使用 himalaya 或 Gmail）。AgentMail 赋予代理自己的身份和收件箱。

## 设置

### 1. 获取 API 密钥
- 前往 https://console.agentmail.to
- 创建一个账户并生成 API 密钥（以 `am_` 开头）

### 2. 配置 MCP 服务器
添加到 `~/.kclaw/config.yaml`（粘贴您的实际密钥 — MCP 环境变量不会从 .env 展开）：
```yaml
mcp_servers:
  agentmail:
    command: "npx"
    args: ["-y", "agentmail-mcp"]
    env:
      AGENTMAIL_API_KEY: "am_your_key_here"
```

### 3. 重启 KClaw
```bash
kclaw
```
现在 11 个 AgentMail 工具全部自动可用。

## 可用工具（通过 MCP）

| 工具 | 描述 |
|------|-------------|
| `list_inboxes` | 列出所有代理收件箱 |
| `get_inbox` | 获取特定收件箱的详情 |
| `create_inbox` | 创建新收件箱（获得真实电子邮件地址） |
| `delete_inbox` | 删除收件箱 |
| `list_threads` | 列出收件箱中的电子邮件线程 |
| `get_thread` | 获取特定电子邮件线程 |
| `send_message` | 发送新电子邮件 |
| `reply_to_message` | 回复现有电子邮件 |
| `forward_message` | 转发电子邮件 |
| `update_message` | 更新消息标签/状态 |
| `get_attachment` | 下载电子邮件附件 |

## 程序

### 创建收件箱并发送电子邮件
1. 创建专用收件箱：
   - 使用用户名（例如 `kclaw`）调用 `create_inbox`
   - 代理获得地址：`kclaw@agentmail.to`
2. 发送电子邮件：
   - 使用 `inbox_id`、`to`、`subject`、`text` 调用 `send_message`
3. 检查回复：
   - 使用 `list_threads` 查看收到的对话
   - 使用 `get_thread` 阅读特定线程

### 检查收到的电子邮件
1. 使用 `list_inboxes` 找到您的收件箱 ID
2. 使用收件箱 ID 使用 `list_threads` 查看对话
3. 使用 `get_thread` 阅读线程及其消息

### 回复电子邮件
1. 使用 `get_thread` 获取线程
2. 使用消息 ID 和回复文本调用 `reply_to_message`

## 示例工作流

**注册服务：**
```
1. create_inbox (username: "signup-bot")
2. 使用收件箱地址在服务上注册
3. list_threads 检查验证电子邮件
4. get_thread 阅读验证码
```

**代理到人类外联：**
```
1. create_inbox (username: "kclaw-outreach")
2. send_message (to: user@example.com, subject: "Hello", text: "...")
3. list_threads 检查回复
```

## 陷阱
- 免费层限制为 3 个收件箱和 3,000 封邮件/月
- 免费层上电子邮件来自 `@agentmail.to` 域名（付费计划上为自定义域名）
- MCP 服务器需要 Node.js (18+)（`npx -y agentmail-mcp`）
- 必须安装 `mcp` Python 包：`pip install mcp`
- 实时入站电子邮件（webhooks）需要公共服务器 — 对于个人使用，通过 cronjob 使用 `list_threads` 轮询代替

## 验证
设置后，用以下方式测试：
```
kclaw --toolsets mcp -q "Create an AgentMail inbox called test-agent and tell me its email address"
```
您应该看到返回的新收件箱地址。

## 参考
- AgentMail 文档：https://docs.agentmail.to/
- AgentMail 控制台：https://console.agentmail.to
- AgentMail MCP 仓库：https://github.com/agentmail-to/agentmail-mcp
- 定价：https://www.agentmail.to/pricing
