# KClaw Agent - 开发指南

面向 AI 编程助手和开发者的 kclaw 代码库工作指引。

## 开发环境

```bash
source venv/bin/activate  # 运行 Python 前务必激活
```

## 项目结构

```
kclaw/
├── run_agent.py          # AIAgent 类 — 核心对话循环
├── model_tools.py        # 工具编排，_discover_tools()，handle_function_call()
├── toolsets.py           # 工具集定义，_KCLAW_CORE_TOOLS 列表
├── cli.py                # KClawCLI 类 — 交互式 CLI 编排器
├── kclaw_state.py       # SessionDB — SQLite 会话存储（FTS5 搜索）
├── agent/                # Agent 内部实现
│   ├── prompt_builder.py     # 系统提示词组装
│   ├── context_compressor.py # 自动上下文压缩
│   ├── prompt_caching.py     # Anthropic 提示词缓存
│   ├── auxiliary_client.py   # 辅助 LLM 客户端（视觉、摘要）
│   ├── model_metadata.py     # 模型上下文长度、token 估算
│   ├── models_dev.py         # models.dev 注册表集成（感知 provider 的上下文）
│   ├── display.py            # KawaiiSpinner，工具预览格式化
│   ├── skill_commands.py     # Skill 斜杠命令（CLI/gateway 共享）
│   └── trajectory.py         # 轨迹保存辅助
├── kclaw_cli/           # CLI 子命令和设置
│   ├── main.py           # 入口 — 所有 `kclaw` 子命令
│   ├── config.py         # DEFAULT_CONFIG，OPTIONAL_ENV_VARS，迁移
│   ├── commands.py       # 斜杠命令定义 + SlashCommandCompleter
│   ├── callbacks.py      # 终端回调（澄清、sudo、审批）
│   ├── setup.py          # 交互式设置向导
│   ├── skin_engine.py    # 皮肤/主题引擎 — CLI 视觉定制
│   ├── skills_config.py  # `kclaw skills` — 按平台启用/禁用技能
│   ├── tools_config.py   # `kclaw tools` — 按平台启用/禁用工具
│   ├── skills_hub.py     # `/skills` 斜杠命令（搜索、浏览、安装）
│   ├── models.py         # 模型目录，provider 模型列表
│   ├── model_switch.py   # 共享 /model 切换管线（CLI + gateway）
│   └── auth.py           # Provider 凭据解析
├── tools/                # 工具实现（每个工具一个文件）
│   ├── registry.py       # 中央工具注册表（schema、handler、分发）
│   ├── approval.py       # 危险命令检测
│   ├── terminal_tool.py  # 终端编排
│   ├── process_registry.py # 后台进程管理
│   ├── file_tools.py     # 文件读/写/搜索/补丁
│   ├── web_tools.py      # Web 搜索/提取（Parallel + Firecrawl）
│   ├── browser_tool.py   # Browserbase 浏览器自动化
│   ├── code_execution_tool.py # execute_code 沙箱
│   ├── delegate_tool.py  # 子 Agent 委托
│   ├── mcp_tool.py       # MCP 客户端（约 1050 行）
│   └── environments/     # 终端后端（local、docker、ssh、modal、daytona、singularity）
├── gateway/              # 消息平台网关
│   ├── run.py            # 主循环，斜杠命令，消息分发
│   ├── session.py        # SessionStore — 对话持久化
│   └── platforms/        # 适配器：telegram、discord、slack、whatsapp、homeassistant、signal
├── acp_adapter/          # ACP 服务器（VS Code / Zed / JetBrains 集成）
├── cron/                 # 调度器（jobs.py、scheduler.py）
├── environments/         # RL 训练环境（Atropos）
├── tests/                # Pytest 测试套件（约 3000 个测试）
└── batch_runner.py       # 并行批处理
```

**用户配置：** `~/.kclaw/config.yaml`（设置），`~/.kclaw/.env`（API 密钥）

## 文件依赖链

```
tools/registry.py  （无依赖 — 被所有工具文件导入）
       ↑
tools/*.py  （每个在导入时调用 registry.register()）
       ↑
model_tools.py  （导入 tools/registry + 触发工具发现）
       ↑
run_agent.py, cli.py, batch_runner.py, environments/
```

