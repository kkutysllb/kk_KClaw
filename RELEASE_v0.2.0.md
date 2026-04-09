# KClaw Agent v0.2.0 (v2026.3.12)

**发布日期：** 2026年3月12日

> 自 v0.1.0（最初的预公开基础版本）以来的首个正式标签发布。短短两周多时间，KClaw Agent 从一个小型内部项目发展成为一个功能完善的 AI Agent 平台——这得益于社区贡献的爆发式增长。本版本涵盖 **216 个合并的 Pull Request**，来自 **63 位贡献者**，解决了 **119 个问题**。

---

## ✨ 亮点

- **多平台消息网关** — 支持 Telegram、Discord、Slack、WhatsApp、Signal、Email（IMAP/SMTP）和 Home Assistant 平台，提供统一的会话管理、媒体附件和平台特定工具配置。

- **MCP（Model Context Protocol）客户端** — 原生 MCP 支持，包括 stdio 和 HTTP 传输、重连机制、资源/提示发现以及采样（服务器端发起的 LLM 请求）。（[#291](https://github.com/NousResearch/kclaw/pull/291) — @0xbyt4，[#301](https://github.com/NousResearch/kclaw/pull/301)，[#753](https://github.com/NousResearch/kclaw/pull/753)）

- **技能生态系统** — 15+ 分类下提供 70+ 个捆绑和可选技能，配备技能中心供社区发现，支持平台级启用/禁用、根据工具可用性的条件激活以及前置条件验证。（[#743](https://github.com/NousResearch/kclaw/pull/743) — @teyrebaz33，[#785](https://github.com/NousResearch/kclaw/pull/785) — @teyrebaz33）

- **集中式提供商路由** — 统一的 `call_llm()`/`async_call_llm()` API 取代了分散在视觉、摘要、压缩和轨迹保存各处的提供商逻辑。所有辅助消费者通过单一代码路径路由，自动完成凭证解析。（[#1003](https://github.com/NousResearch/kclaw/pull/1003)）

- **ACP 服务器** — 通过 Agent Communication Protocol 标准实现 VS Code、Zed 和 JetBrains 编辑器集成。（[#949](https://github.com/NousResearch/kclaw/pull/949)）

- **CLI 皮肤/主题引擎** — 数据驱动的视觉定制：横幅、旋转加载器、颜色、品牌标识。7 种内置皮肤 + 自定义 YAML 皮肤。

- **Git Worktree 隔离** — `kclaw -w` 在 git worktree 中启动隔离的 Agent 会话，用于在同一仓库上安全地并行工作。（[#654](https://github.com/NousResearch/kclaw/pull/654)）

- **文件系统检查点与回滚** — 在破坏性操作前自动快照，并可通过 `/rollback` 恢复。（[#824](https://github.com/NousResearch/kclaw/pull/824)）

- **3,289 个测试** — 从近乎零测试覆盖率到覆盖 Agent、网关、工具、Cron 和 CLI 的全面测试套件。

---

## 🏗️ 核心 Agent 与架构

### 提供商与模型支持
- 集中式提供商路由器，配有 `resolve_provider_client()` + `call_llm()` API（[#1003](https://github.com/NousResearch/kclaw/pull/1003)）
- Nous Portal 作为一级提供商纳入设置（[#644](https://github.com/NousResearch/kclaw/issues/644)）
- OpenAI Codex（Responses API）支持 ChatGPT 订阅（[#43](https://github.com/NousResearch/kclaw/pull/43)）— @grp06
- Codex OAuth 视觉支持 + 多模态内容适配器
- 通过实时 API 而非硬编码列表验证 `/model`
- 自托管 Firecrawl 支持（[#460](https://github.com/NousResearch/kclaw/pull/460)）— @caentzminger
- Kimi Code API 支持（[#635](https://github.com/NousResearch/kclaw/pull/635)）— @christomitov
- MiniMax 模型 ID 更新（[#473](https://github.com/NousResearch/kclaw/pull/473)）— @tars90percent
- OpenRouter 提供商路由配置（provider_preferences）
- 401 错误时刷新 Nous 凭证（[#571](https://github.com/NousResearch/kclaw/pull/571)，[#269](https://github.com/NousResearch/kclaw/pull/269)）— @rewbs
- z.ai/GLM、Kimi/Moonshot、MiniMax、Azure OpenAI 作为一级提供商
- 统一 `/model` 和 `/provider` 为单一视图

### Agent 循环与会话
- 用于提供商弹性的简单回退模型（[#740](https://github.com/NousResearch/kclaw/pull/740)）
- 父 Agent + 子 Agent 委托之间共享迭代预算
- 通过工具结果注入迭代预算压力
- 可配置的子 Agent 提供商/模型，具有完整的凭证解析
- 通过压缩处理 413 payload-too-large，而非中止（[#153](https://github.com/NousResearch/kclaw/pull/153)）— @tekelala
- 压缩后使用重建的有效载荷重试（[#616](https://github.com/NousResearch/kclaw/pull/616)）— @tripledoublev
- 自动压缩病态大型网关会话（[#628](https://github.com/NousResearch/kclaw/issues/628)）
- 工具调用修复中间件 — 自动小写化和无效工具处理
- 推理努力配置和 `/reasoning` 命令（[#921](https://github.com/NousResearch/kclaw/pull/921)）
- 在上下文压缩后检测并阻止文件重读/搜索循环（[#705](https://github.com/NousResearch/kclaw/pull/705)）— @0xbyt4

### 会话与记忆
- 会话命名，带唯一标题、自动谱系、丰富列表和按名称恢复（[#720](https://github.com/NousResearch/kclaw/pull/720)）
- 带搜索过滤的交互式会话浏览器（[#733](https://github.com/NousResearch/kclaw/pull/733)）
- 恢复会话时显示之前的消息（[#734](https://github.com/NousResearch/kclaw/pull/734)）
- Honcho AI 原生跨会话用户建模（[#38](https://github.com/NousResearch/kclaw/pull/38)）— @erosika
- 会话过期时主动异步刷新记忆
- 智能上下文长度探测，带持久缓存 + 横幅显示
- `/resume` 命令，用于在网关中切换到命名会话
- 消息平台的会话重置策略

---

## 📱 消息平台（网关）

### Telegram
- 原生文件附件：send_document + send_video
- PDF、文本和 Office 文档文件处理 — @tekelala
- 论坛话题会话隔离（[#766](https://github.com/NousResearch/kclaw/pull/766)）— @spanishflu-est1918
- 通过 MEDIA: 协议分享浏览器截图（[#657](https://github.com/NousResearch/kclaw/pull/657)）
- 附近查找技能的位置支持
- TTS 语音消息累积修复（[#176](https://github.com/NousResearch/kclaw/pull/176)）— @Bartok9
- 改进的错误处理和日志记录（[#763](https://github.com/NousResearch/kclaw/pull/763)）— @aydnOktay
- 斜体正则换行修复 + 43 个格式测试（[#204](https://github.com/NousResearch/kclaw/pull/204)）— @0xbyt4

### Discord
- 频道话题包含在会话上下文中（[#248](https://github.com/NousResearch/kclaw/pull/248)）— @Bartok9
- 用于 Bot 消息过滤的 DISCORD_ALLOW_BOTS 配置（[#758](https://github.com/NousResearch/kclaw/pull/758)）
- 文档和视频支持（[#784](https://github.com/NousResearch/kclaw/pull/784)）
- 改进的错误处理和日志记录（[#761](https://github.com/NousResearch/kclaw/pull/761)）— @aydnOktay

### Slack
- App_mention 404 修复 + 文档/视频支持（[#784](https://github.com/NousResearch/kclaw/pull/784)）
- 替换 print 语句的结构化日志 — @aydnOktay

### WhatsApp
- 原生媒体发送 — 图片、视频、文档（[#292](https://github.com/NousResearch/kclaw/pull/292)）— @satelerd
- 多用户会话隔离（[#75](https://github.com/NousResearch/kclaw/pull/75)）— @satelerd
- 跨平台端口清理，替换仅限 Linux 的 fuser（[#433](https://github.com/NousResearch/kclaw/pull/433)）— @Farukest
- DM 中断键不匹配修复（[#350](https://github.com/NousResearch/kclaw/pull/350)）— @Farukest

### Signal
- 通过 signal-cli-rest-api 实现完整 Signal 信使网关（[#405](https://github.com/NousResearch/kclaw/issues/405)）
- 消息事件中的媒体 URL 支持（[#871](https://github.com/NousResearch/kclaw/pull/871)）

### Email（IMAP/SMTP）
- 新的 Email 网关平台 — @0xbyt4

### Home Assistant
- REST 工具 + WebSocket 网关集成（[#184](https://github.com/NousResearch/kclaw/pull/184)）— @0xbyt4
- 服务发现和增强设置
- 工具集映射修复（[#538](https://github.com/NousResearch/kclaw/pull/538)）— @Himess

### 网关核心
- 向用户暴露子 Agent 工具调用和思考过程（[#186](https://github.com/NousResearch/kclaw/pull/186)）— @cutepawss
- 可配置的后台进程监视器通知（[#840](https://github.com/NousResearch/kclaw/pull/840)）
- Telegram/Discord/Slack 的 `edit_message()`，带回退
- `/compress`、`/usage`、`/update` 斜杠命令
- 消除网关会话中 3 倍的 SQLite 消息重复（[#873](https://github.com/NousResearch/kclaw/pull/873)）
- 缓存命中时稳定系统提示跨网关轮次（[#754](https://github.com/NousResearch/kclaw/pull/754)）
- 网关退出时 MCP 服务器关闭（[#796](https://github.com/NousResearch/kclaw/pull/796)）— @0xbyt4
- 传递 session_db 到 AIAgent，修复 session_search 错误（[#108](https://github.com/NousResearch/kclaw/pull/108)）— @Bartok9
- 在 /retry、/undo 中持久化转录更改；修复 /reset 属性（[#217](https://github.com/NousResearch/kclaw/pull/217)）— @Farukest
- UTF-8 编码修复，防止 Windows 崩溃（[#369](https://github.com/NousResearch/kclaw/pull/369)）— @ch3ronsa

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- 数据驱动的皮肤/主题引擎 — 7 种内置皮肤（default、ares、mono、slate、poseidon、sisyphus、charizard）+ 自定义 YAML 皮肤
- `/personality` 命令，支持自定义人格 + 禁用支持（[#773](https://github.com/NousResearch/kclaw/pull/773)）— @teyrebaz33
- 用户定义的快速命令，绕过 Agent 循环（[#746](https://github.com/NousResearch/kclaw/pull/746)）— @teyrebaz33
- `/reasoning` 命令，用于努力级别和显示切换（[#921](https://github.com/NousResearch/kclaw/pull/921)）
- `/verbose` 斜杠命令，用于运行时切换调试（[#94](https://github.com/NousResearch/kclaw/pull/94)）— @cesareth
- `/insights` 命令 — 使用分析、成本估算和活动模式（[#552](https://github.com/NousResearch/kclaw/pull/552)）
- `/background` 命令，用于管理后台进程
- `/help` 格式化，带命令分类
- 完成后响铃 — Agent 结束时终端响铃（[#738](https://github.com/NousResearch/kclaw/pull/738)）
- 上/下箭头历史导航
- 剪贴板图像粘贴（Alt+V / Ctrl+V）
- 慢速斜杠命令的加载指示器（[#882](https://github.com/NousResearch/kclaw/pull/882)）
- patch_stdout 下旋转器闪烁修复（[#91](https://github.com/NousResearch/kclaw/pull/91)）— @0xbyt4
- `--quiet/-Q` 标志，用于程序化单查询模式
- `--fuck-it-ship-it` 标志，绕过所有批准提示（[#724](https://github.com/NousResearch/kclaw/pull/724)）— @dmahan93
- 工具摘要标志（[#767](https://github.com/NousResearch/kclaw/pull/767)）— @luisv-1
- SSH 终端闪烁修复（[#284](https://github.com/NousResearch/kclaw/pull/284)）— @ygd58
- 多行粘贴检测修复（[#84](https://github.com/NousResearch/kclaw/pull/84)）— @0xbyt4

### 设置与配置
- 模块化设置向导，带部分子命令和工具优先 UX
- 容器资源配置提示
- 后端验证必需的二进制文件
- 配置迁移系统（当前 v7）
- API 密钥正确路由到 .env 而非 config.yaml（[#469](https://github.com/NousResearch/kclaw/pull/469)）— @ygd58
- .env 原子写入，防止崩溃时 API 密钥丢失（[#954](https://github.com/NousResearch/kclaw/pull/954)）
- `kclaw tools` — 带 curses UI 的平台级工具启用/禁用
- `kclaw doctor` 用于所有已配置提供商的健康检查
- `kclaw update`，带网关服务自动重启
- 在 CLI 横幅中显示更新可用通知
- 多个命名自定义提供商
- PATH 设置的 Shell 配置检测改进（[#317](https://github.com/NousResearch/kclaw/pull/317)）— @mehmetkr-31
- 一致的 KCLAW_HOME 和 .env 路径解析（[#51](https://github.com/NousResearch/kclaw/pull/51)，[#48](https://github.com/NousResearch/kclaw/pull/48)）— @deankerr
- macOS 上的 Docker 后端修复 + Nous Portal 子代理 auth（[#46](https://github.com/NousResearch/kclaw/pull/46)）— @rsavitt

---

## 🔧 工具系统

### MCP（Model Context Protocol）
- 原生 MCP 客户端，支持 stdio + HTTP 传输（[#291](https://github.com/NousResearch/kclaw/pull/291) — @0xbyt4，[#301](https://github.com/NousResearch/kclaw/pull/301)）
- 采样支持 — 服务器发起的 LLM 请求（[#753](https://github.com/NousResearch/kclaw/pull/753)）
- 资源和提示发现
- 自动重连和安全加固
- 横幅集成、`/reload-mcp` 命令
- `kclaw tools` UI 集成

### 浏览器
- 本地浏览器后端 — 零成本无头 Chromium（无需 Browserbase）
- 控制台/错误工具、带注释的截图、自动录制、dogfood QA 技能（[#745](https://github.com/NousResearch/kclaw/pull/745)）
- 通过 MEDIA: 在所有消息平台上分享截图（[#657](https://github.com/NousResearch/kclaw/pull/657)）

### 终端与执行
- `execute_code` 沙箱，带 json_parse、shell_quote、重试辅助函数
- Docker：自定义卷挂载（[#158](https://github.com/NousResearch/kclaw/pull/158)）— @Indelwin
- Daytona 云沙箱后端（[#451](https://github.com/NousResearch/kclaw/pull/451)）— @rovle
- SSH 后端修复（[#59](https://github.com/NousResearch/kclaw/pull/59)）— @deankerr
- Shell 噪音过滤和登录 shell 执行，确保环境一致性
- execute_code stdout 溢出的头+尾截断
- 可配置的后台进程通知模式

### 文件操作
- 文件系统检查点和 `/rollback` 命令（[#824](https://github.com/NousResearch/kclaw/pull/824)）
- patch 和 search_files 的结构化工具结果提示（下一操作指导）（[#722](https://github.com/NousResearch/kclaw/issues/722)）
- 传递给沙箱容器配置的 Docker 卷（[#687](https://github.com/NousResearch/kclaw/pull/687)）— @manuelschipper

---

## 🧩 技能生态系统

### 技能系统
- 平台级技能启用/禁用（[#743](https://github.com/NousResearch/kclaw/pull/743)）— @teyrebaz33
- 基于工具可用性的条件技能激活（[#785](https://github.com/NousResearch/kclaw/pull/785)）— @teyrebaz33
- 技能前置条件 — 隐藏有未满足依赖的技能（[#659](https://github.com/NousResearch/kclaw/pull/659)）— @kshitijk4poor
- 可选技能 — 附带但默认不激活
- `kclaw skills browse` — 分页中心浏览
- 技能子类别组织
- 平台条件技能加载
- 技能文件原子写入（[#551](https://github.com/NousResearch/kclaw/pull/551)）— @aydnOktay
- 技能同步数据丢失预防（[#563](https://github.com/NousResearch/kclaw/pull/563)）— @0xbyt4
- CLI 和网关的动态技能斜杠命令

### 新增技能（精选）
- **ASCII Art** — pyfiglet（571 种字体）、cowsay、图片转 ASCII（[#209](https://github.com/NousResearch/kclaw/pull/209)）— @0xbyt4
- **ASCII Video** — 完整生产流水线（[#854](https://github.com/NousResearch/kclaw/pull/854)）— @SHL0MS
- **DuckDuckGo Search** — Firecrawl 回退（[#267](https://github.com/NousResearch/kclaw/pull/267)）— @gamedevCloudy；DDGS API 扩展（[#598](https://github.com/NousResearch/kclaw/pull/598)）— @areu01or00
- **Solana Blockchain** — 钱包余额、USD 价格、代币名称（[#212](https://github.com/NousResearch/kclaw/pull/212)）— @gizdusum
- **AgentMail** — Agent 拥有的 Email 收件箱（[#330](https://github.com/NousResearch/kclaw/pull/330)）— @teyrebaz33
- **Polymarket** — 预测市场数据（只读）（[#629](https://github.com/NousResearch/kclaw/pull/629)）
- **OpenClaw Migration** — 官方迁移工具（[#570](https://github.com/NousResearch/kclaw/pull/570)）— @unmodeled-tyler
- **Domain Intelligence** — 被动侦察：子域名、SSL、WHOIS、DNS（[#136](https://github.com/NousResearch/kclaw/pull/136)）— @FurkanL0
- **Superpowers** — 软件开发技能（[#137](https://github.com/NousResearch/kclaw/pull/137)）— @kaos35
- **KClaw-Atropos** — RL 环境开发技能（[#815](https://github.com/NousResearch/kclaw/pull/815)）
- 此外还有：arXiv 搜索、OCR/文档、Excalidraw 图表、YouTube 字幕、GIF 搜索、精灵宝可梦玩家、Minecraft modpack 服务器、OpenHue（Philips Hue）、Google Workspace、Notion、PowerPoint、Obsidian、find-nearby 以及 40+ MLOps 技能

---

## 🔒 安全与可靠性

### 安全加固
- skill_view 中的路径遍历修复 — 阻止读取任意文件（[#220](https://github.com/NousResearch/kclaw/issues/220)）— @Farukest
- sudo 密码管道中的 Shell 注入预防（[#65](https://github.com/NousResearch/kclaw/pull/65)）— @leonsgithub
- 危险命令检测：多行绕过修复（[#233](https://github.com/NousResearch/kclaw/pull/233)）— @Farukest；tee/进程替换模式（[#280](https://github.com/NousResearch/kclaw/pull/280)）— @dogiladeveloper
- skills_guard 中的符号链接边界检查修复（[#386](https://github.com/NousResearch/kclaw/pull/386)）— @Farukest
- macOS 上写入拒绝列表的符号链接绕过修复（[#61](https://github.com/NousResearch/kclaw/pull/61)）— @0xbyt4
- 多词提示注入绕过预防（[#192](https://github.com/NousResearch/kclaw/pull/192)）— @0xbyt4
- Cron 提示注入扫描器绕过修复（[#63](https://github.com/NousResearch/kclaw/pull/63)）— @0xbyt4
- 对敏感文件强制执行 0600/0700 文件权限（[#757](https://github.com/NousResearch/kclaw/pull/757)）
- .env 文件权限限制为仅所有者可访问（[#529](https://github.com/NousResearch/kclaw/pull/529)）— @Himess
- `--force` 标志正确阻止覆盖危险判定（[#388](https://github.com/NousResearch/kclaw/pull/388)）— @Farukest
- FTS5 查询清理 + DB 连接泄漏修复（[#565](https://github.com/NousResearch/kclaw/pull/565)）— @0xbyt4
- 扩展密钥清理模式 + 禁用配置开关
- 内存中的永久白名单，防止数据泄漏（[#600](https://github.com/NousResearch/kclaw/pull/600)）— @alireza78a

### 原子写入（数据丢失预防）
- sessions.json（[#611](https://github.com/NousResearch/kclaw/pull/611)）— @alireza78a
- Cron 任务（[#146](https://github.com/NousResearch/kclaw/pull/146)）— @alireza78a
- .env 配置（[#954](https://github.com/NousResearch/kclaw/pull/954)）
- 进程检查点（[#298](https://github.com/NousResearch/kclaw/pull/298)）— @aydnOktay
- 批处理运行器（[#297](https://github.com/NousResearch/kclaw/pull/297)）— @aydnOktay
- 技能文件（[#551](https://github.com/NousResearch/kclaw/pull/551)）— @aydnOktay

### 可靠性
- 保护所有 print() 免受 OSError 影响，适用于 systemd/无头环境（[#963](https://github.com/NousResearch/kclaw/pull/963)）
- 在 run_conversation 开始时重置所有重试计数器（[#607](https://github.com/NousResearch/kclaw/pull/607)）— @0xbyt4
- 批准回调超时时返回拒绝而非 None（[#603](https://github.com/NousResearch/kclaw/pull/603)）— @0xbyt4
- 修复整个代码库中 None 消息内容导致的崩溃（[#277](https://github.com/NousResearch/kclaw/pull/277)）
- 修复本地 LLM 后端的上下文溢出崩溃（[#403](https://github.com/NousResearch/kclaw/pull/403)）— @ch3ronsa
- 防止 `_flush_sentinel` 泄漏到外部 API（[#227](https://github.com/NousResearch/kclaw/pull/227)）— @Farukest
- 防止调用者中的 conversation_history 变化（[#229](https://github.com/NousResearch/kclaw/pull/229)）— @Farukest
- 修复 systemd 重启循环（[#614](https://github.com/NousResearch/kclaw/pull/614)）— @voidborne-d
- 关闭文件句柄和套接字，防止 fd 泄漏（[#568](https://github.com/NousResearch/kclaw/pull/568) — @alireza78a，[#296](https://github.com/NousResearch/kclaw/pull/296) — @alireza78a，[#709](https://github.com/NousResearch/kclaw/pull/709) — @memosr）
- 防止剪贴板 PNG 转换中的数据丢失（[#602](https://github.com/NousResearch/kclaw/pull/602)）— @0xbyt4
- 消除终端输出中的 Shell 噪音（[#293](https://github.com/NousResearch/kclaw/pull/293)）— @0xbyt4
- 提示、Cron 和 execute_code 的时区感知 now()（[#309](https://github.com/NousResearch/kclaw/pull/309)）— @areu01or00

### Windows 兼容性
- 保护仅 POSIX 的进程函数（[#219](https://github.com/NousResearch/kclaw/pull/219)）— @Farukest
- 通过 Git Bash + 基于 ZIP 的更新回退实现 Windows 原生支持
- pywinpty 用于 PTY 支持（[#457](https://github.com/NousResearch/kclaw/pull/457)）— @shitcoinsherpa
- 所有配置/数据文件 I/O 上的显式 UTF-8 编码（[#458](https://github.com/NousResearch/kclaw/pull/458)）— @shitcoinsherpa
- Windows 兼容的路径处理（[#354](https://github.com/NousResearch/kclaw/pull/354)，[#390](https://github.com/NousResearch/kclaw/pull/390)）— @Farukest
- 驱动器字母路径的正则表达式搜索输出解析（[#533](https://github.com/NousResearch/kclaw/pull/533)）— @Himess
- Windows 认证存储文件锁（[#455](https://github.com/NousResearch/kclaw/pull/455)）— @shitcoinsherpa

---

## 🐛 重要 Bug 修复

- 修复 DeepSeek V3 工具调用解析器静默丢弃多行 JSON 参数（[#444](https://github.com/NousResearch/kclaw/pull/444)）— @PercyDikec
- 修复由于偏移量不匹配导致网关转录每轮丢失 1 条消息（[#395](https://github.com/NousResearch/kclaw/pull/395)）— @PercyDikec
- 修复 /retry 命令静默丢弃 Agent 最终响应（[#441](https://github.com/NousResearch/kclaw/pull/441)）— @PercyDikec
- 修复 think-block 剥离后 max-iterations 重试返回空字符串（[#438](https://github.com/NousResearch/kclaw/pull/438)）— @PercyDikec
- 修复 max-iterations 重试使用硬编码的 max_tokens（[#436](https://github.com/NousResearch/kclaw/pull/436)）— @Farukest
- 修复 Codex 状态字典键不匹配（[#448](https://github.com/NousResearch/kclaw/pull/448)）和可见性过滤器（[#446](https://github.com/NousResearch/kclaw/pull/446)）— @PercyDikec
- 从最终面向用户的响应中剥离 \<think\> 块（[#174](https://github.com/NousResearch/kclaw/pull/174)）— @Bartok9
- 修复当模型字面讨论标签时 \<think\> 块正则剥离可见内容（[#786](https://github.com/NousResearch/kclaw/issues/786)）
- 修复 Mistral 422 错误来自 assistant 消息中残留的 finish_reason（[#253](https://github.com/NousResearch/kclaw/pull/253)）— @Sertug17
- 修复所有代码路径中 OPENROUTER_API_KEY 解析顺序（[#295](https://github.com/NousResearch/kclaw/pull/295)）— @0xbyt4
- 修复 OPENAI_BASE_URL API 密钥优先级（[#420](https://github.com/NousResearch/kclaw/pull/420)）— @manuelschipper
- 修复 Anthropic "prompt is too long" 400 错误未被识别为上下文长度错误（[#813](https://github.com/NousResearch/kclaw/issues/813)）
- 修复 SQLite 会话转录累积重复消息 — 3-4 倍 token 膨胀（[#860](https://github.com/NousResearch/kclaw/issues/860)）
- 修复设置向导在首次安装时跳过 API 密钥提示（[#748](https://github.com/NousResearch/kclaw/pull/748)）
- 修复设置向导为 Nous Portal 显示 OpenRouter 模型列表（[#575](https://github.com/NousResearch/kclaw/pull/575)）— @PercyDikec
- 修复通过 kclaw model 切换时提供商选择未持久化（[#881](https://github.com/NousResearch/kclaw/pull/881)）
- 修复 macOS 上 docker 不在 PATH 时 Docker 后端失败（[#889](https://github.com/NousResearch/kclaw/pull/889)）
- 修复 ClawHub 技能中心适配器的 API 端点变更（[#286](https://github.com/NousResearch/kclaw/pull/286)）— @BP602
- 修复存在 API 密钥时 Honcho 自动启用（[#243](https://github.com/NousResearch/kclaw/pull/243)）— @Bartok9
- 修复 Python 3.11+ 上重复 'skills' 子解析器崩溃（[#898](https://github.com/NousResearch/kclaw/issues/898)）
- 修复内容包含章节符号时的记忆工具条目解析（[#162](https://github.com/NousResearch/kclaw/pull/162)）— @aydnOktay
- 修复交互式提示失败时管道安装静默中止（[#72](https://github.com/NousResearch/kclaw/pull/72)）— @cutepawss
- 修复递归删除检测中的误报（[#68](https://github.com/NousResearch/kclaw/pull/68)）— @cutepawss
- 修复整个代码库的 Ruff lint 警告（[#608](https://github.com/NousResearch/kclaw/pull/608)）— @JackTheGit
- 修复 Anthropic 原生 base URL 快速失败（[#173](https://github.com/NousResearch/kclaw/pull/173)）— @adavyas
- 修复 install.sh 在移动 Node.js 目录前创建 ~/.kclaw（[#53](https://github.com/NousResearch/kclaw/pull/53)）— @JoshuaMart
- 修复 Ctrl+C 时 atexit 清理期间的 SystemExit traceback（[#55](https://github.com/NousResearch/kclaw/pull/55)）— @bierlingm
- 恢复缺失的 MIT 许可证文件（[#620](https://github.com/NousResearch/kclaw/pull/620)）— @stablegenius49

---

## 🧪 测试

- **3,289 个测试**，覆盖 Agent、网关、工具、Cron 和 CLI
- 使用 pytest-xdist 并行化测试套件（[#802](https://github.com/NousResearch/kclaw/pull/802)）— @OutThisLife
- 单元测试批次 1：8 个核心模块（[#60](https://github.com/NousResearch/kclaw/pull/60)）— @0xbyt4
- 单元测试批次 2：8 个更多模块（[#62](https://github.com/NousResearch/kclaw/pull/62)）— @0xbyt4
- 单元测试批次 3：8 个未测试模块（[#191](https://github.com/NousResearch/kclaw/pull/191)）— @0xbyt4
- 单元测试批次 4：5 个安全/逻辑关键模块（[#193](https://github.com/NousResearch/kclaw/pull/193)）— @0xbyt4
- AIAgent（run_agent.py）单元测试（[#67](https://github.com/NousResearch/kclaw/pull/67)）— @0xbyt4
- 轨迹压缩器测试（[#203](https://github.com/NousResearch/kclaw/pull/203)）— @0xbyt4
- 澄清工具测试（[#121](https://github.com/NousResearch/kclaw/pull/121)）— @Bartok9
- Telegram 格式测试 — 43 个斜体/粗体/代码渲染测试（[#204](https://github.com/NousResearch/kclaw/pull/204)）— @0xbyt4
- 视觉工具类型提示 + 42 个测试（[#792](https://github.com/NousResearch/kclaw/pull/792)）
- 压缩器工具调用边界回归测试（[#648](https://github.com/NousResearch/kclaw/pull/648)）— @intertwine
- 测试结构重组（[#34](https://github.com/NousResearch/kclaw/pull/34)）— @0xbyt4
- Shell 噪音消除 + 修复 36 个测试失败（[#293](https://github.com/NousResearch/kclaw/pull/293)）— @0xbyt4

---

## 🔬 强化学习与评估环境

- WebResearchEnv — 多步 Web 研究强化学习环境（[#434](https://github.com/NousResearch/kclaw/pull/434)）— @jackx707
- Modal 沙箱并发限制以避免死锁（[#621](https://github.com/NousResearch/kclaw/pull/621)）— @voteblake
- KClaw-atropos-environments 捆绑技能（[#815](https://github.com/NousResearch/kclaw/pull/815)）
- 本地 vLLM 实例支持评估 — @dmahan93
- YC-Bench 长时域 Agent 基准测试环境
- OpenThoughts-TBLite 评估环境和脚本

---

## 📚 文档

- 完整的文档网站（Docusaurus），37+ 页面
- Telegram、Discord、Slack、WhatsApp、Signal、Email 全面平台设置指南
- AGENTS.md — AI 编程助手开发指南
- CONTRIBUTING.md（[#117](https://github.com/NousResearch/kclaw/pull/117)）— @Bartok9
- 斜杠命令参考（[#142](https://github.com/NousResearch/kclaw/pull/142)）— @Bartok9
- AGENTS.md 全面准确性审核（[#732](https://github.com/NousResearch/kclaw/pull/732)）
- 皮肤/主题系统文档
- MCP 文档和示例
- 文档准确性审核 — 35+ 处修正
- 文档拼写错误修复（[#825](https://github.com/NousResearch/kclaw/pull/825)，[#439](https://github.com/NousResearch/kclaw/pull/439)）— @JackTheGit
- CLI 配置优先权和术语标准化（[#166](https://github.com/NousResearch/kclaw/pull/166)，[#167](https://github.com/NousResearch/kclaw/pull/167)，[#168](https://github.com/NousResearch/kclaw/pull/168)）— @Jr-kenny
- Telegram 令牌正则表达式文档（[#713](https://github.com/NousResearch/kclaw/pull/713)）— @VolodymyrBg

---

## 👥 贡献者

感谢 63 位使此版本成为可能的贡献者！短短两周多时间，KClaw Agent 社区齐心协力完成了大量工作。

### 核心
- **@teknium1** — 43 个 PR：项目负责人、核心架构、提供商路由、会话、技能、CLI、文档

### 顶级社区贡献者
- **@0xbyt4** — 40 个 PR：MCP 客户端、Home Assistant、安全修复（符号链接、提示注入、Cron）、广泛测试覆盖（6 个批次）、ascii-art 技能、shell 噪音消除、技能同步、Telegram 格式化以及众多其他贡献
- **@Farukest** — 16 个 PR：安全加固（路径遍历、危险命令检测、符号链接边界）、Windows 兼容性（POSIX 保护、路径处理）、WhatsApp 修复、max-iterations 重试、网关修复
- **@aydnOktay** — 11 个 PR：原子写入（进程检查点、批处理运行器、技能文件）、跨 Telegram、Discord、代码执行、转录、TTS 和技能的错误处理改进
- **@Bartok9** — 9 个 PR：CONTRIBUTING.md、斜杠命令参考、Discord 频道话题、think-block 剥离、TTS 修复、Honcho 修复、会话计数修复、澄清测试
- **@PercyDikec** — 7 个 PR：DeepSeek V3 解析器修复、/retry 响应丢弃、网关转录偏移、Codex 状态/可见性、max-iterations 重试、设置向导修复
- **@teyrebaz33** — 5 个 PR：技能启用/禁用系统、快速命令、人格定制、条件技能激活
- **@alireza78a** — 5 个 PR：原子写入（cron、会话）、fd 泄漏预防、安全白名单、代码执行套接字清理
- **@shitcoinsherpa** — 3 个 PR：Windows 支持（pywinpty、UTF-8 编码、认证存储锁）
- **@Himess** — 3 个 PR：Cron/HomeAssistant/Daytona 修复、Windows 驱动器字母解析、.env 权限
- **@satelerd** — 2 个 PR：WhatsApp 原生媒体、多用户会话隔离
- **@rovle** — 1 个 PR：Daytona 云沙箱后端（4 个提交）
- **@erosika** — 1 个 PR：Honcho AI 原生记忆集成
- **@dmahan93** — 1 个 PR：--fuck-it-ship-it 标志 + 强化学习环境工作
- **@SHL0MS** — 1 个 PR：ASCII video 技能

### 所有贡献者
@0xbyt4, @BP602, @Bartok9, @Farukest, @FurkanL0, @Himess, @Indelwin, @JackTheGit, @JoshuaMart, @Jr-kenny, @OutThisLife, @PercyDikec, @SHL0MS, @Sertug17, @VencentSoliman, @VolodymyrBg, @adavyas, @alireza78a, @areu01or00, @aydnOktay, @batuhankocyigit, @bierlingm, @caentzminger, @cesareth, @ch3ronsa, @christomitov, @cutepawss, @deankerr, @dmahan93, @dogiladeveloper, @dragonkhoi, @erosika, @gamedevCloudy, @gizdusum, @grp06, @intertwine, @jackx707, @jdblackstar, @johnh4098, @kaos35, @kshitijk4poor, @leonsgithub, @luisv-1, @manuelschipper, @mehmetkr-31, @memosr, @PeterFile, @rewbs, @rovle, @rsavitt, @satelerd, @spanishflu-est1918, @stablegenius49, @tars90percent, @tekelala, @teknium1, @teyrebaz33, @tripledoublev, @unmodeled-tyler, @voidborne-d, @voteblake, @ygd58

---

**完整变更日志**：[v0.1.0...v2026.3.12](https://github.com/NousResearch/kclaw/compare/v0.1.0...v2026.3.12)
