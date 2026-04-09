---
name: instructor
description: 通过Pydantic验证从LLM响应中提取结构化数据，自动重试失败提取，解析复杂JSON并保证类型安全，以及使用Instructor流式传输部分结果 - 经过实战检验的结构化输出库
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [instructor, pydantic, openai, anthropic]
metadata:
  kclaw:
    tags: [提示工程, Instructor, 结构化输出, Pydantic, 数据提取, JSON解析, 类型安全, 验证, 流式传输, OpenAI, Anthropic]

---

# Instructor：结构化LLM输出

## 何时使用此技能

在需要以下情况时使用Instructor：
- 从LLM响应中**可靠地提取结构化数据**
- **自动验证**输出符合Pydantic模式
- **自动重试**失败提取并进行错误处理
- **解析复杂JSON**并保证类型安全和验证
- **流式传输部分结果**用于实时处理
- **支持多个LLM提供商**并保持一致的API

**GitHub Stars**：15,000+ | **经过实战检验**：100,000+开发者

## 安装

```bash
# 基础安装
pip install instructor

# 配合特定提供商
pip install "instructor[anthropic]"  # Anthropic Claude
pip install "instructor[openai]"     # OpenAI
pip install "instructor[all]"        # 所有提供商
```

## 快速开始

### 基本示例：提取用户数据

```python
import instructor
from pydantic import BaseModel
from anthropic import Anthropic

# 定义输出结构
class User(BaseModel):
    name: str
    age: int
    email: str

# 创建instructor客户端
client = instructor.from_anthropic(Anthropic())

# 提取结构化数据
user = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "John Doe is 30 years old. His email is john@example.com"
    }],
    response_model=User
)

print(user.name)   # "John Doe"
print(user.age)    # 30
print(user.email)  # "john@example.com"
```

### 配合OpenAI使用

```python
from openai import OpenAI

client = instructor.from_openai(OpenAI())

user = client.chat.completions.create(
    model="gpt-4o-mini",
    response_model=User,
    messages=[{"role": "user", "content": "Extract: Alice, 25, alice@email.com"}]
)
```

## 核心概念

### 1. 响应模型（Pydantic）

响应模型定义LLM输出的结构和验证规则。

#### 基本模型

```python
from pydantic import BaseModel, Field

class Article(BaseModel):
    title: str = Field(description="Article title")
    author: str = Field(description="Author name")
    word_count: int = Field(description="Number of words", gt=0)
    tags: list[str] = Field(description="List of relevant tags")

article = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "Analyze this article: [article text]"
    }],
    response_model=Article
)
```

**好处：**
- 带Python类型提示的类型安全
- 自动验证（word_count > 0）
- 带Field描述的自我记录
- IDE自动完成支持

#### 嵌套模型

```python
class Address(BaseModel):
    street: str
    city: str
    country: str

class Person(BaseModel):
    name: str
    age: int
    address: Address  # 嵌套模型

person = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "John lives at 123 Main St, Boston, USA"
    }],
    response_model=Person
)

print(person.address.city)  # "Boston"
```

#### 可选字段

```python
from typing import Optional

class Product(BaseModel):
    name: str
    price: float
    discount: Optional[float] = None  # 可选
    description: str = Field(default="No description")  # 默认值

# LLM不需要提供discount或description
```

#### 用于约束的枚举

```python
from enum import Enum

class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"

class Review(BaseModel):
    text: str
    sentiment: Sentiment  # 只允许这3个值

review = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "This product is amazing!"
    }],
    response_model=Review
)

print(review.sentiment)  # Sentiment.POSITIVE
```

### 2. 验证

Pydantic自动验证LLM输出。如果验证失败，Instructor重试。

#### 内置验证器

```python
from pydantic import Field, EmailStr, HttpUrl

class Contact(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    age: int = Field(ge=0, le=120)  # 0 <= age <= 120
    email: EmailStr  # 验证电子邮件格式
    website: HttpUrl  # 验证URL格式

# 如果LLM提供无效数据，Instructor自动重试
```

#### 自定义验证器

```python
from pydantic import field_validator

class Event(BaseModel):
    name: str
    date: str
    attendees: int

    @field_validator('date')
    def validate_date(cls, v):
        """确保日期格式为YYYY-MM-DD。"""
        import re
        if not re.match(r'\d{4}-\d{2}-\d{2}', v):
            raise ValueError('Date must be YYYY-MM-DD format')
        return v

    @field_validator('attendees')
    def validate_attendees(cls, v):
        """确保有正的参与人数。"""
        if v < 1:
            raise ValueError('Must have at least 1 attendee')
        return v
```

#### 模型级验证

