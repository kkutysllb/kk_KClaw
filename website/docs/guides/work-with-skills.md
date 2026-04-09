---
sidebar_position: 12
title: "使用技能"
description: "查找、安装、使用和创建技能——按需知识，教 KClaw 新工作流程"
---

# 使用技能

技能是按需知识文档，教 KClaw 如何处理特定任务——从生成 ASCII 艺术到管理 GitHub PR。本指南引导您日常使用它们。

有关完整技术参考，请参阅[技能系统](/docs/user-guide/features/skills)。

---

## 查找技能

每个 KClaw 安装都附带捆绑技能。查看可用内容：

```bash
# 在任何聊天会话中：
/skills

# 或从 CLI：
kclaw skills list
```

这显示带有名称和描述的紧凑列表：

```
ascii-art         使用 pyfiglet、cowsay、boxes... 生成 ASCII 艺术
arxiv             从 arXiv 搜索和检索学术论文...
github-pr-workflow 完整 PR 生命周期——创建分支、提交...
plan              计划模式——检查上下文，编写 markdown...
excalidraw        使用 Excalidraw 创建手绘风格图表...
```

### 搜索技能

```bash
# 按关键字搜索
/skills search docker
/skills search music
```

### 技能中心

官方可选技能（默认不活跃的较重或专业技能）可通过中心获得：

```bash
# 浏览官方可选技能
/skills browse

# 搜索中心
/skills search blockchain
```

---

## 使用技能

每个已安装的技能都会自动成为斜杠命令。只需输入它的名称：

```bash
# 加载技能并给它一个任务
/ascii-art 制作一个说 "HELLO WORLD" 的横幅
/plan 为待办事项应用设计 REST API
/github-pr-workflow 为身份验证重构创建 PR

# 只需技能名称（无任务）加载它并让您描述您需要的内容
/excalidraw
```

您也可以通过自然对话触发技能——要求 KClaw 使用特定技能，它会通过 `skill_view` 工具加载它。

### 渐进式披露

技能使用令牌高效加载模式。代理不会一次性加载所有内容：

1. **`skills_list()`** — 所有技能的紧凑列表（~3k 令牌）。在会话开始时加载。
2. **`skill_view(name)`** — 一个技能的完整 SKILL.md 内容。当代理决定需要该技能时加载。
3. **`skill_view(name, file_path)`** — 技能内的特定参考文件。仅在需要时加载。

这意味着技能在实际使用之前不会消耗令牌。

---

## 从中心安装

官方可选技能随 KClaw 附带，但默认不活跃。显式安装：

```bash
# 安装官方可选技能
kclaw skills install official/research/arxiv

# 在聊天会话中从中心安装
/skills install official/creative/songwriting-and-ai-music
```

发生的事情：
1. 技能目录复制到 `~/.kclaw/skills/`
2. 它出现在您的 `skills_list` 输出中
3. 它作为斜杠命令可用

:::tip
已安装的技能在新会话中生效。如果您在当前会话中想要它可用，使用 `/reset` 开始新的，或添加 `--now` 立即使提示缓存失效（下次turn成本更高）。
:::

### 验证安装

```bash
# 检查它在那里
kclaw skills list | grep arxiv

# 或在聊天中
/skills search arxiv
```

---

## 配置技能设置

一些技能在其 frontmatter 中声明它们需要的配置：

```yaml
metadata:
  kclaw:
    config:
      - key: tenor.api_key
        description: "Tenor API 密钥，用于 GIF 搜索"
        prompt: "输入您的 Tenor API 密钥"
        url: "https://developers.google.com/tenor/guides/quickstart"
```

当首次加载带有配置的技能时，KClaw 提示您输入值。它们存储在 `config.yaml` 下的 `skills.config.*` 中。

从 CLI 管理技能配置：

```bash
# 交互式配置特定技能
kclaw skills config gif-search

# 查看所有技能配置
kclaw config get skills.config
```

---

## 创建您自己的技能

