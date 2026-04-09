# Accelerate自定义插件

## 概述

Accelerate允许创建**自定义插件**以扩展内置选项（DDP、FSDP、DeepSpeed）之外的分布式训练策略。

## 插件架构

### 基础插件结构

```python
from accelerate.utils import DistributedDataParallelKwargs
from dataclasses import dataclass

@dataclass
class CustomPlugin:
    """自定义训练插件。"""

    # 插件配置
    param1: int = 1
    param2: str = "default"

    def __post_init__(self):
        # 验证逻辑
        if self.param1 < 1:
            raise ValueError("param1 must be >= 1")
```

### 使用自定义插件

```python
from accelerate import Accelerator

# 创建插件
custom_plugin = CustomPlugin(param1=4, param2="value")

# 传递给Accelerator
accelerator = Accelerator(
    custom_plugin=custom_plugin  # 不是真实参数，仅示例
)
```

## 内置插件示例

### 1. GradScalerKwargs（FP16配置）

```python
from accelerate.utils import GradScalerKwargs

# 配置FP16梯度缩放器
scaler_kwargs = GradScalerKwargs(
    init_scale=2.**16,        # 初始损失缩放
    growth_factor=2.0,        # 缩放增长速率
    backoff_factor=0.5,       # 缩放退避速率
    growth_interval=2000,     # 缩放增加的步数间隔
    enabled=True              # 启用缩放器
)

accelerator = Accelerator(
    mixed_precision='fp16',
    kwargs_handlers=[scaler_kwargs]  # 作为kwargs处理程序传递
)
```

**使用场景**：微调FP16梯度缩放行为

### 2. DistributedDataParallelKwargs

```python
from accelerate.utils import DistributedDataParallelKwargs

# 配置DDP行为
ddp_kwargs = DistributedDataParallelKwargs(
    bucket_cap_mb=25,                 # 梯度分桶大小
    find_unused_parameters=False,     # 查找未使用参数（较慢）
    check_reduction=False,            # 检查梯度归约
    gradient_as_bucket_view=True,     # 内存优化
    static_graph=False                # 静态计算图
)

accelerator = Accelerator(
    kwargs_handlers=[ddp_kwargs]
)
```

**使用场景**：优化特定模型的DDP性能

### 3. FP8RecipeKwargs（H100 FP8）

```python
from accelerate.utils import FP8RecipeKwargs

# 配置FP8训练（H100）
fp8_recipe = FP8RecipeKwargs(
    backend="te",              # TransformerEngine后端
    margin=0,                  # 缩放边距
    interval=1,                # 缩放间隔
    fp8_format="HYBRID",       # E4M3 + E5M2混合
    amax_history_len=1024,     # AMAX历史长度
    amax_compute_algo="max"    # AMAX计算算法
)

accelerator = Accelerator(
    mixed_precision='fp8',
    kwargs_handlers=[fp8_recipe]
)
```

**使用场景**：在H100 GPU上实现超快训练

## 自定义DeepSpeed配置

### ZeRO-3与CPU卸载

```python
from accelerate import Accelerator
from accelerate.utils import DeepSpeedPlugin

# 自定义DeepSpeed配置
ds_plugin = DeepSpeedPlugin(
    zero_stage=3,                     # ZeRO-3
    offload_optimizer_device="cpu",   # CPU卸载优化器
    offload_param_device="cpu",       # CPU卸载参数
    zero3_init_flag=True,             # ZeRO-3初始化
    zero3_save_16bit_model=True,      # 保存FP16权重
)

accelerator = Accelerator(
    deepspeed_plugin=ds_plugin,
    mixed_precision='bf16'
)
```

### ZeRO-2与NVMe卸载

```python
ds_plugin = DeepSpeedPlugin(
    zero_stage=2,
    offload_optimizer_device="nvme",  # NVMe卸载
    offload_param_device="nvme",
    nvme_path="/local_nvme",          # NVMe挂载路径
)
```

### 自定义JSON配置

