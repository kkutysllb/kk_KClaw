# Honcho 记忆提供商

具有辩证问答、语义搜索、对等卡片和持久结论的 AI 原生跨会话用户建模。

> **Honcho 文档：** <https://docs.honcho.dev/v3/guides/integrations/kclaw>

## 需求

- `pip install honcho-ai`
- 从 [app.honcho.dev](https://app.honcho.dev) 获取 Honcho API 密钥，或自托管实例

## 设置

```bash
kclaw honcho setup    # 完全交互式向导（云端或本地）
kclaw memory setup    # 通用选择器，也可以工作
```

或手动：
```bash
kclaw config set memory.provider honcho
echo "HONCHO_API_KEY=your-key" >> ~/.kclaw/.env
```

## 配置解析

配置从存在的第一个文件读取：

| 优先级 | 路径 | 范围 |
|--------|------|------|
| 1 | `$KCLAW_HOME/honcho.json` | Profile 本地（隔离的 KClaw 实例）|
| 2 | `~/.kclaw/honcho.json` | 默认 profile（共享 host 块）|
| 3 | `~/.honcho/config.json` | 全局（跨应用互操作）|

Host 密钥从活动的 KClaw profile 派生：`kclaw`（默认）或 `kclaw.<profile>`。

## 工具

| 工具 | LLM 调用？ | 描述 |
|------|-----------|------|
| `honcho_profile` | 否 | 用户的对等卡片 — 关键事实快照 |
| `honcho_search` | 否 | 存储上下文的语义搜索（默认 800 tok，最大 2000）|
| `honcho_context` | 是 | 通过辩证推理的 LLM 综合答案 |
| `honcho_conclude` | 否 | 写入关于用户的持久事实 |

工具可用性取决于 `recallMode`：在 `context` 模式下隐藏，在 `tools` 和 `hybrid` 中始终存在。

## 完整配置参考

### 身份与连接

| 键 | 类型 | 默认值 | 范围 | 描述 |
|-----|------|--------|-------|------|
| `apiKey` | string | -- | root / host | API 密钥。回退到 `HONCHO_API_KEY` 环境变量 |
| `baseUrl` | string | -- | root | 自托管 Honcho 的 Base URL。本地 URL（`localhost`、`127.0.0.1`、`::1`）自动跳过 API 密钥认证 |
| `environment` | string | `"production"` | root / host | SDK 环境映射 |
| `enabled` | bool | auto | root / host | 主开关。当存在 `apiKey` 或 `baseUrl` 时自动启用 |
| `workspace` | string | host 密钥 | root / host | Honcho 工作区 ID |
| `peerName` | string | -- | root / host | 用户对等身份 |
| `aiPeer` | string | host 密钥 | root / host | AI 对等身份 |

### 记忆与召回

| 键 | 类型 | 默认值 | 范围 | 描述 |
|-----|------|--------|-------|------|
| `recallMode` | string | `"hybrid"` | root / host | `"hybrid"`（自动注入 + 工具）、`"context"`（仅自动注入，工具隐藏）、`"tools"`（仅工具，无注入）。旧版 `"auto"` 规范化为 `"hybrid"` |
| `observationMode` | string | `"directional"` | root / host | 速记预设：`"directional"`（全部开启）或 `"unified"`（共享池）。使用 `observation` 对象进行细粒度控制 |
| `observation` | object | -- | root / host | 每个对等的观察配置（见下文）|

#### 观察（细粒度）

映射到 Honcho 的每个对等 `SessionPeerConfig`。在 root 或每个 host 块设置 — 每个 profile 可以有不同的观察设置。存在时，覆盖 `observationMode` 预设。

```json
"observation": {
  "user": { "observeMe": true, "observeOthers": true },
  "ai":   { "observeMe": true, "observeOthers": true }
}
```

| 字段 | 默认值 | 描述 |
|------|--------|------|
| `user.observeMe` | `true` | 用户对等自我观察（Honcho 构建用户表示）|
| `user.observeOthers` | `true` | 用户对等观察 AI 消息 |
| `ai.observeMe` | `true` | AI 对等自我观察（Honcho 构建 AI 表示）|
| `ai.observeOthers` | `true` | AI 对等观察用户消息（启用跨对等辩证）|

`observationMode` 的预设：
- `"directional"`（默认）：全部四个布尔值为 `true`
- `"unified"`：用户 `observeMe=true`，AI `observeOthers=true`，其余 `false`

每个 profile 示例 — 观察用户但用户不观察 coder：

```json
"hosts": {
  "kclaw.coder": {
    "observation": {
      "user": { "observeMe": true, "observeOthers": false },
      "ai":   { "observeMe": true, "observeOthers": true }
    }
  }
}
```

在 [Honcho 仪表板](https://app.honcho.dev) 中更改的设置会在会话初始化时同步回来。

### 写入行为

| 键 | 类型 | 默认值 | 范围 | 描述 |
|-----|------|--------|-------|------|
| `writeFrequency` | string 或 int | `"async"` | root / host | `"async"`（后台线程）、`"turn"`（每轮同步）、`"session"`（结束时批量）、或整数 N（每 N 轮）|
| `saveMessages` | bool | `true` | root / host | 是否将消息持久化到 Honcho API |

### 会话解析

| 键 | 类型 | 默认值 | 范围 | 描述 |
|-----|------|--------|-------|------|
| `sessionStrategy` | string | `"per-directory"` | root / host | `"per-directory"`、`"per-session"`（每次运行新建）、`"per-repo"`（git 根名称）、`"global"`（单个会话）|
| `sessionPeerPrefix` | bool | `false` | root / host | 在会话键前添加对等名称 |
| `sessions` | object | `{}` | root | 手动目录到会话名称的映射：`{"/path/to/project": "my-session"}` |

### Token 预算与辩证

| 键 | 类型 | 默认值 | 范围 | 描述 |
|-----|------|--------|-------|------|
| `contextTokens` | int | SDK 默认 | root / host | `context()` API 调用的 Token 预算。也限制预取截断（tokens x 4 字符）|
| `dialecticReasoningLevel` | string | `"low"` | root / host | `peer.chat()` 的基础推理级别：`"minimal"`、`"low"`、`"medium"`、`"high"`、`"max"` |
| `dialecticDynamic` | bool | `true` | root / host | 根据查询长度自动提升推理：`<120` 字符 = 基础级别，`120-400` = +1，`>400` = +2（上限为 `"high"`）。设置为 `false` 以始终按原样使用 `dialecticReasoningLevel` |
| `dialecticMaxChars` | int | `600` | root / host | 注入系统提示的辩证结果的最大字符数 |
| `dialecticMaxInputChars` | int | `10000` | root / host | 辩证查询输入到 `peer.chat()` 的最大字符数。Honcho 云限制：10k |
| `messageMaxChars` | int | `25000` | root / host | 通过 `add_messages()` 发送的每条消息的最大字符数。超过此限制的消息用 `[continued]` 标记分块。Honcho 云限制：25k |

### 成本意识（高级）

这些从根配置对象读取，不是 host 块。必须在 `honcho.json` 中手动设置。

| 键 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `injectionFrequency` | string | `"every-turn"` | `"every-turn"` 或 `"first-turn"`（仅在第 0 轮注入上下文）|
| `contextCadence` | int | `1` | `context()` API 调用之间的最小轮次 |
| `dialecticCadence` | int | `1` | `peer.chat()` API 调用之间的最小轮次 |
| `reasoningLevelCap` | string | -- | 自动提升推理的硬上限：`"minimal"`、`"low"`、`"mid"`、`"high"` |

### 硬编码限制（不可配置）

| 限制 | 值 | 位置 |
|------|-----|------|
| 搜索工具最大 tokens | 2000（硬上限）、800（默认）| `__init__.py` handle_tool_call |
| 对等卡片获取 tokens | 200 | `session.py` get_peer_card |

## 配置优先级

对于每个键，解析顺序为：**host 块 > root > 环境变量 > 默认值**。

Host 密钥派生：`KCLAW_HONCHO_HOST` 环境变量 > 活动 profile（`kclaw.<profile>`）> `"kclaw"`。

## 环境变量

| 变量 | 回退用于 |
|------|----------|
| `HONCHO_API_KEY` | `apiKey` |
| `HONCHO_BASE_URL` | `baseUrl` |
| `HONCHO_ENVIRONMENT` | `environment` |
| `KCLAW_HONCHO_HOST` | Host 密钥覆盖 |

## CLI 命令

| 命令 | 描述 |
|------|------|
| `kclaw honcho setup` | 完全交互式设置向导 |
| `kclaw honcho status` | 显示活动 profile 的解析配置 |
| `kclaw honcho enable` / `disable` | 切换活动 profile 的 Honcho |
| `kclaw honcho mode <mode>` | 更改召回或观察模式 |
| `kclaw honcho peer --user <name>` | 更新用户对等名称 |
| `kclaw honcho peer --ai <name>` | 更新 AI 对等名称 |
| `kclaw honcho tokens --context <N>` | 设置上下文 token 预算 |
| `kclaw honcho tokens --dialectic <N>` | 设置辩证最大字符数 |
| `kclaw honcho map <name>` | 将当前目录映射到会话名称 |
| `kclaw honcho sync` | 为所有 KClaw profiles 创建 host 块 |

## 配置示例

```json
{
  "apiKey": "your-key",
  "workspace": "kclaw",
  "peerName": "eri",
  "hosts": {
    "kclaw": {
      "enabled": true,
      "aiPeer": "kclaw",
      "workspace": "kclaw",
      "peerName": "eri",
      "recallMode": "hybrid",
      "observation": {
        "user": { "observeMe": true, "observeOthers": true },
        "ai": { "observeMe": true, "observeOthers": true }
      },
      "writeFrequency": "async",
      "sessionStrategy": "per-directory",
      "dialecticReasoningLevel": "low",
      "dialecticMaxChars": 600,
      "saveMessages": true
    },
    "kclaw.coder": {
      "enabled": true,
      "aiPeer": "coder",
      "workspace": "kclaw",
      "peerName": "eri",
      "observation": {
        "user": { "observeMe": true, "observeOthers": false },
        "ai": { "observeMe": true, "observeOthers": true }
      }
    }
  },
  "sessions": {
    "/home/user/myproject": "myproject-main"
  }
}
```
