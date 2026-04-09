# 训练自定义分词器

从头开始训练分词器的完整指南。

## 训练工作流程

### 步骤1：选择分词算法

**决策树**：
- **GPT风格模型** → BPE
- **BERT风格模型** → WordPiece
- **多语言/无词边界** → Unigram

### 步骤2：准备训练数据

```python
# 选项1：从文件
files = ["train.txt", "validation.txt"]

# 选项2：从Python列表
texts = [
    "This is the first sentence.",
    "This is the second sentence.",
    # ... 更多文本
]

# 选项3：从数据集迭代器
from datasets import load_dataset

dataset = load_dataset("wikitext", "wikitext-103-raw-v1", split="train")

def batch_iterator(batch_size=1000):
    for i in range(0, len(dataset), batch_size):
        yield dataset[i:i + batch_size]["text"]
```

### 步骤3：初始化分词器

**BPE示例**：
```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer
from tokenizers.pre_tokenizers import ByteLevel
from tokenizers.decoders import ByteLevel as ByteLevelDecoder

tokenizer = Tokenizer(BPE())
tokenizer.pre_tokenizer = ByteLevel()
tokenizer.decoder = ByteLevelDecoder()

trainer = BpeTrainer(
    vocab_size=50000,
    min_frequency=2,
    special_tokens=["<|endoftext|>", "<|padding|>"],
    show_progress=True
)
```

**WordPiece示例**：
```python
from tokenizers.models import WordPiece
from tokenizers.trainers import WordPieceTrainer
from tokenizers.normalizers import BertNormalizer
from tokenizers.pre_tokenizers import BertPreTokenizer

tokenizer = Tokenizer(WordPiece(unk_token="[UNK]"))
tokenizer.normalizer = BertNormalizer(lowercase=True)
tokenizer.pre_tokenizer = BertPreTokenizer()

trainer = WordPieceTrainer(
    vocab_size=30522,
    min_frequency=2,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"],
    continuing_subword_prefix="##",
    show_progress=True
)
```

**Unigram示例**：
```python
from tokenizers.models import Unigram
from tokenizers.trainers import UnigramTrainer

tokenizer = Tokenizer(Unigram())

trainer = UnigramTrainer(
    vocab_size=8000,
    special_tokens=["<unk>", "<s>", "</s>", "<pad>"],
    unk_token="<unk>",
    show_progress=True
)
```

### 步骤4：训练

```python
# 从文件
tokenizer.train(files=files, trainer=trainer)

# 从迭代器（推荐用于大数据集）
tokenizer.train_from_iterator(
    batch_iterator(),
    trainer=trainer,
    length=len(dataset)  # 可选，用于进度条
)
```

**训练时间**（16核CPU上30k词表）：
- 10 MB: 15-30秒
- 100 MB: 1-3分钟
- 1 GB: 15-30分钟
- 10 GB: 2-4小时

### 步骤5：添加后处理

```python
from tokenizers.processors import TemplateProcessing

# BERT风格
tokenizer.post_processor = TemplateProcessing(
    single="[CLS] $A [SEP]",
    pair="[CLS] $A [SEP] $B [SEP]",
    special_tokens=[
        ("[CLS]", tokenizer.token_to_id("[CLS]")),
        ("[SEP]", tokenizer.token_to_id("[SEP]")),
    ],
)

# GPT-2风格
tokenizer.post_processor = TemplateProcessing(
    single="$A <|endoftext|>",
    special_tokens=[
        ("<|endoftext|>", tokenizer.token_to_id("<|endoftext|>")),
    ],
)
```

### 步骤6：保存

```python
# 保存为JSON
tokenizer.save("my-tokenizer.json")

# 保存到目录（用于transformers）
tokenizer.save("my-tokenizer-dir/tokenizer.json")

# 转换为transformers格式
from transformers import PreTrainedTokenizerFast

transformers_tokenizer = PreTrainedTokenizerFast(
    tokenizer_object=tokenizer,
    unk_token="[UNK]",
    pad_token="[PAD]",
    cls_token="[CLS]",
    sep_token="[SEP]",
    mask_token="[MASK]"
)

transformers_tokenizer.save_pretrained("my-tokenizer-dir")
```

## 训练器配置

### BpeTrainer参数

```python
from tokenizers.trainers import BpeTrainer

trainer = BpeTrainer(
    vocab_size=30000,              # 目标词表大小
    min_frequency=2,               # 合并的最小频率
    special_tokens=["[UNK]"],      # 特殊token（首先添加）
    limit_alphabet=1000,           # 限制初始字母表大小
    initial_alphabet=[],           # 预定义的初始字符
    show_progress=True,            # 显示进度条
    continuing_subword_prefix="",  # 连续子词的前缀
    end_of_word_suffix=""          # 词尾的后缀
)
```

**参数调优**：
- **vocab_size**：英语从30k开始，多语言50k
- **min_frequency**：大语料库2-5，小语料库1
- **limit_alphabet**：非英语（CJK语言）减少

### WordPieceTrainer参数

