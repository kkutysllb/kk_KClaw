# honcho 集成规范

KClaw Agent 与 openclaw-honcho 的对比 — 以及将 KClaw 模式移植到其他 Honcho 集成的规范。

---

## 概述

两个独立的 Honcho 集成已为两个不同的代理运行时构建：**KClaw Agent**（Python，内置于运行器）和 **openclaw-honcho**（通过 hook/工具 API 的 TypeScript 插件）。两者使用相同的 Honcho 对等模式 — 双对等模型、`session.context()`、`peer.chat()` — 但在每个层次做出了不同的权衡。

本文档映射了这些权衡并定义了一个移植规范：一组 KClaw 起源的模式，每个模式都表述为与集成无关的接口，任何 Honcho 集成都可以采用，无论运行时或语言。

> **范围** 两个集成目前都能正常工作。本规范是关于增量 — KClaw 中值得推广的模式和 openclaw-honcho 中 KClaw 最终应采用的模式。规范是附加的，不是规定性的。

---

## 架构对比

### KClaw：内置运行器

Honcho 直接在 `AIAgent.__init__` 中初始化。没有插件边界。会话管理、上下文注入、异步预取和 CLI 界面都是运行器的一等公民。上下文每会话注入一次（烘焙到 `_cached_system_prompt`），在会话中期从不重新获取 — 这在 LLM 提供商处最大化前缀缓存命中。

轮次流程：

```
用户消息
  → _honcho_prefetch()       （读取缓存 — 无 HTTP）
  → _build_system_prompt()   （仅第一轮，缓存）
  → LLM 调用
  → 响应
  → _honcho_fire_prefetch()  （守护线程，轮次结束）
       → prefetch_context() 线程  ──┐
       → prefetch_dialectic() 线程 ─┴→ _context_cache / _dialectic_cache
```

### openclaw-honcho：基于 hook 的插件

插件针对 OpenClaw 的事件总线注册 hook。上下文在每个轮次的 `before_prompt_build` 内同步获取。消息捕获发生在 `agent_end`。多代理层次通过 `subagent_spawned` 跟踪。此模型是正确的，但每个轮次在 LLM 调用开始前都要支付阻塞的 Honcho 往返。

轮次流程：

```
用户消息
  → before_prompt_build（阻塞 HTTP — 每个轮次）
       → session.context()
  → 系统提示组装
  → LLM 调用
  → 响应
  → agent_end hook
       → session.addMessages()
       → session.setMetadata()
```

---

## 差异表

| 维度 | KClaw Agent | openclaw-honcho |
|---|---|---|
| **上下文注入时机** | 每会话一次（缓存）。第一轮后响应路径零 HTTP。 | 每个轮次，阻塞。每轮新上下文但增加延迟。 |
| **预取策略** | 守护线程在轮次结束触发；下一轮从缓存消费。 | 无。提示构建时的阻塞调用。 |
| **辩证法（peer.chat）** | 异步预取；结果在下一轮注入系统提示。 | 通过 `honcho_recall` / `honcho_analyze` 工具按需调用。 |
| **推理级别** | 动态：随消息长度缩放。底部 = 配置默认值。顶部 = "high"。 | 每个工具固定：recall=minimal，analyze=medium。 |
| **记忆模式** | `user_memory_mode` / `agent_memory_mode`：hybrid / honcho / local。 | 无。始终写入 Honcho。 |
| **写入频率** | async（后台队列）、turn、session、N 轮。 | 每个 agent_end 后（无控制）。 |
| **AI 对等身份** | `observe_me=True`、`seed_ai_identity()`、`get_ai_representation()`、SOUL.md → AI 对等。 | 设置时上传到 AI 对等的代理文件。无持续的自我观察。 |
| **上下文范围** | 用户对等 + AI 对等表示，均注入。 | 用户对等（所有者）表示 + 对话摘要。上下文调用上的 `peerPerspective`。 |
| **会话命名** | per-directory / global / 手动映射 / 基于标题。 | 源自平台会话键。 |
| **多代理** | 仅限单代理。 | 通过 `subagent_spawned` 的父观察者层次。 |
| **工具界面** | 单个 `query_user_context` 工具（按需辩证）。 | 6 个工具：session、profile、search、context（快速）+ recall、analyze（LLM）。 |
| **平台元数据** | 未剥离。 | 在 Honcho 存储前明确剥离。 |
| **消息去重** | 无。 | 会话元数据中的 `lastSavedIndex` 防止重新发送。 |
| **CLI 界面在提示中** | 注入管理命令到系统提示。代理知道自己的 CLI。 | 未注入。 |
| **AI 对等名称在身份中** | 配置时替换默认代理身份中的 "KClaw Agent"。 | 未实现。 |
| **QMD / 本地文件搜索** | 未实现。 | 配置 QMD 后端时的直通工具。 |
| **工作区元数据** | 未实现。 | 工作区元数据中的 `agentPeerMap` 跟踪代理→对等 ID。 |

