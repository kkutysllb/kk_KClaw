# PyTorch Lightning分布式训练

## 分布式策略

Lightning通过单个参数更改支持多种分布式策略。

### 1. DDP（DistributedDataParallel）

**多GPU的默认策略**：

```python
# 在所有可用GPU上自动DDP
trainer = L.Trainer(accelerator='gpu', devices=4, strategy='ddp')

# 或自动检测
trainer = L.Trainer(accelerator='gpu', devices='auto')
```

**DDP工作原理**：
- 在每个GPU上复制模型
- 每个GPU处理不同的batch
- 梯度在GPU之间all-reduced
- 模型权重同步

**启动**：
```bash
# Lightning自动处理生成进程
python train.py
```

**DDP配置**：
```python
from lightning.pytorch.strategies import DDPStrategy

strategy = DDPStrategy(
    find_unused_parameters=False,  # 如果模型有未使用的参数则设为True
    gradient_as_bucket_view=True,  # 内存优化
    static_graph=False,  # 如果图不变则设为True
)

trainer = L.Trainer(strategy=strategy)
```

### 2. FSDP（Fully Sharded Data Parallel）

**用于大型模型（7B+参数）**：

```python
from lightning.pytorch.strategies import FSDPStrategy

strategy = FSDPStrategy(
    sharding_strategy="FULL_SHARD",  # ZeRO-3等价物
    activation_checkpointing=None,   # 或指定层类型
    cpu_offload=False,               # CPU卸载以节省内存
)

trainer = L.Trainer(
    accelerator='gpu',
    devices=8,
    strategy=strategy,
    precision='bf16'  # 与FSDP一起推荐
)

trainer.fit(model, train_loader)
```

**FSDP分片策略**：
```python
# FULL_SHARD（最高内存效率，等价于ZeRO-3）
strategy = FSDPStrategy(sharding_strategy="FULL_SHARD")

# SHARD_GRAD_OP（较低内存效率，等价于ZeRO-2）
strategy = FSDPStrategy(sharding_strategy="SHARD_GRAD_OP")

# NO_SHARD（无分片，类似DDP）
strategy = FSDPStrategy(sharding_strategy="NO_SHARD")
```

**自动包装策略**（包装transformer块）：
```python
from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy
from transformers.models.gpt2.modeling_gpt2 import GPT2Block
import functools

auto_wrap_policy = functools.partial(
    transformer_auto_wrap_policy,
    transformer_layer_cls={GPT2Block}
)

strategy = FSDPStrategy(
    auto_wrap_policy=auto_wrap_policy,
    activation_checkpointing_policy={GPT2Block}  # 检查这些块
)
```

### 3. DeepSpeed

**用于超大型模型（70B+参数）**：

```python
from lightning.pytorch.strategies import DeepSpeedStrategy

# 带CPU卸载的DeepSpeed ZeRO-3
strategy = DeepSpeedStrategy(
    stage=3,                       # ZeRO-3
    offload_optimizer=True,        # CPU卸载优化器
    offload_parameters=True,       # CPU卸载参数
    cpu_checkpointing=True,        # 检查点到CPU
)

trainer = L.Trainer(
    accelerator='gpu',
    devices=8,
    strategy=strategy,
    precision='bf16'
)

trainer.fit(model, train_loader)
```

**DeepSpeed配置文件**：
```json
{
  "train_batch_size": "auto",
  "train_micro_batch_size_per_gpu": "auto",
  "gradient_accumulation_steps": "auto",
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
    "reduce_bucket_size": 5e8,
    "stage3_prefetch_bucket_size": 5e8,
    "stage3_param_persistence_threshold": 1e6
  },
  "bf16": {
    "enabled": true
  }
}
```

**使用配置文件**：
```python
strategy = DeepSpeedStrategy(config='deepspeed_config.json')
trainer = L.Trainer(strategy=strategy)
```

### 4. DDP Spawn

**Windows兼容的DDP**：

