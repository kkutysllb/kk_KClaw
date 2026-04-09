# KClaw Agent v0.6.0 (v2026.3.30)

**发布日期：** 2026年3月30日

> 多实例版本 — Profiles 用于运行隔离的代理实例、MCP 服务器模式、Docker 容器、回退提供商链、两个新的消息平台（飞书和企业微信）、Telegram webhook 模式、Slack 多工作区 OAuth，2天内 95 个 PR 和 16 个已解决的问题。

---

## 亮点

- **Profiles — 多实例 KClaw** — 从同一安装运行多个隔离的 KClaw 实例。每个 profile 拥有自己的配置、内存、会话、技能和网关服务。使用 `kclaw profile create` 创建，使用 `kclaw -p <name>` 切换，导出/导入以供共享。完整的 token-lock 隔离防止两个 profile 使用相同的机器人凭据。（[#3681](https://github.com/NousResearch/kclaw/pull/3681)）

- **MCP 服务器模式** — 通过 `kclaw mcp serve` 将 KClaw 对话和会话暴露给任何 MCP 兼容客户端（Claude Desktop、Cursor、VS Code 等）。浏览对话、读取消息、跨会话搜索和管理附件 — 全部通过 Model Context Protocol。支持 stdio 和 Streamable HTTP 传输。（[#3795](https://github.com/NousResearch/kclaw/pull/3795)）

- **Docker 容器** — 用于在容器中运行 KClaw Agent 的官方 Dockerfile。支持 CLI 和网关模式，配置通过卷挂载。（[#3668](https://github.com/NousResearch/kclaw/pull/3668)，关闭 [#850](https://github.com/NousResearch/kclaw/issues/850)）

- **有序回退提供商链** — 配置多个推理提供商，自动故障转移。当您的主提供商返回错误或无法访问时，KClaw 自动尝试链中的下一个提供商。通过 `fallback_providers` 在 config.yaml 中配置。（[#3813](https://github.com/NousResearch/kclaw/pull/3813)，关闭 [#1734](https://github.com/NousResearch/kclaw/issues/1734)）

- **飞书/ Lark 平台支持** — 飞书的完整网关适配器，支持事件订阅、消息卡片、群聊、图片/文件附件和交互式卡片回调。（[#3799](https://github.com/NousResearch/kclaw/pull/3799)，[#3817](https://github.com/NousResearch/kclaw/pull/3817)，关闭 [#1788](https://github.com/NousResearch/kclaw/issues/1788)）

- **企业微信平台支持** — 企业微信的新网关适配器，支持文本/图片/语音消息、群聊和回调验证。（[#3847](https://github.com/NousResearch/kclaw/pull/3847)）

- **Slack 多工作区 OAuth** — 通过 OAuth token 文件将单个 KClaw 网关连接到多个 Slack 工作区。每个工作区获得自己的机器人 token，根据传入事件动态解析。（[#3903](https://github.com/NousResearch/kclaw/pull/3903)）

- **Telegram Webhook 模式和群组控制** — 将 Telegram 适配器运行在 webhook 模式作为轮询的替代方案 — 响应时间更快，更适合在反向代理后面的生产部署。新的群组提及门控控制机器人的响应方式：始终、在被 @提及时、或通过正则表达式触发。（[#3880](https://github.com/NousResearch/kclaw/pull/3880)，[#3870](https://github.com/NousResearch/kclaw/pull/3870)）

- **Exa 搜索后端** — 添加 Exa 作为 Firecrawl 和 DuckDuckGo 的替代网络搜索和内容提取后端。设置 `EXA_API_KEY` 并配置为首选后端。（[#3648](https://github.com/NousResearch/kclaw/pull/3648)）

- **远程后端的技能和凭据** — 将技能目录和凭据文件挂载到 Modal 和 Docker 容器中，以便远程终端会话可以访问与本地执行相同的技能和秘密。（[#3890](https://github.com/NousResearch/kclaw/pull/3890)，[#3671](https://github.com/NousResearch/kclaw/pull/3671)，关闭 [#3665](https://github.com/NousResearch/kclaw/issues/3665)，[#3433](https://github.com/NousResearch/kclaw/issues/3433)）

---

## 核心代理与架构

### 提供商和模型支持
- **有序回退提供商链** — 在多个配置的提供商之间自动故障转移（[#3813](https://github.com/NousResearch/kclaw/pull/3813)）
- **提供商切换时修复 api_mode** — 通过 `kclaw model` 切换提供商现在正确清除过期的 `api_mode`，而不是硬编码 `chat_completions`，修复了具有 Anthropic 兼容端点的提供商的 404 错误（[#3726](https://github.com/NousResearch/kclaw/pull/3726)，[#3857](https://github.com/NousResearch/kclaw/pull/3857)，关闭 [#3685](https://github.com/NousResearch/kclaw/issues/3685)）
- **停止静默 OpenRouter 回退** — 当没有配置提供商时，KClaw 现在会抛出清晰的错误，而不是静默路由到 OpenRouter（[#3807](https://github.com/NousResearch/kclaw/pull/3807)，[#3862](https://github.com/NousResearch/kclaw/pull/3862)）
- **Gemini 3.1 预览模型** — 添加到 OpenRouter 和 Nous Portal 目录（[#3803](https://github.com/NousResearch/kclaw/pull/3803)，关闭 [#3753](https://github.com/NousResearch/kclaw/issues/3753)）
- **Gemini 直接 API 上下文长度** — 完整的上下文长度解析用于直接 Google AI 端点（[#3876](https://github.com/NousResearch/kclaw/pull/3876)）
- **gpt-5.4-mini** 添加到 Codex 回退目录（[#3855](https://github.com/NousResearch/kclaw/pull/3855)）
- **策展模型列表优于实时 API 探测** 当探测返回的模型较少时（[#3856](https://github.com/NousResearch/kclaw/pull/3856)，[#3867](https://github.com/NousResearch/kclaw/pull/3867)）
- **用户友好的 429 速率限制消息** 包含 Retry-After 倒计时（[#3809](https://github.com/NousResearch/kclaw/pull/3809)）
- **辅助客户端占位符密钥** 用于没有认证要求的本地服务器（[#3842](https://github.com/NousResearch/kclaw/pull/3842)）
- **辅助提供商解析的 INFO 级别日志**（[#3866](https://github.com/NousResearch/kclaw/pull/3866)）

### 代理循环与对话
- **子代理状态报告** — 当摘要存在时报告 `completed` 状态，而不是通用失败（[#3829](https://github.com/NousResearch/kclaw/pull/3829)）
- **压缩期间更新会话日志文件** — 防止上下文压缩后出现陈旧的文件引用（[#3835](https://github.com/NousResearch/kclaw/pull/3835)）
- **省略空工具参数** — 空时不发送 `tools` 参数而是发送 `None`，修复了与严格提供商的兼容性（[#3820](https://github.com/NousResearch/kclaw/pull/3820)）

### Profiles 与多实例
- **Profiles 系统** — `kclaw profile create/list/switch/delete/export/import/rename`。每个 profile 获得隔离的 KCLAW_HOME、网关服务、CLI 包装器。Token 锁防止凭据冲突。Profile 名称的 Tab 补全。（[#3681](https://github.com/NousResearch/kclaw/pull/3681)）
- **Profile 感知的显示路径** — 所有面向用户的 `~/.kclaw` 路径替换为 `display_kclaw_home()` 以显示正确的 profile 目录（[#3623](https://github.com/NousResearch/kclaw/pull/3623)）
- **延迟 display_kclaw_home 导入** — 防止 `kclaw update` 期间模块缓存过期字节码时的 `ImportError`（[#3776](https://github.com/NousResearch/kclaw/pull/3776)）
- **KCLAW_HOME 用于受保护路径** — `.env` 写拒绝路径现在尊重 KCLAW_HOME 而不是硬编码的 `~/.kclaw`（[#3840](https://github.com/NousResearch/kclaw/pull/3840)）

---

## 消息平台（网关）

### 新平台
- **飞书/Lark** — 完整的适配器，支持事件订阅、消息卡片、群聊、图片/文件附件、交互式卡片回调（[#3799](https://github.com/NousResearch/kclaw/pull/3799)，[#3817](https://github.com/NousResearch/kclaw/pull/3817)）
- **企业微信** — 文本/图片/语音消息、群聊、回调验证（[#3847](https://github.com/NousResearch/kclaw/pull/3847)）

### Telegram
- **Webhook 模式** — 作为 webhook 端点运行而不是轮询，适合生产部署（[#3880](https://github.com/NousResearch/kclaw/pull/3880)）
- **群组提及门控和正则表达式触发器** — 可配置的机器人群组响应行为：始终、仅 @mention、或正则匹配（[#3870](https://github.com/NousResearch/kclaw/pull/3870)）
- **优雅处理已删除的回复目标** — 回复的消息被删除时不再崩溃（[#3858](https://github.com/NousResearch/kclaw/pull/3858)，关闭 [#3229](https://github.com/NousResearch/kclaw/issues/3229)）

### Discord
- **消息处理反应** — 处理时添加反应 emoji，完成后移除，在频道中提供视觉反馈（[#3871](https://github.com/NousResearch/kclaw/pull/3871)）
- **DISCORD_IGNORE_NO_MENTION** — 跳过 @mention 其他用户/机器人但不 @mention KClaw 的消息（[#3640](https://github.com/NousResearch/kclaw/pull/3640)）
- **清理延迟的"思考中..."** — 斜杠命令完成后正确移除"思考中..."指示器（[#3674](https://github.com/NousResearch/kclaw/pull/3674)，关闭 [#3595](https://github.com/NousResearch/kclaw/issues/3595)）

### Slack
- **多工作区 OAuth** — 通过 OAuth token 文件从单个网关连接到多个 Slack 工作区（[#3903](https://github.com/NousResearch/kclaw/pull/3903)）

### WhatsApp
- **持久化 aiohttp 会话** — 跨请求重用 HTTP 会话，而不是每条消息创建新会话（[#3818](https://github.com/NousResearch/kclaw/pull/3818)）
- **LID↔电话别名解析** — 正确匹配允许列表中 Linked ID 和电话号码格式（[#3830](https://github.com/NousResearch/kclaw/pull/3830)）
- **机器人模式下跳过回复前缀** — 作为 WhatsApp 机器人运行时更干净的消息格式（[#3931](https://github.com/NousResearch/kclaw/pull/3931)）

### Matrix
- **通过 MSC3245 的原生语音消息** — 将语音消息作为正确的 Matrix 语音事件发送，而不是文件附件（[#3877](https://github.com/NousResearch/kclaw/pull/3877)）

### Mattermost
- **可配置的提及行为** — 无需 @mention 即可回复消息（[#3664](https://github.com/NousResearch/kclaw/pull/3664)）

### Signal
- **URL 编码电话号码** 和正确的附件 RPC 参数 — 修复某些电话号码格式的投递失败（[#3670](https://github.com/NousResearch/kclaw/pull/3670)）— @kshitijk4poor

### 电子邮件
- **失败时关闭 SMTP/IMAP 连接** — 防止错误场景中的连接泄漏（[#3804](https://github.com/NousResearch/kclaw/pull/3804)）

### 网关核心
- **原子配置写入** — 使用原子文件写入 config.yaml 以防止崩溃期间的数据丢失（[#3800](https://github.com/NousResearch/kclaw/pull/3800)）
- **主通道环境覆盖** — 一致地应用主通道的环境变量覆盖（[#3796](https://github.com/NousResearch/kclaw/pull/3796)，[#3808](https://github.com/NousResearch/kclaw/pull/3808)）
- **用 logger 替换 print()** — BasePlatformAdapter 现在使用正确的日志记录而不是 print 语句（[#3669](https://github.com/NousResearch/kclaw/pull/3669)）
- **Cron 投递标签** — 通过通道目录解析人类友好的投递标签（[#3860](https://github.com/NousResearch/kclaw/pull/3860)，关闭 [#1945](https://github.com/NousResearch/kclaw/issues/1945)）
- **Cron [SILENT] 收紧** — 阻止代理在报告前添加 [SILENT] 来抑制投递（[#3901](https://github.com/NousResearch/kclaw/pull/3901)）
- **后台任务媒体投递** 和视觉下载超时修复（[#3919](https://github.com/NousResearch/kclaw/pull/3919)）
- **Boot-md 钩子** — 在网关启动时运行 BOOT.md 文件的示例内置钩子（[#3733](https://github.com/NousResearch/kclaw/pull/3733)）

---

## CLI 与用户体验

### 交互式 CLI
- **可配置的工具预览长度** — 默认显示完整文件路径，而不是在 40 个字符处截断（[#3841](https://github.com/NousResearch/kclaw/pull/3841)）
- **工具 token 上下文显示** — `kclaw tools` 检查清单现在显示每个工具集的估计 token 成本（[#3805](https://github.com/NousResearch/kclaw/pull/3805)）
- **/bg spinner TUI 修复** — 通过 TUI 小部件路由后台任务 spinner 以防止状态栏冲突（[#3643](https://github.com/NousResearch/kclaw/pull/3643)）
- **防止状态栏包装** 成重复行（[#3883](https://github.com/NousResearch/kclaw/pull/3883)）— @kshitijk4poor
- **处理关闭的 stdout ValueError** 在安全打印路径中 — 修复网关线程关闭期间 stdout 关闭时的崩溃（[#3843](https://github.com/NousResearch/kclaw/pull/3843)，关闭 [#3534](https://github.com/NousResearch/kclaw/issues/3534)）
- **从 /tools disable 移除 input()** — 禁用工具时消除终端冻结（[#3918](https://github.com/NousResearch/kclaw/pull/3918)）
- **交互式 CLI 命令的 TTY 守卫** — 无终端启动时防止 CPU 空转（[#3933](https://github.com/NousResearch/kclaw/pull/3933)）
- **Argparse 入口点** — 在顶级启动器中使用 argparse 以获得更清晰的错误处理（[#3874](https://github.com/NousResearch/kclaw/pull/3874)）
- **延迟初始化的工具在横幅中显示黄色** 而不是红色，减少关于"缺少"工具的误报（[#3822](https://github.com/NousResearch/kclaw/pull/3822)）
- **配置的 Honcho 工具在横幅中显示**（[#3810](https://github.com/NousResearch/kclaw/pull/3810)）

### 设置与配置
- **选择 Matrix 时在 `kclaw setup` 期间自动安装 matrix-nio**（[#3802](https://github.com/NousResearch/kclaw/pull/3802)，[#3873](https://github.com/NousResearch/kclaw/pull/3873)）
- **会话导出 stdout 支持** — 使用 `-` 导出会话到 stdout 以进行管道传输（[#3641](https://github.com/NousResearch/kclaw/pull/3641)，关闭 [#3609](https://github.com/NousResearch/kclaw/issues/3609)）
- **可配置的批准超时** — 设置危险命令批准提示在自动拒绝之前等待的时间（[#3886](https://github.com/NousResearch/kclaw/pull/3886)，关闭 [#3765](https://github.com/NousResearch/kclaw/issues/3765)）
- **更新期间清除 __pycache__** — 防止 `kclaw update` 后过期字节码导致 ImportError（[#3819](https://github.com/NousResearch/kclaw/pull/3819)）

---

## 工具系统

### MCP
- **MCP 服务器模式** — `kclaw mcp serve` 通过 stdio 或 Streamable HTTP 向 MCP 客户端暴露对话、会话和附件（[#3795](https://github.com/NousResearch/kclaw/pull/3795)）
- **动态工具发现** — 响应 `notifications/tools/list_changed` 事件以在不重新连接的情况下从 MCP 服务器获取新工具（[#3812](https://github.com/NousResearch/kclaw/pull/3812)）
- **非弃用的 HTTP 传输** — 从 `sse_client` 切换到 `streamable_http_client`（[#3646](https://github.com/NousResearch/kclaw/pull/3646)）

### Web 工具
- **Exa 搜索后端** — 作为 Firecrawl 和 DuckDuckGo 的替代方案进行网络搜索和提取（[#3648](https://github.com/NousResearch/kclaw/pull/3648)）

### 浏览器
- **防止 LLM 响应为 None** 在浏览器快照和视觉工具中（[#3642](https://github.com/NousResearch/kclaw/pull/3642)）

### 终端和远程后端
- **将技能目录挂载** 到 Modal 和 Docker 容器（[#3890](https://github.com/NousResearch/kclaw/pull/3890)）
- **将凭据文件挂载** 到远程后端，使用 mtime+size 缓存（[#3671](https://github.com/NousResearch/kclaw/pull/3671)）
- **命令超时时保留部分输出** 而不是丢失一切（[#3868](https://github.com/NousResearch/kclaw/pull/3868)）
- **停止将持久化的环境变量标记为缺失** 在远程后端上（[#3650](https://github.com/NousResearch/kclaw/pull/3650)）

### 音频
- **转录工具中的 .aac 格式支持**（[#3865](https://github.com/NousResearch/kclaw/pull/3865)，关闭 [#1963](https://github.com/NousResearch/kclaw/issues/1963)）
- **音频下载重试** — `cache_audio_from_url` 的重试逻辑与现有图像下载模式匹配（[#3401](https://github.com/NousResearch/kclaw/pull/3401)）— @binhnt92

### 视觉
- **拒绝非图像文件** 并强制执行仅网站策略进行视觉分析（[#3845](https://github.com/NousResearch/kclaw/pull/3845)）

### 工具模式
- **确保名称字段始终存在** 在工具定义中，修复 `KeyError: 'name'` 崩溃（[#3811](https://github.com/NousResearch/kclaw/pull/3811)，关闭 [#3729](https://github.com/NousResearch/kclaw/issues/3729)）

### ACP（编辑器集成）
- **VS Code/Zed/JetBrains 客户端的完整会话管理界面** — 正确的任务生命周期、取消支持、会话持久化（[#3675](https://github.com/NousResearch/kclaw/pull/3675)）

---

## 技能与插件

### 技能系统
- **外部技能目录** — 通过 `skills.external_dirs` 在 config.yaml 中配置额外的技能目录（[#3678](https://github.com/NousResearch/kclaw/pull/3678)）
- **阻止类别路径遍历** — 防止技能类别名称中的 `../` 攻击（[#3844](https://github.com/NousResearch/kclaw/pull/3844)）
- **parallel-cli 移至 optional-skills** — 减少默认技能占用（[#3673](https://github.com/NousResearch/kclaw/pull/3673)）— @kshitijk4poor

### 新技能
- **memento-flashcards** — 间隔重复抽认卡系统（[#3827](https://github.com/NousResearch/kclaw/pull/3827)）
- **songwriting-and-ai-music** — 歌曲创作和 AI 音乐生成提示（[#3834](https://github.com/NousResearch/kclaw/pull/3834)）
- **SiYuan Note** — 与思源笔记应用的集成（[#3742](https://github.com/NousResearch/kclaw/pull/3742)）
- **Scrapling** — 使用 Scrapling 库的网络抓取技能（[#3742](https://github.com/NousResearch/kclaw/pull/3742)）
- **one-three-one-rule** — 沟通框架技能（[#3797](https://github.com/NousResearch/kclaw/pull/3797)）

### 插件系统
- **插件启用/禁用命令** — `kclaw plugins enable/disable <name>` 用于管理插件状态而不移除它们（[#3747](https://github.com/NousResearch/kclaw/pull/3747)）
- **插件消息注入** — 插件现在可以通过 `ctx.inject_message()` 代表用户将消息注入对话流（[#3778](https://github.com/NousResearch/kclaw/pull/3778)）— @winglian
- **Honcho 自托管支持** — 允许本地 Honcho 实例而不需要 API 密钥（[#3644](https://github.com/NousResearch/kclaw/pull/3644)）

---

## 安全与可靠性

### 安全加固
- **强化危险命令检测** — 扩展风险 shell 命令的模式匹配，并为敏感位置添加文件工具路径保护（`/etc/`、`/boot/`、docker.sock）（[#3872](https://github.com/NousResearch/kclaw/pull/3872)）
- **审批系统中的敏感路径写入检查** — 通过文件工具捕获对系统配置文件的写入，而不仅仅是终端（[#3859](https://github.com/NousResearch/kclaw/pull/3859)）
- **秘密编辑扩展** — 现在涵盖 ElevenLabs、Tavily 和 Exa API 密钥（[#3920](https://github.com/NousResearch/kclaw/pull/3920)）
- **视觉文件拒绝** — 拒绝传递给视觉分析的非图像文件以防止信息泄露（[#3845](https://github.com/NousResearch/kclaw/pull/3845)）
- **阻止类别路径遍历** — 防止技能类别名称中的目录遍历（[#3844](https://github.com/NousResearch/kclaw/pull/3844)）

### 可靠性
- **原子 config.yaml 写入** — 防止网关崩溃期间的数据丢失（[#3800](https://github.com/NousResearch/kclaw/pull/3800)）
- **更新时清除 __pycache__** — 防止过期字节码导致更新后 ImportError（[#3819](https://github.com/NousResearch/kclaw/pull/3819)）
- **延迟导入以确保更新安全** — 防止 `kclaw update` 期间模块引用新函数时的 ImportError 链（[#3776](https://github.com/NousResearch/kclaw/pull/3776)）
- **从补丁损坏中恢复 terminalbench2** — 恢复被补丁工具秘密编辑损坏的文件（[#3801](https://github.com/NousResearch/kclaw/pull/3801)）
- **终端超时保留部分输出** — 超时时不再丢失命令输出（[#3868](https://github.com/NousResearch/kclaw/pull/3868)）

---

## 重要错误修复

- **OpenClaw 迁移模型配置覆盖** — 迁移不再用字符串覆盖模型配置字典（[#3924](https://github.com/NousResearch/kclaw/pull/3924)）— @0xbyt4
- **OpenClaw 迁移扩展** — 覆盖完整数据足迹，包括会话、cron、内存（[#3869](https://github.com/NousResearch/kclaw/pull/3869)）
- **Telegram 已删除的回复目标** — 优雅处理对已删除消息的回复而不是崩溃（[#3858](https://github.com/NousResearch/kclaw/pull/3858)）
- **Discord"思考中..."持久化** — 正确清理延迟的响应指示器（[#3674](https://github.com/NousResearch/kclaw/pull/3674)）
- **WhatsApp LID↔电话别名** — 修复允许列表匹配失败与 Linked ID 格式（[#3830](https://github.com/NousResearch/kclaw/pull/3830)）
- **Signal URL 编码的电话号码** — 修复某些格式的投递失败（[#3670](https://github.com/NousResearch/kclaw/pull/3670)）
- **电子邮件连接泄漏** — 错误时正确关闭 SMTP/IMAP 连接（[#3804](https://github.com/NousResearch/kclaw/pull/3804)）
- **_safe_print ValueError** — 不再因 stdout 关闭而导致网关线程崩溃（[#3843](https://github.com/NousResearch/kclaw/pull/3843)）
- **工具模式 KeyError 'name'** — 确保工具定义中名称字段始终存在（[#3811](https://github.com/NousResearch/kclaw/pull/3811)）
- **提供商切换时 api_mode 陈旧** — 通过 `kclaw model` 切换提供商时正确清除（[#3857](https://github.com/NousResearch/kclaw/pull/3857)）

---

## 测试

- 解决了 hooks、tiktoken、插件和技能测试中的 10+ 个 CI 失败（[#3848](https://github.com/NousResearch/kclaw/pull/3848)，[#3721](https://github.com/NousResearch/kclaw/pull/3721)，[#3936](https://github.com/NousResearch/kclaw/pull/3936)）

---

## 文档

- **全面的 OpenClaw 迁移指南** — 从 OpenClaw/Claw3D 迁移到 KClaw Agent 的分步指南（[#3864](https://github.com/NousResearch/kclaw/pull/3864)，[#3900](https://github.com/NousResearch/kclaw/pull/3900)）
- **凭据文件传递文档** — 记录如何将凭据文件和环境变量转发到远程后端（[#3677](https://github.com/NousResearch/kclaw/pull/3677)）
- **澄清 DuckDuckGo 要求** — 注明对 duckduckgo-search 包的运行时依赖（[#3680](https://github.com/NousResearch/kclaw/pull/3680)）
- **技能目录更新** — 添加红队类别和可选技能列表（[#3745](https://github.com/NousResearch/kclaw/pull/3745)）
- **飞书文档 MDX 修复** — 转义破坏 Docusaurus 构建的角度括号 URL（[#3902](https://github.com/NousResearch/kclaw/pull/3902)）

---

## 贡献者

### 核心
- **@teknium1** — 跨所有子系统的 90 个 PR

### 社区贡献者
- **@kshitijk4poor** — 3 个 PR：Signal 电话号码修复（[#3670](https://github.com/NousResearch/kclaw/pull/3670)），parallel-cli 到 optional-skills（[#3673](https://github.com/NousResearch/kclaw/pull/3673)），状态栏包装修复（[#3883](https://github.com/NousResearch/kclaw/pull/3883)）
- **@winglian** — 1 个 PR：插件消息注入接口（[#3778](https://github.com/NousResearch/kclaw/pull/3778)）
- **@binhnt92** — 1 个 PR：音频下载重试逻辑（[#3401](https://github.com/NousResearch/kclaw/pull/3401)）
- **@0xbyt4** — 1 个 PR：OpenClaw 迁移模型配置修复（[#3924](https://github.com/NousResearch/kclaw/pull/3924)）

### 解决的社区问题
@Material-Scientist（[#850](https://github.com/NousResearch/kclaw/issues/850)），@hanxu98121（[#1734](https://github.com/NousResearch/kclaw/issues/1734)），@penwyp（[#1788](https://github.com/NousResearch/kclaw/issues/1788)），@dan-and（[#1945](https://github.com/NousResearch/kclaw/issues/1945)），@AdrianScott（[#1963](https://github.com/NousResearch/kclaw/issues/1963)），@clawdbot47（[#3229](https://github.com/NousResearch/kclaw/issues/3229)），@alanfwilliams（[#3404](https://github.com/NousResearch/kclaw/issues/3404)），@kentimsit（[#3433](https://github.com/NousResearch/kclaw/issues/3433)），@hayka-pacha（[#3534](https://github.com/NousResearch/kclaw/issues/3534)），@primmer（[#3595](https://github.com/NousResearch/kclaw/issues/3595)），@dagelf（[#3609](https://github.com/NousResearch/kclaw/issues/3609)），@HenkDz（[#3685](https://github.com/NousResearch/kclaw/issues/3685)），@tmdgusya（[#3729](https://github.com/NousResearch/kclaw/issues/3729)），@TypQxQ（[#3753](https://github.com/NousResearch/kclaw/issues/3753)），@acsezen（[#3765](https://github.com/NousResearch/kclaw/issues/3765)）

---

**完整变更日志**： [v2026.3.28...v2026.3.30](https://github.com/NousResearch/kclaw/compare/v2026.3.28...v2026.3.30)
