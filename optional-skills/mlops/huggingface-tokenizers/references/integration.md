# Transformers集成

HuggingFace分词器与Transformers库的使用完整指南。

## AutoTokenizer

加载分词器的最简单方法。

### 加载预训练分词器

```python
from transformers import AutoTokenizer

# 从HuggingFace Hub加载
tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")

# 检查是否使用快速分词器（基于Rust）
print(tokenizer.is_fast)  # True

# 访问底层tokenizers.Tokenizer
if tokenizer.is_fast:
    fast_tokenizer = tokenizer.backend_tokenizer
    print(type(fast_tokenizer))  # <class 'tokenizers.Tokenizer'>
```

### 快速与慢速分词器对比

| 特性                  | 快速（Rust）    | 慢速（Python） |
|--------------------------|----------------|---------------|
| 速度                    | 5-10倍更快   | 基线      |
| 对齐跟踪       | ✅ 完全支持 | ❌ 有限     |
| 批量处理         | ✅ 优化     | ⚠️ 较慢      |
| 偏移映射           | ✅ 是          | ❌ 否          |
| 安装             | `tokenizers`   | 内置      |

**尽可能使用快速分词器。**

### 检查可用分词器

```python
from transformers import TOKENIZER_MAPPING

# 列出所有快速分词器
for config_class, (slow, fast) in TOKENIZER_MAPPING.items():
    if fast is not None:
        print(f"{config_class.__name__}: {fast.__name__}")
```

## PreTrainedTokenizerFast

为transformers包装自定义分词器。

### 转换自定义分词器

```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer
from transformers import PreTrainedTokenizerFast

# 训练自定义分词器
tokenizer = Tokenizer(BPE())
trainer = BpeTrainer(
    vocab_size=30000,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"]
)
tokenizer.train(files=["corpus.txt"], trainer=trainer)

# 保存分词器
tokenizer.save("my-tokenizer.json")

# 为transformers包装
transformers_tokenizer = PreTrainedTokenizerFast(
    tokenizer_file="my-tokenizer.json",
    unk_token="[UNK]",
    sep_token="[SEP]",
    pad_token="[PAD]",
    cls_token="[CLS]",
    mask_token="[MASK]"
)

# 以transformers格式保存
transformers_tokenizer.save_pretrained("my-tokenizer")
```

**结果**：包含`tokenizer.json` + `tokenizer_config.json` + `special_tokens_map.json`的目录

### 像任何transformers分词器一样使用

```python
# 加载
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained("my-tokenizer")

# 使用所有transformers特性编码
outputs = tokenizer(
    "Hello world",
    padding="max_length",
    truncation=True,
    max_length=128,
    return_tensors="pt"
)

print(outputs.keys())
# dict_keys(['input_ids', 'token_type_ids', 'attention_mask'])
```

## 特殊token

### 默认特殊token

| 模型系列 | CLS/BOS | SEP/EOS       | PAD     | UNK     | MASK    |
|--------------|---------|---------------|---------|---------|---------|
| BERT         | [CLS]   | [SEP]         | [PAD]   | [UNK]   | [MASK]  |
| GPT-2        | -       | <\|endoftext\|> | <\|endoftext\|> | <\|endoftext\|> | -       |
| RoBERTa      | <s>     | </s>          | <pad>   | <unk>   | <mask>  |
| T5           | -       | </s>          | <pad>   | <unk>   | -       |

### 添加特殊token

```python
# 添加新的特殊token
special_tokens_dict = {
    "additional_special_tokens": ["<|image|>", "<|video|>", "<|audio|>"]
}

num_added_tokens = tokenizer.add_special_tokens(special_tokens_dict)
print(f"添加了 {num_added_tokens} 个token")

# 调整模型嵌入大小
model.resize_token_embeddings(len(tokenizer))

# 使用新token
text = "This is an image: <|image|>"
tokens = tokenizer.encode(text)
```

### 添加常规token

