---
name: index_types
description: FAISS索引类型完整指南，涵盖Flat、IVF、HNSW、PQ等索引类型的选择和使用。
---

# FAISS索引类型指南

选择和使用FAISS索引类型的完整指南。

## 索引选择指南

| 数据集大小 | 索引类型 | 训练 | 准确性 | 速度 |
|--------------|------------|----------|----------|-------|
| < 10K | Flat | 否 | 100% | 慢 |
| 10K-1M | IVF | 是 | 95-99% | 快 |
| 1M-10M | HNSW | 否 | 99% | 最快 |
| > 10M | IVF+PQ | 是 | 90-95% | 快，低内存 |

## Flat索引（精确搜索）

### IndexFlatL2 - L2（欧几里得）距离

```python
import faiss
import numpy as np

d = 128  # 维度
index = faiss.IndexFlatL2(d)

# 添加向量
vectors = np.random.random((1000, d)).astype('float32')
index.add(vectors)

# 搜索
k = 5
query = np.random.random((1, d)).astype('float32')
distances, indices = index.search(query, k)
```

**使用场景：**
- 数据集 < 10,000个向量
- 需要100%准确性
- 作为基准对比

### IndexFlatIP - 内积（余弦相似度）

```python
# 对于余弦相似度，先归一化向量
import faiss

d = 128
index = faiss.IndexFlatIP(d)

# 归一化向量（余弦相似度必需）
faiss.normalize_L2(vectors)
index.add(vectors)

# 搜索
faiss.normalize_L2(query)
distances, indices = index.search(query, k)
```

**使用场景：**
- 需要余弦相似度
- 推荐系统
- 文本嵌入

## IVF索引（倒排文件）

### IndexIVFFlat - 基于聚类的搜索

```python
# 创建量化器
quantizer = faiss.IndexFlatL2(d)

# 创建带100个聚类的IVF索引
nlist = 100  # 聚类数量
index = faiss.IndexIVFFlat(quantizer, d, nlist)

# 在数据上训练（必需！）
index.train(vectors)

# 添加向量
index.add(vectors)

# 搜索（nprobe = 要搜索的聚类数）
index.nprobe = 10  # 搜索最近的10个聚类
distances, indices = index.search(query, k)
```

**参数：**
- `nlist`：聚类数量（推荐√N到4√N）
- `nprobe`：要搜索的聚类数（1到nlist，越高越准确）

**使用场景：**
- 数据集10K-1M个向量
- 需要快速近似搜索
- 可以承受训练时间

### 调整nprobe

```python
# 测试不同的nprobe值
for nprobe in [1, 5, 10, 20, 50]:
    index.nprobe = nprobe
    distances, indices = index.search(query, k)
    # 测量召回率/速度权衡
```

**指南：**
- `nprobe=1`：最快，约50%召回率
- `nprobe=10`：良好平衡，约95%召回率
- `nprobe=nlist`：精确搜索（与Flat相同）

## HNSW索引（基于图）

### IndexHNSWFlat - 分层NSW

```python
# HNSW索引
M = 32  # 每层连接数（16-64）
index = faiss.IndexHNSWFlat(d, M)

# 可选：设置ef_construction（构建时间参数）
index.hnsw.efConstruction = 40  # 越高质量越好，构建越慢

# 添加向量（无需训练！）
index.add(vectors)

# 搜索
index.hnsw.efSearch = 16  # 搜索时间参数
distances, indices = index.search(query, k)
```

**参数：**
- `M`：每层连接数（16-64，默认32）
- `efConstruction`：构建质量（40-200，越高越好）
- `efSearch`：搜索质量（16-512，越高越准确）

**使用场景：**
- 需要最佳质量的近似搜索
- 可以承受更高内存（更多连接）
- 数据集1M-10M个向量

## PQ索引（乘积量化）

### IndexPQ - 内存高效

```python
# PQ将内存减少16-32倍
m = 8   # 子量化器数量（分割d）
nbits = 8  # 每个子量化器的位数

index = faiss.IndexPQ(d, m, nbits)

# 训练（必需！）
index.train(vectors)

# 添加向量
index.add(vectors)

# 搜索
distances, indices = index.search(query, k)
```

**参数：**
- `m`：子量化器数量（d必须能被m整除）
- `nbits`：每个码的位数（8或16）

**内存节省：**
- 原始：d × 4字节（float32）
- PQ：m字节
- 压缩比：4d/m

**使用场景：**
- 内存受限
- 大型数据集（> 10M向量）
- 可以接受约90-95%准确性

### IndexIVFPQ - IVF + PQ组合

```python
# 适合超大型数据集
nlist = 4096
m = 8
nbits = 8

quantizer = faiss.IndexFlatL2(d)
index = faiss.IndexIVFPQ(quantizer, d, nlist, m, nbits)

# 训练
index.train(vectors)
index.add(vectors)

# 搜索
index.nprobe = 32
distances, indices = index.search(query, k)
```

**使用场景：**
- 数据集 > 10M向量
- 需要快速搜索 + 低内存
- 可以接受90-95%准确性

## GPU索引

### 单GPU

```python
import faiss

# 创建CPU索引
index_cpu = faiss.IndexFlatL2(d)

# 移动到GPU
res = faiss.StandardGpuResources()  # GPU资源
index_gpu = faiss.index_cpu_to_gpu(res, 0, index_cpu)  # GPU 0

# 正常使用
index_gpu.add(vectors)
distances, indices = index_gpu.search(query, k)
```

### 多GPU

```python
# 使用所有可用GPU
index_gpu = faiss.index_cpu_to_all_gpus(index_cpu)

# 或指定GPU
gpus = [0, 1, 2, 3]  # 使用GPU 0-3
index_gpu = faiss.index_cpu_to_gpus_list(index_cpu, gpus)
```

**加速：**
- 单GPU：比CPU快10-50倍
- 多GPU：近线性扩展

## 索引工厂

```python
# 使用字符串描述符轻松创建索引
index = faiss.index_factory(d, "IVF100,Flat")
index = faiss.index_factory(d, "HNSW32")
index = faiss.index_factory(d, "IVF4096,PQ8")

# 训练和使用
index.train(vectors)
index.add(vectors)
```

**常用描述符：**
- `"Flat"`：精确搜索
- `"IVF100,Flat"`：100个聚类的IVF
- `"HNSW32"`：M=32的HNSW
- `"IVF4096,PQ8"`：IVF + PQ压缩

## 性能比较

### 搜索速度（1M向量，k=10）

| 索引 | 构建时间 | 搜索时间 | 内存 | 召回率 |
|-------|------------|-------------|--------|--------|
| Flat | 0秒 | 50ms | 512 MB | 100% |
| IVF100 | 5秒 | 2ms | 512 MB | 95% |
| HNSW32 | 60秒 | 1ms | 1GB | 99% |
| IVF4096+PQ8 | 30秒 | 3ms | 32 MB | 90% |

*CPU（16核），128维向量*

## 最佳实践

1. **从Flat开始** - 作为基准对比
2. **中型数据集使用IVF** - 良好的平衡
3. **质量优先使用HNSW** - 如果内存允许
4. **内存节省添加PQ** - 大型数据集
5. **100K以上使用GPU** - 10-50倍加速
6. **调优nprobe/efSearch** - 权衡速度/准确性
7. **用代表性数据训练** - 更好的聚类
8. **保存训练好的索引** - 避免重新训练

## 资源

- **Wiki**: https://github.com/facebookresearch/faiss/wiki
- **论文**: https://arxiv.org/abs/1702.08734
