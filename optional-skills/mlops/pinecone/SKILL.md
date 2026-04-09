---
name: pinecone
description: 用于生产环境AI应用的托管向量数据库。全托管、自动扩展，支持混合搜索（稠密+稀疏）、元数据过滤和命名空间。低延迟（<100ms p95）。用于生产环境RAG、推荐系统或大规模语义搜索。最適合无服务器、托管基础设施。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [pinecone-client]
metadata:
  kclaw:
    tags: [RAG, Pinecone, 向量数据库, 托管服务, 无服务器, 混合搜索, 生产环境, 自动扩展, 低延迟, 推荐系统]

---

# Pinecone - 托管向量数据库

用于生产环境AI应用的向量数据库。

## 何时使用Pinecone

**在以下情况下使用：**
- 需要托管的、无服务器向量数据库
- 生产环境RAG应用
- 需要自动扩展
- 低延迟至关重要（<100ms）
- 不想管理基础设施
- 需要混合搜索（稠密+稀疏向量）

**指标：**
- 全托管SaaS
- 自动扩展到数十亿向量
- **p95延迟<100ms**
- 99.9%正常运行时间SLA

**使用替代方案：**
- **Chroma**：自托管、开源
- **FAISS**：离线、纯相似度搜索
- **Weaviate**：自托管、功能更多

## 快速开始

### 安装

```bash
pip install pinecone-client
```

### 基础用法

```python
from pinecone import Pinecone, ServerlessSpec

# 初始化
pc = Pinecone(api_key="your-api-key")

# 创建索引
pc.create_index(
    name="my-index",
    dimension=1536,  # 必须匹配嵌入维度
    metric="cosine",  # 或 "euclidean", "dotproduct"
    spec=ServerlessSpec(cloud="aws", region="us-east-1")
)

# 连接到索引
index = pc.Index("my-index")

# Upsert向量
index.upsert(vectors=[
    {"id": "vec1", "values": [0.1, 0.2, ...], "metadata": {"category": "A"}},
    {"id": "vec2", "values": [0.3, 0.4, ...], "metadata": {"category": "B"}}
])

# 查询
results = index.query(
    vector=[0.1, 0.2, ...],
    top_k=5,
    include_metadata=True
)

print(results["matches"])
```

## 核心操作

### 创建索引

```python
# 无服务器（推荐）
pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=ServerlessSpec(
        cloud="aws",         # 或 "gcp", "azure"
        region="us-east-1"
    )
)

# 基于Pod（用于一致性能）
from pinecone import PodSpec

pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=PodSpec(
        environment="us-east1-gcp",
        pod_type="p1.x1"
    )
)
```

### Upsert向量

```python
# 单个upsert
index.upsert(vectors=[
    {
        "id": "doc1",
        "values": [0.1, 0.2, ...],  # 1536维
        "metadata": {
            "text": "文档内容",
            "category": "教程",
            "timestamp": "2025-01-01"
        }
    }
])

# 批量upsert（推荐）
vectors = [
    {"id": f"vec{i}", "values": embedding, "metadata": metadata}
    for i, (embedding, metadata) in enumerate(zip(embeddings, metadatas))
]

index.upsert(vectors=vectors, batch_size=100)
```

### 查询向量

```python
# 基础查询
results = index.query(
    vector=[0.1, 0.2, ...],
    top_k=10,
    include_metadata=True,
    include_values=False
)

# 带元数据过滤
results = index.query(
    vector=[0.1, 0.2, ...],
    top_k=5,
    filter={"category": {"$eq": "教程"}}
)

# 命名空间查询
results = index.query(
    vector=[0.1, 0.2, ...],
    top_k=5,
    namespace="production"
)

# 访问结果
for match in results["matches"]:
    print(f"ID: {match['id']}")
    print(f"分数: {match['score']}")
    print(f"元数据: {match['metadata']}")
```

### 元数据过滤

```python
# 精确匹配
filter = {"category": "教程"}

# 比较
filter = {"price": {"$gte": 100}}  # $gt, $gte, $lt, $lte, $ne

# 逻辑运算符
filter = {
    "$and": [
        {"category": "教程"},
        {"difficulty": {"$lte": 3}}
    ]
}  # 也支持: $or

# In运算符
filter = {"tags": {"$in": ["python", "ml"]}}
```

