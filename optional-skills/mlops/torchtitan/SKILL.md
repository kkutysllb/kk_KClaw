---
name: distributed-llm-pretraining-torchtitan
description: 使用torchtitan提供PyTorch原生的分布式LLM预训练，支持4D并行（FSDP2、TP、PP、CP）。用于在8到512+ GPU上预训练Llama 3.1、DeepSeek V3或自定义模型，支持Float8、torch.compile和分布式检查点。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [torch>=2.6.0, torchtitan>=0.2.0, torchao>=0.5.0]
metadata:
  kclaw:
    tags: [模型架构, 分布式训练, TorchTitan, FSDP2, 张量并行, 流水线并行, 上下文并行, Float8, Llama, 预训练]

---

# TorchTitan - PyTorch原生分布式LLM预训练

## 快速开始

TorchTitan是PyTorch官方的大规模LLM预训练平台，支持可组合的4D并行（FSDP2、TP、PP、CP），在H100 GPU上实现超过65%的加速。

**安装**：
```bash
# 从PyPI（稳定版）
pip install torchtitan

# 从源码（最新功能，需要PyTorch nightly）
git clone https://github.com/pytorch/torchtitan
cd torchtitan
pip install -r requirements.txt
```

**下载分词器**：
```bash
# 从https://huggingface.co/settings/tokens获取HF token
python scripts/download_hf_assets.py --repo_id meta-llama/Llama-3.1-8B --assets tokenizer --hf_token=...
```

**在8个GPU上开始训练**：
```bash
CONFIG_FILE="./torchtitan/models/llama3/train_configs/llama3_8b.toml" ./run_train.sh
```

## 常见工作流

### 工作流1：在单节点上预训练Llama 3.1 8B

复制此检查清单：

```
单节点预训练：
- [ ] 步骤1：下载分词器
- [ ] 步骤2：配置训练
- [ ] 步骤3：启动训练
- [ ] 步骤4：监控和检查点
```

**步骤1：下载分词器**

```bash
python scripts/download_hf_assets.py \
  --repo_id meta-llama/Llama-3.1-8B \
  --assets tokenizer \
  --hf_token=YOUR_HF_TOKEN
```

**步骤2：配置训练**

编辑或创建TOML配置文件：

```toml
# llama3_8b_custom.toml
[job]
dump_folder = "./outputs"
description = "Llama 3.1 8B训练"

[model]
name = "llama3"
flavor = "8B"
hf_assets_path = "./assets/hf/Llama-3.1-8B"

[optimizer]
name = "AdamW"
lr = 3e-4

[lr_scheduler]
warmup_steps = 200

[training]
local_batch_size = 2
seq_len = 8192
max_norm = 1.0
steps = 1000
dataset = "c4"

[parallelism]
data_parallel_shard_degree = -1  # 使用所有GPU用于FSDP

[activation_checkpoint]
mode = "selective"
selective_ac_option = "op"

[checkpoint]
enable = true
folder = "checkpoint"
interval = 500
```

**步骤3：启动训练**

```bash
# 单节点8个GPU
CONFIG_FILE="./llama3_8b_custom.toml" ./run_train.sh

# 或使用torchrun显式指定
torchrun --nproc_per_node=8 \
  -m torchtitan.train \
  --job.config_file ./llama3_8b_custom.toml
```

**步骤4：监控和检查点**

TensorBoard日志保存到`./outputs/tb/`：
```bash
tensorboard --logdir ./outputs/tb
```

### 工作流2：使用SLURM进行多节点训练

```
多节点训练：
- [ ] 步骤1：配置并行以实现规模
- [ ] 步骤2：设置SLURM脚本
- [ ] 步骤3：提交作业
- [ ] 步骤4：从检查点恢复
```

**步骤1：配置并行以实现规模**

对于256个GPU上的70B模型：
```toml
[parallelism]
data_parallel_shard_degree = 32  # 跨32个等级FSDP
tensor_parallel_degree = 8        # 节点内TP
pipeline_parallel_degree = 1      # 70B不需要PP
context_parallel_degree = 1       # 为长序列增加
```

**步骤2：设置SLURM脚本**

```bash
#!/bin/bash
#SBATCH --job-name=llama70b
#SBATCH --nodes=32
#SBATCH --ntasks-per-node=8
#SBATCH --gpus-per-node=8

srun torchrun \
  --nnodes=32 \
  --nproc_per_node=8 \
  --rdzv_backend=c10d \
  --rdzv_endpoint=$MASTER_ADDR:$MASTER_PORT \
  -m torchtitan.train \
  --job.config_file ./llama3_70b.toml
```

**步骤3：提交作业**

```bash
sbatch multinode_trainer.slurm
```

**步骤4：从检查点恢复**

如果检查点存在于配置的文件夹中，训练会自动恢复。

### 工作流3：为H100启用Float8训练

Float8在H100 GPU上提供30-50%加速。

```
Float8训练：
- [ ] 步骤1：安装torchao
- [ ] 步骤2：配置Float8
- [ ] 步骤3：使用compile启动
```

**步骤1：安装torchao**

```bash
USE_CPP=0 pip install git+https://github.com/pytorch/ao.git
```

**步骤2：配置Float8**

