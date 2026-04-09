---
sidebar_position: 7
title: "在 KClaw 中使用 SOUL.md"
description: "如何使用 SOUL.md 塑造 KClaw Agent 的默认语音、什么应该放在那里，以及它与 AGENTS.md 和 /personality 的区别"
---

# 在 KClaw 中使用 SOUL.md

`SOUL.md` 是您的 KClaw 实例的**主要身份**。它是系统提示中的第一项——它定义代理是谁、如何说话以及应该避免什么。

如果您希望 KClaw 每次与您交谈时都感觉像同一个助手——或者如果您想用自己的完全替换 KClaw persona——请使用这个文件。

## SOUL.md 的用途

使用 `SOUL.md`：
- 语气
- 人格
- 沟通风格
- KClaw 应该有多直接或温暖
- KClaw 应该在风格上避免什么
- KClaw 应该如何看待不确定性、分歧和歧义

简言之：
- `SOUL.md` 是关于 KClaw 是谁以及 KClaw 如何说话

## SOUL.md 不用于什么

不要用于：
- 特定仓库的编码约定
- 文件路径
- 命令
- 服务端口
- 架构说明
- 项目工作流指令

那些属于 `AGENTS.md`。

一个好规则：
- 如果它应该无处不在，请放在 `SOUL.md`
- 如果它只属于一个项目，请放在 `AGENTS.md`

## 它在哪里

KClaw 现在仅使用当前实例的全局 SOUL 文件：

```text
~/.kclaw/SOUL.md
```

如果您使用自定义主目录运行 KClaw，它变成：

```text
$KCLAW_HOME/SOUL.md
```

## 首次运行行为

如果 `SOUL.md` 尚不存在，KClaw 会自动为您生成一个入门的 `SOUL.md`。

这意味着大多数用户现在从一个可以立即读取和编辑的真实文件开始。

重要：
- 如果您已有 `SOUL.md`，KClaw 不会覆盖它
- 如果文件存在但为空，KClaw 不会从中添加任何内容到提示

## KClaw 如何使用它

当 KClaw 启动会话时，它从 `KCLAW_HOME` 读取 `SOUL.md`，扫描提示注入模式，如需要截断它，并将其用作**代理身份**——系统提示中的位置 #1。这意味着 SOUL.md 完全替换内置的默认身份文本。

如果 SOUL.md 缺失、为空或无法加载，KClaw 回退到内置默认身份。

文件周围没有添加包装语言。内容本身很重要——以您希望代理思考和说话的方式书写。

## 一个好的首次编辑

如果您不做其他操作，请打开文件只更改几行，使其感觉像您。

例如：

```markdown
You are direct, calm, and technically precise.
Prefer substance over politeness theater.
Push back clearly when an idea is weak.
Keep answers compact unless deeper detail is useful.
```

仅此一项就可以显著改变 KClaw 的感觉。

## 示例风格

### 1. 务实的工程师

```markdown
You are a pragmatic senior engineer.
You care more about correctness and operational reality than sounding impressive.

## Style
- Be direct
- Be concise unless complexity requires depth
- Say when something is a bad idea
- Prefer practical tradeoffs over idealized abstractions

## Avoid
- Sycophancy
- Hype language
- Overexplaining obvious things
```

### 2. 研究伙伴

```markdown
You are a thoughtful research collaborator.
You are curious, honest about uncertainty, and excited by unusual ideas.

## Style
- Explore possibilities without pretending certainty
- Distinguish speculation from evidence
- Ask clarifying questions when the idea space is underspecified
- Prefer conceptual depth over shallow completeness
```

### 3. 教师/解释者

```markdown
You are a patient technical teacher.
You care about understanding, not performance.

## Style
- Explain clearly
- Use examples when they help
- Do not assume prior knowledge unless the user signals it
- Build from intuition to details
```

### 4. 严格审查者

```markdown
You are a rigorous reviewer.
You are fair, but you do not soften important criticism.

## Style
- Point out weak assumptions directly
- Prioritize correctness over harmony
- Be explicit about risks and tradeoffs
- Prefer blunt clarity to vague diplomacy
```

## 是什么造就了一个强大的 SOUL.md？

一个强大的 `SOUL.md` 是：
- 稳定的
- 广泛适用的
- 语音具体的
- 不过载临时指令

一个弱的 `SOUL.md` 是：
- 充满项目细节
- 矛盾的
- 试图微观管理每个响应形状
- 大多是通用填充物如"要有帮助"和"要清晰"

KClaw 已经努力变得有帮助和清晰。`SOUL.md` 应该添加真正的人格和风格，而不是重述明显的默认值。

## 建议的结构

您不需要标题，但它们有帮助。

一个简单有效的结构：

```markdown
# Identity
Who KClaw is.

# Style
How KClaw should sound.

# Avoid
What KClaw should not do.

# Defaults
How KClaw should behave when ambiguity appears.
```

## SOUL.md vs /personality

这些是互补的。

使用 `SOUL.md` 作为您的持久基线。
使用 `/personality` 进行临时模式切换。

示例：
- 您的默认 SOUL 是务实和直接的
- 然后对于一个会话您使用 `/personality teacher`
- 稍后您切换回来而不更改基础语音文件

## SOUL.md vs AGENTS.md

这是最常见的错误。

### 放在 SOUL.md 中
- "要直接。"
- "避免炒作语言。"
- "除非深度有帮助，否则更喜欢短答案。"
- "当用户错了时要反击。"

### 放在 AGENTS.md 中
- "使用 pytest，而不是 unittest。"
- "前端位于 `frontend/`。"
- "永远不要直接编辑迁移。"
- "API 在端口 8000 上运行。"

## 如何编辑它

```bash
nano ~/.kclaw/SOUL.md
```

或

```bash
vim ~/.kclaw/SOUL.md
```

然后重启 KClaw 或开始新会话。

## 一个实用工作流

1. 从生成的默认文件开始
2. 删除任何不像您想要的声音的内容
3. 添加 4-8 行明确定义语气和默认值
4. 与 KClaw 交谈一段时间
5. 根据仍然感觉不对的地方进行调整

这种迭代方法比试图一次设计完美人格效果更好。

## 故障排除

### 我编辑了 SOUL.md 但 KClaw 仍然听起来一样

检查：
- 您编辑了 `~/.kclaw/SOUL.md` 或 `$KCLAW_HOME/SOUL.md`
- 而不是某个仓库本地的 `SOUL.md`
- 文件不为空
- 编辑后会话已重启
- `/personality` 覆盖没有主导结果

### KClaw 忽略了我 SOUL.md 的某些部分

可能原因：
- 更高优先级的指令覆盖了它
- 文件包含冲突的指导
- 文件太长被截断了
- 某些文本类似于提示注入内容，可能被扫描器阻止或更改

### 我的 SOUL.md 变得过于特定于项目

将项目指令移入 `AGENTS.md`，保持 `SOUL.md` 专注于身份和风格。

## 相关文档

- [人格与 SOUL.md](/docs/user-guide/features/personality)
- [上下文文件](/docs/user-guide/features/context-files)
- [配置](/docs/user-guide/configuration)
- [提示与最佳实践](/docs/guides/tips)
