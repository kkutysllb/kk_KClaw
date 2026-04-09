# Accelerate与Megatron集成

## 概述

Accelerate支持Megatron-LM，用于通过张量并行和流水线并行进行大规模模型训练。

**Megatron能力**：
- **张量并行（TP）**：跨GPU分割层
- **流水线并行（PP）**：跨GPU分割模型深度
- **数据并行（DP）**：跨GPU组复制模型
- **序列并行**：为长上下文分割序列

## 设置

### 安装Megatron-LM

```bash
# 克隆Megatron-LM仓库
git clone https://github.com/NVIDIA/Megatron-LM.git
cd Megatron-LM
pip install -e .

# 安装Apex（NVIDIA优化）
git clone https://github.com/NVIDIA/apex
cd apex
pip install -v --disable-pip-version-check --no-cache-dir --no-build-isolation \
  --config-settings "--build-option=--cpp_ext" --config-settings "--build-option=--cuda_ext" ./
```

### Accelerate配置

```bash
accelerate config
```

**问题**：
```
您使用哪种计算环境？
> 本机

您使用哪种类型的机器？
> 多GPU

您将使用多少台不同的机器？
> 1

您想使用DeepSpeed/FSDP吗？
> 否

您想使用Megatron-LM吗？
> 是

张量并行度是多少？[1-8]
> 2

您想启用序列并行吗？
> 否

流水线并行度是多少？[1-8]
> 2

数据并行度是多少？[1-8]
> 2

在哪里执行激活检查点？['SELECTIVE', 'FULL', 'NONE']
> SELECTIVE

在哪里执行激活分区？['SEQUENTIAL', 'UNIFORM']
> SEQUENTIAL
```

**生成的配置**（`~/.cache/huggingface/accelerate/default_config.yaml`）：
```yaml
compute_environment: LOCAL_MACHINE
distributed_type: MEGATRON_LM
downcast_bf16: 'no'
machine_rank: 0
main_training_function: main
megatron_lm_config:
  megatron_lm_gradient_clipping: 1.0
  megatron_lm_learning_rate_decay_iters: 320000
  megatron_lm_num_micro_batches: 1
  megatron_lm_pp_degree: 2
  megatron_lm_recompute_activations: true
  megatron_lm_sequence_parallelism: false
  megatron_lm_tp_degree: 2
mixed_precision: bf16
num_machines: 1
num_processes: 8
rdzv_backend: static
same_network: true
tpu_env: []
tpu_use_cluster: false
tpu_use_sudo: false
use_cpu: false
```

## 并行策略

### 张量并行（TP）

**跨GPU分割每个Transformer层**：

```python
# 层跨2个GPU分割
# GPU 0: 前一半注意力头
# GPU 1: 后一半注意力头

# 每个GPU计算部分输出
# All-reduce组合结果
```

**TP度数推荐**：
- **TP=1**：无张量并行（每层单GPU）
- **TP=2**：每层2个GPU（适合7-13B模型）
- **TP=4**：每层4个GPU（适合20-40B模型）
- **TP=8**：每层8个GPU（适合70B+模型）

**优势**：
- 减少每个GPU的内存
- All-reduce通信（快）

**缺点**：
- 需要快速的GPU间带宽（NVLink）
- 每层通信开销

### 流水线并行（PP）

**跨GPU分割模型深度**：

```python
# 12层模型，PP=4
# GPU 0: 第0-2层
# GPU 1: 第3-5层
# GPU 2: 第6-8层
# GPU 3: 第9-11层
```

**PP度数推荐**：
- **PP=1**：无流水线并行
- **PP=2**：2个流水线阶段（适合20-40B模型）
- **PP=4**：4个流水线阶段（适合70B+模型）
- **PP=8**：8个流水线阶段（适合175B+模型）

**优势**：
- 线性内存减少（4× PP = 4×内存）
- 跨节点工作（较慢互连也可以）

**缺点**：
- 流水线气泡（空闲时间）
- 需要微批处理

### 数据并行（DP）

**跨GPU组复制模型**：

```python
# 8个GPU，TP=2，PP=2，DP=2
# 组0（GPU 0-3）：完整模型副本
# 组1（GPU 4-7）：完整模型副本
```

