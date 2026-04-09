# 数据集

SimPO训练的偏好数据集完整指南。

## 数据集格式

### 必需字段

偏好数据集必须包含：
```json
{
  "prompt": "用户问题或指令",
  "chosen": "更好/首选的响应",
  "rejected": "更差/被拒绝的响应"
}
```

**替代字段名**（自动检测）：
- `prompt` → `question`, `instruction`, `input`
- `chosen` → `response_chosen`, `winner`, `preferred`
- `rejected` → `response_rejected`, `loser`

### 示例条目

```json
{
  "prompt": "用简单的术语解释量子计算。",
  "chosen": "量子计算使用量子位（qubits），它们可以通过叠加同时存在于多种状态。这使得量子计算机能够同时处理许多可能性，在密码学和优化等特定任务上可能比经典计算机快得多。",
  "rejected": "它就像普通计算一样，但是量子的。"
}
```

## 流行数据集

### 1. UltraFeedback（推荐）

**HuggingFaceH4/ultrafeedback_binarized**：
- **大小**：60K偏好对
- **质量**：高（GPT-4标注）
- **领域**：通用指令遵循
- **格式**：干净，直接可用

**配置**：
```yaml
dataset_mixer:
  HuggingFaceH4/ultrafeedback_binarized: 1.0
dataset_splits:
  - train_prefs
  - test_prefs
```

### 2. Argilla UltraFeedback（已清洗）

**argilla/ultrafeedback-binarized-preferences-cleaned**：
- **大小**：50K对（已过滤）
- **质量**：非常高（去重、清洗）
- **领域**：通用
- **格式**：干净

**配置**：
```yaml
dataset_mixer:
  argilla/ultrafeedback-binarized-preferences-cleaned: 1.0
```

### 3. Distilabel Math

**argilla/distilabel-math-preference-dpo**：
- **大小**：30K对
- **质量**：高（GSM8K、MATH）
- **领域**：数学推理
- **格式**：数学特定

**配置**：
```yaml
dataset_mixer:
  argilla/distilabel-math-preference-dpo: 1.0
```

### 4. HelpSteer

**nvidia/HelpSteer**：
- **大小**：38K样本
- **质量**：高（人类评分）
- **领域**：帮助性对齐
- **格式**：多属性评分

**配置**：
```yaml
dataset_mixer:
  nvidia/HelpSteer: 1.0
```

### 5. Anthropic HH-RLHF

**Anthropic/hh-rlhf**：
- **大小**：161K样本
- **质量**：高（人类偏好）
- **领域**：无害+有帮助
- **格式**：对话

**配置**：
```yaml
dataset_mixer:
  Anthropic/hh-rlhf: 1.0
```

## 数据集混合

### 多数据集

**等量混合**：
```yaml
dataset_mixer:
  HuggingFaceH4/ultrafeedback_binarized: 0.5
  Anthropic/hh-rlhf: 0.5
```

**加权混合**：
```yaml
dataset_mixer:
  HuggingFaceH4/ultrafeedback_binarized: 0.7
  argilla/distilabel-math-preference-dpo: 0.2
  nvidia/HelpSteer: 0.1
```

**领域特定强调**：
```yaml
# 80%通用 + 20%数学
dataset_mixer:
  HuggingFaceH4/ultrafeedback_binarized: 0.8
  argilla/distilabel-math-preference-dpo: 0.2
```

## 数据质量

### 质量指标

**好的偏好数据**：
- ✅ 被选中/被拒绝之间有明显的质量差异
- ✅ 多样的提示
- ✅ 最小的噪声/标注错误
- ✅ 适当的难度级别

**差的偏好数据**：
- ❌ 模糊的偏好
- ❌ 重复的提示
- ❌ 标注噪声
- ❌ 过于简单/困难的提示

### 质量过滤

