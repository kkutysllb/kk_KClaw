---
sidebar_position: 2
title: "技能系统"
description: "按需加载的知识文档——渐进式披露、代理管理的技能和技能中心"
---

# 技能系统

技能是代理可以在需要时加载的按需知识文档。它们遵循**渐进式披露**模式以最小化令牌使用，并兼容 [agentskills.io](https://agentskills.io/specification) 开放标准。

所有技能都存放在 **`~/.kclaw/skills/`** ——主要目录和真实来源。在全新安装时，捆绑的技能从仓库复制。中心安装和代理创建的技能也放在这里。代理可以修改或删除任何技能。

您还可以让 KClaw 指向**外部技能目录**——与本地目录一起扫描的额外文件夹。参见下面的[外部技能目录](#external-skill-directories)。

另请参阅：

- [捆绑技能目录](/docs/reference/skills-catalog)
- [官方可选技能目录](/docs/reference/optional-skills-catalog)

## 使用技能

每个已安装的技能都会自动作为斜杠命令可用：

```bash
# 在 CLI 或任何消息平台上：
/gif-search funny cats
/axolotl help me fine-tune Llama 3 on my dataset
/github-pr-workflow create a PR for the auth refactor
/plan design a rollout for migrating our auth provider

# 只需技能名称加载它，让代理询问您需要什么：
/excalidraw
```

捆绑的 `plan` 技能是一个很好的技能支持的斜杠命令示例，具有自定义行为。运行 `/plan [request]` 告诉 KClaw 在需要时检查上下文，编写 markdown 实现计划而不是执行任务，并将结果保存到活动工作区/后端工作目录下的 `.kclaw/plans/` 中。

您也可以通过自然对话与技能交互：

```bash
kclaw chat --toolsets skills -q "What skills do you have?"
kclaw chat --toolsets skills -q "Show me the axolotl skill"
```

## 渐进式披露

技能使用令牌高效加载模式：

```
Level 0: skills_list()           → [{name, description, category}, ...]   (~3k tokens)
Level 1: skill_view(name)        → 完整内容 + 元数据           (varies)
Level 2: skill_view(name, path)  → 特定参考文件           (varies)
```

代理仅在真正需要时才加载完整的技能内容。

## SKILL.md 格式

```markdown
---
name: my-skill
description: Brief description of what this skill does
version: 1.0.0
platforms: [macos, linux]     # Optional — restrict to specific OS platforms
metadata:
  kclaw:
    tags: [python, automation]
    category: devops
    fallback_for_toolsets: [web]    # Optional — conditional activation (see below)
    requires_toolsets: [terminal]   # Optional — conditional activation (see below)
    config:                          # Optional — config.yaml settings
      - key: my.setting
        description: "What this controls"
        default: "value"
        prompt: "Prompt for setup"
---

# Skill Title

## When to Use
Trigger conditions for this skill.

## Procedure
1. Step one
2. Step two

## Pitfalls
- Known failure modes and fixes

## Verification
How to confirm it worked.
```

### 平台特定技能

技能可以使用 `platforms` 字段将自己限制在特定操作系统：

| 值 | 匹配 |
|-------|---------|
| `macos` | macOS (Darwin) |
| `linux` | Linux |
| `windows` | Windows |

```yaml
platforms: [macos]            # 仅 macOS（例如 iMessage、Apple Reminders、FindMy）
platforms: [macos, linux]     # macOS 和 Linux
```

设置后，技能会自动从不兼容平台上的系统提示、`skills_list()` 和斜杠命令中隐藏。如果省略，技能会在所有平台上加载。

### 条件激活（备用技能）

技能可以根据当前会话中哪些工具可用自动显示或隐藏自己。这对于**备用技能**最有用——免费或本地替代方案，只应在高级工具不可用时出现。

```yaml
metadata:
  kclaw:
    fallback_for_toolsets: [web]      # 仅在这些工具集不可用时显示
    requires_toolsets: [terminal]     # 仅在这些工具集可用时显示
    fallback_for_tools: [web_search]  # 仅在这些特定工具不可用时显示
    requires_tools: [terminal]        # 仅在这些特定工具可用时显示
```

| 字段 | 行为 |
|-------|---------|
| `fallback_for_toolsets` | 当列出的工具集可用时，技能**隐藏**。缺失时显示。 |
| `fallback_for_tools` | 相同，但检查单个工具而不是工具集。 |
| `requires_toolsets` | 当列出的工具集不可用时，技能**隐藏**。存在时显示。 |
| `requires_tools` | 相同，但检查单个工具。 |

**示例：** 内置的 `duckduckgo-search` 技能使用 `fallback_for_toolsets: [web]`。当您设置了 `FIRECRAWL_API_KEY` 时，web 工具集可用，代理使用 `web_search`——DuckDuckGo 技能保持隐藏。如果 API 密钥缺失，web 工具集不可用，DuckDuckGo 技能会自动显示为备用。

没有任何条件字段的技能行为与以前完全相同——它们始终显示。

## 加载时的安全设置

技能可以声明所需的环境变量而不会从发现中消失：

```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: Tenor API key
    help: Get a key from https://developers.google.com/tenor
    required_for: full functionality
```

当遇到缺失值时，KClaw 仅在实际在本地 CLI 加载技能时才安全地询问它。您可以跳过设置并继续使用技能。消息平台永远不会在聊天中询问秘密——它们告诉您使用 `kclaw setup` 或 `~/.kclaw/.env` 本地代替。

设置后，声明的环境变量**自动传递**到 `execute_code` 和 `terminal` 沙箱——技能的脚本可以直接使用 `$TENOR_API_KEY`。对于非技能环境变量，请使用 `terminal.env_passthrough` 配置选项。参见[环境变量传递](/docs/user-guide/security#environment-variable-passthrough)了解详情。

### 技能配置设置

技能还可以声明存储在 `config.yaml` 中的非秘密配置设置（路径、偏好）：

```yaml
metadata:
  kclaw:
    config:
      - key: wiki.path
        description: Path to the wiki directory
        default: "~/wiki"
        prompt: Wiki directory path
```

设置存储在 config.yaml 的 `skills.config` 下。`kclaw config migrate` 提示未配置的项目，`kclaw config show` 显示它们。当技能加载时，其解析的配置值被注入上下文，以便代理自动知道配置的值。

参见[技能设置](/docs/user-guide/configuration#skill-settings)和[创建技能——配置设置](/docs/developer-guide/creating-skills#config-settings-configyaml)了解详情。

## 技能目录结构

```text
~/.kclaw/skills/                  # 单一真实来源
├── mlops/                         # 类别目录
│   ├── axolotl/
│   │   ├── SKILL.md               # 主要说明（必需）
│   │   ├── references/            # 附加文档
│   │   ├── templates/             # 输出格式
│   │   ├── scripts/               # 可从技能调用的辅助脚本
│   │   └── assets/                # 补充文件
│   └── vllm/
│       └── SKILL.md
├── devops/
│   └── deploy-k8s/                # 代理创建的技能
│       ├── SKILL.md
│       └── references/
├── .hub/                          # 技能中心状态
│   ├── lock.json
│   ├── quarantine/
│   └── audit.log
└── .bundled_manifest              # 跟踪植入的捆绑技能
```

## 外部技能目录

如果您在 KClaw 之外维护技能——例如，多个 AI 工具使用的共享 `~/.agents/skills/` 目录——您可以告诉 KClaw 也扫描这些目录。

在 `~/.kclaw/config.yaml` 的 `skills` 部分下添加 `external_dirs`：

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
    - /home/shared/team-skills
    - ${SKILLS_REPO}/skills
```

路径支持 `~` 展开和 `${VAR}` 环境变量替换。

### 工作原理

- **只读**：外部目录仅用于技能发现扫描。当代理创建或编辑技能时，它始终写入 `~/.kclaw/skills/`。
- **本地优先**：如果同一技能名称同时存在于本地目录和外部目录中，本地版本优先。
- **完全集成**：外部技能出现在系统提示索引、`skills_list`、`skill_view` 和 `/skill-name` 斜杠命令中——与本地技能无异。
- **不存在的路径被静默跳过**：如果配置的目录不存在，KClaw 忽略它而不会出错。对可能不在每台机器上存在的可选共享目录有用。

### 示例

```text
~/.kclaw/skills/               # 本地（主要，可读写）
├── devops/deploy-k8s/
│   └── SKILL.md
└── mlops/axolotl/
    └── SKILL.md

~/.agents/skills/               # 外部（只读，共享）
├── my-custom-workflow/
│   └── SKILL.md
└── team-conventions/
    └── SKILL.md
```

所有四个技能都出现在您的技能索引中。如果您在本地创建一个名为 `my-custom-workflow` 的新技能，它会遮盖外部版本。

## 代理管理的技能（skill_manage 工具）

代理可以通过 `skill_manage` 工具创建、更新和删除自己的技能。这是代理的**程序记忆**——当它弄清楚一个非平凡的工作流时，它将方法保存为技能以供将来重用。

### 代理何时创建技能

- 成功完成复杂任务后（5+ 工具调用）
- 当它遇到错误或死胡同并找到可行路径时
- 当用户纠正了它的方法时
- 当它发现一个非平凡的工作流时

### 操作

| 操作 | 用于 | 关键参数 |
|--------|---------|------------|
| `create` | 从头开始新技能 | `name`, `content`（完整 SKILL.md），可选 `category` |
| `patch` | 定向修复（首选） | `name`, `old_string`, `new_string` |
| `edit` | 主要结构性重写 | `name`, `content`（完整 SKILL.md 替换） |
| `delete` | 完全删除技能 | `name` |
| `write_file` | 添加/更新支持文件 | `name`, `file_path`, `file_content` |
| `remove_file` | 删除支持文件 | `name`, `file_path` |

:::tip
`patch` 操作是更新的首选——它比 `edit` 更节省令牌，因为只有更改的文本出现在工具调用中。
:::

## 技能中心

浏览、搜索、安装和管理来自在线注册中心、`skills.sh`、直接众所周知的技能端点以及官方可选技能的技能。

### 常用命令

```bash
kclaw skills browse                              # 浏览所有中心技能（官方优先）
kclaw skills browse --source official            # 仅浏览官方可选技能
kclaw skills search kubernetes                   # 搜索所有来源
kclaw skills search react --source skills-sh     # 搜索 skills.sh 目录
kclaw skills search https://mintlify.com/docs --source well-known
kclaw skills inspect openai/skills/k8s           # 安装前预览
kclaw skills install openai/skills/k8s           # 带安全扫描安装
kclaw skills install official/security/1password
kclaw skills install skills-sh/vercel-labs/json-render/json-render-react --force
kclaw skills install well-known:https://mintlify.com/docs/.well-known/skills/mintlify
kclaw skills list --source hub                   # 列出中心安装的技能
kclaw skills check                               # 检查已安装的中心技能是否有上游更新
kclaw skills update                              # 需要时用上游更改重新安装中心技能
kclaw skills audit                               # 重新扫描所有中心技能的安全
kclaw skills uninstall k8s                       # 移除中心技能
kclaw skills publish skills/my-skill --to github --repo owner/repo
kclaw skills snapshot export setup.json          # 导出技能配置
kclaw skills tap add myorg/skills-repo           # 添加自定义 GitHub 来源
```

### 支持的中心来源

| 来源 | 示例 | 备注 |
|--------|--------|-------|
| `official` | `official/security/1password` | 与 KClaw 一起提供的可选技能。 |
| `skills-sh` | `skills-sh/vercel-labs/agent-skills/vercel-react-best-practices` | 可通过 `kclaw skills search <query> --source skills-sh` 搜索。当 skills.sh slug 与 repo 文件夹不同时，KClaw 解析别名风格技能。 |
| `well-known` | `well-known:https://mintlify.com/docs/.well-known/skills/mintlify` | 直接从网站的 `/.well-known/skills/index.json` 提供技能。不是单一集中中心——而是网络发现约定。 |
| `github` | `openai/skills/k8s` | 直接 GitHub repo/path 安装和自定义 tap。 |
| `clawhub`, `lobehub`, `claude-marketplace` | 特定于来源的标识符 | 社区或市场集成。 |

### 集成中心和注册表

KClaw 目前与这些技能生态系统和发现来源集成：

#### 1. 官方可选技能（`official`）

这些在 KClaw 仓库本身中维护，安装时具有内置信任。

- 目录：[官方可选技能目录](../../reference/optional-skills-catalog)
- Repo 中的来源：`optional-skills/`
- 示例：

```bash
kclaw skills browse --source official
kclaw skills install official/security/1password
```

#### 2. skills.sh（`skills-sh`）

这是 Vercel 的公共技能目录。KClaw 可以直接搜索它、检查技能详情页面、解析别名风格 slug，并从底层源 repo 安装。

- 目录：[skills.sh](https://skills.sh/)
- CLI/工具 repo：[vercel-labs/skills](https://github.com/vercel-labs/skills)
- 官方 Vercel 技能 repo：[vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills)
- 示例：

```bash
kclaw skills search react --source skills-sh
kclaw skills inspect skills-sh/vercel-labs/json-render/json-render-react
kclaw skills install skills-sh/vercel-labs/json-render/json-render-react --force
```

#### 3. 众所周知的技能端点（`well-known`）

这是来自发布 `/.well-known/skills/index.json` 的网站的基于 URL 的发现。它不是单一集中中心——而是一种网络发现约定。

- 示例实时端点：[Mintlify docs skills index](https://mintlify.com/docs/.well-known/skills/index.json)
- 参考服务器实现：[vercel-labs/skills-handler](https://github.com/vercel-labs/skills-handler)
- 示例：

```bash
kclaw skills search https://mintlify.com/docs --source well-known
kclaw skills inspect well-known:https://mintlify.com/docs/.well-known/skills/mintlify
kclaw skills install well-known:https://mintlify.com/docs/.well-known/skills/mintlify
```

#### 4. 直接 GitHub 技能（`github`）

KClaw 可以直接从 GitHub 仓库和基于 GitHub 的 tap 安装。当您已经知道 repo/path 或想添加自己的自定义源 repo 时，这很有用。

默认 tap（无需任何设置即可浏览）：
- [openai/skills](https://github.com/openai/skills)
- [anthropics/skills](https://github.com/anthropics/skills)
- [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills)
- [garrytan/gstack](https://github.com/garrytan/gstack)

- 示例：

```bash
kclaw skills install openai/skills/k8s
kclaw skills tap add myorg/skills-repo
```

#### 5. ClawHub（`clawhub`）

作为社区来源集成的第三方技能市场。

- 网站：[clawhub.ai](https://clawhub.ai/)
- KClaw 来源 ID：`clawhub`

#### 6. Claude 市场风格仓库（`claude-marketplace`）

KClaw 支持发布 Claude 兼容插件/市场清单的市场仓库。

已知的集成来源包括：
- [anthropics/skills](https://github.com/anthropics/skills)
- [aiskillstore/marketplace](https://github.com/aiskillstore/marketplace)

KClaw 来源 ID：`claude-marketplace`

#### 7. LobeHub（`lobehub`）

KClaw 可以从 LobeHub 的公共目录搜索和转换代理条目为可安装的 KClaw 技能。

- 网站：[LobeHub](https://lobehub.com/)
- 公共代理索引：[chat-agents.lobehub.com](https://chat-agents.lobehub.com/)
- 支持 repo：[lobehub/lobe-chat-agents](https://github.com/lobehub/lobe-chat-agents)
- KClaw 来源 ID：`lobehub`

### 安全扫描和 `--force`

所有中心安装的技能都要经过**安全扫描器**，检查数据泄露、提示注入、破坏性命令、供应链信号和其他威胁。

`kclaw skills inspect ...` 现在也在可用时显示上游元数据：
- repo URL
- skills.sh 详情页 URL
- 安装命令
- 每周安装数
- 上游安全审计状态
- 众所周知的索引/端点 URL

当您审查了第三方技能并想覆盖非危险策略阻止时使用 `--force`：

```bash
kclaw skills install skills-sh/anthropics/skills/pdf --force
```

重要行为：
- `--force` 可以覆盖 caution/warn 风格发现的策略阻止。
- `--force` **不会**覆盖 `dangerous` 扫描裁决。
- 官方可选技能（`official/...`）被视为内置信任，不显示第三方警告面板。

### 信任级别

| 级别 | 来源 | 策略 |
|-------|--------|--------|
| `builtin` | KClaw 附带 | 始终信任 |
| `official` | Repo 中的 `optional-skills/` | 内置信任，无第三方警告 |
| `trusted` | 信任的注册表/repos 如 `openai/skills`、`anthropics/skills` | 比社区来源更宽松的策略 |
| `community` | 其他一切（`skills.sh`、well-known 端点、自定义 GitHub repos、大多数市场） | 非危险发现可以用 `--force` 覆盖；`dangerous` 裁决保持阻止 |

### 更新生命周期

中心现在跟踪足够的来源信息来重新检查已安装技能的上游副本：

```bash
kclaw skills check          # 报告哪些已安装的中心技能在上游有更改
kclaw skills update         # 仅重新安装有可用更新的技能
kclaw skills update react   # 更新一个特定的已安装中心技能
```

这使用存储的源标识符加上当前上游包内容哈希来检测漂移。

### 斜杠命令（在聊天中）

所有相同的命令都可以用 `/skills`：

```text
/skills browse
/skills search react --source skills-sh
/skills search https://mintlify.com/docs --source well-known
/skills inspect skills-sh/vercel-labs/json-render/json-render-react
/skills install openai/skills/skill-creator --force
/skills check
/skills update
/skills list
```

官方可选技能仍使用像 `official/security/1password` 和 `official/migration/openclaw-migration` 这样的标识符。
