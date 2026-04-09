---
name: qdrant-vector-search
description: 用于RAG和语义搜索的高性能向量相似度搜索引擎。当需要构建低延迟的生成式检索系统、带有过滤功能的混合搜索、或可扩展的向量存储（Rust驱动性能）时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [qdrant-client>=1.12.0]
metadata:
  kclaw:
    tags: [RAG, 向量搜索, Qdrant, 语义搜索, 嵌入, 相似度搜索, HNSW, 生产环境, 分布式]

---

# Qdrant - 向量相似度搜索引擎

用于生产环境RAG和语义搜索的高性能向量数据库，采用Rust编写。

## 何时使用Qdrant

**在以下情况下使用Qdrant：**
- 构建需要低延迟的生产环境RAG系统
- 需要混合搜索（向量 + 元数据过滤）
- 需要水平扩展（分片/复制）
- 想要本地部署并完全控制数据
- 需要每条记录存储多个向量（稠密 + 稀疏）
- 构建实时推荐系统

**关键特性：**
- **Rust驱动**：内存安全，高性能
- **丰富过滤**：搜索时可按任意payload字段过滤
- **多向量支持**：每条记录支持稠密、稀疏、多稠密向量
- **量化**：标量积、乘积、二进制量化以节省内存
- **分布式**：Raft共识、分片、复制
- **REST + gRPC**：两个API，功能完整对等

**使用替代方案：**
- **Chroma**：更简单的设置，嵌入式用例
- **FAISS**：最大原始速度，研究/批处理
- **Pinecone**：全托管，零运维优先
- **Weaviate**：GraphQL偏好，内置向量化器

## 快速开始

### 安装

```bash
# Python客户端
pip install qdrant-client

# Docker（推荐用于开发）
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant

# Docker持久化存储
docker run -p 6333:6333 -p 6334:6334 \
    -v $(pwd)/qdrant_storage:/qdrant/storage \
    qdrant/qdrant
```

### 基础用法

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

# 连接到Qdrant
client = QdrantClient(host="localhost", port=6333)

# 创建集合
client.create_collection(
    collection_name="documents",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
)

# 插入带payload的向量
client.upsert(
    collection_name="documents",
    points=[
        PointStruct(
            id=1,
            vector=[0.1, 0.2, ...],  # 384维向量
            payload={"title": "文档1", "category": "技术"}
        ),
        PointStruct(
            id=2,
            vector=[0.3, 0.4, ...],
            payload={"title": "文档2", "category": "科学"}
        )
    ]
)

# 带过滤的搜索
results = client.search(
    collection_name="documents",
    query_vector=[0.15, 0.25, ...],
    query_filter={
        "must": [{"key": "category", "match": {"value": "技术"}}]
    },
    limit=10
)

for point in results:
    print(f"ID: {point.id}, 分数: {point.score}, Payload: {point.payload}")
```

## 核心概念

### 点 - 基本数据单元

```python
from qdrant_client.models import PointStruct

# 点 = ID + 向量(s) + Payload
point = PointStruct(
    id=123,                              # 整数或UUID字符串
    vector=[0.1, 0.2, 0.3, ...],        # 稠密向量
    payload={                            # 任意JSON元数据
        "title": "文档标题",
        "category": "技术",
        "timestamp": 1699900000,
        "tags": ["python", "ml"]
    }
)

# 批量upsert（推荐）
client.upsert(
    collection_name="documents",
    points=[point1, point2, point3],
    wait=True  # 等待索引完成
)
```

### 集合 - 向量容器

```python
from qdrant_client.models import VectorParams, Distance, HnswConfigDiff

# 创建带HNSW配置的集合
client.create_collection(
    collection_name="documents",
    vectors_config=VectorParams(
        size=384,                        # 向量维度
        distance=Distance.COSINE         # COSINE, EUCLID, DOT, MANHATTAN
    ),
    hnsw_config=HnswConfigDiff(
        m=16,                            # 每个节点的连接数（默认16）
        ef_construct=100,                # 构建时精度（默认100）
        full_scan_threshold=10000        # 低于此值切换到暴力搜索
    ),
    on_disk_payload=True                 # 将payload存储在磁盘上
)

