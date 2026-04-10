---
slug: /
sidebar_position: 0
title: "KClaw Agent 文档"
description: "由 kkutysllb 构建的自我改进 AI 代理。内置学习循环，从经验中创建技能，在使用中改进技能，并跨会话记忆。"
hide_table_of_contents: true
---

# KClaw Agent

由 [kkutysllb](https://nousresearch.com) 构建的自我改进 AI 代理。唯一具有内置学习循环的代理——它从经验中创建技能，在使用中改进技能，推动自己保持知识，并在跨会话中建立对用户越来越深入的了解。

<div style={{display: 'flex', gap: '1rem', marginBottom: '2rem', flexWrap: 'wrap'}}>
  <a href="/docs/getting-started/installation" style={{display: 'inline-block', padding: '0.6rem 1.2rem', backgroundColor: '#FFD700', color: '#07070d', borderRadius: '8px', fontWeight: 600, textDecoration: 'none'}}>开始使用 →</a>
  <a href="https://github.com/NousResearch/kclaw" style={{display: 'inline-block', padding: '0.6rem 1.2rem', border: '1px solid rgba(255,215,0,0.2)', borderRadius: '8px', textDecoration: 'none'}}>在 GitHub 上查看</a>
</div>

## 什么是 KClaw Agent？

它不是绑在 IDE 上的代码副驾驶，也不是单一 API 的聊天机器人包装器。它是一个**自主代理**，运行时间越长能力越强。它可以生活在任何地方——5 美元的 VPS、GPU 集群，或者几乎空闲时成本几乎为零的无服务器基础设施（Daytona、Modal）。当你通过 Telegram 与它交谈时，它可以在你从不 SSH 连接的云 VM 上工作。它不绑定在你的笔记本电脑上。

## 快速链接

| | |
|---|---|
| 🚀 **[安装](/docs/getting-started/installation)** | 在 Linux、macOS 或 WSL2 上 60 秒内安装 |
| 📖 **[快速入门教程](/docs/getting-started/quickstart)** | 您的第一次对话和尝试的关键功能 |
| 🗺️ **[学习路径](/docs/getting-started/learning-path)** | 根据您的经验水平找到合适的文档 |
| ⚙️ **[配置](/docs/user-guide/configuration)** | 配置文件、提供商、模型和选项 |
| 💬 **[消息网关](/docs/user-guide/messaging)** | 设置 Telegram、Discord、Slack 或 WhatsApp |
| 🔧 **[工具和工具集](/docs/user-guide/features/tools)** | 47 个内置工具及配置方法 |
| 🧠 **[记忆系统](/docs/user-guide/features/memory)** | 跨会话增长的持久记忆 |
| 📚 **[技能系统](/docs/user-guide/features/skills)** | 代理创建和重用的程序记忆 |
| 🔌 **[MCP 集成](/docs/user-guide/features/mcp)** | 连接到 MCP 服务器、过滤工具并安全扩展 KClaw |
| 🧭 **[在 KClaw 中使用 MCP](/docs/guides/use-mcp-with-kclaw)** | 实用的 MCP 设置模式、示例和教程 |
| 🎙️ **[语音模式](/docs/user-guide/features/voice-mode)** | 在 CLI、Telegram、Discord 和 Discord VC 中的实时语音交互 |
| 🗣️ **[在 KClaw 中使用语音模式](/docs/guides/use-voice-mode-with-kclaw)** | KClaw 语音工作流的手动设置和使用模式 |
| 🎭 **[人格和 SOUL.md](/docs/user-guide/features/personality)** | 使用全局 SOUL.md 定义 KClaw 的默认声音 |
| 📄 **[上下文文件](/docs/user-guide/features/context-files)** | 塑造每个对话的项目上下文文件 |
| 🔒 **[安全](/docs/user-guide/security)** | 命令批准、授权、容器隔离 |
| 💡 **[提示和最佳实践](/docs/guides/tips)** | 充分利用 KClaw 的快速技巧 |
| 🏗️ **[架构](/docs/developer-guide/architecture)** | 底层工作原理 |
| ❓ **[常见问题和故障排除](/docs/reference/faq)** | 常见问题及解决方案 |

## 关键功能

- **闭环学习循环** — 带定期推动的代理策划记忆、自主技能创建、使用中的技能自我改进、带 LLM 摘要的 FTS5 跨会话召回，以及 [Honcho](https://github.com/plastic-labs/honcho) 辩证用户建模
- **随处运行，而不只是笔记本** — 6 个终端后端：本地、Docker、SSH、Daytona、Singularity、Modal。Daytona 和 Modal 提供无服务器持久化——您的环境在空闲时休眠，几乎不产生成本
- **生活在您所在的地方** — CLI、Telegram、Discord、Slack、WhatsApp、Signal、Matrix、Mattermost、Email、SMS、DingTalk、Feishu、WeCom、BlueBubbles、Home Assistant——来自一个网关的 15+ 平台
- **由模型训练者构建** — 由 [kkutysllb](https://nousresearch.com) 创建，KClaw、Nomos 和 Psyche 背后的实验室。支持 [Nous Portal](https://portal.nousresearch.com)、[OpenRouter](https://openrouter.ai)、OpenAI 或任何端点
- **计划自动化** — 内置 cron，可投放到任何平台
- **委托和并行化** — 为并行工作流生成隔离的子代理。通过 `execute_code` 进行编程式工具调用，将多步骤管道折叠为单一推理调用
- **开放标准技能** — 与 [agentskills.io](https://agentskills.io) 兼容。技能可移植、可共享，通过技能中心由社区贡献
- **完全网络控制** — 搜索、提取、浏览、视觉、图像生成、TTS
- **MCP 支持** — 连接到任何 MCP 服务器以扩展工具能力
- **研究就绪** — 批处理、轨迹导出、使用 Atropos 进行 RL 训练。由 [kkutysllb](https://nousresearch.com) 构建——KClaw、Nomos 和 Psyche 模型背后的实验室
