---
name: advanced-usage
description: Qdrant高级用法指南，涵盖分布式部署、混合搜索、推荐系统和量化策略。
---

# Qdrant高级用法指南

## 分布式部署

### 集群设置

Qdrant使用Raft共识进行分布式协调。

```yaml
# 3节点集群的docker-compose.yml
version: '3.8'
services:
  qdrant-node-1:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
      - "6335:6335"
    volumes:
      - ./node1_storage:/qdrant/storage
    environment:
      - QDRANT__CLUSTER__ENABLED=true
      - QDRANT__CLUSTER__P2P__PORT=6335
      - QDRANT__SERVICE__HTTP_PORT=6333
      - QDRANT__SERVICE__GRPC_PORT=6334

  qdrant-node-2:
    image: qdrant/qdrant:latest
    ports:
      - "6343:6333"
      - "6344:6334"
      - "6345:6335"
    volumes:
      - ./node2_storage:/qdrant/storage
    environment:
      - QDRANT__CLUSTER__ENABLED=true
      - QDRANT__CLUSTER__P2P__PORT=6335
      - QDRANT__CLUSTER__BOOTSTRAP=http://qdrant-node-1:6335
    depends_on:
      - qdrant-node-1

  qdrant-node-3:
    image: qdrant/qdrant:latest
    ports:
      - "6353:6333"
      - "6354:6334"
      - "6355:6335"
    volumes:
      - ./node3_storage:/qdrant/storage
    environment:
      - QDRANT__CLUSTER__ENABLED=true
      - QDRANT__CLUSTER__P2P__PORT=6335
      - QDRANT__CLUSTER__BOOTSTRAP=http://qdrant-node-1:6335
    depends_on:
      - qdrant-node-1
```

### 分片配置

```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, ShardingMethod

client = QdrantClient(host="localhost", port=6333)

# 创建分片集合
client.create_collection(
    collection_name="large_collection",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    shard_number=6,  # 分片数量
    replication_factor=2,  # 每个分片的副本数
    write_consistency_factor=1  # 写入所需确认数
)

# 检查集群状态
cluster_info = client.get_cluster_info()
print(f"节点: {cluster_info.peers}")
print(f"Raft状态: {cluster_info.raft_info}")
```

### 复制和一致性

```python
from qdrant_client.models import WriteOrdering

# 强一致性写入
client.upsert(
    collection_name="critical_data",
    points=points,
    ordering=WriteOrdering.STRONG  # 等待所有副本
)

# 最终一致性（更快）
client.upsert(
    collection_name="logs",
    points=points,
    ordering=WriteOrdering.WEAK  # 主节点确认后返回
)

# 从特定分片读取
results = client.search(
    collection_name="documents",
    query_vector=query,
    consistency="majority"  # 从多数副本读取
)
```

## 混合搜索

### 稠密 + 稀疏向量

结合语义（稠密）和关键词（稀疏）搜索：

```python
from qdrant_client.models import (
    VectorParams, SparseVectorParams, SparseIndexParams,
    Distance, PointStruct, SparseVector, Prefetch, Query
)

# 创建混合集合
client.create_collection(
    collection_name="hybrid",
    vectors_config={
        "dense": VectorParams(size=384, distance=Distance.COSINE)
    },
    sparse_vectors_config={
        "sparse": SparseVectorParams(
            index=SparseIndexParams(on_disk=False)
        )
    }
)

# 插入两种向量类型
def encode_sparse(text: str) -> SparseVector:
    """简单的类BM25稀疏编码"""
    from collections import Counter
    tokens = text.lower().split()
    counts = Counter(tokens)
    # 将词元映射到索引（生产环境使用词汇表）
    indices = [hash(t) % 30000 for t in counts.keys()]
    values = list(counts.values())
    return SparseVector(indices=indices, values=values)

client.upsert(
    collection_name="hybrid",
    points=[
        PointStruct(
            id=1,
            vector={
                "dense": dense_encoder.encode("Python编程").tolist(),
                "sparse": encode_sparse("Python编程语言代码")
            },
            payload={"text": "Python编程语言代码"}
        )
    ]
)

# 使用倒数排名融合（RRF）的混合搜索
from qdrant_client.models import FusionQuery

results = client.query_points(
    collection_name="hybrid",
    prefetch=[
        Prefetch(query=dense_query, using="dense", limit=20),
        Prefetch(query=sparse_query, using="sparse", limit=20)
    ],
    query=FusionQuery(fusion="rrf"),  # 合并结果
    limit=10
)
```

### 多阶段搜索

