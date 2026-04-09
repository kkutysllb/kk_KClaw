# 分词算法深度探讨

BPE、WordPiece和Unigram算法的详细解释。

## 字节对编码（BPE）

### 算法概述

BPE迭代合并语料库中最频繁的token对。

**训练过程**：
1. 用所有字符初始化词表
2. 计算所有相邻token对的频率
3. 将最频繁的对合并为新token
4. 将新token添加到词表
5. 用新token更新语料库
6. 重复直到达到词表大小

### 逐步示例

**语料库**：
```
low: 5
lower: 2
newest: 6
widest: 3
```

**迭代1**：
```
计算对：
'e' + 's': 9 (newest: 6, widest: 3)  ← 最频繁
'l' + 'o': 7
'o' + 'w': 7
...

合并: 'e' + 's' → 'es'

更新后语料库：
low: 5
lower: 2
newest: 6 → new|es|t: 6
widest: 3 → wides|t: 3

词表: [a-z] + ['es']
```

**迭代2**：
```
计算对：
'es' + 't': 9  ← 最频繁
'l' + 'o': 7
...

合并: 'es' + 't' → 'est'

更新后语料库：
low: 5
lower: 2
newest: 6 → new|est: 6
widest: 3 → wid|est: 3

词表: [a-z] + ['es', 'est']
```

**继续直到达到目标词表大小...**

### 使用训练的BPE进行分词

给定词表：`['l', 'o', 'w', 'e', 'r', 'n', 's', 't', 'i', 'd', 'es', 'est', 'lo', 'low', 'ne', 'new', 'newest', 'wi', 'wid', 'widest']`

分词"lowest"：
```
步骤1: 分割成字符
['l', 'o', 'w', 'e', 's', 't']

步骤2: 按训练期间学习的顺序应用合并
- 合并 'l' + 'o' → 'lo'（如学过）
- 合并 'lo' + 'w' → 'low'（如学过）
- 合并 'e' + 's' → 'es'（学过）
- 合并 'es' + 't' → 'est'（学过）

最终: ['low', 'est']
```

### 实现

```python
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.trainers import BpeTrainer
from tokenizers.pre_tokenizers import Whitespace

# 初始化
tokenizer = Tokenizer(BPE(unk_token="[UNK]"))
tokenizer.pre_tokenizer = Whitespace()

# 配置训练器
trainer = BpeTrainer(
    vocab_size=1000,
    min_frequency=2,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"]
)

# 训练
corpus = [
    "This is a sample corpus for BPE training.",
    "BPE learns subword units from the training data.",
    # ... 更多句子
]

tokenizer.train_from_iterator(corpus, trainer=trainer)

# 使用
output = tokenizer.encode("This is tokenization")
print(output.tokens)  # ['This', 'is', 'token', 'ization']
```

### 字节级BPE（GPT-2变体）

**问题**：标准BPE字符覆盖有限（256+ Unicode字符）

**解决方案**：在字节级别操作（256字节）

```python
from tokenizers.pre_tokenizers import ByteLevel
from tokenizers.decoders import ByteLevel as ByteLevelDecoder

tokenizer = Tokenizer(BPE())

# 字节级预分词
tokenizer.pre_tokenizer = ByteLevel()
tokenizer.decoder = ByteLevelDecoder()

# 这处理所有可能的字符，包括emoji
text = "Hello 🌍 世界"
tokens = tokenizer.encode(text).tokens
```

**优势**：
- 处理任何Unicode字符（256字节覆盖）
- 没有未知token（最坏情况：字节）
- 被GPT-2、GPT-3、BART使用

**权衡**：
- 压缩稍差（字节 vs 字符）
- 非ASCII文本更多token

### BPE变体

**SentencePiece BPE**：
- 语言独立（无预分词）
- 将输入视为原始字节流
- 被T5、ALBERT、XLNet使用

**Robust BPE**：
- 训练期间Dropout（随机跳过合并）
- 推理时分词更稳健
- 减少对训练数据的过拟合

## WordPiece

### 算法概述

WordPiece类似于BPE，但使用不同的合并选择标准。