```python
# 添加领域特定token
new_tokens = ["COVID-19", "mRNA", "vaccine"]
num_added = tokenizer.add_tokens(new_tokens)

# 这些不是特殊token（需要时可分割）
tokenizer.add_tokens(new_tokens, special_tokens=False)

# 这些是特殊token（永不分割）
tokenizer.add_tokens(new_tokens, special_tokens=True)
```

## 编码和解码

### 基础编码

```python
# 单句
text = "Hello, how are you?"
encoded = tokenizer(text)

print(encoded)
# {'input_ids': [101, 7592, 1010, 2129, 2024, 2017, 1029, 102],
#  'token_type_ids': [0, 0, 0, 0, 0, 0, 0, 0],
#  'attention_mask': [1, 1, 1, 1, 1, 1, 1, 1]}
```

### 批量编码

```python
# 多句
texts = ["Hello world", "How are you?", "I am fine"]
encoded = tokenizer(texts, padding=True, truncation=True, max_length=10)

print(encoded['input_ids'])
# [[101, 7592, 2088, 102, 0, 0, 0, 0, 0, 0],
#  [101, 2129, 2024, 2017, 1029, 102, 0, 0, 0, 0],
#  [101, 1045, 2572, 2986, 102, 0, 0, 0, 0, 0]]
```

### 返回tensors

```python
# 返回PyTorch tensors
outputs = tokenizer("Hello world", return_tensors="pt")
print(outputs['input_ids'].shape)  # torch.Size([1, 5])

# 返回TensorFlow tensors
outputs = tokenizer("Hello world", return_tensors="tf")

# 返回NumPy数组
outputs = tokenizer("Hello world", return_tensors="np")

# 返回列表（默认）
outputs = tokenizer("Hello world", return_tensors=None)
```

### 解码

```python
# 解码token ID
ids = [101, 7592, 2088, 102]
text = tokenizer.decode(ids)
print(text)  # "[CLS] hello world [SEP]"

# 跳过特殊token
text = tokenizer.decode(ids, skip_special_tokens=True)
print(text)  # "hello world"

# 批量解码
batch_ids = [[101, 7592, 102], [101, 2088, 102]]
texts = tokenizer.batch_decode(batch_ids, skip_special_tokens=True)
print(texts)  # ["hello", "world"]
```

## 填充和截断

### 填充策略

```python
# 填充到批次中的最大长度
tokenizer(texts, padding="longest")

# 填充到模型最大长度
tokenizer(texts, padding="max_length", max_length=128)

# 不填充
tokenizer(texts, padding=False)

# 填充到值的倍数（为了高效计算）
tokenizer(texts, padding="max_length", max_length=128, pad_to_multiple_of=8)
# 结果：长度将为128（已经是8的倍数）
```

### 截断策略

```python
# 截断到最大长度
tokenizer(text, truncation=True, max_length=10)

# 仅截断第一个序列（对于句子对）
tokenizer(text1, text2, truncation="only_first", max_length=20)

# 仅截断第二个序列
tokenizer(text1, text2, truncation="only_second", max_length=20)

# 截断最长的优先（句子对默认）
tokenizer(text1, text2, truncation="longest_first", max_length=20)

# 不截断（如果太长则错误）
tokenizer(text, truncation=False)
```

### 长文档的步长

```python
# 对于长于max_length的文档
text = "Very long document " * 1000

# 带重叠编码
encodings = tokenizer(
    text,
    max_length=512,
    stride=128,          # 块之间的重叠
    truncation=True,
    return_overflowing_tokens=True,
    return_offsets_mapping=True
)

# 获取所有块
num_chunks = len(encodings['input_ids'])
print(f"分割成 {num_chunks} 个块")

# 每个块按步长重叠
for i, chunk in enumerate(encodings['input_ids']):
    print(f"块 {i}: {len(chunk)} 个token")
```

**用途**：长文档问答、滑动窗口推理

## 对齐和偏移

### 偏移映射

```python
# 获取每个token的字符偏移
encoded = tokenizer("Hello, world!", return_offsets_mapping=True)

for token, (start, end) in zip(
    encoded.tokens(),
    encoded['offset_mapping'][0]
):
    print(f"{token:10s} → [{start:2d}, {end:2d})")

# 输出:
# [CLS]      → [ 0,  0)
# Hello      → [ 0,  5)
# ,          → [ 5,  6)
# world      → [ 7, 12)
# !          → [12, 13)
# [SEP]      → [ 0,  0)
```

