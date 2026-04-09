# Hindsight 记忆提供商

具有知识图谱、实体解析和多策略检索的长期记忆。支持云端、本地嵌入和本地外部模式。

## 需求

- **云端：** 从 [ui.hindsight.vectorize.io](https://ui.hindsight.vectorize.io) 获取 API 密钥
- **本地嵌入：** 支持的 LLM 提供商的 API 密钥（OpenAI、Anthropic、Gemini、Groq、OpenRouter、MiniMax、Ollama 或任何 OpenAI 兼容端点）。嵌入和重排完全在本地运行 — 无需额外的 API 密钥。
- **本地外部：** 可通过 HTTP 访问的运行中的 Hindsight 实例（Docker 或自托管）。

## 设置

```bash
kclaw memory setup    # 选择 "hindsight"
```

设置向导将通过 `uv` 自动安装依赖，并引导您完成配置。

或手动（使用默认值的云模式）：
```bash
kclaw config set memory.provider hindsight
echo "HINDSIGHT_API_KEY=your-key" >> ~/.kclaw/.env
```

### 云端

连接到 Hindsight Cloud API。需要从 [ui.hindsight.vectorize.io](https://ui.hindsight.vectorize.io) 获取 API 密钥。

### 本地嵌入

KClaw 启动一个带有内置 PostgreSQL 的本地 Hindsight 守护进程。需要 LLM API 密钥进行记忆提取和综合。守护进程在首次使用时自动在后台启动，并在 5 分钟不活动后停止。

支持任何 OpenAI 兼容的 LLM 端点（llama.cpp、vLLM、LM Studio 等）— 选择 `openai_compatible` 作为提供商并输入 base URL。

守护进程启动日志：`~/.kclaw/logs/hindsight-embed.log`
守护进程运行日志：`~/.hindsight/profiles/<profile>.log`

打开 Hindsight Web UI（仅本地嵌入模式）：
```bash
hindsight-embed -p kclaw ui start
```

### 本地外部

将插件指向您已在运行（Docker、自托管等）的现有 Hindsight 实例。无守护进程管理 — 只需一个 URL 和一个可选的 API 密钥。

## 配置

配置文件：`~/.kclaw/hindsight/config.json`

### 连接

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `mode` | `cloud` | `cloud`、`local_embedded` 或 `local_external` |
| `api_url` | `https://api.hindsight.vectorize.io` | API URL（云端和 local_external 模式）|

### 记忆库

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `bank_id` | `kclaw` | 记忆库名称 |
| `bank_mission` | — | 反思任务（身份/框架用于反思推理）。通过 Banks API 应用。|
| `bank_retain_mission` | — | 保留任务（引导提取的内容）。通过 Banks API 应用。|

### 召回

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `recall_budget` | `mid` | 召回详尽程度：`low` / `mid` / `high` |
| `recall_prefetch_method` | `recall` | 自动召回方法：`recall`（原始事实）或 `reflect`（LLM 综合）|
| `recall_max_tokens` | `4096` | 召回结果的最大 token 数 |
| `recall_max_input_chars` | `800` | 自动召回的最大输入查询长度 |
| `recall_prompt_preamble` | — | 上下文中召回记忆的自定义前言 |
| `recall_tags` | — | 搜索记忆时过滤的标签 |
| `recall_tags_match` | `any` | 标签匹配模式：`any` / `all` / `any_strict` / `all_strict` |
| `auto_recall` | `true` | 每轮之前自动召回记忆 |

### 保留

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `auto_retain` | `true` | 自动保留对话轮次 |
| `retain_async` | `true` | 在 Hindsight 服务器上异步处理保留 |
| `retain_every_n_turns` | `1` | 每 N 轮保留一次（1 = 每轮）|
| `retain_context` | `KClaw Agent 和用户之间的对话` | 保留记忆的上下文标签 |
| `tags` | — | 存储记忆时应用的标签 |

### 集成

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `memory_mode` | `hybrid` | 记忆如何集成到代理中 |

**memory_mode:**
- `hybrid` — 自动上下文注入 + 暴露给 LLM 的工具
- `context` — 仅自动注入，不暴露工具
- `tools` — 仅工具，无自动注入

### 本地嵌入 LLM

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `llm_provider` | `openai` | `openai`、`anthropic`、`gemini`、`groq`、`openrouter`、`minimax`、`ollama`、`lmstudio`、`openai_compatible` |
| `llm_model` | per-provider | 模型名称（例如 `gpt-4o-mini`、`qwen/qwen3.5-9b`）|
| `llm_base_url` | — | `openai_compatible` 的端点 URL（例如 `http://192.168.1.10:8080/v1`）|

LLM API 密钥存储在 `~/.kclaw/.env` 中作为 `HINDSIGHT_LLM_API_KEY`。

## 工具

在 `hybrid` 和 `tools` 记忆模式下可用：

| 工具 | 描述 |
|------|------|
| `hindsight_retain` | 存储信息，自动提取实体 |
| `hindsight_recall` | 多策略搜索（语义 + 实体图）|
| `hindsight_reflect` | 跨记忆综合（LLM 驱动）|

## 环境变量

| 变量 | 描述 |
|------|------|
| `HINDSIGHT_API_KEY` | Hindsight Cloud 的 API 密钥 |
| `HINDSIGHT_LLM_API_KEY` | 本地模式的 LLM API 密钥 |
| `HINDSIGHT_API_LLM_BASE_URL` | 本地模式的 LLM Base URL（例如 OpenRouter）|
| `HINDSIGHT_API_URL` | 覆盖 API 端点 |
| `HINDSIGHT_BANK_ID` | 覆盖记忆库名称 |
| `HINDSIGHT_BUDGET` | 覆盖召回预算 |
| `HINDSIGHT_MODE` | 覆盖模式（`cloud`、`local_embedded`、`local_external`）|

## 客户端版本

需要 `hindsight-client >= 0.4.22`。如果检测到旧版本，插件会在会话开始时自动升级。
