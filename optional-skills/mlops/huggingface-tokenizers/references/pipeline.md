# 分词管道组件

标准化器、预分词器、模型、后处理器和解码器的完整指南。

## 管道概述

**完整分词管道**：
```
原始文本
  ↓
标准化（清理、小写）
  ↓
预分词（分割成单词）
  ↓
模型（应用BPE/WordPiece/Unigram）
  ↓
后处理（添加特殊token）
  ↓
Token ID
```

**解码反转这个过程**：
```
Token ID
  ↓
解码器（处理特殊编码）
  ↓
原始文本
```

## 标准化器

清理和标准化输入文本。

### 常见标准化器

**小写**：
```python
from tokenizers.normalizers import Lowercase

tokenizer.normalizer = Lowercase()

# 输入: "Hello WORLD"
# 输出: "hello world"
```

**Unicode标准化**：
```python
from tokenizers.normalizers import NFD, NFC, NFKD, NFKC

# NFD：规范分解
tokenizer.normalizer = NFD()
# "é" → "e" + "́" (分离的字符)

# NFC：规范组合（默认）
tokenizer.normalizer = NFC()
# "e" + "́" → "é" (组合)

# NFKD：兼容性分解
tokenizer.normalizer = NFKD()
# "ﬁ" → "f" + "i"

# NFKC：兼容性组合
tokenizer.normalizer = NFKC()
# 最激进标准化
```

**去除重音**：
```python
from tokenizers.normalizers import StripAccents

tokenizer.normalizer = StripAccents()

# 输入: "café"
# 输出: "cafe"
```

**空白处理**：
```python
from tokenizers.normalizers import Strip, StripAccents

# 移除前导/尾随空白
tokenizer.normalizer = Strip()

# 输入: "  hello  "
# 输出: "hello"
```

**替换模式**：
```python
from tokenizers.normalizers import Replace

# 用空格替换换行符
tokenizer.normalizer = Replace("\\n", " ")

# 输入: "hello\nworld"
# 输出: "hello world"
```

### 组合标准化器

```python
from tokenizers.normalizers import Sequence, NFD, Lowercase, StripAccents

# BERT风格标准化
tokenizer.normalizer = Sequence([
    NFD(),           # Unicode分解
    Lowercase(),     # 转换为小写
    StripAccents()   # 移除重音
])

# 输入: "Café au Lait"
# NFD后: "Café au Lait" (e + ́)
# Lowercase后: "café au lait"
# StripAccents后: "cafe au lait"
```

### 使用场景示例

**不区分大小写的模型（BERT）**：
```python
from tokenizers.normalizers import BertNormalizer

# 一体化BERT标准化
tokenizer.normalizer = BertNormalizer(
    clean_text=True,        # 移除控制字符
    handle_chinese_chars=True,  # 在中文周围添加空格
    strip_accents=True,     # 移除重音
    lowercase=True          # 小写
)
```

**区分大小写的模型（GPT-2）**：
```python
# 最小标准化
tokenizer.normalizer = NFC()  # 仅标准化Unicode
```

**多语言（mBERT）**：
```python
# 保留脚本，标准化形式
tokenizer.normalizer = NFKC()
```

## 预分词器

在分词前将文本分割成类似单词的单元。

### 空白分割

```python
from tokenizers.pre_tokenizers import Whitespace

tokenizer.pre_tokenizer = Whitespace()

# 输入: "Hello world! How are you?"
# 输出: [("Hello", (0, 5)), ("world!", (6, 12)), ("How", (13, 16)), ("are", (17, 20)), ("you?", (21, 25))]
```

### 标点隔离

```python
from tokenizers.pre_tokenizers import Punctuation

tokenizer.pre_tokenizer = Punctuation()

# 输入: "Hello, world!"
# 输出: [("Hello", ...), (",", ...), ("world", ...), ("!", ...)]
```

### 字节级（GPT-2）

```python
from tokenizers.pre_tokenizers import ByteLevel

tokenizer.pre_tokenizer = ByteLevel(add_prefix_space=True)

# 输入: "Hello world"
# 输出: 带空格前缀Ġ的字节级token
# [("ĠHello", ...), ("Ġworld", ...)]
```

**关键特性**：处理所有Unicode字符（256字节组合）

### Metaspace（SentencePiece）

```python
from tokenizers.pre_tokenizers import Metaspace

tokenizer.pre_tokenizer = Metaspace(replacement="▁", add_prefix_space=True)

# 输入: "Hello world"
# 输出: [("▁Hello", ...), ("▁world", ...)]
```

**使用者**：T5、ALBERT（通过SentencePiece）

