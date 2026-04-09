---
name: slime-rl-training
description: 提供使用slime（一个Megatron+SGLang框架）进行LLM后训练的RL指导。用于训练GLM模型、实现自定义数据生成工作流程，或需要紧密Megatron-LM集成以进行RL扩展。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [sglang-router>=0.2.3, ray, torch>=2.0.0, transformers>=4.40.0]
metadata:
  kclaw:
    tags: [强化学习, Megatron-LM, SGLang, GRPO, 后训练, GLM]

---

# slime：用于RL扩展的LLM后训练框架

slime是清华大学THUDM团队的LLM后训练框架，为GLM-4.5、GLM-4.6和GLM-4.7提供支持。它将Megatron-LM用于训练，SGLang用于高吞吐量rollout生成。

## 何时使用slime

**在以下情况下选择slime：**
- 需要与SGLang推理的本机Megatron-LM训练
- 使用灵活数据缓冲区的自定义数据生成工作流程
- 训练GLM、Qwen3、DeepSeek V3或Llama 3模型
- 需要生产级框架支持的研究（Z.ai）

**在以下情况下考虑替代方案：**
- 需要企业级稳定性功能 → 使用 **miles**
- 需要灵活的后端交换 → 使用 **verl**
- 需要PyTorch原生抽象 → 使用 **torchforge**

## 关键特性

- **训练**：支持完全并行（TP、PP、DP、SP）的Megatron-LM
- **Rollout**：基于SGLang的高吞吐量生成，带路由器
- **数据缓冲**：灵活的提示管理和样本存储
- **模型**：GLM-4.x、Qwen3、DeepSeek V3/R1、Llama 3

## 架构概述

```
┌─────────────────────────────────────────────────────────┐
│                    数据缓冲区                            │
│ - 提示初始化和管理                                      │
│ - 自定义数据生成和过滤                                  │
│ - Rollout样本存储                                      │
└─────────────┬───────────────────────────┬───────────────┘
              │                           │
┌─────────────▼───────────┐ ┌─────────────▼───────────────┐
│ 训练（Megatron-LM）     │ │ Rollout（SGLang + 路由器）  │
│ - Actor模型训练         │ │ - 响应生成                  │
│ - Critic（可选）         │ │ - 奖励/验证器输出            │
│ - 权重同步到rollout     │ │ - 多轮支持                  │
└─────────────────────────┘ └─────────────────────────────┘
```

## 安装

```bash
# 推荐：Docker
docker pull slimerl/slime:latest
docker run --rm --gpus all --ipc=host --shm-size=16g \
  -it slimerl/slime:latest /bin/bash

# 容器内
cd /root/slime && pip install -e . --no-deps
```

### 从源码安装

```bash
git clone https://github.com/THUDM/slime.git
cd slime
pip install -r requirements.txt
pip install -e .
```

## 快速开始：GRPO训练

```bash
# 源模型配置
source scripts/models/qwen3-4B.sh

# 启动训练
python train.py \
    --actor-num-nodes 1 \
    --actor-num-gpus-per-node 4 \
    --rollout-num-gpus 4 \
    --advantage-estimator grpo \
    --use-kl-loss --kl-loss-coef 0.001 \
    --rollout-batch-size 32 \
    --n-samples-per-prompt 8 \
    --global-batch-size 256 \
    --num-rollout 3000 \
    --prompt-data /path/to/data.jsonl \
    ${MODEL_ARGS[@]} ${CKPT_ARGS[@]}
```

---

## 工作流程1：标准GRPO训练

使用组相对优势训练推理模型。

### 前置条件清单
- [ ] Docker环境或已安装Megatron-LM + SGLang
- [ ] 模型检查点（HuggingFace或Megatron格式）
- [ ] JSONL格式的训练数据

### 步骤1：准备数据

```python
# data.jsonl格式
{"prompt": "2 + 2是多少？", "label": "4"}
{"prompt": "解：3x = 12", "label": "x = 4"}
```

