# 超参数

SimPO超参数选择和调优的完整指南。

## 概述

SimPO中的关键超参数：
1. **学习率** - 最关键
2. **Beta（β）** - 奖励缩放
3. **Gamma-Beta比（γ/β）** - 目标边界
4. **SFT权重** - 正则化强度

## 学习率

### 推荐范围

**按模型大小**：
| 模型大小 | 学习率 | 说明 |
|------------|---------------|-------|
| 1B-3B | 5e-7到1e-6 | 高端安全 |
| 7B-8B | 3e-7到5e-7 | **标准** |
| 13B-30B | 1e-7到3e-7 | 为稳定性较低 |
| 70B+ | 5e-8到1e-7 | 非常保守 |

**按任务类型**：
| 任务 | 学习率 | 原因 |
|------|---------------|--------|
| 通用聊天 | 5e-7 | 标准 |
| 代码生成 | 3e-7 | **精确推理** |
| 数学推理 | 3e-7 | **谨慎优化** |
| 创意写作 | 1e-6 | 更激进可以 |

### 为什么学习率重要

**太高**（7B > 1e-6）：
- 损失发散
- 灾难性遗忘
- 训练不稳定

**太低**（7B < 1e-7）：
- 收敛非常慢
- 可能无法在时间内完成
- 训练不足

**最优**（7B为3e-7到5e-7）：
- 稳定收敛
- 良好的最终性能
- 高效训练

### 配置示例

**Mistral 7B（通用）**：
```yaml
learning_rate: 5e-7
num_train_epochs: 1
warmup_ratio: 0.1
lr_scheduler_type: cosine
```

**Llama 3 8B（推理）**：
```yaml
learning_rate: 3e-7
num_train_epochs: 1
warmup_ratio: 0.1
lr_scheduler_type: cosine
```

**Gemma 2 9B（创意）**：
```yaml
learning_rate: 1e-6
num_train_epochs: 1
warmup_ratio: 0.1
lr_scheduler_type: linear
```

## Beta（β）

### 推荐值

**范围**：2.0到10.0（比DPO的0.01-0.1高得多）

**按偏好强度**：
| Beta | 偏好强度 | 用途 |
|------|-------------------|----------|
| 1.0-2.0 | 弱 | 微妙的偏好 |
| 2.0-5.0 | **标准** | 通用对齐 |
| 5.0-10.0 | 强 | 清晰的偏好 |

**默认**：2.0到2.5

### 为什么Beta重要

**低beta**（< 2.0）：
- 弱奖励信号
- 慢偏好学习
- 可能欠拟合

**高beta**（> 10.0）：
- 非常强的奖励信号
- 过拟合风险
- 可能忽略弱偏好

**最优**（2.0-5.0）：
- 平衡奖励缩放
- 稳定训练
- 良好泛化

### 与Gamma的相互作用

**Beta和gamma一起**：
```
目标边界（奖励空间）= gamma
目标边界（logit空间）= gamma / beta
```

**示例**：
```yaml
beta: 2.0
gamma_beta_ratio: 0.5
# 有效gamma = 2.0 * 0.5 = 1.0
```

### 配置示例

**弱偏好**：
```yaml
beta: 2.0
gamma_beta_ratio: 0.3  # 小边界
```

**标准**：
```yaml
beta: 2.5
gamma_beta_ratio: 0.5  # 默认
```

**强偏好**：
```yaml
beta: 5.0
gamma_beta_ratio: 0.7  # 更大边界
```

## Gamma-Beta比（γ/β）

### 推荐值

**范围**：0.0到1.0

**按场景**：
| 比值 | 边界 | 用途 |
|-------|--------|----------|
| 0.0-0.3 | 小 | 弱偏好数据 |
| 0.4-0.6 | **标准** | 通用 |
| 0.7-1.0 | 大 | 非常清晰的偏好 |

**默认**：0.5

### 为什么Gamma重要

**低gamma**（< 0.3）：
- 小目标边界
- 不那么激进的对齐
- 更保守

**高gamma**（> 0.7）：
- 大目标边界
- 更强对齐
- 更激进

**最优**（0.4-0.6）：
- 平衡边界
- 稳定训练
- 良好对齐

### 数学含义

**在损失函数中**：
```python
logits = pi_logratios - gamma_beta_ratio
loss = -log(sigmoid(beta * logits))
```

**解释**：
- gamma_beta_ratio移动决策边界
- 更高比值 = 需要更大的对数概率差异
- 控制偏好的"清晰度"

### 配置示例

**嘈杂偏好**：
```yaml
gamma_beta_ratio: 0.3  # 更小边界，更宽容
```

**标准**：
```yaml
gamma_beta_ratio: 0.5  # 默认
```

**高质量偏好**：
```yaml
gamma_beta_ratio: 0.8  # 更大边界，更严格
```

## SFT权重

### 推荐值

**范围**：0.0到1.0

**按模型类型**：
| 模型类型 | SFT权重 | 原因 |
|------------|-----------|--------|
| 基础模型 | 0.0 | 无先前能力 |
| **指令模型** | 0.05-0.1 | 保持指令遵循 |
| 聊天模型 | 0.1-0.2 | 保持对话技能 |

**默认**：0.0（无SFT正则化）

### 为什么SFT权重重要