### 数字分割

```python
from tokenizers.pre_tokenizers import Digits

# 单独分割数字
tokenizer.pre_tokenizer = Digits(individual_digits=True)

# 输入: "Room 123"
# 输出: [("Room", ...), ("1", ...), ("2", ...), ("3", ...)]

# 保持数字在一起
tokenizer.pre_tokenizer = Digits(individual_digits=False)

# 输入: "Room 123"
# 输出: [("Room", ...), ("123", ...)]
```

### BERT预分词器

```python
from tokenizers.pre_tokenizers import BertPreTokenizer

tokenizer.pre_tokenizer = BertPreTokenizer()

# 按空白和标点分割，保留中文
# 输入: "Hello, 世界!"
# 输出: [("Hello", ...), (",", ...), ("世", ...), ("界", ...), ("!", ...)]
```

### 组合预分词器

```python
from tokenizers.pre_tokenizers import Sequence, Whitespace, Punctuation

tokenizer.pre_tokenizer = Sequence([
    Whitespace(),     # 先按空白分割
    Punctuation()     # 然后隔离标点
])

# 输入: "Hello, world!"
# Whitespace后: [("Hello,", ...), ("world!", ...)]
# Punctuation后: [("Hello", ...), (",", ...), ("world", ...), ("!", ...)]
```

### 预分词器对比

| 预分词器     | 使用场景                        | 示例                                    |
|-------------------|---------------------------------|--------------------------------------------|
| Whitespace        | 简单英文                  | "Hello world" → ["Hello", "world"]         |
| Punctuation       | 隔离符号                 | "world!" → ["world", "!"]                  |
| ByteLevel         | 多语言、emoji            | "🌍" → 字节token                          |
| Metaspace         | SentencePiece风格             | "Hello" → ["▁Hello"]                       |
| BertPreTokenizer  | BERT风格（中文感知）          | "世界" → ["世", "界"]                        |
| Digits            | 处理数字                  | "123" → ["1", "2", "3"] 或 ["123"]        |

## 模型

核心分词算法。

### BPE模型

```python
from tokenizers.models import BPE

model = BPE(
    vocab=None,           # 或提供预构建词表
    merges=None,          # 或提供合并规则
    unk_token="[UNK]",    # 未知token
    continuing_subword_prefix="",
    end_of_word_suffix="",
    fuse_unk=False        # 保持未知token分离
)

tokenizer = Tokenizer(model)
```

**参数**：
- `vocab`：token → id的字典
- `merges`：合并规则列表`["a b", "ab c"]`
- `unk_token`：未知词的token
- `continuing_subword_prefix`：子词前缀（GPT-2为空）
- `end_of_word_suffix`：最后一个子词的后缀（GPT-2为空）

### WordPiece模型

```python
from tokenizers.models import WordPiece

model = WordPiece(
    vocab=None,
    unk_token="[UNK]",
    max_input_chars_per_word=100,  # 最大词长度
    continuing_subword_prefix="##"  # BERT风格前缀
)

tokenizer = Tokenizer(model)
```

**关键区别**：使用`##`前缀表示连续的子词。

### Unigram模型

```python
from tokenizers.models import Unigram

model = Unigram(
    vocab=None,  # (token, score)元组列表
    unk_id=0,    # 未知token的ID
    byte_fallback=False  # 如果没有匹配则回退到字节
)

tokenizer = Tokenizer(model)
```

**概率性**：选择最高概率的分词。

### WordLevel模型

```python
from tokenizers.models import WordLevel

# 简单词到ID映射（无子词）
model = WordLevel(
    vocab=None,
    unk_token="[UNK]"
)

tokenizer = Tokenizer(model)
```

**警告**：需要巨大词表（每个词一个token）。

## 后处理器

添加特殊token并格式化输出。

### 模板处理

**BERT风格**（`[CLS] sentence [SEP]`）：
```python
from tokenizers.processors import TemplateProcessing

tokenizer.post_processor = TemplateProcessing(
    single="[CLS] $A [SEP]",
    pair="[CLS] $A [SEP] $B [SEP]",
    special_tokens=[
        ("[CLS]", 101),
        ("[SEP]", 102),
    ],
)

# 单句
output = tokenizer.encode("Hello world")
# [101, ..., 102]  ([CLS] hello world [SEP])

# 句子对
output = tokenizer.encode("Hello", "world")
# [101, ..., 102, ..., 102]  ([CLS] hello [SEP] world [SEP])
```

**GPT-2风格**（`sentence <|endoftext|>`）：
```python
tokenizer.post_processor = TemplateProcessing(
    single="$A <|endoftext|>",
    special_tokens=[
        ("<|endoftext|>", 50256),
    ],
)
```