```python
from qdrant_client.models import Prefetch, Query

# 两阶段检索：粗排然后精排
results = client.query_points(
    collection_name="documents",
    prefetch=[
        Prefetch(
            query=query_vector,
            limit=100,  # 第一阶段广泛召回
            params={"quantization": {"rescore": False}}  # 快速、近似
        )
    ],
    query=Query(nearest=query_vector),
    limit=10,
    params={"quantization": {"rescore": True}}  # 精确重排
)
```

## 推荐系统

### 项目对项目推荐

```python
# 查找相似项目
recommendations = client.recommend(
    collection_name="products",
    positive=[1, 2, 3],  # 用户喜欢的ID
    negative=[4],         # 用户不喜欢的ID
    limit=10
)

# 带过滤
recommendations = client.recommend(
    collection_name="products",
    positive=[1, 2],
    query_filter={
        "must": [
            {"key": "category", "match": {"value": "电子产品"}},
            {"key": "in_stock", "match": {"value": True}}
        ]
    },
    limit=10
)
```

### 从另一个集合查找

```python
from qdrant_client.models import RecommendStrategy, LookupLocation

# 使用另一个集合的向量进行推荐
results = client.recommend(
    collection_name="products",
    positive=[
        LookupLocation(
            collection_name="user_history",
            id="user_123"
        )
    ],
    strategy=RecommendStrategy.AVERAGE_VECTOR,
    limit=10
)
```

## 高级过滤

### 嵌套Payload过滤

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue, NestedCondition

# 对嵌套对象过滤
results = client.search(
    collection_name="documents",
    query_vector=query,
    query_filter=Filter(
        must=[
            NestedCondition(
                key="metadata",
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="author.name",
                            match=MatchValue(value="张三")
                        )
                    ]
                )
            )
        ]
    ),
    limit=10
)
```

### 地理过滤

```python
from qdrant_client.models import FieldCondition, GeoRadius, GeoPoint

# 查找半径范围内的结果
results = client.search(
    collection_name="locations",
    query_vector=query,
    query_filter=Filter(
        must=[
            FieldCondition(
                key="location",
                geo_radius=GeoRadius(
                    center=GeoPoint(lat=40.7128, lon=-74.0060),
                    radius=5000  # 米
                )
            )
        ]
    ),
    limit=10
)

# 地理边界框
from qdrant_client.models import GeoBoundingBox

results = client.search(
    collection_name="locations",
    query_vector=query,
    query_filter=Filter(
        must=[
            FieldCondition(
                key="location",
                geo_bounding_box=GeoBoundingBox(
                    top_left=GeoPoint(lat=40.8, lon=-74.1),
                    bottom_right=GeoPoint(lat=40.6, lon=-73.9)
                )
            )
        ]
    ),
    limit=10
)
```

### 全文搜索

```python
from qdrant_client.models import TextIndexParams, TokenizerType

# 创建文本索引
client.create_payload_index(
    collection_name="documents",
    field_name="content",
    field_schema=TextIndexParams(
        type="text",
        tokenizer=TokenizerType.WORD,
        min_token_len=2,
        max_token_len=15,
        lowercase=True
    )
)

# 全文过滤
from qdrant_client.models import MatchText

results = client.search(
    collection_name="documents",
    query_vector=query,
    query_filter=Filter(
        must=[
            FieldCondition(
                key="content",
                match=MatchText(text="机器学习")
            )
        ]
    ),
    limit=10
)
```

## 量化策略

### 标量量化（INT8）

```python
from qdrant_client.models import ScalarQuantization, ScalarQuantizationConfig, ScalarType

# 约4倍内存减少，几乎无精度损失
client.create_collection(
    collection_name="scalar_quantized",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            quantile=0.99,       # 裁剪极端值
            always_ram=True     # 保持量化向量在RAM中
        )
    )
)
```

### 乘积量化

```python
from qdrant_client.models import ProductQuantization, ProductQuantizationConfig, CompressionRatio

# 约16倍内存减少，有一定精度损失
client.create_collection(
    collection_name="product_quantized",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=ProductQuantization(
        product=ProductQuantizationConfig(
            compression=CompressionRatio.X16,
            always_ram=True
        )
    )
)
```

### 二进制量化

```python
from qdrant_client.models import BinaryQuantization, BinaryQuantizationConfig

# 约32倍内存减少，需要过采样
client.create_collection(
    collection_name="binary_quantized",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=BinaryQuantization(
        binary=BinaryQuantizationConfig(always_ram=True)
    )
)

# 过采样搜索
results = client.search(
    collection_name="binary_quantized",
    query_vector=query,
    search_params={
        "quantization": {
            "rescore": True,
            "oversampling": 2.0  # 检索2倍候选，重新排序
        }
    },
    limit=10
)
```

## 快照和备份

### 创建快照

```python
# 创建集合快照
snapshot_info = client.create_snapshot(collection_name="documents")
print(f"快照: {snapshot_info.name}")

