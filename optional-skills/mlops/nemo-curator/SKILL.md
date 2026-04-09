---
name: nemo-curator
description: GPU加速的数据策管工具，用于LLM训练。支持文本/图像/视频/音频。提供模糊去重（16倍加速）、质量过滤（30+启发式规则）、语义去重、PII脱敏、NSFW检测。支持RAPIDS跨GPU扩展。用于准备高质量训练数据集、清洗网页数据或大规模语料去重。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [nemo-curator, cudf, dask, rapids]
metadata:
  kclaw:
    tags: [数据处理, NeMo Curator, 数据策管, GPU加速, 去重, 质量过滤, NVIDIA, RAPIDS, PII脱敏, 多模态, LLM训练数据]

---

# NeMo Curator - GPU加速的数据策管

NVIDIA用于准备LLM高质量训练数据的工具包。

## 何时使用NeMo Curator

**在以下情况下使用NeMo Curator：**
- 从网页抓取数据准备LLM训练数据（Common Crawl）
- 需要快速去重（比CPU快16倍）
- 策管多模态数据集（文本、图像、视频、音频）
- 过滤低质量或有害内容
- 在GPU集群上扩展数据处理

**性能：**
- **16倍加速**模糊去重（8TB RedPajama v2）
- **40%更低TCO**相比CPU方案
- **近线性扩展**跨GPU节点

**使用替代方案：**
- **datatrove**：基于CPU的开源数据处理
- **dolma**：Allen AI的数据工具包
- **Ray Data**：通用ML数据处理（无策管重点）

## 快速开始

### 安装

```bash
# 文本策管（CUDA 12）
uv pip install "nemo-curator[text_cuda12]"

# 全模态
uv pip install "nemo-curator[all_cuda12]"

# 仅CPU（较慢）
uv pip install "nemo-curator[cpu]"
```

### 基础文本策管流程

```python
from nemo_curator import ScoreFilter, Modify
from nemo_curator.datasets import DocumentDataset
import pandas as pd

# 加载数据
df = pd.DataFrame({"text": ["Good document", "Bad doc", "Excellent text"]})
dataset = DocumentDataset(df)

# 质量过滤
def quality_score(doc):
    return len(doc["text"].split()) > 5  # 过滤短文档

filtered = ScoreFilter(quality_score)(dataset)

# 去重
from nemo_curator.modules import ExactDuplicates
deduped = ExactDuplicates()(filtered)

# 保存
deduped.to_parquet("curated_data/")
```

## 数据策管流程

### 阶段1：质量过滤

```python
from nemo_curator.filters import (
    WordCountFilter,
    RepeatedLinesFilter,
    UrlRatioFilter,
    NonAlphaNumericFilter
)

# 应用30+启发式过滤器
from nemo_curator import ScoreFilter

# 词数过滤器
dataset = dataset.filter(WordCountFilter(min_words=50, max_words=100000))

# 移除重复内容
dataset = dataset.filter(RepeatedLinesFilter(max_repeated_line_fraction=0.3))

# URL比例过滤器
dataset = dataset.filter(UrlRatioFilter(max_url_ratio=0.2))
```

### 阶段2：去重

**精确去重**：
```python
from nemo_curator.modules import ExactDuplicates

# 移除完全重复项
deduped = ExactDuplicates(id_field="id", text_field="text")(dataset)
```

**模糊去重**（GPU上16倍加速）：
```python
from nemo_curator.modules import FuzzyDuplicates

# MinHash + LSH去重
fuzzy_dedup = FuzzyDuplicates(
    id_field="id",
    text_field="text",
    num_hashes=260,      # MinHash参数
    num_buckets=20,
    hash_method="md5"
)

deduped = fuzzy_dedup(dataset)
```

**语义去重**：
```python
from nemo_curator.modules import SemanticDuplicates

# 基于嵌入的去重
semantic_dedup = SemanticDuplicates(
    id_field="id",
    text_field="text",
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    threshold=0.8  # 余弦相似度阈值
)

deduped = semantic_dedup(dataset)
```

### 阶段3：PII脱敏

```python
from nemo_curator.modules import Modify
from nemo_curator.modifiers import PIIRedactor

# 脱敏个人身份信息
pii_redactor = PIIRedactor(
    supported_entities=["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON", "LOCATION"],
    anonymize_action="replace"  # 或 "redact"
)

redacted = Modify(pii_redactor)(dataset)
```

### 阶段4：分类器过滤

```python
from nemo_curator.classifiers import QualityClassifier

# 质量分类
quality_clf = QualityClassifier(
    model_path="nvidia/quality-classifier-deberta",
    batch_size=256,
    device="cuda"
)

# 过滤低质量文档
high_quality = dataset.filter(lambda doc: quality_clf(doc["text"]) > 0.5)
```

## GPU加速

### GPU与CPU性能对比

| 操作 | CPU（16核） | GPU（A100） | 加速比 |
|-----------|----------------|------------|---------|
| 模糊去重（8TB） | 120小时 | 7.5小时 | 16× |
| 精确去重（1TB） | 8小时 | 0.5小时 | 16× |
| 质量过滤 | 2小时 | 0.2小时 | 10× |

### 多GPU扩展