---

## AIAgent 类 (run_agent.py)

```python
class AIAgent:
    def __init__(self,
        model: str = "anthropic/claude-opus-4.6",
        max_iterations: int = 90,
        enabled_toolsets: list = None,
        disabled_toolsets: list = None,
        quiet_mode: bool = False,
        save_trajectories: bool = False,
        platform: str = None,           # "cli", "telegram", 等
        session_id: str = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        # ... 加上 provider, api_mode, callbacks, routing 参数
    ): ...

    def chat(self, message: str) -> str:
        """简单接口 — 返回最终响应字符串。"""

    def run_conversation(self, user_message: str, system_message: str = None,
                         conversation_history: list = None, task_id: str = None) -> dict:
        """完整接口 — 返回包含 final_response + messages 的字典。"""
```

### Agent 循环

核心循环位于 `run_conversation()` 内 — 完全同步：

```python
while api_call_count < self.max_iterations and self.iteration_budget.remaining > 0:
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

消息遵循 OpenAI 格式：`{"role": "system/user/assistant/tool", ...}`。推理内容存储在 `assistant_msg["reasoning"]` 中。

---

## CLI 架构 (cli.py)

- **Rich** 用于横幅/面板，**prompt_toolkit** 用于带自动补全的输入
- **KawaiiSpinner** (`agent/display.py`) — API 调用期间的动画表情，工具结果的 `┊` 活动信息流
- `load_cli_config()` 在 `cli.py` 中合并硬编码默认值 + 用户配置 YAML
- **皮肤引擎** (`kclaw_cli/skin_engine.py`) — 数据驱动的 CLI 主题化；启动时从 `display.skin` 配置键初始化；皮肤可定制横幅颜色、spinner 表情/动词/翅膀、工具前缀、响应框、品牌文本
- `process_command()` 是 `KClawCLI` 的方法 — 通过中央注册表的 `resolve_command()` 解析规范命令名后分发
- **Skill 斜杠命令**：`agent/skill_commands.py` 扫描 `~/.kclaw/skills/`，以**用户消息**（非系统提示词）注入以保持提示词缓存

### 斜杠命令注册表 (`kclaw_cli/commands.py`)

所有斜杠命令定义在中央 `COMMAND_REGISTRY` 列表中，由 `CommandDef` 对象组成。每个下游消费者自动从此注册表派生：

- **CLI** — `process_command()` 通过 `resolve_command()` 解析别名，按规范名分发
- **Gateway** — `GATEWAY_KNOWN_COMMANDS` 冻结集合用于 hook 发射，`resolve_command()` 用于分发
- **Gateway 帮助** — `gateway_help_lines()` 生成 `/help` 输出
- **Telegram** — `telegram_bot_commands()` 生成 BotCommand 菜单
- **Slack** — `slack_subcommand_map()` 生成 `/kclaw` 子命令路由
- **自动补全** — `COMMANDS` 扁平字典供 `SlashCommandCompleter` 使用
- **CLI 帮助** — `COMMANDS_BY_CATEGORY` 字典供 `show_help()` 使用

#### 添加斜杠命令

1. 在 `kclaw_cli/commands.py` 的 `COMMAND_REGISTRY` 中添加 `CommandDef` 条目：

```python
CommandDef("mycommand", "Description of what it does", "Session",
           aliases=("mc",), args_hint="[arg]"),
```

2. 在 `cli.py` 的 `KClawCLI.process_command()` 中添加处理函数：

```python
elif canonical == "mycommand":
    self._handle_mycommand(cmd_original)
```

3. 如果该命令在 gateway 中可用，在 `gateway/run.py` 中添加处理函数：

```python
if canonical == "mycommand":
    return await self._handle_mycommand(event)
