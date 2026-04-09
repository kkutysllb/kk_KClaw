---
name: fastmcp
description: 使用FastMCP在Python中构建、测试、检查、安装和部署MCP服务器。当创建新的MCP服务器、将API或数据库包装为MCP工具、暴露资源或提示，或为Claude Code、Cursor或HTTP部署准备FastMCP服务器时使用。
version: 1.0.0
author: KClaw Agent
license: MIT
metadata:
  kclaw:
    tags: [MCP, FastMCP, Python, 工具, 资源, 提示, 部署]
    homepage: https://gofastmcp.com
    related_skills: [native-mcp, mcporter]
prerequisites:
  commands: [python3]
---

# FastMCP

使用FastMCP在Python中构建MCP服务器，在本地验证，将它们安装到MCP客户端，以及部署为HTTP端点。

## 何时使用

在以下任务中使用此技能：

- 在Python中创建新的MCP服务器
- 将API、数据库、CLI或文件处理工作流包装为MCP工具
- 除了工具外还暴露资源或提示
- 在将其接入KClaw或其他客户端之前，使用FastMCP CLI对服务器进行冒烟测试
- 将服务器安装到Claude Code、Claude Desktop、Cursor或类似的MCP客户端
- 准备FastMCP服务器仓库以进行HTTP部署

当服务器已存在且只需要连接到KClaw时使用`native-mcp`。当目标是临时CLI访问现有MCP服务器而不是构建一个时使用`mcporter`。

## 前置要求

首先在工作环境中安装FastMCP：

```bash
pip install fastmcp
fastmcp version
```

对于API模板，如果尚未安装`httpx`，请安装：

```bash
pip install httpx
```

## 包含的文件

### 模板

- `templates/api_wrapper.py` - 带auth header支持的REST API包装器
- `templates/database_server.py` - 只读SQLite查询服务器
- `templates/file_processor.py` - 文本文件检查和搜索服务器

### 脚本

- `scripts/scaffold_fastmcp.py` - 复制起始模板并替换服务器名称占位符

### 参考

- `references/fastmcp-cli.md` - FastMCP CLI工作流、安装目标和部署检查

## 工作流

### 1. 选择最小可行的服务器形态

首先选择最窄的有用表面区域：

- API包装器：从1-3个高价值端点开始，而不是整个API
- 数据库服务器：暴露只读自省和受限查询路径
- 文件处理器：暴露带显式路径参数的确定性操作
- 提示/资源：仅在客户端需要可重用的提示模板或可发现文档时添加

偏好具有好名称、文档字符串和模式的薄服务器，而不是具有模糊工具的大型服务器。

### 2. 从模板搭建

直接复制模板或使用搭建辅助函数：

```bash
python ~/.kclaw/skills/mcp/fastmcp/scripts/scaffold_fastmcp.py \
  --template api_wrapper \
  --name "Acme API" \
  --output ./acme_server.py
```

可用模板：

```bash
python ~/.kclaw/skills/mcp/fastmcp/scripts/scaffold_fastmcp.py --list
```

如果手动复制，用真实服务器名称替换`__SERVER_NAME__`。

### 3. 首先实现工具

在添加资源或提示之前从`@mcp.tool`函数开始。

工具设计规则：

- 给每个工具一个具体的基于动词的名称
- 将文档字符串写作用户面向的工具描述
- 保持参数明确和类型化
- 尽可能返回结构化JSON安全数据
- 尽早验证不安全输入
- 默认首选只读行为

好的工具示例：

- `get_customer`
- `search_tickets`
- `describe_table`
- `summarize_text_file`

弱的工具示例：

- `run`
- `process`
- `do_thing`

### 4. 仅在有帮助时添加资源和提示

当客户端受益于获取稳定的只读内容（如模式、策略文档或生成报告）时添加`@mcp.resource`。

当服务器应该为已知工作流提供可重用的提示模板时添加`@mcp.prompt`。

不要将每个文档都变成提示。偏好：

- 工具用于操作
- 资源用于数据/文档检索
- 提示用于可重用的LLM指令

### 5. 在集成到任何地方之前测试服务器