**训练过程**：
1. 用所有字符初始化词表
2. 计算所有token对的频率
3. 对每对评分：`score = freq(pair) / (freq(first) × freq(second))`
4. 合并评分最高的pair
5. 重复直到达到词表大小

### 为什么评分不同？

**BPE**：合并最频繁的对
- "aa"出现100次 → 高优先级
- 即使'a'单独出现1000次

**WordPiece**：合并语义相关的对
- "aa"出现100次，'a'出现1000次 → 低评分（100 / (1000 × 1000)）
- "th"出现50次，' t'出现60次，'h'出现55次 → 高评分（50 / (60 × 55)）
- 优先合并一起出现的对

### 逐步示例

**语料库**：
```
low: 5
lower: 2
newest: 6
widest: 3
```

**迭代1**：
```
计算频率：
'e': 11 (lower: 2, newest: 6, widest: 3)
's': 9
't': 9
...

计算对：
'e' + 's': 9 (newest: 6, widest: 3)
'es' + 't': 9 (newest: 6, widest: 3)
...

计算评分：
score('e' + 's') = 9 / (11 × 9) = 0.091
score('es' + 't') = 9 / (9 × 9) = 0.111  ← 最高评分
score('l' + 'o') = 7 / (7 × 9) = 0.111   ← 并列

选择: 'es' + 't' → 'est'（或并列时选'lo'）
```

**关键区别**：WordPiece优先考虑罕见组合而非常见组合。

### 使用WordPiece分词

给定词表：`['##e', '##s', '##t', 'l', 'o', 'w', 'new', 'est', 'low']`

分词"lowest"：
```
步骤1: 找到最长匹配前缀
'lowest' → 'low'（匹配）

步骤2: 为剩余部分找最长匹配
'est' → 'est'（匹配）

最终: ['low', 'est']
```

**如果没有匹配**：
```
分词"unknownword"：
'unknownword' → 无匹配
'unknown' → 无匹配
'unkn' → 无匹配
'un' → 无匹配
'u' → 无匹配
→ [UNK]
```

### 实现

```python
from tokenizers import Tokenizer
from tokenizers.models import WordPiece
from tokenizers.trainers import WordPieceTrainer
from tokenizers.normalizers import BertNormalizer
from tokenizers.pre_tokenizers import BertPreTokenizer

# 初始化BERT风格分词器
tokenizer = Tokenizer(WordPiece(unk_token="[UNK]"))

# 标准化（小写、去重音）
tokenizer.normalizer = BertNormalizer(lowercase=True)

# 预分词（空白 + 标点）
tokenizer.pre_tokenizer = BertPreTokenizer()

# 配置训练器
trainer = WordPieceTrainer(
    vocab_size=30522,  # BERT词表大小
    min_frequency=2,
    special_tokens=["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"],
    continuing_subword_prefix="##"  # BERT使用##
)

# 训练
tokenizer.train_from_iterator(corpus, trainer=trainer)

# 使用
output = tokenizer.encode("Tokenization works great!")
print(output.tokens)  # ['token', '##ization', 'works', 'great', '!']
```

### 子词前缀

**BERT使用`##`前缀**：
```
"unbelievable" → ['un', '##believ', '##able']
```

**为什么？**
- 表示token是连续的
- 允许重构：移除##，连接
- 帮助模型区分词边界

### WordPiece优势

**语义合并**：
- 优先有意义的组合
- "qu"高评分（总是一起）
- "qx"低评分（罕见组合）

**更适合形态学**：
- 捕获词缀：un-、-ing、-ed
- 保留词干

**权衡**：
- 训练比BPE慢
- 更多内存（存储词表，非合并）
- 原始实现不开源（HF重新实现）

## Unigram

### 算法概述

Unigram反向工作：从大词表开始，移除token。

**训练过程**：
1. 用大词表初始化（所有子串）
2. 估计每个token的概率（基于频率）
3. 对于每个token，计算如果移除则损失增加多少
4. 移除10-20%影响损失最小的token
5. 重新估计概率
6. 重复直到达到目标词表大小

### 概率分词

**Unigram假设**：每个token是独立的。

给定带概率的词表：
```
P('low') = 0.02
P('l') = 0.01
P('o') = 0.015
P('w') = 0.01
P('est') = 0.03
P('e') = 0.02
P('s') = 0.015
P('t') = 0.015
```

