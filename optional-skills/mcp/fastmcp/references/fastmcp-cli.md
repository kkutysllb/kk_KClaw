---
name: fastmcp-cli
description: FastMCP CLI工作流参考，用于构建、测试、检查、安装和部署MCP服务器。
---

# FastMCP CLI参考

当任务需要精确的FastMCP CLI工作流而不是`SKILL.md`中的更高层指导时使用此文件。

## 安装和验证

```bash
pip install fastmcp
fastmcp version
```

FastMCP将`pip install fastmcp`和`fastmcp version`作为基线安装和验证路径。

## 运行服务器

从Python文件运行服务器对象：

```bash
fastmcp run server.py:mcp
```

通过HTTP运行相同服务器：

```bash
fastmcp run server.py:mcp --transport http --host 127.0.0.1 --port 8000
```

## 检查服务器

检查FastMCP将暴露的内容：

```bash
fastmcp inspect server.py:mcp
```

这也是FastMCP建议在部署到Prefect Horizon之前进行的检查。

## 列出和调用工具

从Python文件列出工具：

```bash
fastmcp list server.py --json
```

从HTTP端点列出工具：

```bash
fastmcp list http://127.0.0.1:8000/mcp --json
```

使用键值参数调用工具：

```bash
fastmcp call server.py search_resources query=router limit=5 --json
```

使用完整JSON输入负载调用工具：

```bash
fastmcp call server.py create_item '{"name": "Widget", "tags": ["sale"]}' --json
```

## 发现命名的MCP服务器

查找本地MCP感知工具中已配置的命名服务器：

```bash
fastmcp discover
```

FastMCP记录了针对Claude Desktop、Claude Code、Cursor、Goose和`./mcp.json`的基于名称的解析。

## 安装到MCP客户端

向常见客户端注册服务器：

```bash
fastmcp install claude-code server.py
fastmcp install claude-desktop server.py
fastmcp install cursor server.py -e .
```

FastMCP指出客户端安装会在隔离环境中运行，因此需要时使用`--with`、`--env-file`或可编辑安装等标志明确声明依赖。

## 部署检查

### Prefect Horizon

推送到Horizon之前：

```bash
fastmcp inspect server.py:mcp
```

FastMCP的Horizon文档期望：

- 一个GitHub仓库
- 包含FastMCP服务器对象的Python文件
- 在`requirements.txt`或`pyproject.toml`中声明的依赖
- 如`main.py:mcp`的入口点

### 通用HTTP托管

发布到任何其他主机之前：

1. 使用HTTP传输在本地启动服务器。
2. 针对本地`/mcp` URL验证`fastmcp list`。
3. 验证至少一个`fastmcp call`。
4. 记录所需的环境变量。
