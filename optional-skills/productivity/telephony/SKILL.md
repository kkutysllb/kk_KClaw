---
name: telephony
description: 赋予 KClaw 电话能力而无需核心工具更改。配置和持久化 Twilio 号码、发送和接收 SMS/MMS、直接通话，以及通过 Bland.ai 或 Vapi 放置 AI 驱动的外呼电话。
version: 1.0.0
author: kkutysllb
license: MIT
metadata:
  kclaw:
    tags: [电话, SMS, MMS, 语音, twilio, bland.ai, vapi, 通话, 短信]
    related_skills: [find-nearby, google-workspace, agentmail]
    category: productivity
---

# 电话 — 号码、通话和短信（无需核心工具更改）

这个可选技能赋予 KClaw 实用的电话能力，同时将电话功能保持在核心工具列表之外。

它附带辅助脚本 `scripts/telephony.py`，可以：
- 将提供商凭据保存到 `~/.kclaw/.env`
- 搜索和购买 Twilio 电话号码
- 记住该号码以供后续会话使用
- 从拥有的号码发送 SMS / MMS
- 无需 webhook 服务器即可轮询该号码的入站 SMS
- 使用 TwiML `<Say>` 或 `<Play>` 进行直接 Twilio 通话
- 将拥有的 Twilio 号码导入 Vapi
- 通过 Bland.ai 或 Vapi 放置外呼 AI 电话

## 这个解决什么问题

此技能旨在涵盖用户实际想要的实际电话任务：
- 外呼电话
- 短信
- 拥有可重用的代理号码
- 检查稍后到达该号码的消息
- 在会话之间保留该号码和相关 ID
- 为入站 SMS 轮询和其他自动化保留未来友好的电话身份

它**不会**将 KClaw 变成实时入站电话网关。入站 SMS 通过轮询 Twilio REST API 处理。这对于许多工作流（包括通知和一些一次性代码检索）已经足够，无需添加核心 webhook 基础设施。

## 安全规则 — 强制性的

1. 放置通话或发送短信前务必确认。
2. 永远不要拨打紧急号码。
3. 永远不要将电话用于骚扰、垃圾邮件、冒充或任何非法活动。
4. 将第三方电话号码视为敏感操作数据：
   - 不要将它们保存到 KClaw 记忆
   - 不要将它们包含在技能文档、摘要或后续笔记中，除非用户明确要求
5. 可以持久化**代理拥有的 Twilio 号码**，因为那是用户配置的一部分。
6. VoIP 号码**不能保证**适用于所有第三方 2FA 流程。谨慎使用并明确设置用户期望。

## 决策树 — 使用哪个服务？

使用此逻辑而非硬编码提供商路由：

### 1) "我希望 KClaw 拥有一个真实电话号码"
使用 **Twilio**。

原因：
- 购买和保留号码的最简单途径
- 最佳 SMS / MMS 支持
- 最简单的入站 SMS 轮询方案
- 最干净的未来入站 webhooks 或通话处理路径

用例：
- 稍后接收短信
- 发送部署警报 / cron 通知
- 为代理维护可重用的电话身份
- 稍后试验基于电话的身份验证流程

### 2) "我现在只需要最简单的外呼 AI 电话"
使用 **Bland.ai**。

原因：
- 最快设置
- 一个 API 密钥
- 无需先自己购买/导入号码

权衡：
- 不太灵活
- 语音质量不错，但不是最好的

### 3) "我想要最佳对话 AI 语音质量"
使用 **Twilio + Vapi**。

原因：
- Twilio 给予您拥有的号码
- Vapi 给予您更好的对话 AI 通话质量和更多语音/模型灵活性

推荐流程：
1. 购买/保存 Twilio 号码
2. 将其导入 Vapi
3. 保存返回的 `VAPI_PHONE_NUMBER_ID`
4. 使用 `ai-call --provider vapi`

### 4) "我想用自定义录音语音消息通话"
使用 **Twilio 直接通话**和公共音频 URL。

原因：
- 播放自定义 MP3 的最简单方式
- 与 KClaw `text_to_speech` 加公共文件主机或隧道配合良好

## 文件和持久化状态

技能在两个地方持久化电话状态：

### `~/.kclaw/.env`
用于长期存在的提供商凭据和拥有的号码 ID，例如：
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `TWILIO_PHONE_NUMBER_SID`
- `BLAND_API_KEY`
- `VAPI_API_KEY`
- `VAPI_PHONE_NUMBER_ID`
- `PHONE_PROVIDER`（AI 通话提供商：bland 或 vapi）

