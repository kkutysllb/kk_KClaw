---
name: simpo-training
description: 用于LLM对齐的简单偏好优化。比DPO性能更好（AlpacaEval 2.0上+6.4分），无需参考模型，比DPO更高效。当想要比DPO/PPO更简单、更快的训练时，用于偏好对齐。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [torch, transformers, datasets, trl, accelerate]
metadata:
  kclaw:
    tags: [后训练, SimPO, 偏好优化, 对齐, DPO替代, 无参考, LLM对齐, 高效训练]

---

# SimPO - 简单偏好优化

## 快速开始

SimPO是一种无需参考模型的免参考偏好优化方法，性能优于DPO。

**安装**：
```bash
# 创建环境
conda create -n simpo python=3.10 && conda activate simpo

# 安装PyTorch 2.2.2
# 访问: https://pytorch.org/get-started/locally/

# 安装alignment-handbook
git clone https://github.com/huggingface/alignment-handbook.git
cd alignment-handbook
python -m pip install .

# 安装Flash Attention 2
python -m pip install flash-attn --no-build-isolation
```

**训练**（Mistral 7B）：
```bash
ACCELERATE_LOG_LEVEL=info accelerate launch \
  --config_file accelerate_configs/deepspeed_zero3.yaml \
  scripts/run_simpo.py \
  training_configs/mistral-7b-base-simpo.yaml
```

## 常见工作流程

### 工作流程1：从基础模型训练（Mistral 7B）

**配置**（`mistral-7b-base-simpo.yaml`）：
```yaml
# 模型
model_name_or_path: mistralai/Mistral-7B-v0.1
torch_dtype: bfloat16

# 数据集
dataset_mixer:
  HuggingFaceH4/ultrafeedback_binarized: 1.0
dataset_splits:
  - train_prefs
  - test_prefs

# SimPO超参数
beta: 2.0                  # 奖励缩放（2.0-10.0）
gamma_beta_ratio: 0.5       # 目标边界（0-1）
loss_type: sigmoid          # sigmoid或hinge
sft_weight: 0.0             # 可选的SFT正则化

# 训练
learning_rate: 5e-7         # 关键：3e-7到1e-6
num_train_epochs: 1
per_device_train_batch_size: 1
gradient_accumulation_steps: 8

# 输出
output_dir: ./outputs/mistral-7b-simpo
```

**启动训练**：
```bash
accelerate launch --config_file accelerate_configs/deepspeed_zero3.yaml \
  scripts/run_simpo.py training_configs/mistral-7b-base-simpo.yaml
```

### 工作流程2：微调指令模型（Llama 3 8B）

**配置**（`llama3-8b-instruct-simpo.yaml`）：
```yaml
model_name_or_path: meta-llama/Meta-Llama-3-8B-Instruct

dataset_mixer:
  argilla/ultrafeedback-binarized-preferences-cleaned: 1.0

beta: 2.5
gamma_beta_ratio: 0.5
learning_rate: 5e-7
sft_weight: 0.1             # 添加SFT损失以保持能力

num_train_epochs: 1
per_device_train_batch_size: 2
gradient_accumulation_steps: 4
output_dir: ./outputs/llama3-8b-simpo
```

**启动**：
```bash
accelerate launch --config_file accelerate_configs/deepspeed_zero3.yaml \
  scripts/run_simpo.py training_configs/llama3-8b-instruct-simpo.yaml
```

### 工作流程3：推理密集型任务（较低学习率）

**对于数学/代码任务**：
```yaml
model_name_or_path: deepseek-ai/deepseek-math-7b-base

dataset_mixer:
  argilla/distilabel-math-preference-dpo: 1.0

beta: 5.0                   # 更高以获得更强信号
gamma_beta_ratio: 0.7       # 更大边界
learning_rate: 3e-7         # 推理任务用较低学习率
sft_weight: 0.0

num_train_epochs: 1
per_device_train_batch_size: 1
gradient_accumulation_steps: 16
```

## 何时使用与替代方案

**在以下情况下使用SimPO**：
- 想要比DPO更简单的训练（无参考模型）
- 有偏好数据（chosen/rejected对）
- 需要比DPO更好的性能
- 计算资源有限
- 单节点训练足够

**算法选择**：
- **SimPO**：最简单，性能最好，无需参考模型
- **DPO**：需要参考模型基线，更保守
- **PPO**：最大控制，需要奖励模型，复杂设置
- **GRPO**：内存高效RL，无需评论家

**使用替代方案**：
- **OpenRLHF**：多节点分布式训练，PPO/GRPO
- **TRL**：需要在一个框架中使用多种方法
- **DPO**：已建立的基线比较

## 常见问题

**问题：损失发散**

降低学习率：
```yaml
learning_rate: 3e-7  # 从5e-7降低
```

降低beta：
```yaml
beta: 1.0  # 从2.0降低
```

**问题：模型遗忘能力**

添加SFT正则化：
```yaml
sft_weight: 0.1  # 添加SFT损失分量
```

**问题：偏好分离差**

增加beta和边界：
```yaml
beta: 5.0            # 从2.0增加
gamma_beta_ratio: 0.8  # 从0.5增加
```

**问题：训练期间OOM**

降低批量大小：
```yaml
per_device_train_batch_size: 1
gradient_accumulation_steps: 16  # 维持有效批量
```

启用梯度检查点：
```yaml
gradient_checkpointing: true
```

## 高级主题

**损失函数**：有关sigmoid与hinge损失、数学公式及各者适用场景，请参阅[references/loss-functions.md](references/loss-functions.md)。

**超参数调优**：有关beta、gamma、学习率选择指南和模型大小特定推荐，请参阅[references/hyperparameters.md](references/hyperparameters.md)。

**数据集准备**：有关偏好数据格式、质量过滤和自定义数据集创建，请参阅[references/datasets.md](references/datasets.md)。

## 硬件要求

- **GPU**：推荐NVIDIA A100/H100
- **VRAM**：
  - 7B模型：1× A100 40GB（DeepSpeed ZeRO-3）
  - 8B模型：2× A100 40GB
  - 70B模型：8× A100 80GB
- **单节点**：DeepSpeed ZeRO-3足够
- **混合精度**：推荐BF16

**内存优化**：
- DeepSpeed ZeRO-3（默认配置）
- 梯度检查点
- Flash Attention 2

## 资源

- 论文：https://arxiv.org/abs/2405.14734（NeurIPS 2024）
- GitHub：https://github.com/princeton-nlp/SimPO
- 模型：https://huggingface.co/princeton-nlp
- Alignment Handbook：https://github.com/huggingface/alignment-handbook
