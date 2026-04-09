---
sidebar_position: 13
title: "委托和并行工作"
description: "何时以及如何使用子代理委托——并行研究、代码审查和多文件工作的模式"
---

# 委托和并行工作

KClaw 可以生成隔离的子代理来并行处理任务。每个子代理获得自己的对话、终端会话和工具集。只有最终摘要返回——中间工具调用永远不会进入您的上下文窗口。

有关完整功能参考，请参阅[子代理委托](/docs/user-guide/features/delegation)。

---

## 何时委托

**委托的好候选：**
- 重推理的子任务（调试、代码审查、研究综合）
- 会用中间数据淹没您上下文的任务
- 并行独立工作流（同时研究 A 和 B）
- 需要代理无偏见地处理的全新上下文任务

**使用其他方式：**
- 单个工具调用 → 直接使用工具
- 步骤之间有逻辑的机械多步工作 → `execute_code`
- 需要用户交互的任务 → 子代理不能使用 `clarify`
- 快速文件编辑 → 直接做

---

## 模式：并行研究

同时研究三个主题并获得结构化摘要：

```
并行研究这三个主题：
1. WebAssembly 在浏览器之外的现状
2. 2025 年 RISC-V 服务器芯片的采用情况
3. 实际量子计算应用

关注近期发展和关键参与者。
```

幕后，KClaw 使用：

```python
delegate_task(tasks=[
    {
        "goal": "研究 2025 年浏览器之外的 WebAssembly",
        "context": "关注：运行时（Wasmtime、Wasmer）、云/边缘用例、WASI 进展",
        "toolsets": ["web"]
    },
    {
        "goal": "研究 RISC-V 服务器芯片采用情况",
        "context": "关注：正在出货的服务器芯片、正在采用的云提供商、软件生态系统",
        "toolsets": ["web"]
    },
    {
        "goal": "研究实际量子计算应用",
        "context": "关注：纠错突破、实际用例、主要公司",
        "toolsets": ["web"]
    }
])
```

所有三个同时运行。每个子代理独立搜索网络并返回摘要。然后父代理将它们综合成一个连贯的简报。

---

## 模式：代码审查

将安全审查委托给全新上下文的子代理，它不带预判地处理代码：

```
审查 src/auth/ 中的身份验证模块的安全问题。
检查 SQL 注入、JWT 验证问题、密码处理和会话管理。修复您发现的任何问题并运行测试。
```

关键是 `context` 字段——它必须包含子代理需要的一切：

```python
delegate_task(
    goal="审查 src/auth/ 的安全问题并修复发现的任何问题",
    context="""项目位于 /home/user/webapp。Python 3.11、Flask、PyJWT、bcrypt。
    身份验证文件：src/auth/login.py、src/auth/jwt.py、src/auth/middleware.py
    测试命令：pytest tests/auth/ -v
    关注：SQL 注入、JWT 验证、密码哈希、会话管理。
    修复发现的问题并验证测试通过。""",
    toolsets=["terminal", "file"]
)
```

:::warning 上下文问题
子代理对您的对话**绝对一无所知**。它们完全重新开始。如果您委托"修复我们正在讨论的 bug"，子代理不知道您指的是哪个 bug。始终显式传递文件路径、错误消息、项目结构和约束。
:::

---

## 模式：比较替代方案

并行评估同一问题的多种方法，然后选择最佳：

```
我需要将全文搜索添加到我们的 Django 应用中。并行评估三种方法：
1. PostgreSQL tsvector（内置）
2. 通过 django-elasticsearch-dsl 的 Elasticsearch
3. 通过 meilisearch-python 的 Meilisearch

对于每个：设置复杂性、查询能力、资源需求和维护开销。比较它们并推荐一个。
```

每个子代理独立研究一个选项。因为它们是隔离的，所以没有交叉污染——每个评估都基于自身优点。父代理获得所有三个摘要并进行对比。

---

## 模式：多文件重构

将大型重构任务拆分到并行子代理中，每个处理代码库的不同部分：

