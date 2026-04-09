---
sidebar_position: 7
title: "子代理委托"
description: "使用 delegate_task 生成具有隔离上下文的子代理进行并行工作流"
---

# 子代理委托

`delegate_task` 工具生成具有隔离上下文、受限工具集和自己的终端会话的子 AIAgent 实例。每个子代理获得一个新的对话并独立工作——只有其最终摘要进入父代理的上下文。

## 单个任务

```python
delegate_task(
    goal="调试为什么测试失败",
    context="错误：test_foo.py 第 42 行的断言",
    toolsets=["terminal", "file"]
)
```

## 并行批次

最多 3 个并发子代理：

```python
delegate_task(tasks=[
    {"goal": "研究主题 A", "toolsets": ["web"]},
    {"goal": "研究主题 B", "toolsets": ["web"]},
    {"goal": "修复构建", "toolsets": ["terminal", "file"]}
])
```

## 子代理上下文如何工作

:::warning 关键：子代理一无所知
子代理从一个**完全新鲜的对话**开始。它们对父代理的对话历史、之前的工具调用或委托前讨论的任何内容都没有了解。子代理的唯一上下文来自您提供的 `goal` 和 `context` 字段。
:::

这意味着您必须传递子代理需要的**一切**：

```python
# 错误 - 子代理不知道"错误"是什么
delegate_task(goal="修复错误")

# 正确 - 子代理有其需要的完整上下文
delegate_task(
    goal="修复 api/handlers.py 中的 TypeError",
    context="""文件 api/handlers.py 在第 47 行有 TypeError：
    'NoneType' object has no attribute 'get'。
    函数 process_request() 从 parse_body() 接收字典，
    但 parse_body() 在 Content-Type 缺失时返回 None。
    项目位于 /home/user/myproject，使用 Python 3.11。"""
)
```

子代理接收从您的 goal 和 context 构建的专注系统提示，指导它完成任务并提供其工作的结构化摘要，包括做了什么、发现了什么、修改了哪些文件以及遇到的任何问题。

## 实际示例

### 并行研究

同时研究多个主题并收集摘要：

```python
delegate_task(tasks=[
    {
        "goal": "研究 2025 年 WebAssembly 的现状",
        "context": "关注：浏览器支持、非浏览器运行时、语言支持",
        "toolsets": ["web"]
    },
    {
        "goal": "研究 2025 年 RISC-V 采用的现状",
        "context": "关注：服务器芯片、嵌入式系统、软件生态系统",
        "toolsets": ["web"]
    },
    {
        "goal": "研究 2025 年量子计算进展",
        "context": "关注：纠错突破、实际应用、主要参与者",
        "toolsets": ["web"]
    }
])
```

### 代码审查 + 修复

将审查和修复工作流委托给新的上下文：

```python
delegate_task(
    goal="审查身份验证模块的安全问题并修复发现的任何问题",
    context="""项目位于 /home/user/webapp。
    身份验证模块文件：src/auth/login.py、src/auth/jwt.py、src/auth/middleware.py。
    项目使用 Flask、PyJWT 和 bcrypt。
    关注：SQL 注入、JWT 验证、密码处理、会话管理。
    修复发现的任何问题并运行测试套件（pytest tests/auth/）。""",
    toolsets=["terminal", "file"]
)
```

### 多文件重构

将可能淹没父代理上下文的大型重构任务委托：

```python
delegate_task(
    goal="重构 src/ 中的所有 Python 文件，将 print() 替换为适当的日志",
    context="""项目位于 /home/user/myproject。
    使用 'logging' 模块和 logger = logging.getLogger(__name__)。
    替换 print() 调用为适当的日志级别：
    - print(f"Error: ...") -> logger.error(...)
    - print(f"Warning: ...") -> logger.warning(...)
    - print(f"Debug: ...") -> logger.debug(...)
    - 其他 print -> logger.info(...)
    不要更改测试文件或 CLI 输出中的 print()。
    之后运行 pytest 验证没有破坏任何东西。""",
    toolsets=["terminal", "file"]
)
```

## 批次模式详情

当您提供 `tasks` 数组时，子代理使用线程池**并行**运行：