```python
# 当DDP不工作时使用（例如Windows、Jupyter）
trainer = L.Trainer(
    accelerator='gpu',
    devices=2,
    strategy='ddp_spawn'  # 生成新进程
)
```

**注意**：由于进程生成开销，比DDP慢

## 多节点训练

### 设置多节点集群

**节点0（主节点）**：
```bash
export MASTER_ADDR=192.168.1.100
export MASTER_PORT=12355
export WORLD_SIZE=16  # 2节点 × 8 GPU
export NODE_RANK=0

python train.py
```

**节点1（工作节点）**：
```bash
export MASTER_ADDR=192.168.1.100
export MASTER_PORT=12355
export WORLD_SIZE=16
export NODE_RANK=1

python train.py
```

**训练脚本**：
```python
trainer = L.Trainer(
    accelerator='gpu',
    devices=8,              # 每节点GPU数
    num_nodes=2,            # 总节点数
    strategy='ddp'
)

trainer.fit(model, train_loader)
```

### SLURM集成

**SLURM作业脚本**：
```bash
#!/bin/bash
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=8
#SBATCH --gres=gpu:8
#SBATCH --time=24:00:00

# Lightning自动检测SLURM环境
srun python train.py
```

**训练脚本**（无需更改）：
```python
# Lightning自动读取SLURM环境变量
trainer = L.Trainer(
    accelerator='gpu',
    devices=8,
    num_nodes=4,  # 来自SBATCH --nodes
    strategy='ddp'
)
```

### Kubernetes（KubeFlow）

**训练脚本**：
```python
import os

# Lightning自动检测Kubernetes
trainer = L.Trainer(
    accelerator='gpu',
    devices=int(os.getenv('WORLD_SIZE', 1)),
    strategy='ddp'
)
```

## 混合精度训练

### BF16（A100/H100）

```python
trainer = L.Trainer(
    precision='bf16',  # 或'bf16-mixed'
    accelerator='gpu'
)
```

**优势**：
- 不需要梯度缩放器
- 与FP32相同的动态范围
- 2倍加速，50%内存减少

### FP16（V100、旧GPU）

```python
trainer = L.Trainer(
    precision='16-mixed',  # 或只是'16'
    accelerator='gpu'
)
```

**自动梯度缩放**由Lightning处理

### FP8（H100）

```python
# 需要transformer_engine
# pip install transformer-engine[pytorch]

trainer = L.Trainer(
    precision='transformer-engine',
    accelerator='gpu'
)
```

**优势**：比H100上BF16快2倍

## 梯度累积

**模拟更大的batch大小**：

```python
trainer = L.Trainer(
    accumulate_grad_batches=4,  # 累积4个batch
    precision='bf16'
)

# 有效batch = batch_size × accumulate_grad_batches × num_gpus
# 示例：32 × 4 × 8 = 1024
```

**动态累积**：
```python
# 训练早期累积更多
trainer = L.Trainer(
    accumulate_grad_batches={
        0: 8,   # Epoch 0-4：累积8
        5: 4,   # Epoch 5-9：累积4
        10: 2   # Epoch 10+：累积2
    }
)
```

## 分布式中的检查点

### 保存检查点

```python
from lightning.pytorch.callbacks import ModelCheckpoint

# 默认仅rank 0保存
checkpoint = ModelCheckpoint(
    dirpath='checkpoints/',
    filename='model-{epoch:02d}',
    save_top_k=3
)

trainer = L.Trainer(callbacks=[checkpoint], strategy='ddp')
trainer.fit(model, train_loader)
```

**手动保存**：
```python
class MyModel(L.LightningModule):
    def training_step(self, batch, batch_idx):
        # 训练...
        loss = ...

        # 每1000步保存一次（仅rank 0）
        if batch_idx % 1000 == 0 and self.trainer.is_global_zero:
            self.trainer.save_checkpoint(f'checkpoint_step_{batch_idx}.ckpt')

        return loss
```

### 加载检查点