**按长度差异过滤**：
```python
def filter_by_length(example):
    chosen_len = len(example['chosen'].split())
    rejected_len = len(example['rejected'].split())
    # 如果选中文本短得多则拒绝（可能是低努力）
    return chosen_len >= rejected_len * 0.5

dataset = dataset.filter(filter_by_length)
```

**按多样性过滤**：
```python
seen_prompts = set()

def filter_duplicates(example):
    prompt = example['prompt']
    if prompt in seen_prompts:
        return False
    seen_prompts.add(prompt)
    return True

dataset = dataset.filter(filter_duplicates)
```

## 自定义数据集创建

### 格式1：JSON Lines

**文件**（`preferences.jsonl`）：
```jsonl
{"prompt": "什么是Python？", "chosen": "Python是一种高级编程语言...", "rejected": "它是一种蛇。"}
{"prompt": "解释AI。", "chosen": "AI指的是能够...的系统", "rejected": "它是会思考的计算机。"}
```

**加载**：
```yaml
dataset_mixer:
  json:
    data_files: preferences.jsonl
```

### 格式2：HuggingFace数据集

**从字典创建**：
```python
from datasets import Dataset

data = {
    "prompt": ["什么是Python？", "解释AI。"],
    "chosen": ["Python是...", "AI指的是..."],
    "rejected": ["它是一种蛇。", "它是会思考的计算机。"]
}

dataset = Dataset.from_dict(data)
dataset.push_to_hub("username/my-preferences")
```

**在配置中使用**：
```yaml
dataset_mixer:
  username/my-preferences: 1.0
```

### 格式3：ChatML

**对于对话数据**：
```json
{
  "prompt": [
    {"role": "user", "content": "什么是量子计算？"}
  ],
  "chosen": [
    {"role": "assistant", "content": "量子计算使用量子位..."}
  ],
  "rejected": [
    {"role": "assistant", "content": "它就像普通计算一样，但是量子的。"}
  ]
}
```

**应用聊天模板**：
```yaml
dataset_text_field: null  # 将应用聊天模板
```

## 合成数据生成

### 使用GPT-4

**提示模板**：
```
给定以下问题：
{prompt}

生成两个响应：
1. 高质量、详细的响应（chosen）
2. 低质量、简短的响应（rejected）

格式为带"chosen"和"rejected"字段的JSON。
```

**示例代码**：
```python
import openai

def generate_pair(prompt):
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{
            "role": "user",
            "content": f"给定：{prompt}\n\n以JSON格式生成chosen/rejected对。"
        }]
    )
    return json.loads(response.choices[0].message.content)

# 生成数据集
prompts = load_prompts()
dataset = [generate_pair(p) for p in prompts]
```

### 使用本地模型

**使用vLLM**：
```python
from vllm import LLM

llm = LLM(model="meta-llama/Meta-Llama-3-70B-Instruct")

def generate_variations(prompt):
    # 生成多个完成
    outputs = llm.generate(
        [prompt] * 4,
        sampling_params={
            "temperature": 0.8,
            "top_p": 0.9,
            "max_tokens": 512
        }
    )

    # 选择最好/最差
    chosen = max(outputs, key=lambda x: len(x.outputs[0].text))
    rejected = min(outputs, key=lambda x: len(x.outputs[0].text))

    return {
        "prompt": prompt,
        "chosen": chosen.outputs[0].text,
        "rejected": rejected.outputs[0].text
    }
```

## 数据预处理

### 截断

**限制序列长度**：
```yaml
max_prompt_length: 512
max_completion_length: 512
max_length: 1024  # 总计
```

**实现**：
```python
def truncate_example(example):
    tokenizer.truncation_side = "left"  # 对于提示
    prompt_tokens = tokenizer(
        example['prompt'],
        max_length=512,
        truncation=True
    )

    tokenizer.truncation_side = "right"  # 对于完成
    chosen_tokens = tokenizer(
        example['chosen'],
        max_length=512,
        truncation=True
    )

    return {
        "prompt": tokenizer.decode(prompt_tokens['input_ids']),
        "chosen": tokenizer.decode(chosen_tokens['input_ids'])
    }

dataset = dataset.map(truncate_example)
```

