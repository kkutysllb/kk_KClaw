# 提供商配置

使用Instructor配合不同LLM提供商的指南。

## Anthropic Claude

```python
import instructor
from anthropic import Anthropic

# 基础设置
client = instructor.from_anthropic(Anthropic())

# 带API密钥
client = instructor.from_anthropic(
    Anthropic(api_key="your-api-key")
)

# 推荐模式
client = instructor.from_anthropic(
    Anthropic(),
    mode=instructor.Mode.ANTHROPIC_TOOLS
)

# 用法
result = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "..."}],
    response_model=YourModel
)
```

## OpenAI

```python
from openai import OpenAI

client = instructor.from_openai(OpenAI())

result = client.chat.completions.create(
    model="gpt-4o-mini",
    response_model=YourModel,
    messages=[{"role": "user", "content": "..."}]
)
```

## 本地模型（Ollama）

```python
client = instructor.from_openai(
    OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama"
    ),
    mode=instructor.Mode.JSON
)

result = client.chat.completions.create(
    model="llama3.1",
    response_model=YourModel,
    messages=[...]
)
```

## 模式

- `Mode.ANTHROPIC_TOOLS`：Claude推荐
- `Mode.TOOLS`：OpenAI函数调用
- `Mode.JSON`：不支持提供商的后备
