# slime故障排除指南

## 常见问题和解决方案

### SGLang问题

#### 问题：SGLang引擎崩溃

**症状**：推理引擎在训练中途死亡，连接错误

**解决方案**：

1. **启用容错**：
```bash
--use-fault-tolerance
```

2. **增加内存分配**：
```bash
--sglang-mem-fraction-static 0.85  # 从0.8增加
```

3. **减少批量大小**：
```bash
--rollout-batch-size 16  # 从32减少
```

4. **禁用CUDA图**（用于调试）：
```bash
--sglang-disable-cuda-graph
```

#### 问题：SGLang路由器负载不平衡

**症状**：某些SGLang引擎过载而其他空闲

**解决方案**：

1. **调整路由策略**：
```bash
--sglang-router-strategy round_robin
```

2. **增加引擎数量**：
```bash
--rollout-num-gpus-per-engine 1  # 更多引擎，每个引擎更少GPU
```

### 权重同步问题

#### 问题：权重同步超时

**症状**：rollout后训练挂起，超时错误

**解决方案**：

1. **增加同步间隔**（异步模式）：
```bash
--update-weights-interval 5  # 从2增加
```

2. **使用共置模式**（消除网络传输）：
```bash
--colocate
```

3. **检查网络带宽**：
```bash
# 验证InfiniBand已启用
ibstat
```

#### 问题：多节点中权重同步失败

**症状**：节点未能接收更新的权重

**解决方案**：

1. **设置NCCL环境**：
```bash
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=eth0
export NCCL_IB_DISABLE=0
```

2. **增加超时**：
```bash
export NCCL_TIMEOUT=1800
```

### 内存问题

#### 问题：训练期间OOM

**症状**：反向传播时CUDA OOM

**解决方案**：

1. **启用梯度检查点**：
```bash
--recompute-activations
```

2. **减少微批次大小**：
```bash
--micro-batch-size 1
```

3. **启用序列并行**：
```bash
--sequence-parallel
```

4. **减少全局批量大小**：
```bash
--global-batch-size 128  # 从256减少
```

#### 问题：共置模式OOM

**症状**：训练和推理同时在相同GPU上运行时OOM

**解决方案**：

1. **减少SGLang内存**：
```bash
--sglang-mem-fraction-static 0.4  # 从0.8减少
```

2. **启用卸载**：
```bash
--offload-optimizer-states
```

3. **使用更小的序列长度**：
```bash
--seq-length 2048  # 从4096减少
```

### 数据加载问题

#### 问题：数据加载慢

**症状**：数据获取期间GPU空闲，GPU利用率低

**解决方案**：

1. **增加数据workers**：
```bash
--num-data-workers 4
```

2. **使用流式数据集**：
```bash
--streaming-data
```

3. **预分词数据**：
```python
# 离线预处理数据
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained("model_path")
# 保存分词后的数据
```

#### 问题：数据格式错误

**症状**：KeyError、缺少字段、解析失败

**解决方案**：

1. **验证数据格式**：
```python
import json
with open("data.jsonl") as f:
    for line in f:
        data = json.loads(line)
        assert "prompt" in data, "缺少prompt字段"
        assert "label" in data, "缺少label字段"
```

2. **检查键名**：
```bash
--input-key prompt  # 必须与你的数据匹配
--label-key label   # 必须与你的数据匹配
```

### 训练稳定性问题

#### 问题：损失爆炸/NaN

**症状**：损失变为NaN或爆炸

**解决方案**：

1. **降低学习率**：
```bash
--lr 1e-6  # 从5e-6降低
```

2. **启用梯度裁剪**：
```bash
--clip-grad 1.0
```

3. **检查数据问题**：
```python
# 验证没有空提示或空响应
for sample in dataset:
    assert len(sample["prompt"]) > 0
```

4. **使用BF16而非FP16**：
```bash
--bf16  # 数值更稳定
```

#### 问题：奖励崩溃

**症状**：奖励降至零，模型输出垃圾

**解决方案**：

1. **增加KL惩罚**：
```bash
--kl-loss-coef 0.01  # 从0.001增加
```

2. **减少样本数量**：
```bash
--n-samples-per-prompt 4  # 从8减少
```

3. **验证奖励函数**：
```python
# 独立测试奖励函数
from custom_rm import reward_func
sample = Sample(prompt="test", response="test response")
reward = reward_func(args, sample)
print(f"奖励: {reward}")  # 应该合理
```

### 异步训练问题

#### 问题：异步训练不支持共置

**症状**：将`--colocate`与`train_async.py`一起使用时出错

**解决方案**：共置模式不支持异步训练。使用独立GPU：
```bash
# 移除--colocate标志
python train_async.py \
    --actor-num-gpus-per-node 4 \
    --rollout-num-gpus 4 \
    # 不要加--colocate
```

#### 问题：异步模式中权重过时

**症状**：策略发散，行为不一致

**解决方案**：

1. **减少异步缓冲区大小**：
```bash
--async-buffer-size 2  # 从4减少
```

2. **增加权重更新频率**：
```bash
--update-weights-interval 1  # 每次rollout同步
```

### 多轮训练问题

#### 问题：工具响应包含在损失中

**症状**：模型学习逐字输出工具响应

**解决方案**：在自定义生成函数中正确设置损失掩码：
```python
def build_loss_mask(sample):
    """创建排除工具响应的损失掩码。"""
    mask = []
    for i, token in enumerate(sample.tokens):
        if is_tool_response(token, sample.metadata):
            mask.append(0)  # 不计算损失
        else:
            mask.append(1)  # 计算损失
    return mask
```

#### 问题：多轮上下文太长

**症状**：多轮对话中OOM或截断

**解决方案**：

1. **限制对话历史**：
```python
# 在自定义生成函数中
conversation = sample.prompt[-10:]  # 保留最后10轮
```

2. **增加上下文长度**：
```bash
--sglang-context-length 16384
```

### 检查点问题

#### 问题：检查点加载失败

**症状**：无法加载保存的检查点

**解决方案**：

1. **验证检查点路径**：
```bash
ls -la /path/to/checkpoint/
```

2. **检查并行度匹配**：
```bash
# 检查点以TP=2保存，必须以TP=2加载
--tensor-model-parallel-size 2
```

3. **转换HuggingFace到Megatron**（如需要）：
```bash
python tools/convert_hf_to_megatron.py \
    --hf_model_path /path/to/hf/model \
    --save_path /path/to/megatron/checkpoint
```

### 调试技巧

#### 启用详细日志

```bash
--log-level DEBUG
export SLIME_DEBUG=1
```

#### 检查GPU利用率

```bash
watch -n 1 nvidia-smi
```

#### 监控训练

```bash
tensorboard --logdir outputs/
```

#### 独立测试自定义函数

```python
# 测试奖励函数
import asyncio
from custom_rm import reward_func

async def test():
    sample = Sample(prompt="test", response="test", label="expected")
    reward = await reward_func(args, sample)
    print(f"奖励: {reward}")

asyncio.run(test())
```

## 约束参考

需要记住的关键约束：

```
rollout_batch_size × n_samples_per_prompt = global_batch_size × num_steps_per_rollout
```

示例：`32 × 8 = 256 × 1`

## 资源

- GitHub Issues：https://github.com/THUDM/slime/issues
- 文档：https://thudm.github.io/slime/
- 示例：`examples/`目录
