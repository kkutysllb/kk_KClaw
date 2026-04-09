# 损失函数

SimPO损失函数的完整指南和数学公式。

## 概述

SimPO支持两种损失类型：
- **Sigmoid**（默认）- 平滑、可微分的损失
- **Hinge** - 基于边界、稀疏的损失

两者都是无参考的（无需参考模型）。

## SimPO损失公式

### 核心计算

**步骤1：对数概率比**：
```
pi_logratios = log P_θ(y_chosen|x) - log P_θ(y_rejected|x)
```

**步骤2：应用目标边界**：
```
logits = pi_logratios - γ/β
```
其中：
- γ/β = `gamma_beta_ratio`（目标边界）

**步骤3：计算损失**（取决于损失类型）

### Sigmoid损失（默认）

**公式**：
```
L = -log σ(β * logits) * (1 - ε) - log σ(-β * logits) * ε
```

其中：
- β = `beta`（奖励缩放）
- σ = sigmoid函数
- ε = `label_smoothing`（默认0.0）

**实现**：
```python
losses = (
    -F.logsigmoid(self.beta * logits) * (1 - self.label_smoothing)
    - F.logsigmoid(-self.beta * logits) * self.label_smoothing
)
```

**特点**：
- 平滑、连续梯度
- 概率解释
- 大多数任务的標準选择
- 与较高的beta值配合良好

### Hinge损失

**公式**：
```
L = max(0, 1 - β * logits)
```

**实现**：
```python
losses = torch.relu(1 - self.beta * logits)
```

**特点**：
- 非平滑（在logits = 1/β处有拐点）
- 基于边界（SVM风格）
- 可能导致更稀疏的解
- 较少使用

## 与DPO的对比

### DPO损失（需要参考模型）

**公式**：
```
L_DPO = -E[log σ(β * log(π_θ(y_w|x)/π_ref(y_w|x)) - β * log(π_θ(y_l|x)/π_ref(y_l|x)))]
```

**关键特征**：
- 需要参考模型π_ref
- 按参考对数概率归一化
- 更保守（保持接近参考）

### SimPO损失（无参考）

**公式**：
```
L_SimPO = -log σ(β * (log π_θ(y_w|x) - log π_θ(y_l|x) - γ/β))
```

**关键特征**：
- 无需参考模型
- 直接偏好优化
- 目标边界γ/β控制偏好强度
- 更高效（更少的模型前向传递）

**可视化对比**：
```
DPO:    [策略] - [参考] → 损失
SimPO:  [策略]               → 损失
```

## 平均对数概率奖励

### 计算

**每token对数概率**：
```python
# 获取每个token的对数概率
per_token_logps = log_softmax(logits).gather(dim=-1, index=labels)

# 创建掩码以忽略填充
loss_mask = (labels != label_pad_token_id)
```

**平均对数概率**（如果`average_log_prob=True`）：
```python
avg_logp = (per_token_logps * loss_mask).sum(-1) / loss_mask.sum(-1)
```

**求和对数概率**（如果`average_log_prob=False`）：
```python
sum_logp = (per_token_logps * loss_mask).sum(-1)
```

**为什么使用平均？**
- 按序列长度归一化
- 防止偏向更短/更长的响应
- SimPO的标准做法

### 奖励指标

**被选中的奖励**：
```python
chosen_rewards = beta * policy_chosen_logps.detach()
```

**被拒绝的奖励**：
```python
rejected_rewards = beta * policy_rejected_logps.detach()
```

**奖励边界**：
```python
reward_margin = chosen_rewards.mean() - rejected_rewards.mean()
```

## 标签平滑

### 带平滑的公式

**Sigmoid损失**：
```
L = -log σ(β * logits) * (1 - ε) - log σ(-β * logits) * ε
```

**效果**：
- ε = 0.0：无平滑（默认）
- ε = 0.1：10%平滑（软标签）
- ε = 0.5：最大平滑

**何时使用**：
- 嘈杂的偏好标签
- 不确定的偏好
- 防止过度自信

**配置**：
```yaml
label_smoothing: 0.1  # 10%平滑
```

## SFT正则化

### 组合损失