---

## 模式

KClaw 有六个模式值得在任何 Honcho 集成中采用。每个都描述为与集成无关的接口。

**KClaw 贡献：**
- 异步预取（零延迟）
- 动态推理级别
- 每对等记忆模式
- AI 对等身份形成
- 会话命名策略
- CLI 界面注入

**openclaw-honcho 回馈（KClaw 应采用）：**
- `lastSavedIndex` 去重
- 平台元数据剥离
- 多代理观察者层次
- `context()` 上的 `peerPerspective`
- 分层工具界面（快速/LLM）
- 工作区 `agentPeerMap`

---

## 规范：异步预取

### 问题

在每个 LLM 调用前同步调用 `session.context()` 和 `peer.chat()` 为每个轮次增加 200–800ms 的 Honcho 往返延迟。

### 模式

在每个轮次**结束**时将两个调用作为非阻塞后台工作触发。将结果存储在按会话 ID 键控的 per-session 缓存中。在下一个轮次**开始**时从缓存弹出 — HTTP 已经完成。第一轮是冷的（空缓存）；所有后续轮次在响应路径上是零延迟的。

### 接口契约

```typescript
interface AsyncPrefetch {
  // 在轮次结束触发上下文 + 辩证获取。非阻塞。
  firePrefetch(sessionId: string, userMessage: string): void;

  // 在轮次开始时弹出缓存结果。如果缓存为空返回 null。
  popContextResult(sessionId: string): ContextResult | null;
  popDialecticResult(sessionId: string): string | null;
}

type ContextResult = {
  representation: string;
  card: string[];
  aiRepresentation?: string;  // 如果启用，AI 对等上下文
  summary?: string;           // 如果获取，对话摘要
};
```

### 实现说明

- **Python：** `threading.Thread(daemon=True)`。写入 `dict[session_id, result]` — GIL 使简单写入安全。
- **TypeScript：** `Promise` 存储在 `Map<string, Promise<ContextResult>>` 中。在弹出时等待。如果尚未解析，返回 null — 不要阻塞。
- 弹出是破坏性的：读取后清除缓存条目，这样陈旧数据永远不会积累。
- 预取也应该在第一轮触发（即使在第二轮之前不会被消费）。

### openclaw-honcho 采用

将 `session.context()` 从 `before_prompt_build` 移动到 post-`agent_end` 后台任务。将结果存储在 `state.contextCache` 中。在 `before_prompt_build` 中从缓存读取，而不是调用 Honcho。如果缓存为空（第一轮），不注入任何内容 — 没有 Honcho 上下文的提示仍然是有效的。

---

## 规范：动态推理级别

### 问题

Honcho 的辩证端点支持从 `minimal` 到 `max` 的推理级别。每个工具固定级别浪费简单查询的预算，也无法很好地服务复杂查询。

### 模式

根据用户消息动态选择推理级别。使用配置的默认值作为底部。按消息长度增加。以 `high` 为上限自动选择 — 永远不要自动选择 `max`。

### 逻辑

```
< 120 字符 → 默认（通常为 "low"）
120–400 字符 → 默认上一级（上限为 "high"）
> 400 字符 → 默认上两级（上限为 "high"）
```

### 配置键

添加 `dialecticReasoningLevel`（字符串，默认 `"low"`）。这设置底部。动态增加始终适用。

