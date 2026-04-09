# Accelerate性能调优

## 性能分析

### 基础性能分析

```python
from accelerate import Accelerator
import time

accelerator = Accelerator()

# 预热
for _ in range(10):
    batch = next(iter(dataloader))
    outputs = model(**batch)
    loss = outputs.loss
    accelerator.backward(loss)
    optimizer.step()
    optimizer.zero_grad()

# 分析训练循环
start = time.time()
total_batches = 100

for i, batch in enumerate(dataloader):
    if i >= total_batches:
        break

    outputs = model(**batch)
    loss = outputs.loss
    accelerator.backward(loss)
    optimizer.step()
    optimizer.zero_grad()

accelerator.wait_for_everyone()  # 同步所有进程
elapsed = time.time() - start

# 指标
batches_per_sec = total_batches / elapsed
samples_per_sec = (total_batches * batch_size * accelerator.num_processes) / elapsed

print(f"吞吐量: {samples_per_sec:.2f} samples/sec")
print(f"批次/秒: {batches_per_sec:.2f}")
```

### PyTorch Profiler集成

```python
from torch.profiler import profile, ProfilerActivity

with profile(
    activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
    record_shapes=True,
    profile_memory=True,
    with_stack=True
) as prof:
    for i, batch in enumerate(dataloader):
        if i >= 10:  # 分析前10个批次
            break

        outputs = model(**batch)
        loss = outputs.loss
        accelerator.backward(loss)
        optimizer.step()
        optimizer.zero_grad()

# 打印分析结果
print(prof.key_averages().table(
    sort_by="cuda_time_total", row_limit=20
))

# 导出为Chrome跟踪
prof.export_chrome_trace("trace.json")
# 在 chrome://tracing 中查看
```

## 内存优化

### 1. 梯度累积

**问题**：大批量导致OOM

**解决方案**：跨微批次累积梯度

```python
accelerator = Accelerator(gradient_accumulation_steps=8)

# 有效批量 = batch_size × accumulation_steps × num_gpus
# 示例: 4 × 8 × 8 = 256

for batch in dataloader:
    with accelerator.accumulate(model):  # 自动处理累积逻辑
        outputs = model(**batch)
        loss = outputs.loss
        accelerator.backward(loss)
        optimizer.step()
        optimizer.zero_grad()
```

**内存节省**：使用8个累积步骤时激活内存减少8倍

### 2. 梯度检查点

**在模型中启用**：

```python
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "gpt2",
    use_cache=False  # 梯度检查点所需
)

# 启用检查点
model.gradient_checkpointing_enable()

# 使用Accelerate准备
model = accelerator.prepare(model)
```

**内存节省**：30-50%，代价是速度慢10-15%

### 3. 混合精度

**BF16 (A100/H100)**：
```python
accelerator = Accelerator(mixed_precision='bf16')

# 自动混合精度
for batch in dataloader:
    outputs = model(**batch)  # BF16前向传播
    loss = outputs.loss
    accelerator.backward(loss)  # FP32反向传播
    optimizer.step()
```

**FP16 (V100、旧GPU)**：
```python
from accelerate.utils import GradScalerKwargs

scaler_kwargs = GradScalerKwargs(
    init_scale=2.**16,
    growth_interval=2000
)

accelerator = Accelerator(
    mixed_precision='fp16',
    kwargs_handlers=[scaler_kwargs]
)
```

**内存节省**：比FP32减少50%

### 4. CPU卸载（DeepSpeed）

```python
from accelerate.utils import DeepSpeedPlugin

ds_plugin = DeepSpeedPlugin(
    zero_stage=3,
    offload_optimizer_device="cpu",  # 卸载优化器到CPU
    offload_param_device="cpu",      # 卸载参数到CPU
)

accelerator = Accelerator(
    deepspeed_plugin=ds_plugin,
    mixed_precision='bf16'
)
```

**内存节省**：优化器状态10-20×，参数5-10×

**代价**：由于CPU-GPU传输速度慢20-30%

### 5. Flash Attention

```python
# 安装flash-attn
# pip install flash-attn

from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "gpt2",
    attn_implementation="flash_attention_2"  # 启用Flash Attention 2
)

model = accelerator.prepare(model)
```

**内存节省**：注意力减少50%，速度提升2倍

**要求**：A100/H100，序列长度必须是128的倍数

## 通信优化

### 1. 梯度分桶（DDP）