# 获取集合信息
info = client.get_collection("documents")
print(f"点数: {info.points_count}, 向量数: {info.vectors_count}")
```

### 距离度量

| 度量 | 用例 | 范围 |
|--------|----------|------|
| `COSINE` | 文本嵌入，归一化向量 | 0 到 2 |
| `EUCLID` | 空间数据，图像特征 | 0 到 ∞ |
| `DOT` | 推荐，未归一化 | -∞ 到 ∞ |
| `MANHATTAN` | 稀疏特征，离散数据 | 0 到 ∞ |

## 搜索操作

### 基础搜索

```python
# 简单的最近邻搜索
results = client.search(
    collection_name="documents",
    query_vector=[0.1, 0.2, ...],
    limit=10,
    with_payload=True,
    with_vectors=False  # 不返回向量（更快）
)
```

### 过滤搜索

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range

# 复杂过滤
results = client.search(
    collection_name="documents",
    query_vector=query_embedding,
    query_filter=Filter(
        must=[
            FieldCondition(key="category", match=MatchValue(value="技术")),
            FieldCondition(key="timestamp", range=Range(gte=1699000000))
        ],
        must_not=[
            FieldCondition(key="status", match=MatchValue(value="已归档"))
        ]
    ),
    limit=10
)

# 简写过滤语法
results = client.search(
    collection_name="documents",
    query_vector=query_embedding,
    query_filter={
        "must": [
            {"key": "category", "match": {"value": "技术"}},
            {"key": "price", "range": {"gte": 10, "lte": 100}}
        ]
    },
    limit=10
)
```

### 批量搜索

```python
from qdrant_client.models import SearchRequest

# 单次请求多个查询
results = client.search_batch(
    collection_name="documents",
    requests=[
        SearchRequest(vector=[0.1, ...], limit=5),
        SearchRequest(vector=[0.2, ...], limit=5, filter={"must": [...]}),
        SearchRequest(vector=[0.3, ...], limit=10)
    ]
)
```

## RAG集成

### 配合sentence-transformers使用

```python
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

# 初始化
encoder = SentenceTransformer("all-MiniLM-L6-v2")
client = QdrantClient(host="localhost", port=6333)

# 创建集合
client.create_collection(
    collection_name="knowledge_base",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
)

# 索引文档
documents = [
    {"id": 1, "text": "Python是一种编程语言", "source": "wiki"},
    {"id": 2, "text": "机器学习使用算法", "source": "textbook"},
]

points = [
    PointStruct(
        id=doc["id"],
        vector=encoder.encode(doc["text"]).tolist(),
        payload={"text": doc["text"], "source": doc["source"]}
    )
    for doc in documents
]
client.upsert(collection_name="knowledge_base", points=points)

# RAG检索
def retrieve(query: str, top_k: int = 5) -> list[dict]:
    query_vector = encoder.encode(query).tolist()
    results = client.search(
        collection_name="knowledge_base",
        query_vector=query_vector,
        limit=top_k
    )
    return [{"text": r.payload["text"], "score": r.score} for r in results]

# 在RAG流水线中使用
context = retrieve("什么是Python？")
prompt = f"上下文: {context}\n\n问题: 什么是Python?"
```

### 配合LangChain使用

```python
from langchain_community.vectorstores import Qdrant
from langchain_community.embeddings import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = Qdrant.from_documents(documents, embeddings, url="http://localhost:6333", collection_name="docs")
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
```

### 配合LlamaIndex使用

```python
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import VectorStoreIndex, StorageContext

vector_store = QdrantVectorStore(client=client, collection_name="llama_docs")
storage_context = StorageContext.from_defaults(vector_store=vector_store)
index = VectorStoreIndex.from_documents(documents, storage_context=storage_context)
query_engine = index.as_query_engine()
```

## 多向量支持

### 命名向量（不同嵌入模型）

```python
from qdrant_client.models import VectorParams, Distance

# 带多向量类型的集合
client.create_collection(
    collection_name="hybrid_search",
    vectors_config={
        "dense": VectorParams(size=384, distance=Distance.COSINE),
        "sparse": VectorParams(size=30000, distance=Distance.DOT)
    }
)

# 插入命名向量
client.upsert(
    collection_name="hybrid_search",
    points=[
        PointStruct(
            id=1,
            vector={
                "dense": dense_embedding,
                "sparse": sparse_embedding
            },
            payload={"text": "文档文本"}
        )
    ]
)

# 搜索特定向量
results = client.search(
    collection_name="hybrid_search",
    query_vector=("dense", query_dense),  # 指定向量类型
    limit=10
)
```