**零SFT**（0.0）：
- 纯偏好优化
- 可能遗忘能力
- 基础模型的标准

**低SFT**（0.05-0.1）：
- 平衡方法
- **指令模型推荐**
- 轻微能力保留

**高SFT**（> 0.2）：
- 强能力保留
- 更弱偏好对齐
- 可能减少对齐收益

### 权衡

```
总损失 = SimPO损失 + (sft_weight * SFT损失)
```

**示例**：
```yaml
sft_weight: 0.1
# 90%偏好优化 + 10%能力保留
```

### 配置示例

**基础模型（无SFT）**：
```yaml
model_name_or_path: mistralai/Mistral-7B-v0.1
sft_weight: 0.0
```

**指令模型（轻SFT）**：
```yaml
model_name_or_path: meta-llama/Meta-Llama-3-8B-Instruct
sft_weight: 0.1
```

**聊天模型（中等SFT）**：
```yaml
model_name_or_path: HuggingFaceH4/zephyr-7b-beta
sft_weight: 0.2
```

## 模型大小特定推荐

### 7B模型（Mistral、Llama 3）

**标准配置**：
```yaml
learning_rate: 5e-7
beta: 2.0
gamma_beta_ratio: 0.5
sft_weight: 0.0  # 指令模型为0.1
num_train_epochs: 1
per_device_train_batch_size: 2
gradient_accumulation_steps: 4
```

### 8B-13B模型

**标准配置**：
```yaml
learning_rate: 3e-7
beta: 2.5
gamma_beta_ratio: 0.5
sft_weight: 0.1  # 指令模型
num_train_epochs: 1
per_device_train_batch_size: 1
gradient_accumulation_steps: 8
```

### 70B模型

**标准配置**：
```yaml
learning_rate: 1e-7
beta: 2.0
gamma_beta_ratio: 0.5
sft_weight: 0.05
num_train_epochs: 1
per_device_train_batch_size: 1
gradient_accumulation_steps: 16
```

## 批量大小和梯度累积

### 有效批量大小

```
有效批量大小 = per_device_batch_size * num_gpus * grad_accum_steps
```

**推荐有效批量大小**：
- 7B：128-256
- 13B：64-128
- 70B：32-64

### 配置示例

**单GPU（A100 40GB）**：
```yaml
per_device_train_batch_size: 1
gradient_accumulation_steps: 128  # 有效批量 = 128
```

**4 GPU（A100 40GB）**：
```yaml
per_device_train_batch_size: 2
gradient_accumulation_steps: 16  # 有效批量 = 2*4*16 = 128
```

**8 GPU（A100 80GB）**：
```yaml
per_device_train_batch_size: 2
gradient_accumulation_steps: 8  # 有效批量 = 2*8*8 = 128
```

## 损失类型

### Sigmoid vs Hinge

**Sigmoid**（默认，推荐）：
```yaml
loss_type: sigmoid
label_smoothing: 0.0
```

**Hinge**（实验性）：
```yaml
loss_type: hinge
# hinge不支持标签平滑
```

**何时使用hinge**：
- 基于边界的任务
- SVM风格优化
- 实验目的

**通常**：坚持使用sigmoid

## 调优指南

### 步骤1：从默认开始

```yaml
learning_rate: 5e-7  # 对于7B
beta: 2.0
gamma_beta_ratio: 0.5
sft_weight: 0.0  # 指令模型为0.1
loss_type: sigmoid
```

### 步骤2：监控训练

**每100步检查**：
- 损失曲线（应平稳下降）
- 奖励边界（应增加）
- 选中/拒绝的对数概率（应分离）

### 步骤3：按需调整

**如果损失发散**：
```yaml
learning_rate: 3e-7  # 从5e-7减少
beta: 1.0           # 从2.0减少
```

**如果损失早期平台**：
```yaml
learning_rate: 1e-6  # 从5e-7增加
beta: 5.0           # 从2.0增加
```

**如果模型遗忘**：
```yaml
sft_weight: 0.2  # 从0.0增加
```

## 完整配置示例

### Mistral 7B基础（标准）

```yaml
model_name_or_path: mistralai/Mistral-7B-v0.1
dataset_mixer:
  HuggingFaceH4/ultrafeedback_binarized: 1.0

learning_rate: 5e-7
beta: 2.0
gamma_beta_ratio: 0.5
loss_type: sigmoid
sft_weight: 0.0

num_train_epochs: 1
per_device_train_batch_size: 2
gradient_accumulation_steps: 4
warmup_ratio: 0.1
lr_scheduler_type: cosine

bf16: true
gradient_checkpointing: true
```

### Llama 3 8B指令（推理）

```yaml
model_name_or_path: meta-llama/Meta-Llama-3-8B-Instruct
dataset_mixer:
  argilla/distilabel-math-preference-dpo: 1.0

learning_rate: 3e-7
beta: 5.0
gamma_beta_ratio: 0.7
loss_type: sigmoid
sft_weight: 0.1

num_train_epochs: 1
per_device_train_batch_size: 1
gradient_accumulation_steps: 16
warmup_ratio: 0.1
lr_scheduler_type: cosine
```

## 参考

- SimPO论文：https://arxiv.org/abs/2405.14734
- Alignment Handbook：https://github.com/huggingface/alignment-handbook