- **最大并发：** 3 个任务（如果 `tasks` 数组更长，则截断为 3）
- **线程池：** 使用 `ThreadPoolExecutor` 和 `MAX_CONCURRENT_CHILDREN = 3` 个工作者
- **进度显示：** 在 CLI 模式下，树形视图实时显示每个子代理的工具调用，带有每任务完成行。在网关模式下，进度被批处理并传递到父代理的进度回调
- **结果排序：** 结果按任务索引排序以匹配输入顺序，无论完成顺序如何
- **中断传播：** 中断父代理（例如发送新消息）会中断所有活动子代理

单任务委托直接运行，没有线程池开销。

## 模型覆盖

您可以通过 `config.yaml` 为子代理配置不同的模型——对于将简单任务委托给更便宜/更快的模型很有用：

```yaml
# 在 ~/.kclaw/config.yaml 中
delegation:
  model: "google/gemini-flash-2.0"    # 子代理的更便宜模型
  provider: "openrouter"              # 可选：将子代理路由到不同的提供商
```

如果省略，子代理使用与父代理相同的模型。

## 工具集选择提示

`toolsets` 参数控制子代理可以访问哪些工具。根据任务选择：

| 工具集模式 | 使用场景 |
|----------------|----------|
| `["terminal", "file"]` | 代码工作、调试、文件编辑、构建 |
| `["web"]` | 研究、事实核查、文档查找 |
| `["terminal", "file", "web"]` | 全栈任务（默认） |
| `["file"]` | 只读分析，无需执行的代码审查 |
| `["terminal"]` | 系统管理、进程管理 |

某些工具集无论您指定什么，**始终被阻止**用于子代理：
- `delegation` — 无递归委托（防止无限生成）
- `clarify` — 子代理不能与用户交互
- `memory` — 不写入共享持久记忆
- `code_execution` — 子代理应该逐步推理
- `send_message` — 无跨平台副作用（例如发送 Telegram 消息）

## 最大迭代次数

每个子代理有迭代限制（默认：50），控制它可以进行多少次工具调用轮次：

```python
delegate_task(
    goal="快速文件检查",
    context="检查 /etc/nginx/nginx.conf 是否存在并打印前 10 行",
    max_iterations=10  # 简单任务，不需要很多轮次
)
```

## 深度限制

委托有**深度限制 2** — 父代理（深度 0）可以生成子代理（深度 1），但子代理不能再委托。这防止了失控的递归委托链。

## 关键属性

- 每个子代理获得自己的**终端会话**（与父代理分开）
- **无嵌套委托** — 子代理不能再委托（没有孙代理）
- 子代理**不能**调用：`delegate_task`、`clarify`、`memory`、`send_message`、`execute_code`
- **中断传播** — 中断父代理会中断所有活动子代理
- 只有最终摘要进入父代理的上下文，保持令牌使用高效
- 子代理继承父代理的**API 密钥、提供商配置和凭证池**（在速率限制时启用密钥轮换）

## 委托 vs execute_code

| 因素 | delegate_task | execute_code |
|--------|--------------|-------------|
| **推理** | 完整 LLM 推理循环 | 只是 Python 代码执行 |
| **上下文** | 新鲜隔离对话 | 无对话，只有脚本 |
| **工具访问** | 带推理的所有非阻止工具 | 通过 RPC 的 7 个工具，无推理 |
| **并行性** | 最多 3 个并发子代理 | 单个脚本 |
| **适用于** | 需要判断的复杂任务 | 机械多步数据处理 |
| **令牌成本** | 更高（完整 LLM 循环） | 更低（只返回 stdout） |
| **用户交互** | 无（子代理不能澄清） | 无 |

**经验法则：** 当子任务需要推理、判断或逐步问题解决时使用 `delegate_task`。当您需要机械数据处理或脚本化工作流时使用 `execute_code`。

## 配置

```yaml
# 在 ~/.kclaw/config.yaml 中
delegation:
  max_iterations: 50                        # 每个子代理的最大轮次（默认：50）
  default_toolsets: ["terminal", "file", "web"]  # 默认工具集
  model: "google/gemini-3-flash-preview"             # 可选的提供商/模型覆盖
  provider: "openrouter"                             # 可选的内置提供商

# 或使用直接自定义端点而不是提供商：
delegation:
  model: "qwen2.5-coder"
  base_url: "http://localhost:1234/v1"
  api_key: "local-key"
```

:::tip
代理根据任务复杂性自动处理委托。您不需要明确要求它委托——它在有意义时会这样做。
:::
