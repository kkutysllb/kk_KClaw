# RetainDB 记忆提供商

具有混合搜索（向量 + BM25 + 重排）和 7 种记忆类型的云端记忆 API。

## 需求

- RetainDB 账户（$20/月）来自 [retaindb.com](https://www.retaindb.com)
- `pip install requests`

## 设置

```bash
kclaw memory setup    # 选择 "retaindb"
```

或手动：
```bash
kclaw config set memory.provider retaindb
echo "RETAINDB_API_KEY=your-key" >> ~/.kclaw/.env
```

## 配置

所有配置通过 `.env` 中的环境变量：

| 环境变量 | 默认值 | 描述 |
|---------|--------|------|
| `RETAINDB_API_KEY` | （必需）| API 密钥 |
| `RETAINDB_BASE_URL` | `https://api.retaindb.com` | API 端点 |
| `RETAINDB_PROJECT` | auto (profile-scoped) | 项目标识符 |

## 工具

| 工具 | 描述 |
|------|------|
| `retaindb_profile` | 用户的稳定 profile |
| `retaindb_search` | 语义搜索 |
| `retaindb_context` | 任务相关上下文 |
| `retaindb_remember` | 存储带有类型和重要性的事实 |
| `retaindb_forget` | 按 ID 删除记忆 |
