# Mem0 记忆提供商

具有语义搜索、重排和自动去重的服务器端 LLM 事实提取。

## 需求

- `pip install mem0ai`
- 从 [app.mem0.ai](https://app.mem0.ai) 获取 Mem0 API 密钥

## 设置

```bash
kclaw memory setup    # 选择 "mem0"
```

或手动：
```bash
kclaw config set memory.provider mem0
echo "MEM0_API_KEY=your-key" >> ~/.kclaw/.env
```

## 配置

配置文件：`$KCLAW_HOME/mem0.json`

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `user_id` | `kclaw-user` | Mem0 上的用户标识符 |
| `agent_id` | `kclaw` | 代理标识符 |
| `rerank` | `true` | 启用召回重排 |

## 工具

| 工具 | 描述 |
|------|------|
| `mem0_profile` | 存储的关于用户的所有记忆 |
| `mem0_search` | 带可选重排的语义搜索 |
| `mem0_conclude` | 原样存储事实（无 LLM 提取）|