```python
import json

# 加载自定义DeepSpeed配置
with open('deepspeed_config.json', 'r') as f:
    ds_config = json.load(f)

ds_plugin = DeepSpeedPlugin(hf_ds_config=ds_config)

accelerator = Accelerator(deepspeed_plugin=ds_plugin)
```

**示例配置**（`deepspeed_config.json`）：
```json
{
  "train_batch_size": "auto",
  "train_micro_batch_size_per_gpu": "auto",
  "gradient_accumulation_steps": "auto",
  "gradient_clipping": 1.0,
  "zero_optimization": {
    "stage": 3,
    "offload_optimizer": {
      "device": "cpu",
      "pin_memory": true
    },
    "offload_param": {
      "device": "cpu",
      "pin_memory": true
    },
    "overlap_comm": true,
    "contiguous_gradients": true,
    "sub_group_size": 1e9,
    "reduce_bucket_size": 5e8,
    "stage3_prefetch_bucket_size": 5e8,
    "stage3_param_persistence_threshold": 1e6,
    "stage3_max_live_parameters": 1e9,
    "stage3_max_reuse_distance": 1e9,
    "stage3_gather_16bit_weights_on_model_save": true
  },
  "bf16": {
    "enabled": true
  },
  "steps_per_print": 100,
  "wall_clock_breakdown": false
}
```

## 自定义FSDP配置

### 带自定义自动包装策略的FSDP

```python
from accelerate.utils import FullyShardedDataParallelPlugin
from torch.distributed.fsdp import BackwardPrefetch, ShardingStrategy
from torch.distributed.fsdp.wrap import size_based_auto_wrap_policy
import functools

# 自定义包装策略（基于大小）
wrap_policy = functools.partial(
    size_based_auto_wrap_policy,
    min_num_params=1e6  # 包装100M+参数的层
)

fsdp_plugin = FullyShardedDataParallelPlugin(
    sharding_strategy=ShardingStrategy.FULL_SHARD,  # ZeRO-3等价
    backward_prefetch=BackwardPrefetch.BACKWARD_PRE,  # 预取策略
    mixed_precision_policy=None,  # 使用Accelerator的混合精度
    auto_wrap_policy=wrap_policy,  # 自定义包装
    cpu_offload=False,
    ignored_modules=None,  # 不包装的模块
    state_dict_type="FULL_STATE_DICT",  # 保存格式
    optim_state_dict_config=None,
    limit_all_gathers=False,
    use_orig_params=True,  # 使用原始参数形状
)

accelerator = Accelerator(
    fsdp_plugin=fsdp_plugin,
    mixed_precision='bf16'
)
```

### 带Transformer自动包装的FSDP

```python
from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy
from transformers.models.gpt2.modeling_gpt2 import GPT2Block

# 在transformer块级别包装
wrap_policy = functools.partial(
    transformer_auto_wrap_policy,
    transformer_layer_cls={GPT2Block}  # 包装GPT2Block层
)

fsdp_plugin = FullyShardedDataParallelPlugin(
    auto_wrap_policy=wrap_policy
)
```

## 创建自定义训练策略

### 示例：自定义梯度累积

```python
from accelerate import Accelerator

class CustomGradientAccumulation:
    def __init__(self, steps=4, adaptive=False):
        self.steps = steps
        self.adaptive = adaptive
        self.current_step = 0

    def should_sync(self, loss):
        """决定是否同步梯度。"""
        self.current_step += 1

        # 自适应：高损失时同步
        if self.adaptive and loss > threshold:
            self.current_step = 0
            return True

        # 常规：每N步同步
        if self.current_step >= self.steps:
            self.current_step = 0
            return True

        return False

# 使用
custom_accum = CustomGradientAccumulation(steps=8, adaptive=True)
accelerator = Accelerator()

for batch in dataloader:
    outputs = model(**batch)
    loss = outputs.loss

    # 缩放损失
    loss = loss / custom_accum.steps
    accelerator.backward(loss)

    # 条件同步
    if custom_accum.should_sync(loss.item()):
        optimizer.step()
        optimizer.zero_grad()
```

### 示例：自定义混合精度