### `~/.kclaw/telephony_state.json`
用于应该在会话之间保留的仅技能状态，例如：
- 记住的默认 Twilio 号码 / SID
- 记住的 Vapi 电话号码 ID
- 上次入站消息 SID/日期，用于收件箱轮询检查点

这意味着：
- 下次加载技能时，`diagnose` 可以告诉您已配置了什么号码
- `twilio-inbox --since-last --mark-seen` 可以从之前的检查点继续

## 找到辅助脚本

安装此技能后，按如下方式找到脚本：

```bash
SCRIPT="$(find ~/.kclaw/skills -path '*/telephony/scripts/telephony.py' -print -quit)"
```

如果 `SCRIPT` 为空，则技能尚未安装。

## 安装

这是一个官方可选技能，因此从技能中心安装：

```bash
kclaw skills search telephony
kclaw skills install official/productivity/telephony
```

## 提供商设置

### Twilio — 拥有的号码、SMS/MMS、直接通话、入站 SMS 轮询

注册：
- https://www.twilio.com/try-twilio

然后保存凭据到 KClaw：

```bash
python3 "$SCRIPT" save-twilio ACXXXXXXXXXXXXXXXXXXXXXXXXXXXX your_auth_token_here
```

搜索可用号码：

```bash
python3 "$SCRIPT" twilio-search --country US --area-code 702 --limit 5
```

购买并记住号码：

```bash
python3 "$SCRIPT" twilio-buy "+17025551234" --save-env
```

列出拥有的号码：

```bash
python3 "$SCRIPT" twilio-owned
```

稍后将其中一个设置为默认：

```bash
python3 "$SCRIPT" twilio-set-default "+17025551234" --save-env
# 或
python3 "$SCRIPT" twilio-set-default PNXXXXXXXXXXXXXXXXXXXXXXXXXXXX --save-env
```

### Bland.ai — 最简单外呼 AI 通话

注册：
- https://app.bland.ai

保存配置：

```bash
python3 "$SCRIPT" save-bland your_bland_api_key --voice mason
```

### Vapi — 更好的对话语音质量

注册：
- https://dashboard.vapi.ai

首先保存 API 密钥：

```bash
python3 "$SCRIPT" save-vapi your_vapi_api_key
```

将拥有的 Twilio 号码导入 Vapi 并持久化返回的电话号码 ID：

```bash
python3 "$SCRIPT" vapi-import-twilio --save-env
```

如果您已经知道 Vapi 电话号码 ID，直接保存：

```bash
python3 "$SCRIPT" save-vapi your_vapi_api_key --phone-number-id vapi_phone_number_id_here
```

## 诊断当前状态

随时检查技能已经知道的内容：

```bash
python3 "$SCRIPT" diagnose
```

在稍后的会话恢复工作时首先使用这个。

## 常见工作流

### A. 购买代理号码并稍后继续使用

1. 保存 Twilio 凭据：
```bash
python3 "$SCRIPT" save-twilio AC... auth_token_here
```

2. 搜索号码：
```bash
python3 "$SCRIPT" twilio-search --country US --area-code 702 --limit 10
```

3. 购买它并保存到 `~/.kclaw/.env` + 状态：
```bash
python3 "$SCRIPT" twilio-buy "+17025551234" --save-env
```

4. 下个会话，运行：
```bash
python3 "$SCRIPT" diagnose
```
这显示记住的默认号码和收件箱检查点状态。

### B. 从代理号码发送短信

```bash
python3 "$SCRIPT" twilio-send-sms "+15551230000" "Your deployment completed successfully."
```

带媒体：

```bash
python3 "$SCRIPT" twilio-send-sms "+15551230000" "Here is the chart." --media-url "https://example.com/chart.png"
```

### C. 无 webhook 服务器稍后检查入站短信

轮询默认 Twilio 号码的收件箱：

```bash
python3 "$SCRIPT" twilio-inbox --limit 20
```

仅显示自上次检查点以来到达的消息，并在阅读完毕后推进检查点：

```bash
python3 "$SCRIPT" twilio-inbox --since-last --mark-seen
```

这是"下次加载技能时如何访问收到的消息？"的主要答案

### D. 使用内置 TTS 进行直接 Twilio 通话

```bash
python3 "$SCRIPT" twilio-call "+15551230000" --message "Hello! This is KClaw calling with your status update." --voice Polly.Joanna
```

### E. 使用录音/自定义语音消息通话

这是重用自己的现有 `text_to_speech` 支持的主要路径。

