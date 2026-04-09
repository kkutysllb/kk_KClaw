# KClaw Agent v0.3.0 (v2026.3.17)

**发布日期：** 2026年3月17日

> 流式输出、插件系统与 Provider 版本的发布——统一实时 Token 传输、一流插件架构、重建的 Provider 系统（支持 Vercel AI Gateway）、原生 Anthropic Provider、智能审批系统、实时 Chrome CDP 浏览器连接、ACP IDE 集成、Honcho 记忆管理、语音模式、持久化 Shell 以及跨全平台 50+ 错误修复。

---

## ✨ 亮点

- **统一流式传输基础设施** — CLI 和所有网关平台上的实时逐 Token 传输。响应在生成时即可流式传输，无需等待完整返回。 ([#1538](https://github.com/NousResearch/kclaw/pull/1538))

- **一流插件架构** — 将 Python 文件放入 `~/.kclaw/plugins/` 目录即可使用自定义工具、命令和钩子扩展 KClaw。无需 Fork。 ([#1544](https://github.com/NousResearch/kclaw/pull/1544), [#1555](https://github.com/NousResearch/kclaw/pull/1555))

- **原生 Anthropic Provider** — 直接调用 Anthropic API，支持 Claude Code 凭证自动发现、OAuth PKCE 流程和原生 Prompt 缓存。无需通过 OpenRouter 中转。 ([#1097](https://github.com/NousResearch/kclaw/pull/1097))

- **智能审批系统 + `/stop` 命令** — 源自 Codex 的审批系统，可学习哪些命令是安全的并记住您的偏好。`/stop` 可立即终止当前 Agent 运行。 ([#1543](https://github.com/NousResearch/kclaw/pull/1543))

- **Honcho 记忆集成** — 异步写入、可配置的记忆召回模式、会话标题集成以及网关模式下的多用户隔离。由 @erosika 提供。 ([#736](https://github.com/NousResearch/kclaw/pull/736))

- **语音模式** — CLI 中的按键通话、Telegram/Discord 语音笔记、Discord 语音频道支持，以及通过 faster-whisper 实现的本地 Whisper 转录。 ([#1299](https://github.com/NousResearch/kclaw/pull/1299), [#1185](https://github.com/NousResearch/kclaw/pull/1185), [#1429](https://github.com/NousResearch/kclaw/pull/1429))

- **并发工具执行** — 多个独立的工具调用现在通过 ThreadPoolExecutor 并行运行，显著降低多工具调用的延迟。 ([#1152](https://github.com/NousResearch/kclaw/pull/1152))

- **PII 脱敏** — 当启用 `privacy.redact_pii` 时，个人身份信息在发送上下文给 LLM Provider 前会被自动清除。 ([#1542](https://github.com/NousResearch/kclaw/pull/1542))

- **通过 CDP 的 `/browser connect`** — 通过 Chrome DevTools Protocol 将浏览器工具附加到实时 Chrome 实例。调试、检查并与您已打开的页面交互。 ([#1549](https://github.com/NousResearch/kclaw/pull/1549))

- **Vercel AI Gateway Provider** — 通过 Vercel 的 AI Gateway 路由 KClaw，访问其模型目录和基础设施。 ([#1628](https://github.com/NousResearch/kclaw/pull/1628))

- **集中式 Provider 路由** — 通过 `call_llm` API 重建 Provider 系统，统一 `/model` 命令，切换模型时自动检测 Provider，支持辅助/委托客户端的直接端点覆盖。 ([#1003](https://github.com/NousResearch/kclaw/pull/1003), [#1506](https://github.com/NousResearch/kclaw/pull/1506), [#1375](https://github.com/NousResearch/kclaw/pull/1375))

- **ACP Server（IDE 集成）** — VS Code、Zed 和 JetBrains 现在可以将 KClaw 作为 Agent 后端连接，支持完整的斜杠命令。 ([#1254](https://github.com/NousResearch/kclaw/pull/1254), [#1532](https://github.com/NousResearch/kclaw/pull/1532))

- **持久化 Shell 模式** — 本地和 SSH 终端后端可以在工具调用之间保持 Shell 状态——cd、环境变量和别名均会保留。由 @alt-glitch 提供。 ([#1067](https://github.com/NousResearch/kclaw/pull/1067), [#1483](https://github.com/NousResearch/kclaw/pull/1483))

- **Agentic On-Policy Distillation (OPD)** — 新的 RL 训练环境，用于提炼 Agent 策略，扩展 Atropos 训练生态系统。 ([#1149](https://github.com/NousResearch/kclaw/pull/1149))

---

## 🏗️ 核心 Agent 与架构

### Provider 与模型支持
- **集中式 Provider 路由**，提供 `call_llm` API 和统一的 `/model` 命令——无缝切换模型和 Provider ([#1003](https://github.com/NousResearch/kclaw/pull/1003))
- **Vercel AI Gateway** Provider 支持 ([#1628](https://github.com/NousResearch/kclaw/pull/1628))
- 通过 `/model` 切换模型时**自动检测 Provider** ([[#1506]](https://github.com/NousResearch/kclaw/pull/1506))
- 辅助和委托客户端的**直接端点覆盖**——将视觉/子 Agent 调用指向特定端点 ([#1375](https://github.com/NousResearch/kclaw/pull/1375))
- **原生 Anthropic 辅助视觉**——使用 Claude 的原生视觉 API，而非通过 OpenAI 兼容端点路由 ([#1377](https://github.com/NousResearch/kclaw/pull/1377))
- Anthropic OAuth 流程改进——自动运行 `claude setup-token`、重新认证、PKCE 状态持久化、身份指纹 ([#1132](https://github.com/NousResearch/kclaw/pull/1132), [#1360](https://github.com/NousResearch/kclaw/pull/1360), [#1396](https://github.com/NousResearch/kclaw/pull/1396), [#1597](https://github.com/NousResearch/kclaw/pull/1597))
- 修复 Claude 4.6 模型在无 `budget_tokens` 情况下的自适应思考——由 @ASRagab 提供 ([#1128](https://github.com/NousResearch/kclaw/pull/1128))
- 修复通过适配器的 Anthropic 缓存标记——由 @brandtcormorant 提供 ([#1216](https://github.com/NousResearch/kclaw/pull/1216))
- 重试 Anthropic 429/529 错误并向用户展示详情——由 @0xbyt4 提供 ([#1585](https://github.com/NousResearch/kclaw/pull/1585))
- 修复 Anthropic 适配器 max_tokens、回退崩溃、代理 base_url——由 @0xbyt4 提供 ([#1121](https://github.com/NousResearch/kclaw/pull/1121))
- 修复 DeepSeek V3 解析器丢弃多个并行工具调用——由 @mr-emmett-one 提供 ([#1365](https://github.com/NousResearch/kclaw/pull/1365), [#1300](https://github.com/NousResearch/kclaw/pull/1300))
- 对未列出模型发出警告而非拒绝接受 ([#1047](https://github.com/NousResearch/kclaw/pull/1047), [#1102](https://github.com/NousResearch/kclaw/pull/1102))
- 为不支持的 OpenRouter 模型跳过推理参数 ([#1485](https://github.com/NousResearch/kclaw/pull/1485))
- MiniMax Anthropic API 兼容性修复 ([#1623](https://github.com/NousResearch/kclaw/pull/1623))
- 自定义端点 `/models` 验证和 `/v1` base URL 建议 ([#1480](https://github.com/NousResearch/kclaw/pull/1480))
- 从 `custom_providers` 配置中解析委托 Provider ([#1328](https://github.com/NousResearch/kclaw/pull/1328))
- Kimi 模型新增和 User-Agent 修复 ([#1039](https://github.com/NousResearch/kclaw/pull/1039))
- 为 Mistral 兼容性剥离 `call_id`/`response_item_id` ([#1058](https://github.com/NousResearch/kclaw/pull/1058))

### Agent 循环与对话
- **Anthropic Context Editing API** 支持 ([#1147](https://github.com/NousResearch/kclaw/pull/1147))
- 改进上下文压缩交接摘要——压缩器现在保留更多可操作状态 ([#1273](https://github.com/NousResearch/kclaw/pull/1273))
- 中途运行上下文压缩后同步 session_id ([#1160](https://github.com/NousResearch/kclaw/pull/1160))
- 会话卫生阈值调整为 50% 以实现更主动的压缩 ([#1096](https://github.com/NousResearch/kclaw/pull/1096), [#1161](https://github.com/NousResearch/kclaw/pull/1161))
- 通过 `--pass-session-id` 标志在系统提示中包含会话 ID ([#1040](https://github.com/NousResearch/kclaw/pull/1040))
- 防止跨重试重用已关闭的 OpenAI 客户端 ([#1391](https://github.com/NousResearch/kclaw/pull/1391))
- 清理聊天负载和 Provider 优先级 ([#1253](https://github.com/NousResearch/kclaw/pull/1253))
- 处理来自 Codex 和本地后端的字典工具调用参数 ([#1393](https://github.com/NousResearch/kclaw/pull/1393), [#1440](https://github.com/NousResearch/kclaw/pull/1440))

### 记忆与会话
- **改进记忆优先级**——用户偏好和修正的权重高于程序性知识 ([#1548](https://github.com/NousResearch/kclaw/pull/1548))
- 在系统提示中收紧记忆和会话召回指导 ([#1329](https://github.com/NousResearch/kclaw/pull/1329))
- 将 CLI Token 计数持久化到会话数据库以供 `/insights` 使用 ([#1498](https://github.com/NousResearch/kclaw/pull/1498))
- 将 Honcho 召回保留在缓存的系统前缀之外 ([#1201](https://github.com/NousResearch/kclaw/pull/1201))
- 修正 `seed_ai_identity` 使用 `session.add_messages()` ([#1475](https://github.com/NousResearch/kclaw/pull/1475))
- 为多用户网关隔离 Honcho 会话路由 ([#1500](https://github.com/NousResearch/kclaw/pull/1500))

---

## 📱 消息平台（网关）

### 网关核心
- **系统网关服务模式**——作为系统级 systemd 服务运行，而非仅用户级 ([#1371](https://github.com/NousResearch/kclaw/pull/1371))
- **网关安装范围提示**——在设置期间选择用户或系统范围 ([#1374](https://github.com/NousResearch/kclaw/pull/1374))
- **推理热重载**——无需重启网关即可更改推理设置 ([#1275](https://github.com/NousResearch/kclaw/pull/1275))
- 默认组会话为按用户隔离——群聊中不再有跨用户共享状态 ([#1495](https://github.com/NousResearch/kclaw/pull/1495), [#1417](https://github.com/NousResearch/kclaw/pull/1417))
- 加固网关重启恢复 ([#1310](https://github.com/NousResearch/kclaw/pull/1310))
- 关机时取消正在运行的任务 ([#1427](https://github.com/NousResearch/kclaw/pull/1427))
- NixOS 和非标准系统的 SSL 证书自动检测 ([#1494](https://github.com/NousResearch/kclaw/pull/1494))
- 为无头服务器上的 `systemctl --user` 自动检测 D-Bus 会话总线 ([#1601](https://github.com/NousResearch/kclaw/pull/1601))
- 在无头服务器上安装网关时自动启用 systemd linger ([#1334](https://github.com/NousResearch/kclaw/pull/1334))
- 当 `kclaw` 不在 PATH 上时回退到模块入口点 ([#1355](https://github.com/NousResearch/kclaw/pull/1355))
- 修复 `kclaw update` 后 macOS launchd 上的双网关问题 ([#1567](https://github.com/NousResearch/kclaw/pull/1567))
- 从 systemd 单元中移除递归 ExecStop ([#1530](https://github.com/NousResearch/kclaw/pull/1530))
- 防止网关模式下的日志处理器累积 ([#1251](https://github.com/NousResearch/kclaw/pull/1251))
- 在可重试启动故障时重启——由 @jplew 提供 ([#1517](https://github.com/NousResearch/kclaw/pull/1517))
- 在 Agent 运行后回填网关会话模型 ([#1306](https://github.com/NousResearch/kclaw/pull/1306))
- 基于 PID 的网关终止和延迟配置写入 ([#1499](https://github.com/NousResearch/kclaw/pull/1499))

### Telegram
- 缓冲媒体组以防止照片突发导致自我中断 ([#1341](https://github.com/NousResearch/kclaw/pull/1341), [#1422](https://github.com/NousResearch/kclaw/pull/1422))
- 连接和发送时对临时 TLS 故障进行重试 ([#1535](https://github.com/NousResearch/kclaw/pull/1535))
- 加固轮询冲突处理 ([#1339](https://github.com/NousResearch/kclaw/pull/1339))
- 在 MarkdownV2 中转义分块指示器和内联代码 ([#1478](https://github.com/NousResearch/kclaw/pull/1478), [#1626](https://github.com/NousResearch/kclaw/pull/1626))
- 断开连接前检查更新器/应用状态 ([#1389](https://github.com/NousResearch/kclaw/pull/1389))

### Discord
- `/thread` 命令，支持 `auto_thread` 配置和媒体元数据修复 ([#1178](https://github.com/NousResearch/kclaw/pull/1178))
- @提及时自动创建线程，在机器人线程中跳过提及文本 ([#1438](https://github.com/NousResearch/kclaw/pull/1438))
- 对系统消息重试时不带回复引用 ([#1385](https://github.com/NousResearch/kclaw/pull/1385))
- 保留原生文档和视频附件支持 ([#1392](https://github.com/NousResearch/kclaw/pull/1392))
- 延迟 Discord 适配器注解以避免可选导入崩溃 ([#1314](https://github.com/NousResearch/kclaw/pull/1314))

### Slack
- 线程处理全面改革——进度消息、响应和会话隔离均遵循线程 ([#1103](https://github.com/NousResearch/kclaw/pull/1103))
- 格式设置、反应、用户解析和命令改进 ([#1106](https://github.com/NousResearch/kclaw/pull/1106))
- 修复 MAX_MESSAGE_LENGTH 3900 → 39000 ([#1117](https://github.com/NousResearch/kclaw/pull/1117))
- 文件上传回退保留线程上下文——由 @0xbyt4 提供 ([#1122](https://github.com/NousResearch/kclaw/pull/1122))
- 改进设置指导 ([#1387](https://github.com/NousResearch/kclaw/pull/1387))

### 电子邮件
- 修复 IMAP UID 跟踪和 SMTP TLS 验证 ([#1305](https://github.com/NousResearch/kclaw/pull/1305))
- 通过 config.yaml 添加 `skip_attachments` 选项 ([#1536](https://github.com/NousResearch/kclaw/pull/1536))

### Home Assistant
- 事件过滤默认关闭 ([#1169](https://github.com/NousResearch/kclaw/pull/1169))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **持久化 CLI 状态栏**——始终可见的模型、Provider 和 Token 计数 ([#1522](https://github.com/NousResearch/kclaw/pull/1522))
- **输入提示中的文件路径自动补全** ([#1545](https://github.com/NousResearch/kclaw/pull/1545))
- **`/plan` 命令**——从规格生成实现计划 ([#1372](https://github.com/NousResearch/kclaw/pull/1372), [#1381](https://github.com/NousResearch/kclaw/pull/1381))
- **`/rollback` 重大改进**——更丰富的检查点历史、更清晰的 UX ([#1505](https://github.com/NousResearch/kclaw/pull/1505))
- **启动时预加载 CLI 技能**——技能在首次提示前即可使用 ([#1359](https://github.com/NousResearch/kclaw/pull/1359))
- **集中式斜杠命令注册表**——所有命令定义一次，各处使用 ([#1603](https://github.com/NousResearch/kclaw/pull/1603))
- `/bg` 作为 `/background` 的别名 ([#1590](https://github.com/NousResearch/kclaw/pull/1590))
- 斜杠命令前缀匹配——`/mod` 解析为 `/model` ([#1320](https://github.com/NousResearch/kclaw/pull/1320))
- `/new`、`/reset`、`/clear` 现在真正启动全新会话 ([#1237](https://github.com/NousResearch/kclaw/pull/1237))
- 接受会话 ID 前缀用于会话操作 ([#1425](https://github.com/NousResearch/kclaw/pull/1425))
- TUI 提示和强调输出现在遵循活动皮肤 ([#1282](https://github.com/NousResearch/kclaw/pull/1282))
- 在注册表中集中工具 emoji 元数据 + 皮肤集成 ([#1484](https://github.com/NousResearch/kclaw/pull/1484))
- 危险命令审批添加"查看完整命令"选项——由社区成员 @teknium1 根据设计实现 ([#887](https://github.com/NousResearch/kclaw/pull/887))
- 非阻塞启动更新检查和横幅去重 ([#1386](https://github.com/NousResearch/kclaw/pull/1386))
- `/reasoning` 命令输出排序和内联思考提取修复 ([#1031](https://github.com/NousResearch/kclaw/pull/1031))
- 详细模式显示完整未截断输出 ([#1472](https://github.com/NousResearch/kclaw/pull/1472))
- 修复 `/status` 以报告实时状态和 Token ([#1476](https://github.com/NousResearch/kclaw/pull/1476))
- 植入默认全局 SOUL.md ([#1311](https://github.com/NousResearch/kclaw/pull/1311))

### 设置与配置
- 首次设置期间的 **OpenClaw 迁移**——由 @kshitijk4poor 提供 ([#981](https://github.com/NousResearch/kclaw/pull/981))
- `kclaw claw migrate` 命令 + 迁移文档 ([#1059](https://github.com/NousResearch/kclaw/pull/1059))
- 尊重用户所选 Provider 的智能视觉设置 ([#1323](https://github.com/NousResearch/kclaw/pull/1323))
- 端到端处理无头设置流程 ([#1274](https://github.com/NousResearch/kclaw/pull/1274))
- 在 setup.py 中优先使用 curses 而非 `simple_term_menu` ([#1487](https://github.com/NousResearch/kclaw/pull/1487))
- 在 `/status` 中显示有效模型和 Provider ([#1284](https://github.com/NousResearch/kclaw/pull/1284))
- Config set 示例使用占位符语法 ([#1322](https://github.com/NousResearch/kclaw/pull/1322))
- 重新加载 .env 以覆盖陈旧的 Shell 覆盖 ([#1434](https://github.com/NousResearch/kclaw/pull/1434))
- 修复 is_coding_plan NameError 崩溃——由 @0xbyt4 提供 ([#1123](https://github.com/NousResearch/kclaw/pull/1123))
- 在 setuptools 配置中添加缺失的包——由 @alt-glitch 提供 ([#912](https://github.com/NousResearch/kclaw/pull/912))
- 安装程序：在每个提示处明确说明为何需要 sudo ([#1602](https://github.com/NousResearch/kclaw/pull/1602))

---

## 🔧 工具系统

### 终端与执行
- **本地和 SSH 后端的持久化 Shell 模式**——在工具调用之间保持 Shell 状态——由 @alt-glitch 提供 ([#1067](https://github.com/NousResearch/kclaw/pull/1067), [#1483](https://github.com/NousResearch/kclaw/pull/1483))
- **Tirith 执行前命令扫描**——执行前分析命令的安全层 ([#1256](https://github.com/NousResearch/kclaw/pull/1256))
- 从所有子进程环境中剥离 KClaw Provider 环境变量 ([#1157](https://github.com/NousResearch/kclaw/pull/1157), [#1172](https://github.com/NousResearch/kclaw/pull/1172), [#1399](https://github.com/NousResearch/kclaw/pull/1399), [#1419](https://github.com/NousResearch/kclaw/pull/1419))——初始修复由 @eren-karakus0 提供
- SSH 预检检查 ([#1486](https://github.com/NousResearch/kclaw/pull/1486))
- Docker 后端：使 cwd 工作区挂载成为明确的选入功能 ([#1534](https://github.com/NousResearch/kclaw/pull/1534))
- 在 execute_code 沙盒中将项目根目录添加到 PYTHONPATH ([#1383](https://github.com/NousResearch/kclaw/pull/1383))
- 消除网关平台上的 execute_code 进度刷屏 ([#1098](https://github.com/NousResearch/kclaw/pull/1098))
- 更清晰的 Docker 后端预检错误 ([#1276](https://github.com/NousResearch/kclaw/pull/1276))

### 浏览器
- **`/browser connect`**——通过 CDP 将浏览器工具附加到实时 Chrome 实例 ([#1549](https://github.com/NousResearch/kclaw/pull/1549))
- 改进浏览器清理、本地浏览器 PATH 设置和截图恢复 ([#1333](https://github.com/NousResearch/kclaw/pull/1333))

### MCP
- **基于实用策略的选择性工具加载**——过滤哪些 MCP 工具可用 ([#1302](https://github.com/NousResearch/kclaw/pull/1302))
- 当 `mcp_servers` 配置更改时自动重载 MCP 工具，无需重启 ([#1474](https://github.com/NousResearch/kclaw/pull/1474))
- 解决 npx stdio 连接失败 ([#1291](https://github.com/NousResearch/kclaw/pull/1291))
- 保存平台工具配置时保留 MCP 工具集 ([#1421](https://github.com/NousResearch/kclaw/pull/1421))

### 视觉
- 统一视觉后端门控 ([#1367](https://github.com/NousResearch/kclaw/pull/1367))
- 显示实际错误原因而非通用消息 ([#1338](https://github.com/NousResearch/kclaw/pull/1338))
- 使 Claude 图像处理端到端工作 ([#1408](https://github.com/NousResearch/kclaw/pull/1408))

### Cron
- **将 Cron 管理压缩为一个工具**——单一 `cronjob` 工具替代多个命令 ([#1343](https://github.com/NousResearch/kclaw/pull/1343))
- 抑制对自动投递目标的重复 Cron 发送 ([#1357](https://github.com/NousResearch/kclaw/pull/1357))
- 将 Cron 会话持久化到 SQLite ([#1255](https://github.com/NousResearch/kclaw/pull/1255))
- 每作业运行时覆盖（Provider、模型、base_url） ([#1398](https://github.com/NousResearch/kclaw/pull/1398))
- `save_job_output` 中的原子写入以防止崩溃时数据丢失 ([#1173](https://github.com/NousResearch/kclaw/pull/1173))
- 为 `deliver=origin` 保留线程上下文 ([#1437](https://github.com/NousResearch/kclaw/pull/1437))

### Patch 工具
- 避免在 V4A patch 应用中损坏管道字符 ([#1286](https://github.com/NousResearch/kclaw/pull/1286))
- 宽松的 `block_anchor` 阈值和 Unicode 归一化 ([#1539](https://github.com/NousResearch/kclaw/pull/1539))

### 委托
- 向子 Agent 结果添加可观测性元数据（模型、Token、持续时间、工具追踪） ([#1175](https://github.com/NousResearch/kclaw/pull/1175))

---

## 🧩 技能生态系统

### 技能系统
- **将 skills.sh 集成为与 ClawHub 并列的集线器源** ([#1303](https://github.com/NousResearch/kclaw/pull/1303))
- 加载时安全设置技能环境 ([#1153](https://github.com/NousResearch/kclaw/pull/1153))
- 遵守危险判决的策略表 ([#1330](https://github.com/NousResearch/kclaw/pull/1330))
- 加固 ClawHub 技能搜索精确匹配 ([#1400](https://github.com/NousResearch/kclaw/pull/1400))
- 修复 ClawHub 技能安装——使用 `/download` ZIP 端点 ([#1060](https://github.com/NousResearch/kclaw/pull/1060))
- 避免将本地技能误标为内置——由 @arceus77-7 提供 ([#862](https://github.com/NousResearch/kclaw/pull/862))

### 新增技能
- **Linear** 项目管理 ([#1230](https://github.com/NousResearch/kclaw/pull/1230))
- **X/Twitter** 通过 x-cli ([#1285](https://github.com/NousResearch/kclaw/pull/1285))
- **电话通信**——Twilio、SMS 和 AI 通话 ([#1289](https://github.com/NousResearch/kclaw/pull/1289))
- **1Password**——由 @arceus77-7 提供 ([#883](https://github.com/NousResearch/kclaw/pull/883), [#1179](https://github.com/NousResearch/kclaw/pull/1179))
- **NeuroSkill BCI** 集成 ([#1135](https://github.com/NousResearch/kclaw/pull/1135))
- **Blender MCP** 用于 3D 建模 ([#1531](https://github.com/NousResearch/kclaw/pull/1531))
- **OSS 安全取证** ([#1482](https://github.com/NousResearch/kclaw/pull/1482))
- **并行 CLI** 研究技能 ([#1301](https://github.com/NousResearch/kclaw/pull/1301))
- **OpenCode** CLI 技能 ([#1174](https://github.com/NousResearch/kclaw/pull/1174))
- **ASCII Video** 技能重构——由 @SHL0MS 提供 ([#1213](https://github.com/NousResearch/kclaw/pull/1213), [#1598](https://github.com/NousResearch/kclaw/pull/1598))

---

## 🎙️ 语音模式

- 语音模式基础——CLI 按键通话、Telegram/Discord 语音笔记 ([#1299](https://github.com/NousResearch/kclaw/pull/1299))
- 通过 faster-whisper 实现免费本地 Whisper 转录 ([#1185](https://github.com/NousResearch/kclaw/pull/1185))
- Discord 语音频道可靠性修复 ([#1429](https://github.com/NousResearch/kclaw/pull/1429))
- 恢复网关语音笔记的本地 STT 回退 ([#1490](https://github.com/NousResearch/kclaw/pull/1490))
- 在整个网关转录中遵守 `stt.enabled: false` ([#1394](https://github.com/NousResearch/kclaw/pull/1394))
- 修复 Telegram 语音笔记上的虚假不可用消息（问题 [#1033](https://github.com/NousResearch/kclaw/issues/1033)）

---

## 🔌 ACP（IDE 集成）

- 恢复 ACP Server 实现 ([#1254](https://github.com/NousResearch/kclaw/pull/1254))
- 在 ACP 适配器中支持斜杠命令 ([#1532](https://github.com/NousResearch/kclaw/pull/1532))

---

## 🧪 RL 训练

- **Agentic On-Policy Distillation (OPD) 环境**——用于 Agent 策略提炼的新型 RL 训练环境 ([#1149](https://github.com/NousResearch/kclaw/pull/1149))
- 使 tinker-atropos RL 训练完全可选 ([#1062](https://github.com/NousResearch/kclaw/pull/1062))

---

## 🔒 安全与可靠性

### 安全加固
- **Tirith 执行前命令扫描**——执行前对终端命令进行静态分析 ([#1256](https://github.com/NousResearch/kclaw/pull/1256))
- 当启用 `privacy.redact_pii` 时进行 **PII 脱敏** ([#1542](https://github.com/NousResearch/kclaw/pull/1542))
- 从所有子进程环境中剥离 KClaw Provider/网关/工具环境变量 ([#1157](https://github.com/NousResearch/kclaw/pull/1157), [#1172](https://github.com/NousResearch/kclaw/pull/1172), [#1399](https://github.com/NousResearch/kclaw/pull/1399), [#1419](https://github.com/NousResearch/kclaw/pull/1419))
- Docker cwd 工作区挂载现在明确选入——永不自动挂载主机目录 ([#1534](https://github.com/NousResearch/kclaw/pull/1534))
- 在 fork bomb 正则模式中转义括号和大括号 ([#1397](https://github.com/NousResearch/kclaw/pull/1397))
- 加固 `.worktreeinclude` 路径包含 ([#1388](https://github.com/NousResearch/kclaw/pull/1388))
- 使用描述作为 `pattern_key` 以防止审批冲突 ([#1395](https://github.com/NousResearch/kclaw/pull/1395))

### 可靠性
- 保护初始化时的 stdio 写入 ([#1271](https://github.com/NousResearch/kclaw/pull/1271))
- 会话日志写入重用共享原子 JSON 帮助函数 ([#1280](https://github.com/NousResearch/kclaw/pull/1280))
- 中断时受保护的原子临时清理 ([#1401](https://github.com/NousResearch/kclaw/pull/1401))

---

## 🐛 重要错误修复

- **`/status` 始终显示 0 Token**——现在报告实时状态（问题 [#1465](https://github.com/NousResearch/kclaw/issues/1465)，[#1476](https://github.com/NousResearch/kclaw/pull/1476)）
- **自定义模型端点不工作**——恢复配置保存的端点解析（问题 [#1460](https://github.com/NousResearch/kclaw/issues/1460)，[#1373](https://github.com/NousResearch/kclaw/pull/1373)）
- **MCP 工具在重启前不可见**——配置更改时自动重载（问题 [#1036](https://github.com/NousResearch/kclaw/issues/1036)，[#1474](https://github.com/NousResearch/kclaw/pull/1474)）
- **`kclaw tools` 移除 MCP 工具**——保存时保留 MCP 工具集（问题 [#1247](https://github.com/NousResearch/kclaw/issues/1247)，[#1421](https://github.com/NousResearch/kclaw/pull/1421)）
- **终端子进程继承 `OPENAI_BASE_URL`** 破坏外部工具（问题 [#1002](https://github.com/NousResearch/kclaw/issues/1002)，[#1399](https://github.com/NousResearch/kclaw/pull/1399)）
- **网关重启后后台进程丢失**——改进的恢复（问题 [#1144](https://github.com/NousResearch/kclaw/issues/1144)）
- **Cron 作业未持久化状态**——现在存储在 SQLite 中（问题 [#1416](https://github.com/NousResearch/kclaw/issues/1416)，[#1255](https://github.com/NousResearch/kclaw/pull/1255)）
- **Cronjob `deliver: origin` 未保留线程上下文**（问题 [#1219](https://github.com/NousResearch/kclaw/issues/1219)，[#1437](https://github.com/NousResearch/kclaw/pull/1437)）
- **网关 systemd 服务在浏览器进程孤立时未能自动重启**（问题 [#1617](https://github.com/NousResearch/kclaw/issues/1617)）
- **Telegram 中 `/background` 完成报告被截断**（问题 [#1443](https://github.com/NousResearch/kclaw/issues/1443)）
- **模型切换未生效**（问题 [#1244](https://github.com/NousResearch/kclaw/issues/1244)，[#1183](https://github.com/NousResearch/kclaw/pull/1183)）
- **`kclaw doctor` 报告 cronjob 不可用**（问题 [#878](https://github.com/NousResearch/kclaw/issues/878)，[#1180](https://github.com/NousResearch/kclaw/pull/1180)）
- **WhatsApp 桥接消息未从移动设备接收**（问题 [#1142](https://github.com/NousResearch/kclaw/issues/1142)）
- **设置向导在无头 SSH 上挂起**（问题 [#905](https://github.com/NousResearch/kclaw/issues/905)，[#1274](https://github.com/NousResearch/kclaw/pull/1274)）
- **日志处理器累积** 降低网关性能（问题 [#990](https://github.com/NousResearch/kclaw/issues/990)，[#1251](https://github.com/NousResearch/kclaw/pull/1251)）
- **网关数据库中的 NULL 模型**（问题 [#987](https://github.com/NousResearch/kclaw/issues/987)，[#1306](https://github.com/NousResearch/kclaw/pull/1306)）
- **严格端点拒绝重放 tool_calls**（问题 [#893](https://github.com/NousResearch/kclaw/issues/893)）
- **剩余硬编码 `~/.kclaw` 路径**——现在均遵守 `KCLAW_HOME`（问题 [#892](https://github.com/NousResearch/kclaw/issues/892)，[#1233](https://github.com/NousResearch/kclaw/pull/1233)）
- **委托工具与自定义推理 Provider 不工作**（问题 [#1011](https://github.com/NousResearch/kclaw/issues/1011)，[#1328](https://github.com/NousResearch/kclaw/pull/1328)）
- **技能 Guard 阻止官方技能**（问题 [#1006](https://github.com/NousResearch/kclaw/issues/1006)，[#1330](https://github.com/NousResearch/kclaw/pull/1330)）
- **设置在模型选择前写入 Provider**（问题 [#1182](https://github.com/NousResearch/kclaw/issues/1182)）
- **`GatewayConfig.get()` AttributeError** 导致所有消息处理崩溃（问题 [#1158](https://github.com/NousResearch/kclaw/issues/1158)，[#1287](https://github.com/NousResearch/kclaw/pull/1287)）
- **`/update` 硬失败并显示"command not found"**（问题 [#1049](https://github.com/NousResearch/kclaw/issues/1049)）
- **图像分析静默失败**（问题 [#1034](https://github.com/NousResearch/kclaw/issues/1034)，[#1338](https://github.com/NousResearch/kclaw/pull/1338)）
- **API `BadRequestError` from `'dict'` object has no attribute `'strip'`**（问题 [#1071](https://github.com/NousResearch/kclaw/issues/1071)）
- **斜杠命令需要精确全名**——现在使用前缀匹配（问题 [#928](https://github.com/NousResearch/kclaw/issues/928)，[#1320](https://github.com/NousResearch/kclaw/pull/1320)）
- **在无头系统上关闭终端时网关停止响应**（问题 [#1005](https://github.com/NousResearch/kclaw/issues/1005)）

---

## 🧪 测试

- 覆盖空的缓存 Anthropic 工具调用轮次 ([#1222](https://github.com/NousResearch/kclaw/pull/1222))
- 修复解析器和快速命令覆盖中的过时 CI 假设 ([#1236](https://github.com/NousResearch/kclaw/pull/1236))
- 修复没有隐式事件循环的网关异步测试 ([#1278](https://github.com/NousResearch/kclaw/pull/1278))
- 使网关异步测试 xdist 安全 ([#1281](https://github.com/NousResearch/kclaw/pull/1281))
- Cron 的跨时区朴素时间戳回归 ([#1319](https://github.com/NousResearch/kclaw/pull/1319))
- 从本地环境隔离 codex Provider 测试 ([#1335](https://github.com/NousResearch/kclaw/pull/1335))
- 锁定重试替换语义 ([#1379](https://github.com/NousResearch/kclaw/pull/1379))
- 改进会话搜索工具的错误日志——由 @aydnOktay 提供 ([#1533](https://github.com/NousResearch/kclaw/pull/1533))

---

## 📚 文档

- 全面的 SOUL.md 指南 ([#1315](https://github.com/NousResearch/kclaw/pull/1315))
- 语音模式文档 ([#1316](https://github.com/NousResearch/kclaw/pull/1316), [#1362](https://github.com/NousResearch/kclaw/pull/1362))
- Provider 贡献指南 ([#1361](https://github.com/NousResearch/kclaw/pull/1361))
- ACP 和内部系统实现指南 ([#1259](https://github.com/NousResearch/kclaw/pull/1259))
- 在 CLI、工具、技能和皮肤上扩展 Docusaurus 覆盖 ([#1232](https://github.com/NousResearch/kclaw/pull/1232))
- 终端后端和 Windows 故障排除 ([#1297](https://github.com/NousResearch/kclaw/pull/1297))
- 技能中心参考部分 ([#1317](https://github.com/NousResearch/kclaw/pull/1317))
- 检查点、/rollback 和 git worktrees 指南 ([#1493](https://github.com/NousResearch/kclaw/pull/1493), [#1524](https://github.com/NousResearch/kclaw/pull/1524))
- CLI 状态栏和 /usage 参考 ([#1523](https://github.com/NousResearch/kclaw/pull/1523))
- 回退 Provider + /background 命令文档 ([#1430](https://github.com/NousResearch/kclaw/pull/1430))
- 网关服务范围文档 ([#1378](https://github.com/NousResearch/kclaw/pull/1378))
- Slack 线程回复行为文档 ([#1407](https://github.com/NousResearch/kclaw/pull/1407))
- 使用 Nous 蓝色调色板重新设计的着陆页——由 @austinpickett 提供 ([#974](https://github.com/NousResearch/kclaw/pull/974))
- 修复多个文档拼写错误——由 @JackTheGit 提供 ([#953](https://github.com/NousResearch/kclaw/pull/953))
- 稳定网站图表 ([#1405](https://github.com/NousResearch/kclaw/pull/1405))
- README 中的 CLI 与消息传递快速参考 ([#1491](https://github.com/NousResearch/kclaw/pull/1491))
- 在 Docusaurus 中添加搜索 ([#1053](https://github.com/NousResearch/kclaw/pull/1053))
- Home Assistant 集成文档 ([#1170](https://github.com/NousResearch/kclaw/pull/1170))

---

## 👥 贡献者

### 核心
- **@teknium1** — 220+ PR，涵盖代码库的各个领域

### 顶级社区贡献者

- **@0xbyt4**（4 个 PR）— Anthropic 适配器修复（max_tokens、回退崩溃、429/529 重试）、Slack 文件上传线程上下文、设置 NameError 修复
- **@erosika**（1 个 PR）— Honcho 记忆集成：异步写入、记忆模式、会话标题集成
- **@SHL0MS**（2 个 PR）— ASCII 视频技能设计模式和重构
- **@alt-glitch**（2 个 PR）— 本地/SSH 后端的持久化 Shell 模式、setuptools 打包修复
- **@arceus77-7**（2 个 PR）— 1Password 技能、修复技能列表误标
- **@kshitijk4poor**（1 个 PR）— 设置向导期间的 OpenClaw 迁移
- **@ASRagab**（1 个 PR）— 修复 Claude 4.6 模型的自适应思考
- **@eren-karakus0**（1 个 PR）— 从子进程环境剥离 KClaw Provider 环境变量
- **@mr-emmett-one**（1 个 PR）— 修复 DeepSeek V3 解析器多工具调用支持
- **@jplew**（1 个 PR）— 在可重试启动故障时重启网关
- **@brandtcormorant**（1 个 PR）— 修复空文本块的 Anthropic 缓存控制
- **@aydnOktay**（1 个 PR）— 改进会话搜索工具的错误日志
- **@austinpickett**（1 个 PR）— 使用 Nous 蓝色调色板的着陆页重新设计
- **@JackTheGit**（1 个 PR）— 文档拼写错误修复

### 所有贡献者

@0xbyt4, @alt-glitch, @arceus77-7, @ASRagab, @austinpickett, @aydnOktay, @brandtcormorant, @eren-karakus0, @erosika, @JackTheGit, @jplew, @kshitijk4poor, @mr-emmett-one, @SHL0MS, @teknium1

---

**完整变更日志**: [v2026.3.12...v2026.3.17](https://github.com/NousResearch/kclaw/compare/v2026.3.12...v2026.3.17)
