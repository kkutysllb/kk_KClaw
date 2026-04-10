# 为 KClaw Agent 做贡献

感谢您为 KClaw Agent 做贡献！本指南涵盖您需要了解的一切：设置开发环境、理解架构、决定要构建什么，以及让您的 PR 被合并。

---

## 贡献优先级

我们按以下顺序重视贡献：

1. **Bug 修复** — 崩溃、错误行为、数据丢失。始终最高优先级。
2. **跨平台兼容性** — Windows、macOS、不同的 Linux 发行版、不同的终端模拟器。我们希望 KClaw 能在任何地方运行。
3. **安全加固** — shell 注入、提示注入、路径遍历、权限提升。参见[安全](#安全注意事项)。
4. **性能和稳健性** — 重试逻辑、错误处理、优雅降级。
5. **新技能** — 但仅限广泛有用的。参见[应该是技能还是工具？](#应该是技能还是工具)
6. **新工具** — 很少需要。大多数能力应该是技能。参见下文。
7. **文档** — 修复、澄清、新示例。

---

## 应该是技能还是工具？

这是新贡献者最常见的问题。答案几乎总是**技能**。

### 做成技能当：

- 该能力可以表达为指令 + shell 命令 + 现有工具
- 它包装了智能体可以通过 `terminal` 或 `web_extract` 调用的外部 CLI 或 API
- 它不需要内置于智能体的自定义 Python 集成或 API 密钥管理
- 示例：arXiv 搜索、git 工作流、Docker 管理、PDF 处理、通过 CLI 工具发送邮件

### 做成工具当：

- 它需要与 API 密钥、认证流程或由智能体管理器管理的多组件配置进行端到端集成
- 它需要每次精确执行的自定义处理逻辑（不是来自 LLM 解释的"尽力而为"）
- 它处理无法通过终端的二进制数据、流或实时事件
- 示例：浏览器自动化（Browserbase 会话管理）、TTS（音频编码 + 平台交付）、视觉分析（base64 图像处理）

### 技能应该打包吗？

打包的技能（在 `skills/` 中）随每个 KClaw 安装一起发布。它们应该**对大多数用户广泛有用**：

- 文档处理、网络研究、常见开发工作流、系统管理
- 被广泛的人群定期使用

如果您的技能是官方的且有用的，但不是普遍需要的（例如，付费服务集成、重型依赖），请将其放入 **`optional-skills/``** — 它随 repo 一起发布，但默认不激活。用户可以通过 `kclaw skills browse` 发现它（标记为"官方"）并使用 `kclaw skills install` 安装它（无第三方警告、内置信任）。

如果您的技能是专门的、社区贡献的或小众的，它更适合 **技能中心** — 上传到技能注册表并在 [kkutysllb Discord](https://discord.gg/NousResearch) 中分享。用户可以使用 `kclaw skills install` 安装它。

---

## 开发设置

### 前置要求

| 要求 | 备注 |
|-------------|-------|
| **Git** | 支持 `--recurse-submodules` |
| **Python 3.11+** | uv 会在缺失时安装 |
| **uv** | 快速 Python 包管理器（[安装](https://docs.astral.sh/uv/)）|
| **Node.js 18+** | 可选 — 需要浏览器工具和 WhatsApp 桥接 |

### 克隆和安装

```bash
git clone --recurse-submodules https://github.com/kkutysllb/kk_KClaw.git
cd kclaw

# 使用 Python 3.11 创建 venv
uv venv venv --python 3.11
export VIRTUAL_ENV="$(pwd)/venv"

# 安装所有额外功能（消息、cron、CLI 菜单、开发工具）
uv pip install -e ".[all,dev]"

# 可选：RL 训练子模块
# git submodule update --init tinker-atropos && uv pip install -e "./tinker-atropos"

# 可选：浏览器工具
npm install
```

### 配置用于开发

```bash
mkdir -p ~/.kclaw/{cron,sessions,logs,memories,skills}
cp cli-config.yaml.example ~/.kclaw/config.yaml
touch ~/.kclaw/.env

# 至少添加一个 LLM 提供商密钥：
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key' >> ~/.kclaw/.env
```

### 运行

```bash
# 创建符号链接以供全局访问
mkdir -p ~/.local/bin
ln -sf "$(pwd)/venv/bin/kclaw" ~/.local/bin/kclaw

# 验证
kclaw doctor
kclaw chat -q "你好"
```

### 运行测试

```bash
pytest tests/ -v
```

---

## 项目结构

```
kclaw/
├── run_agent.py              # AIAgent 类 — 核心对话循环、工具分发、会话持久化
├── cli.py                    # KClawCLI 类 — 交互式 TUI、prompt_toolkit 集成
├── model_tools.py            # 工具编排（基于 tools/registry.py 的薄层）
├── toolsets.py               # 工具分组和预设（kclaw-cli、kclaw-telegram 等）
├── kclaw_state.py           # 带 FTS5 全文搜索的 SQLite 会话数据库、会话标题
├── batch_runner.py           # 用于轨迹生成的并行批处理
│
├── agent/                    # 智能体内部实现（提取的模块）
│   ├── prompt_builder.py         # 系统提示组装（身份、技能、上下文文件、记忆）
│   ├── context_compressor.py     # 接近上下文限制时的自动摘要
│   ├── auxiliary_client.py       # 解析辅助 OpenAI 客户端（摘要、视觉）
│   ├── display.py                # KawaiiSpinner、工具进度格式化
│   ├── model_metadata.py         # 模型上下文长度、令牌估算
│   └── trajectory.py             # 轨迹保存辅助
│
├── kclaw_cli/               # CLI 命令实现
│   ├── main.py                   # 入口点、参数解析、命令分发
│   ├── config.py                 # 配置管理、迁移、环境变量定义
│   ├── setup.py                  # 交互式设置向导
│   ├── auth.py                   # 提供商解析、OAuth、Nous Portal
│   ├── models.py                 # OpenRouter 模型选择列表
│   ├── banner.py                 # 欢迎横幅、ASCII 艺术
│   ├── commands.py               # 中央斜杠命令注册表（CommandDef）、自动完成、网关辅助
│   ├── callbacks.py              # 交互回调（澄清、sudo、审批）
│   ├── doctor.py                 # 诊断
│   ├── skills_hub.py             # 技能中心 CLI + /skills 斜杠命令
│   └── skin_engine.py            # 皮肤/主题引擎 — 数据驱动的 CLI 视觉定制
│
├── tools/                    # 工具实现（自注册）
│   ├── registry.py               # 中央工具注册表（schema、处理器、分发）
│   ├── approval.py               # 危险命令检测 + 每会话审批
│   ├── terminal_tool.py          # 终端编排（sudo、环境生命周期、后端）
│   ├── file_operations.py        # read_file、write_file、搜索、补丁等
│   ├── web_tools.py              # web_search、web_extract（Parallel/Firecrawl + Gemini 摘要）
│   ├── vision_tools.py           # 通过多模态模型进行图像分析
│   ├── delegate_tool.py           # 子智能体生成和并行任务执行
│   ├── code_execution_tool.py    # 带 RPC 工具访问的沙箱 Python
│   ├── session_search_tool.py     # 使用 FTS5 + 摘要搜索过去的对话
│   ├── cronjob_tools.py           # 调度任务管理
│   ├── skill_tools.py            # 技能搜索、加载、管理
│   └── environments/             # 终端执行后端
│       ├── base.py                   # BaseEnvironment 抽象类
│       ├── local.py、docker.py、ssh.py、singularity.py、modal.py、daytona.py
│
├── gateway/                  # 消息网关
│   ├── run.py                    # GatewayRunner — 平台生命周期、消息路由、cron
│   ├── config.py                 # 平台配置解析
│   ├── session.py                # 会话存储、上下文提示、重置策略
│   └── platforms/                # 平台适配器
│       ├── telegram.py、discord_adapter.py、slack.py、whatsapp.py
│
├── scripts/                  # 安装程序和桥接脚本
│   ├── install.sh                # Linux/macOS 安装程序
│   ├── install.ps1               # Windows PowerShell 安装程序
│   └── whatsapp-bridge/          # Node.js WhatsApp 桥接（Baileys）
│
├── skills/                   # 打包的技能（安装时复制到 ~/.kclaw/skills/）
├── optional-skills/          # 官方可选技能（可通过 hub 发现，默认不激活）
├── environments/             # RL 训练环境（Atropos 集成）
├── tests/                    # 测试套件
├── website/                  # 文档站点（kclaw.nousresearch.com）
│
├── cli-config.yaml.example   # 示例配置（复制到 ~/.kclaw/config.yaml）
└── AGENTS.md                 # AI 编程助手开发者指南
```

### 用户配置（存储在 `~/.kclaw/`）

| 路径 | 用途 |
|------|---------|
| `~/.kclaw/config.yaml` | 设置（模型、终端、工具集、压缩等）|
| `~/.kclaw/.env` | API 密钥和 secrets |
| `~/.kclaw/auth.json` | OAuth 凭据（Nous Portal）|
| `~/.kclaw/skills/` | 所有活动技能（打包 + hub 安装 + 智能体创建）|
| `~/.kclaw/memories/` | 持久记忆（MEMORY.md、USER.md）|
| `~/.kclaw/state.db` | SQLite 会话数据库 |
| `~/.kclaw/sessions/` | JSON 会话日志 |
| `~/.kclaw/cron/` | 调度作业数据 |
| `~/.kclaw/whatsapp/session/` | WhatsApp 桥接凭据 |

---

## 架构概述

### 核心循环

```
用户消息 → AIAgent._run_agent_loop()
  ├── 构建系统提示（prompt_builder.py）
  ├── 构建 API 参数（模型、消息、工具、推理配置）
  ├── 调用 LLM（OpenAI 兼容 API）
  ├── 如果响应中有 tool_calls：
  │     ├── 通过注册表分发执行每个工具
  │     ├── 将工具结果添加到对话
  │     └── 循环回 LLM 调用
  ├── 如果是文本响应：
  │     ├── 将会话持久化到数据库
  │     └── 返回最终响应
  └── 如果接近令牌限制则进行上下文压缩
```

### 关键设计模式

- **自注册工具**：每个工具文件在导入时调用 `registry.register()`。`model_tools.py` 通过导入所有工具模块来触发发现。
- **工具集分组**：工具被分组为工具集（`web`、`terminal`、`file`、`browser` 等），可以按平台启用/禁用。
- **会话持久化**：所有对话都存储在 SQLite（`kclaw_state.py`）中，支持全文搜索和唯一会话标题。JSON 日志发送到 `~/.kclaw/sessions/`。
- **临时注入**：系统提示和预填充消息在 API 调用时注入，从不持久化到数据库或日志。
- **提供商抽象**：智能体与任何 OpenAI 兼容 API 配合使用。提供商解析在初始化时发生（Nous Portal OAuth、OpenRouter API 密钥或自定义端点）。
- **提供商路由**：使用 OpenRouter 时，`provider_routing` 在 config.yaml 中控制提供商选择（按吞吐量/延迟/价格排序、允许/忽略特定提供商、数据保留策略）。这些作为 `extra_body.provider` 注入到 API 请求中。

---

## 代码风格

- **PEP 8**，有实际例外（我们不强制执行严格的行长度）
- **注释**：仅在解释非显而易见的意图、权衡或 API 怪癖时添加。不要叙述代码做什么 — `# 增加计数器` 没有增加任何价值
- **错误处理**：捕获特定异常。使用 `logger.warning()`/`logger.error()` 记录 — 对于意外错误使用 `exc_info=True`，以便堆栈跟踪出现在日志中
- **跨平台**：永远不要假设 Unix。参见[跨平台兼容性](#跨平台兼容性)

---

## 添加新工具

在编写工具之前问一下：[这应该是技能而不是工具吗？](#应该是技能还是工具)

工具通过中央注册表自注册。每个工具文件将它的 schema、处理器和注册放在一起：

```python
"""my_tool — 关于此工具作用的简要描述。"""

import json
from tools.registry import registry


def my_tool(param1: str, param2: int = 10, **kwargs) -> str:
    """处理器。返回字符串结果（通常是 JSON）。"""
    result = do_work(param1, param2)
    return json.dumps(result)


MY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "此工具做什么以及智能体何时应该使用它。",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "param1 是什么"},
                "param2": {"type": "integer", "description": "param2 是什么", "default": 10},
            },
            "required": ["param1"],
        },
    },
}


def _check_requirements() -> bool:
    """如果此工具的依赖可用则返回 True。"""
    return True


registry.register(
    name="my_tool",
    toolset="my_toolset",
    schema=MY_TOOL_SCHEMA,
    handler=lambda args, **kw: my_tool(**args, **kw),
    check_fn=_check_requirements,
)
```

然后将导入添加到 `model_tools.py` 的 `_modules` 列表中：

```python
_modules = [
    # ... 现有模块 ...
    "tools.my_tool",
]
```

如果是新工具集，请将其添加到 `toolsets.py` 和相关平台预设中。

---

## 添加技能

打包的技能存在于按类别组织的 `skills/` 中。官方可选技能在 `optional-skills/` 中使用相同的结构：

```
skills/
├── research/
│   └── arxiv/
│       ├── SKILL.md              # 必需：主要指令
│       └── scripts/              # 可选：辅助脚本
│           └── search_arxiv.py
├── productivity/
│   └── ocr-and-documents/
│       ├── SKILL.md
│       ├── scripts/
│       └── references/
└── ...
```

### SKILL.md 格式

```markdown
---
name: my-skill
description: 简要描述（显示在技能搜索结果中）
version: 1.0.0
author: 您的姓名
license: MIT
platforms: [macos, linux]          # 可选 — 限制在特定操作系统平台
                                   #   有效值：macos、linux、windows
                                   #   省略则在所有平台上加载（默认）
required_environment_variables:    # 可选 — 安全设置时加载元数据
  - name: MY_API_KEY
    prompt: API 密钥
    help: 在哪里获取
    required_for: 完整功能
prerequisites:                     # 可选的旧版运行时要求
  env_vars: [MY_API_KEY]           #   向后兼容的环境变量别名
  commands: [curl, jq]             #   仅咨询；不会隐藏技能
metadata:
  kclaw:
    tags: [类别, 子类别, 关键词]
    related_skills: [other-skill-name]
    fallback_for_toolsets: [web]       # 可选 — 仅在工具集不可用时显示
    requires_toolsets: [terminal]      # 可选 — 仅在工具集可用时显示
---

# 技能标题

简要介绍。

## 何时使用
触发条件 — 智能体何时应该加载此技能？

## 快速参考
常用命令或 API 调用表。

## 程序
智能体遵循的逐步说明。

## 陷阱
已知的失败模式及如何处理。

## 验证
智能体确认其工作的方式。
```

### 平台特定技能

技能可以通过 `platforms` 前置字段声明支持哪些操作系统平台。具有此字段的技能会自动从不兼容平台上的系统提示、`skills_list()` 和斜杠命令中隐藏。

```yaml
platforms: [macos]            # 仅 macOS（例如 iMessage、Apple Reminders）
platforms: [macos, linux]     # macOS 和 Linux
platforms: [windows]          # 仅 Windows
```

如果字段被省略或为空，技能会在所有平台上加载（向后兼容）。参见 `skills/apple/` 了解仅 macOS 技能的示例。

### 条件技能激活

技能可以声明条件来控制它们何时出现在系统提示中，基于当前会话中哪些工具和工具集可用。这主要用于**回退技能** — 仅在主工具不可用时才显示的替代方案。

支持 `metadata.kclaw` 下的四个字段：

```yaml
metadata:
  kclaw:
    fallback_for_toolsets: [web]      # 仅在这些工具集不可用时显示
    requires_toolsets: [terminal]     # 仅在这些工具集可用时显示
    fallback_for_tools: [web_search]  # 仅在这些特定工具不可用时显示
    requires_tools: [terminal]        # 仅在这些特定工具可用时显示
```

**语义：**
- `fallback_for_*`：技能是备份。当列出的工具/工具集可用时，它**被隐藏**；当它们不可用时**被显示**。将这用于免费替代付费工具。
- `requires_*`：技能需要某些工具才能运行。当列出的工具/工具集不可用时，它**被隐藏**。将这用于依赖特定能力的技能（例如，只有在有终端访问时才有意义的技能）。
- 如果两者都指定，必须满足两个条件才能显示技能。
- 如果两者都未指定，技能总是显示（向后兼容）。

**示例：**

```yaml
# DuckDuckGo 搜索 — 当 Firecrawl（web 工具集）不可用时显示
metadata:
  kclaw:
    fallback_for_toolsets: [web]

# 智能家居技能 — 仅在终端可用时有用的
metadata:
  kclaw:
    requires_toolsets: [terminal]

# 本地浏览器回退 — 当 Browserbase 不可用时显示
metadata:
  kclaw:
    fallback_for_toolsets: [browser]
```

过滤在 `agent/prompt_builder.py` 中的提示构建时发生。`build_skills_system_prompt()` 函数从智能体接收可用工具和工具集，并使用 `_skill_should_show()` 评估每个技能的条件。

### 技能设置元数据

技能可以通过 `required_environment_variables` 前置字段声明安全设置时加载元数据。缺失值不会从发现中隐藏技能；当技能实际加载时，它们会触发仅 CLI 的安全提示。

```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: Tenor API 密钥
    help: 从 https://developers.google.com/tenor 获取
    required_for: 完整功能
```

用户可以跳过设置并继续加载技能。KClaw 仅向模型暴露元数据（`stored_as`、`skipped`、`validated`）— 永远不会暴露 secret 值。

旧的 `prerequisites.env_vars` 继续受支持，并被规范化为新的表示形式。

```yaml
prerequisites:
  env_vars: [TENOR_API_KEY]       # required_environment_variables 的旧别名
  commands: [curl, jq]            # 仅咨询 CLI 检查
```

网关和消息会话从不带内收集 secrets；它们指示用户运行 `kclaw setup` 或在本地更新 `~/.kclaw/.env`。

**何时声明所需环境变量：**
- 技能使用在加载时应安全收集的 API 密钥或令牌
- 如果用户跳过设置，技能仍然有用，但可能会优雅降级

**何时声明命令先决条件：**
- 技能依赖可能未安装的 CLI 工具（例如 `himalaya`、`openhue`、`ddgs`）
- 将命令检查视为指导，而不是发现时隐藏

参见 `skills/gifs/gif-search/` 和 `skills/email/himalaya/` 了解示例。

### 技能指南

- **除非绝对必要，否则不依赖外部依赖。** 优先使用标准库 Python、curl 和现有 KClaw 工具（`web_extract`、`terminal`、`read_file`）。
- **渐进式披露。** 把最常见的工作流放在首位。边缘情况和高级用法放在底部。
- **包含辅助脚本** 用于 XML/JSON 解析或复杂逻辑 — 不要期望 LLM 每次都内联编写解析器。
- **测试它。** 运行 `kclaw --toolsets skills -q "使用 X 技能做 Y"` 并验证智能体正确遵循说明。

---

## 添加皮肤 / 主题

KClaw 使用数据驱动的皮肤系统 — 添加新皮肤无需代码更改。

**选项 A：用户皮肤（YAML 文件）**

创建 `~/.kclaw/skins/<name>.yaml`：

```yaml
name: mytheme
description: 主题的简短描述

colors:
  banner_border: "#HEX"     # 面板边框颜色
  banner_title: "#HEX"      # 面板标题颜色
  banner_accent: "#HEX"     # 章节标题颜色
  banner_dim: "#HEX"        # 暗淡/暗文本颜色
  banner_text: "#HEX"       # 正文颜色
  response_border: "#HEX"   # 响应框边框

spinner:
  waiting_faces: ["(⚔)", "(⛨)"]
  thinking_faces: ["(⚔)", "(⌁)"]
  thinking_verbs: ["forging", "plotting"]
  wings:                     # 可选的左/右装饰
    - ["⟪⚔", "⚔⟫"]

branding:
  agent_name: "我的智能体"
  welcome: "欢迎消息"
  response_label: " ⚔ 智能体 "
  prompt_symbol: "⚔ ❯ "

tool_prefix: "╎"             # 工具输出行前缀
```

所有字段都是可选的 — 缺失值从默认皮肤继承。

**选项 B：内置皮肤**

添加到 `kclaw_cli/skin_engine.py` 中的 `_BUILTIN_SKINS` 字典。使用与上述相同的 schema，但作为 Python 字典。内置皮肤随包一起发布，始终可用。

**激活：**
- CLI：`/skin mytheme` 或在 config.yaml 中设置 `display.skin: mytheme`
- 配置：`display: { skin: mytheme }`

参见 `kclaw_cli/skin_engine.py` 了解完整 schema 和现有皮肤示例。

---

## 跨平台兼容性

KClaw 运行在 Linux、macOS 和 Windows 上。编写涉及操作系统的代码时：

### 关键规则

1. **`termios` 和 `fcntl` 仅限 Unix。** 始终捕获 `ImportError` 和 `NotImplementedError`：
   ```python
   try:
       from simple_term_menu import TerminalMenu
       menu = TerminalMenu(options)
       idx = menu.show()
   except (ImportError, NotImplementedError):
       # 回退：Windows 的编号菜单
       for i, opt in enumerate(options):
           print(f"  {i+1}. {opt}")
       idx = int(input("选择: ")) - 1
   ```

2. **文件编码。** Windows 可能以 `cp1252` 保存 `.env` 文件。始终处理编码错误：
   ```python
   try:
       load_dotenv(env_path)
   except UnicodeDecodeError:
       load_dotenv(env_path, encoding="latin-1")
   ```

3. **进程管理。** `os.setsid()`、`os.killpg()` 和信号处理在 Windows 上不同。使用平台检查：
   ```python
   import platform
   if platform.system() != "Windows":
       kwargs["preexec_fn"] = os.setsid
   ```

4. **路径分隔符。** 使用 `pathlib.Path` 而不是用 `/` 进行字符串连接。

5. **安装程序中的 shell 命令。** 如果更改 `scripts/install.sh`，检查是否需要在 `scripts/install.ps1` 中进行等效更改。

---

## 安全注意事项

KClaw 有终端访问权限。安全很重要。

### 现有保护

| 层 | 实现 |
|-------|---------------|
| **Sudo 密码管道** | 使用 `shlex.quote()` 防止 shell 注入 |
| **危险命令检测** | `tools/approval.py` 中的正则表达式模式，带用户审批流程 |
| **Cron 提示注入** | `tools/cronjob_tools.py` 中的扫描器阻止指令覆盖模式 |
| **写入拒绝列表** | 受保护路径（`~/.ssh/authorized_keys`、`/etc/shadow`）通过 `os.path.realpath()` 解析以防止符号链接绕过 |
| **技能守卫** | hub 安装技能的安全扫描器（`tools/skills_guard.py`）|
| **代码执行沙箱** | `execute_code` 子进程运行时从环境中剥离 API 密钥 |
| **容器加固** | Docker：丢弃所有能力，无权限提升，PID 限制，大小受限的 tmpfs |

### 贡献安全敏感代码时

- **始终使用 `shlex.quote()`** 将用户输入插入 shell 命令时
- **使用 `os.path.realpath()` 解析符号链接** 在基于路径的访问控制检查之前
- **不要记录 secrets。** API 密钥、令牌和密码不应出现在日志输出中
- **在工具执行周围捕获广泛异常**，以便单个失败不会导致智能体循环崩溃
- **在所有平台上测试**，如果您的更改涉及文件路径、进程管理或 shell 命令

如果您的 PR 影响安全，请在描述中明确说明。

---

## 拉取请求流程

### 分支命名

```
fix/描述        # Bug 修复
feat/描述       # 新功能
docs/描述       # 文档
test/描述       # 测试
refactor/描述   # 代码重构
```

### 提交前

1. **运行测试**：`pytest tests/ -v`
2. **手动测试**：运行 `kclaw` 并练习您更改的代码路径
3. **检查跨平台影响**：如果您触及文件 I/O、进程管理或终端处理，请考虑 Windows 和 macOS
4. **保持 PR 专注**：每个 PR 一个逻辑更改。不要将 bug 修复与重构和新功能混合。

### PR 描述

包括：
- **什么**变了以及**为什么**
- **如何测试**（对于 bug 的复现步骤，对于功能的使用示例）
- **您在哪些平台**上测试过
- 引用任何相关问题

### 提交消息

我们使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<类型>(<范围>): <描述>
```

| 类型 | 用于 |
|------|---------|
| `fix` | Bug 修复 |
| `feat` | 新功能 |
| `docs` | 文档 |
| `test` | 测试 |
| `refactor` | 代码重构（无行为更改）|
| `chore` | 构建、CI、依赖更新 |

范围：`cli`、`gateway`、`tools`、`skills`、`agent`、`install`、`whatsapp`、`security` 等。

示例：
```
fix(cli): 防止在 save_config_value 中模型为字符串时崩溃
feat(gateway): 添加 WhatsApp 多用户会话隔离
fix(security): 防止 sudo 密码管道中的 shell 注入
test(tools): 为 file_operations 添加单元测试
```

---

## 报告问题

- 使用 [GitHub Issues](https://github.com/kkutysllb/kk_KClaw/issues)
- 包括：操作系统、Python 版本、KClaw 版本（`kclaw version`）、完整错误跟踪
- 包括复现步骤
- 在创建重复项之前检查现有问题
- 对于安全漏洞，请私下报告

---

## 社区

- **Discord**: [discord.gg/NousResearch](https://discord.gg/NousResearch) — 用于提问、展示项目和分享技能
- **GitHub Discussions**: 用于设计提案和架构讨论
- **技能中心**：将专门技能上传到注册表并与社区分享

---

## 许可证

通过贡献，您同意您的贡献将在 [MIT 许可证](LICENSE) 下获得许可。