```python
from pydantic import model_validator

class DateRange(BaseModel):
    start_date: str
    end_date: str

    @model_validator(mode='after')
    def check_dates(self):
        """确保end_date在start_date之后。"""
        from datetime import datetime
        start = datetime.strptime(self.start_date, '%Y-%m-%d')
        end = datetime.strptime(self.end_date, '%Y-%m-%d')

        if end < start:
            raise ValueError('end_date must be after start_date')
        return self
```

### 3. 自动重试

当验证失败时Instructor自动重试，向LLM提供错误反馈。

```python
# 如果验证失败最多重试3次
user = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "Extract user from: John, age unknown"
    }],
    response_model=User,
    max_retries=3  # 默认是3
)

# 如果无法提取age，Instructor告诉LLM：
# "Validation error: age - field required"
# LLM带着更好的提取再次尝试
```

**工作原理：**
1. LLM生成输出
2. Pydantic验证
3. 如果无效：错误消息发送回LLM
4. LLM带着错误反馈再次尝试
5. 重复直到max_retries

### 4. 流式传输

流式传输部分结果用于实时处理。

#### 流式传输部分对象

```python
from instructor import Partial

class Story(BaseModel):
    title: str
    content: str
    tags: list[str]

# 当LLM生成时流式传输部分更新
for partial_story in client.messages.create_partial(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "Write a short sci-fi story"
    }],
    response_model=Story
):
    print(f"Title: {partial_story.title}")
    print(f"Content so far: {partial_story.content[:100]}...")
    # 实时更新UI
```

#### 流式传输可迭代对象

```python
class Task(BaseModel):
    title: str
    priority: str

# 生成时流式传输列表项
tasks = client.messages.create_iterable(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "Generate 10 project tasks"
    }],
    response_model=Task
)

for task in tasks:
    print(f"- {task.title} ({task.priority})")
    # 到达时处理每个任务
```

## 提供商配置

### Anthropic Claude

```python
import instructor
from anthropic import Anthropic

client = instructor.from_anthropic(
    Anthropic(api_key="your-api-key")
)

# 配合Claude模型使用
response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[...],
    response_model=YourModel
)
```

### OpenAI

```python
from openai import OpenAI

client = instructor.from_openai(
    OpenAI(api_key="your-api-key")
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    response_model=YourModel,
    messages=[...]
)
```

### 本地模型（Ollama）

```python
from openai import OpenAI

# 指向本地Ollama服务器
client = instructor.from_openai(
    OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama"  # 必需但被忽略
    ),
    mode=instructor.Mode.JSON
)

response = client.chat.completions.create(
    model="llama3.1",
    response_model=YourModel,
    messages=[...]
)
```

## 常见模式

### 模式1：从文本提取数据

```python
class CompanyInfo(BaseModel):
    name: str
    founded_year: int
    industry: str
    employees: int
    headquarters: str

text = """
Tesla, Inc. was founded in 2003. It operates in the automotive and energy
industry with approximately 140,000 employees. The company is headquartered
in Austin, Texas.
"""

company = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": f"Extract company information from: {text}"
    }],
    response_model=CompanyInfo
)
```

### 模式2：分类

```python
class Category(str, Enum):
    TECHNOLOGY = "technology"
    FINANCE = "finance"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    OTHER = "other"

class ArticleClassification(BaseModel):
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    keywords: list[str]

classification = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "Classify this article: [article text]"
    }],
    response_model=ArticleClassification
)
```

### 模式3：多实体提取

```python
class Person(BaseModel):
    name: str
    role: str

class Organization(BaseModel):
    name: str
    industry: str

class Entities(BaseModel):
    people: list[Person]
    organizations: list[Organization]
    locations: list[str]

text = "Tim Cook, CEO of Apple, announced at the event in Cupertino..."

entities = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": f"Extract all entities from: {text}"
    }],
    response_model=Entities
)

for person in entities.people:
    print(f"{person.name} - {person.role}")
```

### 模式4：结构化分析

```python
class SentimentAnalysis(BaseModel):
    overall_sentiment: Sentiment
    positive_aspects: list[str]
    negative_aspects: list[str]
    suggestions: list[str]
    score: float = Field(ge=-1.0, le=1.0)

review = "The product works well but setup was confusing..."

analysis = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": f"Analyze this review: {review}"
    }],
    response_model=SentimentAnalysis
)
```

### 模式5：批处理

```python
def extract_person(text: str) -> Person:
    return client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"Extract person from: {text}"
        }],
        response_model=Person
    )

texts = [
    "John Doe is a 30-year-old engineer",
    "Jane Smith, 25, works in marketing",
    "Bob Johnson, age 40, software developer"
]

people = [extract_person(text) for text in texts]
```

