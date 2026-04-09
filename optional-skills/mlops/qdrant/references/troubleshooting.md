---
name: troubleshooting
description: Qdrant故障排除指南，涵盖安装、连接、集合、搜索和内存问题。
---

# Qdrant故障排除指南

## 安装问题

### Docker问题

**错误**: `Cannot connect to Docker daemon`

**修复**:
```bash
# 启动Docker守护进程
sudo systemctl start docker

# 或在Mac/Windows上使用Docker Desktop
open -a Docker
```

**错误**: `Port 6333 already in use`

**修复**:
```bash
# 查找占用端口的进程
lsof -i :6333

# 终止进程或使用不同端口
docker run -p 6334:6333 qdrant/qdrant
```

### Python客户端问题

**错误**: `ModuleNotFoundError: No module named 'qdrant_client'`

**修复**:
```bash
pip install qdrant-client

# 安装指定版本
pip install qdrant-client>=1.12.0
```

**错误**: `grpc._channel._InactiveRpcError`

**修复**:
```bash
# 安装带gRPC支持的版本
pip install 'qdrant-client[grpc]'

# 或禁用gRPC
client = QdrantClient(host="localhost", port=6333, prefer_grpc=False)
```

## 连接问题

### 无法连接到服务器

**错误**: `ConnectionRefusedError: [Errno 111] Connection refused`

**解决方案**:

1. **检查服务器是否运行**:
```bash
docker ps | grep qdrant
curl http://localhost:6333/healthz
```

2. **验证端口绑定**:
```bash
# 检查监听端口
netstat -tlnp | grep 6333

# Docker端口映射
docker port <container_id>
```

3. **使用正确的主机**:
```python
# Linux上的Docker
client = QdrantClient(host="localhost", port=6333)

# Mac/Windows上网络问题
client = QdrantClient(host="127.0.0.1", port=6333)

# Docker网络内部
client = QdrantClient(host="qdrant", port=6333)
```

### 超时错误

**错误**: `TimeoutError: Connection timed out`

**修复**:
```python
# 增加超时时间
client = QdrantClient(
    host="localhost",
    port=6333,
    timeout=60  # 秒
)

# 对于大型操作
client.upsert(
    collection_name="documents",
    points=large_batch,
    wait=False  # 不等待索引
)
```

### SSL/TLS错误

**错误**: `ssl.SSLCertVerificationError`

**修复**:
```python
# Qdrant Cloud
client = QdrantClient(
    url="https://cluster.cloud.qdrant.io",
    api_key="your-api-key"
)

# 自签名证书
client = QdrantClient(
    host="localhost",
    port=6333,
    https=True,
    verify=False  # 禁用验证（生产环境不推荐）
)
```

## 集合问题

### 集合已存在

**错误**: `ValueError: Collection 'documents' already exists`

**修复**:
```python
# 创建前检查
collections = client.get_collections().collections
names = [c.name for c in collections]

if "documents" not in names:
    client.create_collection(...)

# 或重新创建
client.recreate_collection(
    collection_name="documents",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
)
```

### 集合未找到

**错误**: `NotFoundException: Collection 'docs' not found`

**修复**:
```python
# 列出可用集合
collections = client.get_collections()
print([c.name for c in collections.collections])

# 检查精确名称（区分大小写）
try:
    info = client.get_collection("documents")
except Exception as e:
    print(f"集合未找到: {e}")
```

### 向量维度不匹配

**错误**: `ValueError: Vector dimension mismatch. Expected 384, got 768`

**修复**:
```python
# 检查集合配置
info = client.get_collection("documents")
print(f"期望维度: {info.config.params.vectors.size}")

# 使用正确维度重新创建
client.recreate_collection(
    collection_name="documents",
    vectors_config=VectorParams(size=768, distance=Distance.COSINE)  # 匹配嵌入
)
```

## 搜索问题

### 搜索结果为空

**问题**: 搜索返回空结果。

**解决方案**:

1. **验证数据存在**:
```python
info = client.get_collection("documents")
print(f"点数: {info.points_count}")

# 滚动检查数据
points, _ = client.scroll(
    collection_name="documents",
    limit=10,
    with_payload=True
)
print(points)
```