```

4. 对于持久化设置，使用 `cli.py` 中的 `save_config_value()`

**CommandDef 字段：**

- `name` — 不带斜杠的规范名称（例如 `"background"`）
- `description` — 人类可读的描述
- `category` — 以下之一：`"Session"`、`"Configuration"`、`"Tools & Skills"`、`"Info"`、`"Exit"`
- `aliases` — 备选名称元组（例如 `("bg",)`）
- `args_hint` — 帮助中显示的参数占位符（例如 `"<prompt>"`、`"[name]"`）
- `cli_only` — 仅在交互式 CLI 中可用
- `gateway_only` — 仅在消息平台中可用
- `gateway_config_gate` — 配置点路径（例如 `"display.tool_progress_command"`）；当设置在 `cli_only` 命令上时，如果配置值为真，该命令在 gateway 中也可用。`GATEWAY_KNOWN_COMMANDS` 始终包含配置门控命令以便 gateway 可以分发；帮助/菜单仅在门打开时显示它们。

添加别名只需在现有 `CommandDef` 的 `aliases` 元组中添加即可。无需修改其他文件 — 分发、帮助文本、Telegram 菜单、Slack 映射和自动补全都会自动更新。

---

## 添加新工具

需要修改 **3 个文件**：

**1. 创建 `tools/your_tool.py`：**

```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("EXAMPLE_API_KEY"))

def example_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": "..."})

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

**2. 添加导入** 在 `model_tools.py` 的 `_discover_tools()` 列表中。

**3. 添加到 `toolsets.py`** — 可以是 `_KCLAW_CORE_TOOLS`（所有平台）或新工具集。

注册表负责 schema 收集、分发、可用性检查和错误包装。所有 handler 必须返回 JSON 字符串。

**工具 schema 中的路径引用**：如果 schema 描述中提到文件路径（例如默认输出目录），使用 `display_kclaw_home()` 使其感知 profile。schema 在导入时生成，此时 `_apply_profile_override()` 已设置 `KCLAW_HOME`。

**状态文件**：如果工具存储持久状态（缓存、日志、检查点），使用 `get_kclaw_home()` 作为基础目录 — 绝不要用 `Path.home() / ".kclaw"`。这确保每个 profile 有独立的状态。

**Agent 级别工具**（todo、memory）：由 `run_agent.py` 在 `handle_function_call()` 之前拦截。参见 `todo_tool.py` 的模式。

---

## 添加配置

### config.yaml 选项：

1. 在 `kclaw_cli/config.py` 的 `DEFAULT_CONFIG` 中添加
2. 递增 `_config_version`（当前为 5）以触发现有用户的迁移

### .env 变量：

在 `kclaw_cli/config.py` 的 `OPTIONAL_ENV_VARS` 中添加，附带元数据：

```python
"NEW_API_KEY": {
    "description": "What it's for",
    "prompt": "Display name",
    "url": "https://...",
    "password": True,
    "category": "tool",  # provider, tool, messaging, setting
},
```

### 配置加载器（两个独立系统）：

| 加载器 | 使用方 | 位置 |
|--------|--------|------|
| `load_cli_config()` | CLI 模式 | `cli.py` |
| `load_config()` | `kclaw tools`、`kclaw setup` | `kclaw_cli/config.py` |
| 直接 YAML 加载 | Gateway | `gateway/run.py` |

---

## 皮肤/主题系统

皮肤引擎（`kclaw_cli/skin_engine.py`）提供数据驱动的 CLI 视觉定制。皮肤是**纯数据** — 添加新皮肤无需修改代码。

### 架构

```
kclaw_cli/skin_engine.py    # SkinConfig 数据类，内置皮肤，YAML 加载器
~/.kclaw/skins/*.yaml       # 用户安装的自定义皮肤（即插即用）
```

- `init_skin_from_config()` — CLI 启动时调用，从配置读取 `display.skin`
- `get_active_skin()` — 返回当前皮肤的缓存 `SkinConfig`
- `set_active_skin(name)` — 运行时切换皮肤（由 `/skin` 命令使用）
- `load_skin(name)` — 优先从用户皮肤加载，然后内置，最后回退到默认
- 缺失的皮肤值自动从 `default` 皮肤继承

### 皮肤可定制的内容

