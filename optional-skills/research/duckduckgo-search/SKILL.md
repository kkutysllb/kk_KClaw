---
name: duckduckgo-search
description: 通过 DuckDuckGo 进行免费网络搜索 — 文本、新闻、图片、视频。无需 API 密钥。首选已安装的 `ddgs` CLI；只有在验证当前运行时可用 `ddgs` 后才使用 Python DDGS 库。
version: 1.3.0
author: gamedevCloudy
license: MIT
metadata:
  kclaw:
    tags: [search, duckduckgo, web-search, free, fallback]
    related_skills: [arxiv]
    fallback_for_toolsets: [web]
---

# DuckDuckGo 搜索

使用 DuckDuckGo 进行免费网络搜索。**无需 API 密钥。**

当 `web_search` 不可用或不适合时首选（例如未设置 `FIRECRAWL_API_KEY` 时）。也可以作为独立搜索路径使用，当特别需要 DuckDuckGo 结果时。

## 检测流程

在选择方法之前检查实际可用的内容：

```bash
# 检查 CLI 可用性
command -v ddgs >/dev/null && echo "DDGS_CLI=installed" || echo "DDGS_CLI=missing"
```

决策树：
1. 如果 `ddgs` CLI 已安装，首选 `terminal` + `ddgs`
2. 如果 `ddgs` CLI 缺失，不要假设 `execute_code` 可以导入 `ddgs`
3. 如果用户特别想要 DuckDuckGo，先在相关环境中安装 `ddgs`
4. 否则回退到内置 web/浏览器工具

重要的运行时注意：
- Terminal 和 `execute_code` 是独立的运行时
- 成功的 shell 安装不能保证 `execute_code` 可以导入 `ddgs`
- 永远不要假设第三方 Python 包已预装在 `execute_code` 中

## 安装

仅在特别需要 DuckDuckGo 搜索且运行时尚未提供时才安装 `ddgs`。

```bash
# Python 包 + CLI 入口点
pip install ddgs

# 验证 CLI
ddgs --help
```

如果工作流依赖于 Python 导入，在使用 `from ddgs import DDGS` 之前验证同一运行时可以导入 `ddgs`。

## 方法 1：CLI 搜索（首选）

当 `ddgs` 命令存在时，通过 `terminal` 使用 `ddgs` 命令。这是首选路径，因为它避免了假设 `execute_code` 沙箱已安装 `ddgs` Python 包。

```bash
# 文本搜索
ddgs text -k "python async programming" -m 5

# 新闻搜索
ddgs news -k "artificial intelligence" -m 5

# 图片搜索
ddgs images -k "landscape photography" -m 10

# 视频搜索
ddgs videos -k "python tutorial" -m 5

# 带区域过滤器
ddgs text -k "best restaurants" -m 5 -r us-en

# 仅最近结果（d=天，w=周，m=月，y=年）
ddgs text -k "latest AI news" -m 5 -t w

# JSON 输出用于解析
ddgs text -k "fastapi tutorial" -m 5 -o json
```

### CLI 标志

| 标志 | 描述 | 示例 |
|------|------|------|
| `-k` | 关键字（查询）— **必需** | `-k "search terms"` |
| `-m` | 最大结果数 | `-m 5` |
| `-r` | 区域 | `-r us-en` |
| `-t` | 时间限制 | `-t w`（周）|
| `-s` | 安全搜索 | `-s off` |
| `-o` | 输出格式 | `-o json` |

## 方法 2：Python API（仅在验证后）

仅在验证 `ddgs` 已安装在 `execute_code` 或其他 Python 运行时后才使用 `DDGS` 类。不要假设 `execute_code` 默认包含第三方包。

安全措辞：
- "在需要时安装或验证包后，在 `execute_code` 中使用 `ddgs`"

避免说：
- "`execute_code` 包含 `ddgs`"
- "DuckDuckGo 搜索在 `execute_code` 中默认可用"

**重要：** `max_results` 必须始终作为**关键字参数**传递 — 位置使用会在所有方法上引发错误。

### 文本搜索

最适合：一般研究、公司、文档。

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.text("python async programming", max_results=5):
        print(r["title"])
        print(r["href"])
        print(r.get("body", "")[:200])
        print()
```

返回：`title`、`href`、`body`

### 新闻搜索

最适合：时事、突发新闻、最新更新。

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.news("AI regulation 2026", max_results=5):
        print(r["date"], "-", r["title"])
        print(r.get("source", ""), "|", r["url"])
        print(r.get("body", "")[:200])
        print()
```

