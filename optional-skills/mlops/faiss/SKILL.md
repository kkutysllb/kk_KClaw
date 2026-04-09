---
name: faiss
description: Facebook的高效相似度搜索和稠密向量聚类库。支持数十亿向量、GPU加速和各种索引类型（Flat、IVF、HNSW）。用于快速k-NN搜索、大规模向量检索，或需要纯相似度搜索（无元数据）的场景。最适合高性能应用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [faiss-cpu, faiss-gpu, numpy]
metadata:
  kclaw:
    tags: [RAG, FAISS, 相似度搜索, 向量搜索, Facebook AI, GPU加速, 十亿级, K-NN, HNSW, 高性能, 大规模]

---

# FAISS - 高效相似度搜索

Facebook AI的十亿级向量相似度搜索库。

## 何时使用FAISS

**在以下情况下使用FAISS：**
- 需要在大型向量数据集（百万/十亿级）上进行快速相似度搜索
- 需要GPU加速
- 纯向量相似度（不需要元数据过滤）
- 高吞吐量、低延迟至关重要
- 嵌入的离线/批处理

**指标：**
- **31,700+ GitHub stars**
- Meta/Facebook AI Research
- **处理数十亿向量**
- **C++** 配合Python绑定

**使用替代方案：**
- **Chroma/Pinecone**：需要元数据过滤
- **Weaviate**：需要完整数据库功能
- **Annoy**：更简单，功能更少

## 快速开始

### 安装

```bash
# 仅CPU
pip install faiss-cpu

# GPU支持
pip install faiss-gpu
```

### 基础用法

```python
import faiss
import numpy as np

# 创建样本数据（1000个向量，128维）
d = 128
nb = 1000
vectors = np.random.random((nb, d)).astype('float32')

# 创建索引
index = faiss.IndexFlatL2(d)  # L2距离
index.add(vectors)             # 添加向量

# 搜索
k = 5  # 找5个最近邻
query = np.random.random((1, d)).astype('float32')
distances, indices = index.search(query, k)

print(f"最近邻: {indices}")
print(f"距离: {distances}")
```

## 索引类型

### 1. Flat（精确搜索）

```python
# L2（欧几里得）距离
index = faiss.IndexFlatL2(d)

# 内积（归一化后为余弦相似度）
index = faiss.IndexFlatIP(d)

# 最慢，最精确
```

### 2. IVF（倒排文件）- 快速近似

```python
# 创建量化器
quantizer = faiss.IndexFlatL2(d)

# IVF索引，100个聚类
nlist = 100
index = faiss.IndexIVFFlat(quantizer, d, nlist)

# 在数据上训练
index.train(vectors)

# 添加向量
index.add(vectors)

# 搜索（nprobe = 搜索的聚类数）
index.nprobe = 10
distances, indices = index.search(query, k)
```

### 3. HNSW（分层NSW）- 最佳质量/速度

```python
# HNSW索引
M = 32  # 每层连接数
index = faiss.IndexHNSWFlat(d, M)

# 无需训练
index.add(vectors)

# 搜索
distances, indices = index.search(query, k)
```

### 4. 产品量化 - 内存高效

```python
# PQ将内存减少16-32倍
m = 8   # 子量化器数量
nbits = 8
index = faiss.IndexPQ(d, m, nbits)

# 训练和添加
index.train(vectors)
index.add(vectors)
```

## 保存和加载

```python
# 保存索引
faiss.write_index(index, "large.index")

# 加载索引
index = faiss.read_index("large.index")

# 继续使用
distances, indices = index.search(query, k)
```

## GPU加速

```python
# 单GPU
res = faiss.StandardGpuResources()
index_cpu = faiss.IndexFlatL2(d)
index_gpu = faiss.index_cpu_to_gpu(res, 0, index_cpu)  # GPU 0

# 多GPU
index_gpu = faiss.index_cpu_to_all_gpus(index_cpu)

# 比CPU快10-100倍
```

## LangChain集成

```python
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

# 创建FAISS向量存储
vectorstore = FAISS.from_documents(docs, OpenAIEmbeddings())

# 保存
vectorstore.save_local("faiss_index")

# 加载
vectorstore = FAISS.load_local(
    "faiss_index",
    OpenAIEmbeddings(),
    allow_dangerous_deserialization=True
)

# 搜索
results = vectorstore.similarity_search("query", k=5)
```

## LlamaIndex集成

```python
from llama_index.vector_stores.faiss import FaissVectorStore
import faiss

# 创建FAISS索引
d = 1536
faiss_index = faiss.IndexFlatL2(d)

vector_store = FaissVectorStore(faiss_index=faiss_index)
```

## 最佳实践

1. **选择正确的索引类型** - 10K以下用Flat，10K-1M用IVF，质量优先用HNSW
2. **余弦相似度归一化** - 对归一化向量使用IndexFlatIP
3. **大型数据集使用GPU** - 快10-100倍
4. **保存训练好的索引** - 训练代价昂贵
5. **调优nprobe/ef_search** - 平衡速度/精度
6. **监控内存** - 大数据集用PQ
7. **批量查询** - 更好的GPU利用率

## 性能

| 索引类型 | 构建时间 | 搜索时间 | 内存 | 精度 |
|------------|------------|-------------|--------|----------|
| Flat | 快 | 慢 | 高 | 100% |
| IVF | 中 | 快 | 中 | 95-99% |
| HNSW | 慢 | 最快 | 高 | 99% |
| PQ | 中 | 快 | 低 | 90-95% |

## 资源

- **GitHub**: https://github.com/facebookresearch/faiss ⭐ 31,700+
- **Wiki**: https://github.com/facebookresearch/faiss/wiki
- **许可证**: MIT