```python
import torch

class CustomMixedPrecision:
    """带动态损失缩放的自定义混合精度。"""

    def __init__(self, init_scale=2**16, scale_window=2000):
        self.scaler = torch.cuda.amp.GradScaler(
            init_scale=init_scale,
            growth_interval=scale_window
        )
        self.scale_history = []

    def scale_loss(self, loss):
        """为反向传播缩放损失。"""
        return self.scaler.scale(loss)

    def unscale_and_clip(self, optimizer, max_norm=1.0):
        """取消缩放梯度并裁剪。"""
        self.scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(
            optimizer.param_groups[0]['params'],
            max_norm
        )

    def step(self, optimizer):
        """带缩放器更新的优化器步骤。"""
        scale_before = self.scaler.get_scale()
        self.scaler.step(optimizer)
        self.scaler.update()
        scale_after = self.scaler.get_scale()

        # 跟踪缩放变化
        if scale_before != scale_after:
            self.scale_history.append(scale_after)

# 使用
custom_mp = CustomMixedPrecision()

for batch in dataloader:
    with torch.cuda.amp.autocast(dtype=torch.float16):
        loss = model(**batch).loss

    scaled_loss = custom_mp.scale_loss(loss)
    scaled_loss.backward()

    custom_mp.unscale_and_clip(optimizer, max_norm=1.0)
    custom_mp.step(optimizer)
    optimizer.zero_grad()
```

## 高级：自定义分布式后端

### 自定义AllReduce策略

```python
import torch.distributed as dist

class CustomAllReduce:
    """带压缩的自定义all-reduce。"""

    def __init__(self, compression_ratio=0.1):
        self.compression_ratio = compression_ratio

    def compress_gradients(self, tensor):
        """Top-k梯度压缩。"""
        k = int(tensor.numel() * self.compression_ratio)
        values, indices = torch.topk(tensor.abs().view(-1), k)
        return values, indices

    def all_reduce_compressed(self, tensor):
        """带梯度压缩的all-reduce。"""
        # 压缩
        values, indices = self.compress_gradients(tensor)

        # 对压缩梯度进行all-reduce
        dist.all_reduce(values, op=dist.ReduceOp.SUM)

        # 解压缩
        tensor_compressed = torch.zeros_like(tensor).view(-1)
        tensor_compressed[indices] = values / dist.get_world_size()

        return tensor_compressed.view_as(tensor)

# 训练循环中使用
custom_ar = CustomAllReduce(compression_ratio=0.1)

for batch in dataloader:
    loss = model(**batch).loss
    loss.backward()

    # 自定义all-reduce
    for param in model.parameters():
        if param.grad is not None:
            param.grad.data = custom_ar.all_reduce_compressed(param.grad.data)

    optimizer.step()
    optimizer.zero_grad()
```

## 插件最佳实践

### 1. 在`__post_init__`中验证

```python
@dataclass
class CustomPlugin:
    learning_rate: float = 1e-3
    warmup_steps: int = 1000

    def __post_init__(self):
        # 验证参数
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if self.warmup_steps < 0:
            raise ValueError("warmup_steps must be non-negative")

        # 计算派生值
        self.min_lr = self.learning_rate * 0.1
```

### 2. 兼容性检查

```python
@dataclass
class CustomPlugin:
    feature_enabled: bool = True

    def is_compatible(self, accelerator):
        """检查插件是否与accelerator配置兼容。"""
        if self.feature_enabled and accelerator.mixed_precision == 'fp8':
            raise ValueError("Custom plugin not compatible with FP8")
        return True
```

### 3. 状态管理

```python
@dataclass
class CustomPlugin:
    counter: int = 0
    history: list = None

    def __post_init__(self):
        if self.history is None:
            self.history = []

    def update_state(self, value):
        """在训练期间更新插件状态。"""
        self.counter += 1
        self.history.append(value)
```

## 资源

- Accelerate插件：https://huggingface.co/docs/accelerate/package_reference/kwargs
- DeepSpeed配置：https://www.deepspeed.ai/docs/config-json/
- FSDP指南：https://pytorch.org/docs/stable/fsdp.html
- 自定义训练循环：https://huggingface.co/docs/accelerate/usage_guides/training_tpu
