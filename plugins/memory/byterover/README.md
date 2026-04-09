# ByteRover 记忆提供商

通过 `brv` CLI 实现持久化记忆 — 具有分层检索（模糊文本 → LLM 驱动搜索）的分层知识树。

## 需求

安装 ByteRover CLI：
```bash
curl -fsSL https://byterover.dev/install.sh | sh
# 或
npm install -g byterover-cli
```

## 设置

```bash
kclaw memory setup    # 选择 "byterover"
```

或手动：
```bash
kclaw config set memory.provider byterover
# 可选的云同步：
echo "BRV_API_KEY=your-key" >> ~/.kclaw/.env
```

## 配置

| 环境变量 | 必需 | 描述 |
|---------|------|------|
| `BRV_API_KEY` | 否 | 云同步密钥（可选，默认为本地优先）|

工作目录：`$KCLAW_HOME/byterover/`（profile 范围）。

## 工具

| 工具 | 描述 |
|------|------|
| `brv_query` | 搜索知识树 |
| `brv_curate` | 存储事实、决策、模式 |
| `brv_status` | CLI 版本、树统计、同步状态 |
