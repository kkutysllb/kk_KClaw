# Holographic 记忆提供商

具有 FTS5 搜索、信任评分、实体解析和基于 HRR 组合检索的本地 SQLite 事实存储。

## 需求

无 — 使用 SQLite（始终可用）。NumPy 可选用于 HRR 代数。

## 设置

```bash
kclaw memory setup    # 选择 "holographic"
```

或手动：
```bash
kclaw config set memory.provider holographic
```

## 配置

在 `config.yaml` 下的 `plugins.kclaw-memory-store` 中配置：

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `db_path` | `$KCLAW_HOME/memory_store.db` | SQLite 数据库路径 |
| `auto_extract` | `false` | 在会话结束时自动提取事实 |
| `default_trust` | `0.5` | 新事实的默认信任评分 |
| `hrr_dim` | `1024` | HRR 向量维度 |

## 工具

| 工具 | 描述 |
|------|------|
| `fact_store` | 9 种操作：添加、搜索、探测、相关、推理、矛盾、更新、移除、列表 |
| `fact_feedback` | 将事实评为有用/无用（训练信任评分）|
