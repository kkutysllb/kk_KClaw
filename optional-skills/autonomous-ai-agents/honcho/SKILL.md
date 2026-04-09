---
name: honcho
description: 配置和使用Honcho记忆与KClaw — 跨会话用户建模、多profile对等隔离、观察配置和辩证推理。当设置Honcho、排除记忆故障、管理具有Honcho对等的profile或调优观察和召回设置时使用。
version: 1.0.0
author: KClaw Agent
license: MIT
metadata:
  kclaw:
    tags: [Honcho, 记忆, Profiles, 观察, 辩证, 用户建模]
    homepage: https://docs.honcho.dev
    related_skills: [kclaw]
prerequisites:
  pip: [honcho-ai]
---

# KClaw的Honcho记忆

Honcho提供AI原生的跨会话用户建模。它跨对话学习用户是谁，并为每个KClaw profile提供自己的对等身份，同时共享用户的统一视图。

## 何时使用

- 设置Honcho（云或自托管）
- 排除记忆不工作/对等不同步的故障
- 创建每个代理有自己的Honcho对等的多profile设置
- 调优观察、召回或写入频率设置
- 理解4个Honcho工具的作用及何时使用它们

## 设置

### 云（app.honcho.dev）

```bash
kclaw honcho setup
# 选择"cloud"，粘贴来自 https://app.honcho.dev 的API密钥
```

### 自托管

```bash
kclaw honcho setup
# 选择"local"，输入基础URL（例如 http://localhost:8000）
```

参见：https://docs.honcho.dev/v3/guides/integrations/kclaw#running-honcho-locally-with-kclaw

### 验证

```bash
kclaw honcho status    # 显示解析后的配置、连接测试、对等信息
```

## 架构

### 对等

Honcho将对话建模为**对等**之间的交互。KClaw每个会话创建两个对等：

- **用户对等**（`peerName`）：代表人类。Honcho从观察到的消息中构建用户表示。
- **AI对等**（`aiPeer`）：代表这个KClaw实例。每个profile获得自己的AI对等，以便代理发展独立视图。

### 观察

每个对等有两个观察开关，控制Honcho从中学习的内容：

| 开关 | 作用 |
|--------|-------------|
| `observeMe` | 观察对等自己的消息（构建自我表示） |
| `observeOthers` | 观察其他对等的消息（构建跨对等理解） |

默认：所有四个开关**开**（完全双向观察）。

在`honcho.json`中配置每个对等：

```json
{
  "observation": {
    "user": { "observeMe": true, "observeOthers": true },
    "ai":   { "observeMe": true, "observeOthers": true }
  }
}
```

或使用简写预设：

| 预设 | 用户 | AI | 用例 |
|--------|------|----|----------|
| `"directional"`（默认） | me:开, others:开 | me:开, others:开 | 多代理，完全记忆 |
| `"unified"` | me:开, others:关 | me:关, others:开 | 单代理，仅用户建模 |

