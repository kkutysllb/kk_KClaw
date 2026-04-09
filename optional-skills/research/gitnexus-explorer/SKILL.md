---
name: gitnexus-explorer
description: 使用 GitNexus 为代码库建立索引，并通过 Web UI + Cloudflare 隧道提供交互式知识图谱服务。
version: 1.0.0
author: KClaw Agent + Teknium
license: MIT
metadata:
  kclaw:
    tags: [gitnexus, code-intelligence, knowledge-graph, visualization]
    related_skills: [native-mcp, codebase-inspection]
---

# GitNexus Explorer

将任何代码库索引为知识图谱，并提供交互式 Web UI 以探索符号、调用链、集群和执行流程。通过 Cloudflare 隧道实现远程访问。

## 何时使用

- 用户想要直观地探索代码库的架构
- 用户请求仓库的知识图谱 / 依赖图
- 用户想要与他人分享交互式代码库浏览器

## 先决条件

- **Node.js** (v18+) — GitNexus 和代理所需
- **git** — 仓库必须具有 `.git` 目录
- **cloudflared** — 用于隧道（如果缺失则自动安装到 ~/.local/bin）

## 大小警告

Web UI 在浏览器中渲染所有节点。约 5,000 个文件以下的仓库效果良好。大型仓库（30k+ 节点）会变慢或使浏览器标签崩溃。CLI/MCP 工具在任何规模下都能工作 — 只有 Web 可视化有此限制。

## 步骤

### 1. 克隆并构建 GitNexus（一次性设置）

```bash
GITNEXUS_DIR="${GITNEXUS_DIR:-$HOME/.local/share/gitnexus}"

if [ ! -d "$GITNEXUS_DIR/gitnexus-web/dist" ]; then
  git clone https://github.com/abhigyanpatwari/GitNexus.git "$GITNEXUS_DIR"
  cd "$GITNEXUS_DIR/gitnexus-shared" && npm install && npm run build
  cd "$GITNEXUS_DIR/gitnexus-web" && npm install
fi
```

### 2. 修补 Web UI 以实现远程访问

Web UI 默认为 API 调用使用 `localhost:4747`。修补它以使用同源，这样可以通过隧道/代理工作：

**文件：`$GITNEXUS_DIR/gitnexus-web/src/config/ui-constants.ts`**
更改：
```typescript
export const DEFAULT_BACKEND_URL = 'http://localhost:4747';
```
为：
```typescript
export const DEFAULT_BACKEND_URL = typeof window !== 'undefined' && window.location.hostname !== 'localhost' ? window.location.origin : 'http://localhost:4747';
```

**文件：`$GITNEXUS_DIR/gitnexus-web/vite.config.ts`**
在 `server: { }` 块内添加 `allowedHosts: true`（仅在使用开发模式而不是生产构建时才需要）：
```typescript
server: {
    allowedHosts: true,
    // ... existing config
},
```

然后构建生产捆绑包：
```bash
cd "$GITNEXUS_DIR/gitnexus-web" && npx vite build
```

### 3. 为目标仓库建立索引

```bash
cd /path/to/target-repo
npx gitnexus analyze --skip-agents-md
rm -rf .claude/    # 移除 Claude Code 特定的人工制品
```

添加 `--embeddings` 以进行语义搜索（较慢 — 分钟而不是秒）。

索引位于仓库内的 `.gitnexus/` 中（自动被 gitignore）。

### 4. 创建代理脚本

将其写入文件（例如 `$GITNEXUS_DIR/proxy.mjs`）。它提供生产 Web UI 并将 `/api/*` 代理到 GitNexus 后端 — 同源、无 CORS 问题、无 sudo、无 nginx。