| 元素 | 皮肤键 | 使用方 |
|------|--------|--------|
| 横幅面板边框 | `colors.banner_border` | `banner.py` |
| 横幅面板标题 | `colors.banner_title` | `banner.py` |
| 横幅节标题 | `colors.banner_accent` | `banner.py` |
| 横幅暗淡文本 | `colors.banner_dim` | `banner.py` |
| 横幅正文文本 | `colors.banner_text` | `banner.py` |
| 响应框边框 | `colors.response_border` | `cli.py` |
| Spinner 表情（等待中） | `spinner.waiting_faces` | `display.py` |
| Spinner 表情（思考中） | `spinner.thinking_faces` | `display.py` |
| Spinner 动词 | `spinner.thinking_verbs` | `display.py` |
| Spinner 翅膀（可选） | `spinner.wings` | `display.py` |
| 工具输出前缀 | `tool_prefix` | `display.py` |
| 每个工具的 emoji | `tool_emojis` | `display.py` → `get_tool_emoji()` |
| Agent 名称 | `branding.agent_name` | `banner.py`、`cli.py` |
| 欢迎消息 | `branding.welcome` | `cli.py` |
| 响应框标签 | `branding.response_label` | `cli.py` |
| 提示符 | `branding.prompt_symbol` | `cli.py` |

### 内置皮肤

- `default` — 经典 KClaw 金色/可爱风格（当前外观）
- `ares` — 绯红/青铜战神主题，带自定义 spinner 翅膀
- `mono` — 简洁灰度单色
- `slate` — 冷蓝色开发者专注主题

### 添加内置皮肤

在 `kclaw_cli/skin_engine.py` 的 `_BUILTIN_SKINS` 字典中添加：

```python
"mytheme": {
    "name": "mytheme",
    "description": "Short description",
    "colors": { ... },
    "spinner": { ... },
    "branding": { ... },
    "tool_prefix": "┊",
},
```

### 用户皮肤（YAML）

用户创建 `~/.kclaw/skins/<name>.yaml`：

```yaml
name: cyberpunk
description: Neon-soaked terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

通过 `/skin cyberpunk` 或 `config.yaml` 中的 `display.skin: cyberpunk` 激活。

---

## 重要策略

### 提示词缓存不得破坏

KClaw-Agent 确保缓存在整个对话期间保持有效。**不要实施以下变更：**

- 在对话中途修改过去的上下文
- 在对话中途更改工具集
- 在对话中途重新加载记忆或重建系统提示词

缓存破坏会大幅增加成本。我们修改上下文的唯一时机是上下文压缩期间。

### 工作目录行为

- **CLI**：使用当前目录（`.` → `os.getcwd()`）
- **消息平台**：使用 `MESSAGING_CWD` 环境变量（默认：用户主目录）

### 后台进程通知（Gateway）

当使用 `terminal(background=true, check_interval=...)` 时，gateway 运行一个监视器，向用户聊天推送状态更新。通过 `config.yaml` 中的 `display.background_process_notifications`（或 `KCLAW_BACKGROUND_NOTIFICATIONS` 环境变量）控制详细程度：

- `all` — 运行输出更新 + 最终消息（默认）
- `result` — 仅最终完成消息
- `error` — 仅退出码 != 0 时的最终消息
- `off` — 完全不发送监视器消息

---

## Profile：多实例支持

KClaw 支持 **profile** — 多个完全隔离的实例，每个都有自己独立的 `KCLAW_HOME` 目录（配置、API 密钥、记忆、会话、技能、gateway 等）。

核心机制：`kclaw_cli/main.py` 中的 `_apply_profile_override()` 在任何模块导入之前设置 `KCLAW_HOME`。所有 119+ 处对 `get_kclaw_home()` 的引用自动限定到活动 profile。

### Profile 安全编码规则

1. **对所有 `KCLAW_HOME` 路径使用 `get_kclaw_home()`。** 从 `kclaw_constants` 导入。绝不要在读写状态的代码中硬编码 `~/.kclaw` 或 `Path.home() / ".kclaw"`。

```python
# 正确
from kclaw_constants import get_kclaw_home
config_path = get_kclaw_home() / "config.yaml"

# 错误 — 破坏 profile
config_path = Path.home() / ".kclaw" / "config.yaml"
```

2. **对面向用户的消息使用 `display_kclaw_home()`。** 从 `kclaw_constants` 导入。默认返回 `~/.kclaw`，profile 返回 `~/.kclaw/profiles/<name>`。

```python
# 正确
from kclaw_constants import display_kclaw_home
print(f"Config saved to {display_kclaw_home()}/config.yaml")

