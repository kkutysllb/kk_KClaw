---
sidebar_position: 9
sidebar_label: "构建插件"
title: "构建 KClaw 插件"
description: "从头开始构建完整 KClaw 插件的分步指南——包含工具、钩子、数据文件和技能"
---

# 构建 KClaw 插件

本指南从头开始构建一个完整的 KClaw 插件。最后您将拥有一个包含多个工具、生命周期钩子、附带数据文件和捆绑技能的可用插件——插件系统支持的一切。

## 您正在构建什么

一个**计算器**插件，包含两个工具：
- `calculate` — 计算数学表达式（`2**16`、`sqrt(144)`、`pi * 5**2`）
- `unit_convert` — 单位转换（`100 F → 37.78 C`、`5 km → 3.11 mi`）

加上一个记录每个工具调用的钩子，以及一个捆绑的技能文件。

## 步骤 1：创建插件目录

```bash
mkdir -p ~/.kclaw/plugins/calculator
cd ~/.kclaw/plugins/calculator
```

## 步骤 2：编写清单

创建 `plugin.yaml`：

```yaml
name: calculator
version: 1.0.0
description: Math calculator — evaluate expressions and convert units
provides_tools:
  - calculate
  - unit_convert
provides_hooks:
  - post_tool_call
```

这告诉 KClaw："我是一个名为 calculator 的插件，我提供工具和钩子。"`provides_tools` 和 `provides_hooks` 字段是插件注册的内容列表。

可以添加的可选字段：
```yaml
author: Your Name
requires_env:          # gate loading on env vars; prompted during install
  - SOME_API_KEY       # simple format — plugin disabled if missing
  - name: OTHER_KEY    # rich format — shows description/url during install
    description: "Key for the Other service"
    url: "https://other.com/keys"
    secret: true
```

## 步骤 3：编写工具模式

创建 `schemas.py` — 这是 LLM 读取以决定何时调用您的工具的内容：

```python
"""Tool schemas — what the LLM sees."""

CALCULATE = {
    "name": "calculate",
    "description": (
        "Evaluate a mathematical expression and return the result. "
        "Supports arithmetic (+, -, *, /, **), functions (sqrt, sin, cos, "
        "log, abs, round, floor, ceil), and constants (pi, e). "
        "Use this for any math the user asks about."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate (e.g., '2**10', 'sqrt(144)')",
            },
        },
        "required": ["expression"],
    },
}

UNIT_CONVERT = {
    "name": "unit_convert",
    "description": (
        "Convert a value between units. Supports length (m, km, mi, ft, in), "
        "weight (kg, lb, oz, g), temperature (C, F, K), data (B, KB, MB, GB, TB), "
        "and time (s, min, hr, day)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "value": {
                "type": "number",
                "description": "The numeric value to convert",
            },
            "from_unit": {
                "type": "string",
                "description": "Source unit (e.g., 'km', 'lb', 'F', 'GB')",
            },
            "to_unit": {
                "type": "string",
                "description": "Target unit (e.g., 'mi', 'kg', 'C', 'MB')",
            },
        },
        "required": ["value", "from_unit", "to_unit"],
    },
}
```

**为什么模式重要：** `description` 字段是 LLM 决定何时使用您的工具的方式。要具体说明它做什么以及何时使用它。`parameters` 定义 LLM 传递的参数。

## 步骤 4：编写工具处理程序

创建 `tools.py` — 这是 LLM 调用您的工具时实际执行的代码：