```python
from tokenizers.trainers import WordPieceTrainer

trainer = WordPieceTrainer(
    vocab_size=30522,              # BERT使用30,522
    min_frequency=2,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"],
    limit_alphabet=1000,
    continuing_subword_prefix="##", # BERT风格前缀
    show_progress=True
)
```

### UnigramTrainer参数

```python
from tokenizers.trainers import UnigramTrainer

trainer = UnigramTrainer(
    vocab_size=8000,               # 通常小于BPE/WordPiece
    special_tokens=["<unk>", "<s>", "</s>"],
    unk_token="<unk>",
    max_piece_length=16,           # 最大token长度
    n_sub_iterations=2,            # EM算法迭代
    shrinking_factor=0.75,         # 每次迭代词表减少率
    show_progress=True
)
```

## 从大数据集训练

### 内存高效训练

```python
from datasets import load_dataset
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer

# 加载数据集
dataset = load_dataset("wikipedia", "20220301.en", split="train", streaming=True)

# 创建迭代器（产生批次）
def batch_iterator(batch_size=1000):
    batch = []
    for sample in dataset:
        batch.append(sample["text"])
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch

# 初始化分词器
tokenizer = Tokenizer(BPE())
trainer = BpeTrainer(vocab_size=50000, special_tokens=["<|endoftext|>"])

# 训练（内存高效 - 流式数据）
tokenizer.train_from_iterator(
    batch_iterator(),
    trainer=trainer
)
```

**内存使用**：约200 MB（对比加载完整数据集10+ GB）

### 多文件训练

```python
import glob

# 查找所有训练文件
files = glob.glob("data/train/*.txt")
print(f"训练 {len(files)} 个文件")

# 在所有文件上训练
tokenizer.train(files=files, trainer=trainer)
```

### 并行训练（多处理）

```python
from multiprocessing import Pool, cpu_count
import os

def train_shard(shard_files):
    """在文件分片上训练分词器。"""
    tokenizer = Tokenizer(BPE())
    trainer = BpeTrainer(vocab_size=50000)
    tokenizer.train(files=shard_files, trainer=trainer)
    return tokenizer.get_vocab()

# 将文件分割成分片
num_shards = cpu_count()
file_shards = [files[i::num_shards] for i in range(num_shards)]

# 并行训练分片
with Pool(num_shards) as pool:
    vocab_shards = pool.map(train_shard, file_shards)

# 合并词表（需要自定义逻辑）
# 这是一个简化示例 - 实际实现需要智能合并
final_vocab = {}
for vocab in vocab_shards:
    final_vocab.update(vocab)
```

## 领域特定分词器

### 代码分词器

```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer
from tokenizers.pre_tokenizers import ByteLevel
from tokenizers.normalizers import Sequence, NFC

# 代码优化配置
tokenizer = Tokenizer(BPE())

# 最小标准化（保留大小写、空白）
tokenizer.normalizer = NFC()  # 仅标准化Unicode

# 字节级预分词（处理所有字符）
tokenizer.pre_tokenizer = ByteLevel()

# 在代码语料上训练
trainer = BpeTrainer(
    vocab_size=50000,
    special_tokens=["<|endoftext|>", "<|pad|>"],
    min_frequency=2
)

tokenizer.train(files=["code_corpus.txt"], trainer=trainer)
```

### 医学/科学分词器

```python
# 保留大小写和特殊字符
from tokenizers.normalizers import NFKC
from tokenizers.pre_tokenizers import Whitespace, Punctuation, Sequence

tokenizer = Tokenizer(BPE())

# 最小标准化
tokenizer.normalizer = NFKC()

# 保留医学术语
tokenizer.pre_tokenizer = Sequence([
    Whitespace(),
    Punctuation(behavior="isolated")  # 保持标点分离
])

trainer = BpeTrainer(
    vocab_size=50000,
    special_tokens=["[UNK]", "[CLS]", "[SEP]"],
    min_frequency=3  # 对罕见医学术语更高阈值
)

tokenizer.train(files=["pubmed_corpus.txt"], trainer=trainer)
```

### 多语言分词器

```python
# 处理多种脚本
from tokenizers.normalizers import NFKC, Lowercase, Sequence

tokenizer = Tokenizer(BPE())

# 标准化但不小写（保留脚本差异）
tokenizer.normalizer = NFKC()

# 字节级处理所有Unicode
from tokenizers.pre_tokenizers import ByteLevel
tokenizer.pre_tokenizer = ByteLevel()

trainer = BpeTrainer(
    vocab_size=100000,  # 多语言需要更大词表
    special_tokens=["<unk>", "<s>", "</s>"],
    limit_alphabet=None  # 无限制（处理所有脚本）
)

# 在多语言语料上训练
tokenizer.train(files=["multilingual_corpus.txt"], trainer=trainer)
```

## 词表大小选择

### 按任务的指南

| 任务                  | 推荐词表大小 | 理由 |
|-----------------------|------------------------|-----------|
| 英语（单语言） | 30,000 - 50,000       | 平衡覆盖 |
| 多语言          | 50,000 - 250,000      | 更多语言 = 更多token |
| 代码                  | 30,000 - 50,000       | 与英语相似 |
| 领域特定       | 10,000 - 30,000       | 更小，专注词表 |
| 字符级任务 | 1,000 - 5,000         | 仅字符 + 子词 |