```python
# 恢复训练
trainer = L.Trainer(strategy='ddp')
trainer.fit(model, train_loader, ckpt_path='checkpoints/last.ckpt')

# 加载用于推理
model = MyModel.load_from_checkpoint('checkpoints/best.ckpt')
model.eval()
```

## 策略对比

| 策略 | 内存效率 | 速度 | 用例 |
|----------|------------------|-------|----------|
| DDP | 低 | 快 | 小模型（<7B），单节点 |
| FSDP | 高 | 中 | 大模型（7-70B） |
| DeepSpeed ZeRO-2 | 中 | 快 | 中型模型（1-13B） |
| DeepSpeed ZeRO-3 | 非常高 | 较慢 | 超大型模型（70B+） |
| DDP Spawn | 低 | 慢 | Windows、调试 |

## 最佳实践

### 1. 选择正确的策略

```python
# 模型大小指南
if model_params < 1e9:  # <1B
    strategy = 'ddp'
elif model_params < 7e9:  # 1-7B
    strategy = 'ddp' or DeepSpeedStrategy(stage=2)
elif model_params < 70e9:  # 7-70B
    strategy = FSDPStrategy(sharding_strategy="FULL_SHARD")
else:  # 70B+
    strategy = DeepSpeedStrategy(stage=3, offload_optimizer=True)

trainer = L.Trainer(strategy=strategy)
```

### 2. 避免同步问题

```python
class MyModel(L.LightningModule):
    def training_step(self, batch, batch_idx):
        # 错误：这在所有GPU上独立运行
        if batch_idx % 100 == 0:
            self.log_something()  # 在8个GPU上记录8次！

        # 正确：使用is_global_zero
        if batch_idx % 100 == 0 and self.trainer.is_global_zero:
            self.log_something()  # 只记录一次

        loss = ...
        return loss
```

### 3. 高效数据加载

```python
from torch.utils.data import DataLoader, DistributedSampler

# Lightning自动处理DistributedSampler
train_loader = DataLoader(
    dataset,
    batch_size=32,
    num_workers=4,  # 每GPU 4个工作进程
    pin_memory=True,
    persistent_workers=True
)

# Lightning在DDP中自动用DistributedSampler包装
trainer.fit(model, train_loader)
```

### 4. 减少通信开销

```python
from lightning.pytorch.strategies import DDPStrategy

strategy = DDPStrategy(
    gradient_as_bucket_view=True,  # 减少内存拷贝
    static_graph=True,  # 如果模型图不变（更快）
)

trainer = L.Trainer(strategy=strategy)
```

## 常见问题

### 问题：NCCL超时

**症状**：训练挂起并显示`NCCL timeout`错误

**解决方案1**：增加超时
```bash
export NCCL_TIMEOUT=3600  # 1小时
python train.py
```

**解决方案2**：检查网络
```bash
# 测试节点间通信
nvidia-smi nvlink -s

# 验证所有节点可以相互ping
ping <node-2-ip>
```

### 问题：FSDP OOM

**解决方案**：启用CPU卸载
```python
strategy = FSDPStrategy(
    sharding_strategy="FULL_SHARD",
    cpu_offload=True  # 卸载到CPU
)
```

### 问题：DDP结果不同

**原因**：每个GPU随机种子不同

**解决方案**：在LightningModule中设置种子
```python
class MyModel(L.LightningModule):
    def __init__(self):
        super().__init__()
        L.seed_everything(42, workers=True)  # 到处相同种子
```

### 问题：DeepSpeed配置错误

**解决方案**：使用Lightning的自动配置
```python
strategy = DeepSpeedStrategy(
    stage=3,
    # 不指定配置文件，Lightning自动生成
)
```

## 资源

- 分布式策略：https://lightning.ai/docs/pytorch/stable/accelerators/gpu_intermediate.html
- FSDP指南：https://lightning.ai/docs/pytorch/stable/advanced/model_parallel/fsdp.html
- DeepSpeed：https://lightning.ai/docs/pytorch/stable/advanced/model_parallel/deepspeed.html
- 多节点：https://lightning.ai/docs/pytorch/stable/clouds/cluster.html
