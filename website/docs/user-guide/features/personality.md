---
sidebar_position: 9
title: "人格与 SOUL.md"
description: "使用全局 SOUL.md、内置人格和自定义人格定义自定义 KClaw Agent 的人格"
---

# 人格与 SOUL.md

KClaw Agent 的人格完全可自定义。`SOUL.md` 是**主要身份**——它是系统提示中的第一项，定义代理是谁。

- `SOUL.md` — 一个持久的 persona 文件，位于 `KCLAW_HOME`，作为代理的身份（系统提示中的位置 #1）
- 内置或自定义的 `/personality` 预设 — 会话级系统提示覆盖

如果您想更改 KClaw 是谁——或者用完全不同的代理 persona 替换它——请编辑 `SOUL.md`。

## SOUL.md 现在如何工作

KClaw 现在自动在以下位置生成默认 `SOUL.md`：

```text
~/.kclaw/SOUL.md
```

更准确地说，它使用当前实例的 `KCLAW_HOME`，因此如果您使用自定义主目录运行 KClaw，它将使用：

```text
$KCLAW_HOME/SOUL.md
```

### 重要行为

- **SOUL.md 是代理的主要身份。** 它占据系统提示中的位置 #1，替换硬编码的默认身份。
- KClaw 如果不存在会自动创建入门 `SOUL.md`
- 现有用户 `SOUL.md` 文件永远不会被覆盖
- KClaw 仅从 `KCLAW_HOME` 加载 `SOUL.md`
- KClaw 不在当前工作目录中查找 `SOUL.md`
- 如果 `SOUL.md` 存在但为空，或无法加载，KClaw 回退到内置默认身份
- 如果 `SOUL.md` 有内容，该内容在安全扫描和截断后逐字注入
- SOUL.md **不会**在上下文文件部分重复——它只出现一次，作为身份

这使 `SOUL.md` 成为真正的每用户或每实例身份，而不仅仅是附加层。

## 为什么这样设计

这使人格可预测。

如果 KClaw 从您碰巧启动它的目录加载 `SOUL.md`，您的人格可能会在项目之间意外改变。通过仅从 `KCLAW_HOME` 加载，人格属于 KClaw 实例本身。

这也使教导用户更容易：
- "编辑 `~/.kclaw/SOUL.md` 更改 KClaw 的默认人格。"

## 在哪里编辑

对于大多数用户：

```bash
~/.kclaw/SOUL.md
```

如果您使用自定义主目录：

```bash
$KCLAW_HOME/SOUL.md
```

## 什么应该放在 SOUL.md 中？

将其用于持久的语音和人格指导，例如：
- 语气
- 沟通风格
- 直接程度
- 默认交互风格
- 风格上要避免什么
- KClaw 如何处理不确定性、分歧或歧义

少用于：
- 一次性项目说明
- 文件路径
- 仓库约定
- 临时工作流细节

那些属于 `AGENTS.md`，而不是 `SOUL.md`。

## 好的 SOUL.md 内容

一个好的 SOUL 文件是：
- 在不同上下文中稳定
- 足够广泛以适用于许多对话
- 足够具体以实质性地塑造语音
- 专注于沟通和身份，而不是特定任务的指令

### 示例

```markdown
# Personality

You are a pragmatic senior engineer with strong taste.
You optimize for truth, clarity, and usefulness over politeness theater.

## Style
- Be direct without being cold
- Prefer substance over filler
- Push back when something is a bad idea
- Admit uncertainty plainly
- Keep explanations compact unless depth is useful

## What to avoid
- Sycophancy
- Hype language
- Repeating the user's framing if it's wrong
- Overexplaining obvious things

## Technical posture
- Prefer simple systems over clever systems
- Care about operational reality, not idealized architecture
- Treat edge cases as part of the design, not cleanup
```

## KClaw 注入提示的内容

`SOUL.md` 内容直接进入系统提示的位置 #1——代理身份位置。周围不添加包装语言。

内容经过：
- 提示注入扫描
- 如果太大则截断

如果文件为空、仅空白或无法读取，KClaw 回退到内置默认身份（"You are KClaw Agent, an intelligent AI assistant created by kkutysllb..."）。当 `skip_context_files` 设置时（例如在子代理/委托上下文中），此回退也适用。

## 安全扫描