在以下情况下使用：
- 您希望通话使用 KClaw 配置的 TTS 语音而不是 Twilio `<Say>`
- 您想要单向语音传递（简报、警报、笑话、提醒、状态更新）
- 您**不需要**实时对话电话

单独生成或托管音频，然后：

```bash
python3 "$SCRIPT" twilio-call "+155****0000" --audio-url "https://example.com/briefing.mp3"
```

推荐的 KClaw TTS -> Twilio Play 工作流：

1. 使用 KClaw `text_to_speech` 生成音频
2. 使生成的 MP3 可公开访问
3. 使用 `--audio-url` 放置 Twilio 电话

MP3 的好托管选项：
- 临时公共对象/存储 URL
- 到本地静态文件服务器的短期隧道
- 电话提供商可以直接获取的任何现有 HTTPS URL

重要提示：
- KClaw TTS 非常适合预录制的外呼消息
- Bland/Vapi 更适合**实时对话 AI 电话**，因为它们自己处理实时电话音频栈
- KClaw STT/TTS 本身不用作全双工电话对话引擎；那需要比重此技能引入的更重的流式传输/webhook 集成

### F. 使用 Twilio 直接通话导航电话树 / IVR

如果需要在通话连接后按数字，使用 `--send-digits`。
Twilio 将 `w` 解释为短暂等待。

```bash
python3 "$SCRIPT" twilio-call "+18005551234" --message "Connecting to billing now." --send-digits "ww1w2w3"
```

这对于在转接给人类或传递简短状态消息之前到达特定菜单分支很有用。

### G. 通过 Bland.ai 进行外呼 AI 电话

```bash
python3 "$SCRIPT" ai-call "+15551230000" "Call the dental office, ask for a cleaning appointment on Tuesday afternoon, and if they do not have Tuesday availability, ask for Wednesday or Thursday instead." --provider bland --voice mason --max-duration 3
```

检查状态：

```bash
python3 "$SCRIPT" ai-status <call_id> --provider bland
```

完成后询问 Bland 分析问题：

```bash
python3 "$SCRIPT" ai-status <call_id> --provider bland --analyze "Was the appointment confirmed?,What date and time?,Any special instructions?"
```

### H. 在您拥有的号码上通过 Vapi 进行外呼 AI 电话

1. 将 Twilio 号码导入 Vapi：
```bash
python3 "$SCRIPT" vapi-import-twilio --save-env
```

2. 放置通话：
```bash
python3 "$SCRIPT" ai-call "+15551230000" "You are calling to make a dinner reservation for two at 7:30 PM. If that is unavailable, ask for the nearest time between 6:30 and 8:30 PM." --provider vapi --max-duration 4
```

3. 检查结果：
```bash
python3 "$SCRIPT" ai-status <call_id> --provider vapi
```

## 建议的代理程序

当用户要求通话或短信时：

1. 通过决策树确定适合请求的路径。
2. 如果配置状态不清楚，运行 `diagnose`。
3. 收集完整任务详情。
4. 通话或短信前与用户确认。
5. 使用正确的命令。
6. 如需要轮询结果。
7. 总结结果，不要将第三方号码持久化到 KClaw 记忆。

## 此技能仍然不能做的事情

- 实时入站电话应答
- 基于 webhook 的实时 SMS 推入代理循环
- 任意第三方 2FA 提供商的保证支持

这些需要比重纯可选技能更多的基础设施。

## 陷阱

- Twilio 试用账户和地区规则可能限制您可以通话/短信的对象。
- 一些服务拒绝 VoIP 号码用于 2FA。
- `twilio-inbox` 轮询 REST API；不是即时推送传递。
- Vapi 外呼通话仍然依赖于拥有有效的导入号码。
- Bland 最简单，但并非始终最好听。
- 不要将任意第三方电话号码存储在 KClaw 记忆中。

## 验证检查清单

设置后，您应该能够仅用此技能完成以下所有操作：

1. `diagnose` 显示提供商准备状态和记住的状态
2. 搜索和购买 Twilio 号码
3. 将该号码持久化到 `~/.kclaw/.env`
4. 从拥有的号码发送 SMS
5. 稍后轮询拥有号码的入站短信
6. 放置直接 Twilio 电话
7. 通过 Bland 或 Vapi 放置 AI 电话

## 参考

- Twilio 电话号码：https://www.twilio.com/docs/phone-numbers/api
- Twilio 消息：https://www.twilio.com/docs/messaging/api/message-resource
- Twilio 语音：https://www.twilio.com/docs/voice/api/call-resource
- Vapi 文档：https://docs.vapi.ai/
- Bland.ai：https://app.bland.ai/
