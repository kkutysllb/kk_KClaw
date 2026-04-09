# 高级验证模式

使用Pydantic进行Instructor中验证的完整指南。

## 目录
- 内置验证器
- 自定义字段验证器
- 模型级验证
- 复杂验证模式
- 错误处理

## 内置验证器

### 数值约束

```python
from pydantic import BaseModel, Field

class Product(BaseModel):
    price: float = Field(gt=0, description="价格必须为正")
    discount: float = Field(ge=0, le=100, description="折扣0-100%")
    quantity: int = Field(ge=1, description="至少1件")
    rating: float = Field(ge=0.0, le=5.0, description="评分0-5星")

# 如果LLM提供无效值，自动重试并返回错误反馈
```

**可用约束：**
- `gt`：大于
- `ge`：大于或等于
- `lt`：小于
- `le`：小于或等于
- `multiple_of`：必须是此数字的倍数

### 字符串约束

```python
class User(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=20,
        pattern=r'^[a-zA-Z0-9_]+$',
        description="3-20个字母数字字符"
    )
    bio: str = Field(max_length=500, description="最多500字符的简介")
    status: str = Field(pattern=r'^(active|inactive|pending)$')

# pattern验证正则表达式
```

### 电子邮件和URL验证

```python
from pydantic import EmailStr, HttpUrl, AnyUrl

class Contact(BaseModel):
    email: EmailStr  # 验证电子邮件格式
    website: HttpUrl  # 验证HTTP/HTTPS URL
    portfolio: AnyUrl  # 任何有效URL方案

contact = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "提取：john@example.com, https://example.com"
    }],
    response_model=Contact
)
```

### 日期和日期时间验证

```python
from datetime import date, datetime
from pydantic import Field, field_validator

class Event(BaseModel):
    event_date: date  # 验证日期格式
    created_at: datetime  # 验证日期时间格式
    year: int = Field(ge=1900, le=2100)

    @field_validator('event_date')
    def future_date(cls, v):
        """确保事件在将来。"""
        if v < date.today():
            raise ValueError('事件必须在将来')
        return v
```

### 列表和字典验证

```python
class Document(BaseModel):
    tags: list[str] = Field(min_length=1, max_length=10)
    keywords: list[str] = Field(min_length=3, description="至少3个关键词")
    metadata: dict[str, str] = Field(description="字符串键值对")

    @field_validator('tags')
    def unique_tags(cls, v):
        """确保标签唯一。"""
        if len(v) != len(set(v)):
            raise ValueError('标签必须唯一')
        return v
```

## 自定义字段验证器

### 基本字段验证器

```python
from pydantic import field_validator

class Person(BaseModel):
    name: str
    age: int

    @field_validator('name')
    def name_must_not_be_empty(cls, v):
        """验证名称不为空或仅空格。"""
        if not v or not v.strip():
            raise ValueError('名称不能为空')
        return v.strip()

    @field_validator('age')
    def age_must_be_reasonable(cls, v):
        """验证年龄在0到120之间。"""
        if v < 0 or v > 120:
            raise ValueError('年龄必须在0到120之间')
        return v
```

### 带字段信息的验证器

```python
from pydantic import ValidationInfo

class Article(BaseModel):
    title: str
    content: str

    @field_validator('content')
    def content_length(cls, v, info: ValidationInfo):
        """验证内容比标题长。"""
        if 'title' in info.data:
            title_len = len(info.data['title'])
            if len(v) < title_len * 2:
                raise ValueError('内容应该至少是标题长度的2倍')
        return v
```

### 多字段验证

```python
class TimeRange(BaseModel):
    start_time: str
    end_time: str

    @field_validator('start_time', 'end_time')
    def valid_time_format(cls, v):
        """验证两个时间都是HH:MM格式。"""
        import re
        if not re.match(r'^\d{2}:\d{2}$', v):
            raise ValueError('时间必须是HH:MM格式')
        return v
```

### 转换和验证

```python
class URL(BaseModel):
    url: str

    @field_validator('url')
    def normalize_url(cls, v):
        """如果缺少则添加https://。"""
        if not v.startswith(('http://', 'https://')):
            v = f'https://{v}'
        return v
```

## 模型级验证

### 跨字段验证

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
            raise ValueError('end_date必须在start_date之后')
        return self