使用FastMCP CLI进行本地验证：

```bash
fastmcp inspect acme_server.py:mcp
fastmcp list acme_server.py --json
fastmcp call acme_server.py search_resources query=router limit=5 --json
```

对于快速迭代调试，本地运行服务器：

```bash
fastmcp run acme_server.py:mcp
```

要测试HTTP传输：

```bash
fastmcp run acme_server.py:mcp --transport http --host 127.0.0.1 --port 8000
fastmcp list http://127.0.0.1:8000/mcp --json
fastmcp call http://127.0.0.1:8000/mcp search_resources query=router --json
```

在声称服务器工作之前，始终至少运行一个针对每个新工具的真实`fastmcp call`。

### 6. 当本地验证通过时安装到客户端

FastMCP可以将服务器注册到支持的MCP客户端：

```bash
fastmcp install claude-code acme_server.py
fastmcp install claude-desktop acme_server.py
fastmcp install cursor acme_server.py -e .
```

使用`fastmcp discover`检查已配置在机器上的命名MCP服务器。

当目标是KClaw集成时，可以：

- 使用`native-mcp`技能在`~/.kclaw/config.yaml`中配置服务器，或
- 在开发过程中继续使用FastMCP CLI命令，直到接口稳定

### 7. 在本地契约稳定后部署

对于托管托管，Prefect Horizon是FastMCP最直接记录的路径。部署前：

```bash
fastmcp inspect acme_server.py:mcp
```

确保仓库包含：

- 带FastMCP服务器对象的Python文件
- `requirements.txt`或`pyproject.toml`
- 部署所需的任何环境变量文档

对于通用HTTP托管，首先在本地验证HTTP传输，然后部署在任何可以暴露服务器端口的Python兼容平台上。

## 常见模式

### API包装器模式

用于将REST或HTTP API暴露为MCP工具。

推荐第一个切片：

- 一个读取路径
- 一个列表/搜索路径
- 可选的健康检查

实现注意：

- 将auth保持在环境变量中，不要硬编码
- 在一个辅助函数中集中请求逻辑
- 在返回之前简洁地呈现API错误
- 在返回之前标准化不一致的上游有效载荷

从`templates/api_wrapper.py`开始。

### 数据库模式

用于暴露安全查询和检查能力。

推荐第一个切片：

- `list_tables`
- `describe_table`
- 一个受限的读取查询工具

实现注意：

- 默认只读DB访问
- 在早期版本中拒绝非`SELECT` SQL
- 限制行数
- 返回行以及列名

从`templates/database_server.py`开始。

### 文件处理器模式

当服务器需要按需检查或转换文件时使用。

推荐第一个切片：

- 总结文件内容
- 在文件中搜索
- 提取确定性元数据

实现注意：

- 接受显式文件路径
- 检查缺失文件和编码失败
- 限制预览和结果计数
- 除非需要特定外部工具，否则避免外部调用

从`templates/file_processor.py`开始。

## 质量标准

在交付FastMCP服务器之前，验证以下所有内容：

- 服务器干净地导入
- `fastmcp inspect <file.py:mcp>`成功
- `fastmcp list <server spec> --json`成功
- 每个新工具至少有一个真实的`fastmcp call`
- 环境变量有文档
- 工具表面足够小，无需猜测就能理解

## 故障排除

### FastMCP命令缺失

在活动环境中安装包：

```bash
pip install fastmcp
fastmcp version
```

### `fastmcp inspect`失败

检查：

- 文件导入时没有崩溃的副作用
- FastMCP实例在`<file.py:object>`中命名正确
- 模板中的可选依赖已安装

### 工具在Python中工作但通过CLI不工作

运行：

```bash
fastmcp list server.py --json
fastmcp call server.py your_tool_name --json
```

这通常会暴露命名不匹配、缺少必需参数或不可序列化的返回值。

### KClaw无法看到部署的服务器

服务器构建部分可能正确而KClaw配置不正确。加载`native-mcp`技能并在`~/.kclaw/config.yaml`中配置服务器，然后重启KClaw。

## 参考

有关CLI详情、安装目标和部署检查，请阅读`references/fastmcp-cli.md`。