2. **检查向量格式**:
```python
# 必须是浮点数列表
query_vector = embedding.tolist()  # numpy转list

# 检查维度
print(f"查询维度: {len(query_vector)}")
```

3. **验证过滤条件**:
```python
# 先不带过滤测试
results = client.search(
    collection_name="documents",
    query_vector=query,
    limit=10
    # 无过滤
)

# 然后逐步添加过滤
```

### 搜索性能慢

**问题**: 搜索时间太长。

**解决方案**:

1. **创建payload索引**:
```python
# 为过滤中使用的字段建立索引
client.create_payload_index(
    collection_name="documents",
    field_name="category",
    field_schema="keyword"
)
```

2. **启用量化和磁盘存储**:
```python
client.update_collection(
    collection_name="documents",
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(type=ScalarType.INT8)
    )
)
```

3. **调整HNSW参数**:
```python
# 更快搜索（较低准确性）
client.update_collection(
    collection_name="documents",
    hnsw_config=HnswConfigDiff(ef_construct=64, m=8)
)

# 使用ef搜索参数
results = client.search(
    collection_name="documents",
    query_vector=query,
    search_params={"hnsw_ef": 64},  # 越低越快
    limit=10
)
```

4. **使用gRPC**:
```python
client = QdrantClient(
    host="localhost",
    port=6333,
    grpc_port=6334,
    prefer_grpc=True
)
```

### 结果不一致

**问题**: 相同查询返回不同结果。

**解决方案**:

1. **等待索引**:
```python
client.upsert(
    collection_name="documents",
    points=points,
    wait=True  # 等待索引更新
)
```

2. **检查复制一致性**:
```python
# 强一致性读取
results = client.search(
    collection_name="documents",
    query_vector=query,
    consistency="all"  # 从所有副本读取
)
```

## Upsert问题

### 批量Upsert失败

**错误**: `PayloadError: Payload too large`

**修复**:
```python
# 拆分成更小的批次
def batch_upsert(client, collection, points, batch_size=100):
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        client.upsert(
            collection_name=collection,
            points=batch,
            wait=True
        )

batch_upsert(client, "documents", large_points_list)
```

### 无效的Point ID

**错误**: `ValueError: Invalid point ID`

**修复**:
```python
# 有效的ID类型：int或UUID字符串
from uuid import uuid4

# 整数ID
PointStruct(id=123, vector=vec, payload={})

# UUID字符串
PointStruct(id=str(uuid4()), vector=vec, payload={})

# 无效
PointStruct(id="custom-string-123", ...)  # 使用UUID格式
```

### Payload验证错误

**错误**: `ValidationError: Invalid payload`

**修复**:
```python
# 确保JSON可序列化
import json

payload = {
    "title": "文档",
    "count": 42,
    "tags": ["a", "b"],
    "nested": {"key": "value"}
}

# upsert前验证
json.dumps(payload)  # 不应抛出异常

# 避免不可序列化的类型
# 无效：datetime, numpy数组, 自定义对象
payload = {
    "timestamp": datetime.now().isoformat(),  # 转为字符串
    "vector": embedding.tolist()  # numpy转list
}
```

## 内存问题

### 内存不足

**错误**: `MemoryError`或容器被杀死

**解决方案**:

1. **启用磁盘存储**:
```python
client.create_collection(
    collection_name="large_collection",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    on_disk_payload=True,  # 将payload存储在磁盘上
    hnsw_config=HnswConfigDiff(on_disk=True)  # 将HNSW存储在磁盘上
)
```

2. **使用量化**:
```python
# 4倍内存减少
client.update_collection(
    collection_name="large_collection",
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            always_ram=False  # 保持在磁盘上
        )
    )
)
```

3. **增加Docker内存**:
```bash
docker run -m 8g -p 6333:6333 qdrant/qdrant
```

4. **配置Qdrant存储**:
```yaml
# config.yaml
storage:
  performance:
    max_search_threads: 2
  optimizers:
    memmap_threshold_kb: 20000
```

### 索引期间高内存使用

