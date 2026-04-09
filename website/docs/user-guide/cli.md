---
sidebar_position: 1
title: "CLI 界面"
description: "掌握 KClaw Agent 终端界面——命令、键绑定、人格等"
---

# CLI 界面

KClaw Agent 的 CLI 是一个完整的终端用户界面（TUI）——不是 Web UI。它具有多行编辑、斜杠命令自动完成、对话历史、中断和重定向以及流式工具输出。为在终端中工作的人构建。

## 运行 CLI

```bash
# 启动交互式会话（默认）
kclaw

# 单查询模式（非交互式）
kclaw chat -q "你好"

# 使用特定模型
kclaw chat --model "anthropic/claude-sonnet-4"

# 使用特定提供商
kclaw chat --provider nous        # 使用 Nous Portal
kclaw chat --provider openrouter  # 强制使用 OpenRouter

# 使用特定工具集
kclaw chat --toolsets "web,terminal,skills"

# 启动时预加载一个或多个技能
kclaw -s kclaw-dev,github-auth
kclaw chat -s github-pr-workflow -q "打开一个草稿 PR"

# 恢复上一个会话
kclaw --continue             # 恢复最近的 CLI 会话（-c）
kclaw --resume <session_id>  # 通过 ID 恢复特定会话（-r）

# 详细模式（调试输出）
kclaw chat --verbose

# 隔离的 git worktree（用于并行运行多个代理）
kclaw -w                         # 在 worktree 中的交互模式
kclaw -w -q "修复问题 #123"     # 在 worktree 中的单查询
```

## 界面布局

<img className="docs-terminal-figure" src="/img/docs/cli-layout.svg" alt="KClaw CLI 布局的样式化预览，显示横幅、对话区域和固定输入提示。" />
<p className="docs-figure-caption">KClaw CLI 横幅、对话流和固定输入提示渲染为稳定的文档图形，而非脆弱的文本艺术。</p>

欢迎横幅一目了然地显示您的模型、终端后端、工作目录、可用工具和已安装技能。

### 状态栏

输入区域上方有一个持久状态栏，实时更新：

```
 ⚕ claude-sonnet-4-20250514 │ 12.4K/200K │ [██████░░░░] 6% │ $0.06 │ 15m
```

| 元素 | 描述 |
|---------|-------------|
| 模型名称 | 当前模型（如果超过 26 个字符则截断） |
| 令牌计数 | 已使用/最大上下文窗口的上下文令牌 |
| 上下文栏 | 带颜色编码阈值的可视化填充指示器 |
| 成本 | 估计的会话成本（对于未知/零价格模型为 `n/a`） |
| 持续时间 | 经过的会话时间 |

栏适应终端宽度——在 ≥76 列时完整布局，在 52-75 列时紧凑，在 52 列以下最小（仅模型和持续时间）。

**上下文颜色编码：**

| 颜色 | 阈值 | 含义 |
|-------|-----------|---------|
| 绿色 | < 50% | 空间充足 |
| 黄色 | 50-80% | 越来越满 |
| 橙色 | 80-95% | 接近限制 |
| 红色 | ≥ 95% | 接近溢出——考虑 `/compress` |

使用 `/usage` 获取包括按类别成本（输入 vs 输出令牌）的详细细分。

### 会话恢复显示

