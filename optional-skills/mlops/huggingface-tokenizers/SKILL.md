---
name: huggingface-tokenizers
description: 为研究和生产优化的高速分词器。基于Rust实现，1GB文本分词<20秒。支持BPE、WordPiece和Unigram算法。训练自定义词表，跟踪对齐，处理填充/截断。与transformers无缝集成。需要高性能分词或自定义分词器训练时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [tokenizers, transformers, datasets]
metadata:
  kclaw:
    tags: [分词, HuggingFace, BPE, WordPiece, Unigram, 快速分词, Rust, 自定义分词器, 对齐跟踪, 生产]

---

# HuggingFace分词器 - NLP快速分词

具有Rust性能和Python易用性的生产级分词器。

## 何时使用HuggingFace分词器

**在以下情况下使用HuggingFace分词器：**
- 需要极快的分词（<20秒/GB文本）
- 从头训练自定义分词器
- 想要对齐跟踪（token → 原始文本位置）
- 构建生产NLP管道
- 需要高效分词大型语料

**性能**：
- **速度**：在CPU上分词1GB <20秒
- **实现**：Rust核心，带Python/Node.js绑定
- **效率**：比纯Python实现快10-100倍

**使用替代方案**：
- **SentencePiece**：语言独立，T5/ALBERT使用
- **tiktoken**：OpenAI的GPT模型BPE分词器
- **transformers AutoTokenizer**：仅加载预训练（内部使用此库）

## 快速开始

### 安装

```bash
# 安装分词器
pip install tokenizers

# 带transformers集成
pip install tokenizers transformers
```

### 加载预训练分词器

```python
from tokenizers import Tokenizer

# 从HuggingFace Hub加载
tokenizer = Tokenizer.from_pretrained("bert-base-uncased")

# 编码文本
output = tokenizer.encode("Hello, how are you?")
print(output.tokens)  # ['hello', ',', 'how', 'are', 'you', '?']
print(output.ids)     # [7592, 1010, 2129, 2024, 2017, 1029]

# 解码回来
text = tokenizer.decode(output.ids)
print(text)  # "hello, how are you?"
```

### 训练自定义BPE分词器

```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer
from tokenizers.pre_tokenizers import Whitespace

# 使用BPE模型初始化分词器
tokenizer = Tokenizer(BPE(unk_token="[UNK]"))
tokenizer.pre_tokenizer = Whitespace()

# 配置训练器
trainer = BpeTrainer(
    vocab_size=30000,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"],
    min_frequency=2
)

# 在文件上训练
files = ["train.txt", "validation.txt"]
tokenizer.train(files, trainer)

# 保存
tokenizer.save("my-tokenizer.json")
```

**训练时间**：100MB语料约1-2分钟，1GB约10-20分钟

### 带填充的批量编码

```python
# 启用填充
tokenizer.enable_padding(pad_id=3, pad_token="[PAD]")

# 批量编码
texts = ["Hello world", "This is a longer sentence"]
encodings = tokenizer.encode_batch(texts)

for encoding in encodings:
    print(encoding.ids)
# [101, 7592, 2088, 102, 3, 3, 3]
# [101, 2023, 2003, 1037, 2936, 6251, 102]
```

## 分词算法

### BPE（字节对编码）

**工作原理**：
1. 从字符级词表开始
2. 找到最常见的字符对
3. 合并为新token，添加到词表
4. 重复直到达到词表大小

**使用者**：GPT-2、GPT-3、RoBERTa、BART、DeBERTa

```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer
from tokenizers.pre_tokenizers import ByteLevel

tokenizer = Tokenizer(BPE(unk_token="<|endoftext|>"))
tokenizer.pre_tokenizer = ByteLevel()

trainer = BpeTrainer(
    vocab_size=50257,
    special_tokens=["<|endoftext|>"],
    min_frequency=2
)

tokenizer.train(files=["data.txt"], trainer=trainer)
```

**优势**：
- 处理OOV词好（分解为子词）
- 灵活的词表大小
- 适合形态丰富的语言

**权衡**：
- 分词取决于合并顺序
- 可能意外拆分常见词

### WordPiece

**工作原理**：
1. 从字符词表开始
2. 评分合并对：`frequency(pair) / (frequency(first) × frequency(second))`
3. 合并评分最高的对
4. 重复直到达到词表大小

**使用者**：BERT、DistilBERT、MobileBERT

```python
from tokenizers import Tokenizer
from tokenizers.models import WordPiece
from tokenizers.trainers import WordPieceTrainer
from tokenizers.pre_tokenizers import Whitespace
from tokenizers.normalizers import BertNormalizer

tokenizer = Tokenizer(WordPiece(unk_token="[UNK]"))
tokenizer.normalizer = BertNormalizer(lowercase=True)
tokenizer.pre_tokenizer = Whitespace()

trainer = WordPieceTrainer(
    vocab_size=30522,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"],
    continuing_subword_prefix="##"
)

tokenizer.train(files=["corpus.txt"], trainer=trainer)
```