```python
from accelerate.utils import DistributedDataParallelKwargs

ddp_kwargs = DistributedDataParallelKwargs(
    bucket_cap_mb=25,  # 梯度归约的分桶大小
    gradient_as_bucket_view=True,  # 减少内存复制
    static_graph=False  # 如果模型不变则设为True
)

accelerator = Accelerator(kwargs_handlers=[ddp_kwargs])
```

**推荐桶大小**：
- 小模型（<1B）：25 MB
- 中模型（1-10B）：50-100 MB
- 大模型（>10B）：100-200 MB

### 2. 查找未使用参数

```python
# 仅在模型有未使用参数时启用（较慢！）
ddp_kwargs = DistributedDataParallelKwargs(
    find_unused_parameters=True
)
```

**使用场景**：带条件分支的模型（如混合专家）

**代价**：速度慢10-20%

### 3. NCCL调优

```bash
# 在启动前设置环境变量
export NCCL_DEBUG=INFO           # 调试信息
export NCCL_IB_DISABLE=0         # 启用InfiniBand
export NCCL_SOCKET_IFNAME=eth0   # 网络接口
export NCCL_P2P_LEVEL=NVL        # 使用NVLink

accelerate launch train.py
```

**NCCL_P2P_LEVEL选项**：
- `NVL`：NVLink（最快，节点内）
- `PIX`：PCIe（快，节点内）
- `PHB`：PCIe主机桥（慢，跨节点）

## 数据加载优化

### 1. DataLoader Workers

```python
from torch.utils.data import DataLoader

train_loader = DataLoader(
    dataset,
    batch_size=32,
    num_workers=4,      # 并行数据加载
    pin_memory=True,    # 固定内存以加快GPU传输
    prefetch_factor=2,  # 每个worker的预取批次
    persistent_workers=True  # 在epoch之间保持worker存活
)

train_loader = accelerator.prepare(train_loader)
```

**推荐**：
- `num_workers`：每个GPU 2-4个（8个GPU → 16-32个workers）
- `pin_memory`：GPU训练时始终为True
- `prefetch_factor`：2-4（数据加载慢时更高）

### 2. 数据预处理

```python
from datasets import load_dataset

# 错误：训练期间预处理（慢）
dataset = load_dataset("openwebtext")

for batch in dataset:
    tokens = tokenizer(batch['text'])  # 慢！
    ...

# 正确：预处理一次，保存
dataset = load_dataset("openwebtext")
tokenized = dataset.map(
    lambda x: tokenizer(x['text']),
    batched=True,
    num_proc=8,  # 并行预处理
    remove_columns=['text']
)
tokenized.save_to_disk("preprocessed_data")

# 加载预处理数据
dataset = load_from_disk("preprocessed_data")
```

### 3. 更快的分词

```python
import os

# 启用基于Rust的分词器（快10倍）
os.environ["TOKENIZERS_PARALLELISM"] = "true"

from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(
    "gpt2",
    use_fast=True  # 使用快速的Rust分词器
)
```

## 编译（PyTorch 2.0+）

### 编译模型

```python
import torch

# 编译模型以加快执行
model = torch.compile(
    model,
    mode="reduce-overhead",  # 选项: default, reduce-overhead, max-autotune
    fullgraph=False,         # 编译整个图（更严格）
    dynamic=True             # 支持动态形状
)

model = accelerator.prepare(model)
```

**加速**：根据模型不同加速10-50%

**编译模式**：
- `default`：平衡（大多数情况最佳）
- `reduce-overhead`：最小开销（小批量最佳）
- `max-autotune`：最大性能（编译慢，生产最佳）

### 编译最佳实践

```python
# 错误：在prepare后编译（不起作用）
model = accelerator.prepare(model)
model = torch.compile(model)  # 错误！

# 正确：在prepare前编译
model = torch.compile(model)
model = accelerator.prepare(model)

# 训练循环
for batch in dataloader:
    # 第一次迭代：慢（编译）
    # 后续迭代：快（已编译）
    outputs = model(**batch)
    ...
```

## 基准测试不同策略

### 脚本模板