```python
from nemo_curator import get_client
import dask_cuda

# 初始化GPU集群
client = get_client(cluster_type="gpu", n_workers=8)

# 使用8个GPU处理
deduped = FuzzyDuplicates(...)(dataset)
```

## 多模态策管

### 图像策管

```python
from nemo_curator.image import (
    AestheticFilter,
    NSFWFilter,
    CLIPEmbedder
)

# 美学评分
aesthetic_filter = AestheticFilter(threshold=5.0)
filtered_images = aesthetic_filter(image_dataset)

# NSFW检测
nsfw_filter = NSFWFilter(threshold=0.9)
safe_images = nsfw_filter(filtered_images)

# 生成CLIP嵌入
clip_embedder = CLIPEmbedder(model="openai/clip-vit-base-patch32")
image_embeddings = clip_embedder(safe_images)
```

### 视频策管

```python
from nemo_curator.video import (
    SceneDetector,
    ClipExtractor,
    InternVideo2Embedder
)

# 检测场景
scene_detector = SceneDetector(threshold=27.0)
scenes = scene_detector(video_dataset)

# 提取片段
clip_extractor = ClipExtractor(min_duration=2.0, max_duration=10.0)
clips = clip_extractor(scenes)

# 生成嵌入
video_embedder = InternVideo2Embedder()
video_embeddings = video_embedder(clips)
```

### 音频策管

```python
from nemo_curator.audio import (
    ASRInference,
    WERFilter,
    DurationFilter
)

# ASR转录
asr = ASRInference(model="nvidia/stt_en_fastconformer_hybrid_large_pc")
transcribed = asr(audio_dataset)

# 按WER（词错误率）过滤
wer_filter = WERFilter(max_wer=0.3)
high_quality_audio = wer_filter(transcribed)

# 时长过滤
duration_filter = DurationFilter(min_duration=1.0, max_duration=30.0)
filtered_audio = duration_filter(high_quality_audio)
```

## 常见模式

### 网页抓取数据策管（Common Crawl）

```python
from nemo_curator import ScoreFilter, Modify
from nemo_curator.filters import *
from nemo_curator.modules import *
from nemo_curator.datasets import DocumentDataset

# 加载Common Crawl数据
dataset = DocumentDataset.read_parquet("common_crawl/*.parquet")

# 流程
pipeline = [
    # 1. 质量过滤
    WordCountFilter(min_words=100, max_words=50000),
    RepeatedLinesFilter(max_repeated_line_fraction=0.2),
    SymbolToWordRatioFilter(max_symbol_to_word_ratio=0.3),
    UrlRatioFilter(max_url_ratio=0.3),

    # 2. 语言过滤
    LanguageIdentificationFilter(target_languages=["en"]),

    # 3. 去重
    ExactDuplicates(id_field="id", text_field="text"),
    FuzzyDuplicates(id_field="id", text_field="text", num_hashes=260),

    # 4. PII脱敏
    PIIRedactor(),

    # 5. NSFW过滤
    NSFWClassifier(threshold=0.8)
]

# 执行
for stage in pipeline:
    dataset = stage(dataset)

# 保存
dataset.to_parquet("curated_common_crawl/")
```

### 分布式处理

```python
from nemo_curator import get_client
from dask_cuda import LocalCUDACluster

# 多GPU集群
cluster = LocalCUDACluster(n_workers=8)
client = get_client(cluster=cluster)

# 处理大型数据集
dataset = DocumentDataset.read_parquet("s3://large_dataset/*.parquet")
deduped = FuzzyDuplicates(...)(dataset)

# 清理
client.close()
cluster.close()
```

## 性能基准

### 模糊去重（8TB RedPajama v2）

- **CPU（256核）**：120小时
- **GPU（8× A100）**：7.5小时
- **加速比**：16×

### 精确去重（1TB）

- **CPU（64核）**：8小时
- **GPU（4× A100）**：0.5小时
- **加速比**：16×

### 质量过滤（100GB）

- **CPU（32核）**：2小时
- **GPU（2× A100）**：0.2小时
- **加速比**：10×

## 成本对比

**基于CPU的策管**（AWS c5.18xlarge × 10）：
- 成本：$3.60/小时 × 10 = $36/小时
- 8TB耗时：120小时
- **总计**：$4,320

**基于GPU的策管**（AWS p4d.24xlarge × 2）：
- 成本：$32.77/小时 × 2 = $65.54/小时
- 8TB耗时：7.5小时
- **总计**：$491.55

**节省**：89%减少（节省$3,828）

## 支持的数据格式

- **输入**：Parquet、JSONL、CSV
- **输出**：Parquet（推荐）、JSONL
- **WebDataset**：多模态TAR存档

## 使用场景

**生产部署**：
- NVIDIA使用NeMo Curator准备Nemotron-4训练数据
- 开源数据集策管：RedPajama v2、The Pile

## 参考资料

- **[过滤指南](references/filtering.md)** - 30+质量过滤器、启发式规则
- **[去重指南](references/deduplication.md)** - 精确、模糊、语义方法

## 资源

- **GitHub**：https://github.com/NVIDIA/NeMo-Curator
- **文档**：https://docs.nvidia.com/nemo-framework/user-guide/latest/datacuration/
- **版本**：0.4.0+
- **许可证**：Apache 2.0