## 命名空间

```python
# 按命名空间分区数据
index.upsert(
    vectors=[{"id": "vec1", "values": [...]}],
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

## 混合搜索（稠密+稀疏）

```python
# Upsert带稀疏向量
index.upsert(vectors=[
    {
        "id": "doc1",
        "values": [0.1, 0.2, ...],  # 稠密向量
        "sparse_values": {
            "indices": [10, 45, 123],  # 词元ID
            "values": [0.5, 0.3, 0.8]   # TF-IDF分数
        },
        "metadata": {"text": "..."}
    }
])

# 混合查询
results = index.query(
    vector=[0.1, 0.2, ...],
    sparse_vector={
        "indices": [10, 45],
        "values": [0.5, 0.3]
    },
    top_k=5,
    alpha=0.5  # 0=稀疏, 1=稠密, 0.5=混合
)
```

## LangChain集成

```python
from langchain_pinecone import PineconeVectorStore
from langchain_openai import OpenAIEmbeddings

# 创建向量存储
vectorstore = PineconeVectorStore.from_documents(
    documents=docs,
    embedding=OpenAIEmbeddings(),
    index_name="my-index"
)

# 查询
results = vectorstore.similarity_search("query", k=5)

# 带元数据过滤
results = vectorstore.similarity_search(
    "query",
    k=5,
    filter={"category": "教程"}
)

# 作为检索器
retriever = vectorstore.as_retriever(search_kwargs={"k": 10})
```

## LlamaIndex集成

```python
from llama_index.vector_stores.pinecone import PineconeVectorStore

# 连接到Pinecone
pc = Pinecone(api_key="your-key")
pinecone_index = pc.Index("my-index")

# 创建向量存储
vector_store = PineconeVectorStore(pinecone_index=pinecone_index)

# 在LlamaIndex中使用
from llama_index.core import StorageContext, VectorStoreIndex

storage_context = StorageContext.from_defaults(vector_store=vector_store)
index = VectorStoreIndex.from_documents(documents, storage_context=storage_context)
```

## 索引管理

```python
# 列出索引
indexes = pc.list_indexes()

# 描述索引
index_info = pc.describe_index("my-index")
print(index_info)

# 获取索引统计
stats = index.describe_index_stats()
print(f"总向量数: {stats['total_vector_count']}")
print(f"命名空间: {stats['namespaces']}")

# 删除索引
pc.delete_index("my-index")
```

## 删除向量

```python
# 按ID删除
index.delete(ids=["vec1", "vec2"])

# 按过滤删除
index.delete(filter={"category": "old"})

# 删除命名空间中的所有内容
index.delete(delete_all=True, namespace="test")

# 删除整个索引
index.delete(delete_all=True)
```

## 最佳实践

1. **使用无服务器** - 自动扩展，性价比高
2. **批量upsert** - 更高效（每批100-200个）
3. **添加元数据** - 启用过滤
4. **使用命名空间** - 按用户/租户隔离数据
5. **监控使用** - 查看Pinecone仪表板
6. **优化过滤** - 为频繁过滤的字段建立索引
7. **先用免费层测试** - 1个索引，100K向量免费
8. **使用混合搜索** - 更高质量
9. **设置合适的维度** - 匹配嵌入模型
10. **定期备份** - 导出重要数据

## 性能

| 操作 | 延迟 | 说明 |
|-----------|---------|-------|
| Upsert | ~50-100ms | 每批次 |
| 查询（p50） | ~50ms | 取决于索引大小 |
| 查询（p95） | ~100ms | SLA目标 |
| 元数据过滤 | ~+10-20ms | 额外开销 |

## 定价（截至2025年）

**无服务器**：
- 每百万读取单位$0.096
- 每百万写入单位$0.06
- 每GB存储/月$0.06

**免费层**：
- 1个无服务器索引
- 100K向量（1536维）
- 非常适合原型开发

## 资源

- **网站**: https://www.pinecone.io
- **文档**: https://docs.pinecone.io
- **控制台**: https://app.pinecone.io
- **定价**: https://www.pinecone.io/pricing