**RoBERTa风格**（`<s> sentence </s>`）：
```python
tokenizer.post_processor = TemplateProcessing(
    single="<s> $A </s>",
    pair="<s> $A </s> </s> $B </s>",
    special_tokens=[
        ("<s>", 0),
        ("</s>", 2),
    ],
)
```

**T5风格**（无特殊token）：
```python
# T5不通过后处理器添加特殊token
tokenizer.post_processor = None
```

### RobertaProcessing

```python
from tokenizers.processors import RobertaProcessing

tokenizer.post_processor = RobertaProcessing(
    sep=("</s>", 2),
    cls=("<s>", 0),
    add_prefix_space=True,  # 在第一个token前添加空格
    trim_offsets=True       # 从偏移量中修整前导空格
)
```

### ByteLevelProcessing

```python
from tokenizers.processors import ByteLevel as ByteLevelProcessing

tokenizer.post_processor = ByteLevelProcessing(
    trim_offsets=True  # 从偏移量中移除Ġ
)
```

## 解码器

将Token ID转换回文本。

### 字节级解码器

```python
from tokenizers.decoders import ByteLevel

tokenizer.decoder = ByteLevel()

# 处理字节级token
# ["ĠHello", "Ġworld"] → "Hello world"
```

### WordPiece解码器

```python
from tokenizers.decoders import WordPiece

tokenizer.decoder = WordPiece(prefix="##")

# 移除##前缀并连接
# ["token", "##ization"] → "tokenization"
```

### Metaspace解码器

```python
from tokenizers.decoders import Metaspace

tokenizer.decoder = Metaspace(replacement="▁", add_prefix_space=True)

# 将▁转换回空格
# ["▁Hello", "▁world"] → "Hello world"
```

### BPEDecoder

```python
from tokenizers.decoders import BPEDecoder

tokenizer.decoder = BPEDecoder(suffix="</w>")

# 移除后缀并连接
# ["token", "ization</w>"] → "tokenization"
```

### 序列解码器

```python
from tokenizers.decoders import Sequence, ByteLevel, Strip

tokenizer.decoder = Sequence([
    ByteLevel(),      # 先解码字节级
    Strip(' ', 1, 1)  # 修整前导/尾随空格
])
```

## 完整管道示例

### BERT分词器

```python
from tokenizers import Tokenizer
from tokenizers.models import WordPiece
from tokenizers.normalizers import BertNormalizer
from tokenizers.pre_tokenizers import BertPreTokenizer
from tokenizers.processors import TemplateProcessing
from tokenizers.decoders import WordPiece as WordPieceDecoder

# 模型
tokenizer = Tokenizer(WordPiece(unk_token="[UNK]"))

# 标准化
tokenizer.normalizer = BertNormalizer(lowercase=True)

# 预分词
tokenizer.pre_tokenizer = BertPreTokenizer()

# 后处理
tokenizer.post_processor = TemplateProcessing(
    single="[CLS] $A [SEP]",
    pair="[CLS] $A [SEP] $B [SEP]",
    special_tokens=[("[CLS]", 101), ("[SEP]", 102)],
)

# 解码器
tokenizer.decoder = WordPieceDecoder(prefix="##")

# 启用填充
tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

# 启用截断
tokenizer.enable_truncation(max_length=512)
```

### GPT-2分词器

```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.normalizers import NFC
from tokenizers.pre_tokenizers import ByteLevel
from tokenizers.decoders import ByteLevel as ByteLevelDecoder
from tokenizers.processors import TemplateProcessing

# 模型
tokenizer = Tokenizer(BPE())

# 标准化（最小）
tokenizer.normalizer = NFC()

# 字节级预分词
tokenizer.pre_tokenizer = ByteLevel(add_prefix_space=False)

# 后处理
tokenizer.post_processor = TemplateProcessing(
    single="$A <|endoftext|>",
    special_tokens=[("<|endoftext|>", 50256)],
)

# 字节级解码器
tokenizer.decoder = ByteLevelDecoder()
```

### T5分词器（SentencePiece风格）

```python
from tokenizers import Tokenizer
from tokenizers.models import Unigram
from tokenizers.normalizers import NFKC
from tokenizers.pre_tokenizers import Metaspace
from tokenizers.decoders import Metaspace as MetaspaceDecoder

# 模型
tokenizer = Tokenizer(Unigram())

# 标准化
tokenizer.normalizer = NFKC()

# Metaspace预分词
tokenizer.pre_tokenizer = Metaspace(replacement="▁", add_prefix_space=True)

# 无后处理（T5不添加CLS/SEP）
tokenizer.post_processor = None

# Metaspace解码器
tokenizer.decoder = MetaspaceDecoder(replacement="▁", add_prefix_space=True)
```

