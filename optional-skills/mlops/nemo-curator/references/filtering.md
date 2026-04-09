# 质量过滤指南

NeMo Curator的30+质量过滤器的完整指南。

## 基于文本的过滤器

### 词数

```python
from nemo_curator.filters import WordCountFilter

# 按词数过滤
dataset = dataset.filter(WordCountFilter(min_words=50, max_words=100000))
```

### 重复内容

```python
from nemo_curator.filters import RepeatedLinesFilter

# 移除重复行比例超过30%的文档
dataset = dataset.filter(RepeatedLinesFilter(max_repeated_line_fraction=0.3))
```

### 符号比例

```python
from nemo_curator.filters import SymbolToWordRatioFilter

# 移除符号过多的文档
dataset = dataset.filter(SymbolToWordRatioFilter(max_symbol_to_word_ratio=0.3))
```

### URL比例

```python
from nemo_curator.filters import UrlRatioFilter

# 移除URL过多的文档
dataset = dataset.filter(UrlRatioFilter(max_url_ratio=0.2))
```

## 语言过滤

```python
from nemo_curator.filters import LanguageIdentificationFilter

# 仅保留英语文档
dataset = dataset.filter(LanguageIdentificationFilter(target_languages=["en"]))

# 多语言
dataset = dataset.filter(LanguageIdentificationFilter(target_languages=["en", "es", "fr"]))
```

## 基于分类器的过滤

### 质量分类器

```python
from nemo_curator.classifiers import QualityClassifier

quality_clf = QualityClassifier(
    model_path="nvidia/quality-classifier-deberta",
    batch_size=256,
    device="cuda"
)

# 过滤低质量（阈值 > 0.5 = 高质量）
dataset = dataset.filter(lambda doc: quality_clf(doc["text"]) > 0.5)
```

### NSFW分类器

```python
from nemo_curator.classifiers import NSFWClassifier

nsfw_clf = NSFWClassifier(threshold=0.9, device="cuda")

# 移除NSFW内容
dataset = dataset.filter(lambda doc: nsfw_clf(doc["text"]) < 0.9)
```

## 启发式过滤器

完整的30+过滤器列表：
- WordCountFilter
- RepeatedLinesFilter
- UrlRatioFilter
- SymbolToWordRatioFilter
- NonAlphaNumericFilter
- BulletsFilter
- WhiteSpaceFilter
- ParenthesesFilter
- LongWordFilter
- 还有20+更多...

## 最佳实践

1. **先应用便宜的过滤器** - 在GPU分类器之前先做词数过滤
2. **在样本上调优阈值** - 在全量运行前用10k文档测试
3. **谨慎使用GPU分类器** - 昂贵但有效
4. **高效链接过滤器** - 按成本排序（便宜 → 昂贵）
