# KClaw Agent v0.7.0 (v2026.4.3)

**发布日期：** 2026年4月3日

> 弹性版本 — 可插拔内存提供商、凭据池轮换、Camofox 反检测浏览器、内联 diff 预览、网关强化（竞态条件和审批路由）、深度的安全修复，168 个 PR 和 46 个已解决的问题。

---

## 亮点

- **可插拔内存提供商接口** — 内存现在是一个可扩展的插件系统。第三方内存后端（Honcho、向量存储、自定义数据库）实现简单的提供商 ABC 并通过插件系统注册。内置内存是默认提供商。Honcho 集成作为参考插件恢复到完全对等，具有 profile 范围的 host/peer 解析。（[#4623](https://github.com/NousResearch/kclaw/pull/4623)，[#4616](https://github.com/NousResearch/kclaw/pull/4616)，[#4355](https://github.com/NousResearch/kclaw/pull/4355)）

- **同提供商凭据池** — 为同一提供商配置多个 API 密钥，自动轮换。线程安全的 `least_used` 策略在密钥间分配负载，401 失败触发自动轮换到下一个凭据。通过设置向导或 `credential_pool` 配置进行设置。（[#4188](https://github.com/NousResearch/kclaw/pull/4188)，[#4300](https://github.com/NousResearch/kclaw/pull/4300)，[#4361](https://github.com/NousResearch/kclaw/pull/4361)）

- **Camofox 反检测浏览器后端** — 使用 Camoufox 进行隐蔽浏览的新本地浏览器后端。持久化会话，带 VNC URL 发现用于可视化调试，为本地后端配置可选的 SSRF 绕过，通过 `kclaw tools` 自动安装。（[#4008](https://github.com/NousResearch/kclaw/pull/4008)，[#4419](https://github.com/NousResearch/kclaw/pull/4419)，[#4292](https://github.com/NousResearch/kclaw/pull/4292)）

- **内联 Diff 预览** — 文件写入和补丁操作现在在工具活动提要中显示内联 diff，让您在代理继续之前直观地确认发生了什么变化。（[#4411](https://github.com/NousResearch/kclaw/pull/4411)，[#4423](https://github.com/NousResearch/kclaw/pull/4423)）

- **API 服务器会话连续性和工具流** — API 服务器（Open WebUI 集成）现在实时流式传输工具进度事件，并支持 `X-KClaw-Session-Id` 头以跨请求保持会话持久化。会话持久化到共享 SessionDB。（[#4092](https://github.com/NousResearch/kclaw/pull/4092)，[#4478](https://github.com/NousResearch/kclaw/pull/4478)，[#4802](https://github.com/NousResearch/kclaw/pull/4802)）

- **ACP：客户端提供的 MCP 服务器** — 编辑器集成（VS Code、Zed、JetBrains）现在可以注册自己的 MCP 服务器，KClaw 将其作为附加代理工具。编辑器的 MCP 生态系统直接流入代理。（[#4705](https://github.com/NousResearch/kclaw/pull/4705)）

- **网关强化** — 跨竞态条件、照片媒体投递、洪水控制、卡住会话、审批路由和压缩死亡螺旋的重大稳定性提升。网关在生产环境中更加可靠。（[#4727](https://github.com/NousResearch/kclaw/pull/4727)，[#4750](https://github.com/NousResearch/kclaw/pull/4750)，[#4798](https://github.com/NousResearch/kclaw/pull/4798)，[#4557](https://github.com/NousResearch/kclaw/pull/4557)）

- **安全：阻止秘密泄露** — 浏览器 URL 和 LLM 响应现在会扫描秘密模式，阻止通过 URL 编码、base64 或提示注入的泄露尝试。凭据目录保护扩展到 `.docker`、`.azure`、`.config/gh`。Execute_code 沙箱输出被编辑。（[#4483](https://github.com/NousResearch/kclaw/pull/4483)，[#4360](https://github.com/NousResearch/kclaw/pull/4360)，[#4305](https://github.com/NousResearch/kclaw/pull/4305)，[#4327](https://github.com/NousResearch/kclaw/pull/4327)）

---

## 核心代理与架构

### 提供商和模型支持
- **同提供商凭据池** — 配置多个 API 密钥，自动 `least_used` 轮换和 401 故障转移（[#4188](https://github.com/NousResearch/kclaw/pull/4188)，[#4300](https://github.com/NousResearch/kclaw/pull/4300)）
- **智能路由保留凭据池** — 池状态在回退提供商切换中保持，并在 429 时延迟急切回退（[#4361](https://github.com/NousResearch/kclaw/pull/4361)）
- **每轮主运行时恢复** — 使用回退提供商后，代理在下一轮自动恢复主提供商并进行传输恢复（[#4624](https://github.com/NousResearch/kclaw/pull/4624)）
- **GPT-5 和 Codex 模型的 `developer` 角色** — 为更新模型使用 OpenAI 推荐的系统消息角色（[#4498](https://github.com/NousResearch/kclaw/pull/4498)）
- **Google 模型操作指南** — Gemini 和 Gemma 模型获得提供商特定的提示指导（[#4641](https://github.com/NousResearch/kclaw/pull/4641)）
- **Anthropic 长上下文层级 429 处理** — 达到层级限制时自动将上下文减少到 200k（[#4747](https://github.com/NousResearch/kclaw/pull/4747)）
- **第三方 Anthropic 端点的基于 URL 的认证** + CI 测试修复（[#4148](https://github.com/NousResearch/kclaw/pull/4148)）
- **MiniMax Anthropic 端点的 Bearer 认证**（[#4028](https://github.com/NousResearch/kclaw/pull/4028)）
- **Fireworks 上下文长度检测**（[#4158](https://github.com/NousResearch/kclaw/pull/4158)）
- **Alibaba 提供商的标准 DashScope 国际端点**（[#4133](https://github.com/NousResearch/kclaw/pull/4133)，关闭 [#3912](https://github.com/NousResearch/kclaw/issues/3912)）
- **自定义提供商 context_length 在卫生压缩中被尊重**（[#4085](https://github.com/NousResearch/kclaw/pull/4085)）
- **非 sk-ant 密钥被视为常规 API 密钥**，而不是 OAuth token（[#4093](https://github.com/NousResearch/kclaw/pull/4093)）
- **Claude-sonnet-4.6** 添加到 OpenRouter 和 Nous 模型列表（[#4157](https://github.com/NousResearch/kclaw/pull/4157)）
- **Qwen 3.6 Plus Preview** 添加到模型列表（[#4376](https://github.com/NousResearch/kclaw/pull/4376)）
- **MiniMax M2.7** 添加到 kclaw 模型选择器和 OpenCode（[#4208](https://github.com/NousResearch/kclaw/pull/4208)）
- **自定义端点设置中从服务器探测自动检测模型**（[#4218](https://github.com/NousResearch/kclaw/pull/4218)）
- **config.yaml 作为端点 URL 的单一事实来源** — 不再存在环境变量与 config.yaml 的冲突（[#4165](https://github.com/NousResearch/kclaw/pull/4165)）
- **设置向导不再覆盖** 自定义端点配置（[#4180](https://github.com/NousResearch/kclaw/pull/4180)，关闭 [#4172](https://github.com/NousResearch/kclaw/issues/4172)）
- **统一的设置向导提供商选择** 配合 `kclaw model` — 两个流程的单一代码路径（[#4200](https://github.com/NousResearch/kclaw/pull/4200)）
- **根级提供商配置不再覆盖** `model.provider`（[#4329](https://github.com/NousResearch/kclaw/pull/4329)）
- **速率限制配对拒绝消息** 防止垃圾信息（[#4081](https://github.com/NousResearch/kclaw/pull/4081)）

### 代理循环与对话
- **跨工具使用轮次保留 Anthropic thinking block 签名**（[#4626](https://github.com/NousResearch/kclaw/pull/4626)）
- **在重试前分类仅思考空响应** — 防止在产生 thinking block 而无内容的模型上无限重试循环（[#4645](https://github.com/NousResearch/kclaw/pull/4645)）
- **防止 API 断开导致的压缩死亡螺旋** — 停止压缩触发、失败、再压缩的循环（[#4750](https://github.com/NousResearch/kclaw/pull/4750)，关闭 [#2153](https://github.com/NousResearch/kclaw/issues/2153)）
- **压缩后将压缩的上下文持久化** 到网关会话（[#4095](https://github.com/NousResearch/kclaw/pull/4095)）
- **上下文超限错误消息** 现在包含可操作的指导（[#4155](https://github.com/NousResearch/kclaw/pull/4155)，关闭 [#4061](https://github.com/NousResearch/kclaw/issues/4061)）
- **从面向用户的响应中剥离孤立的 think/reasoning 标签**（[#4311](https://github.com/NousResearch/kclaw/pull/4311)，关闭 [#4285](https://github.com/NousResearch/kclaw/issues/4285)）
- **强化 Codex 响应预检** 和流错误处理（[#4313](https://github.com/NousResearch/kclaw/pull/4313)）
- **确定性 call_id 后备** 而不是随机 UUID 以保持提示缓存一致性（[#3991](https://github.com/NousResearch/kclaw/pull/3991)）
- **压缩后防止上下文压力警告泛滥**（[#4012](https://github.com/NousResearch/kclaw/pull/4012)）
- **在轨迹压缩器中延迟创建 AsyncOpenAI** 以避免关闭的事件循环错误（[#4013](https://github.com/NousResearch/kclaw/pull/4013)）

### 内存与会话
- **可插拔内存提供商接口** — 用于自定义内存后端的基于 ABC 的插件系统，具有 profile 隔离（[#4623](https://github.com/NousResearch/kclaw/pull/4623)）
- **Honcho 完全集成对等** 作为参考内存提供商插件恢复（[#4355](https://github.com/NousResearch/kclaw/pull/4355)）— @erosika
- **Honcho profile 范围** 的 host 和 peer 解析（[#4616](https://github.com/NousResearch/kclaw/pull/4616)）
- **内存刷新状态持久化** 以防止网关重启时冗余重新刷新（[#4481](https://github.com/NousResearch/kclaw/pull/4481)）
- **内存提供商工具** 通过顺序执行路径路由（[#4803](https://github.com/NousResearch/kclaw/pull/4803)）
- **Honcho 配置** 写入实例本地路径以实现 profile 隔离（[#4037](https://github.com/NousResearch/kclaw/pull/4037)）
- **API 服务器会话** 持久化到共享 SessionDB（[#4802](https://github.com/NousResearch/kclaw/pull/4802)）
- **Token 使用量持久化** 用于非 CLI 会话（[#4627](https://github.com/NousResearch/kclaw/pull/4627)）
- **FTS5 查询中引用点分术语** — 修复包含点的术语的会话搜索（[#4549](https://github.com/NousResearch/kclaw/pull/4549)）

---

## 消息平台（网关）

### 网关核心
- **竞态条件修复** — 照片媒体丢失、洪水控制、卡住会话和 STT 配置问题在一轮强化中解决（[#4727](https://github.com/NousResearch/kclaw/pull/4727)）
- **通过 running-agent guard 的审批路由** — `/approve` 和 `/deny` 现在在代理被阻塞等待审批时正确路由，而不是作为中断被吞掉（[#4798](https://github.com/NousResearch/kclaw/pull/4798)，[#4557](https://github.com/NousResearch/kclaw/pull/4557)，关闭 [#4542](https://github.com/NousResearch/kclaw/issues/4542)）
- **批准后恢复代理** — 执行被阻止的命令时工具结果不再丢失（[#4418](https://github.com/NousResearch/kclaw/pull/4418)）
- **DM 线程会话播种** 时附带父 transcript 以保留上下文（[#4559](https://github.com/NousResearch/kclaw/pull/4559)）
- **技能感知斜杠命令** — 网关动态注册已安装的技能作为斜杠命令，带分页的 `/commands` 列表和 Telegram 100 命令上限（[#3934](https://github.com/NousResearch/kclaw/pull/3934)，[#4005](https://github.com/NousResearch/kclaw/pull/4005)，[#4006](https://github.com/NousResearch/kclaw/pull/4006)，[#4010](https://github.com/NousResearch/kclaw/pull/4010)，[#4023](https://github.com/NousResearch/kclaw/pull/4023)）
- **尊重每个平台禁用的技能** 在 Telegram 菜单和网关分发中（[#4799](https://github.com/NousResearch/kclaw/pull/4799)）
- **移除面向用户的压缩警告** — 更干净的消息流（[#4139](https://github.com/NousResearch/kclaw/pull/4139)）
- **`-v/-q` 标志连接到 stderr 日志** 用于网关服务（[#4474](https://github.com/NousResearch/kclaw/pull/4474)）
- **KCLAW_HOME 重新映射** 到系统服务单元中的目标用户（[#4456](https://github.com/NousResearch/kclaw/pull/4456)）
- **尊重无效类似布尔配置值的默认值**（[#4029](https://github.com/NousResearch/kclaw/pull/4029)）
- **使用 setsid 而不是 systemd-run** 用于 `/update` 命令以避免 systemd 权限问题（[#4104](https://github.com/NousResearch/kclaw/pull/4104)，关闭 [#4017](https://github.com/NousResearch/kclaw/issues/4017)）
- **'正在初始化代理...'** 在第一条消息时显示以获得更好的 UX（[#4086](https://github.com/NousResearch/kclaw/pull/4086)）
- **允许以 root 身份运行网关服务** 用于 LXC/容器环境（[#4732](https://github.com/NousResearch/kclaw/pull/4732)）

### Telegram
- **命令名称的 32 字符限制** 带碰撞避免（[#4211](https://github.com/NousResearch/kclaw/pull/4211)）
- **菜单中强制执行优先级顺序** 核心 > 插件 > 技能（[#4023](https://github.com/NousResearch/kclaw/pull/4023)）
- **上限 50 个命令** — API 拒绝超过约 60 个（[#4006](https://github.com/NousResearch/kclaw/pull/4006)）
- **跳过空/空白文本** 以防止 400 错误（[#4388](https://github.com/NousResearch/kclaw/pull/4388)）
- **E2E 网关测试** 添加（[#4497](https://github.com/NousResearch/kclaw/pull/4497)）— @pefontana

### Discord
- **基于按钮的审批 UI** — 使用交互式按钮提示注册 `/approve` 和 `/deny` 斜杠命令（[#4800](https://github.com/NousResearch/kclaw/pull/4800)）
- **可配置的反应** — `discord.reactions` 配置选项禁用消息处理反应（[#4199](https://github.com/NousResearch/kclaw/pull/4199)）
- **跳过未经授权用户的反应和自动线程**（[#4387](https://github.com/NousResearch/kclaw/pull/4387)）

### Slack
- **在线程中回复** — `slack.reply_in_thread` 配置选项用于线程化响应（[#4643](https://github.com/NousResearch/kclaw/pull/4643)，关闭 [#2662](https://github.com/NousResearch/kclaw/issues/2662)）

### WhatsApp
- **在群聊中强制执行 require_mention**（[#4730](https://github.com/NousResearch/kclaw/pull/4730)）

### Webhook
- **平台支持修复** — 跳过主通道提示，为 webhook 适配器禁用工具进度（[#4660](https://github.com/NousResearch/kclaw/pull/4660)）

### Matrix
- **E2EE 解密强化** — 请求缺失密钥、自动信任设备、重试缓冲事件（[#4083](https://github.com/NousResearch/kclaw/pull/4083)）

---

## CLI 与用户体验

### 新斜杠命令
- **`/yolo`** — 切换危险命令审批的开启/关闭（[#3990](https://github.com/NousResearch/kclaw/pull/3990)）
- **`/btw`** — 临时性问题，不影响主对话上下文（[#4161](https://github.com/NousResearch/kclaw/pull/4161)）
- **`/profile`** — 显示活动 profile 信息而不离开聊天会话（[#4027](https://github.com/NousResearch/kclaw/pull/4027)）

### 交互式 CLI
- **写入和补丁操作的内联 diff 预览** 在工具活动提要中（[#4411](https://github.com/NousResearch/kclaw/pull/4411)，[#4423](https://github.com/NousResearch/kclaw/pull/4423)）
- **TUI 在启动时固定在底部** — 不再在响应和输入之间出现大面积空白（[#4412](https://github.com/NousResearch/kclaw/pull/4412)，[#4359](https://github.com/NousResearch/kclaw/pull/4359)，关闭 [#4398](https://github.com/NousResearch/kclaw/issues/4398)，[#4421](https://github.com/NousResearch/kclaw/issues/4421)）
- **`/history` 和 `/resume`** 现在直接显示最近会话而不是需要搜索（[#4728](https://github.com/NousResearch/kclaw/pull/4728)）
- **`/insights` 概述中显示缓存的 token** 以便总数相加（[#4428](https://github.com/NousResearch/kclaw/pull/4428)）
- **`--max-turns` CLI 标志** 用于 `kclaw chat` 限制代理迭代（[#4314](https://github.com/NousResearch/kclaw/pull/4314)）
- **检测拖拽的文件路径** 而不是将其视为斜杠命令（[#4533](https://github.com/NousResearch/kclaw/pull/4533)）— @rolme
- **允许空字符串和假值** 在 `config set` 中（[#4310](https://github.com/NousResearch/kclaw/pull/4310)，关闭 [#4277](https://github.com/NousResearch/kclaw/issues/4277)）
- **WSL 中的语音模式** 配置了 PulseAudio 桥接时（[#4317](https://github.com/NousResearch/kclaw/pull/4317)）
- **尊重 `NO_COLOR` 环境变量** 和 `TERM=dumb` 以提高可访问性（[#4079](https://github.com/NousResearch/kclaw/pull/4079)，关闭 [#4066](https://github.com/NousResearch/kclaw/issues/4066)）— @SHL0MS
- **macOS/zsh 用户的正确 shell 重载说明**（[#4025](https://github.com/NousResearch/kclaw/pull/4025)）
- **成功安静模式查询的零退出代码**（[#4613](https://github.com/NousResearch/kclaw/pull/4613)，关闭 [#4601](https://github.com/NousResearch/kclaw/issues/4601)）— @devorun
- **on_session_end 钩子在中断退出时触发**（[#4159](https://github.com/NousResearch/kclaw/pull/4159)）
- **Profile 列表显示** 正确读取 `model.default` 键（[#4160](https://github.com/NousResearch/kclaw/pull/4160)）
- **在重新配置菜单中显示浏览器和 TTS**（[#4041](https://github.com/NousResearch/kclaw/pull/4041)）
- **简化 Web 后端优先级检测**（[#4036](https://github.com/NousResearch/kclaw/pull/4036)）

### 设置与配置
- **设置期间保留 allowed_users** 和安静未配置提供商警告（[#4551](https://github.com/NousResearch/kclaw/pull/4551)）— @kshitijk4poor
- **为自定义端点保存 API 密钥** 到模型配置（[#4202](https://github.com/NousResearch/kclaw/pull/4202)，关闭 [#4182](https://github.com/NousResearch/kclaw/issues/4182)）
- **Claude Code 凭据由向导触发器背后的显式 KClaw 配置把关**（[#4210](https://github.com/NousResearch/kclaw/pull/4210)）
- **save_config_value 中的原子写入** 以防止中断时配置丢失（[#4298](https://github.com/NousResearch/kclaw/pull/4298)，[#4320](https://github.com/NousResearch/kclaw/pull/4320)）
- **Token 刷新时写入 Claude Code 凭据的 scopes 字段**（[#4126](https://github.com/NousResearch/kclaw/pull/4126)）

### 更新系统
- **`kclaw update` 中的分叉检测和上游同步**（[#4744](https://github.com/NousResearch/kclaw/pull/4744)）
- **一个 extra 失败时保留工作的可选 extras**（[#4550](https://github.com/NousResearch/kclaw/pull/4550)）
- **处理 kclaw 更新期间的 git 索引冲突**（[#4735](https://github.com/NousResearch/kclaw/pull/4735)）
- **避免 macOS 上的 launchd 重启竞速**（[#4736](https://github.com/NousResearch/kclaw/pull/4736)）
- **为 doctor 和 status 命令添加缺失的 subprocess.run() 超时**（[#4009](https://github.com/NousResearch/kclaw/pull/4009)）

---

## 工具系统

### 浏览器
- **Camofox 反检测浏览器后端** — 通过 `kclaw tools` 自动安装的本地隐蔽浏览（[#4008](https://github.com/NousResearch/kclaw/pull/4008)）
- **持久化 Camofox 会话** 带 VNC URL 发现用于可视化调试（[#4419](https://github.com/NousResearch/kclaw/pull/4419)）
- **为本地后端跳过 SSRF 检查**（Camofox、无头 Chromium）（[#4292](https://github.com/NousResearch/kclaw/pull/4292)）
- **通过 `browser.allow_private_urls` 配置 SSRF 检查**（[#4198](https://github.com/NousResearch/kclaw/pull/4198)）— @nils010485
- **CAMOFOX_PORT=9377** 添加到 Docker 命令（[#4340](https://github.com/NousResearch/kclaw/pull/4340)）

### 文件操作
- **写入和补丁操作的内联 diff 预览**（[#4411](https://github.com/NousResearch/kclaw/pull/4411)，[#4423](https://github.com/NousResearch/kclaw/pull/4423)）
- **写入和补丁的陈旧文件检测** — 当文件自上次读取后被外部修改时警告（[#4345](https://github.com/NousResearch/kclaw/pull/4345)）
- **写入后刷新陈旧时间戳**（[#4390](https://github.com/NousResearch/kclaw/pull/4390)）
- **read_file 的大小保护、去重和设备阻止**（[#4315](https://github.com/NousResearch/kclaw/pull/4315)）

### MCP
- **稳定性修复包** — 重载超时、关闭清理、事件循环处理程序、OAuth 非阻塞（[#4757](https://github.com/NousResearch/kclaw/pull/4757)，关闭 [#4462](https://github.com/NousResearch/kclaw/issues/4462)，[#2537](https://github.com/NousResearch/kclaw/issues/2537)）

### ACP（编辑器集成）
- **客户端提供的 MCP 服务器** 注册为代理工具 — 编辑器将其 MCP 服务器传递给 KClaw（[#4705](https://github.com/NousResearch/kclaw/pull/4705)）

### 技能系统
- **代理写入的大小限制** 和技能补丁的模糊匹配 — 防止过大的技能写入并提高编辑可靠性（[#4414](https://github.com/NousResearch/kclaw/pull/4414)）
- **安装前验证 hub 捆绑路径** — 阻止技能捆绑中的路径遍历（[#3986](https://github.com/NousResearch/kclaw/pull/3986)）
- **统一 kclaw 和 kclaw-setup** 为单一技能（[#4332](https://github.com/NousResearch/kclaw/pull/4332)）
- **extract_skill_conditions 中的技能元数据类型检查**（[#4479](https://github.com/NousResearch/kclaw/pull/4479)）

### 新/更新的技能
- **research-paper-writing** — 完整的端到端研究流程（替换 ml-paper-writing）（[#4654](https://github.com/NousResearch/kclaw/pull/4654)）— @SHL0MS
- **ascii-video** — 文本可读性技术和外部布局 oracle（[#4054](https://github.com/NousResearch/kclaw/pull/4054)）— @SHL0MS
- **youtube-transcript** 为 youtube-transcript-api v1.x 更新（[#4455](https://github.com/NousResearch/kclaw/pull/4455)）— @el-analista
- **技能浏览和搜索页面** 添加到文档站点（[#4500](https://github.com/NousResearch/kclaw/pull/4500)）— @IAvecilla

---

## 安全与可靠性

### 安全加固
- **阻止通过浏览器 URL 和 LLM 响应泄露秘密** — 扫描 URL 编码、base64 和提示注入向量中的秘密模式（[#4483](https://github.com/NousResearch/kclaw/pull/4483)）
- **从 execute_code 沙箱输出中编辑秘密**（[#4360](https://github.com/NousResearch/kclaw/pull/4360)）
- **保护 `.docker`、`.azure`、`.config/gh` 凭据目录** 免受文件工具和终端的读取/写入（[#4305](https://github.com/NousResearch/kclaw/pull/4305)，[#4327](https://github.com/NousResearch/kclaw/pull/4327)）— @memosr
- **GitHub OAuth token 模式** 添加到编辑 + 快照编辑标志（[#4295](https://github.com/NousResearch/kclaw/pull/4295)）
- **在 Telegram DoH 后备中拒绝私有和回环 IP**（[#4129](https://github.com/NousResearch/kclaw/pull/4129)）
- **在凭据文件注册中拒绝路径遍历**（[#4316](https://github.com/NousResearch/kclaw/pull/4316)）
- **配置文件导入时验证 tar 归档成员路径** — 阻止 zip-slip 攻击（[#4318](https://github.com/NousResearch/kclaw/pull/4318)）
- **从 profile 导出中排除 auth.json 和 .env**（[#4475](https://github.com/NousResearch/kclaw/pull/4475)）

### 可靠性
- **防止 API 断开导致的压缩死亡螺旋**（[#4750](https://github.com/NousResearch/kclaw/pull/4750)，关闭 [#2153](https://github.com/NousResearch/kclaw/issues/2153)）
- **将 `is_closed` 作为方法处理** 在 OpenAI SDK 中 — 防止误报客户端关闭检测（[#4416](https://github.com/NousResearch/kclaw/pull/4416)，关闭 [#4377](https://github.com/NousResearch/kclaw/issues/4377)）
- **从 [all] extras 中排除 matrix** — python-olm 上游损坏，防止安装失败（[#4615](https://github.com/NousResearch/kclaw/pull/4615)，关闭 [#4178](https://github.com/NousResearch/kclaw/issues/4178)）
- **修复 OpenCode 模型路由**（[#4508](https://github.com/NousResearch/kclaw/pull/4508)）
- **优化 Docker 容器镜像**（[#4034](https://github.com/NousResearch/kclaw/pull/4034)）— @bcross

### Windows 和跨平台
- **WSL 中的语音模式** 带 PulseAudio 桥接（[#4317](https://github.com/NousResearch/kclaw/pull/4317)）
- **Homebrew 打包准备**（[#4099](https://github.com/NousResearch/kclaw/pull/4099)）
- **CI 分叉条件** 防止分叉上的工作流失败（[#4107](https://github.com/NousResearch/kclaw/pull/4107)）

---

## 重要错误修复

- **网关审批阻止代理线程** — 审批现在像 CLI 一样阻止代理线程，防止工具结果丢失（[#4557](https://github.com/NousResearch/kclaw/pull/4557)，关闭 [#4542](https://github.com/NousResearch/kclaw/issues/4542)）
- **API 断开导致的压缩死亡螺旋** — 检测并停止而不是循环（[#4750](https://github.com/NousResearch/kclaw/pull/4750)，关闭 [#2153](https://github.com/NousResearch/kclaw/issues/2153)）
- **跨工具使用轮次丢失 Anthropic thinking blocks**（[#4626](https://github.com/NousResearch/kclaw/pull/4626)）
- **`-p` 标志忽略的 Profile 模型配置** — model.model 现在正确提升到 model.default（[#4160](https://github.com/NousResearch/kclaw/pull/4160)，关闭 [#4486](https://github.com/NousResearch/kclaw/issues/4486)）
- **响应和输入区域之间的 CLI 空白**（[#4412](https://github.com/NousResearch/kclaw/pull/4412)，[#4359](https://github.com/NousResearch/kclaw/pull/4359)，关闭 [#4398](https://github.com/NousResearch/kclaw/issues/4398)）
- **将拖拽的文件路径视为斜杠命令** 而不是文件引用（[#4533](https://github.com/NousResearch/kclaw/pull/4533)）— @rolme
- **泄露到面向用户响应的孤儿 `think`/`reasoning` 标签**（[#4311](https://github.com/NousResearch/kclaw/pull/4311)，关闭 [#4285](https://github.com/NousResearch/kclaw/issues/4285)）
- **OpenAI SDK `is_closed`** 是方法不是属性 — 误报客户端关闭（[#4416](https://github.com/NousResearch/kclaw/pull/4416)，关闭 [#4377](https://github.com/NousResearch/kclaw/issues/4377)）
- **MCP OAuth 服务器可能阻止 KClaw 启动** 而不是优雅降级（[#4757](https://github.com/NousResearch/kclaw/pull/4757)，关闭 [#4462](https://github.com/NousResearch/kclaw/issues/4462)）
- **MCP 关闭时事件循环关闭** 带有 HTTP 服务器（[#4757](https://github.com/NousResearch/kclaw/pull/4757)，关闭 [#2537](https://github.com/NousResearch/kclaw/issues/2537)）
- **Alibaba 提供商硬编码到错误端点**（[#4133](https://github.com/NousResearch/kclaw/pull/4133)，关闭 [#3912](https://github.com/NousResearch/kclaw/issues/3912)）
- **Slack reply_in_thread** 缺少配置选项（[#4643](https://github.com/NousResearch/kclaw/pull/4643)，关闭 [#2662](https://github.com/NousResearch/kclaw/issues/2662)）
- **安静模式退出代码** — 成功的 `-q` 查询不再非零退出（[#4613](https://github.com/NousResearch/kclaw/pull/4613)，关闭 [#4601](https://github.com/NousResearch/kclaw/issues/4601)）
- **移动侧边栏由于文档站点中的 backdrop-filter 问题仅显示关闭按钮**（[#4207](https://github.com/NousResearch/kclaw/pull/4207)）— @xsmyile
- **被陈旧分支压缩合并还原的配置恢复** — 修复 `_config_version`（[#4440](https://github.com/NousResearch/kclaw/pull/4440)）

---

## 测试

- **Telegram 网关 E2E 测试** — Telegram 适配器的完整集成测试套件（[#4497](https://github.com/NousResearch/kclaw/pull/4497)）— @pefontana
- **修复了 11 个真实测试失败** 以及 sys.modules 级联毒药问题（[#4570](https://github.com/NousResearch/kclaw/pull/4570)）
- **解决了 7 个 CI 失败** 跨 hooks、插件和技能测试（[#3936](https://github.com/NousResearch/kclaw/pull/3936)）
- **更新 Codex 401 刷新测试** 以兼容 CI（[#4166](https://github.com/NousResearch/kclaw/pull/4166)）
- **修复陈旧的 OPENAI_BASE_URL 测试**（[#4217](https://github.com/NousResearch/kclaw/pull/4217)）

---

## 文档

- **全面的文档审计** — 跨 21 个文件修复了 9 个 HIGH 和 20+ 个 MEDIUM 差距（[#4087](https://github.com/NousResearch/kclaw/pull/4087)）
- **站点导航重组** — 功能和平臺升级为顶级（[#4116](https://github.com/NousResearch/kclaw/pull/4116)）
- **记录 API 服务器和 Open WebUI 的工具进度流**（[#4138](https://github.com/NousResearch/kclaw/pull/4138)）
- **Telegram webhook 模式文档**（[#4089](https://github.com/NousResearch/kclaw/pull/4089)）
- **本地 LLM 提供商指南** — 带上下文长度警告的全面设置指南（[#4294](https://github.com/NousResearch/kclaw/pull/4294)）
- **澄清 WhatsApp 允许列表行为** 带 `WHATSAPP_ALLOW_ALL_USERS` 文档（[#4293](https://github.com/NousResearch/kclaw/pull/4293)）
- **Slack 配置选项** — Slack 文档中的新配置部分（[#4644](https://github.com/NousResearch/kclaw/pull/4644)）
- **扩展终端后端部分** + 文档构建修复（[#4016](https://github.com/NousResearch/kclaw/pull/4016)）
- **更新添加提供商指南** 用于统一设置流程（[#4201](https://github.com/NousResearch/kclaw/pull/4201)）
- **修复 ACP Zed 配置**（[#4743](https://github.com/NousResearch/kclaw/pull/4743)）
- **社区 FAQ** 条目用于常见工作流程和故障排除（[#4797](https://github.com/NousResearch/kclaw/pull/4797)）
- **文档站点上的技能浏览和搜索页面**（[#4500](https://github.com/NousResearch/kclaw/pull/4500)）— @IAvecilla

---

## 贡献者

### 核心
- **@teknium1** — 跨所有子系统的 135 次提交

### 顶级社区贡献者
- **@kshitijk4poor** — 13 次提交：设置期间保留 allowed_users（[#4551](https://github.com/NousResearch/kclaw/pull/4551)），以及各种修复
- **@erosika** — 12 次提交：Honcho 完全集成对等作为内存提供商插件恢复（[#4355](https://github.com/NousResearch/kclaw/pull/4355)）
- **@pefontana** — 9 次提交：Telegram 网关 E2E 测试套件（[#4497](https://github.com/NousResearch/kclaw/pull/4497)）
- **@bcross** — 5 次提交：Docker 容器镜像优化（[#4034](https://github.com/NousResearch/kclaw/pull/4034)）
- **@SHL0MS** — 4 次提交：NO_COLOR/TERM=dumb 支持（[#4079](https://github.com/NousResearch/kclaw/pull/4079)），ascii-video 技能更新（[#4054](https://github.com/NousResearch/kclaw/pull/4054)），research-paper-writing 技能（[#4654](https://github.com/NousResearch/kclaw/pull/4654)）

### 所有贡献者
@0xbyt4, @arasovic, @Bartok9, @bcross, @binhnt92, @camden-lowrance, @curtitoo, @Dakota, @Dave Tist, @Dean Kerr, @devorun, @dieutx, @Dilee, @el-analista, @erosika, @Gutslabs, @IAvecilla, @Jack, @Johannnnn506, @kshitijk4poor, @Laura Batalha, @Leegenux, @Lume, @MacroAnarchy, @maymuneth, @memosr, @NexVeridian, @Nick, @nils010485, @pefontana, @Penov, @rolme, @SHL0MS, @txchen, @xsmyile

### 解决的社区问题
@acsezen（[#2537](https://github.com/NousResearch/kclaw/issues/2537)），@arasovic（[#4285](https://github.com/NousResearch/kclaw/issues/4285)），@camden-lowrance（[#4462](https://github.com/NousResearch/kclaw/issues/4462)），@devorun（[#4601](https://github.com/NousResearch/kclaw/issues/4601)），@eloklam（[#4486](https://github.com/NousResearch/kclaw/issues/4486)），@HenkDz（[#3719](https://github.com/NousResearch/kclaw/issues/3719)），@hypotyposis（[#2153](https://github.com/NousResearch/kclaw/issues/2153)），@kazamak（[#4178](https://github.com/NousResearch/kclaw/issues/4178)），@lstep（[#4366](https://github.com/NousResearch/kclaw/issues/4366)），@Mark-Lok（[#4542](https://github.com/NousResearch/kclaw/issues/4542)），@NoJster（[#4421](https://github.com/NousResearch/kclaw/issues/4421)），@patp（[#2662](https://github.com/NousResearch/kclaw/issues/2662)），@pr0n（[#4601](https://github.com/NousResearch/kclaw/issues/4601)），@saulmc（[#4377](https://github.com/NousResearch/kclaw/issues/4377)），@SHL0MS（[#4060](https://github.com/NousResearch/kclaw/issues/4060)，[#4061](https://github.com/NousResearch/kclaw/issues/4061)，[#4066](https://github.com/NousResearch/kclaw/issues/4066)，[#4172](https://github.com/NousResearch/kclaw/issues/4172)，[#4277](https://github.com/NousResearch/kclaw/issues/4277)），@Z-Mackintosh（[#4398](https://github.com/NousResearch/kclaw/issues/4398)）

---

**完整变更日志**： [v2026.3.30...v2026.4.3](https://github.com/NousResearch/kclaw/compare/v2026.3.30...v2026.4.3)
