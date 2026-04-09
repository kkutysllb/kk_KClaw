---
title: "功能概述"
sidebar_label: "概述"
sidebar_position: 1
---

# 功能概述

KClaw Agent 包含丰富的功能，远超基本聊天。从持久记忆和文件感知上下文到浏览器自动化和语音对话，这些功能共同使 KClaw 成为强大的自主助手。

## 核心功能

- **[工具与工具集](tools.md)** — 工具是扩展代理能力的函数。它们被组织成逻辑工具集，可以在每个平台上启用或禁用，覆盖网络搜索、终端执行、文件编辑、记忆、委托等。
- **[技能系统](skills.md)** — 按需加载的知识文档。技能遵循渐进式披露模式以最小化令牌使用，并兼容 [agentskills.io](https://agentskills.io/specification) 开放标准。
- **[持久记忆](memory.md)** — 有界限的精选记忆，跨会话持久化。KClaw 记住您的偏好、项目、环境以及通过 `MEMORY.md` 和 `USER.md` 学习的内容。
- **[上下文文件](context-files.md)** — KClaw 自动发现并加载项目上下文文件（`.kclaw.md`、`AGENTS.md`、`CLAUDE.md`、`SOUL.md`、`.cursorrules`），以塑造其在您的项目中的行为。
- **[上下文引用](context-references.md)** — 输入 `@` 后跟引用，将文件、文件夹、git diff 和 URL 直接注入到您的消息中。KClaw 内联展开引用并自动附加内容。
- **[检查点](../checkpoints-and-rollback.md)** — KClaw 在进行文件更改前自动快照您的工作目录，为您提供安全网以便在使用 `/rollback` 出问题时回滚。

## 自动化

- **[计划任务（Cron）](cron.md)** — 使用自然语言或 cron 表达式安排自动运行的任务。作业可以附加技能，将结果传递到任何平台，并支持暂停/恢复/编辑操作。
- **[子代理委托](delegation.md)** — `delegate_task` 工具生成具有隔离上下文、受限工具集和自己的终端会话的子代理实例。最多可运行 3 个并发子代理进行并行工作流。
- **[代码执行](code-execution.md)** — `execute_code` 工具让代理编写以编程方式调用 KClaw 工具的 Python 脚本，通过沙箱 RPC 执行将多步工作流压缩为单个 LLM 轮次。
- **[事件钩子](hooks.md)** — 在关键生命周期点运行自定义代码。网关钩子处理日志、警报和 webhook；插件钩子处理工具拦截、指标和保护。
- **[批处理](batch-processing.md)** — 跨数百或数千个提示并行运行 KClaw 代理，生成结构化的 ShareGPT 格式轨迹数据用于训练数据生成或评估。

## 媒体与网络

- **[语音模式](voice-mode.md)** — 跨 CLI 和消息平台的完整语音交互。使用麦克风与代理对话，听取语音回复，并在 Discord 语音频道中进行实时语音对话。
- **[浏览器自动化](browser.md)** — 多种后端的完整浏览器自动化：Browserbase 云、Browser Use 云、通过 CDP 的本地 Chrome 或本地 Chromium。导航网站、填写表单和提取信息。
- **[视觉与图像粘贴](vision.md)** — 多模态视觉支持。将图像从剪贴板粘贴到 CLI 中，并要求代理使用任何支持视觉的模型对其进行分析、描述或处理。
- **[图像生成](image-generation.md)** — 使用 FAL.ai 的 FLUX 2 Pro 模型从文本提示生成图像，并通过 Clarity Upscaler 进行自动 2 倍放大。
- **[语音与 TTS](tts.md)** — 跨所有消息平台的文本转语音输出和语音消息转录，有五个提供商选项：Edge TTS（免费）、ElevenLabs、OpenAI TTS、 MiniMax 和 NeuTTS。

## 集成

- **[MCP 集成](mcp.md)** — 通过 stdio 或 HTTP 传输连接到任何 MCP 服务器。访问 GitHub、数据库、文件系统 和内部 API 的外部工具，无需编写原生 KClaw 工具。包括每服务器工具过滤和采样支持。
- **[提供商路由](provider-routing.md)** — 精细控制哪个 AI 提供商处理您的请求。通过排序、白名单、黑名单和优先级排序优化成本、速度或质量。
- **[备用提供商](fallback-providers.md)** — 当您的主模型遇到错误时，自动故障转移到备用 LLM 提供商，包括对视觉和压缩等辅助任务的独立备用。
- **[凭证池](credential-pools.md)** — 在同一提供商的多个密钥之间分配 API 调用。在速率限制或失败时自动轮换。
- **[记忆提供商](memory-providers.md)** — 插入外部记忆后端（Honcho、OpenViking、Mem0、Hindsight、Holographic、RetainDB、ByteRover），用于超出内置记忆系统的跨会话用户建模和个性化。
- **[API 服务器](api-server.md)** — 将 KClaw 公开为 OpenAI 兼容的 HTTP 端点。连接任何使用 OpenAI 格式的前端——Open WebUI、LobeChat、LibreChat 等。
- **[IDE 集成（ACP）](acp.md)** — 在 ACP 兼容的编辑器（如 VS Code、Zed 和 JetBrains）中使用 KClaw。聊天、工具活动、文件 diff 和终端命令在您的编辑器中渲染。
- **[强化学习训练](rl-training.md)** — 从代理会话生成轨迹数据，用于强化学习和模型微调。

## 自定义

- **[人格与 SOUL.md](personality.md)** — 完全可定制的代理人格。`SOUL.md` 是主要身份文件——系统提示中的第一项——您可以为每个会话切换内置或自定义的 `/personality` 预设。
- **[皮肤与主题](skins.md)** — 自定义 CLI 的视觉呈现：横幅颜色、微调器面孔和动词、响应框标签、品牌文字和工具活动前缀。
- **[插件](plugins.md)** — 无需修改核心代码即可添加自定义工具、钩子和集成。将目录放入 `~/.kclaw/plugins/` 中，包含 `plugin.yaml` 和 Python 代码。
