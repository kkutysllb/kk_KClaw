# SAELens API参考

## SAE类

表示稀疏自编码器的核心类。

### 加载预训练SAE

```python
from sae_lens import SAE

# 从官方发布
sae, cfg_dict, sparsity = SAE.from_pretrained(
    release="gpt2-small-res-jb",
    sae_id="blocks.8.hook_resid_pre",
    device="cuda"
)

# 从HuggingFace
sae, cfg_dict, sparsity = SAE.from_pretrained(
    release="username/repo-name",
    sae_id="path/to/sae",
    device="cuda"
)

# 从本地磁盘
sae = SAE.load_from_disk("/path/to/sae", device="cuda")
```

### SAE属性

| 属性 | 形状 | 描述 |
|-----------|-------|-------------|
| `W_enc` | [d_in, d_sae] | 编码器权重 |
| `W_dec` | [d_sae, d_in] | 解码器权重 |
| `b_enc` | [d_sae] | 编码器偏置 |
| `b_dec` | [d_in] | 解码器偏置 |
| `cfg` | SAEConfig | 配置对象 |

### 核心方法

#### encode()

```python
# 将激活编码为稀疏特征
features = sae.encode(activations)
# 输入：[batch, pos, d_in]
# 输出：[batch, pos, d_sae]
```

#### decode()

```python
# 从特征重建激活
reconstructed = sae.decode(features)
# 输入：[batch, pos, d_sae]
# 输出：[batch, pos, d_in]
```

#### forward()

```python
# 完整前向传播（编码 + 解码）
reconstructed = sae(activations)
# 返回重建的激活
```

#### save_model()

```python
sae.save_model("/path/to/save")
```

---

## SAEConfig

SAE架构和训练上下文的配置类。

### 关键参数

| 参数 | 类型 | 描述 |
|-----------|------|-------------|
| `d_in` | int | 输入维度（模型的d_model） |
| `d_sae` | int | SAE隐藏维度 |
| `architecture` | str | "standard"、"gated"、"jumprelu"、"topk" |
| `activation_fn_str` | str | 激活函数名称 |
| `model_name` | str | 源模型名称 |
| `hook_name` | str | 模型中的钩子点 |
| `normalize_activations` | str | 归一化方法 |
| `dtype` | str | 数据类型 |
| `device` | str | 设备 |

### 访问配置

```python
print(sae.cfg.d_in)      # GPT-2 small为768
print(sae.cfg.d_sae)     # 例如24576（32倍扩展）
print(sae.cfg.hook_name) # 例如"blocks.8.hook_resid_pre"
```

---

## LanguageModelSAERunnerConfig

训练SAE的综合配置。

### 配置示例

```python
from sae_lens import LanguageModelSAERunnerConfig

cfg = LanguageModelSAERunnerConfig(
    # 模型和钩子
    model_name="gpt2-small",
    hook_name="blocks.8.hook_resid_pre",
    hook_layer=8,
    d_in=768,

    # SAE架构
    architecture="standard",  # "standard"、"gated"、"jumprelu"、"topk"
    d_sae=768 * 8,           # 扩展因子
    activation_fn="relu",

    # 训练超参数
    lr=4e-4,
    l1_coefficient=8e-5,
    lp_norm=1.0,
    lr_scheduler_name="constant",
    lr_warm_up_steps=500,

    # 稀疏性控制
    l1_warm_up_steps=1000,
    use_ghost_grads=True,
    feature_sampling_window=1000,
    dead_feature_window=5000,
    dead_feature_threshold=1e-8,

    # 数据
    dataset_path="monology/pile-uncopyrighted",
    streaming=True,
    context_size=128,

    # 批大小
    train_batch_size_tokens=4096,
    store_batch_size_prompts=16,
    n_batches_in_buffer=64,

    # 训练时长
    training_tokens=100_000_000,

    # 日志
    log_to_wandb=True,
    wandb_project="sae-training",
    wandb_log_frequency=100,

    # 检查点
    checkpoint_path="checkpoints",
    n_checkpoints=5,

    # 硬件
    device="cuda",
    dtype="float32",
)
```