恢复上一个会话时（`kclaw -c` 或 `kclaw --resume <id>`），横幅和输入提示之间会出现"上一个对话"面板，显示对话历史的紧凑摘要。请参阅[会话——恢复时的对话摘要](sessions.md#conversation-recap-on-resume)了解详细信息和配置。

## 键绑定

| 键 | 操作 |
|-----|--------|
| `Enter` | 发送消息 |
| `Alt+Enter` 或 `Ctrl+J` | 新行（多行输入） |
| `Alt+V` | 从剪贴板粘贴图像（当终端支持时） |
| `Ctrl+V` | 粘贴文本并有条件地附加剪贴板图像 |
| `Ctrl+B` | 启用语音模式时开始/停止语音录制（`voice.record_key`，默认：`ctrl+b`） |
| `Ctrl+C` | 中断代理（2 秒内双按强制退出） |
| `Ctrl+D` | 退出 |
| `Ctrl+Z` | 将 KClaw 挂起到后台（仅 Unix）。在 shell 中运行 `fg` 恢复。 |
| `Tab` | 接受自动建议（幽灵文本）或自动完成斜杠命令 |

## 斜杠命令

输入 `/` 查看自动完成下拉列表。KClaw 支持大量 CLI 斜杠命令、动态技能命令和用户定义的快速命令。

常见示例：

| 命令 | 描述 |
|---------|-------------|
| `/help` | 显示命令帮助 |
| `/model` | 显示或更改当前模型 |
| `/tools` | 列出当前可用的工具 |
| `/skills browse` | 浏览技能中心和官方可选技能 |
| `/background <prompt>` | 在单独的后台会话中运行提示 |
| `/skin` | 显示或切换活动 CLI 皮肤 |
| `/voice on` | 启用 CLI 语音模式（按 `Ctrl+B` 录制） |
| `/voice tts` | 切换 KClaw 回复的语音播放 |
| `/reasoning high` | 增加推理努力 |
| `/title My Session` | 命名当前会话 |

有关完整的内置 CLI 和消息传递列表，请参阅[斜杠命令参考](../reference/slash-commands.md)。

有关设置、提供商、沉默调整和消息/Discord 语音使用，请参阅[语音模式](features/voice-mode.md)。

:::tip
命令不区分大小写——`/HELP` 和 `/help` 一样工作。已安装的技能也会自动成为斜杠命令。
:::

## 快速命令

您可以定义在调用 LLM 时立即运行 shell 命令的自定义命令。这些在 CLI 和消息平台（Telegram、Discord 等）中都有效。

```yaml
# ~/.kclaw/config.yaml
quick_commands:
  status:
    type: exec
    command: systemctl status kclaw
  gpu:
    type: exec
    command: nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader
```

然后在任何聊天中输入 `/status` 或 `/gpu`。有关更多示例，请参阅[配置指南](/docs/user-guide/configuration#quick-commands)。

## 启动时预加载技能

如果您已经知道会话想要激活哪些技能，请在启动时传递它们：

```bash
kclaw -s kclaw-dev,github-auth
kclaw chat -s github-pr-workflow -s github-auth
```

KClaw 在第一轮之前将每个命名技能加载到会话提示中。相同标志在交互模式和单查询模式下都有效。

## 技能斜杠命令

`~/.kclaw/skills/` 中的每个已安装技能会自动注册为斜杠命令。技能名称成为命令：

```
/gif-search 有趣的猫
/axolotl 帮助我在我的数据集上微调 Llama 3
/github-pr-workflow 为身份验证重构创建一个 PR

# 只需技能名称加载它，让代理询问您需要什么：
/excalidraw
```

## 人格

设置预定义人格以更改代理的语气：

```
/personality pirate
/personality kawaii
/personality concise
```

内置人格包括：`helpful`、`concise`、`technical`、`creative`、`teacher`、`kawaii`、`catgirl`、`pirate`、`shakespeare`、`surfer`、`noir`、`uwu`、`philosopher`、`hype`。

您也可以在 `~/.kclaw/config.yaml` 中定义自定义人格：

```yaml
personalities:
  helpful: "你是一个有用、友好的 AI 助手。"
  kawaii: "你是一个可爱的助手！使用可爱的表情..."
  pirate: "啊！你在和船长 KClaw 说话..."
  # 添加你自己的！
```

## 多行输入

有两种方式输入多行消息：

1. **`Alt+Enter` 或 `Ctrl+J`** — 插入新行
2. **反斜杠继续** — 以 `\` 结束一行以继续：

```
❯ 写一个函数：\
  1. 获取数字列表\
  2. 返回总和
```

:::info
支持粘贴多行文本——使用 `Alt+Enter` 或 `Ctrl+J` 插入新行，或直接粘贴内容。
:::

## 中断代理

您可以在任何时候中断代理：

- **在代理工作时输入新消息 + Enter**——它中断并处理您的新指令
- **`Ctrl+C`**——中断当前操作（2 秒内双按强制退出）
- 正在进行的终端命令立即终止（SIGTERM，然后 1 秒后 SIGKILL）
- 在中断期间输入的多条消息合并为一个提示

### 忙碌输入模式

`display.busy_input_mode` 配置键控制当您在代理工作时按 Enter 时发生的情况：

| 模式 | 行为 |
|------|----------|
| `"interrupt"`（默认） | 您的消息中断当前操作并立即处理 |
| `"queue"` | 您的消息被静默排队，并在代理完成后作为下一轮发送 |

```yaml
# ~/.kclaw/config.yaml
display:
  busy_input_mode: "queue"   # 或 "interrupt"（默认）
```

队列模式在您想要准备后续消息而不必意外取消进行中的工作时很有用。未知值回退到 `"interrupt"`。

### 挂起到后台

在 Unix 系统上，按 **`Ctrl+Z`** 将 KClaw 挂起到后台——就像任何终端进程一样。shell 打印确认：

```
KClaw Agent 已挂起。运行 `fg` 恢复 KClaw Agent。
```

在 shell 中输入 `fg` 以精确恢复您离开的地方。不支持 Windows。

## 工具进度显示

CLI 在代理工作时显示动画反馈：

**思考动画**（API 调用期间）：
```
  ◜ (｡•́︿•̀｡) 思考中... (1.2s)
  ◠ (⊙_⊙) 思考中... (2.4s)
  ✧٩(ˊᗜˋ*)و ✧ 明白了！ (3.1s)
```

**工具执行反馈：**
```
  ┊ 💻 终端 `ls -la` (0.3s)
  ┊ 🔍 web_search (1.2s)
  ┊ 📄 web_extract (2.1s)
```

用 `/verbose` 循环切换显示模式：`关闭 → 新 → 全部 → 详细`。此命令也可以为消息平台启用——请参阅[配置](/docs/user-guide/configuration#display-settings)。

### 工具预览长度

`display.tool_preview_length` 配置键控制工具调用预览行中显示的最大字符数（例如文件路径、终端命令）。默认值为 `0`，表示无限制——显示完整路径和命令。

```yaml
# ~/.kclaw/config.yaml
display:
  tool_preview_length: 80   # 将工具预览截断为 80 个字符（0 = 无限制）
```

这在窄终端上或当工具参数包含非常长的文件路径时很有用。

## 会话管理

### 恢复会话

当您退出 CLI 会话时，会打印恢复命令：

```
恢复此会话：
  kclaw --resume 20260225_143052_a1b2c3

会话：        20260225_143052_a1b2c3
持续时间：       12m 34s
消息：       28（5 个用户，18 个工具调用）
```

恢复选项：

```bash
kclaw --continue                          # 恢复最近的 CLI 会话
kclaw -c                                  # 简短形式
kclaw -c "my project"                     # 恢复命名会话（最新世系）
kclaw --resume 20260225_143052_a1b2c3     # 通过 ID 恢复特定会话
kclaw --resume "refactoring auth"         # 通过标题恢复
kclaw -r 20260225_143052_a1b2c3           # 简短形式
```

恢复从 SQLite 恢复完整对话历史。代理看到所有先前的消息、工具调用和响应——就像您从未离开过一样。

在聊天中使用 `/title My Session Name` 命名当前会话，或从命令行使用 `kclaw sessions rename <id> <title>`。使用 `kclaw sessions list` 浏览过去的会话。

### 会话存储

CLI 会话存储在 `~/.kclaw/state.db` 中的 KClaw SQLite 状态数据库中。数据库保留：

- 会话元数据（ID、标题、时间戳、令牌计数器）
- 消息历史
- 压缩/恢复会话的世系
- `session_search` 使用的全文搜索索引

一些消息适配器也在数据库旁边保留每个平台的转录文件，但 CLI 本身从 SQLite 会话存储恢复。

### 上下文压缩

当接近上下文限制时，长对话会自动摘要：

```yaml
# 在 ~/.kclaw/config.yaml
compression:
  enabled: true
  threshold: 0.50    # 默认在上下文限制的 50% 时压缩
  summary_model: "google/gemini-3-flash-preview"  # 用于摘要的模型
```

当压缩触发时，中间轮次被摘要，而前 3 轮和后 4 轮始终保留。

## 后台会话

在单独的后台会话中运行提示，同时继续使用 CLI 进行其他工作：

```
/background 分析 /var/log 中的日志并总结今天的任何错误
```

KClaw 立即确认任务并返回提示：

```
🔄 后台任务 #1 已启动："分析 /var/log 中的日志并总结..."
   任务 ID：bg_143022_a1b2c3
```

### 工作原理

每个 `/background` 提示生成一个**完全独立的代理会话**在守护线程中：

- **隔离对话** — 后台代理不知道您当前会话的历史。它仅接收您提供的提示。
- **相同配置** — 后台代理从当前会话继承您的模型、提供商、工具集、推理设置和回退模型。
- **非阻塞** — 您的前台会话保持完全交互式。您可以聊天、运行命令，甚至启动更多后台任务。
- **多个任务** — 您可以同时运行多个后台任务。每个获得一个编号 ID。

### 结果

当后台任务完成时，结果显示为终端中的面板：

```
╭─ ⚕ KClaw（后台 #1）─────────────────────────────────╮
│ 在今天的 syslog 中发现 3 个错误：                         │
│ 1. OOM killer 在 03:22 调用——杀死进程 nginx        │
│ 2. /dev/sda1 在 07:15 磁盘 I/O 错误                  │
│ 3. 14:30 时来自 192.168.1.50 的失败 SSH 登录尝试    │
╰──────────────────────────────────────────────────────────╯
```

如果任务失败，您会看到错误通知。如果在您的配置中启用了 `display.bell_on_complete`，任务完成时终端铃声会响。

### 用例

- **长时间运行的研究** — "/background 研究量子纠错的最新发展" 而您处理代码
- **文件处理** — "/background 分析此仓库中的所有 Python 文件并列出任何安全问题" 而您继续对话
- **并行调查** — 启动多个后台任务同时探索不同角度

:::info
后台会话不会出现在您的主要对话历史中。它们是具有自己任务 ID（例如 `bg_143022_a1b2c3`）的独立会话。
:::

## 安静模式

默认情况下，CLI 以安静模式运行：
- 抑制工具的详细日志
- 启用可爱风格的动画反馈
- 保持输出干净和用户友好

用于调试输出：
```bash
kclaw chat --verbose
```