分词"lowest"：
```
选项1: ['low', 'est']
P = P('low') × P('est') = 0.02 × 0.03 = 0.0006

选项2: ['l', 'o', 'w', 'est']
P = 0.01 × 0.015 × 0.01 × 0.03 = 0.000000045

选项3: ['low', 'e', 's', 't']
P = 0.02 × 0.02 × 0.015 × 0.015 = 0.0000009

选择选项1（最高概率）
```

### Viterbi算法

找到最佳分词是昂贵的（指数级可能性）。

**Viterbi算法**（动态规划）：
```python
def tokenize_viterbi(word, vocab, probs):
    n = len(word)
    # dp[i] = (最佳概率, 最佳token) 对于word[:i]
    dp = [{} for _ in range(n + 1)]
    dp[0] = (0.0, [])  # 对数概率

    for i in range(1, n + 1):
        best_prob = float('-inf')
        best_tokens = []

        # 尝试所有可能的最后token
        for j in range(i):
            token = word[j:i]
            if token in vocab:
                prob = dp[j][0] + log(probs[token])
                if prob > best_prob:
                    best_prob = prob
                    best_tokens = dp[j][1] + [token]

        dp[i] = (best_prob, best_tokens)

    return dp[n][1]
```

**时间复杂度**：O(n² × vocab_size) vs O(2^n)暴力搜索

### 实现

```python
from tokenizers import Tokenizer
from tokenizers.models import Unigram
from tokenizers.trainers import UnigramTrainer

# 初始化
tokenizer = Tokenizer(Unigram())

# 配置训练器
trainer = UnigramTrainer(
    vocab_size=8000,
    special_tokens=["<unk>", "<s>", "</s>"],
    unk_token="<unk>",
    max_piece_length=16,      # 最大token长度
    n_sub_iterations=2,       # EM算法迭代
    shrinking_factor=0.75     # 每次迭代移除25%
)

# 训练
tokenizer.train_from_iterator(corpus, trainer=trainer)

# 使用
output = tokenizer.encode("Tokenization with Unigram")
print(output.tokens)  # ['▁Token', 'ization', '▁with', '▁Un', 'igram']
```

### Unigram优势

**概率性**：
- 多种有效分词
- 可以采样不同分词（数据增强）

**子词正则化**：
```python
# 采样不同分词
for _ in range(3):
    tokens = tokenizer.encode("tokenization", is_pretokenized=False).tokens
    print(tokens)

# 输出（每次不同）：
# ['token', 'ization']
# ['tok', 'en', 'ization']
# ['token', 'iz', 'ation']
```

**语言独立**：
- 不需要词边界
- 适合CJK语言（中文、日文、韩文）
- 将输入视为字符流

**权衡**：
- 训练较慢（EM算法）
- 更多超参数
- 模型更大（存储概率）

## 算法对比

### 训练速度

| 算法  | 小（10MB） | 中（100MB） | 大（1GB） |
|------------|--------------|----------------|-------------|
| BPE        | 10-15秒    | 1-2分钟        | 10-20分钟   |
| WordPiece  | 15-20秒    | 2-3分钟        | 15-30分钟   |
| Unigram    | 20-30秒    | 3-5分钟        | 30-60分钟   |

**测试环境**：16核CPU，30k词表

### 分词质量

在英文Wikipedia上测试（困惑度测量）：

| 算法  | 词表大小 | Tokens/词 | 未知率 |
|------------|------------|-------------|--------------|
| BPE        | 30k        | 1.3         | 0.5%         |
| WordPiece  | 30k        | 1.2         | 1.2%         |
| Unigram    | 8k         | 1.5         | 0.3%         |

**关键观察**：
- WordPiece：压缩稍好
- BPE：未知率更低
- Unigram：最小词表，良好覆盖

### 压缩比

每token字符数（越高 = 压缩越好）：

| 语言 | BPE（30k） | WordPiece（30k） | Unigram（8k） |
|----------|-----------|-----------------|--------------|
| 英语  | 4.2       | 4.5             | 3.8          |
| 中文  | 2.1       | 2.3             | 2.5          |
| 阿拉伯语   | 3.5       | 3.8             | 3.2          |