```python
"""Tool handlers — the code that runs when the LLM calls each tool."""

import json
import math

# Safe globals for expression evaluation — no file/network access
_SAFE_MATH = {
    "abs": abs, "round": round, "min": min, "max": max,
    "pow": pow, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "log": math.log, "log2": math.log2, "log10": math.log10,
    "floor": math.floor, "ceil": math.ceil,
    "pi": math.pi, "e": math.e,
    "factorial": math.factorial,
}


def calculate(args: dict, **kwargs) -> str:
    """Evaluate a math expression safely.

    Rules for handlers:
    1. Receive args (dict) — the parameters the LLM passed
    2. Do the work
    3. Return a JSON string — ALWAYS, even on error
    4. Accept **kwargs for forward compatibility
    """
    expression = args.get("expression", "").strip()
    if not expression:
        return json.dumps({"error": "No expression provided"})

    try:
        result = eval(expression, {"__builtins__": {}}, _SAFE_MATH)
        return json.dumps({"expression": expression, "result": result})
    except ZeroDivisionError:
        return json.dumps({"expression": expression, "error": "Division by zero"})
    except Exception as e:
        return json.dumps({"expression": expression, "error": f"Invalid: {e}"})


# Conversion tables — values are in base units
_LENGTH = {"m": 1, "km": 1000, "mi": 1609.34, "ft": 0.3048, "in": 0.0254, "cm": 0.01}
_WEIGHT = {"kg": 1, "g": 0.001, "lb": 0.453592, "oz": 0.0283495}
_DATA = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_TIME = {"s": 1, "ms": 0.001, "min": 60, "hr": 3600, "day": 86400}


def _convert_temp(value, from_u, to_u):
    # Normalize to Celsius
    c = {"F": (value - 32) * 5/9, "K": value - 273.15}.get(from_u, value)
    # Convert to target
    return {"F": c * 9/5 + 32, "K": c + 273.15}.get(to_u, c)


def unit_convert(args: dict, **kwargs) -> str:
    """Convert between units."""
    value = args.get("value")
    from_unit = args.get("from_unit", "").strip()
    to_unit = args.get("to_unit", "").strip()

    if value is None or not from_unit or not to_unit:
        return json.dumps({"error": "Need value, from_unit, and to_unit"})

    try:
        # Temperature
        if from_unit.upper() in {"C","F","K"} and to_unit.upper() in {"C","F","K"}:
            result = _convert_temp(float(value), from_unit.upper(), to_unit.upper())
            return json.dumps({"input": f"{value} {from_unit}", "result": round(result, 4),
                             "output": f"{round(result, 4)} {to_unit}"})

        # Ratio-based conversions
        for table in (_LENGTH, _WEIGHT, _DATA, _TIME):
            lc = {k.lower(): v for k, v in table.items()}
            if from_unit.lower() in lc and to_unit.lower() in lc:
                result = float(value) * lc[from_unit.lower()] / lc[to_unit.lower()]
                return json.dumps({"input": f"{value} {from_unit}",
                                 "result": round(result, 6),
                                 "output": f"{round(result, 6)} {to_unit}"})

        return json.dumps({"error": f"Cannot convert {from_unit} → {to_unit}"})
    except Exception as e:
        return json.dumps({"error": f"Conversion failed: {e}"})
```

**处理程序的关键规则：**
1. **签名：** `def my_handler(args: dict, **kwargs) -> str`
2. **返回：** 始终是 JSON 字符串。成功和错误都一样。
3. **永不抛出：** 捕获所有异常，改而返回错误 JSON。
4. **接受 `**kwargs`：** KClaw 未来可能传递额外上下文。

## 步骤 5：编写注册

创建 `__init__.py` — 这将模式连接到处理程序：

```python
"""Calculator plugin — registration."""

import logging

from . import schemas, tools

logger = logging.getLogger(__name__)

# Track tool usage via hooks
_call_log = []

def _on_post_tool_call(tool_name, args, result, task_id, **kwargs):
    """Hook: runs after every tool call (not just ours)."""
    _call_log.append({"tool": tool_name, "session": task_id})
    if len(_call_log) > 100:
        _call_log.pop(0)
    logger.debug("Tool called: %s (session %s)", tool_name, task_id)


def register(ctx):
    """Wire schemas to handlers and register hooks."""
    ctx.register_tool(name="calculate",    toolset="calculator",
                      schema=schemas.CALCULATE,    handler=tools.calculate)
    ctx.register_tool(name="unit_convert", toolset="calculator",
                      schema=schemas.UNIT_CONVERT, handler=tools.unit_convert)

    # This hook fires for ALL tool calls, not just ours
    ctx.register_hook("post_tool_call", _on_post_tool_call)
```

**`register()` 做什么：**
- 在启动时恰好调用一次
- `ctx.register_tool()` 将您的工具放入注册表——模型立即看到它
- `ctx.register_hook()` 订阅生命周期事件
- `ctx.register_cli_command()` 注册 CLI 子命令（例如 `kclaw my-plugin <subcommand>`）
- 如果此函数崩溃，插件被禁用但 KClaw 继续正常运行

## 步骤 6：测试它

启动 KClaw：