## 对齐跟踪

跟踪原始文本中的token位置。

### 基础对齐

```python
text = "Hello, world!"
output = tokenizer.encode(text)

for token, (start, end) in zip(output.tokens, output.offsets):
    print(f"{token:10s} → [{start:2d}, {end:2d}): {text[start:end]!r}")

# 输出:
# [CLS]      → [ 0,  0): ''
# hello      → [ 0,  5): 'Hello'
# ,          → [ 5,  6): ','
# world      → [ 7, 12): 'world'
# !          → [12, 13): '!'
# [SEP]      → [ 0,  0): ''
```

### 词级对齐

```python
# 获取word_ids（每个token属于哪个词）
encoding = tokenizer.encode("Hello world")
word_ids = encoding.word_ids

print(word_ids)
# [None, 0, 0, 1, None]
# None = 特殊token，0 = 第一个词，1 = 第二个词
```

**用途**：Token分类（NER）
```python
# 将预测对齐到词
predictions = ["O", "B-PER", "I-PER", "O", "O"]
word_predictions = {}

for token_idx, word_idx in enumerate(encoding.word_ids):
    if word_idx is not None and word_idx not in word_predictions:
        word_predictions[word_idx] = predictions[token_idx]

print(word_predictions)
# {0: "B-PER", 1: "O"}  # 第一个词是PERSON，第二个是OTHER
```

### 跨度对齐

```python
# 查找字符跨度的token跨度
text = "Machine learning is awesome"
char_start, char_end = 8, 16  # "learning"

encoding = tokenizer.encode(text)

# 查找token跨度
token_start = encoding.char_to_token(char_start)
token_end = encoding.char_to_token(char_end - 1) + 1

print(f"Tokens {token_start}:{token_end} = {encoding.tokens[token_start:token_end]}")
# Tokens 2:3 = ['learning']
```

**用途**：问答（提取答案跨度）

## 自定义组件

### 自定义标准化器

```python
from tokenizers import NormalizedString, Normalizer

class CustomNormalizer:
    def normalize(self, normalized: NormalizedString):
        # 自定义标准化逻辑
        normalized.lowercase()
        normalized.replace("  ", " ")  # 替换双空格

# 使用自定义标准化器
tokenizer.normalizer = CustomNormalizer()
```

### 自定义预分词器

```python
from tokenizers import PreTokenizedString

class CustomPreTokenizer:
    def pre_tokenize(self, pretok: PreTokenizedString):
        # 自定义预分词逻辑
        pretok.split(lambda i, char: char.isspace())

tokenizer.pre_tokenizer = CustomPreTokenizer()
```

## 故障排除

### 问题：对齐偏移不匹配

**症状**：偏移与原始文本不匹配
```python
text = "  hello"  # 前导空格
offsets = [(0, 5)]  # 期望"  hel"
```

**解决方案**：检查标准化是否修整空格
```python
# 保留偏移
tokenizer.normalizer = Sequence([
    Strip(),  # 这会改变偏移！
])

# 改用post-processor中的trim_offsets
tokenizer.post_processor = ByteLevelProcessing(trim_offsets=True)
```

### 问题：特殊token未添加

**症状**：输出中没有[CLS]或[SEP]

**解决方案**：检查是否设置了post-processor
```python
tokenizer.post_processor = TemplateProcessing(
    single="[CLS] $A [SEP]",
    special_tokens=[("[CLS]", 101), ("[SEP]", 102)],
)
```

### 问题：解码不正确

**症状**：解码文本有##或▁

**解决方案**：设置正确的解码器
```python
# 对于WordPiece
tokenizer.decoder = WordPieceDecoder(prefix="##")

# 对于SentencePiece
tokenizer.decoder = MetaspaceDecoder(replacement="▁")
```

## 最佳实践

1. **匹配管道到模型架构**：
   - BERT → BertNormalizer + BertPreTokenizer + WordPiece
   - GPT-2 → NFC + ByteLevel + BPE
   - T5 → NFKC + Metaspace + Unigram

2. **在样本输入上测试管道**：
   - 检查标准化不过度标准化
   - 验证预分词正确分割
   - 确保解码重构文本

3. **为下游任务保留对齐**：
   - 使用`trim_offsets`而非在标准化器中修整
   - 在样本跨度上测试`char_to_token()`

4. **记录你的管道**：
   - 保存完整的分词器配置
   - 记录特殊token
   - 注明任何自定义组件