### 词表大小影响

**小词表（10k）**：
- 优点：训练更快，模型更小，内存更少
- 缺点：每句更多token，OOV处理更差

**中等词表（30k-50k）**：
- 优点：良好平衡，标准选择
- 缺点：无（推荐默认）

**大词表（100k+）**：
- 优点：每句token更少，OOV更好
- 缺点：训练更慢，嵌入表更大

### 经验测试

```python
# 用不同词表大小训练多个分词器
vocab_sizes = [10000, 30000, 50000, 100000]

for vocab_size in vocab_sizes:
    tokenizer = Tokenizer(BPE())
    trainer = BpeTrainer(vocab_size=vocab_size)
    tokenizer.train(files=["sample.txt"], trainer=trainer)

    # 在测试集上评估
    test_text = "Test sentence for evaluation..."
    tokens = tokenizer.encode(test_text).ids

    print(f"词表: {vocab_size:6d} | Tokens: {len(tokens):3d} | Avg: {len(test_text)/len(tokens):.2f} chars/token")

# 示例输出:
# 词表:  10000 | Tokens:  12 | Avg: 2.33 chars/token
# 词表:  30000 | Tokens:   8 | Avg: 3.50 chars/token
# 词表:  50000 | Tokens:   7 | Avg: 4.00 chars/token
# 词表: 100000 | Tokens:   6 | Avg: 4.67 chars/token
```

## 测试分词器质量

### 覆盖率测试

```python
# 在保留数据上测试
test_corpus = load_dataset("wikitext", "wikitext-103-raw-v1", split="test")

total_tokens = 0
unk_tokens = 0
unk_id = tokenizer.token_to_id("[UNK]")

for text in test_corpus["text"]:
    if text.strip():
        encoding = tokenizer.encode(text)
        total_tokens += len(encoding.ids)
        unk_tokens += encoding.ids.count(unk_id)

unk_rate = unk_tokens / total_tokens
print(f"未知token率: {unk_rate:.2%}")

# 良好质量：<1%未知token
# 可接受：1-5%
# 差：>5%
```

### 压缩测试

```python
# 测量分词效率
import numpy as np

token_lengths = []

for text in test_corpus["text"][:1000]:
    if text.strip():
        encoding = tokenizer.encode(text)
        chars_per_token = len(text) / len(encoding.ids)
        token_lengths.append(chars_per_token)

avg_chars_per_token = np.mean(token_lengths)
print(f"每token平均字符数: {avg_chars_per_token:.2f}")

# 良好：4-6 chars/token（英语）
# 可接受：3-4 chars/token
# 差：<3 chars/token（压缩不足）
```

### 语义测试

```python
# 手动检查常用词/短语的分词
test_phrases = [
    "tokenization",
    "machine learning",
    "artificial intelligence",
    "preprocessing",
    "hello world"
]

for phrase in test_phrases:
    tokens = tokenizer.encode(phrase).tokens
    print(f"{phrase:25s} → {tokens}")

# 良好分词：
# tokenization              → ['token', 'ization']
# machine learning          → ['machine', 'learning']
# artificial intelligence   → ['artificial', 'intelligence']
```

## 故障排除

### 问题：训练太慢

**解决方案**：
1. 减小词表大小
2. 增加`min_frequency`
3. 使用`limit_alphabet`减少初始字母表
4. 先在子集上训练

```python
# 快速训练配置
trainer = BpeTrainer(
    vocab_size=20000,      # 更小词表
    min_frequency=5,       # 更高阈值
    limit_alphabet=500,    # 限制字母表
    show_progress=True
)
```

### 问题：未知token率高

**解决方案**：
1. 增加词表大小
2. 减小`min_frequency`
3. 检查标准化（可能太激进）

```python
# 更好覆盖配置
trainer = BpeTrainer(
    vocab_size=50000,      # 更大词表
    min_frequency=1,       # 更低阈值
)
```

### 问题：分词质量差

**解决方案**：
1. 验证标准化符合你的用例
2. 检查预分词正确分割
3. 确保训练数据有代表性
4. 尝试不同算法（BPE vs WordPiece vs Unigram）

```python
# 调试分词管道
text = "Sample text to debug"

# 检查标准化
normalized = tokenizer.normalizer.normalize_str(text)
print(f"标准化后: {normalized}")

# 检查预分词
pre_tokens = tokenizer.pre_tokenizer.pre_tokenize_str(text)
print(f"预分词: {pre_tokens}")

# 检查最终分词
tokens = tokenizer.encode(text).tokens
print(f"Token: {tokens}")
```

## 最佳实践

1. **使用有代表性的训练数据** - 匹配你的目标领域
2. **从标准配置开始** - BERT WordPiece或GPT-2 BPE
3. **在保留数据上测试** - 测量未知token率
4. **迭代词表大小** - 测试30k、50k、100k
5. **与模型一起保存分词器** - 确保可重现性
6. **版本控制你的分词器** - 跟踪变化以确保可重现
7. **记录特殊token** - 对模型训练至关重要
