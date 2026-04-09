---
name: huggingface-accelerate
description: 最简单的分布式训练API。仅需4行代码即可为任何PyTorch脚本添加分布式支持。统一API支持DeepSpeed/FSDP/Megatron/DDP。自动设备放置、混合精度（FP16/BF16/FP8）。交互式配置，单一启动命令。HuggingFace生态标准。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [accelerate, torch, transformers]
metadata:
  kclaw:
    tags: [分布式训练, HuggingFace, Accelerate, DeepSpeed, FSDP, 混合精度, PyTorch, DDP, 统一API, 简单]

---

# HuggingFace Accelerate - 统一分布式训练

## 快速开始

Accelerate将分布式训练简化为4行代码。

**安装**：
```bash
pip install accelerate
```

**转换PyTorch脚本**（4行）：
```python
import torch
+ from accelerate import Accelerator

+ accelerator = Accelerator()

  model = torch.nn.Transformer()
  optimizer = torch.optim.Adam(model.parameters())
  dataloader = torch.utils.data.DataLoader(dataset)

+ model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)

  for batch in dataloader:
      optimizer.zero_grad()
      loss = model(batch)
-     loss.backward()
+     accelerator.backward(loss)
      optimizer.step()
```

**运行**（单一命令）：
```bash
accelerate launch train.py
```

## 常见工作流程

### 工作流程1：从单GPU到多GPU

**原始脚本**：
```python
# train.py
import torch

model = torch.nn.Linear(10, 2).to('cuda')
optimizer = torch.optim.Adam(model.parameters())
dataloader = torch.utils.data.DataLoader(dataset, batch_size=32)

for epoch in range(10):
    for batch in dataloader:
        batch = batch.to('cuda')
        optimizer.zero_grad()
        loss = model(batch).mean()
        loss.backward()
        optimizer.step()
```

**使用Accelerate**（添加4行）：
```python
# train.py
import torch
from accelerate import Accelerator  # +1

accelerator = Accelerator()  # +2

model = torch.nn.Linear(10, 2)
optimizer = torch.optim.Adam(model.parameters())
dataloader = torch.utils.data.DataLoader(dataset, batch_size=32)

model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)  # +3

for epoch in range(10):
    for batch in dataloader:
        # 不需要.to('cuda') - 自动处理！
        optimizer.zero_grad()
        loss = model(batch).mean()
        accelerator.backward(loss)  # +4
        optimizer.step()
```

**配置**（交互式）：
```bash
accelerate config
```

**问题**：
- 使用哪台机器？（单/多GPU/TPU/CPU）
- 使用多少台机器？（1）
- 混合精度？（否/fp16/bf16/fp8）
- DeepSpeed？（否/是）

**启动**（适用于任何配置）：
```bash
# 单GPU
accelerate launch train.py

# 多GPU（8个GPU）
accelerate launch --multi_gpu --num_processes 8 train.py

# 多节点
accelerate launch --multi_gpu --num_processes 16 \
  --num_machines 2 --machine_rank 0 \
  --main_process_ip $MASTER_ADDR \
  train.py
```

### 工作流程2：混合精度训练

**启用FP16/BF16**：
```python
from accelerate import Accelerator

# FP16（带梯度缩放）
accelerator = Accelerator(mixed_precision='fp16')

# BF16（无需缩放，更稳定）
accelerator = Accelerator(mixed_precision='bf16')

# FP8（H100+）
accelerator = Accelerator(mixed_precision='fp8')

model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)

# 其他都是自动的！
for batch in dataloader:
    with accelerator.autocast():  # 可选，自动完成
        loss = model(batch)
    accelerator.backward(loss)
```

### 工作流程3：DeepSpeed ZeRO集成

**启用DeepSpeed ZeRO-2**：
```python
from accelerate import Accelerator

accelerator = Accelerator(
    mixed_precision='bf16',
    deepspeed_plugin={
        "zero_stage": 2,  # ZeRO-2
        "offload_optimizer": False,
        "gradient_accumulation_steps": 4
    }
)

# 代码与之前相同！
model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)
```

**或通过配置**：
```bash
accelerate config
# 选择：DeepSpeed → ZeRO-2
```