**优势**：
- 优先有意义的合并（高评分 = 语义相关）
- 在BERT中成功使用（最先进结果）

**权衡**：
- 未知词如果没有子词匹配会变成`[UNK]`
- 保存词表而非合并规则（文件更大）

### Unigram

**工作原理**：
1. 从大词表开始（所有子串）
2. 用当前词表计算语料损失
3. 移除对损失影响最小的token
4. 重复直到达到词表大小

**使用者**：ALBERT、T5、mBART、XLNet（通过SentencePiece）

```python
from tokenizers import Tokenizer
from tokenizers.models import Unigram
from tokenizers.trainers import UnigramTrainer

tokenizer = Tokenizer(Unigram())

trainer = UnigramTrainer(
    vocab_size=8000,
    special_tokens=["<unk>", "<s>", "</s>"],
    unk_token="<unk>"
)

tokenizer.train(files=["data.txt"], trainer=trainer)
```

**优势**：
- 概率性的（找到最可能的分词）
- 适合没有词边界的语言
- 处理多样化语言上下文

**权衡**：
- 训练计算昂贵
- 更多超参数要调

## 分词管道

完整管道：**标准化 → 预分词 → 模型 → 后处理**

### 标准化

清理和标准化文本：

```python
from tokenizers.normalizers import NFD, StripAccents, Lowercase, Sequence

tokenizer.normalizer = Sequence([
    NFD(),           # Unicode标准化（分解）
    Lowercase(),     # 转换为小写
    StripAccents()   # 移除重音
])

# 输入: "Héllo WORLD"
# 标准化后: "hello world"
```

**常见标准化器**：
- `NFD`、`NFC`、`NFKD`、`NFKC` - Unicode标准化形式
- `Lowercase()` - 转换为小写
- `StripAccents()` - 移除重音（é → e）
- `Strip()` - 移除空白
- `Replace(pattern, content)` - 正则替换

### 预分词

将文本分割为类似词的单元：

```python
from tokenizers.pre_tokenizers import Whitespace, Punctuation, Sequence, ByteLevel

# 按空白和标点分割
tokenizer.pre_tokenizer = Sequence([
    Whitespace(),
    Punctuation()
])

# 输入: "Hello, world!"
# 预分词后: ["Hello", ",", "world", "!"]
```

**常见预分词器**：
- `Whitespace()` - 按空格、制表符、换行分割
- `ByteLevel()` - GPT-2风格字节级分割
- `Punctuation()` - 分离标点
- `Digits(individual_digits=True)` - 单独分割数字
- `Metaspace()` - 用▁替换空格（SentencePiece风格）

### 后处理

添加特殊token用于模型输入：

```python
from tokenizers.processors import TemplateProcessing

# BERT风格: [CLS] sentence [SEP]
tokenizer.post_processor = TemplateProcessing(
    single="[CLS] $A [SEP]",
    pair="[CLS] $A [SEP] $B [SEP]",
    special_tokens=[
        ("[CLS]", 1),
        ("[SEP]", 2),
    ],
)
```

**常见模式**：
```python
# GPT-2: sentence <|endoftext|>
TemplateProcessing(
    single="$A <|endoftext|>",
    special_tokens=[("<|endoftext|>", 50256)]
)

# RoBERTa: <s> sentence </s>
TemplateProcessing(
    single="<s> $A </s>",
    pair="<s> $A </s> </s> $B </s>",
    special_tokens=[("<s>", 0), ("</s>", 2)]
)
```

## 对齐跟踪

跟踪原始文本中的token位置：

```python
output = tokenizer.encode("Hello, world!")

# 获取token偏移量
for token, offset in zip(output.tokens, output.offsets):
    start, end = offset
    print(f"{token:10} → [{start:2}, {end:2}): {text[start:end]!r}")

# 输出:
# hello      → [ 0,  5): 'Hello'
# ,          → [ 5,  6): ','
# world      → [ 7, 12): 'world'
# !          → [12, 13): '!'
```

**用途**：
- 命名实体识别（将预测映射回文本）
- 问答（提取答案跨度）
- Token分类（将标签对齐到原始位置）

## 与transformers集成

### 使用AutoTokenizer加载

```python
from transformers import AutoTokenizer

# AutoTokenizer自动使用快速分词器
tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")

# 检查是否使用快速分词器
print(tokenizer.is_fast)  # True

# 访问底层tokenizers.Tokenizer
fast_tokenizer = tokenizer.backend_tokenizer
print(type(fast_tokenizer))  # <class 'tokenizers.Tokenizer'>
```

### 将自定义分词器转换为transformers

