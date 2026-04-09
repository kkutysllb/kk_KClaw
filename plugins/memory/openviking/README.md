# OpenViking 记忆提供商

火山引擎（字节跳动）的上下文数据库，具有文件系统风格的知识层次结构、分层检索和自动记忆提取。

## 需求

- `pip install openviking`
- 运行 OpenViking 服务器（`openviking-server`）
- 在 `~/.openviking/ov.conf` 中配置嵌入 + VLM 模型

## 设置

```bash
kclaw memory setup    # 选择 "openviking"
```

或手动：
```bash
kclaw config set memory.provider openviking
echo "OPENVIKING_ENDPOINT=http://localhost:1933" >> ~/.kclaw/.env
```

## 配置

所有配置通过 `.env` 中的环境变量：

| 环境变量 | 默认值 | 描述 |
|---------|--------|------|
| `OPENVIKING_ENDPOINT` | `http://127.0.0.1:1933` | 服务器 URL |
| `OPENVIKING_API_KEY` | (无) | API 密钥（可选）|

## 工具

| 工具 | 描述 |
|------|------|
| `viking_search` | 快速/深度/自动模式的语义搜索 |
| `viking_read` | 读取 viking:// URI 处的内容（抽象/概览/完整）|
| `viking_browse` | 文件系统风格导航（列表/树/统计）|
| `viking_remember` | 存储一个事实，在会话提交时提取 |
| `viking_add_resource` | 将 URL/文档摄取到知识库中 |
