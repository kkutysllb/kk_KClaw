---
name: qmd
description: 使用 qmd 在本地搜索个人知识库、笔记、文档和会议记录 — 具有 BM25、向量搜索和 LLM 重排的混合检索引擎。支持 CLI 和 MCP 集成。
version: 1.0.0
author: KClaw Agent + Teknium
license: MIT
platforms: [macos, linux]
metadata:
  kclaw:
    tags: [Search, Knowledge-Base, RAG, Notes, MCP, Local-AI]
    related_skills: [obsidian, native-mcp, arxiv]
---

# QMD — 查询标记文档

用于个人知识库的本地、设备上的搜索引擎。为 markdown 笔记、会议记录、文档和任何基于文本的文件建立索引，然后提供结合关键字匹配、语义理解和 LLM 驱动重排的混合搜索 — 全部在本地运行，无云依赖。

由 [Tobi Lütke](https://github.com/tobi/qmd) 创建。MIT 许可。

## 何时使用

- 用户要求搜索他们的笔记、文档、知识库或会议记录
- 用户想要在大量 markdown/文本文件集合中查找内容
- 用户想要语义搜索（"查找关于 X 概念的笔记"）而不仅仅是关键字 grep
- 用户已经设置了 qmd 集合并想要查询它们
- 用户要求设置本地知识库或文档搜索系统
- 关键词："搜索我的笔记"、"在我的文档中查找"、"知识库"、"qmd"

## 先决条件

### Node.js >= 22（必需）

```bash
# 检查版本
node --version  # 必须 >= 22

# macOS — 通过 Homebrew 安装或升级
brew install node@22

# Linux — 使用 NodeSource 或 nvm
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
# 或使用 nvm：
nvm install 22 && nvm use 22
```

### SQLite 扩展支持（仅限 macOS）

macOS 系统 SQLite 缺少扩展加载。通过 Homebrew 安装：

```bash
brew install sqlite
```

### 安装 qmd

```bash
npm install -g @tobilu/qmd
# 或使用 Bun：
bun install -g @tobilu/qmd
```

首次运行自动下载 3 个本地 GGUF 模型（约 2GB 总计）：

| 模型 | 用途 | 大小 |
|------|------|------|
| embeddinggemma-300M-Q8_0 | 向量嵌入 | ~300MB |
| qwen3-reranker-0.6b-q8_0 | 结果重排 | ~640MB |
| qmd-query-expansion-1.7B | 查询扩展 | ~1.1GB |

### 验证安装

```bash
qmd --version
qmd status
```

## 快速参考

| 命令 | 功能 | 速度 |
|------|------|------|
| `qmd search "query"` | BM25 关键字搜索（无模型）| ~0.2s |
| `qmd vsearch "query"` | 语义向量搜索（1 个模型）| ~3s |
| `qmd query "query"` | 混合 + 重排（全部 3 个模型）| ~2-3s 预热，~19s 冷启动 |
| `qmd get <docid>` | 检索完整文档内容 | 即时 |
| `qmd multi-get "glob"` | 检索多个文件 | 即时 |
| `qmd collection add <path> --name <n>` | 将目录添加为集合 | 即时 |
| `qmd context add <path> "description"` | 添加上下文元数据以提高检索质量 | 即时 |
| `qmd embed` | 生成/更新向量嵌入 | 变化 |
| `qmd status` | 显示索引健康和集合信息 | 即时 |
| `qmd mcp` | 启动 MCP 服务器（stdio）| 持久 |
| `qmd mcp --http --daemon` | 启动 MCP 服务器（HTTP，预热模型）| 持久 |

## 设置工作流

### 1. 添加集合

将 qmd 指向包含您的文档的目录：

```bash
# 添加笔记目录
qmd collection add ~/notes --name notes

# 添加项目文档
qmd collection add ~/projects/myproject/docs --name project-docs

# 添加会议记录
qmd collection add ~/meetings --name meetings

# 列出所有集合
qmd collection list
```

### 2. 添加上下文描述

上下文元数据帮助搜索引擎理解每个集合包含什么。这显著提高了检索质量：

```bash
qmd context add qmd://notes "个人笔记、想法和日记条目"
qmd context add qmd://project-docs "主项目的技术文档"
qmd context add qmd://meetings "团队同步会议记录和行动项"
```

### 3. 生成嵌入

```bash
qmd embed
```

这处理所有集合中的所有文档并生成向量嵌入。添加新文档或集合后重新运行。

### 4. 验证

```bash
qmd status   # 显示索引健康、集合统计、模型信息
```

## 搜索模式

### 快速关键字搜索（BM25）

最适合：精确术语、代码标识符、名称、已知短语。
不加载模型 — 几乎即时的结果。

```bash
qmd search "authentication middleware"
qmd search "handleError async"
```

### 语义向量搜索

最适合：自然语言问题、概念查询。
加载嵌入模型（首次查询约 3s）。

```bash
qmd vsearch "how does the rate limiter handle burst traffic"
qmd vsearch "ideas for improving onboarding flow"
```

### 带重排的混合搜索（最佳质量）

最适合：重要查询，质量最重要。
使用全部 3 个模型 — 查询扩展、并行 BM25+向量、重排。

```bash
qmd query "what decisions were made about the database migration"
```

### 结构化多模式查询

在单个查询中组合不同的搜索类型以提高精度：

```bash
# BM25 精确术语 + 向量概念
qmd query $'lex: rate limiter\nvec: how does throttling work under load'

# 带查询扩展
qmd query $'expand: database migration plan\nlex: "schema change"'
```

### 查询语法（lex/BM25 模式）

| 语法 | 效果 | 示例 |
|------|------|------|
| `term` | 前缀匹配 | `perf` 匹配 "performance" |
| `"phrase"` | 精确短语 | `"rate limiter"` |
| `-term` | 排除术语 | `performance -sports` |

### HyDE（假设文档嵌入）

对于复杂主题，写出您期望答案的样子：

```bash
qmd query $'hyde: The migration plan involves three phases. First, we add the new columns without dropping the old ones. Then we backfill data. Finally we cut over and remove legacy columns.'
```

### 限定集合范围

```bash
qmd search "query" --collection notes
qmd query "query" --collection project-docs
```

### 输出格式

```bash
qmd search "query" --json        # JSON 输出（最适合解析）
qmd search "query" --limit 5     # 限制结果数量
qmd get "#abc123"                # 通过文档 ID 获取
qmd get "path/to/file.md"       # 通过文件路径获取
qmd get "file.md:50" -l 100     # 获取特定行范围
qmd multi-get "journals/*.md" --json  # 通过 glob 批量检索
```

## MCP 集成（推荐）

qmd 暴露了一个 MCP 服务器，通过原生 MCP 客户端直接向 KClaw Agent 提供搜索工具。这是首选集成 — 一旦配置，代理会自动获得 qmd 工具，而无需加载此技能。

### 选项 A：Stdio 模式（简单）

添加到 `~/.kclaw/config.yaml`：

```yaml
mcp_servers:
  qmd:
    command: "qmd"
    args: ["mcp"]
    timeout: 30
    connect_timeout: 45
```

这注册了工具：`mcp_qmd_search`、`mcp_qmd_vsearch`、`mcp_qmd_deep_search`、`mcp_qmd_get`、`mcp_qmd_status`。

**权衡：** 模型在第一次搜索调用时加载（约 19s 冷启动），然后在会话期间保持预热。对于偶尔使用来说可以接受。

### 选项 B：HTTP 守护进程模式（快速，推荐重度使用）

单独启动 qmd 守护进程 — 它在内存中保持模型预热：

```bash
# 启动守护进程（跨代理重启持久化）
qmd mcp --http --daemon

# 默认运行在 http://localhost:8181
```

然后配置 KClaw Agent 通过 HTTP 连接：

```yaml
mcp_servers:
  qmd:
    url: "http://localhost:8181/mcp"
    timeout: 30
```

**权衡：** 运行期间使用约 2GB RAM，但每次查询都很快（~2-3s）。最适合频繁搜索的用户。

### 保持守护进程运行

#### macOS（launchd）

```bash
cat > ~/Library/LaunchAgents/com.qmd.daemon.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.qmd.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>qmd</string>
    <string>mcp</string>
    <string>--http</string>
    <string>--daemon</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/qmd-daemon.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/qmd-daemon.log</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.qmd.daemon.plist
```

#### Linux（systemd 用户服务）

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/qmd-daemon.service << 'EOF'
[Unit]
Description=QMD MCP Daemon
After=network.target

[Service]
ExecStart=qmd mcp --http --daemon
Restart=on-failure
RestartSec=10
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now qmd-daemon
systemctl --user status qmd-daemon
```

### MCP 工具参考

连接后，这些工具可用作 `mcp_qmd_*`：

| MCP 工具 | 映射到 | 描述 |
|----------|--------|------|
| `mcp_qmd_search` | `qmd search` | BM25 关键字搜索 |
| `mcp_qmd_vsearch` | `qmd vsearch` | 语义向量搜索 |
| `mcp_qmd_deep_search` | `qmd query` | 混合搜索 + 重排 |
| `mcp_qmd_get` | `qmd get` | 通过 ID 或路径检索文档 |
| `mcp_qmd_status` | `qmd status` | 索引健康和统计 |

MCP 工具接受结构化 JSON 查询以进行多模式搜索：

```json
{
  "searches": [
    {"type": "lex", "query": "authentication middleware"},
    {"type": "vec", "query": "how user login is verified"}
  ],
  "collections": ["project-docs"],
  "limit": 10
}
```

## CLI 使用（无 MCP）

当 MCP 未配置时，直接通过终端使用 qmd：

```
terminal(command="qmd query 'what was decided about the API redesign' --json", timeout=30)
```

对于设置和管理任务，始终使用终端：

```
terminal(command="qmd collection add ~/Documents/notes --name notes")
terminal(command="qmd context add qmd://notes 'Personal research notes and ideas'")
terminal(command="qmd embed")
terminal(command="qmd status")
```

## 搜索管道如何工作

了解内部原理有助于选择正确的搜索模式：

1. **查询扩展** — 微调的 1.7B 模型生成 2 个替代查询。原始查询在融合中获得 2 倍权重。
2. **并行检索** — BM25（SQLite FTS5）和向量搜索跨所有查询变体同时运行。
3. **RRF 融合** — 互惠排名融合（k=60）合并结果。顶级奖励：#1 获得 +0.05，#2-3 获得 +0.02。
4. **LLM 重排** — qwen3-reranker 对前 30 名候选进行评分（0.0-1.0）。
5. **位置感知混合** — 排名 1-3：75% 检索 / 25% 重排器。排名 4-10：60/40。排名 11+：40/60（对长尾更信任重排器）。

**智能分块：** 文档在自然断点（标题、代码块、空白行）处拆分，目标约 900 个 token，带 15% 重叠。代码块永远不会在块中间拆分。

## 最佳实践

1. **始终添加上下文描述** — `qmd context add` 显著提高检索准确性。描述每个集合包含什么。
2. **添加文档后重新嵌入** — 将新文件添加到集合后必须重新运行 `qmd embed`。
3. **使用 `qmd search` 加快速度** — 当需要快速关键字查找（代码标识符、精确名称）时，BM25 是即时的且不需要模型。
4. **使用 `qmd query` 以获得质量** — 当问题是概念性的或用户需要最佳结果时，使用混合搜索。
5. **首选 MCP 集成** — 一旦配置，代理获得原生工具，而无需每次加载此技能。
6. **对于频繁用户使用守护进程模式** — 如果用户定期搜索他们的知识库，推荐 HTTP 守护进程设置。
7. **结构化搜索中的第一个查询获得 2 倍权重** — 在组合 lex 和 vec 时，将最重要/最确定的查询放在第一位。

## 故障排除

### "首次运行时模型下载中"
正常 — qmd 在首次使用时自动下载约 2GB 的 GGUF 模型。这是一次性操作。

### 冷启动延迟（约 19s）
这发生在模型未加载到内存时。解决方案：
- 使用 HTTP 守护进程模式（`qmd mcp --http --daemon`）保持预热
- 当不需要模型时使用 `qmd search`（仅 BM25）
- MCP stdio 模式在第一次搜索时加载模型，在会话期间保持预热

### macOS："无法加载扩展"
安装 Homebrew SQLite：`brew install sqlite`
然后确保它在系统 SQLite 之前在 PATH 上。

### "未找到集合"
运行 `qmd collection add <path> --name <name>` 添加目录，然后 `qmd embed` 为它们建立索引。

### 嵌入模型覆盖（中文/多语言）
为非英语内容设置 `QMD_EMBED_MODEL` 环境变量：
```bash
export QMD_EMBED_MODEL="your-multilingual-model"
```

## 数据存储

- **索引和向量：** `~/.cache/qmd/index.sqlite`
- **模型：** 首次运行时自动下载到本地缓存
- **无云依赖** — 一切在本地运行

## 参考

- [GitHub: tobi/qmd](https://github.com/tobi/qmd)
- [QMD 变更日志](https://github.com/tobi/qmd/blob/main/CHANGELOG.md)