### openclaw-honcho 采用

在 `honcho_recall` 和 `honcho_analyze` 中应用：用动态选择器替换固定的 `reasoningLevel`。`honcho_recall` 使用底部 `"minimal"`，`honcho_analyze` 使用底部 `"medium"` — 两者仍随消息长度增加。

---

## 规范：每对等记忆模式

### 问题

用户希望独立控制是否将用户上下文和代理上下文写入本地、Honcho 或两者。

### 模式

| 模式 | 效果 |
|---|---|
| `hybrid` | 同时写入本地文件和 Honcho（默认） |
| `honcho` | 仅 Honcho — 禁用相应的本地文件写入 |
| `local` | 仅本地文件 — 跳过此对等的 Honcho 同步 |

### 配置模式

```json
{
  "memoryMode": "hybrid",
  "userMemoryMode": "honcho",
  "agentMemoryMode": "hybrid"
}
```

解析顺序：每对等字段胜出 → 简写 `memoryMode` → 默认 `"hybrid"`。

### 对 Honcho 同步的影响

- `userMemoryMode=local`：跳过向 Honcho 添加用户对等消息
- `agentMemoryMode=local`：跳过向 Honcho 添加助手对等消息
- 两者本地：完全跳过 `session.addMessages()`
- `userMemoryMode=honcho`：禁用本地 USER.md 写入
- `agentMemoryMode=honcho`：禁用本地 MEMORY.md / SOUL.md 写入

---

## 规范：AI 对等身份形成

### 问题

Honcho 通过观察用户所说的内容有机地构建用户表示。相同机制存在于 AI 对等 — 但仅在 AI 对等的 `observe_me=True` 设置时。没有它，AI 对等积累为空。

此外，现有 persona 文件（SOUL.md、IDENTITY.md）应在首次激活时为 AI 对等的 Honcho 表示播种。

### 第 A 部分：agent 对等的 observe_me=True

```typescript
await session.addPeers([
  [ownerPeer.id, { observeMe: true,  observeOthers: false }],
  [agentPeer.id, { observeMe: true,  observeOthers: true  }], // 原来是 false
]);
```

一行更改。基础性。没有它，无论代理说什么，AI 对等表示都保持为空。

### 第 B 部分：seedAiIdentity()

```typescript
async function seedAiIdentity(
  agentPeer: Peer,
  content: string,
  source: string
): Promise<boolean> {
  const wrapped = [
    `<ai_identity_seed>`,
    `<source>${source}</source>`,
    ``,
    content.trim(),
    `</ai_identity_seed>`,
  ].join("\n");

  await agentPeer.addMessage("assistant", wrapped);
  return true;
}
```

### 第 C 部分：设置时迁移代理文件

在 `honcho setup` 期间，通过 `seedAiIdentity()` 而非 `session.uploadFile()` 将代理自身文件（SOUL.md、IDENTITY.md、AGENTS.md）上传到代理对等。这将内容路由到 Honcho 的观察管道。

### 第 D 部分：身份中的 AI 对等名称

当代理有配置名称时，预先添加到注入的系统提示：

```typescript
const namePrefix = agentName ? `You are ${agentName}.\n\n` : "";
return { systemPrompt: namePrefix + "## User Memory Context\n\n" + sections };
```

### CLI 界面

```
honcho identity <file>    # 从文件播种
honcho identity --show    # 显示当前 AI 对等表示
```

---

## 规范：会话命名策略

### 问题

单一全局会话意味着每个项目共享相同的 Honcho 上下文。per-directory 会话提供隔离，而无需用户手动命名会话。

### 策略

| 策略 | 会话键 | 何时使用 |
|---|---|---|
| `per-directory` | CWD 的基名 | 默认。每个项目有自己的会话。 |
| `global` | 固定字符串 `"global"` | 单一跨项目会话。 |
| 手动映射 | 每路径的用户配置 | `sessions` 配置映射覆盖目录基名。 |
| 基于标题 | 清理的会话标题 | 当代理支持在对话中设置的命名会话时。 |

### 配置模式