技能只是带有 YAML frontmatter 的 markdown 文件。创建一个需要不到五分钟。

### 1. 创建目录

```bash
mkdir -p ~/.kclaw/skills/my-category/my-skill
```

### 2. 编写 SKILL.md

```markdown title="~/.kclaw/skills/my-category/my-skill/SKILL.md"
---
name: my-skill
description: 简短描述此技能的用途
version: 1.0.0
metadata:
  kclaw:
    tags: [my-tag, automation]
    category: my-category
---

# 我的技能

## 何时使用
当用户询问[特定主题]或需要[特定任务]时使用此技能。

## 程序
1. 首先，检查[先决条件]是否可用
2. 运行 `command --with-flags`
3. 解析输出并呈现结果

## 陷阱
- 常见失败：[描述]。修复：[解决方案]
- 注意[边缘情况]

## 验证
运行 `check-command` 确认结果正确。
```

### 3. 添加参考文件（可选）

技能可以包括代理按需加载的支持文件：

```
my-skill/
├── SKILL.md                    # 主技能文档
├── references/
│   ├── api-docs.md             # 代理可以查阅的 API 参考
│   └── examples.md             # 示例输入/输出
├── templates/
│   └── config.yaml             # 代理可以使用的模板文件
└── scripts/
    └── setup.sh                # 代理可以执行的脚本
```

在您的 SKILL.md 中引用这些：

```markdown
有关 API 详细信息，加载参考：`skill_view("my-skill", "references/api-docs.md")`
```

### 4. 测试它

开始新会话并尝试您的技能：

```bash
kclaw chat -q "/my-skill help me with the thing"
```

技能自动出现——无需注册。将其放入 `~/.kclaw/skills/` 即可生效。

:::info
代理也可以使用 `skill_manage` 创建和更新技能。在解决复杂问题之后，KClaw 可能提供将方法保存为技能以便下次使用。
:::

---

## 每个平台的技能管理

控制哪些技能在哪些平台上可用：

```bash
kclaw skills
```

这会打开一个交互式 TUI，您可以在其中为每个平台（CLI、Telegram、Discord 等）启用或禁用技能。当您希望某些技能仅在特定上下文中可用时，这很有用——例如，将开发技能保持在 Telegram 之外。

---

## 技能与记忆

两者都在会话之间持久化，但它们服务不同目的：

| | 技能 | 记忆 |
|---|---|---|
| **内容** | 程序性知识——如何做事情 | 事实性知识——事物是什么 |
| **何时** | 按需加载，仅在相关时 | 自动注入每个会话 |
| **大小** | 可以很大（数百行） | 应该紧凑（仅关键事实） |
| **成本** | 加载前零令牌 | 小但恒定的令牌成本 |
| **示例** | "如何部署到 Kubernetes" | "用户偏好深色模式，位于 PST" |
| **创建者** | 您、代理或从中心安装 | 基于对话的代理 |

**经验法则：** 如果您会把它放在参考文档中，那就是技能。如果您会把它放在便签上，那就是记忆。

---

## 提示

**保持技能专注。** 试图涵盖"所有 DevOps"的技能会太长太模糊。涵盖"将 Python 应用部署到 Fly.io"的技能足够具体，是真正有用的。

**让代理创建技能。** 在复杂的多步骤任务之后，KClaw 通常会提供将方法保存为技能。说 yes——这些代理编写的技能捕获了包括沿途发现的陷阱的确切工作流程。

**使用分类。** 将技能组织到子目录中（`~/.kclaw/skills/devops/`、`~/.kclaw/skills/research/` 等）。这保持列表可管理，并帮助代理更快找到相关技能。

**技能过时时要更新。** 如果您使用技能但遇到未涵盖的问题，告诉 KClaw 用您学到的内容更新技能。不维护的技能会成为负担。

---

*有关完整的技能参考——frontmatter 字段、条件激活、外部目录等——请参阅[技能系统](/docs/user-guide/features/skills)。*