**带SFT分量**：
```
L_total = L_SimPO + λ * L_SFT
```

其中：
- L_SFT = 被选中响应上的交叉熵损失
- λ = `sft_weight`（0.0到1.0）

**实现**：
```python
if self.sft_weight > 0:
    sft_loss = -policy_chosen_logps
    total_loss = simpo_loss + self.sft_weight * sft_loss
```

**何时使用**：
- 保留模型能力
- 防止灾难性遗忘
- 微调指令模型

**权衡**：
- 更高的sft_weight：保留能力，较少对齐
- 更低的sft_weight：更强对齐，可能遗忘能力

**配置**：
```yaml
sft_weight: 0.1  # 10% SFT正则化
```

## 损失类型选择

### Sigmoid vs Hinge

| 方面 | Sigmoid | Hinge |
|--------|---------|-------|
| 平滑度 | 平滑 | 非平滑 |
| 梯度 | 连续 | 在边界处不连续 |
| 稀疏性 | 密集解 | 稀疏解 |
| 可解释性 | 概率 | 几何边界 |
| 用途 | **通用** | 基于边界的任务 |
| 推荐 | **默认选择** | 实验性 |

**配置**：
```yaml
# Sigmoid（默认）
loss_type: sigmoid

# Hinge（替代）
loss_type: hinge
```

## 数学性质

### 梯度分析

**Sigmoid损失梯度**：
```
∂L/∂logits = -β * σ(-β * logits) * (1 - ε) + β * σ(β * logits) * ε
```

**Hinge损失梯度**：
```
∂L/∂logits = -β   如果 logits < 1/β
             0     否则
```

**含义**：
- Sigmoid：始终提供梯度信号
- Hinge：满足边界时无梯度

### 收敛行为

**Sigmoid**：
- 渐近接近零损失
- 即使有大边界也继续优化
- 更平滑的训练曲线

**Hinge**：
- 在边界处达到零损失
- 一旦满足边界就停止优化
- 可能有训练平台期

## 完整损失示例

### 示例1：基础SimPO（Sigmoid）

**配置**：
```yaml
beta: 2.0
gamma_beta_ratio: 0.5
loss_type: sigmoid
label_smoothing: 0.0
sft_weight: 0.0
```

**损失计算**：
```python
# 步骤1：计算对数概率
chosen_logps = avg_log_prob(policy(chosen))    # 例如 -1.2
rejected_logps = avg_log_prob(policy(rejected)) # 例如 -2.5

# 步骤2：对数比和边界
pi_logratios = -1.2 - (-2.5) = 1.3
logits = 1.3 - 0.5 = 0.8

# 步骤3：Sigmoid损失
loss = -log(sigmoid(2.0 * 0.8))
     = -log(sigmoid(1.6))
     = -log(0.832)
     = 0.184
```

### 示例2：带SFT的SimPO

**配置**：
```yaml
beta: 2.5
gamma_beta_ratio: 0.5
loss_type: sigmoid
sft_weight: 0.1
```

**损失计算**：
```python
# SimPO损失（如上）
simpo_loss = 0.184

# SFT损失
sft_loss = -chosen_logps = -(-1.2) = 1.2

# 总损失
total_loss = simpo_loss + 0.1 * sft_loss
           = 0.184 + 0.12
           = 0.304
```

## 调试

### 检查奖励边界

**低边界（< 0.5）**：
- 偏好未被学习
- 增加beta或gamma_beta_ratio

**高边界（> 5.0）**：
- 可能过拟合
- 减少beta或学习率

**监控**：
```python
reward_margin = chosen_rewards.mean() - rejected_rewards.mean()
print(f"Reward margin: {reward_margin:.2f}")
```

### 检查对数概率

**典型值**：
- 选中：-1.0到-2.0（越高越好）
- 拒绝：-2.0到-4.0（越低越差）

**警告信号**：
- 两者都非常负（< -10）：模型未在学习
- 两者都非常正（> 0）：数值不稳定

## 参考

- SimPO论文：https://arxiv.org/abs/2405.14734
- DPO论文：https://arxiv.org/abs/2305.18290
- 实现：https://github.com/princeton-nlp/SimPO
