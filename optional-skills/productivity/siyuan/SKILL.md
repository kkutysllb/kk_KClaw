---
name: siyuan
description: 通过 curl 使用 SiYuan Note API 搜索、读取、创建和管理自托管知识库中的块和文档。
version: 1.0.0
author: FEUAZUR
license: MIT
metadata:
  kclaw:
    tags: [SiYuan, 笔记, 知识库, PKM, API]
    related_skills: [obsidian, notion]
    homepage: https://github.com/siyuan-note/siyuan
prerequisites:
  env_vars: [SIYUAN_TOKEN]
  commands: [curl, jq]
required_environment_variables:
  - name: SIYUAN_TOKEN
    prompt: SiYuan API token
    help: "Settings > About in SiYuan desktop app"
  - name: SIYUAN_URL
    prompt: SiYuan instance URL (default http://127.0.0.1:6806)
    required_for: remote instances
---

# SiYuan Note API

通过 curl 使用 [SiYuan](https://github.com/siyuan-note/siyuan) 内核 API 搜索、读取、创建、更新和删除自托管知识库中的块和文档。无需额外工具 — 只需 curl 和 API 令牌。

## 前置要求

1. 安装并运行 SiYuan（桌面版或 Docker）
2. 获取您的 API 令牌：**Settings > About > API token**
3. 存储到 `~/.kclaw/.env`：
   ```
   SIYUAN_TOKEN=your_token_here
   SIYUAN_URL=http://127.0.0.1:6806
   ```
   如果未设置，`SIYUAN_URL` 默认为 `http://127.0.0.1:6806`。

## API 基础

所有 SiYuan API 调用都是 **POST 带 JSON body**。每个请求遵循此模式：

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/..." \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"param": "value"}'
```

响应是带此结构的 JSON：
```json
{"code": 0, "msg": "", "data": { ... }}
```
`code: 0` 表示成功。任何其他值都是错误 — 检查 `msg` 获取详情。

**ID 格式：** SiYuan ID 看起来像 `20210808180117-6v0mkxr`（14 位时间戳 + 7 个字母数字字符）。

## 快速参考

| 操作 | 端点 |
|-----------|---------|
| 全文搜索 | `/api/search/fullTextSearchBlock` |
| SQL 查询 | `/api/query/sql` |
| 读取块 | `/api/block/getBlockKramdown` |
| 读取子块 | `/api/block/getChildBlocks` |
| 获取路径 | `/api/filetree/getHPathByID` |
| 获取属性 | `/api/attr/getBlockAttrs` |
| 列出笔记本 | `/api/notebook/lsNotebooks` |
| 列出文档 | `/api/filetree/listDocsByPath` |
| 创建笔记本 | `/api/notebook/createNotebook` |
| 创建文档 | `/api/filetree/createDocWithMd` |
| 追加块 | `/api/block/appendBlock` |
| 更新块 | `/api/block/updateBlock` |
| 重命名文档 | `/api/filetree/renameDocByID` |
| 设置属性 | `/api/attr/setBlockAttrs` |
| 删除块 | `/api/block/deleteBlock` |
| 删除文档 | `/api/filetree/removeDocByID` |
| 导出为 Markdown | `/api/export/exportMdContent` |

## 常见操作

### 搜索（全文）

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/search/fullTextSearchBlock" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "meeting notes", "page": 0}' | jq '.data.blocks[:5]'
```

### 搜索（SQL）

直接查询块数据库。仅 SELECT 语句是安全的。

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/query/sql" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"stmt": "SELECT id, content, type, box FROM blocks WHERE content LIKE '\''%keyword%'\'' AND type='\''p'\'' LIMIT 20"}' | jq '.data'
```

有用列：`id`、`parent_id`、`root_id`、`box`（笔记本 ID）、`path`、`content`、`type`、`subtype`、`created`、`updated`。

### 读取块内容

以 Kramdown（类似 Markdown）格式返回块内容。

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/block/getBlockKramdown" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "20210808180117-6v0mkxr"}' | jq '.data.kramdown'
```

### 读取子块

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/block/getChildBlocks" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "20210808180117-6v0mkxr"}' | jq '.data'
```

### 获取人类可读路径

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/filetree/getHPathByID" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "20210808180117-6v0mkxr"}' | jq '.data'
```

### 获取块属性

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/attr/getBlockAttrs" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "20210808180117-6v0mkxr"}' | jq '.data'
```

### 列出笔记本

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/notebook/lsNotebooks" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' | jq '.data.notebooks[] | {id, name, closed}'
```

### 列出笔记本中的文档

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/filetree/listDocsByPath" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"notebook": "NOTEBOOK_ID", "path": "/"}' | jq '.data.files[] | {id, name}'
```

### 创建文档

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/filetree/createDocWithMd" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "notebook": "NOTEBOOK_ID",
    "path": "/Meeting Notes/2026-03-22",
    "markdown": "# Meeting Notes\n\n- Discussed project timeline\n- Assigned tasks"
  }' | jq '.data'
```

### 创建笔记本

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/notebook/createNotebook" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "My New Notebook"}' | jq '.data.notebook.id'
```

### 追加块到文档

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/block/appendBlock" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "parentID": "DOCUMENT_OR_BLOCK_ID",
    "data": "New paragraph added at the end.",
    "dataType": "markdown"
  }' | jq '.data'
```

也可使用：`/api/block/prependBlock`（相同参数，插入开头）和 `/api/block/insertBlock`（使用 `previousID` 而非 `parentID` 在特定块之后插入）。

### 更新块内容

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/block/updateBlock" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "BLOCK_ID",
    "data": "Updated content here.",
    "dataType": "markdown"
  }' | jq '.data'
```

### 重命名文档

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/filetree/renameDocByID" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "DOCUMENT_ID", "title": "New Title"}'
```

### 设置块属性

自定义属性必须以 `custom-` 为前缀：

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/attr/setBlockAttrs" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "BLOCK_ID",
    "attrs": {
      "custom-status": "reviewed",
      "custom-priority": "high"
    }
  }'