**修复**:
```python
# 批量加载时增加索引阈值
client.update_collection(
    collection_name="documents",
    optimizer_config={
        "indexing_threshold": 50000  # 延迟索引
    }
)

# 批量插入
client.upsert(collection_name="documents", points=all_points, wait=False)

# 然后优化
client.update_collection(
    collection_name="documents",
    optimizer_config={
        "indexing_threshold": 10000  # 恢复正常索引
    }
)
```

## 集群问题

### 节点无法加入集群

**问题**: 新节点无法加入集群。

**修复**:
```bash
# 检查网络连接
docker exec qdrant-node-2 ping qdrant-node-1

# 验证bootstrap URL
docker logs qdrant-node-2 | grep bootstrap

# 检查Raft状态
curl http://localhost:6333/cluster
```

### 分裂脑

**问题**: 集群状态不一致。

**修复**:
```bash
# 强制leader选举
curl -X POST http://localhost:6333/cluster/recover

# 或重启少数节点
docker restart qdrant-node-2 qdrant-node-3
```

### 复制延迟

**问题**: 副本落后。

**修复**:
```python
# 检查集合状态
info = client.get_collection("documents")
print(f"状态: {info.status}")

# 对关键写入使用强一致性
client.upsert(
    collection_name="documents",
    points=points,
    ordering=WriteOrdering.STRONG
)
```

## 性能调优

### 基准测试配置

```python
import time
import numpy as np

def benchmark_search(client, collection, n_queries=100, dimension=384):
    # 生成随机查询
    queries = [np.random.rand(dimension).tolist() for _ in range(n_queries)]

    # 预热
    for q in queries[:10]:
        client.search(collection_name=collection, query_vector=q, limit=10)

    # 基准测试
    start = time.perf_counter()
    for q in queries:
        client.search(collection_name=collection, query_vector=q, limit=10)
    elapsed = time.perf_counter() - start

    print(f"QPS: {n_queries / elapsed:.2f}")
    print(f"延迟: {elapsed / n_queries * 1000:.2f}ms")

benchmark_search(client, "documents")
```

### 最优HNSW参数

```python
# 高召回率（较慢）
client.create_collection(
    collection_name="high_recall",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(
        m=32,              # 更多连接
        ef_construct=200   # 更高构建质量
    )
)

# 高速（较低召回率）
client.create_collection(
    collection_name="high_speed",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(
        m=8,               # 更少连接
        ef_construct=64    # 较低构建质量
    )
)

# 平衡
client.create_collection(
    collection_name="balanced",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(
        m=16,              # 默认
        ef_construct=100   # 默认
    )
)
```

## 调试技巧

### 启用详细日志

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("qdrant_client").setLevel(logging.DEBUG)
```

### 检查服务器日志

```bash
# Docker日志
docker logs -f qdrant

# 带时间戳
docker logs --timestamps qdrant

# 最后100行
docker logs --tail 100 qdrant
```

### 检查集合状态

```python
# 集合信息
info = client.get_collection("documents")
print(f"状态: {info.status}")
print(f"点数: {info.points_count}")
print(f"分段: {len(info.segments)}")
print(f"配置: {info.config}")

# 采样点
points, _ = client.scroll(
    collection_name="documents",
    limit=5,
    with_payload=True,
    with_vectors=True
)
for p in points:
    print(f"ID: {p.id}, Payload: {p.payload}")
```

### 测试连接

```python
def test_connection(host="localhost", port=6333):
    try:
        client = QdrantClient(host=host, port=port, timeout=5)
        collections = client.get_collections()
        print(f"已连接! 集合数: {len(collections.collections)}")
        return True
    except Exception as e:
        print(f"连接失败: {e}")
        return False

test_connection()
```

## 获取帮助

1. **文档**: https://qdrant.tech/documentation/
2. **GitHub Issues**: https://github.com/qdrant/qdrant/issues
3. **Discord**: https://discord.gg/qdrant
4. **Stack Overflow**: 标签 `qdrant`

### 报告问题

包括：
- Qdrant版本: `curl http://localhost:6333/`
- Python客户端版本: `pip show qdrant-client`
- 完整错误追踪
- 最小可复现代码
- 集合配置