### 稀疏向量（BM25, SPLADE）

```python
from qdrant_client.models import SparseVectorParams, SparseIndexParams, SparseVector

# 带稀疏向量的集合
client.create_collection(
    collection_name="sparse_search",
    vectors_config={},
    sparse_vectors_config={"text": SparseVectorParams(index=SparseIndexParams(on_disk=False))}
)

# 插入稀疏向量
client.upsert(
    collection_name="sparse_search",
    points=[PointStruct(id=1, vector={"text": SparseVector(indices=[1, 5, 100], values=[0.5, 0.8, 0.2])}, payload={"text": "文档"})]
)
```

## 量化（内存优化）

```python
from qdrant_client.models import ScalarQuantization, ScalarQuantizationConfig, ScalarType

# 标量量化（4倍内存减少）
client.create_collection(
    collection_name="quantized",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            quantile=0.99,        # 裁剪异常值
            always_ram=True      # 保持量化结果在RAM中
        )
    )
)

# 重排序搜索
results = client.search(
    collection_name="quantized",
    query_vector=query,
    search_params={"quantization": {"rescore": True}},  # 对顶部结果重排序
    limit=10
)
```

## Payload索引

```python
from qdrant_client.models import PayloadSchemaType

# 为过滤字段创建payload索引以加快过滤速度
client.create_payload_index(
    collection_name="documents",
    field_name="category",
    field_schema=PayloadSchemaType.KEYWORD
)

client.create_payload_index(
    collection_name="documents",
    field_name="timestamp",
    field_schema=PayloadSchemaType.INTEGER
)

# 索引类型：KEYWORD, INTEGER, FLOAT, GEO, TEXT（全文）, BOOL
```

## 生产环境部署

### Qdrant Cloud

```python
from qdrant_client import QdrantClient

# 连接到Qdrant Cloud
client = QdrantClient(
    url="https://your-cluster.cloud.qdrant.io",
    api_key="your-api-key"
)
```

### 性能调优

```python
# 针对搜索速度优化（更高召回率）
client.update_collection(
    collection_name="documents",
    hnsw_config=HnswConfigDiff(ef_construct=200, m=32)
)

# 针对索引速度优化（批量加载）
client.update_collection(
    collection_name="documents",
    optimizer_config={"indexing_threshold": 20000}
)
```

## 最佳实践

1. **批量操作** - 使用批量upsert/search以提高效率
2. **Payload索引** - 为过滤中使用的字段建立索引
3. **量化** - 对大型集合（>1M向量）启用
4. **分片** - 对超大型集合（>10M向量）使用
5. **磁盘存储** - 对大型payload启用`on_disk_payload`
6. **连接池** - 重用客户端实例

## 常见问题

**带过滤的搜索慢：**
```python
# 为过滤字段创建payload索引
client.create_payload_index(
    collection_name="docs",
    field_name="category",
    field_schema=PayloadSchemaType.KEYWORD
)
```

**内存不足：**
```python
# 启用量化和磁盘存储
client.create_collection(
    collection_name="large_collection",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=ScalarQuantization(...),
    on_disk_payload=True
)
```

**连接问题：**
```python
# 使用超时和重试
client = QdrantClient(
    host="localhost",
    port=6333,
    timeout=30,
    prefer_grpc=True  # gRPC性能更好
)
```

## 参考

- **[高级用法](references/advanced-usage.md)** - 分布式模式、混合搜索、推荐
- **[故障排除](references/troubleshooting.md)** - 常见问题、调试、性能调优

## 资源

- **GitHub**: https://github.com/qdrant/qdrant (22k+ stars)
- **文档**: https://qdrant.tech/documentation/
- **Python客户端**: https://github.com/qdrant/qdrant-client
- **Cloud**: https://cloud.qdrant.io
- **版本**: 1.12.0+
- **许可证**: Apache 2.0