`SOUL.md` 像其他承载上下文的文件一样，在包含之前会被扫描提示注入模式。

这意味着您仍应保持其专注于 persona/voice，而不是试图偷偷放入奇怪的元指令。

## SOUL.md vs AGENTS.md

这是最重要的区别。

### SOUL.md
用于：
- 身份
- 语气
- 风格
- 沟通默认值
- 人格级行为

### AGENTS.md
用于：
- 项目架构
- 编码约定
- 工具偏好
- 仓库特定工作流
- 命令、端口、路径、部署说明

一个有用的规则：
- 如果它应该随您到各处，它属于 `SOUL.md`
- 如果它属于项目，它属于 `AGENTS.md`

## SOUL.md vs `/personality`

`SOUL.md` 是您的持久默认人格。

`/personality` 是会话级覆盖，更改或补充当前系统提示。

所以：
- `SOUL.md` = 基础语音
- `/personality` = 临时模式切换

示例：
- 保持务实的默认 SOUL，然后使用 `/personality teacher` 进行辅导对话
- 保持简洁的 SOUL，然后使用 `/personality creative` 进行头脑风暴

## 内置人格

KClaw 附带内置人格，您可以使用 `/personality` 切换。

| 名称 | 描述 |
|------|-------------|
| **helpful** | 友好、多功能的助手 |
| **concise** | 简洁、切中要点的回复 |
| **technical** | 详细、准确的技术专家 |
| **creative** | 创新、发散性思维 |
| **teacher** | 耐心的教育者，例子清晰 |
| **kawaii** | 可爱的表达，闪光和热情 ★ |
| **catgirl** | Neko-chan，带有猫类表达，nya~ |
| **pirate** | 船长 KClaw，技术精湛的海盗 |
| **shakespeare** | 带有戏剧性风格的 Bardic 散文 |
| **surfer** | 完全冷静的兄弟氛围 |
| **noir** | 硬汉侦探叙述 |
| **uwu** | 最大化可爱与 uwu 语言 |
| **philosopher** | 对每个查询的深度思考 |
| **hype** | 最大能量和热情！！！ |

## 使用命令切换人格

### CLI

```text
/personality
/personality concise
/personality technical
```

### 消息平台

```text
/personality teacher
```

这些是方便的覆盖，但您的全局 `SOUL.md` 仍然赋予 KClaw 其持久的默认人格，除非覆盖有意义地更改了它。

## 配置中的自定义人格

您也可以在 `~/.kclaw/config.yaml` 的 `agent.personalities` 下定义命名的自定义人格。

```yaml
agent:
  personalities:
    codereviewer: >
      You are a meticulous code reviewer. Identify bugs, security issues,
      performance concerns, and unclear design choices. Be precise and constructive.
```

然后使用以下命令切换到它：

```text
/personality codereviewer
```

## 推荐工作流

一个强大的默认设置是：

1. 在 `~/.kclaw/SOUL.md` 中保持深思熟虑的全局 `SOUL.md`
2. 将项目说明放在 `AGENTS.md`
3. 仅在您想要临时模式切换时使用 `/personality`

这给您：
- 稳定的语音
- 项目特定行为在应有的地方
- 需要时的临时控制

## 人格如何与完整提示交互

高级别地，提示栈包括：
1. **SOUL.md**（代理身份——或如果 SOUL.md 不可用则使用内置回退）
2. 工具感知行为指导
3. 记忆/用户上下文
4. 技能指导
5. 上下文文件（`AGENTS.md`、`.cursorrules`）
6. 时间戳
7. 平台特定格式提示
8. 可选的系统提示覆盖如 `/personality`

`SOUL.md` 是基础——其他一切都在其上构建。

## 相关文档

- [上下文文件](/docs/user-guide/features/context-files)
- [配置](/docs/user-guide/configuration)
- [提示与最佳实践](/docs/guides/tips)
- [SOUL.md 指南](/docs/guides/use-soul-with-kclaw)

## CLI 外观 vs 对话人格

对话人格和 CLI 外观是分开的：

- `SOUL.md`、`agent.system_prompt` 和 `/personality` 影响 KClaw 的说话方式
- `display.skin` 和 `/skin` 影响 KClaw 在终端中的外观

关于终端外观，请参见[皮肤与主题](./skins.md)。
