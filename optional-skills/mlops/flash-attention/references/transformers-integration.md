# HuggingFace Transformers集成

## 目录
- 在Transformers中启用Flash Attention
- 支持的模型架构
- 配置示例
- 性能对比
- 故障排除特定模型问题

## 在Transformers中启用Flash Attention

HuggingFace Transformers（v4.36+）原生支持Flash Attention 2。

**为任何支持的模型简单启用**：
```python
from transformers import AutoModel

model = AutoModel.from_pretrained(
    "meta-llama/Llama-2-7b-hf",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.float16,
    device_map="auto"
)
```

**安装要求**：
```bash
pip install transformers>=4.36
pip install flash-attn --no-build-isolation
```

## 支持的模型架构

截至Transformers 4.40：

**完全支持**：
- Llama / Llama 2 / Llama 3
- Mistral / Mixtral
- Falcon
- GPT-NeoX
- Phi / Phi-2 / Phi-3
- Qwen / Qwen2
- Gemma
- Starcoder2
- GPT-J
- OPT
- BLOOM

**部分支持**（编码器-解码器）：
- BART
- T5 / Flan-T5
- Whisper

**检查支持**：
```python
from transformers import AutoConfig

config = AutoConfig.from_pretrained("model-name")
print(config._attn_implementation_internal)
# 如果支持则为'flash_attention_2'
```

## 配置示例

### 带Flash Attention的Llama 2

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

model_id = "meta-llama/Llama-2-7b-hf"

model = AutoModelForCausalLM.from_pretrained(
    model_id,
    attn_implementation="flash_attention_2",
    torch_dtype=torch.float16,
    device_map="auto"
)

tokenizer = AutoTokenizer.from_pretrained(model_id)

# 生成
inputs = tokenizer("从前有座山", return_tensors="pt").to("cuda")
outputs = model.generate(**inputs, max_length=100)
print(tokenizer.decode(outputs[0]))
```

### 带Flash Attention处理长上下文的Mistral

```python
from transformers import AutoModelForCausalLM
import torch

model = AutoModelForCausalLM.from_pretrained(
    "mistralai/Mistral-7B-v0.1",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.bfloat16,  # 长上下文更好
    device_map="auto",
    max_position_embeddings=32768  # 扩展上下文
)

# 处理长文档（32K词元）
长文本 = "..." * 10000
inputs = tokenizer(长文本, return_tensors="pt", truncation=False).to("cuda")
outputs = model.generate(**inputs, max_new_tokens=512)
```

### 使用Flash Attention微调

```python
from transformers import Trainer, TrainingArguments
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-2-7b-hf",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.float16
)

training_args = TrainingArguments(
    output_dir="./results",
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    num_train_epochs=3,
    fp16=True,  # 必须匹配模型dtype
    optim="adamw_torch_fused"  # 快速优化器
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset
)

trainer.train()
```

### 多GPU训练

```python
from transformers import AutoModelForCausalLM
import torch

# 带Flash Attention的模型并行
model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-2-13b-hf",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.float16,
    device_map="auto",  # 自动多GPU放置
    max_memory={0: "20GB", 1: "20GB"}  # 限制每GPU
)
```

## 性能对比

### 内存使用（Llama 2 7B，batch=1）

| 序列长度 | 标准注意力 | Flash Attention 2 | 减少 |
|-----------------|-------------------|-------------------|-----------|
| 512 | 1.2 GB | 0.9 GB | 25% |
| 2048 | 3.8 GB | 1.4 GB | 63% |
| 8192 | 14.2 GB | 3.2 GB | 77% |
| 32768 | OOM（>24GB） | 10.8 GB | 可容纳！ |

### 速度（词元/秒，A100 80GB）

| 模型 | 标准 | Flash Attn 2 | 加速 |
|-------|----------|--------------|---------|
| Llama 2 7B (seq=2048) | 42 | 118 | 2.8倍 |
| Llama 2 13B (seq=4096) | 18 | 52 | 2.9倍 |
| Llama 2 70B (seq=2048) | 4 | 11 | 2.75倍 |

### 训练吞吐量（样本/秒）

| 模型 | 批大小 | 标准 | Flash Attn 2 | 加速 |
|-------|------------|----------|--------------|---------|
| Llama 2 7B | 4 | 1.2 | 3.1 | 2.6倍 |
| Llama 2 7B | 8 | 2.1 | 5.8 | 2.8倍 |
| Llama 2 13B | 2 | 0.6 | 1.7 | 2.8倍 |

## 故障排除特定模型问题

### 问题：模型不支持Flash Attention

检查上面的支持列表。如果不支持，使用PyTorch SDPA作为后备：

```python
model = AutoModelForCausalLM.from_pretrained(
    "model-name",
    attn_implementation="sdpa",  # PyTorch原生（仍然更快）
    torch_dtype=torch.float16
)
```

### 问题：加载期间CUDA内存不足

减少内存占用：

```python
model = AutoModelForCausalLM.from_pretrained(
    "model-name",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.float16,
    device_map="auto",
    max_memory={0: "18GB"},  # 为KV缓存保留内存
    low_cpu_mem_usage=True
)
```

### 问题：推理比预期慢

确保dtype匹配：

```python
# 模型和输入都必须为float16/bfloat16
model = model.to(torch.float16)
inputs = tokenizer(..., return_tensors="pt").to("cuda")
inputs = {k: v.to(torch.float16) if v.dtype == torch.float32 else v
          for k, v in inputs.items()}
```

### 问题：与标准注意力输出不同

Flash Attention数值等价但使用不同的计算顺序。微小差异（<1e-3）是正常的：

```python
# 比较输出
model_standard = AutoModelForCausalLM.from_pretrained("model-name", torch_dtype=torch.float16)
model_flash = AutoModelForCausalLM.from_pretrained(
    "model-name",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.float16
)

inputs = tokenizer("测试", return_tensors="pt").to("cuda")

with torch.no_grad():
    out_standard = model_standard(**inputs).logits
    out_flash = model_flash(**inputs).logits

diff = (out_standard - out_flash).abs().max()
print(f"最大差异：{diff:.6f}")  # 应该约1e-3到1e-4
```

### 问题：模型加载期间ImportError

安装flash-attn：
```bash
pip install flash-attn --no-build-isolation
```

或禁用Flash Attention：
```python
model = AutoModelForCausalLM.from_pretrained(
    "model-name",
    attn_implementation="eager",  # 标准PyTorch
    torch_dtype=torch.float16
)
```

## 最佳实践

1. **始终使用float16/bfloat16**配合Flash Attention（不是float32）
2. **设置device_map="auto"**以实现自动内存管理
3. **长上下文使用bfloat16**（更好的数值稳定性）
4. **为大型模型训练启用梯度检查点**
5. **使用`torch.cuda.max_memory_allocated()`监控内存**

**包含所有最佳实践的示例**：
```python
from transformers import AutoModelForCausalLM, TrainingArguments

model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-2-7b-hf",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.bfloat16,  # 训练更好
    device_map="auto",
    low_cpu_mem_usage=True
)

# 为内存启用梯度检查点
model.gradient_checkpointing_enable()

# 带优化的训练
training_args = TrainingArguments(
    output_dir="./results",
    per_device_train_batch_size=8,
    gradient_accumulation_steps=2,
    bf16=True,  # 匹配模型dtype
    optim="adamw_torch_fused",
    gradient_checkpointing=True
)
```