或聊天格式：
```python
{
    "prompt": [
        {"role": "system", "content": "你是一个数学导师。"},
        {"role": "user", "content": "15 + 27是多少？"}
    ],
    "label": "42"
}
```

### 步骤2：配置模型

选择预配置的模型脚本：

```bash
# 列出可用模型
ls scripts/models/
# glm4-9B.sh, qwen3-4B.sh, qwen3-30B-A3B.sh, deepseek-v3.sh, llama3-8B.sh, ...

# 源化你的模型
source scripts/models/qwen3-4B.sh
```

### 步骤3：启动训练

```bash
python train.py \
    --actor-num-nodes 1 \
    --actor-num-gpus-per-node 8 \
    --rollout-num-gpus 8 \
    --advantage-estimator grpo \
    --use-kl-loss \
    --kl-loss-coef 0.001 \
    --prompt-data /path/to/train.jsonl \
    --input-key prompt \
    --label-key label \
    --apply-chat-template \
    --rollout-batch-size 32 \
    --n-samples-per-prompt 8 \
    --global-batch-size 256 \
    --num-rollout 3000 \
    --save-interval 100 \
    --eval-interval 50 \
    ${MODEL_ARGS[@]}
```

### 步骤4：监控训练
- [ ] 检查TensorBoard：`tensorboard --logdir outputs/`
- [ ] 验证奖励曲线正在增加
- [ ] 监控跨节点的GPU利用率

---

## 工作流程2：异步训练

使用异步模式通过重叠rollout和训练来提高吞吐量。

### 何时使用异步
- 带长生成时间的大模型
- 同步模式下高GPU空闲时间
- 有足够的内存进行缓冲

### 启动异步训练

```bash
python train_async.py \
    --actor-num-nodes 1 \
    --actor-num-gpus-per-node 8 \
    --rollout-num-gpus 8 \
    --advantage-estimator grpo \
    --async-buffer-size 4 \
    --prompt-data /path/to/train.jsonl \
    ${MODEL_ARGS[@]}
```

### 异步特定参数

```bash
--async-buffer-size 4        # 要缓冲的rollout数
--update-weights-interval 2  # 每N个rollout同步权重
```

---

## 工作流程3：多轮Agent训练

用于训练具有工具使用或多步推理的Agent。

### 前置条件
- [ ] 自定义生成函数用于多轮逻辑
- [ ] 工具/环境接口

### 步骤1：定义自定义生成函数

```python
# custom_generate.py
async def custom_generate(args, samples, evaluation=False):
    """带工具调用的多轮生成。"""
    for sample in samples:
        conversation = sample.prompt

        for turn in range(args.max_turns):
            # 生成响应
            response = await generate_single(conversation)

            # 检查工具调用
            tool_call = extract_tool_call(response)
            if tool_call:
                tool_result = execute_tool(tool_call)
                conversation.append({"role": "assistant", "content": response})
                conversation.append({"role": "tool", "content": tool_result})
            else:
                break

        sample.response = response
        sample.reward = compute_reward(sample)

    return samples
```

### 步骤2：使用自定义函数启动

```bash
python train.py \
    --custom-generate-function-path custom_generate.py \
    --max-turns 5 \
    --prompt-data /path/to/agent_data.jsonl \
    ${MODEL_ARGS[@]}
```

参见`examples/search-r1/`获取完整的多轮搜索示例。

---

## 配置参考

### 三类参数

slime使用三类参数：

**1. Megatron参数**（直接传递）：
```bash
--tensor-model-parallel-size 2
--pipeline-model-parallel-size 1
--num-layers 32
--hidden-size 4096
```

**2. SGLang参数**（前缀为`--sglang-`）：
```bash
--sglang-mem-fraction-static 0.8
--sglang-context-length 8192
--sglang-log-level INFO
```