在[Honcho仪表板](https://app.honcho.dev)中更改的设置在会话初始化时同步回 — 服务器端配置优先于本地默认值。

### 会话

Honcho会话限定消息和观察落在何处。策略选项：

| 策略 | 行为 |
|----------|----------|
| `per-directory`（默认） | 每个工作目录一个会话 |
| `per-repo` | 每个git仓库根一个会话 |
| `per-session` | 每次KClaw运行一个新的Honcho会话 |
| `global` | 跨所有目录的单一会话 |

手动覆盖：`kclaw honcho map my-project-name`

### 召回模式

代理如何访问Honcho记忆：

| 模式 | 自动注入上下文？ | 工具可用？ | 用例 |
|------|---------------------|-----------------|----------|
| `hybrid`（默认） | 是 | 是 | 代理决定何时使用工具vs自动上下文 |
| `context` | 是 | 否（隐藏） | 最小token成本，无工具调用 |
| `tools` | 否 | 是 | 代理显式控制所有记忆访问 |

## 多Profile设置

每个KClaw profile获得自己的Honcho AI对等，同时共享相同的工作区（用户上下文）。这意味着：

- 所有profile看到相同的用户表示
- 每个profile构建自己的AI身份和观察
- 一个profile写的结论可通过共享工作区对其他profile可见

### 创建带Honcho对等的profile

```bash
kclaw profile create coder --clone
# 创建主机块kclaw.coder，AI对等"coder"，从默认继承配置
```

`--clone`对Honcho的作用：
1. 在`honcho.json`中创建`kclaw.coder`主机块
2. 设置`aiPeer: "coder"`（profile名称）
3. 从默认继承`workspace`、`peerName`、`writeFrequency`、`recallMode`等
4. 热切地在Honcho中创建对等，以便它在第一条消息之前存在

### 回填现有profiles

```bash
kclaw honcho sync    # 为所有还没有主机块的profiles创建主机块
```

### 每个profile的配置

在主机块中覆盖任何设置：

```json
{
  "hosts": {
    "kclaw.coder": {
      "aiPeer": "coder",
      "recallMode": "tools",
      "observation": {
        "user": { "observeMe": true, "observeOthers": false },
        "ai": { "observeMe": true, "observeOthers": true }
      }
    }
  }
}
```

## 工具

代理有4个Honcho工具（在`context`召回模式下隐藏）：

### `honcho_profile`
用户的快速事实快照 — 名称、角色、偏好、模式。无LLM调用，最低成本。用于对话开始或快速查找。

### `honcho_search`
在存储的上下文上进行语义搜索。返回按相关性排序的原始摘录，无LLM综合。默认800 token，最大2000。用于当您想要特定的过去事实来自己推理时。

### `honcho_context`
由Honcho后端的辩证推理（LLM调用）回答的自然语言问题。更高成本，更高质量。可查询用户（默认）或AI对等。

### `honcho_conclude`
写下关于用户的持久事实。结论随时间构建用户的profile。当用户声明偏好、纠正您或分享要记住的内容时使用。

## 配置参考

配置文件：`$KCLAW_HOME/honcho.json`（profile本地）或`~/.honcho/config.json`（全局）。

### 关键设置

| 键 | 默认 | 描述 |
|-----|---------|-------------|
| `apiKey` | -- | API密钥（[获取一个](https://app.honcho.dev)） |
| `baseUrl` | -- | 自托管Honcho的基础URL |
| `peerName` | -- | 用户对等身份 |
| `aiPeer` | 主机键 | AI对等身份 |
| `workspace` | 主机键 | 共享工作区ID |
| `recallMode` | `hybrid` | `hybrid`、`context`或`tools` |
| `observation` | 全部开启 | 每个对等的`observeMe`/`observeOthers`布尔值 |
| `writeFrequency` | `async` | `async`、`turn`、`session`或整数N |
| `sessionStrategy` | `per-directory` | `per-directory`、`per-repo`、`per-session`、`global` |
| `dialecticReasoningLevel` | `low` | `minimal`、`low`、`medium`、`high`、`max` |
| `dialecticDynamic` | `true` | 按查询长度自动提升推理。`false` = 固定级别 |
| `messageMaxChars` | `25000` | 每条消息最大字符数（超过则分块） |
| `dialecticMaxInputChars` | `10000` | 辩证查询输入的最大字符数 |

### 成本意识（高级，仅根配置）

| 键 | 默认 | 描述 |
|-----|---------|-------------|
| `injectionFrequency` | `every-turn` | `every-turn`或`first-turn` |
| `contextCadence` | `1` | 上下文API调用之间的最小轮次 |
| `dialecticCadence` | `1` | 辩证API调用之间的最小轮次 |

## 故障排除

### "Honcho未配置"
运行`kclaw honcho setup`。确保`~/.kclaw/config.yaml`中有`memory.provider: honcho`。

### 记忆跨会话不持久
检查`kclaw honcho status` -- 验证`saveMessages: true`且`writeFrequency`不是`session`（仅在退出时写入）。

### Profile未获得自己的对等
创建时使用`--clone`：`kclaw profile create <name> --clone`。对于现有profiles：`kclaw honcho sync`。

### 仪表板中的观察更改未反映
观察配置在每个会话初始化时从服务器同步。在Honcho UI中更改设置后开始新会话。

### 消息被截断
超过`messageMaxChars`（默认25k）的消息会自动分块并带有`[continued]`标记。如果经常遇到这种情况，检查工具结果或技能内容是否膨胀了消息大小。

## CLI命令

| 命令 | 描述 |
|---------|-------------|
| `kclaw honcho setup` | 交互式设置向导（云/本地、身份、观察、召回、会话） |
| `kclaw honcho status` | 显示活动profile的解析配置、连接测试、对等信息 |
| `kclaw honcho enable` | 为活动profile启用Honcho（如需要则创建主机块） |
| `kclaw honcho disable` | 为活动profile禁用Honcho |
| `kclaw honcho peer` | 显示或更新对等名称（`--user <name>`、`--ai <name>`、`--reasoning <level>`） |
| `kclaw honcho peers` | 显示所有profiles的对等身份 |
| `kclaw honcho mode` | 显示或设置召回模式（`hybrid`、`context`、`tools`） |
| `kclaw honcho tokens` | 显示或设置token预算（`--context <N>`、`--dialectic <N>`） |
| `kclaw honcho sessions` | 列出已知的目录到会话名映射 |
| `kclaw honcho map <name>` | 将当前工作目录映射到Honcho会话名 |
| `kclaw honcho identity` | 种子AI对等身份或显示两个对等表示 |
| `kclaw honcho sync` | 为所有还没有主机块的KClaw profiles创建主机块 |
| `kclaw honcho migrate` | 从OpenClaw原生记忆到KClaw + Honcho的分步迁移指南 |
| `kclaw memory setup` | 通用记忆提供者选择器（选择"honcho"运行相同的向导） |
| `kclaw memory status` | 显示活动记忆提供者和配置 |
| `kclaw memory off` | 禁用外部记忆提供者 |