# 错误 — 对 profile 显示错误路径
print("Config saved to ~/.kclaw/config.yaml")
```

3. **模块级常量是安全的** — 它们在导入时缓存 `get_kclaw_home()`，此时 `_apply_profile_override()` 已设置环境变量。只需使用 `get_kclaw_home()`，不要用 `Path.home() / ".kclaw"`。

4. **模拟 `Path.home()` 的测试也必须设置 `KCLAW_HOME`** — 因为代码现在使用 `get_kclaw_home()`（读取环境变量），而非 `Path.home() / ".kclaw"`：

```python
with patch.object(Path, "home", return_value=tmp_path), \
     patch.dict(os.environ, {"KCLAW_HOME": str(tmp_path / ".kclaw")}):
    ...
```

5. **Gateway 平台适配器应使用令牌锁** — 如果适配器使用唯一凭据（bot 令牌、API 密钥）连接，在 `connect()`/`start()` 方法中调用 `gateway.status` 的 `acquire_scoped_lock()`，在 `disconnect()`/`stop()` 中调用 `release_scoped_lock()`。这防止两个 profile 使用同一凭据。参见 `gateway/platforms/telegram.py` 的规范模式。

6. **Profile 操作以 HOME 为锚点，而非 KCLAW_HOME** — `_get_profiles_root()` 返回 `Path.home() / ".kclaw" / "profiles"`，而非 `get_kclaw_home() / "profiles"`。这是有意为之 — 它让 `kclaw -p coder profile list` 无论哪个 profile 处于活动状态都能看到所有 profile。

---

## 已知陷阱

### 不要硬编码 `~/.kclaw` 路径

对代码路径使用 `kclaw_constants` 中的 `get_kclaw_home()`。对面向用户的打印/日志消息使用 `display_kclaw_home()`。硬编码 `~/.kclaw` 会破坏 profile — 每个 profile 有自己的 `KCLAW_HOME` 目录。这是 PR #3575 中修复的 5 个 bug 的根源。

### 不要使用 `simple_term_menu` 作为交互式菜单

在 tmux/iTerm2 中有渲染 bug — 滚动时出现残影。请改用 `curses`（标准库）。参见 `kclaw_cli/tools_config.py` 的模式。

### 不要在 spinner/display 代码中使用 `\033[K`（ANSI 擦除至行尾）

在 `prompt_toolkit` 的 `patch_stdout` 下会泄露为字面 `?[K` 文本。请使用空格填充：`f"\r{line}{' ' * pad}"`。

### `_last_resolved_tool_names` 是 `model_tools.py` 中的进程全局变量

`delegate_tool.py` 中的 `_run_single_child()` 在子 Agent 执行前后保存和恢复此全局变量。如果你添加读取此全局变量的新代码，请注意它在子 Agent 运行期间可能暂时过时。

### 不要在 schema 描述中硬编码跨工具引用

工具 schema 描述不得按名称提及其他工具集的工具（例如 `browser_navigate` 说"prefer web_search"）。这些工具可能不可用（缺少 API 密钥、禁用的工具集），导致模型幻觉调用不存在的工具。如果需要跨引用，在 `model_tools.py` 的 `get_tool_definitions()` 中动态添加 — 参见 `browser_navigate` / `execute_code` 后处理块的模式。

### 测试不得写入 `~/.kclaw/`

`tests/conftest.py` 中的 `_isolate_kclaw_home` autouse fixture 将 `KCLAW_HOME` 重定向到临时目录。绝不要在测试中硬编码 `~/.kclaw/` 路径。

**Profile 测试**：测试 profile 功能时，也要模拟 `Path.home()`，以便 `_get_profiles_root()` 和 `_get_default_kclaw_home()` 在临时目录内解析。使用 `tests/kclaw_cli/test_profiles.py` 中的模式：

```python
@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".kclaw"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("KCLAW_HOME", str(home))
    return home
```

---

## 测试

```bash
source venv/bin/activate
python -m pytest tests/ -q          # 完整套件（约 3000 个测试，约 3 分钟）
python -m pytest tests/test_model_tools.py -q   # 工具集解析
python -m pytest tests/test_cli_init.py -q       # CLI 配置加载
python -m pytest tests/gateway/ -q               # Gateway 测试
python -m pytest tests/tools/ -q                 # 工具级测试
```

推送变更前务必运行完整测试套件。