### 关键参数解释

#### 架构参数

| 参数 | 描述 |
|-----------|-------------|
| `architecture` | SAE类型："standard"、"gated"、"jumprelu"、"topk" |
| `d_sae` | 隐藏维度（或使用`expansion_factor`） |
| `expansion_factor` | d_sae = d_in × expansion_factor的替代 |
| `activation_fn` | "relu"、"topk"等 |
| `activation_fn_kwargs` | 激活参数字典（例如topk的{"k": 50}） |

#### 稀疏性参数

| 参数 | 描述 |
|-----------|-------------|
| `l1_coefficient` | L1惩罚权重（更高=更稀疏） |
| `l1_warm_up_steps` | L1惩罚增加的步数 |
| `use_ghost_grads` | 对死亡特征应用梯度 |
| `dead_feature_threshold` | "死亡"的激活阈值 |
| `dead_feature_window` | 检查死亡特征的步数 |

#### 学习率参数

| 参数 | 描述 |
|-----------|-------------|
| `lr` | 基础学习率 |
| `lr_scheduler_name` | "constant"、"cosineannealing"等 |
| `lr_warm_up_steps` | LR预热步数 |
| `lr_decay_steps` | LR衰减步数 |

---

## SAETrainingRunner

执行训练的主要类。

### 基础训练

```python
from sae_lens import SAETrainingRunner, LanguageModelSAERunnerConfig

cfg = LanguageModelSAERunnerConfig(...)
runner = SAETrainingRunner(cfg)
sae = runner.run()
```

### 访问训练指标

```python
# 训练期间，记录到W&B的指标包括：
# - l0：平均活跃特征
# - ce_loss_score：交叉熵恢复
# - mse_loss：重建损失
# - l1_loss：稀疏性损失
# - dead_features：死亡特征计数
```

---

## ActivationsStore

管理激活收集和批处理。

### 基础用法

```python
from sae_lens import ActivationsStore

store = ActivationsStore.from_sae(
    model=model,
    sae=sae,
    store_batch_size_prompts=8,
    train_batch_size_tokens=4096,
    n_batches_in_buffer=32,
    device="cuda",
)

# 获取一批激活
activations = store.get_batch_tokens()
```

---

## HookedSAETransformer

SAE与TransformerLens模型的集成。

### 基础用法

```python
from sae_lens import HookedSAETransformer

# 加载带SAE的模型
model = HookedSAETransformer.from_pretrained("gpt2-small")
model.add_sae(sae)

# 使用SAE在循环中运行
output = model.run_with_saes(tokens, saes=[sae])

# 使用SAE激活的缓存运行
output, cache = model.run_with_cache_with_saes(tokens, saes=[sae])
```

---

## SAE架构

### Standard（ReLU + L1）

```python
cfg = LanguageModelSAERunnerConfig(
    architecture="standard",
    activation_fn="relu",
    l1_coefficient=8e-5,
)
```

### Gated

```python
cfg = LanguageModelSAERunnerConfig(
    architecture="gated",
)
```

### TopK

```python
cfg = LanguageModelSAERunnerConfig(
    architecture="topk",
    activation_fn="topk",
    activation_fn_kwargs={"k": 50},  # 完全50个活跃特征
)
```

### JumpReLU（最先进）

```python
cfg = LanguageModelSAERunnerConfig(
    architecture="jumprelu",
)
```

---

## 工具函数

### 上传到HuggingFace

```python
from sae_lens import upload_saes_to_huggingface

upload_saes_to_huggingface(
    saes=[sae],
    repo_id="username/my-saes",
    token="hf_token",
)
```

### Neuronpedia集成

```python
# 特征可以在Neuronpedia上查看
# URL格式：neuronpedia.org/{model}/{layer}-{sae_type}/{feature_id}
# 示例：neuronpedia.org/gpt2-small/8-res-jb/1234
```