```bash
kclaw
```

您应该在横幅的工具列表中看到 `calculator: calculate, unit_convert`。

尝试这些提示：
```
What's 2 to the power of 16?
Convert 100 fahrenheit to celsius
What's the square root of 2 times pi?
How many gigabytes is 1.5 terabytes?
```

检查插件状态：
```
/plugins
```

输出：
```
Plugins (1):
  ✓ calculator v1.0.0 (2 tools, 1 hooks)
```

## 插件的最终结构

```
~/.kclaw/plugins/calculator/
├── plugin.yaml      # "I'm calculator, I provide tools and hooks"
├── __init__.py      # Wiring: schemas → handlers, register hooks
├── schemas.py       # What the LLM reads (descriptions + parameter specs)
└── tools.py         # What runs (calculate, unit_convert functions)
```

四个文件，清晰分离：
- **清单**声明插件是什么
- **模式**为 LLM 描述工具
- **处理程序**实现实际逻辑
- **注册**连接一切

## 插件还能做什么？

### 附带数据文件

将任何文件放入插件目录并在导入时读取：

```python
# In tools.py or __init__.py
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent
_DATA_FILE = _PLUGIN_DIR / "data" / "languages.yaml"

with open(_DATA_FILE) as f:
    _DATA = yaml.safe_load(f)
```

### 捆绑技能

包含 `skill.md` 文件并在注册期间安装：

```python
import shutil
from pathlib import Path

def _install_skill():
    """Copy our skill to ~/.kclaw/skills/ on first load."""
    try:
        from kclaw_cli.config import get_kclaw_home
        dest = get_kclaw_home() / "skills" / "my-plugin" / "SKILL.md"
    except Exception:
        dest = Path.home() / ".kclaw" / "skills" / "my-plugin" / "SKILL.md"

    if dest.exists():
        return  # don't overwrite user edits

    source = Path(__file__).parent / "skill.md"
    if source.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)

def register(ctx):
    ctx.register_tool(...)
    _install_skill()
```

### 环境变量门控

如果您的插件需要 API 密钥：

```yaml
# plugin.yaml — simple format (backwards-compatible)
requires_env:
  - WEATHER_API_KEY
```

如果 `WEATHER_API_KEY` 未设置，插件被禁用并显示清晰消息。没有崩溃，没有代理错误——只是"Plugin weather disabled (missing: WEATHER_API_KEY)"。

当用户运行 `kclaw plugins install` 时，会**交互式提示**任何缺失的 `requires_env` 变量。值自动保存到 `.env`。

为了更好的安装体验，使用带有描述和注册 URL 的丰富格式：

```yaml
# plugin.yaml — rich format
requires_env:
  - name: WEATHER_API_KEY
    description: "API key for OpenWeather"
    url: "https://openweathermap.org/api"
    secret: true
```

| 字段 | 必需 | 描述 |
|-------|----------|-------------|
| `name` | 是 | 环境变量名 |
| `description` | 否 | 安装提示期间显示给用户 |
| `url` | 否 | 在哪里获取凭证 |
| `secret` | 否 | 如果为 `true`，输入被隐藏（像密码字段） |

两种格式可以在同一列表中混合。已设置的变量被静默跳过。

### 条件工具可用性

对于依赖于可选库的工具：

```python
ctx.register_tool(
    name="my_tool",
    schema={...},
    handler=my_handler,
    check_fn=lambda: _has_optional_lib(),  # False = tool hidden from model
)
```

### 注册多个钩子

```python
def register(ctx):
    ctx.register_hook("pre_tool_call", before_any_tool)
    ctx.register_hook("post_tool_call", after_any_tool)
    ctx.register_hook("pre_llm_call", inject_memory)
    ctx.register_hook("on_session_start", on_new_session)
    ctx.register_hook("on_session_end", on_session_end)
```

### 钩子参考