### 词ID

```python
# 获取每个token的词索引
encoded = tokenizer("Hello world", return_offsets_mapping=True)
word_ids = encoded.word_ids()

print(word_ids)
# [None, 0, 1, None]
# None = 特殊token，0 = 第一个词，1 = 第二个词
```

**用途**：Token分类（NER、POS标注）

### 字符到token映射

```python
text = "Machine learning is awesome"
encoded = tokenizer(text, return_offsets_mapping=True)

# 查找字符位置的token
char_pos = 8  # "learning"中的"l"
token_idx = encoded.char_to_token(char_pos)

print(f"字符 {char_pos} 在token {token_idx}: {encoded.tokens()[token_idx]}")
# 字符 8 在 token 2: learning
```

**用途**：问答（将答案字符跨度映射到token）

### 序列对

```python
# 编码句子对
encoded = tokenizer("Question here", "Answer here", return_offsets_mapping=True)

# 获取序列ID（每个token属于哪个序列）
sequence_ids = encoded.sequence_ids()
print(sequence_ids)
# [None, 0, 0, 0, None, 1, 1, 1, None]
# None = 特殊token，0 = 问题，1 = 答案
```

## 模型集成

### 与transformers模型使用

```python
from transformers import AutoModel, AutoTokenizer
import torch

# 加载模型和分词器
model = AutoModel.from_pretrained("bert-base-uncased")
tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")

# 分词
text = "Hello world"
inputs = tokenizer(text, return_tensors="pt")

# 前向传播
with torch.no_grad():
    outputs = model(**inputs)

# 获取嵌入
last_hidden_state = outputs.last_hidden_state
print(last_hidden_state.shape)  # [1, seq_len, hidden_size]
```

### 带自定义分词器的自定义模型

```python
from transformers import BertConfig, BertModel

# 训练自定义分词器
from tokenizers import Tokenizer, models, trainers
tokenizer = Tokenizer(models.BPE())
trainer = trainers.BpeTrainer(vocab_size=30000)
tokenizer.train(files=["data.txt"], trainer=trainer)

# 为transformers包装
from transformers import PreTrainedTokenizerFast
fast_tokenizer = PreTrainedTokenizerFast(
    tokenizer_object=tokenizer,
    unk_token="[UNK]",
    pad_token="[PAD]"
)

# 用自定义词表大小创建模型
config = BertConfig(vocab_size=30000)
model = BertModel(config)

# 一起使用
inputs = fast_tokenizer("Hello world", return_tensors="pt")
outputs = model(**inputs)
```

### 一起保存和加载

```python
# 保存两者
model.save_pretrained("my-model")
tokenizer.save_pretrained("my-model")

# 目录结构:
# my-model/
#   ├── config.json
#   ├── pytorch_model.bin
#   ├── tokenizer.json
#   ├── tokenizer_config.json
#   └── special_tokens_map.json

# 加载两者
from transformers import AutoModel, AutoTokenizer

model = AutoModel.from_pretrained("my-model")
tokenizer = AutoTokenizer.from_pretrained("my-model")
```

## 高级特性

### 多模态分词

```python
from transformers import AutoTokenizer

# LLaVA风格（图像+文本）
tokenizer = AutoTokenizer.from_pretrained("llava-hf/llava-1.5-7b-hf")

# 添加图像占位符token
tokenizer.add_special_tokens({"additional_special_tokens": ["<image>"]})

# 在提示中使用
text = "Describe this image: <image>"
inputs = tokenizer(text, return_tensors="pt")
```

### 模板格式化

```python
# 聊天模板
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"},
    {"role": "assistant", "content": "Hi! How can I help?"},
    {"role": "user", "content": "What's the weather?"}
]

# 应用聊天模板（如分词器有）
if hasattr(tokenizer, "apply_chat_template"):
    text = tokenizer.apply_chat_template(messages, tokenize=False)
    inputs = tokenizer(text, return_tensors="pt")
```

