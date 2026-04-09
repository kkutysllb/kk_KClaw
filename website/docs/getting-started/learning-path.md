---
sidebar_position: 3
title: "学习路径"
description: "根据您的经验水平和目标选择 KClaw Agent 文档的学习路径。"
---

# 学习路径

KClaw Agent 可以做很多事情——CLI 助手、Telegram/Discord 机器人、任务自动化、RL 训练等。本页面帮助您根据经验水平和您想要完成的目标确定从哪里开始以及应该阅读什么。

:::tip 从这里开始
如果您还没有安装 KClaw Agent，请从 [安装指南](/docs/getting-started/installation) 开始，然后运行 [快速入门](/docs/getting-started/quickstart)。以下所有内容都假设您有一个可工作的安装。
:::

## 如何使用此页面

- **知道您的级别？** 跳转到 [经验级别表](#by-experience-level) 并按照您所在层的阅读顺序进行。
- **有特定目标？** 跳转到 [按用例](#by-use-case) 并找到匹配的场景。
- **只是浏览？** 查看 [关键功能一览](#key-features-at-a-glance) 表以快速了解 KClaw Agent 的所有功能。

## 按经验级别

| 级别 | 目标 | 推荐的阅读内容 | 时间估计 |
|---|---|---|---|
| **初学者** | 启动并运行，进行基本对话，使用内置工具 | [安装](/docs/getting-started/installation) → [快速入门](/docs/getting-started/quickstart) → [CLI 使用](/docs/user-guide/cli) → [配置](/docs/user-guide/configuration) | 约 1 小时 |
| **中级** | 设置消息机器人，使用内存、cron 作业和技能等高级功能 | [会话](/docs/user-guide/sessions) → [消息](/docs/user-guide/messaging) → [工具](/docs/user-guide/features/tools) → [技能](/docs/user-guide/features/skills) → [记忆](/docs/user-guide/features/memory) → [Cron](/docs/user-guide/features/cron) | 约 2-3 小时 |
| **高级** | 构建自定义工具、创建技能、使用 RL 训练模型、为项目做贡献 | [架构](/docs/developer-guide/architecture) → [添加工具](/docs/developer-guide/adding-tools) → [创建技能](/docs/developer-guide/creating-skills) → [RL 训练](/docs/user-guide/features/rl-training) → [贡献](/docs/developer-guide/contributing) | 约 4-6 小时 |

## 按用例

选择与您想要做的事情匹配的场景。每个场景都链接到您应该按顺序阅读的相关文档。

### "我想要一个 CLI 编码助手"

将 KClaw Agent 用作交互式终端助手，用于编写、审查和运行代码。

1. [安装](/docs/getting-started/installation)
2. [快速入门](/docs/getting-started/quickstart)
3. [CLI 使用](/docs/user-guide/cli)
4. [代码执行](/docs/user-guide/features/code-execution)
5. [上下文文件](/docs/user-guide/features/context-files)
6. [提示和技巧](/docs/guides/tips)

:::tip
将文件直接传递到您的对话中作为上下文文件。KClaw Agent 可以读取、编辑和运行您项目中的代码。
:::

### "我想要一个 Telegram/Discord 机器人"

将 KClaw Agent 部署为您喜爱的消息平台上的机器人。

1. [安装](/docs/getting-started/installation)
2. [配置](/docs/user-guide/configuration)
3. [消息概述](/docs/user-guide/messaging)
4. [Telegram 设置](/docs/user-guide/messaging/telegram)
5. [Discord 设置](/docs/user-guide/messaging/discord)
6. [语音模式](/docs/user-guide/features/voice-mode)
7. [在 KClaw 中使用语音模式](/docs/guides/use-voice-mode-with-kclaw)
8. [安全](/docs/user-guide/security)

有关完整项目示例，请参阅：
- [每日简报机器人](/docs/guides/daily-briefing-bot)
- [团队 Telegram 助手](/docs/guides/team-telegram-assistant)

### "我想要自动化任务"

计划重复任务、运行批处理作业或将代理操作链接在一起。

1. [快速入门](/docs/getting-started/quickstart)
2. [Cron 计划](/docs/user-guide/features/cron)
3. [批处理](/docs/user-guide/features/batch-processing)
4. [委托](/docs/user-guide/features/delegation)
5. [钩子](/docs/user-guide/features/hooks)

:::tip
Cron 作业让 KClaw Agent 按计划运行任务——每日摘要、周期性检查、自动报告——即使您不在场也可以。
:::

### "我想要构建自定义工具/技能"

用您自己的工具和可重用的技能包扩展 KClaw Agent。

1. [工具概述](/docs/user-guide/features/tools)
2. [技能概述](/docs/user-guide/features/skills)
3. [MCP（模型上下文协议）](/docs/user-guide/features/mcp)
4. [架构](/docs/developer-guide/architecture)
5. [添加工具](/docs/developer-guide/adding-tools)
6. [创建技能](/docs/developer-guide/creating-skills)

:::tip
工具是代理可以调用的单个函数。技能是捆绑在一起的工具、提示和配置的包。先从工具开始，然后升格到技能。
:::

### "我想要训练模型"

使用强化学习通过 KClaw Agent 内置的 RL 训练管道微调模型行为。

1. [快速入门](/docs/getting-started/quickstart)
2. [配置](/docs/user-guide/configuration)
3. [RL 训练](/docs/user-guide/features/rl-training)
4. [提供商路由](/docs/user-guide/features/provider-routing)
5. [架构](/docs/developer-guide/architecture)

:::tip
RL 训练在您已经了解 KClaw Agent 如何处理对话和工具调用的基本原理时效果最佳。如果您是新手，请先完成初学者路径。
:::

### "我想将它用作 Python 库"

以编程方式将 KClaw Agent 集成到您自己的 Python 应用程序中。

1. [安装](/docs/getting-started/installation)
2. [快速入门](/docs/getting-started/quickstart)
3. [Python 库指南](/docs/guides/python-library)
4. [架构](/docs/developer-guide/architecture)
5. [工具](/docs/user-guide/features/tools)
6. [会话](/docs/user-guide/sessions)

## 关键功能一览

不确定有什么可用？这是所有主要功能的快速目录：

| 功能 | 功能 | 链接 |
|---|---|---|
| **工具** | 代理可以调用的内置工具（文件 I/O、搜索、shell 等） | [工具](/docs/user-guide/features/tools) |
| **技能** | 可安装的插件包，添加新功能 | [技能](/docs/user-guide/features/skills) |
| **记忆** | 跨会话持久记忆 | [记忆](/docs/user-guide/features/memory) |
| **上下文文件** | 将文件和目录输入对话 | [上下文文件](/docs/user-guide/features/context-files) |
| **MCP** | 通过模型上下文协议连接到外部工具服务器 | [MCP](/docs/user-guide/features/mcp) |
| **Cron** | 计划重复代理任务 | [Cron](/docs/user-guide/features/cron) |
| **委托** | 生成子代理进行并行工作 | [委托](/docs/user-guide/features/delegation) |
| **代码执行** | 在沙盒环境中运行代码 | [代码执行](/docs/user-guide/features/code-execution) |
| **浏览器** | Web 浏览和抓取 | [浏览器](/docs/user-guide/features/browser) |
| **钩子** | 事件驱动的回调和中间件 | [钩子](/docs/user-guide/features/hooks) |
| **批处理** | 批量处理多个输入 | [批处理](/docs/user-guide/features/batch-processing) |
| **RL 训练** | 使用强化学习微调模型 | [RL 训练](/docs/user-guide/features/rl-training) |
| **提供商路由** | 跨多个 LLM 提供商路由请求 | [提供商路由](/docs/user-guide/features/provider-routing) |

## 接下来阅读什么

根据您现在的位置：

- **刚完成安装？** → 前往 [快速入门](/docs/getting-started/quickstart) 运行您的第一次对话。
- **完成了快速入门？** → 阅读 [CLI 使用](/docs/user-guide/cli) 和 [配置](/docs/user-guide/configuration) 来自定义您的设置。
- **对基础知识感到舒适？** → 探索 [工具](/docs/user-guide/features/tools)、[技能](/docs/user-guide/features/skills) 和 [记忆](/docs/user-guide/features/memory) 以释放代理的全部能力。
- **为团队设置？** → 阅读 [安全](/docs/user-guide/security) 和 [会话](/docs/user-guide/sessions) 了解访问控制和对话管理。
- **准备好构建？** → 跳转到 [开发者指南](/docs/developer-guide/architecture) 了解内部结构并开始贡献。
- **想要实际示例？** → 查看 [指南](/docs/guides/tips) 部分获取实际项目和小技巧。

:::tip
您不需要阅读所有内容。选择与您的目标匹配的路径，按顺序关注链接，您将很快变得高效。您可以随时返回此页面找到您的下一步。
:::
