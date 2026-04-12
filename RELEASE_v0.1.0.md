# KClaw Agent v0.1.0 (v2026.4.8)

**发布日期：** 2026年4月8日

> 智能版本发布 — 后台任务自动通知、Nous Portal 免费 MiMo v2 Pro、跨平台实时模型切换、自优化 GPT/Codex 工具调用指导、原生 Google AI Studio、智能空闲超时、审批按钮、MCP OAuth 2.1，以及 209 个合并 PR 与 82 个已解决 issues。

---

## ✨ 亮点

- **后台进程自动通知（`notify_on_complete`）** — 后台任务现可在完成时自动通知 agent。启动长时间运行的进程（AI 模型训练、测试套件、部署、构建），agent 会在完成时收到通知 — 无需轮询。Agent 可以继续处理其他任务，在结果返回时获取。 ([#5779](https://github.com/NousResearch/kclaw/pull/5779))

- **Nous Portal 免费 Xiaomi MiMo v2 Pro** — Nous Portal 现支持免费版 Xiaomi MiMo v2 Pro 模型用于辅助任务（压缩、视觉、摘要），并在模型选择中提供免费版模型限制和价格显示。 ([#6018](https://github.com/NousResearch/kclaw/pull/6018), [#5880](https://github.com/NousResearch/kclaw/pull/5880))

- **实时模型切换（`/model` 命令）** — 可在 CLI、Telegram、Discord、Slack 或任何网关平台中在会话中途切换模型和提供商。聚合器感知的解析会在可能时保持使用 OpenRouter/Nous，并在需要时自动跨提供商回退。Telegram 和 Discord 上的交互式模型选择器，带内联按钮。 ([#5181](https://github.com/NousResearch/kclaw/pull/5181), [#5742](https://github.com/NousResearch/kclaw/pull/5742))

- **自优化 GPT/Codex 工具调用指导** — Agent 通过自动化行为基准测试诊断并修复了 GPT 和 Codex 工具调用中的 5 个故障模式，显著提高了 OpenAI 模型的可靠性。包括执行规范指导和用于结构化推理的仅思考预填充延续。 ([#6120](https://github.com/NousResearch/kclaw/pull/6120), [#5414](https://github.com/NousResearch/kclaw/pull/5414), [#5931](https://github.com/NousResearch/kclaw/pull/5931))

- **Google AI Studio (Gemini) 原生提供商** — 通过 Google 的 AI Studio API 直接访问 Gemini 模型。包括自动 models.dev 注册表集成，可跨任何提供商实时检测上下文长度。 ([#5577](https://github.com/NousResearch/kclaw/pull/5577))

- **基于空闲的 Agent 超时** — 网关和 cron 超时现追踪实际工具活动而非挂钟时间。正在活跃工作的长时间运行任务不会被终止 — 只有真正空闲的 agent 才会超时。 ([#5389](https://github.com/NousResearch/kclaw/pull/5389), [#5440](https://github.com/NousResearch/kclaw/pull/5440))

- **Slack 和 Telegram 上的审批按钮** — 通过原生平台按钮进行危险命令审批，无需输入 `/approve`。Slack 保留线程上下文；Telegram 使用表情符号反应显示审批状态。 ([#5890](https://github.com/NousResearch/kclaw/pull/5890), [#5975](https://github.com/NousResearch/kclaw/pull/5975))

- **MCP OAuth 2.1 PKCE + OSV 恶意软件扫描** — 符合完整标准的 MCP 服务器认证 OAuth 支持，加上通过 OSV 漏洞数据库对 MCP 扩展包的自动恶意软件扫描。 ([#5420](https://github.com/NousResearch/kclaw/pull/5420), [#5305](https://github.com/NousResearch/kclaw/pull/5305))

- **集中式日志与配置验证** — 结构化日志写入 `~/.kclaw/logs/`（agent.log + errors.log），并提供 `kclaw logs` 命令用于跟踪和过滤。配置结构验证在启动时检测格式错误的 YAML，避免后续出现神秘故障。 ([#5430](https://github.com/NousResearch/kclaw/pull/5430), [#5426](https://github.com/NousResearch/kclaw/pull/5426))

- **插件系统扩展** — 插件现可注册 CLI 子命令、接收带关联 ID 的请求作用域 API 钩子、在安装时提示必需的环境变量，并挂钩会话生命周期事件（finalize/reset）。 ([#5295](https://github.com/NousResearch/kclaw/pull/5295), [#5427](https://github.com/NousResearch/kclaw/pull/5427), [#5470](https://github.com/NousResearch/kclaw/pull/5470), [#6129](https://github.com/NousResearch/kclaw/pull/6129))

- **Matrix Tier 1 与平台加固** — Matrix 新增表情反应、已读回执、富格式和房间管理。Discord 新增频道控制和忽略频道功能。Signal 实现完整的 MEDIA: 标签传递。Mattermost 实现文件附件。跨所有平台的全面可靠性修复。 ([#5275](https://github.com/NousResearch/kclaw/pull/5275), [#5975](https://github.com/NousResearch/kclaw/pull/5975), [#5602](https://github.com/NousResearch/kclaw/pull/5602))

- **安全加固审查** — 整合 SSRF 防护、时间攻击缓解、tar 遍历预防、凭据泄露保护、cron 路径遍历加固和跨会话隔离。所有后端的终端工作目录清理。 ([#5944](https://github.com/NousResearch/kclaw/pull/5944), [#5613](https://github.com/NousResearch/kclaw/pull/5613), [#5629](https://github.com/NousResearch/kclaw/pull/5629))

---

## 🏗️ 核心 Agent 与架构

### 提供商与模型支持
- **原生 Google AI Studio (Gemini) 提供商**，集成 models.dev 自动检测上下文长度 ([#5577](https://github.com/NousResearch/kclaw/pull/5577))
- **`/model` 命令 — 完整的提供商+模型系统重构** — 跨 CLI 和所有网关平台实时切换，聚合器感知解析 ([#5181](https://github.com/NousResearch/kclaw/pull/5181))
- **Telegram 和 Discord 交互式模型选择器** — 基于内联按钮的模型选择 ([#5742](https://github.com/NousResearch/kclaw/pull/5742))
- **Nous Portal 免费版模型限制**，在模型选择中显示价格 ([#5880](https://github.com/NousResearch/kclaw/pull/5880))
- **OpenRouter 和 Nous Portal 提供商的模型价格显示** ([#5416](https://github.com/NousResearch/kclaw/pull/5416))
- **xAI (Grok) 提示缓存**，通过 `x-grok-conv-id` 头实现 ([#5604](https://github.com/NousResearch/kclaw/pull/5604))
- **Grok 添加至工具使用强制执行模型**，用于直接 xAI 使用 ([#5595](https://github.com/NousResearch/kclaw/pull/5595))
- **MiniMax TTS 提供商** (speech-2.8) ([#4963](https://github.com/NousResearch/kclaw/pull/4963))
- **非代理模型警告** — 当加载不适合工具使用的 KClaw LLM 模型时警告用户 ([#5378](https://github.com/NousResearch/kclaw/pull/5378))
- **Ollama Cloud 认证、`/model` 切换持久化和别名补全** ([#5269](https://github.com/NousResearch/kclaw/pull/5269))
- **保留 OpenCode Go 模型名称中的点** (minimax-m2.7, glm-4.5, kimi-k2.5) ([#5597](https://github.com/NousResearch/kclaw/pull/5597))
- **MiniMax 模型 404 修复** — 为 OpenCode Go 去除 Anthropic 基础 URL 的 /v1 ([#4918](https://github.com/NousResearch/kclaw/pull/4918))
- **提供商凭据重置窗口** 在池化故障转移中得到遵守 ([#5188](https://github.com/NousResearch/kclaw/pull/5188))
- **凭据池与凭据文件之间的 OAuth 令牌同步** ([#4981](https://github.com/NousResearch/kclaw/pull/4981))
- **过期的 OAuth 凭据** 不再阻止自动检测时的 OpenRouter 用户 ([#5746](https://github.com/NousResearch/kclaw/pull/5746))
- **Codex OAuth 凭据池断开连接** + 过期令牌导入修复 ([#5681](https://github.com/NousResearch/kclaw/pull/5681))
- **Codex 池条目同步** — 从 `~/.codex/auth.json` 在耗尽时导入 — @GratefulDave ([#5610](https://github.com/NousResearch/kclaw/pull/5610))
- **辅助客户端付款回退** — 在 402 时使用下一个提供商重试 ([#5599](https://github.com/NousResearch/kclaw/pull/5599))
- **辅助客户端解析命名自定义提供商** 和 'main' 别名 ([#5978](https://github.com/NousResearch/kclaw/pull/5978))
- **在 Nous 免费版上使用 mimo-v2-pro** 处理非视觉辅助任务 ([#6018](https://github.com/NousResearch/kclaw/pull/6018))
- **视觉自动检测** 优先尝试主提供商 ([#6041](https://github.com/NousResearch/kclaw/pull/6041))
- **提供商排序和快速安装** — @austinpickett ([#4664](https://github.com/NousResearch/kclaw/pull/4664))
- **Nous OAuth access_token** 不再用作推理 API 密钥 — @SHL0MS ([#5564](https://github.com/NousResearch/kclaw/pull/5564))
- **KCLAW_PORTAL_BASE_URL 环境变量** 在 Nous 登录期间被尊重 — @benbarclay ([#5745](https://github.com/NousResearch/kclaw/pull/5745))
- **Nous portal/inference URL 的环境变量覆盖** ([#5419](https://github.com/NousResearch/kclaw/pull/5419))
- **Z.AI 端点自动检测** 通过探测和缓存 ([#5763](https://github.com/NousResearch/kclaw/pull/5763))
- **MiniMax 上下文长度、模型目录、思考防护、辅助模型和配置 base_url** 修正 ([#6082](https://github.com/NousResearch/kclaw/pull/6082))
- **社区提供商/模型解析修复** — 挽救了 4 个社区 PR + MiniMax 辅助 URL ([#5983](https://github.com/NousResearch/kclaw/pull/5983))

### Agent 循环与对话
- **自优化 GPT/Codex 工具调用指导** 通过自动化行为基准测试 — agent 自我诊断并修复了 5 个故障模式 ([#6120](https://github.com/NousResearch/kclaw/pull/6120))
- **GPT/Codex 执行规范指导** 在系统提示中 ([#5414](https://github.com/NousResearch/kclaw/pull/5414))
- **仅思考预填充延续** 用于结构化推理响应 ([#5931](https://github.com/NousResearch/kclaw/pull/5931))
- **接受仅推理响应** 无需重试 — 将内容设置为 "(empty)" 而非无限重试 ([#5278](https://github.com/NousResearch/kclaw/pull/5278))
- **抖动重试退避** — API 重试的带抖动指数退避 ([#6048](https://github.com/NousResearch/kclaw/pull/6048))
- **智能思考块签名管理** — 在多次对话中保留和管理 Anthropic 思考签名 ([#6112](https://github.com/NousResearch/kclaw/pull/6112))
- **强制工具调用参数** 匹配 JSON Schema 类型 — 修复发送字符串而非数字/布尔值的模型 ([#5265](https://github.com/NousResearch/kclaw/pull/5265))
- **将过大的工具结果保存到文件** 而非破坏性截断 ([#5210](https://github.com/NousResearch/kclaw/pull/5210))
- **沙箱感知的工具结果持久化** ([#6085](https://github.com/NousResearch/kclaw/pull/6085))
- **编辑失败后改进了流式回退** ([#6110](https://github.com/NousResearch/kclaw/pull/6110))
- **Codex 空输出间隙** 在回退 + 规范化器 + 辅助客户端中覆盖 ([#5724](https://github.com/NousResearch/kclaw/pull/5724), [#5730](https://github.com/NousResearch/kclaw/pull/5730), [#5734](https://github.com/NousResearch/kclaw/pull/5734))
- **Codex 流输出从 output_item.done 事件回填** ([#5689](https://github.com/NousResearch/kclaw/pull/5689))
- **流消费者在工具边界后创建新消息** ([#5739](https://github.com/NousResearch/kclaw/pull/5739))
- **Codex 验证与空流输出的规范化对齐** ([#5940](https://github.com/NousResearch/kclaw/pull/5940))
- **在 copilot-acp 适配器中桥接工具调用** ([#5460](https://github.com/NousResearch/kclaw/pull/5460))
- **从 chat-completions 负载中过滤仅转录角色** ([#4880](https://github.com/NousResearch/kclaw/pull/4880))
- **温度限制模型上的上下文压缩失败已修复** — @MadKangYu ([#5608](https://github.com/NousResearch/kclaw/pull/5608))
- **为所有严格 API 清理 tool_calls** (Fireworks、Mistral 等) — @lumethegreat ([#5183](https://github.com/NousResearch/kclaw/pull/5183))

### 内存与会话
- **Supermemory 内存提供程序** — 新内存插件，支持多容器、search_mode、身份模板和环境变量覆盖 ([#5737](https://github.com/NousResearch/kclaw/pull/5737), [#5933](https://github.com/NousResearch/kclaw/pull/5933))
- **默认共享线程会话** — 跨网关平台的多用户线程支持 ([#5391](https://github.com/NousResearch/kclaw/pull/5391))
- **子 agent 会话链接到父会话** 并从会话列表中隐藏 ([#5309](https://github.com/NousResearch/kclaw/pull/5309))
- **配置文件作用域的内存隔离和克隆支持** ([#4845](https://github.com/NousResearch/kclaw/pull/4845))
- **线程网关 user_id 到内存插件**，实现每用户作用域 ([#5895](https://github.com/NousResearch/kclaw/pull/5895))
- **Honcho 插件漂移大修 + 插件 CLI 注册系统** ([#5295](https://github.com/NousResearch/kclaw/pull/5295))
- **保留 Honcho 全息提示和信任评分渲染** ([#4872](https://github.com/NousResearch/kclaw/pull/4872))
- **Honcho doctor 修复** — 使用 recall_mode 而非 memory_mode — @techguysimon ([#5645](https://github.com/NousResearch/kclaw/pull/5645))
- **RetainDB** — API 路由、写入队列、辩证法、agent 模型、文件工具修复 ([#5461](https://github.com/NousResearch/kclaw/pull/5461))
- **Hindsight 内存插件大修 + 内存设置向导修复** ([#5094](https://github.com/NousResearch/kclaw/pull/5094))
- **mem0 API v2 兼容性**、预取上下文隔离、秘密编辑 ([#5423](https://github.com/NousResearch/kclaw/pull/5423))
- **mem0 环境变量与 mem0.json 合并** 而非二选一 ([#4939](https://github.com/NousResearch/kclaw/pull/4939))
- **清洁用户消息** 用于所有内存提供程序操作 ([#4940](https://github.com/NousResearch/kclaw/pull/4940))
- **`/new` 和 `/resume` 上的静默内存刷新失败已修复** — @ryanautomated ([#5640](https://github.com/NousResearch/kclaw/pull/5640))
- **OpenViking atexit 安全网** 用于会话提交 ([#5664](https://github.com/NousResearch/kclaw/pull/5664))
- **OpenViking 租户作用域头** 用于多租户服务器 ([#4936](https://github.com/NousResearch/kclaw/pull/4936))
- **ByteRover brv 查询** 在 LLM 调用前同步运行 ([#4831](https://github.com/NousResearch/kclaw/pull/4831))

---

## 📱 消息平台（网关）

### 网关核心
- **基于空闲的 agent 超时** — 用智能活动追踪替代挂钟超时；正在活跃工作的长时间运行任务不会被终止 ([#5389](https://github.com/NousResearch/kclaw/pull/5389))
- **Slack 和 Telegram 的审批按钮** + Slack 线程上下文保留 ([#5890](https://github.com/NousResearch/kclaw/pull/5890))
- **实时流式 `/update` 输出** + 将交互式提示转发给用户 ([#5180](https://github.com/NousResearch/kclaw/pull/5180))
- **无限超时支持** + 定期通知 + 可操作错误消息 ([#4959](https://github.com/NousResearch/kclaw/pull/4959))
- **重复消息预防** — 网关去重 + 部分流守护 ([#4878](https://github.com/NousResearch/kclaw/pull/4878))
- **Webhook delivery_info 持久化** + `/status` 中完整会话 ID ([#5942](https://github.com/NousResearch/kclaw/pull/5942))
- **工具预览截断** 在 all/new 进度模式下尊重 tool_preview_length ([#5937](https://github.com/NousResearch/kclaw/pull/5937))
- **简短预览截断** 为 all/new 工具进度模式恢复 ([#4935](https://github.com/NousResearch/kclaw/pull/4935))
- **原子写入更新待处理状态** 防止损坏 ([#4923](https://github.com/NousResearch/kclaw/pull/4923))
- **审批会话密钥按轮隔离** ([#4884](https://github.com/NousResearch/kclaw/pull/4884))
- **`/approve`、`/deny`、`/stop`、`/new` 的活动会话守卫绕过** ([#4926](https://github.com/NousResearch/kclaw/pull/4926), [#5765](https://github.com/NousResearch/kclaw/pull/5765))
- **审批等待期间暂停打字指示器** ([#5893](https://github.com/NousResearch/kclaw/pull/5893))
- **精确逐行匹配** 而非子字符串用于标题检查（所有平台）([#5939](https://github.com/NousResearch/kclaw/pull/5939))
- **从流式网关消息中剥离 MEDIA: 标签** ([#5152](https://github.com/NousResearch/kclaw/pull/5152))
- **在发送前从 cron 传递中提取 MEDIA: 标签** ([#5598](https://github.com/NousResearch/kclaw/pull/5598))
- **配置文件感知的服务单元** + 语音转录清理 ([#5972](https://github.com/NousResearch/kclaw/pull/5972))
- **线程安全的 PairingStore** 原子写入 — @CharlieKerfoot ([#5656](https://github.com/NousResearch/kclaw/pull/5656))
- **基础平台日志中清理媒体 URL** — @WAXLYY ([#5631](https://github.com/NousResearch/kclaw/pull/5631))
- **减少 Telegram 回退 IP 激活日志噪音** — @MadKangYu ([#5615](https://github.com/NousResearch/kclaw/pull/5615))
- **Cron 静态方法包装器** 防止自绑定 ([#5299](https://github.com/NousResearch/kclaw/pull/5299))
- **过时的 'kclaw login' 替换为 'kclaw auth'** + 凭据移除重新播种修复 ([#5670](https://github.com/NousResearch/kclaw/pull/5670))

### Telegram
- **群组主题技能绑定** 用于超级群组论坛主题 ([#4886](https://github.com/NousResearch/kclaw/pull/4886))
- **表情符号反应** 用于审批状态和通知 ([#5975](https://github.com/NousResearch/kclaw/pull/5975))
- **防止发送超时时重复消息传递** ([#5153](https://github.com/NousResearch/kclaw/pull/5153))
- **命令名称清理** 去除无效字符 ([#5596](https://github.com/NousResearch/kclaw/pull/5596))
- **Telegram 菜单和网关调度中尊重每平台禁用的技能** ([#4799](https://github.com/NousResearch/kclaw/pull/4799))
- **`/approve` 和 `/deny`** 通过运行中 agent 守卫路由 ([#4798](https://github.com/NousResearch/kclaw/pull/4798))

### Discord
- **频道控制** — ignored_channels 和 no_thread_channels 配置选项 ([#5975](https://github.com/NousResearch/kclaw/pull/5975))
- **技能注册为原生斜杠命令** 通过共享网关逻辑 ([#5603](https://github.com/NousResearch/kclaw/pull/5603))
- **`/approve`、`/deny`、`/queue`、`/background`、`/btw` 注册为原生斜杠命令** ([#4800](https://github.com/NousResearch/kclaw/pull/4800), [#5477](https://github.com/NousResearch/kclaw/pull/5477))
- **启动时移除不必要的 members intent** + token 锁泄漏修复 ([#5302](https://github.com/NousResearch/kclaw/pull/5302))

### Slack
- **线程互动** — 在机器人启动的线程和被提及的线程中自动回复 ([#5897](https://github.com/NousResearch/kclaw/pull/5897))
- **edit_message 中的 mrkdwn** + 线程回复不带 @mentions ([#5733](https://github.com/NousResearch/kclaw/pull/5733))

### Matrix
- **Tier 1 功能对等** — 表情反应、已读回执、富格式、房间管理 ([#5275](https://github.com/NousResearch/kclaw/pull/5275))
- **MATRIX_REQUIRE_MENTION 和 MATRIX_AUTO_THREAD 支持** ([#5106](https://github.com/NousResearch/kclaw/pull/5106))
- **全面可靠性** — 加密媒体、认证恢复、cron E2EE、Synapse 兼容性 ([#5271](https://github.com/NousResearch/kclaw/pull/5271))
- **CJK 输入、E2EE 和重连修复** ([#5665](https://github.com/NousResearch/kclaw/pull/5665))

### Signal
- **完整的 MEDIA: 标签传递** — send_image_file、send_voice 和 send_video 已实现 ([#5602](https://github.com/NousResearch/kclaw/pull/5602))

### Mattermost
- **文件附件** — 当帖子有文件附件时将消息类型设置为 DOCUMENT — @nericervin ([#5609](https://github.com/NousResearch/kclaw/pull/5609))

### 飞书
- **交互卡片审批按钮** ([#6043](https://github.com/NousResearch/kclaw/pull/6043))
- **重连和 ACL 修复** ([#5665](https://github.com/NousResearch/kclaw/pull/5665))

### Webhooks
- **`{__raw__}` 模板令牌** 和论坛主题的 thread_id 传递 ([#5662](https://github.com/NousResearch/kclaw/pull/5662))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **延迟响应内容** 直到推理块完成 ([#5773](https://github.com/NousResearch/kclaw/pull/5773))
- **终端调整大小时清除幽灵状态栏行** ([#4960](https://github.com/NousResearch/kclaw/pull/4960))
- **规范化粘贴文本中的 \r\n 和 \r 行尾** ([#4849](https://github.com/NousResearch/kclaw/pull/4849))
- **ChatConsole 错误、curses 滚动、皮肤感知横幅、git 状态横幅修复** ([#5974](https://github.com/NousResearch/kclaw/pull/5974))
- **原生 Windows 图像粘贴支持** ([#5917](https://github.com/NousResearch/kclaw/pull/5917))
- **`--yolo` 和其他标志** 在 'chat' 子命令前放置时不再静默丢弃 ([#5145](https://github.com/NousResearch/kclaw/pull/5145))

### 设置与配置
- **配置结构验证** — 在启动时检测格式错误的 YAML，提供可操作错误消息 ([#5426](https://github.com/NousResearch/kclaw/pull/5426))
- **集中式日志** 到 `~/.kclaw/logs/` — agent.log (INFO+) 和 errors.log (WARNING+)，带 `kclaw logs` 命令 ([#5430](https://github.com/NousResearch/kclaw/pull/5430))
- **设置向导章节添加文档链接** ([#5283](https://github.com/NousResearch/kclaw/pull/5283))
- **Doctor 诊断** — 同步提供商检查、配置迁移、WAL 和 mem0 诊断 ([#5077](https://github.com/NousResearch/kclaw/pull/5077))
- **超时调试日志和面向用户的诊断改进** ([#5370](https://github.com/NousResearch/kclaw/pull/5370))
- **推理工作量统一** 到 config.yaml only ([#6118](https://github.com/NousResearch/kclaw/pull/6118))
- **启动时加载永久命令白名单** ([#5076](https://github.com/NousResearch/kclaw/pull/5076))
- **`kclaw auth remove` 现永久清除环境播种的凭据** ([#5285](https://github.com/NousResearch/kclaw/pull/5285))
- **更新期间将捆绑技能同步到所有配置文件** ([#5795](https://github.com/NousResearch/kclaw/pull/5795))
- **`kclaw update` 不再终止** 刚重启的网关服务 ([#5448](https://github.com/NousResearch/kclaw/pull/5448))
- **所有网关 CLI 命令添加 subprocess.run() 超时** ([#5424](https://github.com/NousResearch/kclaw/pull/5424))
- **Codex 刷新令牌被重用时的可操作错误消息** — @tymrtn ([#5612](https://github.com/NousResearch/kclaw/pull/5612))
- **Google-workspace 技能脚本现可直接运行** — @xinbenlv ([#5624](https://github.com/NousResearch/kclaw/pull/5624))

### Cron 系统
- **基于空闲的 cron 超时** — 替代挂钟时间；活跃任务无限期运行 ([#5440](https://github.com/NousResearch/kclaw/pull/5440))
- **运行前脚本注入** 用于数据收集和变更检测 ([#5082](https://github.com/NousResearch/kclaw/pull/5082))
- **作业状态中的传递失败追踪** ([#6042](https://github.com/NousResearch/kclaw/pull/6042))
- **Cron 提示中的传递指导** — 停止 send_message 抖动 ([#5444](https://github.com/NousResearch/kclaw/pull/5444))
- **MEDIA 文件作为原生平台附件传递** ([#5921](https://github.com/NousResearch/kclaw/pull/5921))
- **[SILENT] 抑制** 可在响应中任何位置生效 — @auspic7 ([#5654](https://github.com/NousResearch/kclaw/pull/5654))
- **Cron 路径遍历加固** ([#5147](https://github.com/NousResearch/kclaw/pull/5147))

---

## 🔧 工具系统

### 终端与执行
- **在远程后端执行代码** — 代码执行现可在 Docker、SSH、Modal 和其他远程终端后端上运行 ([#5088](https://github.com/NousResearch/kclaw/pull/5088))
- **终端结果中常见 CLI 工具的退出代码上下文** — 帮助 agent 理解出了什么问题 ([#5144](https://github.com/NousResearch/kclaw/pull/5144))
- **渐进式子目录提示发现** — agent 在导航时学习项目结构 ([#5291](https://github.com/NousResearch/kclaw/pull/5291))
- **后台进程的 notify_on_complete** — 在长时间运行任务完成时收到通知 ([#5779](https://github.com/NousResearch/kclaw/pull/5779))
- **Docker 环境配置** — 通过 docker_env 配置的显式容器环境变量 ([#4738](https://github.com/NousResearch/kclaw/pull/4738))
- **终端工具结果中包含审批元数据** ([#5141](https://github.com/NousResearch/kclaw/pull/5141))
- **所有后端终端工具中清理工作目录参数** ([#5629](https://github.com/NousResearch/kclaw/pull/5629))
- **分离进程崩溃恢复状态修正** ([#6101](https://github.com/NousResearch/kclaw/pull/6101))
- **保留带空格 agent-browser 路径** — @Vasanthdev2004 ([#6077](https://github.com/NousResearch/kclaw/pull/6077))
- **macOS 便携式 base64 编码** 用于图像读取 — @CharlieKerfoot ([#5657](https://github.com/NousResearch/kclaw/pull/5657))

### 浏览器
- **将托管浏览器提供商从 Browserbase 切换到 Browser Use** — @benbarclay ([#5750](https://github.com/NousResearch/kclaw/pull/5750))
- **Firecrawl 云浏览器提供商** — @alt-glitch ([#5628](https://github.com/NousResearch/kclaw/pull/5628))
- **通过 browser_console 表达式参数进行 JS 评估** ([#5303](https://github.com/NousResearch/kclaw/pull/5303))
- **Windows 浏览器修复** ([#5665](https://github.com/NousResearch/kclaw/pull/5665))

### MCP
- **MCP OAuth 2.1 PKCE** — 完整标准合规 OAuth 客户端支持 ([#5420](https://github.com/NousResearch/kclaw/pull/5420))
- **MCP 扩展包的 OSV 恶意软件检查** ([#5305](https://github.com/NousResearch/kclaw/pull/5305))
- **优先 structuredContent 而非 text** + no_mcp sentinel ([#5979](https://github.com/NousResearch/kclaw/pull/5979))
- **为 MCP 服务器名称抑制未知工具集警告** ([#5279](https://github.com/NousResearch/kclaw/pull/5279))

### Web 与文件
- **.zip 文档支持** + 自动挂载缓存目录到远程后端 ([#4846](https://github.com/NousResearch/kclaw/pull/4846))
- **send_message 错误中编辑查询 secrets** — @WAXLYY ([#5650](https://github.com/NousResearch/kclaw/pull/5650))

### 委托
- **凭据池共享** + 子 agent 的 workspace 路径提示 ([#5748](https://github.com/NousResearch/kclaw/pull/5748))

### ACP (VS Code / Zed / JetBrains)
- **聚合 ACP 改进** — 认证兼容性、协议修复、命令广告、委托、SSE 事件 ([#5292](https://github.com/NousResearch/kclaw/pull/5292))

---

## 🧩 技能生态系统

### 技能系统
- **技能配置接口** — 技能可声明必需的 config.yaml 设置，在设置期间提示，在加载时注入 ([#5635](https://github.com/NousResearch/kclaw/pull/5635))
- **插件 CLI 注册系统** — 插件注册自己的 CLI 子命令，无需触碰 main.py ([#5295](https://github.com/NousResearch/kclaw/pull/5295))
- **带工具调用关联 ID 的请求作用域 API 钩子** 用于插件 ([#5427](https://github.com/NousResearch/kclaw/pull/5427))
- **会话生命周期钩子** — on_session_finalize 和 on_session_reset 用于 CLI + 网关 ([#6129](https://github.com/NousResearch/kclaw/pull/6129))
- **插件安装期间提示必需环境变量** — @kshitijk4poor ([#5470](https://github.com/NousResearch/kclaw/pull/5470))
- **插件名称验证** — 拒绝解析到 plugins 根目录的名称 ([#5368](https://github.com/NousResearch/kclaw/pull/5368))
- **pre_llm_call 插件上下文移至用户消息** 以保留提示缓存 ([#5146](https://github.com/NousResearch/kclaw/pull/5146))

### 新增与更新技能
- **popular-web-designs** — 54 个生产级网站设计系统 ([#5194](https://github.com/NousResearch/kclaw/pull/5194))
- **p5js 创意编程** — @SHL0MS ([#5600](https://github.com/NousResearch/kclaw/pull/5600))
- **manim-video** — 数学和技术动画 — @SHL0MS ([#4930](https://github.com/NousResearch/kclaw/pull/4930))
- **llm-wiki** — Karpathy 的 LLM Wiki 技能 ([#5635](https://github.com/NousResearch/kclaw/pull/5635))
- **gitnexus-explorer** — 代码库索引和知识服务 ([#5208](https://github.com/NousResearch/kclaw/pull/5208))
- **research-paper-writing** — AI-Scientist 和 GPT-Researcher 模式 — @SHL0MS ([#5421](https://github.com/NousResearch/kclaw/pull/5421))
- **blogwatcher 更新至 JulienTant 的 fork** ([#5759](https://github.com/NousResearch/kclaw/pull/5759))
- **claude-code 技能全面重写** v2.0 + v2.2 ([#5155](https://github.com/NousResearch/kclaw/pull/5155), [#5158](https://github.com/NousResearch/kclaw/pull/5158))
- **代码验证技能合并为一个** ([#4854](https://github.com/NousResearch/kclaw/pull/4854))
- **Manim CE 参考文档扩展** — 几何、动画、LaTeX — @leotrs ([#5791](https://github.com/NousResearch/kclaw/pull/5791))
- **Manim-video 参考** — 设计思维、updaters、论文解释器、装饰、生产质量 — @SHL0MS ([#5588](https://github.com/NousResearch/kclaw/pull/5588), [#5408](https://github.com/NousResearch/kclaw/pull/5408))

---

## 🔒 安全与可靠性

### 安全加固
- **整合安全防护** — SSRF 防护、时间攻击缓解、tar 遍历预防、凭据泄露保护 ([#5944](https://github.com/NousResearch/kclaw/pull/5944))
- **跨会话隔离** + cron 路径遍历加固 ([#5613](https://github.com/NousResearch/kclaw/pull/5613))
- **所有后端终端工具中清理工作目录参数** ([#5629](https://github.com/NousResearch/kclaw/pull/5629))
- **防止审批 'once' 会话升级** + cron 传递平台验证 ([#5280](https://github.com/NousResearch/kclaw/pull/5280))
- **配置文件作用域的 Google Workspace OAuth 令牌保护** ([#4910](https://github.com/NousResearch/kclaw/pull/4910))

### 可靠性
- **积极的工作树和分支清理** 防止积累 ([#6134](https://github.com/NousResearch/kclaw/pull/6134))
- **编辑正则表达式中的 O(n²) 灾难性回溯已修复** — 大输出上 100 倍改进 ([#4962](https://github.com/NousResearch/kclaw/pull/4962))
- **核心、web、委托和浏览器工具的运行时稳定性修复** ([#4843](https://github.com/NousResearch/kclaw/pull/4843))
- **API 服务器流式修复** + 对话历史支持 ([#5977](https://github.com/NousResearch/kclaw/pull/5977))
- **OpenViking API 端点路径和响应解析修正** ([#5078](https://github.com/NousResearch/kclaw/pull/5078))

---

## 🐛 重要错误修复

- **挽救了 9 个社区错误修复** — 网关、cron、依赖、macOS launchd 批量处理 ([#5288](https://github.com/NousResearch/kclaw/pull/5288))
- **批量核心错误修复** — 模型配置、会话重置、别名回退、launchctl、委托、原子写入 ([#5630](https://github.com/NousResearch/kclaw/pull/5630))
- **批量网关/平台修复** — Matrix E2EE、CJK 输入、Windows 浏览器、飞书重连 + ACL ([#5665](https://github.com/NousResearch/kclaw/pull/5665))
- **移除过时的测试跳过**、正则回溯、文件搜索错误和测试不稳定性 ([#4969](https://github.com/NousResearch/kclaw/pull/4969))
- **Nix flake** — 读取版本、重生 uv.lock、添加 kclaw_logging — @alt-glitch ([#5651](https://github.com/NousResearch/kclaw/pull/5651))
- **小写变量编辑回归测试** ([#5185](https://github.com/NousResearch/kclaw/pull/5185))

---

## 🧪 测试

- **修复了 57 个失败的 CI 测试**，跨 14 个文件 ([#5823](https://github.com/NousResearch/kclaw/pull/5823))
- **测试套件架构重新设计** + CI 失败修复 — @alt-glitch ([#5946](https://github.com/NousResearch/kclaw/pull/5946))
- **全代码库 lint 清理** — 未使用的导入、死代码和低效模式 ([#5821](https://github.com/NousResearch/kclaw/pull/5821))
- **移除 browser_close 工具** — 自动清理处理 ([#5792](https://github.com/NousResearch/kclaw/pull/5792))

---

## 📚 文档

- **全面文档审计** — 修复过时信息、扩展薄弱页面、增加深度 ([#5393](https://github.com/NousResearch/kclaw/pull/5393))
- **修复文档与代码库之间的 40+ 差异** ([#5818](https://github.com/NousResearch/kclaw/pull/5818))
- **记录上周 PR 的 13 个功能** ([#5815](https://github.com/NousResearch/kclaw/pull/5815))
- **指南部分大修** — 修复现有 + 新增 3 个教程 ([#5735](https://github.com/NousResearch/kclaw/pull/5735))
- **挽救了 4 个文档 PR** — docker 设置、更新后验证、本地 LLM 指南、signal-cli 安装 ([#5727](https://github.com/NousResearch/kclaw/pull/5727))
- **Discord 配置参考** ([#5386](https://github.com/NousResearch/kclaw/pull/5386))
- **社区 FAQ 条目** 用于常见工作流和故障排除 ([#4797](https://github.com/NousResearch/kclaw/pull/4797))
- **用于本地模型服务器的 WSL2 网络指南** ([#5616](https://github.com/NousResearch/kclaw/pull/5616))
- **Honcho CLI 参考** + 插件 CLI 注册文档 ([#5308](https://github.com/NousResearch/kclaw/pull/5308))
- **服务器上的 Obsidian Headless 设置** 在 llm-wiki 中 ([#5660](https://github.com/NousResearch/kclaw/pull/5660))
- **KClaw Mod 可视化皮肤编辑器** 添加到皮肤页面 ([#6095](https://github.com/NousResearch/kclaw/pull/6095))

---

## 👥 贡献者

### 核心
- **@teknium1** — 179 个 PR

### 顶级社区贡献者
- **@SHL0MS** (7 个 PR) — p5js 创意编程技能、manim-video 技能 + 5 个参考扩展、research-paper-writing、Nous OAuth 修复、manim 字体修复
- **@alt-glitch** (3 个 PR) — Firecrawl 云浏览器提供商、测试重新架构 + CI 修复、Nix flake 修复
- **@benbarclay** (2 个 PR) — Browser Use 托管提供商切换、Nous portal base URL 修复
- **@CharlieKerfoot** (2 个 PR) — macOS 便携式 base64 编码、线程安全 PairingStore
- **@WAXLYY** (2 个 PR) — send_message 秘密编辑、网关媒体 URL 清理
- **@MadKangYu** (2 个 PR) — Telegram 日志噪音减少、温度限制模型的上下文压缩修复

### 所有贡献者
@alt-glitch, @austinpickett, @auspic7, @benbarclay, @CharlieKerfoot, @GratefulDave, @kshitijk4poor, @leotrs, @lumethegreat, @MadKangYu, @nericervin, @ryanautomated, @SHL0MS, @techguysimon, @tymrtn, @Vasanthdev2004, @WAXLYY, @xinbenlv

---

**完整变更日志**: [v2026.4.3...v2026.4.8](https://github.com/NousResearch/kclaw/compare/v2026.4.3...v2026.4.8)