每个钩子都在**[事件钩子参考](/docs/user-guide/features/hooks#plugin-hooks)**中有完整文档——回调签名、参数表、确切触发时机和示例。这是摘要：

| 钩子 | 触发时机 | 回调签名 | 返回 |
|------|-----------|-------------------|---------|
| [`pre_tool_call`](/docs/user-guide/features/hooks#pre_tool_call) | 任何工具执行前 | `tool_name: str, args: dict, task_id: str` | ignored |
| [`post_tool_call`](/docs/user-guide/features/hooks#post_tool_call) | 任何工具返回后 | `tool_name: str, args: dict, result: str, task_id: str` | ignored |
| [`pre_llm_call`](/docs/user-guide/features/hooks#pre_llm_call) | 每轮一次，工具调用循环前 | `session_id: str, user_message: str, conversation_history: list, is_first_turn: bool, model: str, platform: str` | [context injection](#pre_llm_call-context-injection) |
| [`post_llm_call`](/docs/user-guide/features/hooks#post_llm_call) | 每轮一次，工具调用循环后（仅成功轮次） | `session_id: str, user_message: str, assistant_response: str, conversation_history: list, model: str, platform: str` | ignored |
| [`on_session_start`](/docs/user-guide/features/hooks#on_session_start) | 创建新会话时（仅第一轮） | `session_id: str, model: str, platform: str` | ignored |
| [`on_session_end`](/docs/user-guide/features/hooks#on_session_end) | 每个 `run_conversation` 调用结束时 + CLI 退出 | `session_id: str, completed: bool, interrupted: bool, model: str, platform: str` | ignored |
| [`pre_api_request`](/docs/user-guide/features/hooks#pre_api_request) | 每次 HTTP 请求到 LLM 提供商前 | `method: str, url: str, headers: dict, body: dict` | ignored |
| [`post_api_request`](/docs/user-guide/features/hooks#post_api_request) | 每次从 LLM 提供商收到 HTTP 响应后 | `method: str, url: str, status_code: int, response: dict` | ignored |

大多数钩子是即发即忘的观察者——它们的返回值被忽略。例外是 `pre_llm_call`，它可以向对话注入上下文。

所有回调应该接受 `**kwargs` 以实现向前兼容。如果钩子回调崩溃，它被记录并跳过。其他钩子和代理继续正常运行。

### `pre_llm_call` 上下文注入

这是唯一返回值重要的钩子。当 `pre_llm_call` 回调返回带有 `"context"` 键的字典（或纯字符串）时，KClaw 将该文本注入**当前轮次的用户消息**。这是记忆插件、RAG 集成、护栏和任何需要向模型提供额外上下文的插件的机制。

#### 返回格式

```python
# Dict with context key
return {"context": "Recalled memories:\n- User prefers dark mode\n- Last project: kclaw"}

# Plain string (equivalent to the dict form above)
return "Recalled memories:\n- User prefers dark mode"

# Return None or don't return → no injection (observer-only)
return None
```

任何带有 `"context"` 键的非 None、非空返回（或纯非空字符串）被收集并附加到当前轮次的用户消息。

#### 注入如何工作

注入的上下文被附加到**用户消息**，而不是系统提示。这是一个深思熟虑的设计选择：

- **提示缓存保持** — 系统提示在轮次之间保持相同。Anthropic 和 OpenRouter 缓存系统提示前缀，因此保持稳定可节省多轮对话中 75%+ 的输入令牌。如果插件修改系统提示，每轮都将是缓存未命中。
- **短暂的** — 注入仅在 API 调用时发生。对话历史中的原始用户消息从未被改变，没有任何内容持久化到会话数据库。
- **系统提示是 KClaw 的领地** — 它包含模型特定指导、工具强制规则、人格指令和缓存技能内容。插件与用户输入一起提供上下文，而不是通过改变代理的核心指令。

#### 示例：记忆召回插件

```python
"""Memory plugin — recalls relevant context from a vector store."""

import httpx

MEMORY_API = "https://your-memory-api.example.com"

def recall_context(session_id, user_message, is_first_turn, **kwargs):
    """Called before each LLM turn. Returns recalled memories."""
    try:
        resp = httpx.post(f"{MEMORY_API}/recall", json={
            "session_id": session_id,
            "query": user_message,
        }, timeout=3)
        memories = resp.json().get("results", [])
        if not memories:
            return None  # nothing to inject

        text = "Recalled context from previous sessions:\n"
        text += "\n".join(f"- {m['text']}" for m in memories)
        return {"context": text}
    except Exception:
        return None  # fail silently, don't break the agent

def register(ctx):
    ctx.register_hook("pre_llm_call", recall_context)
```

#### 示例：护栏插件

```python
"""Guardrails plugin — enforces content policies."""

POLICY = """You MUST follow these content policies for this session:
- Never generate code that accesses the filesystem outside the working directory
- Always warn before executing destructive operations
- Refuse requests involving personal data extraction"""

def inject_guardrails(**kwargs):
    """Injects policy text into every turn."""
    return {"context": POLICY}

def register(ctx):
    ctx.register_hook("pre_llm_call", inject_guardrails)
```

#### 仅观察者钩子示例（无注入）

```python
"""Analytics plugin — tracks turn metadata without injecting context."""

import logging
logger = logging.getLogger(__name__)

def log_turn(session_id, user_message, model, is_first_turn, **kwargs):
    """Fires before each LLM call. Returns None — no context injected."""
    logger.info("Turn: session=%s model=%s first=%s msg_len=%d",
                session_id, model, is_first_turn, len(user_message or ""))
    # No return → no injection

def register(ctx):
    ctx.register_hook("pre_llm_call", log_turn)
```

#### 多个插件返回上下文

当多个插件从 `pre_llm_call` 返回上下文时，它们的输出用双换行符连接并一起附加到用户消息。顺序遵循插件发现顺序（按插件目录名称字母顺序）。

### 注册 CLI 命令

插件可以添加自己的 `kclaw <plugin>` 子命令树：

```python
def _my_command(args):
    """Handler for kclaw my-plugin <subcommand>."""
    sub = getattr(args, "my_command", None)
    if sub == "status":
        print("All good!")
    elif sub == "config":
        print("Current config: ...")
    else:
        print("Usage: kclaw my-plugin <status|config>")

def _setup_argparse(subparser):
    """Build the argparse tree for kclaw my-plugin."""
    subs = subparser.add_subparsers(dest="my_command")
    subs.add_parser("status", help="Show plugin status")
    subs.add_parser("config", help="Show plugin config")
    subparser.set_defaults(func=_my_command)

def register(ctx):
    ctx.register_tool(...)
    ctx.register_cli_command(
        name="my-plugin",
        help="Manage my plugin",
        setup_fn=_setup_argparse,
        handler_fn=_my_command,
    )
```

注册后，用户可以运行 `kclaw my-plugin status`、`kclaw my-plugin config` 等。

**记忆提供商插件**使用基于约定的方法：将 `register_cli(subparser)` 函数添加到插件的 `cli.py` 文件中。记忆插件发现系统自动找到它——不需要 `ctx.register_cli_command()` 调用。详情请参见[记忆提供商插件指南](/docs/developer-guide/memory-provider-plugin#adding-cli-commands)。

**活动提供商门控：** 记忆插件 CLI 命令仅在其提供商是配置中活动的 `memory.provider` 时出现。如果用户未设置您的提供商，您的 CLI 命令不会弄乱帮助输出。

### 通过 pip 分发

对于公开共享插件，将入口点添加到您的 Python 包：

```toml
# pyproject.toml
[project.entry-points."kclaw_agent.plugins"]
my-plugin = "my_plugin_package"
```

```bash
pip install kclaw-plugin-calculator
# Plugin auto-discovered on next kclaw startup
```

## 常见错误

**处理程序不返回 JSON 字符串：**
```python
# Wrong — returns a dict
def handler(args, **kwargs):
    return {"result": 42}

# Right — returns a JSON string
def handler(args, **kwargs):
    return json.dumps({"result": 42})
```

**处理程序签名缺少 `**kwargs`：**
```python
# Wrong — will break if KClaw passes extra context
def handler(args):
    ...

# Right
def handler(args, **kwargs):
    ...
```

**处理程序抛出异常：**
```python
# Wrong — exception propagates, tool call fails
def handler(args, **kwargs):
    result = 1 / int(args["value"])  # ZeroDivisionError!
    return json.dumps({"result": result})

# Right — catch and return error JSON
def handler(args, **kwargs):
    try:
        result = 1 / int(args.get("value", 0))
        return json.dumps({"result": result})
    except Exception as e:
        return json.dumps({"error": str(e)})
```

**模式描述太模糊：**
```python
# Bad — model doesn't know when to use it
"description": "Does stuff"

# Good — model knows exactly when and how
"description": "Evaluate a mathematical expression. Use for arithmetic, trig, logarithms. Supports: +, -, *, /, **, sqrt, sin, cos, log, pi, e."
```
