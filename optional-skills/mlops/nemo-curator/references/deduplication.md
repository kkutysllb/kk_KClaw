# 去重指南

精确、模糊和语义去重的完整指南。

## 精确去重

移除内容完全相同的文档。

```python
from nemo_curator.modules import ExactDuplicates

# 精确去重
exact_dedup = ExactDuplicates(
    id_field="id",
    text_field="text",
    hash_method="md5"  # 或 "sha256"
)

deduped = exact_dedup(dataset)
```

**性能**：比CPU快约16倍

## 模糊去重

使用MinHash + LSH移除近似重复的文档。

```python
from nemo_curator.modules import FuzzyDuplicates

fuzzy_dedup = FuzzyDuplicates(
    id_field="id",
    text_field="text",
    num_hashes=260,        # MinHash排列数（越多越准确）
    num_buckets=20,        # LSH桶数（越多越快，召回率越低）
    hash_method="md5",
    jaccard_threshold=0.8  # 相似度阈值
)

deduped = fuzzy_dedup(dataset)
```

**参数**：
- `num_hashes`：128-512（默认260）
- `num_buckets`：10-50（默认20）
- `jaccard_threshold`：0.7-0.9（默认0.8）

**性能**：在8TB数据集上快16倍（120小时 → 7.5小时）

## 语义去重

使用嵌入移除语义相似的文档。

```python
from nemo_curator.modules import SemanticDuplicates

semantic_dedup = SemanticDuplicates(
    id_field="id",
    text_field="text",
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    embedding_batch_size=256,
    threshold=0.85,  # 余弦相似度阈值
    device="cuda"
)

deduped = semantic_dedup(dataset)
```

**模型**：
- `all-MiniLM-L6-v2`：快速，384维
- `all-mpnet-base-v2`：更好质量，768维
- 支持自定义模型

## 对比

| 方法 | 速度 | 召回率 | 适用场景 |
|--------|-------|--------|----------|
| 精确 | 最快 | 100% | 仅精确匹配 |
| 模糊 | 快 | ~95% | 近似重复（推荐） |
| 语义 | 慢 | ~90% | 改写、 paraphrase |

## 最佳实践

1. **从精确去重开始** - 移除明显的重复项
2. **大数据集使用模糊去重** - 最佳速度/质量权衡
3. **高价值数据使用语义去重** - 昂贵但彻底
4. **需要GPU加速** - 10-16倍加速