### 自定义模板

```python
from transformers import PreTrainedTokenizerFast

tokenizer = PreTrainedTokenizerFast(tokenizer_file="tokenizer.json")

# 定义聊天模板
tokenizer.chat_template = """
{%- for message in messages %}
    {%- if message['role'] == 'system' %}
        System: {{ message['content'] }}\n
    {%- elif message['role'] == 'user' %}
        User: {{ message['content'] }}\n
    {%- elif message['role'] == 'assistant' %}
        Assistant: {{ message['content'] }}\n
    {%- endif %}
{%- endfor %}
Assistant:
"""

# 使用模板
text = tokenizer.apply_chat_template(messages, tokenize=False)
```

## 性能优化

### 批量处理

```python
# 高效处理大型数据集
from datasets import load_dataset

dataset = load_dataset("imdb", split="train[:1000]")

# 批量分词
def tokenize_function(examples):
    return tokenizer(
        examples["text"],
        padding="max_length",
        truncation=True,
        max_length=512
    )

# 在数据集上映射（批量）
tokenized_dataset = dataset.map(
    tokenize_function,
    batched=True,
    batch_size=1000,
    num_proc=4  # 并行处理
)
```

### 缓存

```python
# 为重复分词启用缓存
tokenizer = AutoTokenizer.from_pretrained(
    "bert-base-uncased",
    use_fast=True,
    cache_dir="./cache"  # 缓存分词器文件
)

# 使用缓存分词
from functools import lru_cache

@lru_cache(maxsize=10000)
def cached_tokenize(text):
    return tuple(tokenizer.encode(text))

# 对重复输入重用缓存结果
```

### 内存效率

```python
# 对于非常大的数据集，使用流式
from datasets import load_dataset

dataset = load_dataset("pile", split="train", streaming=True)

def process_batch(batch):
    # 分词
    tokens = tokenizer(batch["text"], truncation=True, max_length=512)

    # 处理tokens...

    return tokens

# 分块处理（内存高效）
for batch in dataset.batch(batch_size=1000):
    processed = process_batch(batch)
```

## 故障排除

### 问题：分词器不是快速的

**症状**：
```python
tokenizer.is_fast  # False
```

**解决方案**：安装tokenizers库
```bash
pip install tokenizers
```

### 问题：特殊token不工作

**症状**：特殊token被分割成子词

**解决方案**：添加为特殊token，而非常规token
```python
# 错误
tokenizer.add_tokens(["<|image|>"])

# 正确
tokenizer.add_special_tokens({"additional_special_tokens": ["<|image|>"]})
```

### 问题：偏移映射不可用

**症状**：
```python
tokenizer("text", return_offsets_mapping=True)
# 错误: return_offsets_mapping不支持
```

**解决方案**：使用快速分词器
```python
from transformers import AutoTokenizer

# 加载快速版本
tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased", use_fast=True)
```

### 问题：填充不一致

**症状**：某些序列填充了，其他没有

**解决方案**：指定填充策略
```python
# 显式填充
tokenizer(
    texts,
    padding="max_length",  # 或 "longest"
    max_length=128
)
```

## 最佳实践

1. **始终使用快速分词器**：
   - 5-10倍更快
   - 完全对齐跟踪
   - 更好的批量处理

2. **与模型一起保存分词器**：
   - 确保可重现性
   - 防止版本不匹配

3. **对数据集使用批量处理**：
   - 使用`.map(batched=True)`分词
   - 设置`num_proc`以实现并行

4. **为重复输入启用缓存**：
   - 使用`lru_cache`进行推理
   - 用`cache_dir`缓存分词器文件

5. **正确处理特殊token**：
   - 使用`add_special_tokens()`处理永不分割的token
   - 添加token后调整嵌入大小

6. **为下游任务测试对齐**：
   - 验证`offset_mapping`正确
   - 在样本上测试`char_to_token()`

7. **版本控制分词器配置**：
   - 保存`tokenizer_config.json`
   - 记录自定义模板
   - 跟踪词表变化