**DP度数**：
- `DP = total_gpus / (TP × PP)`
- 示例：8个GPU，TP=2，PP=2 → DP=2

**优势**：
- 增加吞吐量
- 扩展批量大小

### 序列并行

**跨GPU分割长序列**（扩展TP）：

```python
# 8K序列，TP=2，序列并行=True
# GPU 0: Token 0-4095
# GPU 1: Token 4096-8191
```

**优势**：
- 支持极长序列（100K+ token）
- 减少激活内存

**要求**：
- 必须与TP > 1一起使用
- RoPE/ALiBi位置编码效果最佳

## Accelerate代码示例

### 基础设置

```python
from accelerate import Accelerator
from accelerate.utils import MegatronLMPlugin

# 配置Megatron
megatron_plugin = MegatronLMPlugin(
    tp_degree=2,              # 张量并行度
    pp_degree=2,              # 流水线并行度
    num_micro_batches=4,      # 流水线微批次
    gradient_clipping=1.0,    # 梯度裁剪值
    sequence_parallelism=False,  # 启用序列并行
    recompute_activations=True,  # 激活检查点
    use_distributed_optimizer=True,  # 分布式优化器
    custom_prepare_model_function=None,  # 自定义模型准备
)

# 初始化accelerator
accelerator = Accelerator(
    mixed_precision='bf16',
    megatron_lm_plugin=megatron_plugin
)

# 准备模型和优化器
model, optimizer, train_dataloader = accelerator.prepare(
    model, optimizer, train_dataloader
)

# 训练循环（与DDP相同！）
for batch in train_dataloader:
    optimizer.zero_grad()
    outputs = model(**batch)
    loss = outputs.loss
    accelerator.backward(loss)
    optimizer.step()
```

### 完整训练脚本

```python
import torch
from accelerate import Accelerator
from accelerate.utils import MegatronLMPlugin
from transformers import GPT2Config, GPT2LMHeadModel

def main():
    # Megatron配置
    megatron_plugin = MegatronLMPlugin(
        tp_degree=2,
        pp_degree=2,
        num_micro_batches=4,
        gradient_clipping=1.0,
    )

    accelerator = Accelerator(
        mixed_precision='bf16',
        gradient_accumulation_steps=8,
        megatron_lm_plugin=megatron_plugin
    )

    # 模型
    config = GPT2Config(
        n_layer=24,
        n_head=16,
        n_embd=1024,
    )
    model = GPT2LMHeadModel(config)

    # 优化器
    optimizer = torch.optim.AdamW(model.parameters(), lr=6e-4)

    # 准备
    model, optimizer, train_loader = accelerator.prepare(
        model, optimizer, train_loader
    )

    # 训练循环
    for epoch in range(num_epochs):
        for batch in train_loader:
            with accelerator.accumulate(model):
                outputs = model(**batch)
                loss = outputs.loss
                accelerator.backward(loss)
                optimizer.step()
                optimizer.zero_grad()

        # 保存检查点
        accelerator.wait_for_everyone()
        accelerator.save_state(f'checkpoint-epoch-{epoch}')

if __name__ == '__main__':
    main()
```

### 启动命令

```bash
# 8个GPU，TP=2，PP=2，DP=2
accelerate launch --multi_gpu --num_processes 8 train.py

# 多节点（2个节点，每个8个GPU）
# 节点0
accelerate launch --multi_gpu --num_processes 16 \
  --num_machines 2 --machine_rank 0 \
  --main_process_ip $MASTER_ADDR \
  --main_process_port 29500 \
  train.py

# 节点1
accelerate launch --multi_gpu --num_processes 16 \
  --num_machines 2 --machine_rank 1 \
  --main_process_ip $MASTER_ADDR \
  --main_process_port 29500 \
  train.py
```

## 激活检查点

**通过重新计算激活来减少内存**：

```python
megatron_plugin = MegatronLMPlugin(
    recompute_activations=True,      # 启用检查点
    checkpoint_num_layers=1,         # 每N层检查点
    distribute_checkpointed_activations=True,  # 跨TP分发
    partition_activations=True,      # 在PP中分区
    check_for_nan_in_loss_and_grad=True,  # 稳定性检查
)
```

**策略**：
- `SELECTIVE`：仅检查点transformer块
- `FULL`：检查点所有层
- `NONE`：不检查点