```javascript
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const API_PORT = parseInt(process.env.API_PORT || '4747');
const DIST_DIR = process.argv[2] || './dist';
const PORT = parseInt(process.argv[3] || '8888');

const MIME = {
  '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff',
  '.wasm': 'application/wasm',
};

function proxyToApi(req, res) {
  const opts = {
    hostname: '127.0.0.1', port: API_PORT,
    path: req.url, method: req.method, headers: req.headers,
  };
  const proxy = http.request(opts, (upstream) => {
    res.writeHead(upstream.statusCode, upstream.headers);
    upstream.pipe(res, { end: true });
  });
  proxy.on('error', () => { res.writeHead(502); res.end('Backend unavailable'); });
  req.pipe(proxy, { end: true });
}

function serveStatic(req, res) {
  let filePath = path.join(DIST_DIR, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
  if (!fs.existsSync(filePath)) filePath = path.join(DIST_DIR, 'index.html');
  const ext = path.extname(filePath);
  const mime = MIME[ext] || 'application/octet-stream';
  try {
    const data = fs.readFileSync(filePath);
    res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'public, max-age=3600' });
    res.end(data);
  } catch { res.writeHead(404); res.end('Not found'); }
}

http.createServer((req, res) => {
  if (req.url.startsWith('/api')) proxyToApi(req, res);
  else serveStatic(req, res);
}).listen(PORT, () => console.log(`GitNexus proxy on http://localhost:${PORT}`));
```

### 5. 启动服务

```bash
# 终端 1：GitNexus 后端 API
npx gitnexus serve &

# 终端 2：代理（Web UI + API 在一个端口上）
node "$GITNEXUS_DIR/proxy.mjs" "$GITNEXUS_DIR/gitnexus-web/dist" 8888 &
```

验证：`curl -s http://localhost:8888/api/repos` 应返回索引的仓库。

### 6. 使用 Cloudflare 隧道（可选 — 远程访问）

```bash
# 如需要则安装 cloudflared（无需 sudo）
if ! command -v cloudflared &>/dev/null; then
  mkdir -p ~/.local/bin
  curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o ~/.local/bin/cloudflared
  chmod +x ~/.local/bin/cloudflared
  export PATH="$HOME/.local/bin:$PATH"
fi

# 启动隧道（--config /dev/null 避免与现有命名隧道配置冲突）
cloudflared tunnel --config /dev/null --url http://localhost:8888 --no-autoupdate --protocol http2
```

隧道 URL（例如 `https://random-words.trycloudflare.com`）打印到 stderr。
分享它 — 任何有链接的人都可以探索图谱。

### 7. 清理

```bash
# 停止服务
pkill -f "gitnexus serve"
pkill -f "proxy.mjs"
pkill -f cloudflared

# 从目标仓库移除索引
cd /path/to/target-repo
npx gitnexus clean
rm -rf .claude/
```

## 陷阱

- **如果用户有现有的命名隧道配置在 `~/.cloudflared/config.yml`，则 cloudflared 必须使用 `--config /dev/null`**。否则配置中的 catch-all 入口规则会为所有快速隧道请求返回 404。

- **生产构建是隧道传输的强制要求。** Vite 开发服务器默认阻止非本地主机（`allowedHosts`）。生产构建 + Node 代理完全避免了这个问题。

- **Web UI 不会创建 `.claude/` 或 `CLAUDE.md`。** 这些是由 `npx gitnexus analyze` 创建的。使用 `--skip-agents-md` 抑制 markdown 文件，然后 `rm -rf .claude/` 删除其余。这些是 kclaw 用户不需要的 Claude Code 集成。

- **浏览器内存限制。** Web UI 将整个图谱加载到浏览器内存中。5k+ 文件的仓库可能会变慢。30k+ 文件可能会使标签崩溃。

- **嵌入是可选的。** `--embeddings` 启用语义搜索，但在大型仓库上需要数分钟。对于快速探索可以跳过；如果您想通过 AI 聊天面板进行自然语言查询，则添加它。

- **多个仓库。** `gitnexus serve` 提供所有索引的仓库。索引多个仓库，一次启动 serve，Web UI 让您可以在它们之间切换。
