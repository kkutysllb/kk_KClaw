# 真实世界示例

使用Instructor进行结构化数据提取的实际示例。

## 数据提取

```python
class CompanyInfo(BaseModel):
    name: str
    founded: int
    industry: str
    employees: int

text = "苹果公司成立于1976年，在科技行业拥有164,000名员工。"

company = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": f"提取：{text}"}],
    response_model=CompanyInfo
)
```

## 分类

```python
class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"

class Review(BaseModel):
    sentiment: Sentiment
    confidence: float = Field(ge=0.0, le=1.0)

review = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "这个产品太棒了！"}],
    response_model=Review
)
```

## 多实体提取

```python
class Person(BaseModel):
    name: str
    role: str

class Entities(BaseModel):
    people: list[Person]
    organizations: list[str]
    locations: list[str]

entities = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "库克，苹果CEO，在库比蒂诺发表讲话..."}],
    response_model=Entities
)
```

## 结构化分析

```python
class Analysis(BaseModel):
    summary: str
    key_points: list[str]
    sentiment: Sentiment
    actionable_items: list[str]

analysis = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "分析：[长文本]"}],
    response_model=Analysis
)
```

## 批处理

```python
texts = ["文本1", "文本2", "文本3"]
results = [
    client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": text}],
        response_model=YourModel
    )
    for text in texts
]
```

## 流式传输

```python
for partial in client.messages.create_partial(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "生成报告..."}],
    response_model=Report
):
    print(f"进度：{partial.title}")
    # 实时更新UI
```