添加到TOML配置：
```toml
[model]
converters = ["quantize.linear.float8"]

[quantize.linear.float8]
enable_fsdp_float8_all_gather = true
precompute_float8_dynamic_scale_for_fsdp = true
filter_fqns = ["output"]  # 排除输出层

[compile]
enable = true
components = ["model", "loss"]
```

**步骤3：使用compile启动**

```bash
CONFIG_FILE="./llama3_8b.toml" ./run_train.sh \
  --model.converters="quantize.linear.float8" \
  --quantize.linear.float8.enable_fsdp_float8_all_gather \
  --compile.enable
```

### 工作流4：405B模型的4D并行

```
4D并行（FSDP + TP + PP + CP）：
- [ ] 步骤1：创建种子检查点
- [ ] 步骤2：配置4D并行
- [ ] 步骤3：在512个GPU上启动
```

**步骤1：创建种子检查点**

跨PP阶段一致初始化所需：
```bash
NGPU=1 CONFIG_FILE=./llama3_405b.toml ./run_train.sh \
  --checkpoint.enable \
  --checkpoint.create_seed_checkpoint \
  --parallelism.data_parallel_shard_degree 1 \
  --parallelism.tensor_parallel_degree 1 \
  --parallelism.pipeline_parallel_degree 1
```

**步骤2：配置4D并行**

```toml
[parallelism]
data_parallel_shard_degree = 8   # FSDP
tensor_parallel_degree = 8       # 节点内TP
pipeline_parallel_degree = 8     # 跨节点PP
context_parallel_degree = 1      # 长序列CP

[training]
local_batch_size = 32
seq_len = 8192
```

**步骤3：在512个GPU上启动**

```bash
# 64节点 × 8 GPU = 512 GPU
srun torchrun --nnodes=64 --nproc_per_node=8 \
  -m torchtitan.train \
  --job.config_file ./llama3_405b.toml
```

## 与替代方案的比较

**在以下情况下使用TorchTitan：**
- 从头预训练LLM（8B到405B+）
- 需要PyTorch原生解决方案，无第三方依赖
- 需要可组合的4D并行（FSDP2、TP、PP、CP）
- 在具有Float8支持的H100上训练
- 想要与torchtune/HuggingFace互操作的检查点

**在以下情况下使用替代方案：**
- **Megatron-LM**：NVIDIA仅部署的最高性能
- **DeepSpeed**：更广泛的ZeRO优化生态系统，推理支持
- **Axolotl/TRL**：微调而非预训练
- **LitGPT**：教育目的，小规模训练

## 常见问题

**问题：大型模型内存不足**

启用激活检查点并减少批大小：
```toml
[activation_checkpoint]
mode = "full"  # 而不是"selective"

[training]
local_batch_size = 1
```

或使用梯度累积：
```toml
[training]
local_batch_size = 1
global_batch_size = 32  # 累积梯度
```

**问题：TP导致异步聚合高内存**

设置环境变量：
```bash
export TORCH_NCCL_AVOID_RECORD_STREAMS=1
```

**问题：Float8训练没有更快**

Float8仅对大型GEMM有益。过滤小层：
```toml
[quantize.linear.float8]
filter_fqns = ["attention.wk", "attention.wv", "output", "auto_filter_small_kn"]
```

**问题：并行性改变后检查点加载失败**

使用DCP的重分片功能：
```bash
# 将分片检查点转换为单个文件
python -m torch.distributed.checkpoint.format_utils \
  dcp_to_torch checkpoint/step-1000 checkpoint.pt
```

**问题：流水线并行初始化**

首先创建种子检查点（参见工作流4，步骤1）。

## 支持的模型

| 模型 | 规模 | 状态 |
|-------|-------|--------|
| Llama 3.1 | 8B、70B、405B | 生产 |
| Llama 4 | 各种 | 实验 |
| DeepSeek V3 | 16B、236B、671B（MoE） | 实验 |
| GPT-OSS | 20B、120B（MoE） | 实验 |
| Qwen 3 | 各种 | 实验 |
| Flux | Diffusion | 实验 |

## 性能基准（H100）

| 模型 | GPU数 | 并行性 | TPS/GPU | 技术 |
|-------|------|-------------|---------|------------|
| Llama 8B | 8 | FSDP | 5,762 | 基线 |
| Llama 8B | 8 | FSDP+compile+FP8 | 8,532 | +48% |
| Llama 70B | 256 | FSDP+TP+AsyncTP | 876 | 2D并行 |
| Llama 405B | 512 | FSDP+TP+PP | 128 | 3D并行 |

## 高级主题

**FSDP2配置**：参见[references/fsdp.md](references/fsdp.md)获取详细的FSDP2 vs FSDP1比较和ZeRO等价物。

**Float8训练**：参见[references/float8.md](references/float8.md)获取张量级vs行级缩放配方。

**检查点**：参见[references/checkpoint.md](references/checkpoint.md)获取HuggingFace转换和异步检查点。

**添加自定义模型**：参见[references/custom-models.md](references/custom-models.md)获取TrainSpec协议。

## 资源

- GitHub: https://github.com/pytorch/torchtitan
- 论文: https://arxiv.org/abs/2410.06511
- ICLR 2025: https://iclr.cc/virtual/2025/poster/29620
- PyTorch论坛: https://discuss.pytorch.org/c/distributed/torchtitan/44