```python
from tokenizers import Tokenizer
from transformers import PreTrainedTokenizerFast

# 训练自定义分词器
tokenizer = Tokenizer(BPE())
# ... 训练分词器 ...
tokenizer.save("my-tokenizer.json")

# 为transformers包装
transformers_tokenizer = PreTrainedTokenizerFast(
    tokenizer_file="my-tokenizer.json",
    unk_token="[UNK]",
    pad_token="[PAD]",
    cls_token="[CLS]",
    sep_token="[SEP]",
    mask_token="[MASK]"
)

# 像任何transformers分词器一样使用
outputs = transformers_tokenizer(
    "Hello world",
    padding=True,
    truncation=True,
    max_length=512,
    return_tensors="pt"
)
```

## 常见模式

### 从迭代器训练（大数据集）

```python
from datasets import load_dataset

# 加载数据集
dataset = load_dataset("wikitext", "wikitext-103-raw-v1", split="train")

# 创建批量迭代器
def batch_iterator(batch_size=1000):
    for i in range(0, len(dataset), batch_size):
        yield dataset[i:i + batch_size]["text"]

# 训练分词器
tokenizer.train_from_iterator(
    batch_iterator(),
    trainer=trainer,
    length=len(dataset)  # 用于进度条
)
```

**性能**：约10-20分钟处理1GB

### 启用截断和填充

```python
# 启用截断
tokenizer.enable_truncation(max_length=512)

# 启用填充
tokenizer.enable_padding(
    pad_id=tokenizer.token_to_id("[PAD]"),
    pad_token="[PAD]",
    length=512  # 固定长度，或None为批量最大
)

# 编码时使用两者
output = tokenizer.encode("This is a long sentence that will be truncated...")
print(len(output.ids))  # 512
```

### 多进程

```python
from tokenizers import Tokenizer
from multiprocessing import Pool

# 加载分词器
tokenizer = Tokenizer.from_file("tokenizer.json")

def encode_batch(texts):
    return tokenizer.encode_batch(texts)

# 并行处理大型语料
with Pool(8) as pool:
    # 将语料分割成块
    chunk_size = 1000
    chunks = [corpus[i:i+chunk_size] for i in range(0, len(corpus), chunk_size)]

    # 并行编码
    results = pool.map(encode_batch, chunks)
```

**加速**：8核5-8倍加速

## 性能基准

### 训练速度

| 语料大小 | BPE（30k词表） | WordPiece（30k） | Unigram（8k） |
|-------------|-----------------|-----------------|--------------|
| 10 MB       | 15秒          | 18秒          | 25秒       |
| 100 MB      | 1.5分钟         | 2分钟           | 4分钟        |
| 1 GB        | 15分钟          | 20分钟          | 40分钟       |

**硬件**：16核CPU，在英文Wikipedia上测试

### 分词速度

| 实现 | 1GB语料 | 吞吐量    |
|----------------|-------------|---------------|
| 纯Python    | ~20分钟 | ~50 MB/分钟   |
| HF分词器  | ~15秒   | ~4 GB/分钟    |
| **加速**    | **80倍** | **80倍**      |

**测试**：英文文本，平均句子长度20词

### 内存使用

| 任务                    | 内存  |
|-------------------------|---------|
| 加载分词器          | ~10 MB  |
| 训练BPE（30k词表）   | ~200 MB |
| 编码100万句子     | ~500 MB |

## 支持的模型

可通过`from_pretrained()`使用的预训练分词器：

**BERT系列**：
- `bert-base-uncased`、`bert-large-cased`
- `distilbert-base-uncased`
- `roberta-base`、`roberta-large`

**GPT系列**：
- `gpt2`、`gpt2-medium`、`gpt2-large`
- `distilgpt2`

**T5系列**：
- `t5-small`、`t5-base`、`t5-large`
- `google/flan-t5-xxl`

**其他**：
- `facebook/bart-base`、`facebook/mbart-large-cc25`
- `albert-base-v2`、`albert-xlarge-v2`
- `xlm-roberta-base`、`xlm-roberta-large`

浏览全部：https://huggingface.co/models?library=tokenizers

## 参考

- **[训练指南](references/training.md)** - 训练自定义分词器，配置训练器，处理大数据集
- **[算法深度探讨](references/algorithms.md)** - BPE、WordPiece、Unigram详细解释
- **[管道组件](references/pipeline.md)** - 标准化器、预分词器、后处理器、解码器
- **[Transformers集成](references/integration.md)** - AutoTokenizer、PreTrainedTokenizerFast、特殊token

## 资源

- **文档**：https://huggingface.co/docs/tokenizers
- **GitHub**：https://github.com/huggingface/tokenizers
- **版本**：0.20.0+
- **课程**：https://huggingface.co/learn/nlp-course/chapter6/1
- **论文**：BPE（Sennrich等，2016）、WordPiece（Schuster & Nakajima，2012）