**deepspeed_config.json**：
```json
{
    "fp16": {"enabled": false},
    "bf16": {"enabled": true},
    "zero_optimization": {
        "stage": 2,
        "offload_optimizer": {"device": "cpu"},
        "allgather_bucket_size": 5e8,
        "reduce_bucket_size": 5e8
    }
}
```

**启动**：
```bash
accelerate launch --config_file deepspeed_config.json train.py
```

### 工作流程4：FSDP（完全分片数据并行）

**启用FSDP**：
```python
from accelerate import Accelerator, FullyShardedDataParallelPlugin

fsdp_plugin = FullyShardedDataParallelPlugin(
    sharding_strategy="FULL_SHARD",  # ZeRO-3等价
    auto_wrap_policy="TRANSFORMER_AUTO_WRAP",
    cpu_offload=False
)

accelerator = Accelerator(
    mixed_precision='bf16',
    fsdp_plugin=fsdp_plugin
)

model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)
```

**或通过配置**：
```bash
accelerate config
# 选择：FSDP → Full Shard → No CPU Offload
```

### 工作流程5：梯度累积

**累积梯度**：
```python
from accelerate import Accelerator

accelerator = Accelerator(gradient_accumulation_steps=4)

model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)

for batch in dataloader:
    with accelerator.accumulate(model):  # 自动处理累积
        optimizer.zero_grad()
        loss = model(batch)
        accelerator.backward(loss)
        optimizer.step()
```

**有效批量大小**：`batch_size * num_gpus * gradient_accumulation_steps`

## 何时使用与替代方案

**在以下情况下使用Accelerate**：
- 需要最简单的分布式训练
- 需要单一脚本适用于任何硬件
- 使用HuggingFace生态
- 需要灵活性（DDP/DeepSpeed/FSDP/Megatron）
- 需要快速原型开发

**主要优势**：
- **4行代码**：最少代码改动
- **统一API**：DDP、DeepSpeed、FSDP、Megatron使用相同代码
- **自动化**：设备放置、混合精度、分片
- **交互式配置**：无需手动启动器设置
- **单一启动**：处处可用

**使用替代方案**：
- **PyTorch Lightning**：需要回调、高层抽象
- **Ray Train**：多节点编排、超参数调优
- **DeepSpeed**：直接API控制、高级功能
- **原始DDP**：最大控制、最少抽象

## 常见问题

**问题：设备放置错误**

不要手动移动到设备：
```python
# 错误
batch = batch.to('cuda')

# 正确
# Accelerate在prepare()后自动处理
```

**问题：梯度累积不工作**

使用上下文管理器：
```python
# 正确
with accelerator.accumulate(model):
    optimizer.zero_grad()
    accelerator.backward(loss)
    optimizer.step()
```

**问题：分布式中的检查点**

使用accelerator方法：
```python
# 仅在主进程保存
if accelerator.is_main_process:
    accelerator.save_state('checkpoint/')

# 在所有进程加载
accelerator.load_state('checkpoint/')
```

**问题：FSDP结果不同**

确保相同的随机种子：
```python
from accelerate.utils import set_seed
set_seed(42)
```

## 高级主题

**Megatron集成**：有关张量并行、流水线并行和序列并行的设置，请参阅[references/megatron-integration.md](references/megatron-integration.md)。

**自定义插件**：有关创建自定义分布式插件和高级配置，请参阅[references/custom-plugins.md](references/custom-plugins.md)。

**性能调优**：有关性能分析、内存优化和最佳实践，请参阅[references/performance.md](references/performance.md)。

## 硬件要求

- **CPU**：可用（较慢）
- **单GPU**：可用
- **多GPU**：DDP（默认）、DeepSpeed或FSDP
- **多节点**：DDP、DeepSpeed、FSDP、Megatron
- **TPU**：支持
- **Apple MPS**：支持

**启动器要求**：
- **DDP**：`torch.distributed.run`（内置）
- **DeepSpeed**：`deepspeed`（pip install deepspeed）
- **FSDP**：PyTorch 1.12+（内置）
- **Megatron**：自定义设置

## 资源

- 文档：https://huggingface.co/docs/accelerate
- GitHub：https://github.com/huggingface/accelerate
- 版本：1.11.0+
- 教程："Accelerate your scripts"
- 示例：https://github.com/huggingface/accelerate/tree/main/examples
- 使用者：HuggingFace Transformers、TRL、PEFT、所有HF库