```

### 删除块

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/block/deleteBlock" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "BLOCK_ID"}'
```

要删除整个文档：使用 `{"id": "DOC_ID"}` 的 `/api/filetree/removeDocByID`。
要删除笔记本：使用 `{"notebook": "NOTEBOOK_ID"}` 的 `/api/notebook/removeNotebook`。

### 将文档导出为 Markdown

```bash
curl -s -X POST "${SIYUAN_URL:-http://127.0.0.1:6806}/api/export/exportMdContent" \
  -H "Authorization: Token $SIYUAN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "DOCUMENT_ID"}' | jq -r '.data.content'
```

## 块类型

SQL 查询中的常见 `type` 值：

| 类型 | 描述 |
|------|------|
| `d` | 文档（根块） |
| `p` | 段落 |
| `h` | 标题 |
| `l` | 列表 |
| `i` | 列表项 |
| `c` | 代码块 |
| `m` | 数学块 |
| `t` | 表格 |
| `b` | 块引用 |
| `s` | 超级块 |
| `html` | HTML 块 |

## 陷阱

- **所有端点都是 POST** — 甚至只读操作。不要使用 GET。
- **SQL 安全性**：仅使用 SELECT 查询。INSERT/UPDATE/DELETE/DROP 是危险的，不应发送。
- **ID 验证**：ID 匹配模式 `YYYYMMDDHHmmss-xxxxxxx`。拒绝任何其他内容。
- **错误响应**：在处理 `data` 前始终检查 `code != 0`。
- **大文档**：块内容和导出结果可能非常大。在 SQL 中使用 `LIMIT` 并通过 `jq` 管道仅提取您需要的内容。
- **笔记本 ID**：当使用特定笔记本时，首先通过 `lsNotebooks` 获取其 ID。

## 替代方案：MCP 服务器

如果您更喜欢原生集成而非 curl，请安装 SiYuan MCP 服务器：

```yaml
# 在 ~/.kclaw/config.yaml 下的 mcp_servers 中：
mcp_servers:
  siyuan:
    command: npx
    args: ["-y", "@porkll/siyuan-mcp"]
    env:
      SIYUAN_TOKEN: "your_token"
      SIYUAN_URL: "http://127.0.0.1:6806"
```