# 列出快照
snapshots = client.list_snapshots(collection_name="documents")
for s in snapshots:
    print(f"{s.name}: {s.size} 字节")

# 完整存储快照
full_snapshot = client.create_full_snapshot()
```

### 从快照恢复

```python
# 下载快照
client.download_snapshot(
    collection_name="documents",
    snapshot_name="documents-2024-01-01.snapshot",
    target_path="./backup/"
)

# 恢复（通过 REST API）
import requests

response = requests.put(
    "http://localhost:6333/collections/documents/snapshots/recover",
    json={"location": "file:///backup/documents-2024-01-01.snapshot"}
)
```

## 集合别名

```python
# 创建别名
client.update_collection_aliases(
    change_aliases_operations=[
        {"create_alias": {"alias_name": "production", "collection_name": "documents_v2"}}
    ]
)

# 蓝绿部署
# 1. 创建带更新的新集合
client.create_collection(collection_name="documents_v3", ...)

# 2. 填充新集合
client.upsert(collection_name="documents_v3", points=new_points)

# 3. 原子切换
client.update_collection_aliases(
    change_aliases_operations=[
        {"delete_alias": {"alias_name": "production"}},
        {"create_alias": {"alias_name": "production", "collection_name": "documents_v3"}}
    ]
)

# 通过别名搜索
results = client.search(collection_name="production", query_vector=query, limit=10)
```

## 滚动和迭代

### 滚动遍历所有点

```python
# 分页迭代
offset = None
all_points = []

while True:
    results, offset = client.scroll(
        collection_name="documents",
        limit=100,
        offset=offset,
        with_payload=True,
        with_vectors=False
    )
    all_points.extend(results)

    if offset is None:
        break

print(f"总点数: {len(all_points)}")
```

### 带过滤的滚动

```python
# 带过滤滚动
results, _ = client.scroll(
    collection_name="documents",
    scroll_filter=Filter(
        must=[
            FieldCondition(key="status", match=MatchValue(value="active"))
        ]
    ),
    limit=1000
)
```

## 异步客户端

```python
import asyncio
from qdrant_client import AsyncQdrantClient

async def main():
    client = AsyncQdrantClient(host="localhost", port=6333)

    # 异步操作
    await client.create_collection(
        collection_name="async_docs",
        vectors_config=VectorParams(size=384, distance=Distance.COSINE)
    )

    await client.upsert(
        collection_name="async_docs",
        points=points
    )

    results = await client.search(
        collection_name="async_docs",
        query_vector=query,
        limit=10
    )

    return results

results = asyncio.run(main())
```

## gRPC客户端

```python
from qdrant_client import QdrantClient

# 优先使用gRPC以获得更好性能
client = QdrantClient(
    host="localhost",
    port=6333,
    grpc_port=6334,
    prefer_grpc=True  # 尽可能使用gRPC
)

# 仅gRPC客户端
from qdrant_client import QdrantClient

client = QdrantClient(
    host="localhost",
    grpc_port=6334,
    prefer_grpc=True,
    https=False
)
```

## 多租户

### 基于Payload的隔离

```python
# 单集合，按租户过滤
client.upsert(
    collection_name="multi_tenant",
    points=[
        PointStruct(
            id=1,
            vector=embedding,
            payload={"tenant_id": "tenant_a", "text": "..."}
        )
    ]
)

# 在租户内搜索
results = client.search(
    collection_name="multi_tenant",
    query_vector=query,
    query_filter=Filter(
        must=[FieldCondition(key="tenant_id", match=MatchValue(value="tenant_a"))]
    ),
    limit=10
)
```

### 每个租户一个集合

```python
# 为租户创建集合
def create_tenant_collection(tenant_id: str):
    client.create_collection(
        collection_name=f"tenant_{tenant_id}",
        vectors_config=VectorParams(size=384, distance=Distance.COSINE)
    )

# 搜索租户集合
def search_tenant(tenant_id: str, query_vector: list, limit: int = 10):
    return client.search(
        collection_name=f"tenant_{tenant_id}",
        query_vector=query_vector,
        limit=limit
    )
```

## 性能监控

### 集合统计

```python
# 集合信息
info = client.get_collection("documents")
print(f"点数: {info.points_count}")
print(f"索引向量数: {info.indexed_vectors_count}")
print(f"分段数: {len(info.segments)}")
print(f"状态: {info.status}")

# 详细分段信息
for i, segment in enumerate(info.segments):
    print(f"分段 {i}: {segment}")
```

### 遥测数据

```python
# 获取遥测数据
telemetry = client.get_telemetry()
print(f"集合: {telemetry.collections}")
print(f"操作: {telemetry.operations}")
```