```python
import time
import torch
from accelerate import Accelerator

def benchmark_strategy(strategy_name, accelerator_kwargs):
    """基准测试特定训练策略。"""
    accelerator = Accelerator(**accelerator_kwargs)

    # 设置
    model = create_model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    dataloader = create_dataloader()

    model, optimizer, dataloader = accelerator.prepare(
        model, optimizer, dataloader
    )

    # 预热
    for i, batch in enumerate(dataloader):
        if i >= 10:
            break
        outputs = model(**batch)
        loss = outputs.loss
        accelerator.backward(loss)
        optimizer.step()
        optimizer.zero_grad()

    # 基准测试
    accelerator.wait_for_everyone()
    torch.cuda.synchronize()
    start = time.time()

    num_batches = 100
    for i, batch in enumerate(dataloader):
        if i >= num_batches:
            break

        outputs = model(**batch)
        loss = outputs.loss
        accelerator.backward(loss)
        optimizer.step()
        optimizer.zero_grad()

    accelerator.wait_for_everyone()
    torch.cuda.synchronize()
    elapsed = time.time() - start

    # 指标
    throughput = (num_batches * batch_size * accelerator.num_processes) / elapsed
    memory_used = torch.cuda.max_memory_allocated() / 1e9  # GB

    if accelerator.is_main_process:
        print(f"\n{strategy_name}:")
        print(f"  吞吐量: {throughput:.2f} samples/sec")
        print(f"  内存: {memory_used:.2f} GB")
        print(f"  时间: {elapsed:.2f} sec")

    torch.cuda.reset_peak_memory_stats()

# 基准测试不同策略
strategies = [
    ("DDP + FP32", {}),
    ("DDP + BF16", {"mixed_precision": "bf16"}),
    ("DDP + BF16 + GradAccum", {"mixed_precision": "bf16", "gradient_accumulation_steps": 4}),
    ("FSDP", {"fsdp_plugin": fsdp_plugin}),
    ("DeepSpeed ZeRO-2", {"deepspeed_plugin": ds_plugin_stage2}),
    ("DeepSpeed ZeRO-3", {"deepspeed_plugin": ds_plugin_stage3}),
]

for name, kwargs in strategies:
    benchmark_strategy(name, kwargs)
```

## 性能检查清单

**训练前**：
- [ ] 使用BF16/FP16混合精度
- [ ] 启用梯度检查点（如OOM）
- [ ] 设置适当的`num_workers`（每个GPU 2-4个）
- [ ] 启用`pin_memory=True`
- [ ] 预处理数据一次，而不是训练期间
- [ ] 使用`torch.compile`编译模型（PyTorch 2.0+）

**大模型**：
- [ ] 使用FSDP或DeepSpeed ZeRO-3
- [ ] 启用CPU卸载（如仍OOM）
- [ ] 使用Flash Attention
- [ ] 增加梯度累积

**多节点**：
- [ ] 检查网络拓扑（InfiniBand > Ethernet）
- [ ] 调优NCCL设置
- [ ] 为DDP使用更大的桶大小
- [ ] 验证张量并行的NVLink

**性能分析**：
- [ ] 分析前10-100个批次
- [ ] 检查GPU利用率（`nvidia-smi dmon`）
- [ ] 检查数据加载时间（应<迭代的5%）
- [ ] 识别通信瓶颈

## 常见性能问题

### 问题：GPU利用率低（<80%）

**原因1**：数据加载瓶颈
```python
# 解决方案：增加workers和预取
num_workers=8
prefetch_factor=4
```

**原因2**：批量大小太小
```python
# 解决方案：增加批量大小或使用梯度累积
batch_size=32  # 增加
gradient_accumulation_steps=4  # 或累积
```

### 问题：内存使用高

**解决方案1**：梯度检查点
```python
model.gradient_checkpointing_enable()
```

**解决方案2**：减小批量大小，增加累积
```python
batch_size=8  # 从32减小
gradient_accumulation_steps=16  # 维持有效批量
```

**解决方案3**：使用FSDP或DeepSpeed ZeRO-3
```python
accelerator = Accelerator(fsdp_plugin=fsdp_plugin)
```

### 问题：多GPU训练慢

**原因**：通信瓶颈

**检查1**：梯度桶大小
```python
ddp_kwargs = DistributedDataParallelKwargs(bucket_cap_mb=100)
```

**检查2**：NCCL设置
```bash
export NCCL_DEBUG=INFO
# 检查"Using NVLS"（好）vs"Using PHB"（坏）
```

**检查3**：网络带宽
```bash
# 测试GPU间带宽
nvidia-smi nvlink -s
```

## 资源

- Accelerate性能：https://huggingface.co/docs/accelerate/usage_guides/performance
- PyTorch Profiler：https://pytorch.org/tutorials/recipes/recipes/profiler_recipe.html
- NCCL调优：https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- Flash Attention：https://github.com/Dao-AILab/flash-attention
