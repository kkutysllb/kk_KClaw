# slime API参考

## 架构概述

slime通过Ray orchestrator以三模块架构运行：

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

## 核心数据结构

### Sample对象

`Sample`对象是在`slime/utils/types.py`中定义的核心数据结构：

```python
from slime.utils.types import Sample

@dataclass
class Sample:
    # 核心字段
    group_index: Optional[int]              # 批处理的组索引
    index: Optional[int]                    # 样本索引
    prompt: str | list[dict] = ""           # 输入提示或聊天历史
    tokens: list[int] = field(default_factory=list)  # Token ID
    response: str = ""                      # 生成的响应
    response_length: int = 0                # 响应token长度
    label: Optional[str] = None             # 真实标签
    reward: Optional[float | dict] = None   # RL奖励信号
    loss_mask: Optional[list[int]] = None   # 1=计算损失，0=掩码
    status: Status = Status.PENDING         # 样本状态
    metadata: dict = field(default_factory=dict)  # 自定义数据

    # 多模态支持
    multimodal_inputs: Optional[Any] = None       # 原始多模态数据（图像、视频）
    multimodal_train_inputs: Optional[Any] = None # 处理后的多模态数据（pixel_values）

    # Rollout跟踪
    weight_versions: list[str] = field(default_factory=list)
    rollout_log_probs: Optional[list[float]] = None    # 来自SGLang的对数概率
    rollout_routed_experts: Optional[list[list[int]]] = None  # 专家路由（MoE）

    # 控制字段
    remove_sample: bool = False
    generate_function_path: Optional[str] = None
    train_metadata: Optional[dict] = None
    non_generation_time: float = 0.0

    # 投机解码信息（嵌套dataclass）
    @dataclass
    class SpecInfo:
        spec_accept_token_num: int = 0
        spec_draft_token_num: int = 0
        spec_verify_ct: int = 0
        completion_token_num: int = 0
```

### Status枚举

```python
class Status(Enum):
    PENDING = "pending"           # 尚未处理
    COMPLETED = "completed"       # 成功生成
    TRUNCATED = "truncated"       # 达到最大长度
    ABORTED = "aborted"           # 生成中止
    FAILED = "failed"             # 生成失败
```

## 配置系统

slime使用三类命令行参数：

### 1. Megatron参数

所有Megatron-LM参数直接支持：

```bash
--tensor-model-parallel-size 2
--pipeline-model-parallel-size 1
--num-layers 32
--hidden-size 4096
--num-attention-heads 32
--seq-length 4096
--micro-batch-size 1
--global-batch-size 256
```

### 2. SGLang参数

SGLang参数前缀为`--sglang-`：

```bash
--sglang-mem-fraction-static 0.8   # GPU内存用于KV缓存
--sglang-context-length 8192       # 最大上下文长度
--sglang-log-level INFO            # 日志详细程度
--sglang-tp-size 2                 # 张量并行
--sglang-disable-cuda-graph        # 禁用CUDA图
```

### 3. slime特定参数

定义在`slime/utils/arguments.py`：

```bash
# 资源分配
--actor-num-nodes 1                # 训练节点
--actor-num-gpus-per-node 8        # 每个训练节点的GPU
--rollout-num-gpus 8               # 总rollout GPU
--rollout-num-gpus-per-engine 2    # 每个SGLang引擎的GPU
--colocate                         # 训练/推理共享GPU

# 数据配置
--prompt-data /path/to/data.jsonl  # 训练数据路径
--input-key prompt                 # JSON中提示的键
--label-key label                  # JSON中标签的键
--apply-chat-template              # 应用聊天格式

# 训练循环
--num-rollout 3000                 # 总rollout迭代
--rollout-batch-size 32            # 每次rollout的提示数
--n-samples-per-prompt 8           # 每个提示的响应数
--global-batch-size 256            # 训练批量大小
--num-steps-per-rollout 1          # 每次rollout的训练步数

# RL算法
--advantage-estimator grpo         # grpo, gspo, ppo, reinforce_plus_plus
--use-kl-loss                      # 启用KL损失
--kl-loss-coef 0.001               # KL系数
--calculate-per-token-loss         # Token级损失

# 离线策略选项
--use-tis                          # 截断重要性采样
--tis-threshold 0.9                # TIS阈值
--true-on-policy-mode              # 强制在线策略训练
```

## 数据缓冲系统

### RolloutDataSource（基类）

```python
from slime.data import RolloutDataSource

class RolloutDataSource:
    def __init__(self, dataset, args):
        self.dataset = dataset
        self.args = args

    def get_samples(self, num_samples: int) -> list[Sample]:
        """从数据集获取提示。"""
        return [Sample(prompt=p) for p in self.dataset.sample(num_samples)]

    def add_samples(self, samples: list[Sample]) -> None:
        """生成后调用（默认无操作）。"""
        pass
```

### 缓冲数据源（离线策略）

```python
from slime.data import RolloutDataSourceWithBuffer

class RolloutDataSourceWithBuffer(RolloutDataSource):
    def __init__(self, dataset, args):
        super().__init__(dataset, args)
        self.buffer = []

    def add_samples(self, samples: list[Sample]) -> None:
        """存储生成的样本以供重用。"""
        self.buffer.extend(samples)

    def buffer_filter(self, args, buffer, num_samples) -> list[Sample]:
        """自定义选择逻辑。"""
        # 示例：基于奖励的优先级采样
        sorted_buffer = sorted(buffer, key=lambda s: s.reward, reverse=True)
        return sorted_buffer[:num_samples]
```