## 高级功能

### 联合类型

```python
from typing import Union

class TextContent(BaseModel):
    type: str = "text"
    content: str

class ImageContent(BaseModel):
    type: str = "image"
    url: HttpUrl
    caption: str

class Post(BaseModel):
    title: str
    content: Union[TextContent, ImageContent]  # 任一类型

# LLM根据内容选择适当类型
```

### 动态模型

```python
from pydantic import create_model

# 在运行时创建模型
DynamicUser = create_model(
    'User',
    name=(str, ...),
    age=(int, Field(ge=0)),
    email=(EmailStr, ...)
)

user = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[...],
    response_model=DynamicUser
)
```

### 自定义模式

```python
# 用于没有原生结构化输出的提供商
client = instructor.from_anthropic(
    Anthropic(),
    mode=instructor.Mode.JSON  # JSON模式
)

# 可用模式：
# - Mode.ANTHROPIC_TOOLS（Claude推荐）
# - Mode.JSON（后备）
# - Mode.TOOLS（OpenAI工具）
```

### 上下文管理

```python
# 单次使用客户端
with instructor.from_anthropic(Anthropic()) as client:
    result = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[...],
        response_model=YourModel
    )
    # 客户端自动关闭
```

## 错误处理

### 处理验证错误

```python
from pydantic import ValidationError

try:
    user = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[...],
        response_model=User,
        max_retries=3
    )
except ValidationError as e:
    print(f"重试后失败: {e}")
    # 优雅处理

except Exception as e:
    print(f"API错误: {e}")
```

### 自定义错误消息

```python
class ValidatedUser(BaseModel):
    name: str = Field(description="全名，2-100个字符")
    age: int = Field(description="0-120之间的年龄", ge=0, le=120)
    email: EmailStr = Field(description="有效电子邮件地址")

    class Config:
        # 自定义错误消息
        json_schema_extra = {
            "examples": [
                {
                    "name": "John Doe",
                    "age": 30,
                    "email": "john@example.com"
                }
            ]
        }
```

## 最佳实践

### 1. 清晰的字段描述

```python
# ❌ 坏：模糊
class Product(BaseModel):
    name: str
    price: float

# ✅ 好：描述性
class Product(BaseModel):
    name: str = Field(description="文本中的产品名称")
    price: float = Field(description="美元价格，不含货币符号")
```

### 2. 使用适当的验证

```python
# ✅ 好：约束值
class Rating(BaseModel):
    score: int = Field(ge=1, le=5, description="1到5星的评分")
    review: str = Field(min_length=10, description="评论文本，至少10个字符")
```

### 3. 在提示中提供示例

```python
messages = [{
    "role": "user",
    "content": """从"John, 30, engineer"中提取人物信息

示例格式：
{
  "name": "John Doe",
  "age": 30,
  "occupation": "engineer"
}"""
}]
```

### 4. 对固定类别使用枚举

```python
# ✅ 好：枚举确保有效值
class Status(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class Application(BaseModel):
    status: Status  # LLM必须从枚举中选择
```

### 5. 优雅处理缺失数据

```python
class PartialData(BaseModel):
    required_field: str
    optional_field: Optional[str] = None
    default_field: str = "default_value"

# LLM只需要提供required_field
```

## 与替代方案比较

| 功能 | Instructor | 手动JSON | LangChain | DSPy |
|---------|------------|-------------|-----------|------|
| 类型安全 | ✅ 是 | ❌ 否 | ⚠️ 部分 | ✅ 是 |
| 自动验证 | ✅ 是 | ❌ 否 | ❌ 否 | ⚠️ 有限 |
| 自动重试 | ✅ 是 | ❌ 否 | ❌ 否 | ✅ 是 |
| 流式传输 | ✅ 是 | ❌ 否 | ✅ 是 | ❌ 否 |
| 多提供商 | ✅ 是 | ⚠️ 手动 | ✅ 是 | ✅ 是 |
| 学习曲线 | 低 | 低 | 中 | 高 |

**何时选择Instructor：**
- 需要结构化、经验证的输出
- 想要类型安全和IDE支持
- 需要自动重试
- 构建数据提取系统

**何时选择替代方案：**
- DSPy：需要提示优化
- LangChain：构建复杂链
- 手动：简单的一次性提取

## 资源

- **文档**：https://python.useinstructor.com
- **GitHub**：https://github.com/jxnl/instructor (15k+ stars)
- **食谱**：https://python.useinstructor.com/examples
- **Discord**：社区支持可用

## 另请参阅

- `references/validation.md` - 高级验证模式
- `references/providers.md` - 提供商特定配置
- `references/examples.md` - 真实用例
