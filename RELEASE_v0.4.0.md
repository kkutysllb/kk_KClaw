# KClaw Agent v0.4.0 (v2026.3.23)

**发布日期：** 2026年3月23日

> 平台扩展版本 — OpenAI 兼容 API 服务器、6 个新消息适配器、4 个新推理提供商、MCP 服务器管理（支持 OAuth 2.1）、@ 上下文引用、Gateway 提示缓存、流式输出默认启用，以及包含 200+ 修复的全面可靠性提升。

---

## ✨ 亮点

- **OpenAI 兼容 API 服务器** — 通过新的 `/api/jobs` REST API 将 KClaw 暴露为 `/v1/chat/completions` 端点，包含输入限制、字段白名单、SQLite 响应持久化和 CORS 源保护 ([#1756](https://github.com/NousResearch/kclaw/pull/1756), [#2450](https://github.com/NousResearch/kclaw/pull/2450), [#2456](https://github.com/NousResearch/kclaw/pull/2456), [#2451](https://github.com/NousResearch/kclaw/pull/2451), [#2472](https://github.com/NousResearch/kclaw/pull/2472))

- **6 个新消息平台适配器** — Signal、钉钉、SMS (Twilio)、Mattermost、Matrix 和 Webhook 适配器加入 Telegram、Discord 和 WhatsApp。Gateway 以指数退避策略自动重连失败的平台 ([#2206](https://github.com/NousResearch/kclaw/pull/2206), [#1685](https://github.com/NousResearch/kclaw/pull/1685), [#1688](https://github.com/NousResearch/kclaw/pull/1688), [#1683](https://github.com/NousResearch/kclaw/pull/1683), [#2166](https://github.com/NousResearch/kclaw/pull/2166), [#2584](https://github.com/NousResearch/kclaw/pull/2584))

- **@ 上下文引用** — Claude Code 风格的 `@file` 和 `@url` 上下文注入，CLI 支持 Tab 补全 ([#2343](https://github.com/NousResearch/kclaw/pull/2343), [#2482](https://github.com/NousResearch/kclaw/pull/2482))

- **4 个新推理提供商** — GitHub Copilot (OAuth + token 验证)、阿里云 / DashScope、Kilo Code 和 OpenCode Zen/Go ([#1924](https://github.com/NousResearch/kclaw/pull/1924), [#1879](https://github.com/NousResearch/kclaw/pull/1879) by @mchzimm, [#1673](https://github.com/NousResearch/kclaw/pull/1673), [#1666](https://github.com/NousResearch/kclaw/pull/1666), [#1650](https://github.com/NousResearch/kclaw/pull/1650))

- **MCP 服务器管理 CLI** — `kclaw mcp` 命令用于安装、配置和认证 MCP 服务器，支持完整的 OAuth 2.1 PKCE 流程 ([#2465](https://github.com/NousResearch/kclaw/pull/2465))

- **Gateway 提示缓存** — 每次会话缓存 AIAgent 实例，跨对话轮次保留 Anthropic 提示缓存，显著降低长对话成本 ([#2282](https://github.com/NousResearch/kclaw/pull/2282), [#2284](https://github.com/NousResearch/kclaw/pull/2284), [#2361](https://github.com/NousResearch/kclaw/pull/2361))

- **上下文压缩全面升级** — 结构化摘要、迭代更新、token 预算尾部保护、可配置的摘要端点和回退模型支持 ([#2323](https://github.com/NousResearch/kclaw/pull/2323), [#1727](https://github.com/NousResearch/kclaw/pull/1727), [#2224](https://github.com/NousResearch/kclaw/pull/2224))

- **流式输出默认启用** — CLI 流式输出默认开启，流式模式下正确显示 spinner 和工具进度，以及大量换行符和拼接修复 ([#2340](https://github.com/NousResearch/kclaw/pull/2340), [#2161](https://github.com/NousResearch/kclaw/pull/2161), [#2258](https://github.com/NousResearch/kclaw/pull/2258))

---

## 🖥️ CLI 与用户体验

### 新命令与交互
- **@ 上下文补全** — Tab 可补全的 `@file`/`@url` 引用，可将文件内容或网页注入对话 ([#2482](https://github.com/NousResearch/kclaw/pull/2482), [#2343](https://github.com/NousResearch/kclaw/pull/2343))
- **`/statusbar`** — 切换持久配置栏，显示提示中的模型和提供商信息 ([#2240](https://github.com/NousResearch/kclaw/pull/2240), [#1917](https://github.com/NousResearch/kclaw/pull/1917))
- **`/queue`** — 队列化提示词，不中断当前运行 ([#2191](https://github.com/NousResearch/kclaw/pull/2191), [#2469](https://github.com/NousResearch/kclaw/pull/2469))
- **`/permission`** — 在会话期间动态切换审批模式 ([#2207](https://github.com/NousResearch/kclaw/pull/2207))
- **`/browser`** — 从 CLI 发起交互式浏览器会话 ([#2273](https://github.com/NousResearch/kclaw/pull/2273), [#1814](https://github.com/NousResearch/kclaw/pull/1814))
- **`/cost`** — Gateway 模式下实时定价和使用量追踪 ([#2180](https://github.com/NousResearch/kclaw/pull/2180))
- **`/approve` 和 `/deny`** — 在 Gateway 中用明确命令替代纯文本审批 ([#2002](https://github.com/NousResearch/kclaw/pull/2002))

### 流式输出与显示
- CLI 流式输出默认启用 ([#2340](https://github.com/NousResearch/kclaw/pull/2340))
- 流式模式下显示 spinner 和工具进度 ([#2161](https://github.com/NousResearch/kclaw/pull/2161))
- 启用 `show_reasoning` 时显示推理/思考块 ([#2118](https://github.com/NousResearch/kclaw/pull/2118))
- CLI 和 Gateway 的上下文压力警告 ([#2159](https://github.com/NousResearch/kclaw/pull/2159))
- 修复：流式数据块无空格拼接 ([#2258](https://github.com/NousResearch/kclaw/pull/2258))
- 修复：迭代边界换行符防止流拼接错误 ([#2413](https://github.com/NousResearch/kclaw/pull/2413))
- 修复：延迟流式换行符防止空白行堆积 ([#2473](https://github.com/NousResearch/kclaw/pull/2473))
- 修复：非 TTY 环境下抑制 spinner 动画 ([#2216](https://github.com/NousResearch/kclaw/pull/2216))
- 修复：在 API 错误消息中显示提供商和端点 ([#2266](https://github.com/NousResearch/kclaw/pull/2266))
- 修复：解决状态输出中的 ANSI 转义码乱码问题 ([#2448](https://github.com/NousResearch/kclaw/pull/2448))
- 修复：将金色 ANSI 颜色更新为真彩色格式 ([#2246](https://github.com/NousResearch/kclaw/pull/2246))
- 修复：规范化工具集标签并在横幅中使用肤色 ([#1912](https://github.com/NousResearch/kclaw/pull/1912))

### CLI 优化
- 修复：退出时阻止"按回车继续..."提示 ([#2555](https://github.com/NousResearch/kclaw/pull/2555))
- 修复：在 agent 循环中刷新 stdout 防止 macOS 显示冻结 ([#1654](https://github.com/NousResearch/kclaw/pull/1654))
- 修复：`kclaw setup` 遇到权限错误时显示人类可读的错误信息 ([#2196](https://github.com/NousResearch/kclaw/pull/2196))
- 修复：`/stop` 命令崩溃 + 流式媒体传递中的 UnboundLocalError ([#2463](https://github.com/NousResearch/kclaw/pull/2463))
- 修复：允许自定义/本地端点无需 API key ([#2556](https://github.com/NousResearch/kclaw/pull/2556))
- 修复：Kitty 键盘协议 Shift+Enter 用于 Ghostty/WezTerm（已尝试并因 prompt_toolkit 崩溃而回滚） ([#2345](https://github.com/NousResearch/kclaw/pull/2345), [#2349](https://github.com/NousResearch/kclaw/pull/2349))

### 配置
- **`${ENV_VAR}` 替换** config.yaml 中的环境变量 ([#2684](https://github.com/NousResearch/kclaw/pull/2684))
- **实时配置重载** — config.yaml 更改无需重启即可生效 ([#2210](https://github.com/NousResearch/kclaw/pull/2210))
- **`custom_models.yaml`** 用于用户管理的模型添加 ([#2214](https://github.com/NousResearch/kclaw/pull/2214))
- **基于优先级的上下文文件选择** + CLAUDE.md 支持 ([#2301](https://github.com/NousResearch/kclaw/pull/2301))
- **合并嵌套 YAML 段** 而非在配置更新时替换 ([#2213](https://github.com/NousResearch/kclaw/pull/2213))
- 修复：config.yaml 提供商键无声覆盖环境变量 ([#2272](https://github.com/NousResearch/kclaw/pull/2272))
- 修复：记录警告而非静默吞没 config.yaml 错误 ([#2683](https://github.com/NousResearch/kclaw/pull/2683))
- 修复：`kclaw tools` 后被禁用的工具集会重新启用 ([#2268](https://github.com/NousResearch/kclaw/pull/2268))
- 修复：平台默认工具集静默覆盖工具反选 ([#2624](https://github.com/NousResearch/kclaw/pull/2624))
- 修复：遵守裸 YAML `approvals.mode: off` ([#2620](https://github.com/NousResearch/kclaw/pull/2620))
- 修复：`kclaw update` 使用 `.[all]` extras 并有回退机制 ([#1728](https://github.com/NousResearch/kclaw/pull/1728))
- 修复：`kclaw update` 在 stash 冲突时重置前提示 ([#2390](https://github.com/NousResearch/kclaw/pull/2390))
- 修复：使用 git pull --rebase 避免分支分歧错误 ([#2274](https://github.com/NousResearch/kclaw/pull/2274))
- 修复：添加 zprofile 回退并在全新 macOS 安装时创建 zshrc ([#2320](https://github.com/NousResearch/kclaw/pull/2320))
- 修复：移除 `ANTHROPIC_BASE_URL` 环境变量以避免冲突 ([#1675](https://github.com/NousResearch/kclaw/pull/1675))
- 修复：如果 IMAP 密码已存在于 keyring 或环境变量中则不再询问 ([#2212](https://github.com/NousResearch/kclaw/pull/2212))
- 修复：OpenCode Zen/Go 显示 OpenRouter 模型而非自己的 ([#2277](https://github.com/NousResearch/kclaw/pull/2277))

---

## 🏗️ 核心 Agent 与架构

### 新提供商
- **GitHub Copilot** — 完整 OAuth 认证、API 路由、token 验证和 400k 上下文 ([#1924](https://github.com/NousResearch/kclaw/pull/1924), [#1896](https://github.com/NousResearch/kclaw/pull/1896), [#1879](https://github.com/NousResearch/kclaw/pull/1879) by @mchzimm, [#2507](https://github.com/NousResearch/kclaw/pull/2507))
- **阿里云 / DashScope** — 完整集成 DashScope v1 运行时、模型点号保留和 401 认证修复 ([#1673](https://github.com/NousResearch/kclaw/pull/1673), [#2332](https://github.com/NousResearch/kclaw/pull/2332), [#2459](https://github.com/NousResearch/kclaw/pull/2459))
- **Kilo Code** — 一级推理提供商 ([#1666](https://github.com/NousResearch/kclaw/pull/1666))
- **OpenCode Zen 和 OpenCode Go** — 新的提供商后端 ([#1650](https://github.com/NousResearch/kclaw/pull/1650), [#2393](https://github.com/NousResearch/kclaw/pull/2393) by @0xbyt4)
- **NeuTTS** — 本地 TTS 提供商后端，内置设置流程，替代旧的 optional skill ([#1657](https://github.com/NousResearch/kclaw/pull/1657), [#1664](https://github.com/NousResearch/kclaw/pull/1664))

### 提供商改进
- **速率限制错误时紧急回退** 到备用模型 ([#1730](https://github.com/NousResearch/kclaw/pull/1730))
- **端点元数据** 用于自定义模型上下文和定价；查询本地服务器获取实际上下文窗口大小 ([#1906](https://github.com/NousResearch/kclaw/pull/1906), [#2091](https://github.com/NousResearch/kclaw/pull/2091) by @dusterbloom)
- **上下文长度检测全面升级** — models.dev 集成、提供商感知解析、自定义端点模糊匹配、`/v1/props` 用于 llama.cpp ([#2158](https://github.com/NousResearch/kclaw/pull/2158), [#2051](https://github.com/NousResearch/kclaw/pull/2051), [#2403](https://github.com/NousResearch/kclaw/pull/2403))
- **模型目录更新** — gpt-5.4-mini、gpt-5.4-nano、healer-alpha、haiku-4.5、minimax-m2.7、claude 4.6 支持 1M 上下文 ([#1913](https://github.com/NousResearch/kclaw/pull/1913), [#1915](https://github.com/NousResearch/kclaw/pull/1915), [#1900](https://github.com/NousResearch/kclaw/pull/1900), [#2155](https://github.com/NousResearch/kclaw/pull/2155), [#2474](https://github.com/NousResearch/kclaw/pull/2474))
- **自定义端点改进** — config.yaml 中的 `model.base_url`、`api_mode` 覆盖 responses API、允许无 API key 的端点、密钥缺失时快速失败 ([#2330](https://github.com/NousResearch/kclaw/pull/2330), [#1651](https://github.com/NousResearch/kclaw/pull/1651), [#2556](https://github.com/NousResearch/kclaw/pull/2556), [#2445](https://github.com/NousResearch/kclaw/pull/2445), [#1994](https://github.com/NousResearch/kclaw/pull/1994), [#1998](https://github.com/NousResearch/kclaw/pull/1998))
- 将模型和提供商注入系统提示 ([#1929](https://github.com/NousResearch/kclaw/pull/1929))
- 将 `api_mode` 绑定到提供商配置而非环境变量 ([#1656](https://github.com/NousResearch/kclaw/pull/1656))
- 修复：防止 Anthropic token 泄露到第三方 `anthropic_messages` 提供商 ([#2389](https://github.com/NousResearch/kclaw/pull/2389))
- 修复：防止 Anthropic 回退继承非 Anthropic 的 `base_url` ([#2388](https://github.com/NousResearch/kclaw/pull/2388))
- 修复：`auxiliary_is_nous` 标志从不重置 — 向其他提供商泄露 Nous 标签 ([#1713](https://github.com/NousResearch/kclaw/pull/1713))
- 修复：Anthropic `tool_choice 'none'` 仍允许工具调用 ([#1714](https://github.com/NousResearch/kclaw/pull/1714))
- 修复：Mistral 解析器嵌套 JSON 回退提取 ([#2335](https://github.com/NousResearch/kclaw/pull/2335))
- 修复：MiniMax 401 认证问题（默认使用 `anthropic_messages` 解决） ([#2103](https://github.com/NousResearch/kclaw/pull/2103))
- 修复：大小写不敏感的模型家族匹配 ([#2350](https://github.com/NousResearch/kclaw/pull/2350))
- 修复：激活检查中忽略占位符提供商键 ([#2358](https://github.com/NousResearch/kclaw/pull/2358))
- 修复：在上下文长度检测中保留 Ollama model:tag 冒号 ([#2149](https://github.com/NousResearch/kclaw/pull/2149))
- 修复：在启动门禁识别 Claude Code OAuth 凭证 ([#1663](https://github.com/NousResearch/kclaw/pull/1663))
- 修复：动态检测 Claude Code 版本用于 OAuth user-agent ([#1670](https://github.com/NousResearch/kclaw/pull/1670))
- 修复：刷新/回退后 OAuth 标志过期 ([#1890](https://github.com/NousResearch/kclaw/pull/1890))
- 修复：辅助客户端跳过过期的 Codex JWT ([#2397](https://github.com/NousResearch/kclaw/pull/2397))

### Agent 循环
- **Gateway 提示缓存** — 每次会话缓存 AIAgent，保留 assistant 轮次，修复会话恢复 ([#2282](https://github.com/NousResearch/kclaw/pull/2282), [#2284](https://github.com/NousResearch/kclaw/pull/2284), [#2361](https://github.com/NousResearch/kclaw/pull/2361))
- **上下文压缩全面升级** — 结构化摘要、迭代更新、token 预算尾部保护、可配置的 `summary_base_url` ([#2323](https://github.com/NousResearch/kclaw/pull/2323), [#1727](https://github.com/NousResearch/kclaw/pull/1727), [#2224](https://github.com/NousResearch/kclaw/pull/2224))
- **调用前清理和调用后工具防护** ([#1732](https://github.com/NousResearch/kclaw/pull/1732))
- **自动恢复** — 提供商拒绝 `tool_choice` 时重试（不带该参数） ([#2174](https://github.com/NousResearch/kclaw/pull/2174))
- **后台记忆/skill 审查** 替代内联提示 ([#2235](https://github.com/NousResearch/kclaw/pull/2235))
- **SOUL.md 作为主要 agent 标识** 而非硬编码默认值 ([#1922](https://github.com/NousResearch/kclaw/pull/1922))
- 修复：防止上下文压缩期间静默丢失工具结果 ([#1993](https://github.com/NousResearch/kclaw/pull/1993))
- 修复：处理空/空函数参数的工具调用恢复 ([#2163](https://github.com/NousResearch/kclaw/pull/2163))
- 修复：优雅处理 API 拒绝响应而非崩溃 ([#2156](https://github.com/NousResearch/kclaw/pull/2156))
- 修复：防止格式错误的工具调用导致 agent 循环卡住 ([#2114](https://github.com/NousResearch/kclaw/pull/2114))
- 修复：向模型返回 JSON 解析错误而非用空参数分发 ([#2342](https://github.com/NousResearch/kclaw/pull/2342))
- 修复：连续 assistant 消息合并在混合类型时丢失内容 ([#1703](https://github.com/NousResearch/kclaw/pull/1703))
- 修复：JSON 恢复和错误处理器中的消息角色交替违规 ([#1722](https://github.com/NousResearch/kclaw/pull/1722))
- 修复：`compression_attempts` 每次迭代重置 — 导致无限压缩 ([#1723](https://github.com/NousResearch/kclaw/pull/1723))
- 修复：`length_continue_retries` 从不重置 — 后续截断获得更少重试 ([#1717](https://github.com/NousResearch/kclaw/pull/1717))
- 修复：压缩器摘要角色违反连续角色约束 ([#1720](https://github.com/NousResearch/kclaw/pull/1720), [#1743](https://github.com/NousResearch/kclaw/pull/1743))
- 修复：移除硬编码的 `gemini-3-flash-preview` 作为默认摘要模型 ([#2464](https://github.com/NousResearch/kclaw/pull/2464))
- 修复：正确处理空工具结果 ([#2201](https://github.com/NousResearch/kclaw/pull/2201))
- 修复：`tool_calls` 列表中 None 条目导致崩溃 ([#2209](https://github.com/NousResearch/kclaw/pull/2209) by @0xbyt4, [#2316](https://github.com/NousResearch/kclaw/pull/2316))
- 修复：工作线程中每线程持久化事件循环 ([#2214](https://github.com/NousResearch/kclaw/pull/2214) by @jquesnelle)
- 修复：异步工具并行运行时防止"事件循环已运行"错误 ([#2207](https://github.com/NousResearch/kclaw/pull/2207))
- 修复：在源头剥离 ANSI — 在到达模型前清理终端输出 ([#2115](https://github.com/NousResearch/kclaw/pull/2115))
- 修复：OpenRouter 上跳过 role:tool 的顶层 `cache_control` ([#2391](https://github.com/NousResearch/kclaw/pull/2391))
- 修复：委托工具 — 在子级构造使全局变量变化前保存父工具名称 ([#2083](https://github.com/NousResearch/kclaw/pull/2083) by @ygd58, [#1894](https://github.com/NousResearch/kclaw/pull/1894))
- 修复：仅在空字符串时剥离最后一条 assistant 消息 ([#2326](https://github.com/NousResearch/kclaw/pull/2326))

### 会话与记忆
- **会话搜索** 和管理斜杠命令 ([#2198](https://github.com/NousResearch/kclaw/pull/2198))
- **自动会话标题** 和 `.kclaw.md` 项目配置 ([#1712](https://github.com/NousResearch/kclaw/pull/1712))
- 修复：并发内存写入静默丢弃条目 — 添加文件锁 ([#1726](https://github.com/NousResearch/kclaw/pull/1726))
- 修复：`session_search` 默认搜索所有来源 ([#1892](https://github.com/NousResearch/kclaw/pull/1892))
- 修复：处理连字符 FTS5 查询并保留引号字面量 ([#1776](https://github.com/NousResearch/kclaw/pull/1776))
- 修复：跳过 `load_transcript` 中的损坏行而非崩溃 ([#1744](https://github.com/NousResearch/kclaw/pull/1744))
- 修复：规范化会话键以防止大小写敏感重复 ([#2157](https://github.com/NousResearch/kclaw/pull/2157))
- 修复：防止无会话时 `session_search` 崩溃 ([#2194](https://github.com/NousResearch/kclaw/pull/2194))
- 修复：新会话时重置 token 计数器以准确显示使用量 ([#2101](https://github.com/NousResearch/kclaw/pull/2101) by @InB4DevOps)
- 修复：防止刷新 agent 的过时内存覆盖 ([#2687](https://github.com/NousResearch/kclaw/pull/2687))
- 修复：移除合成错误消息注入，修复重复失败后会话恢复 ([#2303](https://github.com/NousResearch/kclaw/pull/2303))
- 修复：`--resume` 安静模式现在传递 conversation_history ([#2357](https://github.com/NousResearch/kclaw/pull/2357))
- 修复：统一批处理模式下的恢复逻辑 ([#2331](https://github.com/NousResearch/kclaw/pull/2331))

### Honcho 记忆
- Honcho 配置修复和 @ 上下文引用集成 ([#2343](https://github.com/NousResearch/kclaw/pull/2343))
- 自托管 / Docker 配置文档 ([#2475](https://github.com/NousResearch/kclaw/pull/2475))

---

## 📱 消息平台 (Gateway)

### 新平台适配器
- **Signal Messenger** — 完整适配器，支持附件处理、群组消息过滤和 Note to Self 回声保护 ([#2206](https://github.com/NousResearch/kclaw/pull/2206), [#2400](https://github.com/NousResearch/kclaw/pull/2400), [#2297](https://github.com/NousResearch/kclaw/pull/2297), [#2156](https://github.com/NousResearch/kclaw/pull/2156))
- **钉钉** — 适配器，包含 gateway 路由和设置文档 ([#1685](https://github.com/NousResearch/kclaw/pull/1685), [#1690](https://github.com/NousResearch/kclaw/pull/1690), [#1692](https://github.com/NousResearch/kclaw/pull/1692))
- **SMS (Twilio)** ([#1688](https://github.com/NousResearch/kclaw/pull/1688))
- **Mattermost** — 支持 @-提及唯一频道过滤 ([#1683](https://github.com/NousResearch/kclaw/pull/1683), [#2443](https://github.com/NousResearch/kclaw/pull/2443))
- **Matrix** — 支持视觉和图片缓存 ([#1683](https://github.com/NousResearch/kclaw/pull/1683), [#2520](https://github.com/NousResearch/kclaw/pull/2520))
- **Webhook** — 用于外部事件触发的平台适配器 ([#2166](https://github.com/NousResearch/kclaw/pull/2166))
- **OpenAI 兼容 API 服务器** — `/v1/chat/completions` 端点，带 `/api/jobs` 定时任务管理 ([#1756](https://github.com/NousResearch/kclaw/pull/1756), [#2450](https://github.com/NousResearch/kclaw/pull/2450), [#2456](https://github.com/NousResearch/kclaw/pull/2456))

### Telegram 改进
- MarkdownV2 支持 — 删除线、 spoiler、引用块、转义括号/大括号/反斜杠/反引号 ([#2199](https://github.com/NousResearch/kclaw/pull/2199), [#2200](https://github.com/NousResearch/kclaw/pull/2200) by @llbn, [#2386](https://github.com/NousResearch/kclaw/pull/2386))
- 自动检测 HTML 标签并使用 `parse_mode=HTML` ([#1709](https://github.com/NousResearch/kclaw/pull/1709))
- Telegram 群组视觉支持 + 基于线程的会话 ([#2153](https://github.com/NousResearch/kclaw/pull/2153))
- 网络中断后自动重连轮询 ([#2517](https://github.com/NousResearch/kclaw/pull/2517))
- 分发前聚合拆分文本消息 ([#1674](https://github.com/NousResearch/kclaw/pull/1674))
- 修复：流式配置桥接、不变性、洪水控制 ([#1782](https://github.com/NousResearch/kclaw/pull/1782), [#1783](https://github.com/NousResearch/kclaw/pull/1783))
- 修复：edited_message 事件崩溃 ([#2074](https://github.com/NousResearch/kclaw/pull/2074))
- 修复：放弃前重试 409 轮询冲突 ([#2312](https://github.com/NousResearch/kclaw/pull/2312))
- 修复：通过 `platform:chat_id:thread_id` 格式的主题传递 ([#2455](https://github.com/NousResearch/kclaw/pull/2455))

### Discord 改进
- 文档缓存和文本文件注入 ([#2503](https://github.com/NousResearch/kclaw/pull/2503))
- 私信持久化打字指示器 ([#2468](https://github.com/NousResearch/kclaw/pull/2468))
- Discord 私信视觉 — 内联图片 + 附件分析 ([#2186](https://github.com/NousResearch/kclaw/pull/2186))
- Gateway 重启后保持线程参与 ([#1661](https://github.com/NousResearch/kclaw/pull/1661))
- 修复：非 ASCII 服务器名称时 gateway 崩溃 ([#2302](https://github.com/NousResearch/kclaw/pull/2302))
- 修复：线程权限错误 ([#2073](https://github.com/NousResearch/kclaw/pull/2073))
- 修复：线程中的斜杠事件路由 ([#2460](https://github.com/NousResearch/kclaw/pull/2460))
- 修复：移除有问题的跟进消息 + `/ask` 命令 ([#1836](https://github.com/NousResearch/kclaw/pull/1836))
- 修复：优雅的 WebSocket 重连 ([#2127](https://github.com/NousResearch/kclaw/pull/2127))
- 修复：启用流式输出时语音频道 TTS ([#2322](https://github.com/NousResearch/kclaw/pull/2322))

### WhatsApp 与其他适配器
- WhatsApp: 出站 `send_message` 路由 ([#1769](https://github.com/NousResearch/kclaw/pull/1769) by @sai-samarth)、LID 格式自聊 ([#1667](https://github.com/NousResearch/kclaw/pull/1667))、`reply_prefix` 配置修复 ([#1923](https://github.com/NousResearch/kclaw/pull/1923))、网桥子进程退出时重启 ([#2334](https://github.com/NousResearch/kclaw/pull/2334))、图片/网桥改进 ([#2181](https://github.com/NousResearch/kclaw/pull/2181))
- Matrix: 正确的 `reply_to_message_id` 参数 ([#1895](https://github.com/NousResearch/kclaw/pull/1895))、裸媒体类型修复 ([#1736](https://github.com/NousResearch/kclaw/pull/1736))
- Mattermost: 媒体附件的 MIME 类型 ([#2329](https://github.com/NousResearch/kclaw/pull/2329))

### Gateway 核心
- **自动重连** 失败平台，采用指数退避 ([#2584](https://github.com/NousResearch/kclaw/pull/2584))
- **会话自动重置时通知用户** ([#2519](https://github.com/NousResearch/kclaw/pull/2519))
- **回复消息上下文** 用于会话外回复 ([#1662](https://github.com/NousResearch/kclaw/pull/1662))
- **忽略未授权私信** 配置选项 ([#1919](https://github.com/NousResearch/kclaw/pull/1919))
- 修复：线程模式下 `/reset` 重置全局会话而非线程 ([#2254](https://github.com/NousResearch/kclaw/pull/2254))
- 修复：流式响应后传递 MEDIA: 文件 ([#2382](https://github.com/NousResearch/kclaw/pull/2382))
- 修复：限制中断递归深度以防止资源耗尽 ([#1659](https://github.com/NousResearch/kclaw/pull/1659))
- 修复：检测停止的进程并在 `--replace` 时释放过期锁 ([#2406](https://github.com/NousResearch/kclaw/pull/2406), [#1908](https://github.com/NousResearch/kclaw/pull/1908))
- 修复：基于 PID 的等待与强制终止用于 gateway 重启 ([#1902](https://github.com/NousResearch/kclaw/pull/1902))
- 修复：防止 `--replace` 模式杀死调用方进程 ([#2185](https://github.com/NousResearch/kclaw/pull/2185))
- 修复：`/model` 显示活动回退模型而非配置默认值 ([#1660](https://github.com/NousResearch/kclaw/pull/1660))
- 修复：会话尚不存在于 SQLite 时 `/title` 命令失败 ([#2379](https://github.com/NousResearch/kclaw/pull/2379) by @ten-jampa)
- 修复：agent 完成后处理 `/queue` 的消息 ([#2469](https://github.com/NousResearch/kclaw/pull/2469))
- 修复：剥离孤立的 `tool_results` + 让 `/reset` 绕过运行中的 agent ([#2180](https://github.com/NousResearch/kclaw/pull/2180))
- 修复：防止 agent 在 systemd 管理外启动 gateway ([#2617](https://github.com/NousResearch/kclaw/pull/2617))
- 修复：防止 gateway 连接失败时 systemd 重启风暴 ([#2327](https://github.com/NousResearch/kclaw/pull/2327))
- 修复：在 systemd unit 中包含解析的节点路径 ([#1767](https://github.com/NousResearch/kclaw/pull/1767) by @sai-samarth)
- 修复：在 gateway 外层异常处理器中向用户发送错误详情 ([#1966](https://github.com/NousResearch/kclaw/pull/1966))
- 修复：改进 429 用量限制和 500 上下文溢出的错误处理 ([#1839](https://github.com/NousResearch/kclaw/pull/1839))
- 修复：向启动警告检查添加所有缺失的平台白名单环境变量 ([#2628](https://github.com/NousResearch/kclaw/pull/2628))
- 修复：包含空格的文件路径媒体传递失败 ([#2621](https://github.com/NousResearch/kclaw/pull/2621))
- 修复：多平台 gateway 中重复会话键冲突 ([#2171](https://github.com/NousResearch/kclaw/pull/2171))
- 修复：Matrix 和 Mattermost 从不报告已连接 ([#1711](https://github.com/NousResearch/kclaw/pull/1711))
- 修复：PII 重写配置从未读取 — 缺少 yaml 导入 ([#1701](https://github.com/NousResearch/kclaw/pull/1701))
- 修复：skill 斜杠命令上的 NameError ([#1697](https://github.com/NousResearch/kclaw/pull/1697))
- 修复：在检查点中持久化 watcher 元数据用于崩溃恢复 ([#1706](https://github.com/NousResearch/kclaw/pull/1706))
- 修复：在 send_image_file、send_document、send_video 中传递 `message_thread_id` ([#2339](https://github.com/NousResearch/kclaw/pull/2339))
- 修复：快速连续照片消息的媒体组聚合 ([#2160](https://github.com/NousResearch/kclaw/pull/2160))

---

## 🔧 工具系统

### MCP 增强
- **MCP 服务器管理 CLI** + OAuth 2.1 PKCE 认证 ([#2465](https://github.com/NousResearch/kclaw/pull/2465))
- **将 MCP 服务器暴露为独立工具集** ([#1907](https://github.com/NousResearch/kclaw/pull/1907))
- **`kclaw tools` 中的交互式 MCP 工具配置** ([#1694](https://github.com/NousResearch/kclaw/pull/1694))
- 修复：MCP-OAuth 端口不匹配、路径遍历和共享处理程序状态 ([#2552](https://github.com/NousResearch/kclaw/pull/2552))
- 修复：会话重置后保留 MCP 工具注册 ([#2124](https://github.com/NousResearch/kclaw/pull/2124))
- 修复：并发文件访问崩溃 + 重复 MCP 注册 ([#2154](https://github.com/NousResearch/kclaw/pull/2154))
- 修复：规范化 MCP schema + 扩展会话列表列 ([#2102](https://github.com/NousResearch/kclaw/pull/2102))
- 修复：`tool_choice` `mcp_` 前缀处理 ([#1775](https://github.com/NousResearch/kclaw/pull/1775))

### Web 工具后端
- **Tavily** 作为网络搜索/提取/爬虫后端 ([#1731](https://github.com/NousResearch/kclaw/pull/1731))
- **Parallel** 作为替代网络搜索/提取后端 ([#1696](https://github.com/NousResearch/kclaw/pull/1696))
- **可配置 Web 后端** — Firecrawl/BeautifulSoup/Playwright 选择 ([#2256](https://github.com/NousResearch/kclaw/pull/2256))
- 修复：纯空白环境变量绕过 Web 后端检测 ([#2341](https://github.com/NousResearch/kclaw/pull/2341))

### 新工具
- **IMAP 邮件** 读取和发送 ([#2173](https://github.com/NousResearch/kclaw/pull/2173))
- **STT (语音转文字)** 工具，使用 Whisper API ([#2072](https://github.com/NousResearch/kclaw/pull/2072))
- **路由感知定价估算** ([#1695](https://github.com/NousResearch/kclaw/pull/1695))

### 工具改进
- TTS: OpenAI TTS 提供商的 `base_url` 支持 ([#2064](https://github.com/NousResearch/kclaw/pull/2064) by @hanai)
- Vision: 可配置超时、文件路径中的波浪号扩展、带多图和 base64 回退的私信视觉 ([#2480](https://github.com/NousResearch/kclaw/pull/2480), [#2585](https://github.com/NousResearch/kclaw/pull/2585), [#2211](https://github.com/NousResearch/kclaw/pull/2211))
- Browser: 会话创建中的竞态条件修复 ([#1721](https://github.com/NousResearch/kclaw/pull/1721))、意外 LLM 参数的类型错误 ([#1735](https://github.com/NousResearch/kclaw/pull/1735))
- File tools: 剥离 write_file 和 patch 内容中的 ANSI 转义码 ([#2532](https://github.com/NousResearch/kclaw/pull/2532))、在重复搜索键中包含分页参数 ([#1824](https://github.com/NousResearch/kclaw/pull/1824) by @cutepawss)、改进模糊匹配精度 + 位置计算重构 ([#2096](https://github.com/NousResearch/kclaw/pull/2096), [#1681](https://github.com/NousResearch/kclaw/pull/1681))
- Code execution: 资源泄漏和双重 socket 关闭修复 ([#2381](https://github.com/NousResearch/kclaw/pull/2381))
- Delegate: 并发子 agent 委托的线程安全 ([#1672](https://github.com/NousResearch/kclaw/pull/1672))、委托后保留父 agent 的工具列表 ([#1778](https://github.com/NousResearch/kclaw/pull/1778))
- 修复：使并发工具批处理路径感知文件变更 ([#1914](https://github.com/NousResearch/kclaw/pull/1914))
- 修复：在平台分发前分割 `send_message_tool` 中的长消息 ([#1646](https://github.com/NousResearch/kclaw/pull/1646))
- 修复：添加缺失的 'messaging' 工具集 ([#1718](https://github.com/NousResearch/kclaw/pull/1718))
- 修复：防止不可用工具名称泄露到模型 schema ([#2072](https://github.com/NousResearch/kclaw/pull/2072))
- 修复：按引用传递访问集合以防止菱形依赖重复 ([#2311](https://github.com/NousResearch/kclaw/pull/2311))
- 修复：Daytona sandbox 查找从 `find_one` 迁移到 `get/list` ([#2063](https://github.com/NousResearch/kclaw/pull/2063) by @rovle)

---

## 🧩 Skills 生态系统

### Skills 系统改进
- **Agent 创建的 skills** — 允许 Caution 级别发现，危险 skills 询问而非阻止 ([#1840](https://github.com/NousResearch/kclaw/pull/1840), [#2446](https://github.com/NousResearch/kclaw/pull/2446))
- **`--yes` 标志** 在 `/skills install` 和卸载时绕过确认 ([#1647](https://github.com/NousResearch/kclaw/pull/1647))
- **跨横幅、系统提示和斜杠命令尊重禁用的 skills** ([#1897](https://github.com/NousResearch/kclaw/pull/1897))
- 修复：skills custom_tools 导入崩溃 + sandbox file_tools 集成 ([#2239](https://github.com/NousResearch/kclaw/pull/2239))
- 修复：带 pip requirements 的 agent 创建 skills 安装时崩溃 ([#2145](https://github.com/NousResearch/kclaw/pull/2145))
- 修复：`Skills.__init__` 中 `hub.yaml` 缺失时的竞态条件 ([#2242](https://github.com/NousResearch/kclaw/pull/2242))
- 修复：安装前验证 skill 元数据并阻止重复 ([#2241](https://github.com/NousResearch/kclaw/pull/2241))
- 修复：skills hub inspect/resolve — inspect、重定向、发现、tap 列表中的 4 个 bug ([#2447](https://github.com/NousResearch/kclaw/pull/2447))
- 修复：会话重置后 agent 创建的 skills 继续工作 ([#2121](https://github.com/NousResearch/kclaw/pull/2121))

### 新 Skills
- **OCR-and-documents** — PDF/DOCX/XLS/PPTX/图片 OCR，可选 GPU ([#2236](https://github.com/NousResearch/kclaw/pull/2236), [#2461](https://github.com/NousResearch/kclaw/pull/2461))
- **Huggingface-hub** 捆绑 skill ([#1921](https://github.com/NousResearch/kclaw/pull/1921))
- **Sherlock OSINT** 用户名搜索 ([#1671](https://github.com/NousResearch/kclaw/pull/1671))
- **Meme-generation** — 使用 Pillow 的图片生成器 ([#2344](https://github.com/NousResearch/kclaw/pull/2344))
- **Bioinformatics** gateway skill — 索引至 400+ 生物 skills ([#2387](https://github.com/NousResearch/kclaw/pull/2387))
- **Inference.sh** skill (终端版) ([#1686](https://github.com/NousResearch/kclaw/pull/1686))
- **Base blockchain** 可选 skill ([#1643](https://github.com/NousResearch/kclaw/pull/1643))
- **3D-model-viewer** 可选 skill ([#2226](https://github.com/NousResearch/kclaw/pull/2226))
- **FastMCP** 可选 skill ([#2113](https://github.com/NousResearch/kclaw/pull/2113))
- **KClaw-agent-setup** skill ([#1905](https://github.com/NousResearch/kclaw/pull/1905))

---

## 🔌 插件系统增强

- **TUI 扩展钩子** — 在 KClaw 之上构建自定义 CLI ([#2333](https://github.com/NousResearch/kclaw/pull/2333))
- **`kclaw plugins install/remove/list`** 命令 ([#2337](https://github.com/NousResearch/kclaw/pull/2337))
- **插件斜杠命令注册** ([#2359](https://github.com/NousResearch/kclaw/pull/2359))
- **`session:end` 生命周期事件** 钩子 ([#1725](https://github.com/NousResearch/kclaw/pull/1725))
- 修复：项目插件发现需要 opt-in ([#2215](https://github.com/NousResearch/kclaw/pull/2215))

---

## 🔒 安全与可靠性

### 安全
- **Vision tools 和 Web tools 的 SSRF 防护** ([#2679](https://github.com/NousResearch/kclaw/pull/2679))
- **`_expand_path` 中的 shell 注入防护** 通过 `~user` 路径后缀 ([#2685](https://github.com/NousResearch/kclaw/pull/2685))
- **阻止不受信任的浏览器来源** API 服务器访问 ([#2451](https://github.com/NousResearch/kclaw/pull/2451))
- **阻止沙箱后端凭据** 从子进程环境泄露 ([#1658](https://github.com/NousResearch/kclaw/pull/1658))
- **阻止 @ 引用** 读取工作区外的密钥 ([#2601](https://github.com/NousResearch/kclaw/pull/2601) by @Gutslabs)
- **终端工具恶意代码模式预执行扫描器** ([#2245](https://github.com/NousResearch/kclaw/pull/2245))
- **加强终端安全** 和沙箱文件写入 ([#1653](https://github.com/NousResearch/kclaw/pull/1653))
- **PKCE 验证器泄露修复** + OAuth 刷新 Content-Type ([#1775](https://github.com/NousResearch/kclaw/pull/1775))
- **消除 `execute()` 调用中的 SQL 字符串格式化** ([#2061](https://github.com/NousResearch/kclaw/pull/2061) by @dusterbloom)
- **加强 Jobs API** — 输入限制、字段白名单、启动检查 ([#2456](https://github.com/NousResearch/kclaw/pull/2456))

### 可靠性
- 4 个 SessionDB 方法的线程锁 ([#1704](https://github.com/NousResearch/kclaw/pull/1704))
- 并发内存写入的文件锁 ([#1726](https://github.com/NousResearch/kclaw/pull/1726))
- 优雅处理 OpenRouter 错误 ([#2112](https://github.com/NousResearch/kclaw/pull/2112))
- guard print() 调用防止 OSError ([#1668](https://github.com/NousResearch/kclaw/pull/1668))
- 安全处理重写格式化器中的非字符串输入 ([#2392](https://github.com/NousResearch/kclaw/pull/2392), [#1700](https://github.com/NousResearch/kclaw/pull/1700))
- ACP: 模型切换时保留会话提供商，将会话持久化到磁盘 ([#2380](https://github.com/NousResearch/kclaw/pull/2380), [#2071](https://github.com/NousResearch/kclaw/pull/2071))
- API 服务器: 跨重启持久化 ResponseStore 到 SQLite ([#2472](https://github.com/NousResearch/kclaw/pull/2472))
- 修复：`fetch_nous_models` 始终因位置参数 TypeError ([#1699](https://github.com/NousResearch/kclaw/pull/1699))
- 修复：解决 cli.py 中合并冲突标记导致启动中断 ([#2347](https://github.com/NousResearch/kclaw/pull/2347))
- 修复：`minisweagent_path.py` 未包含在 wheel 中 ([#2098](https://github.com/NousResearch/kclaw/pull/2098) by @JiwaniZakir)

### Cron 系统
- **`[SILENT]` 响应** — cron agents 可以抑制传递 ([#1833](https://github.com/NousResearch/kclaw/pull/1833))
- **随调度频率缩放错过的任务宽限期** ([#2449](https://github.com/NousResearch/kclaw/pull/2449))
- **恢复最近的一次性任务** ([#1918](https://github.com/NousResearch/kclaw/pull/1918))
- 修复：将 `repeat<=0` 规范化为 None — 当 LLM 传递 -1 时任务在首次运行后删除 ([#2612](https://github.com/NousResearch/kclaw/pull/2612) by @Mibayy)
- 修复：Matrix 添加到调度器传递 platform_map ([#2167](https://github.com/NousResearch/kclaw/pull/2167) by @buntingszn)
- 修复：无时区的 naive ISO 时间戳 — 任务在错误时间触发 ([#1729](https://github.com/NousResearch/kclaw/pull/1729))
- 修复：`get_due_jobs` 读取 `jobs.json` 两次 — 竞态条件 ([#1716](https://github.com/NousResearch/kclaw/pull/1716))
- 修复：静默任务返回空响应用于跳过传递 ([#2442](https://github.com/NousResearch/kclaw/pull/2442))
- 修复：停止将 cron 输出注入 gateway 会话历史 ([#2313](https://github.com/NousResearch/kclaw/pull/2313))
- 修复：`asyncio.run()` 抛出 RuntimeError 时关闭废弃的协程 ([#2317](https://github.com/NousResearch/kclaw/pull/2317))

---

## 🧪 测试

- 解决所有持续失败的测试 ([#2488](https://github.com/NousResearch/kclaw/pull/2488))
- 替换 `FakePath` 为 `monkeypatch` 以兼容 Python 3.12 ([#2444](https://github.com/NousResearch/kclaw/pull/2444))
- 对齐 KClaw 设置和完整套件预期 ([#1710](https://github.com/NousResearch/kclaw/pull/1710))

---

## 📚 文档

- 近期功能的综合文档更新 ([#1693](https://github.com/NousResearch/kclaw/pull/1693), [#2183](https://github.com/NousResearch/kclaw/pull/2183))
- 阿里云和钉钉设置指南 ([#1687](https://github.com/NousResearch/kclaw/pull/1687), [#1692](https://github.com/NousResearch/kclaw/pull/1692))
- 详细的 skills 文档 ([#2244](https://github.com/NousResearch/kclaw/pull/2244))
- Honcho 自托管 / Docker 配置 ([#2475](https://github.com/NousResearch/kclaw/pull/2475))
- 上下文长度检测 FAQ 和快速入门参考 ([#2179](https://github.com/NousResearch/kclaw/pull/2179))
- 修复参考和用户指南中的文档不一致 ([#1995](https://github.com/NousResearch/kclaw/pull/1995))
- 修复 MCP 安装命令 — 使用 uv 而非裸 pip ([#1909](https://github.com/NousResearch/kclaw/pull/1909))
- 用 Mermaid/列表替换 ASCII 图表 ([#2402](https://github.com/NousResearch/kclaw/pull/2402))
- Gemini OAuth 提供商实现计划 ([#2467](https://github.com/NousResearch/kclaw/pull/2467))
- Discord 服务器成员 Intent 标记为必需 ([#2330](https://github.com/NousResearch/kclaw/pull/2330))
- 修复 api-server.md 中的 MDX 构建错误 ([#1787](https://github.com/NousResearch/kclaw/pull/1787))
- 对齐 venv 路径与安装程序 ([#2114](https://github.com/NousResearch/kclaw/pull/2114))
- 新 skills 添加到 hub 索引 ([#2281](https://github.com/NousResearch/kclaw/pull/2281))

---

## 👥 贡献者

### 核心
- **@teknium1** (Teknium) — 280 个 PR

### 社区贡献者
- **@mchzimm** (to_the_max) — GitHub Copilot 提供商集成 ([#1879](https://github.com/NousResearch/kclaw/pull/1879))
- **@jquesnelle** (Jeffrey Quesnelle) — 每线程持久化事件循环修复 ([#2214](https://github.com/NousResearch/kclaw/pull/2214))
- **@llbn** (lbn) — Telegram MarkdownV2 删除线、spoiler、引用块和转义修复 ([#2199](https://github.com/NousResearch/kclaw/pull/2199), [#2200](https://github.com/NousResearch/kclaw/pull/2200))
- **@dusterbloom** — SQL 注入防护 + 本地服务器上下文窗口查询 ([#2061](https://github.com/NousResearch/kclaw/pull/2061), [#2091](https://github.com/NousResearch/kclaw/pull/2091))
- **@0xbyt4** — Anthropic tool_calls None 防护 + OpenCode-Go 提供商配置修复 ([#2209](https://github.com/NousResearch/kclaw/pull/2209), [#2393](https://github.com/NousResearch/kclaw/pull/2393))
- **@sai-samarth** (Saisamarth) — WhatsApp send_message 路由 + systemd 节点路径 ([#1769](https://github.com/NousResearch/kclaw/pull/1769), [#1767](https://github.com/NousResearch/kclaw/pull/1767))
- **@Gutslabs** (Guts) — 阻止 @ 引用读取密钥 ([#2601](https://github.com/NousResearch/kclaw/pull/2601))
- **@Mibayy** (Mibay) — Cron 任务重复规范化 ([#2612](https://github.com/NousResearch/kclaw/pull/2612))
- **@ten-jampa** (Tenzin Jampa) — Gateway /title 命令修复 ([#2379](https://github.com/NousResearch/kclaw/pull/2379))
- **@cutepawss** (lila) — File tools 搜索分页修复 ([#1824](https://github.com/NousResearch/kclaw/pull/1824))
- **@hanai** (Hanai) — OpenAI TTS base_url 支持 ([#2064](https://github.com/NousResearch/kclaw/pull/2064))
- **@rovle** (Lovre Pešut) — Daytona sandbox API 迁移 ([#2063](https://github.com/NousResearch/kclaw/pull/2063))
- **@buntingszn** (bunting szn) — Matrix cron 传递支持 ([#2167](https://github.com/NousResearch/kclaw/pull/2167))
- **@InB4DevOps** — 新会话时 token 计数器重置 ([#2101](https://github.com/NousResearch/kclaw/pull/2101))
- **@JiwaniZakir** (Zakir Jiwani) — wheel 中缺失文件修复 ([#2098](https://github.com/NousResearch/kclaw/pull/2098))
- **@ygd58** (buray) — 委托工具父工具名称修复 ([#2083](https://github.com/NousResearch/kclaw/pull/2083))

---

**完整变更日志**: [v2026.3.17...v2026.3.23](https://github.com/NousResearch/kclaw/compare/v2026.3.17...v2026.3.23)
