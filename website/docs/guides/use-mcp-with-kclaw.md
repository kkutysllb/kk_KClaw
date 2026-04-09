---
sidebar_position: 6
title: "在 KClaw 中使用 MCP"
description: "将 MCP 服务器连接到 KClaw Agent、过滤其工具并在真实工作流中安全使用的实用指南"
---

# 在 KClaw 中使用 MCP

本指南展示如何在日常工作流中实际使用 MCP 与 KClaw Agent。

如果功能页面解释了什么是 MCP，本指南是关于如何快速和安全地从中获取价值。

## 何时应该使用 MCP？

在以下情况下使用 MCP：
- 工具已以 MCP 形式存在，您不想构建原生 KClaw 工具
- 您希望 KClaw 通过干净的 RPC 层对本地或远程系统进行操作
- 您想要精细的每服务器暴露控制
- 您想将 KClaw 连接到内部 API、数据库或公司系统，而不修改 KClaw 核心

在以下情况下不要使用 MCP：
- 内置 KClaw 工具已经很好地解决了工作
- 服务器暴露了大量危险工具表面，而您没有准备过滤它
- 您只需要一个非常窄的集成，而原生工具会更简单和安全

## 心智模型

将 MCP 视为适配器层：

- KClaw 保持为代理
- MCP 服务器贡献工具
- KClaw 在启动或重新加载时发现这些工具
- 模型可以像普通工具一样使用它们
- 您控制每个服务器可见的程度

最后一点很重要。好的 MCP 使用不是"连接一切"。而是"连接正确的东西，用最小的有用表面"。

## 步骤 1：安装 MCP 支持

如果您使用标准安装脚本安装了 KClaw，MCP 支持已包含（安装程序运行 `uv pip install -e ".[all]"`）。

如果您没有额外组件安装并需要单独添加 MCP：

```bash
cd ~/.kclaw/kclaw
uv pip install -e ".[mcp]"
```

对于基于 npm 的服务器，确保 Node.js 和 `npx` 可用。

对于许多 Python MCP 服务器，`uvx` 是一个不错的默认。

## 步骤 2：首先添加一个服务器

从一个单一、安全的服务器开始。

示例：仅对一个项目目录的文件系统访问。

```yaml
mcp_servers:
  project_fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/my-project"]
```

然后启动 KClaw：

```bash
kclaw chat
```

现在问一些具体的：

```text
Inspect this project and summarize the repo layout.
```

## 步骤 3：验证 MCP 已加载

您可以通过几种方式验证 MCP：

- KClaw 横幅/状态应在配置时显示 MCP 集成
- 询问 KClaw 它有什么可用工具
- 在配置更改后使用 `/reload-mcp`
- 如果服务器连接失败，检查日志

一个实际测试提示：

```text
Tell me which MCP-backed tools are available right now.
```

## 步骤 4：立即开始过滤

如果服务器暴露了大量工具，不要等到以后。

### 示例：仅白名单您想要的内容

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, search_code]
```

这通常是对敏感系统的最佳默认。

### 示例：黑名单危险操作

```yaml
mcp_servers:
  stripe:
    url: "https://mcp.stripe.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      exclude: [delete_customer, refund_payment]
```

### 示例：也禁用实用程序包装器

```yaml
mcp_servers:
  docs:
    url: "https://mcp.docs.example.com"
    tools:
      prompts: false
      resources: false
```

## 过滤实际上影响什么？

KClaw 中有两类 MCP 暴露的功能：

1. 服务器原生 MCP 工具
- 用以下方式过滤：
  - `tools.include`
  - `tools.exclude`

2. KClaw 添加的实用程序包装器
- 用以下方式过滤：
  - `tools.resources`
  - `tools.prompts`

### 您可能看到的实用程序包装器

资源：
- `list_resources`
- `read_resource`

提示：
- `list_prompts`
- `get_prompt`

这些包装器仅在以下情况下出现：
- 您的配置允许它们，且
- MCP 服务器会话实际支持这些能力

所以如果服务器没有资源/提示，KClaw 不会假装它有。

## 常见模式

### 模式 1：本地项目助手

当您希望 KClaw 对有界工作区进行推理时，使用 MCP 进行仓库本地文件系统或 git 服务器。

```yaml
mcp_servers:
  fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/project"]

  git:
    command: "uvx"
    args: ["mcp-server-git", "--repository", "/home/user/project"]
