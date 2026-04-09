---
name: deployment
description: Pinecone生产部署指南，涵盖无服务器与Pod架构、混合搜索、命名空间和元数据过滤。
---

# Pinecone部署指南

Pinecone的生产部署模式。

## 无服务器与Pod架构

### 无服务器（推荐）

```python
from pinecone import Pinecone, ServerlessSpec

pc = Pinecone(api_key="your-key")

# 创建无服务器索引
pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=ServerlessSpec(
        cloud="aws",  # 或 "gcp", "azure"
        region="us-east-1"
    )
)
```

**优势：**
- 自动扩展
- 按使用量付费
- 无需基础设施管理
- 成本效益高，适合可变负载

**适用场景：**
- 流量波动
- 注重成本优化
- 不需要一致延迟

### Pod架构

```python
from pinecone import PodSpec

pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=PodSpec(
        environment="us-east1-gcp",
        pod_type="p1.x1",  # 或 p1.x2, p1.x4, p1.x8
        pods=2,  # Pod数量
        replicas=2  # 高可用性
    )
)
```

**优势：**
- 一致性能
- 可预测延迟
- 更高吞吐量
- 专用资源

**适用场景：**
- 生产工作负载
- 需要一致的p95延迟
- 需要高吞吐量

## 混合搜索

### 稠密 + 稀疏向量

```python
# 同时插入稠密和稀疏向量
index.upsert(vectors=[
    {
        "id": "doc1",
        "values": [0.1, 0.2, ...],  # 稠密（语义）
        "sparse_values": {
            "indices": [10, 45, 123],  # 词元ID
            "values": [0.5, 0.3, 0.8]   # TF-IDF/BM25分数
        },
        "metadata": {"text": "..."}
    }
])

# 混合查询
results = index.query(
    vector=[0.1, 0.2, ...],  # 稠密查询
    sparse_vector={
        "indices": [10, 45],
        "values": [0.5, 0.3]
    },
    top_k=10,
    alpha=0.5  # 0=仅稀疏, 1=仅稠密, 0.5=平衡
)
```

**优势：**
- 兼两家之长
- 语义 + 关键词匹配
- 比单独使用任何一种更好的召回率

## 用于多租户的命名空间

```python
# 按用户/租户隔离数据
index.upsert(
    vectors=[{"id": "doc1", "values": [...]}],
    namespace="user-123"
)

# 查询特定命名空间
results = index.query(
    vector=[...],
    namespace="user-123",
    top_k=5
)

# 列出命名空间
stats = index.describe_index_stats()
print(stats['namespaces'])
```

**使用场景：**
- 多租户SaaS
- 用户特定数据隔离
- A/B测试（生产/预发布命名空间）

## 元数据过滤

### 精确匹配

```python
results = index.query(
    vector=[...],
    filter={"category": "教程"},
    top_k=5
)
```

### 范围查询

```python
results = index.query(
    vector=[...],
    filter={"price": {"$gte": 100, "$lte": 500}},
    top_k=5
)
```

### 复杂过滤器

```python
results = index.query(
    vector=[...],
    filter={
        "$and": [
            {"category": {"$in": ["教程", "指南"]}},
            {"difficulty": {"$lte": 3}},
            {"published": {"$gte": "2024-01-01"}}
        ]
    },
    top_k=5
)
```

## 最佳实践

1. **开发环境使用无服务器** - 成本效益高
2. **生产环境切换到Pod** - 一致性能
3. **使用命名空间** - 多租户隔离
4. **战略性添加元数据** - 启用过滤
5. **使用混合搜索** - 更高质量
6. **批量upsert** - 每批100-200个向量
7. **监控使用量** - 查看Pinecone仪表板
8. **设置警报** - 使用量/成本阈值
9. **定期备份** - 导出重要数据
10. **测试过滤器** - 验证性能

## 资源

- **文档**: https://docs.pinecone.io
- **控制台**: https://app.pinecone.io