**3. slime参数**：
```bash
# 资源分配
--actor-num-nodes 1
--actor-num-gpus-per-node 8
--rollout-num-gpus 8
--colocate  # 训练/推理共享GPU

# 数据
--prompt-data /path/to/data.jsonl
--input-key prompt
--label-key label

# 训练循环
--num-rollout 3000
--rollout-batch-size 32
--n-samples-per-prompt 8
--global-batch-size 256

# 算法
--advantage-estimator grpo  # 或: gspo, ppo, reinforce_plus_plus
--use-kl-loss
--kl-loss-coef 0.001
```

### 关键约束

```
rollout_batch_size × n_samples_per_prompt = global_batch_size × num_steps_per_rollout
```

示例：32 × 8 = 256 × 1

---

## 数据缓冲系统

slime的数据缓冲支持灵活的数据管理：

### 基础数据源

```python
class RolloutDataSource:
    def get_samples(self, num_samples):
        """从数据集获取提示。"""
        return self.dataset.sample(num_samples)

    def add_samples(self, samples):
        """生成后调用（默认无操作）。"""
        pass
```

### 缓冲数据源（离线策略）

```python
class RolloutDataSourceWithBuffer(RolloutDataSource):
    def __init__(self):
        self.buffer = []

    def add_samples(self, samples):
        """存储生成的样本以供重用。"""
        self.buffer.extend(samples)

    def buffer_filter(self, args, buffer, num_samples):
        """自定义选择逻辑（优先级、分层等）。"""
        return select_best(buffer, num_samples)
```

---

## 常见问题和解决方案

### 问题：SGLang引擎崩溃

**症状**：推理引擎在训练中途崩溃

**解决方案**：
```bash
# 启用容错
--use-fault-tolerance

# 增加内存分配
--sglang-mem-fraction-static 0.85

# 减少批量大小
--rollout-batch-size 16
```

### 问题：权重同步超时

**症状**：rollout后训练挂起

**解决方案**：
```bash
# 增加同步间隔
--update-weights-interval 5

# 使用共置模式（无网络传输）
--colocate
```

### 问题：训练期间OOM

**症状**：反向传播时CUDA OOM

**解决方案**：
```bash
# 启用梯度检查点
--recompute-activations

# 减少微批次大小
--micro-batch-size 1

# 启用序列并行
--sequence-parallel
```

### 问题：数据加载慢

**症状**：数据获取期间GPU空闲

**解决方案**：
```bash
# 增加数据workers
--num-data-workers 4

# 使用流式数据集
--streaming-data
```

---

## 支持的模型

| 模型系列 | 配置 |
|--------------|----------------|
| GLM | GLM-4.5, GLM-4.6, GLM-4.7, GLM-Z1-9B |
| Qwen | Qwen3 (4B, 8B, 30B-A3B), Qwen3-MoE, Qwen2.5 |
| DeepSeek | V3, V3.1, R1 |
| Llama | Llama 3 (8B, 70B) |
| 其他 | Kimi K2, Moonlight-16B |

每个模型在`scripts/models/`中都有预配置的脚本。

---

## 高级主题

### 共置模式

在训练和推理之间共享GPU以减少内存：

```bash
python train.py \
    --colocate \
    --actor-num-gpus-per-node 8 \
    --sglang-mem-fraction-static 0.4 \
    ${MODEL_ARGS[@]}
```

### 自定义奖励模型

```python
# custom_rm.py
class CustomRewardModel:
    def __init__(self, model_path):
        self.model = load_model(model_path)

    def compute_reward(self, prompts, responses):
        inputs = self.tokenize(prompts, responses)
        scores = self.model(inputs)
        return scores.tolist()
```

```bash
--custom-rm-path custom_rm.py
```

### 评估多任务

```bash
--eval-prompt-data aime /path/to/aime.jsonl \
--eval-prompt-data gsm8k /path/to/gsm8k.jsonl \
--n-samples-per-eval-prompt 16
```

---

## 资源

- **文档**：https://thudm.github.io/slime/
- **GitHub**：https://github.com/THUDM/slime
- **博客**：https://lmsys.org/blog/2025-07-09-slime/
- **示例**：参见`examples/`目录，包含14+个完整示例