```python
delegate_task(tasks=[
    {
        "goal": "重构所有 API 端点处理器以使用新的响应格式",
        "context": """项目位于 /home/user/api-server。
        文件：src/handlers/users.py、src/handlers/auth.py、src/handlers/billing.py
        旧格式：return {"data": result, "status": "ok"}
        新格式：return APIResponse(data=result, status=200).to_dict()
        导入：from src.responses import APIResponse
        之后运行测试：pytest tests/handlers/ -v""",
        "toolsets": ["terminal", "file"]
    },
    {
        "goal": "更新所有客户端 SDK 方法以处理新的响应格式",
        "context": """项目位于 /home/user/api-server。
        文件：sdk/python/client.py、sdk/python/models.py
        旧解析：result = response.json()["data"]
        新解析：result = response.json()["data"]（相同键，但添加状态码检查）
        同时更新 sdk/python/tests/test_client.py""",
        "toolsets": ["terminal", "file"]
    },
    {
        "goal": "更新 API 文档以反映新的响应格式",
        "context": """项目位于 /home/user/api-server。
        文档位于：docs/api/。格式：带代码示例的 Markdown。
        将所有响应示例从旧格式更新为新格式。
        在 docs/api/overview.md 添加"响应格式"部分解释架构。""",
        "toolsets": ["terminal", "file"]
    }
])
```

:::tip
每个子代理获得自己的终端会话。它们可以在同一项目目录上工作而不会相互干扰——只要它们编辑不同的文件。如果两个子代理可能触及同一文件，请在并行工作完成后自己处理该文件。
:::

---

## 模式：先收集再分析

对机械数据收集使用 `execute_code`，然后对重推理的分析使用委托：

```python
# 步骤 1：机械收集（execute_code 在这里更好——不需要推理）
execute_code("""
from kclaw_tools import web_search, web_extract

results = []
for query in ["AI funding Q1 2026", "AI startup acquisitions 2026", "AI IPOs 2026"]:
    r = web_search(query, limit=5)
    for item in r["data"]["web"]:
        results.append({"title": item["title"], "url": item["url"], "desc": item["description"]})

# 从前 5 个最相关的提取完整内容
urls = [r["url"] for r in results[:5]]
content = web_extract(urls)

# 保存以供分析步骤使用
import json
with open("/tmp/ai-funding-data.json", "w") as f:
    json.dump({"search_results": results, "extracted": content["results"]}, f)
print(f"Collected {len(results)} results, extracted {len(content['results'])} pages")
""")

# 步骤 2：重推理分析（委托在这里更好）
delegate_task(
    goal="分析 AI 资金数据并撰写市场报告",
    context="""原始数据位于 /tmp/ai-funding-data.json，包含关于 2026 年第一季度 AI 资金、
    收购和 IPO 的搜索结果和提取的网页。撰写结构化市场报告：关键交易、趋势、
    值得注意的参与者和展望。关注超过 1 亿美元的交易。""",
    toolsets=["terminal", "file"]
)
```

这通常是最有效的模式：`execute_code` 廉价处理 10+ 顺序工具调用，然后子代理用干净上下文做一个昂贵的推理任务。

---

## 工具集选择

根据子代理需要的内容选择工具集：

| 任务类型 | 工具集 | 为什么 |
|-----------|----------|-----|
| 网络研究 | `["web"]` | 仅 web_search + web_extract |
| 代码工作 | `["terminal", "file"]` | Shell 访问 + 文件操作 |
| 全栈 | `["terminal", "file", "web"]` | 除消息外的一切 |
| 只读分析 | `["file"]` | 只能读取文件，无 shell |

限制工具集保持子代理专注并防止意外副作用（如研究子代理运行 shell 命令）。

---

## 约束

- **最多 3 个并行任务** — 批次限制为 3 个并发子代理
- **无嵌套** — 子代理不能调用 `delegate_task`、`clarify`、`memory`、`send_message` 或 `execute_code`
- **独立终端** — 每个子代理获得自己的终端会话，有独立工作目录和状态
- **无对话历史** — 子代理只看到您放入 `goal` 和 `context` 的内容
- **默认 50 次迭代** — 对简单任务设置更低的 `max_iterations` 以节省成本

---

## 提示

**在目标中具体。** "修复 bug" 太模糊。"修复 api/handlers.py 第 47 行的 TypeError，其中 process_request() 从 parse_body() 接收到 None"给了子代理足够的工作内容。

**包含文件路径。** 子代理不知道您的项目结构。始终包含相关文件的绝对路径、项目根目录和测试命令。

**使用委托进行上下文隔离。** 有时您需要新的视角。委托强制您清晰地表达问题，子代理在没有对话中积累的假设的情况下处理它。

**检查结果。** 子代理摘要只是摘要——而已。如果子代理说"修复了 bug 且测试通过"，通过自己运行测试或读取 diff 来验证。

---

*有关完整委托参考——所有参数、ACP 集成和高级配置——请参阅[子代理委托](/docs/user-guide/features/delegation)。*
