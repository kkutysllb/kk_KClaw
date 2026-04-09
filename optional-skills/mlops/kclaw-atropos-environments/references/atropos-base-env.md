---
name: atropos-base-env
description: Atropos BaseEnv抽象类参考，涵盖抽象方法、可覆盖方法、数据结构等。
---

# Atropos BaseEnv参考

来源：`atroposlib/envs/base.py`（~2124行）

## 抽象方法（必须实现）

| 方法 | 签名 | 描述 |
|--------|-----------|-------------|
| `get_next_item()` | `async def get_next_item(self) -> Item` | 返回下一个轨迹项目。返回None则暂停。 |
| `evaluate()` | `async def evaluate(self, *args, **kwargs)` | 每steps_per_eval步调用一次。 |
| `setup()` | `async def setup(self)` | 启动时调用一次。加载数据集，初始化模型。 |
| `collect_trajectory()` | `async def collect_trajectory(self, item) -> Tuple[Optional[ScoredDataItem], List[Item]]` | 单次rollout。可选择覆盖collect_trajectories。 |

## 可覆盖方法

| 方法 | 默认行为 | 覆盖时机 |
|--------|-----------------|---------------|
| `collect_trajectories()` | 并行运行collect_trajectory group_size次 | 批量生成、MCTS、耦合rollouts |
| `wandb_log()` | 记录完成长度、rollout表、性能统计 | 添加自定义指标（始终调用super） |
| `config_init()` | 返回(env_config_cls(), ServerBaseline()) | 自定义默认值 + 服务器配置 |
| `postprocess_histories()` | 直通 | 发送到训练器前的最终处理 |
| `save_checkpoint()` | 保存JSON到checkpoint_dir | 自定义序列化 |
| `cleanup()` | 无操作 | 每次rollout后释放资源 |

## ScoredDataGroup结构

```python
ScoredDataGroup = TypedDict，包含：
    tokens:             List[List[int]]       # 每次rollout的令牌ID
    masks:              List[List[int]]       # -100=提示，token_id=补全
    scores:             List[float]           # 每次rollout的分数
    advantages:         Optional[...]         # 每令牌优势
    ref_logprobs:       Optional[...]         # 参考模型logprobs
    messages:           Optional[...]         # OpenAI格式消息
    inference_logprobs: Optional[...]         # 推理logprobs
```

## BaseEnvConfig关键字段

| 字段 | 默认 | 描述 |
|-------|---------|-------------|
| `group_size` | 4 | 分组用于评分 |
| `steps_per_eval` | 100 | 评估间隔步数 |
| `max_token_length` | 2048 | 生成的最大令牌长度 |
| `total_steps` | 1000 | 总训练步数 |
| `use_wandb` | True | 启用wandb日志 |
| `tokenizer_name` | DeepKClaw-3 | 用于令牌编码的分词器 |
| `ensure_scores_are_not_same` | True | 跳过分数相同的组 |
| `worker_timeout` | 600 | 任务超时秒数 |

## 数据流

```
env_manager() → add_train_workers() → handle_env()
    → collect_trajectories() → postprocess_histories()
    → handle_send_to_api() → 训练服务器
```

## Atropos环境统计（分析了82个环境）

- 95%实现了setup、collect_trajectories、evaluate、get_next_item
- 76%覆盖了wandb_log
- 54%有自定义配置类
- 大多数使用collect_trajectories（复数），而不是collect_trajectory（单数）
- 常见奖励模式：LLM-judge（~40）、regex提取（~35）、代码执行（~12）