class PriceRange(BaseModel):
    min_price: float
    max_price: float

    @model_validator(mode='after')
    def check_price_range(self):
        """确保max > min。"""
        if self.max_price <= self.min_price:
            raise ValueError('max_price必须大于min_price')
        return self
```

### 条件验证

```python
class Order(BaseModel):
    order_type: str  # "standard" 或 "express"
    delivery_date: str
    delivery_time: Optional[str] = None

    @model_validator(mode='after')
    def check_delivery_time(self):
        """快递订单需要配送时间。"""
        if self.order_type == "express" and not self.delivery_time:
            raise ValueError('快递订单需要delivery_time')
        return self
```

### 复杂业务逻辑

```python
class Discount(BaseModel):
    code: str
    percentage: float = Field(ge=0, le=100)
    min_purchase: float = Field(ge=0)
    max_discount: float = Field(ge=0)

    @model_validator(mode='after')
    def validate_discount(self):
        """确保折扣逻辑合理。"""
        # 最大折扣不能超过min_purchase的百分比
        theoretical_max = (self.percentage / 100) * self.min_purchase
        if self.max_discount > theoretical_max:
            self.max_discount = theoretical_max
        return self
```

## 复杂验证模式

### 嵌套模型验证

```python
class Address(BaseModel):
    street: str
    city: str
    country: str
    postal_code: str

    @field_validator('postal_code')
    def validate_postal_code(cls, v, info: ValidationInfo):
        """根据国家验证邮政编码格式。"""
        if 'country' in info.data:
            country = info.data['country']
            if country == "中国":
                import re
                if not re.match(r'^\d{6}$', v):
                    raise ValueError('无效的中国邮政编码')
            elif country == "美国":
                if not re.match(r'^\d{5}(-\d{4})?$', v):
                    raise ValueError('无效的美国邮政编码')
        return v

class Person(BaseModel):
    name: str
    address: Address

# 嵌套验证自动运行
```

### 模型列表

```python
class Task(BaseModel):
    title: str = Field(min_length=1)
    priority: int = Field(ge=1, le=5)

class Project(BaseModel):
    name: str
    tasks: list[Task] = Field(min_length=1, description="至少1个任务")

    @field_validator('tasks')
    def at_least_one_high_priority(cls, v):
        """确保至少一个任务优先级>=4。"""
        if not any(task.priority >= 4 for task in v):
            raise ValueError('项目需要至少一个高优先级任务')
        return v
```

### 联合类型验证

```python
from typing import Union

class TextBlock(BaseModel):
    type: str = "text"
    content: str = Field(min_length=1)

class ImageBlock(BaseModel):
    type: str = "image"
    url: HttpUrl
    alt_text: str

class Page(BaseModel):
    title: str
    blocks: list[Union[TextBlock, ImageBlock]]

    @field_validator('blocks')
    def validate_block_types(cls, v):
        """确保第一个块是TextBlock。"""
        if v and not isinstance(v[0], TextBlock):
            raise ValueError('第一个块必须是文本')
        return v
```

### 依赖字段

```python
class Subscription(BaseModel):
    plan: str  # "free", "pro", "enterprise"
    max_users: int
    features: list[str]

    @model_validator(mode='after')
    def validate_plan_limits(self):
        """强制执行特定计划的限制。"""
        limits = {
            "free": {"max_users": 1, "required_features": ["basic"]},
            "pro": {"max_users": 10, "required_features": ["basic", "advanced"]},
            "enterprise": {"max_users": 999, "required_features": ["basic", "advanced", "premium"]}
        }

        if self.plan in limits:
            limit = limits[self.plan]

            if self.max_users > limit["max_users"]:
                raise ValueError(f'{self.plan}计划限制{limit["max_users"]}个用户')

            for feature in limit["required_features"]:
                if feature not in self.features:
                    raise ValueError(f'{self.plan}计划需要{feature}功能')

        return self
```

## 错误处理

### 优雅降级

```python
class OptionalExtraction(BaseModel):
    # 必需字段
    title: str

    # 带默认值的可选字段
    author: Optional[str] = None
    date: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

# 即使无法提取所有内容，LLM也可以成功
```

### 部分验证

```python
from pydantic import ValidationError