## 自定义函数

### 自定义生成函数

用于多轮或工具调用场景：

```python
# custom_generate.py
from slime.data import Sample

async def custom_generate(args, samples: list[Sample], evaluation: bool = False) -> list[Sample]:
    """
    用于多轮交互的自定义生成函数。

    Args:
        args: 训练参数
        samples: 带提示的Sample对象列表
        evaluation: 是否是评估运行

    Returns:
        带响应和奖励的Sample对象列表
    """
    for sample in samples:
        conversation = sample.prompt if isinstance(sample.prompt, list) else [
            {"role": "user", "content": sample.prompt}
        ]

        for turn in range(args.max_turns):
            # 生成响应
            response = await generate_single(conversation)

            # 检查工具调用
            tool_call = extract_tool_call(response)
            if tool_call:
                # 执行工具
                tool_result = await execute_tool(tool_call)
                conversation.append({"role": "assistant", "content": response})
                conversation.append({"role": "tool", "content": tool_result})
            else:
                # 最终响应
                sample.response = response
                break

        # 计算奖励
        sample.reward = compute_reward(sample)

        # 设置损失掩码（模型token为1，工具响应为0）
        sample.loss_mask = build_loss_mask(sample)

    return samples
```

用法：
```bash
python train.py \
    --custom-generate-function-path custom_generate.py \
    --max-turns 5
```

### 自定义奖励函数

```python
# custom_rm.py
from slime.data import Sample

async def reward_func(args, sample: Sample, **kwargs) -> float:
    """
    计算单个样本的奖励。

    Args:
        args: 训练参数
        sample: 带响应的Sample对象

    Returns:
        奖励分数（float）
    """
    response = sample.response
    ground_truth = sample.label or sample.metadata.get("answer", "")

    # 示例：精确匹配奖励
    if response.strip() == ground_truth.strip():
        return 1.0
    return 0.0

# 用于批量处理（更高效）
async def batched_custom_rm(args, samples: list[Sample]) -> list[float]:
    """批量奖励计算。"""
    rewards = []
    for sample in samples:
        reward = await reward_func(args, sample)
        rewards.append(reward)
    return rewards
```

用法：
```bash
python train.py \
    --custom-rm-path custom_rm.py \
    --group-rm  # 启用批量处理
```

## 模型配置

### 预配置模型脚本

位于`scripts/models/`：

```bash
# 列出可用模型
ls scripts/models/
# glm4-9B.sh, qwen3-4B.sh, qwen3-30B-A3B.sh, deepseek-v3.sh, llama3-8B.sh

# 源化模型配置
source scripts/models/qwen3-4B.sh
# 这设置MODEL_ARGS和CKPT_ARGS数组
```

### 模型脚本示例

```bash
# scripts/models/qwen3-4B.sh
export MODEL_ARGS=(
    --num-layers 36
    --hidden-size 2560
    --num-attention-heads 20
    --num-query-groups 4
    --ffn-hidden-size 6912
    --max-position-embeddings 32768
    --rotary-percent 1.0
    --rotary-base 1000000
    --swiglu
    --untie-embeddings-and-output-weights
    --no-position-embedding
    --normalization RMSNorm
    --tokenizer-type HuggingFaceTokenizer
    --bf16
)

export CKPT_ARGS=(
    --hf-checkpoint /path/to/qwen3-4b-hf
    --initial-megatron-checkpoint /path/to/megatron/ckpt
)
```

## 异步训练

### 启用异步模式

```bash
python train_async.py \
    --actor-num-gpus-per-node 8 \
    --rollout-num-gpus 8 \
    --async-buffer-size 4 \
    --update-weights-interval 2 \
    ${MODEL_ARGS[@]}
```

### 异步特定参数

```bash
--async-buffer-size 4            # 要缓冲的rollout数
--update-weights-interval 2      # 每N个rollout同步权重
```

**注意**：共置模式（`--colocate`）不支持异步训练。

## 评估

### 多任务评估

```bash
--eval-prompt-data aime /path/to/aime.jsonl \
--eval-prompt-data gsm8k /path/to/gsm8k.jsonl \
--n-samples-per-eval-prompt 16 \
--eval-interval 50
```

### 评估配置

```bash
--eval-interval 50               # 每N个rollout评估
--n-samples-per-eval-prompt 16   # 评估采样数
--eval-temperature 0.0           # 贪婪解码用于评估
```

## 支持的模型

| 模型系列 | 配置 |
|--------------|----------------|
| GLM | GLM-4.5, GLM-4.6, GLM-4.7, GLM-Z1-9B |
| Qwen | Qwen3 (4B, 8B, 30B-A3B), Qwen3-MoE, Qwen2.5 |
| DeepSeek | V3, V3.1, R1 |
| Llama | Llama 3 (8B, 70B) |
| 其他 | Kimi K2, Moonlight-16B |

## 资源

- 文档：https://thudm.github.io/slime/
- GitHub：https://github.com/THUDM/slime
- 博客：https://lmsys.org/blog/2025-07-09-slime/
- 示例：`examples/`目录（14+完整示例）