返回：`date`、`title`、`body`、`url`、`image`、`source`

### 图片搜索

最适合：视觉参考、产品图片、图表。

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.images("semiconductor chip", max_results=5):
        print(r["title"])
        print(r["image"])
        print(r.get("thumbnail", ""))
        print(r.get("source", ""))
        print()
```

返回：`title`、`image`、`thumbnail`、`url`、`height`、`width`、`source`

### 视频搜索

最适合：教程、演示、解释器。

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.videos("FastAPI tutorial", max_results=5):
        print(r["title"])
        print(r.get("content", ""))
        print(r.get("duration", ""))
        print(r.get("provider", ""))
        print(r.get("published", ""))
        print()
```

返回：`title`、`content`、`description`、`duration`、`provider`、`published`、`statistics`、`uploader`

### 快速参考

| 方法 | 何时使用 | 关键字段 |
|------|----------|----------|
| `text()` | 一般研究、公司 | title, href, body |
| `news()` | 时事、更新 | date, title, source, body, url |
| `images()` | 视觉、图表 | title, image, thumbnail, url |
| `videos()` | 教程、演示 | title, content, duration, provider |

## 工作流：先搜索后提取

DuckDuckGo 返回标题、URL 和摘要 — 不是完整页面内容。要获取完整页面内容，先搜索，然后用 `web_extract`、浏览器工具或 curl 提取最相关的 URL。

CLI 示例：

```bash
ddgs text -k "fastapi deployment guide" -m 3 -o json
```

Python 示例，仅在验证 `ddgs` 已安装在该运行时中后：

```python
from ddgs import DDGS

with DDGS() as ddgs:
    results = list(ddgs.text("fastapi deployment guide", max_results=3))
    for r in results:
        print(r["title"], "->", r["href"])
```

然后使用 `web_extract` 或其他内容检索工具提取最佳 URL。

## 限制

- **速率限制**：DuckDuckGo 可能在多次快速请求后进行限制。如有需要，请在搜索之间添加短暂延迟。
- **无内容提取**：`ddgs` 返回摘要，而不是完整页面内容。使用 `web_extract`、浏览器工具或 curl 获取完整文章/页面。
- **结果质量**：通常良好，但不如 Firecrawl 的搜索可配置。
- **可用性**：DuckDuckGo 可能会阻止来自某些云 IP 的请求。如果搜索返回空，请尝试不同的关键字或等待几秒钟。
- **字段可变性**：返回字段可能在结果或 `ddgs` 版本之间有所不同。使用 `.get()` 处理可选字段以避免 `KeyError`。
- **独立运行时**：在终端中成功安装 `ddgs` 不能自动意味着 `execute_code` 可以导入它。

## 故障排除

| 问题 | 可能原因 | 怎么做 |
|------|----------|--------|
| `ddgs: command not found` | CLI 未在 shell 环境中安装 | 安装 `ddgs`，或使用内置 web/浏览器工具 |
| `ModuleNotFoundError: No module named 'ddgs'` | Python 运行时没有安装包 | 在该运行时准备好之前不要在那里使用 Python DDGS |
| 搜索返回空 | 临时速率限制或查询不佳 | 等待几秒钟，重试，或调整查询 |
| CLI 可用但 `execute_code` 导入失败 | Terminal 和 `execute_code` 是不同的运行时 | 继续使用 CLI，或单独准备 Python 运行时 |

## 陷阱

- **`max_results` 是仅关键字的**：`ddgs.text("query", 5)` 会引发错误。使用 `ddgs.text("query", max_results=5)`。
- **不要假设 CLI 存在**：使用前检查 `command -v ddgs`。
- **不要假设 `execute_code` 可以导入 `ddgs`**：`from ddgs import DDGS` 可能因 `ModuleNotFoundError` 而失败，除非该运行时单独准备过。
- **包名**：包是 `ddgs`（之前是 `duckduckgo-search`）。用 `pip install ddgs` 安装。
- **不要混淆 `-k` 和 `-m`**（CLI）：`-k` 是关键字，`-m` 是最大结果数。
- **空结果**：如果 `ddgs` 什么也不返回，可能被速率限制。等待几秒钟并重试。

## 已验证

根据 `ddgs==9.11.2` 语义验证的示例。技能指导现在将 CLI 可用性和 Python 导入可用性视为独立问题，因此记录的工作流与实际运行时行为相匹配。