def extract_with_fallback(text: str):
    """尝试完整提取，失败后回退到部分。"""
    try:
        # 尝试完整提取
        return client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": text}],
            response_model=FullModel
        )
    except ValidationError:
        # 回退到部分模型
        return client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": text}],
            response_model=PartialModel
        )
```

### 验证错误检查

```python
from pydantic import ValidationError

try:
    result = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[...],
        response_model=MyModel,
        max_retries=3
    )
except ValidationError as e:
    # 检查特定错误
    for error in e.errors():
        field = error['loc'][0]
        message = error['msg']
        print(f"字段'{field}'失败：{message}")

        # 每个字段的自定义处理
        if field == 'email':
            # 处理电子邮件验证失败
            pass
```

### 自定义错误消息

```python
class DetailedModel(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=100,
        description="2-100个字符之间的名称"
    )
    age: int = Field(
        ge=0,
        le=120,
        description="0到120之间的年龄"
    )

    @field_validator('name')
    def validate_name(cls, v):
        """提供有用的错误消息。"""
        if not v.strip():
            raise ValueError(
                '名称不能为空。'
                '请从文本中提供有效的名称。'
            )
        return v

# 当验证失败时，LLM会看到这些有用的消息
```

## 验证最佳实践

### 1. 具体明确

```python
# ❌ 不好：模糊验证
class Item(BaseModel):
    name: str

# ✅ 好：具体约束
class Item(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=200,
        description="物品名称，1-200个字符"
    )
```

### 2. 提供上下文

```python
# ✅ 好：解释验证失败的原因
@field_validator('price')
def validate_price(cls, v):
    if v <= 0:
        raise ValueError(
            '价格必须为正。'
            '从文本中提取数字价格，不含货币符号。'
        )
    return v
```

### 3. 对固定集合使用枚举

```python
# ❌ 不好：字符串验证
status: str

@field_validator('status')
def validate_status(cls, v):
    if v not in ['active', 'inactive', 'pending']:
        raise ValueError('无效的状态')
    return v

# ✅ 好：枚举
class Status(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"

status: Status  # 自动验证
```

### 4. 平衡严格性

```python
# 太严格：不必要地失败
class StrictModel(BaseModel):
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    # 如果LLM使用"2024-1-5"而不是"2024-01-05"则失败

# 更好：在验证器中规范化
class FlexibleModel(BaseModel):
    date: str

    @field_validator('date')
    def normalize_date(cls, v):
        from datetime import datetime
        # 解析灵活格式
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y']:
            try:
                dt = datetime.strptime(v, fmt)
                return dt.strftime('%Y-%m-%d')  # 规范化
            except ValueError:
                continue
        raise ValueError('无效的日期格式')
```

### 5. 测试验证

```python
# 用边界情况测试验证器
def test_validation():
    # 应该成功
    valid = MyModel(field="valid_value")

    # 应该失败
    try:
        invalid = MyModel(field="invalid")
        assert False, "应该抛出ValidationError"
    except ValidationError:
        pass  # 预期

# 生产使用前运行测试
```

## 高级技术

### 条件必需字段

```python
from typing import Optional

class ConditionalModel(BaseModel):
    type: str
    detail_a: Optional[str] = None
    detail_b: Optional[str] = None

    @model_validator(mode='after')
    def check_required_details(self):
        """根据类型要求不同字段。"""
        if self.type == "type_a" and not self.detail_a:
            raise ValueError('type_a需要detail_a')
        if self.type == "type_b" and not self.detail_b:
            raise ValueError('type_b需要detail_b')
        return self
```

### 外部数据验证

```python
class Product(BaseModel):
    sku: str
    name: str

    @field_validator('sku')
    def validate_sku(cls, v):
        """检查SKU是否存在于数据库中。"""
        # 查询数据库或API
        if not database.sku_exists(v):
            raise ValueError(f'SKU {v}在目录中未找到')
        return v
```

### 渐进验证

```python
# 从宽松验证开始
class Stage1(BaseModel):
    data: str  # 任何字符串

# 然后严格验证
class Stage2(BaseModel):
    data: str = Field(pattern=r'^[A-Z]{3}-\d{6}$')

# 使用Stage1进行初始提取
# 使用Stage2进行最终验证
```

## 资源

- **Pydantic文档**：https://docs.pydantic.dev/latest/concepts/validators/
- **Instructor示例**：https://python.useinstructor.com/examples