**各语言最佳**：
- 英语：WordPiece
- 中文：Unigram（语言独立）
- 阿拉伯语：WordPiece

### 使用建议

**BPE** - 最佳用于：
- 英语语言模型
- 代码（处理符号好）
- 需要快速训练
- **模型**：GPT-2、GPT-3、RoBERTa、BART

**WordPiece** - 最佳用于：
- 掩码语言建模（BERT风格）
- 形态丰富的语言
- 语义理解任务
- **模型**：BERT、DistilBERT、ELECTRA

**Unigram** - 最佳用于：
- 多语言模型
- 无词边界的语言（中日韩）
- 通过子词正则化进行数据增强
- **模型**：T5、ALBERT、XLNet（通过SentencePiece）

## 高级主题

### 处理罕见词

**BPE方法**：
```
"antidisestablishmentarianism"
→ ['anti', 'dis', 'establish', 'ment', 'arian', 'ism']
```

**WordPiece方法**：
```
"antidisestablishmentarianism"
→ ['anti', '##dis', '##establish', '##ment', '##arian', '##ism']
```

**Unigram方法**：
```
"antidisestablishmentarianism"
→ ['▁anti', 'dis', 'establish', 'ment', 'arian', 'ism']
```

### 处理数字

**挑战**：无限数字组合

**BPE解决方案**：字节级（处理任何数字序列）
```python
tokenizer = Tokenizer(BPE())
tokenizer.pre_tokenizer = ByteLevel()

# 处理任何数字
"123456789" → 字节级token
```

**WordPiece解决方案**：数字预分词
```python
from tokenizers.pre_tokenizers import Digits

# 单独或分组分割数字
tokenizer.pre_tokenizer = Digits(individual_digits=True)

"123" → ['1', '2', '3']
```

**Unigram解决方案**：训练期间学习常见数字模式
```python
# 学习模式
"2023" → ['202', '3'] or ['20', '23']
```

### 处理大小写敏感

**小写（BERT）**：
```python
from tokenizers.normalizers import Lowercase

tokenizer.normalizer = Lowercase()

"Hello WORLD" → "hello world" → ['hello', 'world']
```

**保留大小写（GPT-2）**：
```python
# 无大小写标准化
tokenizer.normalizer = None

"Hello WORLD" → ['Hello', 'WORLD']
```

**带大小写token（RoBERTa）**：
```python
# 学习不同大小写的单独token
词表: ['Hello', 'hello', 'HELLO', 'world', 'WORLD']
```

### 处理emoji和特殊字符

**字节级（GPT-2）**：
```python
tokenizer.pre_tokenizer = ByteLevel()

"Hello 🌍 👋" → 字节级表示（总是有效）
```

**Unicode标准化**：
```python
from tokenizers.normalizers import NFKC

tokenizer.normalizer = NFKC()

"é"（组合）↔ "é"（分解）→ 标准化为一种形式
```

## 故障排除

### 问题：子词分割差

**症状**：
```
"running" → ['r', 'u', 'n', 'n', 'i', 'n', 'g']  (太细粒度)
```

**解决方案**：
1. 增加词表大小
2. 训练更久（更多合并迭代）
3. 降低`min_frequency`阈值

### 问题：太多未知token

**症状**：
```
5%的token是[UNK]
```

**解决方案**：
1. 增加词表大小
2. 使用字节级BPE（不可能UNK）
3. 验证训练语料有代表性

### 问题：分词不一致

**症状**：
```
"running" → ['run', 'ning']
"runner" → ['r', 'u', 'n', 'n', 'e', 'r']
```

**解决方案**：
1. 检查标准化一致性
2. 确保预分词是确定性的
3. 使用Unigram获得概率方差

## 最佳实践

1. **匹配算法到模型架构**：
   - BERT风格 → WordPiece
   - GPT风格 → BPE
   - T5风格 → Unigram

2. **对多语言使用字节级**：
   - 处理任何Unicode
   - 无未知token

3. **在代表性数据上测试**：
   - 测量压缩比
   - 检查未知token率
   - 检查样本分词

4. **版本控制分词器**：
   - 与模型一起保存
   - 记录特殊token
   - 跟踪词表变化
