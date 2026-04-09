---
sidebar_position: 6
title: "事件钩子"
description: "在关键生命周期点运行自定义代码——记录活动、发送警报、发布到 webhook"
---

# 事件钩子

KClaw 有两个钩子系统，在关键生命周期点运行自定义代码：

| 系统 | 通过以下方式注册 | 运行在 | 使用场景 |
|--------|---------------|---------|----------|
| **[网关钩子](#gateway-event-hooks)** | `~/.kclaw/hooks/` 中的 `HOOK.yaml` + `handler.py` | 仅网关 | 日志、警报、webhook |
| **[插件钩子](#plugin-hooks)** | [插件](/docs/user-guide/features/plugins)中的 `ctx.register_hook()` | CLI + 网关 | 工具拦截、指标、护栏 |

两个系统都是非阻塞的——任何钩子中的错误都被捕获并记录，永不让代理崩溃。

## 网关事件钩子

网关钩子在网关运行期间（Telegram、Discord、Slack、WhatsApp）自动触发，不会阻塞主代理管道。

### 创建钩子

每个钩子是 `~/.kclaw/hooks/` 下一个包含两个文件的目录：

```text
~/.kclaw/hooks/
└── my-hook/
    ├── HOOK.yaml      # 声明监听哪些事件
    └── handler.py     # Python 处理程序函数
```

#### HOOK.yaml

```yaml
name: my-hook
description: Log all agent activity to a file
events:
  - agent:start
  - agent:end
  - agent:step
```

`events` 列表确定哪些事件触发您的处理程序。您可以订阅任何事件组合，包括通配符如 `command:*`。

#### handler.py

```python
import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path.home() / ".kclaw" / "hooks" / "my-hook" / "activity.log"

async def handle(event_type: str, context: dict):
    """Called for each subscribed event. Must be named 'handle'."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event": event_type,
        **context,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
```

**处理程序规则：**
- 必须命名为 `handle`
- 接收 `event_type`（字符串）和 `context`（字典）
- 可以是 `async def` 或常规 `def`——两者都可以
- 错误被捕获并记录，永不让代理崩溃

### 可用事件

| 事件 | 触发时机 | 上下文键 |
|-------|---------------|--------------|
| `gateway:startup` | 网关进程启动 | `platforms`（活动平台名称列表） |
| `session:start` | 创建新消息会话 | `platform`, `user_id`, `session_id`, `session_key` |
| `session:end` | 会话结束（重置前） | `platform`, `user_id`, `session_key` |
| `session:reset` | 用户运行 `/new` 或 `/reset` | `platform`, `user_id`, `session_key` |
| `agent:start` | 代理开始处理消息 | `platform`, `user_id`, `session_id`, `message` |
| `agent:step` | 工具调用循环的每次迭代 | `platform`, `user_id`, `session_id`, `iteration`, `tool_names` |
| `agent:end` | 代理完成处理 | `platform`, `user_id`, `session_id`, `message`, `response` |
| `command:*` | 执行的任何斜杠命令 | `platform`, `user_id`, `command`, `args` |

#### 通配符匹配

注册为 `command:*` 的处理程序为任何 `command:` 事件触发（`command:model`、`command:reset` 等）。使用单个订阅监控所有斜杠命令。

### 示例

#### 启动检查列表（BOOT.md）— 内置

网关附带内置的 `boot-md` 钩子，每次启动时查找 `~/.kclaw/BOOT.md`。如果文件存在，代理在后台会话中运行其指令。无需安装——只需创建文件。

**创建 `~/.kclaw/BOOT.md`：**

```markdown
# Startup Checklist

1. Check if any cron jobs failed overnight — run `kclaw cron list`
2. Send a message to Discord #general saying "Gateway restarted, all systems go"
3. Check if /opt/app/deploy.log has any errors from the last 24 hours
```

代理在后台线程中运行这些指令，因此不会阻塞网关启动。如果不需要注意，代理回复 `[SILENT]` 且不发送消息。

:::tip
没有 BOOT.md？钩子静默跳过——零开销。当您需要启动自动化时创建文件，不需要时删除。
:::

#### 长时间任务的 Telegram 警报

当代理花费超过 10 步时向您发送消息：

```yaml
# ~/.kclaw/hooks/long-task-alert/HOOK.yaml
name: long-task-alert
description: Alert when agent is taking many steps
events:
  - agent:step
```

```python
# ~/.kclaw/hooks/long-task-alert/handler.py
import os
import httpx

THRESHOLD = 10
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_HOME_CHANNEL")

async def handle(event_type: str, context: dict):
    iteration = context.get("iteration", 0)
    if iteration == THRESHOLD and BOT_TOKEN and CHAT_ID:
        tools = ", ".join(context.get("tool_names", []))
        text = f"⚠️ Agent has been running for {iteration} steps. Last tools: {tools}"
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text},
            )
```

#### 命令使用记录器

跟踪使用了哪些斜杠命令：

```yaml
# ~/.kclaw/hooks/command-logger/HOOK.yaml
name: command-logger
description: Log slash command usage
events:
  - command:*
```

```python
# ~/.kclaw/hooks/command-logger/handler.py
import json
from datetime import datetime
from pathlib import Path

LOG = Path.home() / ".kclaw" / "logs" / "command_usage.jsonl"

def handle(event_type: str, context: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "command": context.get("command"),
        "args": context.get("args"),
        "platform": context.get("platform"),
        "user": context.get("user_id"),
    }
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
```

#### 会话启动 Webhook

在新会话时 POST 到外部服务：

```yaml
# ~/.kclaw/hooks/session-webhook/HOOK.yaml
name: session-webhook
description: Notify external service on new sessions
events:
  - session:start
  - session:reset
```

```python
# ~/.kclaw/hooks/session-webhook/handler.py
import httpx

WEBHOOK_URL = "https://your-service.example.com/kclaw-events"

async def handle(event_type: str, context: dict):
    async with httpx.AsyncClient() as client:
        await client.post(WEBHOOK_URL, json={
            "event": event_type,
            **context,
        }, timeout=5)
```

### 工作原理

1. 在网关启动时，`HookRegistry.discover_and_load()` 扫描 `~/.kclaw/hooks/`
2. 每个带有 `HOOK.yaml` + `handler.py` 的子目录被动态加载
3. 处理程序为其声明的事件注册
4. 在每个生命周期点，`hooks.emit()` 触发所有匹配的处理程序
5. 任何处理程序中的错误被捕获并记录——损坏的钩子永不让代理崩溃

:::info
网关钩子仅在**网关**中触发（Telegram、Discord、Slack、WhatsApp）。CLI 不加载网关钩子。对于处处工作的钩子，请使用[插件钩子](#plugin-hooks)。
:::

## 插件钩子

[插件](/docs/user-guide/features/plugins)可以注册在 **CLI 和网关**会话中都触发的钩子。这些通过插件的 `register()` 函数中的 `ctx.register_hook()` 以编程方式注册。

```python
def register(ctx):
    ctx.register_hook("pre_tool_call", my_tool_observer)
    ctx.register_hook("post_tool_call", my_tool_logger)
    ctx.register_hook("pre_llm_call", my_memory_callback)
    ctx.register_hook("post_llm_call", my_sync_callback)
    ctx.register_hook("on_session_start", my_init_callback)
    ctx.register_hook("on_session_end", my_cleanup_callback)
```

**所有钩子的一般规则：**

- 回调接收**关键字参数**。始终接受 `**kwargs` 以实现向前兼容——新参数可能在未来版本中添加而不会破坏您的插件。
- 如果回调**崩溃**，它被记录并跳过。其他钩子和代理继续正常运行。行为不端的插件永不能让代理崩溃。
- 所有钩子都是**即发即忘的观察者**，其返回值被忽略——除了 `pre_llm_call`，它可以[注入上下文](#pre_llm_call)。

### 快速参考

| 钩子 | 触发时机 | 返回 |
|------|-----------|---------|
| [`pre_tool_call`](#pre_tool_call) | 任何工具执行前 | ignored |
| [`post_tool_call`](#post_tool_call) | 任何工具返回后 | ignored |
| [`pre_llm_call`](#pre_llm_call) | 每轮一次，工具调用循环前 | context injection |
| [`post_llm_call`](#post_llm_call) | 每轮一次，工具调用循环后 | ignored |
| [`on_session_start`](#on_session_start) | 创建新会话时（仅第一轮） | ignored |
| [`on_session_end`](#on_session_end) | 会话结束 | ignored |

---

### `pre_tool_call`

在每个工具执行**立即之前**触发——无论是内置工具还是插件工具。

**回调签名：**

```python
def my_callback(tool_name: str, args: dict, task_id: str, **kwargs):
```

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `tool_name` | `str` | 即将执行的工具名称（例如 `"terminal"`、`"web_search"`、`"read_file"`） |
| `args` | `dict` | 模型传递给工具的参数 |
| `task_id` | `str` | 会话/任务标识符。如果未设置则为空字符串。 |

**触发：** 在 `model_tools.py` 中，`handle_function_call()` 内部，在工具的处理程序运行之前。每个工具调用触发一次——如果模型并行调用 3 个工具，这触发 3 次。

**返回值：** 忽略。

**使用场景：** 日志、审计跟踪、工具调用计数、阻止危险操作（打印警告）、速率限制。

**示例 — 工具调用审计日志：**

```python
import json, logging
from datetime import datetime

logger = logging.getLogger(__name__)

def audit_tool_call(tool_name, args, task_id, **kwargs):
    logger.info("TOOL_CALL session=%s tool=%s args=%s",
                task_id, tool_name, json.dumps(args)[:200])

def register(ctx):
    ctx.register_hook("pre_tool_call", audit_tool_call)
```

**示例 — 危险工具警告：**

```python
DANGEROUS = {"terminal", "write_file", "patch"}

def warn_dangerous(tool_name, **kwargs):
    if tool_name in DANGEROUS:
        print(f"⚠ Executing potentially dangerous tool: {tool_name}")

def register(ctx):
    ctx.register_hook("pre_tool_call", warn_dangerous)
```

---

### `post_tool_call`

在每个工具执行返回**立即之后**触发。

**回调签名：**

```python
def my_callback(tool_name: str, args: dict, result: str, task_id: str, **kwargs):
```

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `tool_name` | `str` | 刚执行的工具名称 |
| `args` | `dict` | 模型传递给工具的参数 |
| `result` | `str` | 工具的返回值（始终是 JSON 字符串） |
| `task_id` | `str` | 会话/任务标识符。如果未设置则为空字符串。 |

**触发：** 在 `model_tools.py` 中，`handle_function_call()` 内部，在工具的处理程序返回之后。每个工具调用触发一次。如果工具抛出未处理的异常**不**触发（错误被捕获并作为错误 JSON 字符串返回，`post_tool_call` 使用该错误字符串作为 `result` 触发）。

**返回值：** 忽略。

**使用场景：** 记录工具结果、指标收集、跟踪工具成功率、在特定工具完成时发送通知。

**示例 — 跟踪工具使用指标：**

```python
from collections import Counter
import json

_tool_counts = Counter()
_error_counts = Counter()

def track_metrics(tool_name, result, **kwargs):
    _tool_counts[tool_name] += 1
    try:
        parsed = json.loads(result)
        if "error" in parsed:
            _error_counts[tool_name] += 1
    except (json.JSONDecodeError, TypeError):
        pass

def register(ctx):
    ctx.register_hook("post_tool_call", track_metrics)
```

---

### `pre_llm_call`

在工具调用循环开始前**每轮触发一次**。这是**唯一使用返回值的钩子**——它可以向当前轮次的用户消息注入上下文。

**回调签名：**

```python
def my_callback(session_id: str, user_message: str, conversation_history: list,
                is_first_turn: bool, model: str, platform: str, **kwargs):
```

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `session_id` | `str` | 当前会话的唯一标识符 |
| `user_message` | `str` | 用户此轮次的原始消息（在任何技能注入之前） |
| `conversation_history` | `list` | 完整消息列表的副本（OpenAI 格式：`[{"role": "user", "content": "..."}]`） |
| `is_first_turn` | `bool` | 如果这是新会话的第一轮则为 `True`，后续轮次为 `False` |
| `model` | `str` | 模型标识符（例如 `"anthropic/claude-sonnet-4.6"`） |
| `platform` | `str` | 会话运行在哪里：`"cli"`、`"telegram"`、`"discord"` 等 |

**触发：** 在 `run_agent.py` 中，`run_conversation()` 内部，在上下文压缩之后但在主 `while` 循环之前。每 `run_conversation()` 调用触发一次（即每用户轮次一次），而不是在工具循环内的每次 API 调用触发一次。

**返回值：** 如果回调返回带有 `"context"` 键的字典，或纯非空字符串，文本被附加到当前轮次的用户消息。返回 `None` 则不注入。

```python
# 注入上下文
return {"context": "Recalled memories:\n- User likes Python\n- Working on kclaw"}

# 纯字符串（等效）
return "Recalled memories:\n- User likes Python"

# 不注入
return None
```

**注入到哪里：** 始终是**用户消息**，而不是系统提示。这保留了提示缓存——系统提示在轮次之间保持相同，因此缓存的令牌被重用。系统提示是 KClaw 的领地（模型指导、工具强制、人格、技能）。插件与用户的输入一起提供上下文。

所有注入的上下文都是**短暂的**——仅在 API 调用时添加。对话历史中的原始用户消息从未被改变，没有任何内容持久化到会话数据库。

当**多个插件**返回上下文时，它们的输出以双换行符连接，按插件发现顺序（按目录名称字母顺序）。

**使用场景：** 记忆召回、RAG 上下文注入、护栏、每轮分析。

**示例 — 记忆召回：**

```python
import httpx

MEMORY_API = "https://your-memory-api.example.com"

def recall(session_id, user_message, is_first_turn, **kwargs):
    try:
        resp = httpx.post(f"{MEMORY_API}/recall", json={
            "session_id": session_id,
            "query": user_message,
        }, timeout=3)
        memories = resp.json().get("results", [])
        if not memories:
            return None
        text = "Recalled context:\n" + "\n".join(f"- {m['text']}" for m in memories)
        return {"context": text}
    except Exception:
        return None

def register(ctx):
    ctx.register_hook("pre_llm_call", recall)
```

**示例 — 护栏：**

```python
POLICY = "Never execute commands that delete files without explicit user confirmation."

def guardrails(**kwargs):
    return {"context": POLICY}

def register(ctx):
    ctx.register_hook("pre_llm_call", guardrails)
```

---

### `post_llm_call`

在工具调用循环完成且代理产生最终响应后**每轮触发一次**。仅在**成功**的轮次触发——如果轮次被中断则不触发。

**回调签名：**

```python
def my_callback(session_id: str, user_message: str, assistant_response: str,
                conversation_history: list, model: str, platform: str, **kwargs):
```

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `session_id` | `str` | 当前会话的唯一标识符 |
| `user_message` | `str` | 用户此轮次的原始消息 |
| `assistant_response` | `str` | 代理此轮次的最终文本响应 |
| `conversation_history` | `list` | 轮次完成后完整消息列表的副本 |
| `model` | `str` | 模型标识符 |
| `platform` | `str` | 会话运行在哪里 |

**触发：** 在 `run_agent.py` 中，`run_conversation()` 内部，在工具循环以最终响应退出后。受 `if final_response and not interrupted` 保护——因此当用户在轮次中间中断或代理达到迭代限制而没有产生响应时**不**触发。

**返回值：** 忽略。

**使用场景：** 将对话数据同步到外部记忆系统、计算响应质量指标、记录轮次摘要、触发后续操作。

**示例 — 同步到外部记忆：**

```python
import httpx

MEMORY_API = "https://your-memory-api.example.com"

def sync_memory(session_id, user_message, assistant_response, **kwargs):
    try:
        httpx.post(f"{MEMORY_API}/store", json={
            "session_id": session_id,
            "user": user_message,
            "assistant": assistant_response,
        }, timeout=5)
    except Exception:
        pass  # best-effort

def register(ctx):
    ctx.register_hook("post_llm_call", sync_memory)
```

**示例 — 跟踪响应长度：**

```python
import logging
logger = logging.getLogger(__name__)

def log_response_length(session_id, assistant_response, model, **kwargs):
    logger.info("RESPONSE session=%s model=%s chars=%d",
                session_id, model, len(assistant_response or ""))

def register(ctx):
    ctx.register_hook("post_llm_call", log_response_length)
```

---

### `on_session_start`

在全新会话创建时触发**一次**。在会话继续时**不**触发（当用户在现有会话中发送第二条消息时）。

**回调签名：**

```python
def my_callback(session_id: str, model: str, platform: str, **kwargs):
```

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `session_id` | `str` | 新会话的唯一标识符 |
| `model` | `str` | 模型标识符 |
| `platform` | `str` | 会话运行在哪里 |

**触发：** 在 `run_agent.py` 中，`run_conversation()` 内部，在新会话的第一轮——具体是在系统提示构建之后、工具循环开始之前。检查是 `if not conversation_history`（没有先前消息 = 新会话）。

**返回值：** 忽略。

**使用场景：** 初始化会话作用域状态、预热缓存、向外部服务注册会话、记录会话开始。

**示例 — 初始化会话缓存：**

```python
_session_caches = {}

def init_session(session_id, model, platform, **kwargs):
    _session_caches[session_id] = {
        "model": model,
        "platform": platform,
        "tool_calls": 0,
        "started": __import__("datetime").datetime.now().isoformat(),
    }

def register(ctx):
    ctx.register_hook("on_session_start", init_session)
```

---

### `on_session_end`

在每个 `run_conversation()` 调用**非常结束时**触发，无论结果如何。如果代理在用户退出时正处于轮次中间，也从 CLI 的退出处理程序触发。

**回调签名：**

```python
def my_callback(session_id: str, completed: bool, interrupted: bool,
                model: str, platform: str, **kwargs):
```

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `session_id` | `str` | 会话的唯一标识符 |
| `completed` | `bool` | 如果代理产生了最终响应则为 `True`，否则为 `False` |
| `interrupted` | `bool` | 如果轮次被中断（用户发送了新消息、`/stop` 或退出）则为 `True` |
| `model` | `str` | 模型标识符 |
| `platform` | `str` | 会话运行在哪里 |

**触发：** 在两个地方：
1. **`run_agent.py`** — 在每个 `run_conversation()` 调用结束时，在所有清理之后。始终触发，即使轮次出错。
2. **`cli.py`** — 在 CLI 的 atexit 处理程序中，但**仅当**代理在退出时正处于轮次中间（`_agent_running=True`）时。在这种情况下，`completed=False` 且 `interrupted=True`。

**返回值：** 忽略。

**使用场景：** 刷新缓冲区、关闭连接、持久化会话状态、记录会话持续时间、清理在 `on_session_start` 中初始化的资源。

**示例 — 刷新和清理：**

```python
_session_caches = {}

def cleanup_session(session_id, completed, interrupted, **kwargs):
    cache = _session_caches.pop(session_id, None)
    if cache:
        # Flush accumulated data to disk or external service
        status = "completed" if completed else ("interrupted" if interrupted else "failed")
        print(f"Session {session_id} ended: {status}, {cache['tool_calls']} tool calls")

def register(ctx):
    ctx.register_hook("on_session_end", cleanup_session)
```

**示例 — 会话持续时间跟踪：**

```python
import time, logging
logger = logging.getLogger(__name__)

_start_times = {}

def on_start(session_id, **kwargs):
    _start_times[session_id] = time.time()

def on_end(session_id, completed, interrupted, **kwargs):
    start = _start_times.pop(session_id, None)
    if start:
        duration = time.time() - start
        logger.info("SESSION_DURATION session=%s seconds=%.1f completed=%s interrupted=%s",
                     session_id, duration, completed, interrupted)

def register(ctx):
    ctx.register_hook("on_session_start", on_start)
    ctx.register_hook("on_session_end", on_end)
```

---

请参阅**[构建插件指南](/docs/guides/build-a-kclaw-plugin)**获取完整演练，包括工具模式、处理程序和高级钩子模式。