**内存节省**：30-50%，代价是速度慢10-15%

## 分布式优化器

**跨DP等级分片优化器状态**：

```python
megatron_plugin = MegatronLMPlugin(
    use_distributed_optimizer=True,  # 启用分片优化器
)
```

**优势**：
- 按DP度数减少优化器内存
- 示例：DP=4 → 每个GPU优化器内存减少4×

**兼容**：
- AdamW、Adam、SGD
- 混合精度训练

## 性能调优

### 微批次大小

```python
# 流水线并行需要微批处理
megatron_plugin = MegatronLMPlugin(
    pp_degree=4,
    num_micro_batches=16,  # 每个流水线16个微批次
)

# 有效批量 = num_micro_batches × micro_batch_size × DP
# 示例: 16 × 2 × 4 = 128
```

**推荐**：
- 更多微批次 → 更少的流水线气泡
- 典型值：4-16个微批次

### 序列长度

```python
# 对于长序列，启用序列并行
megatron_plugin = MegatronLMPlugin(
    tp_degree=4,
    sequence_parallelism=True,  # 需要: TP > 1
)

# 支持序列达到TP × 正常限制
# 示例：TP=4，正常8K → 序列并行下32K
```

### GPU拓扑

**TP需要NVLink**：
```bash
# 检查NVLink拓扑
nvidia-smi topo -m

# 好拓扑（所有GPU间NVLink）
# GPU0 - GPU1: NV12（快）
# GPU0 - GPU2: NV12（快）

# 坏拓扑（仅PCIe）
# GPU0 - GPU4: PHB（慢，避免跨这些的TP）
```

**推荐**：
- **TP**：在同一节点内（NVLink）
- **PP**：跨节点（较慢互连也可以）
- **DP**：任何拓扑

## 模型大小指南

| 模型大小 | GPU数 | TP | PP | DP | 微批次 |
|------------|------|----|----|----|--------------|
| 7B | 8 | 1 | 1 | 8 | 1 |
| 13B | 8 | 2 | 1 | 4 | 1 |
| 20B | 16 | 4 | 1 | 4 | 1 |
| 40B | 32 | 4 | 2 | 4 | 4 |
| 70B | 64 | 8 | 2 | 4 | 8 |
| 175B | 128 | 8 | 4 | 4 | 16 |

**假设**：BF16，2K序列长度，A100 80GB

## 检查点

### 保存检查点

```python
# 保存完整模型状态
accelerator.save_state('checkpoint-1000')

# Megatron为每个rank保存单独文件
# checkpoint-1000/
#   pytorch_model_tp_0_pp_0.bin
#   pytorch_model_tp_0_pp_1.bin
#   pytorch_model_tp_1_pp_0.bin
#   pytorch_model_tp_1_pp_1.bin
#   optimizer_tp_0_pp_0.bin
#   ...
```

### 加载检查点

```python
# 恢复训练
accelerator.load_state('checkpoint-1000')

# 自动为每个rank加载正确的分片
```

### 转换为标准PyTorch

```bash
# 合并Megatron检查点为单个文件
python merge_megatron_checkpoint.py \
  --checkpoint-dir checkpoint-1000 \
  --output pytorch_model.bin
```

## 常见问题

### 问题：流水线并行OOM

**解决方案**：增加微批次
```python
megatron_plugin = MegatronLMPlugin(
    pp_degree=4,
    num_micro_batches=16,  # 从4增加
)
```

### 问题：训练慢

**检查1**：流水线气泡（PP太高）
```python
# 减少PP，增加TP
tp_degree=4  # 增加
pp_degree=2  # 减少
```

**检查2**：微批次大小太小
```python
num_micro_batches=8  # 增加
```

### 问题：未检测到NVLink

```bash
# 验证NVLink
nvidia-smi nvlink -s

# 如果没有NVLink，避免TP > 1
# 改用PP或DP
```

## 资源

- Megatron-LM：https://github.com/NVIDIA/Megatron-LM
- Accelerate Megatron文档：https://huggingface.co/docs/accelerate/usage_guides/megatron_lm
- 论文："Megatron-LM: Training Multi-Billion Parameter Language Models Using Model Parallelism"
- NVIDIA Apex：https://github.com/NVIDIA/apex