### 去重

**移除精确重复**：
```python
dataset = dataset.unique('prompt')
```

**移除近似重复**（MinHash）：
```python
from datasketch import MinHash, MinHashLSH

def deduplicate_lsh(dataset, threshold=0.8):
    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    seen = []

    for i, example in enumerate(dataset):
        m = MinHash(num_perm=128)
        for word in example['prompt'].split():
            m.update(word.encode('utf8'))

        if not lsh.query(m):
            lsh.insert(i, m)
            seen.append(example)

    return Dataset.from_list(seen)

dataset = deduplicate_lsh(dataset)
```

## 数据增强

### 提示改写

```python
def paraphrase_prompt(example):
    # 使用改写模型
    paraphrased = paraphrase_model(example['prompt'])

    return [
        example,  # 原始
        {
            "prompt": paraphrased,
            "chosen": example['chosen'],
            "rejected": example['rejected']
        }
    ]

dataset = dataset.map(paraphrase_prompt, batched=False, remove_columns=[])
```

### 难度平衡

**混合简单/中等/困难**：
```python
def categorize_difficulty(example):
    prompt_len = len(example['prompt'].split())
    if prompt_len < 20:
        return "easy"
    elif prompt_len < 50:
        return "medium"
    else:
        return "hard"

dataset = dataset.map(lambda x: {"difficulty": categorize_difficulty(x)})

# 采样平衡数据集
easy = dataset.filter(lambda x: x['difficulty'] == 'easy').shuffle().select(range(1000))
medium = dataset.filter(lambda x: x['difficulty'] == 'medium').shuffle().select(range(1000))
hard = dataset.filter(lambda x: x['difficulty'] == 'hard').shuffle().select(range(1000))

balanced = concatenate_datasets([easy, medium, hard]).shuffle()
```

## 数据集统计

### 计算统计

```python
def compute_stats(dataset):
    prompt_lens = [len(x['prompt'].split()) for x in dataset]
    chosen_lens = [len(x['chosen'].split()) for x in dataset]
    rejected_lens = [len(x['rejected'].split()) for x in dataset]

    print(f"数据集大小: {len(dataset)}")
    print(f"平均提示长度: {np.mean(prompt_lens):.1f} 词")
    print(f"平均选中长度: {np.mean(chosen_lens):.1f} 词")
    print(f"平均拒绝长度: {np.mean(rejected_lens):.1f} 词")
    print(f"选中 > 拒绝: {sum(c > r for c, r in zip(chosen_lens, rejected_lens)) / len(dataset):.1%}")

compute_stats(dataset)
```

**预期输出**：
```
数据集大小: 50000
平均提示长度: 45.2 词
平均选中长度: 180.5 词
平均拒绝长度: 120.3 词
选中 > 拒绝: 85.2%
```

## 最佳实践

### 1. 数据质量优于数量

- **优先**：10K高质量对
- **超过**：100K嘈杂对

### 2. 清晰的偏好信号

- 选中应该明显更好
- 避免边缘差异
- 移除模糊的对

### 3. 领域匹配

- 将数据集领域与目标用例匹配
- 混合数据集以获得更广泛的覆盖
- 包含安全过滤的数据

### 4. 训练前验证

```python
# 采样10个随机示例
samples = dataset.shuffle().select(range(10))

for ex in samples:
    print(f"提示: {ex['prompt']}")
    print(f"选中: {ex['chosen'][:100]}...")
    print(f"拒绝: {ex['rejected'][:100]}...")
    print(f"偏好清晰: {'✓' if len(ex['chosen']) > len(ex['rejected']) else '?'}")
    print()
```

## 参考

- HuggingFace数据集：https://huggingface.co/datasets
- Alignment Handbook：https://github.com/huggingface/alignment-handbook
- UltraFeedback：https://huggingface.co/datasets/HuggingFaceH4/ultrafeedback_binarized