```json
{
  "sessionStrategy": "per-directory",
  "sessionPeerPrefix": false,
  "sessions": {
    "/home/user/projects/foo": "foo-project"
  }
}
```

### CLI 界面

```
honcho sessions              # 列出所有映射
honcho map <name>            # 将 cwd 映射到会话名称
honcho map                   # 无参数 = 列出映射
```

解析顺序：手动映射 → 会话标题 → 目录基名 → 平台键。

---

## 规范：CLI 界面注入

### 问题

当用户问"如何更改我的记忆设置？"时，代理要么产生幻觉要么说不知道。代理应该知道自己的管理界面。

### 模式

当 Honcho 处于活动状态时，将紧凑命令参考附加到系统提示。保持在 300 字符以下。

```
# Honcho 记忆集成
活动。会话：{sessionKey}。模式：{mode}。
管理命令：
  honcho status                    — 显示配置 + 连接
  honcho mode [hybrid|honcho|local] — 显示或设置记忆模式
  honcho sessions                  — 列出会话映射
  honcho map <name>                — 将目录映射到会话
  honcho identity [file] [--show]  — 播种或显示 AI 身份
  honcho setup                     — 完全交互式向导
```

---

## openclaw-honcho 检查清单

按影响排序：

- [ ] **异步预取** — 将 `session.context()` 从 `before_prompt_build` 移出到 post-`agent_end` 后台 Promise
- [ ] **agent 对等的 observe_me=True** — `session.addPeers()` 中的一行更改
- [ ] **动态推理级别** — 添加辅助函数；在 `honcho_recall` 和 `honcho_analyze` 中应用；添加 `dialecticReasoningLevel` 到配置
- [ ] **每对等记忆模式** — 添加 `userMemoryMode` / `agentMemoryMode` 到配置；控制 Honcho 同步和本地写入
- [ ] **seedAiIdentity()** — 添加辅助函数；在设置迁移期间用于 SOUL.md / IDENTITY.md
- [ ] **会话命名策略** — 添加 `sessionStrategy`、`sessions` 映射、`sessionPeerPrefix`
- [ ] **CLI 界面注入** — 将命令参考附加到 `before_prompt_build` 返回值
- [ ] **honcho identity 子命令** — 从文件播种或 `--show` 当前表示
- [ ] **AI 对等名称注入** — 如果配置了 `aiPeer` 名称，预先添加到注入的系统提示
- [ ] **honcho mode / sessions / map** — 与 KClaw 的 CLI 对等性

openclaw-honcho 已完成（不要重新实现）：`lastSavedIndex` 去重、平台元数据剥离、多代理父观察者、`context()` 上的 `peerPerspective`、分层工具界面、工作区 `agentPeerMap`、QMD 直通、自托管 Honcho。

---

## nanobot-honcho 检查清单

绿地集成。从 openclaw-honcho 的架构开始，从第一天起应用所有 KClaw 模式。

### 第一阶段 — 核心正确性

- [ ] 双对等模型（所有者 + 代理对等），两者都设置 `observe_me=True`
- [ ] 轮次结束的 `lastSavedIndex` 去重消息捕获
- [ ] Honcho 存储前的平台元数据剥离
- [ ] 从第一天起异步预取 — 不要实现阻塞上下文注入
- [ ] 首次激活时的旧文件迁移（USER.md → 所有者对等，SOUL.md → `seedAiIdentity()`）

### 第二阶段 — 配置

- [ ] 配置模式：`apiKey`、`workspaceId`、`baseUrl`、`memoryMode`、`userMemoryMode`、`agentMemoryMode`、`dialecticReasoningLevel`、`sessionStrategy`、`sessions`
- [ ] 每对等记忆模式控制
- [ ] 动态推理级别
- [ ] 会话命名策略

### 第三阶段 — 工具和 CLI

- [ ] 工具界面：`honcho_profile`、`honcho_recall`、`honcho_analyze`、`honcho_search`、`honcho_context`
- [ ] CLI：`setup`、`status`、`sessions`、`map`、`mode`、`identity`
- [ ] CLI 界面注入到系统提示
- [ ] AI 对等名称连接到代理身份