```

好的提示：

```text
Review the project structure and identify where configuration lives.
```

```text
Check the local git state and summarize what changed recently.
```

### 模式 2：GitHub 分诊助手

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, update_issue, search_code]
      prompts: false
      resources: false
```

好的提示：

```text
List open issues about MCP, cluster them by theme, and draft a high-quality issue for the most common bug.
```

```text
Search the repo for uses of _discover_and_register_server and explain how MCP tools are registered.
```

### 模式 3：内部 API 助手

```yaml
mcp_servers:
  internal_api:
    url: "https://mcp.internal.example.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      include: [list_customers, get_customer, list_invoices]
      resources: false
      prompts: false
```

好的提示：

```text
Look up customer ACME Corp and summarize recent invoice activity.
```

这是严格白名单远优于排除列表的地方。

### 模式 4：文档/知识服务器

一些 MCP 服务器暴露的提示或资源更像共享知识资产而不是直接操作。

```yaml
mcp_servers:
  docs:
    url: "https://mcp.docs.example.com"
    tools:
      prompts: true
      resources: true
```

好的提示：

```text
List available MCP resources from the docs server, then read the onboarding guide and summarize it.
```

```text
List prompts exposed by the docs server and tell me which ones would help with incident response.
```

## 教程：带过滤的端到端设置

这是一个实用的进展。

### 阶段 1：添加带严格白名单的 GitHub MCP

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, search_code]
      prompts: false
      resources: false
```

启动 KClaw 并问：

```text
Search the codebase for references to MCP and summarize the main integration points.
```

### 阶段 2：仅在需要时扩展

如果以后也需要问题更新：

```yaml
tools:
  include: [list_issues, create_issue, update_issue, search_code]
```

然后重新加载：

```text
/reload-mcp
```

### 阶段 3：添加具有不同策略的第二个服务器

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, update_issue, search_code]
      prompts: false
      resources: false

  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/project"]
```

现在 KClaw 可以组合它们：

```text
Inspect the local project files, then create a GitHub issue summarizing the bug you find.
```

这就是 MCP 变得强大的地方：多系统工作流，而不改变 KClaw 核心。

## 安全使用建议

### 对于危险系统首选允许列表

对于任何财务、客户面向或破坏性的：
- 使用 `tools.include`
- 从尽可能小的集合开始

### 禁用未使用的实用程序

如果您不希望模型浏览服务器提供的资源/提示，请关闭它们：

```yaml
tools:
  resources: false
  prompts: false
```

### 保持服务器范围狭窄

示例：
- 文件系统服务器根目录到一个项目目录，而不是您的整个主目录
- git 服务器指向一个仓库
- 内部 API 服务器默认情况下暴露读重工具

### 配置更改后重新加载

```text
/reload-mcp
```

在更改以下内容后执行此操作：
- include/exclude 列表
- 启用标志
- resources/prompts 切换
- auth 头/环境

## 按症状故障排除

### "服务器连接但我期望的工具缺失"

可能原因：
- 被 `tools.include` 过滤
- 被 `tools.exclude` 排除
- 实用程序包装器通过 `resources: false` 或 `prompts: false` 禁用
- 服务器实际上不支持资源/提示

### "服务器已配置但什么都没加载"

检查：
- `enabled: false` 未留在配置中
- 命令/运行时存在（`npx`、`uvx` 等）
- HTTP 端点可访问
- auth 环境或头正确

### "为什么我看到的工具比 MCP 服务器宣传的少？"

因为 KClaw 现在尊重您的每服务器策略和能力感知注册。这是预期的，通常是理想的。

### "如何在不删除配置的情况下移除 MCP 服务器？"

使用：

```yaml
enabled: false
```

这将配置保留在周围，但阻止连接和注册。

## 推荐的首个 MCP 设置

对大多数用户来说好的首个服务器：
- 文件系统
- git
- GitHub
- fetch / 文档 MCP 服务器
- 一个窄的内部 API

不太好首发服务器：
- 具有大量破坏性操作且没有过滤的大型业务系统
- 您不够了解而无法约束的任何东西

## 相关文档

- [MCP（模型上下文协议）](/docs/user-guide/features/mcp)
- [常见问题](/docs/reference/faq)
- [斜杠命令](/docs/reference/slash-commands)
