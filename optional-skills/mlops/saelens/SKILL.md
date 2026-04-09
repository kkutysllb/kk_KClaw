---
name: sparse-autoencoder-training
description: 提供使用SAELens训练和分析稀疏自编码器（SAE）的指导，将神经网络激活分解为可解释的特征。在发现可解释特征、分析叠加或研究语言模型中的单语义表示时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [sae-lens>=6.0.0, transformer-lens>=2.0.0, torch>=2.0.0]
metadata:
  kclaw:
    tags: [稀疏自编码器, SAE, 机制可解释性, 特征发现, 叠加]

---

# SAELens：用于机制可解释性的稀疏自编码器

SAELens是训练和分析稀疏自编码器（SAE）的主要库 - 这是一种将多语义神经网络激活分解为稀疏、可解释特征的技术。基于Anthropic关于单语义性的开创性研究。

**GitHub**：[jbloomAus/SAELens](https://github.com/jbloomAus/SAELens)（1,100+星）

## 问题：多语义性与叠加

神经网络中的单个神经元是**多语义**的 - 它们在多个语义不同的上下文中激活。这是因为模型使用**叠加**来表示比神经元更多的特征，使可解释性变得困难。

**SAE通过以下方式解决此问题**：将密集激活分解为稀疏的单语义特征 - 通常对于任何给定输入，只有少数特征激活，每个特征对应一个可解释的概念。

## 何时使用SAELens

**在以下情况下使用SAELens：**
- 发现模型激活中的可解释特征
- 理解模型学习了什么概念
- 研究叠加和特征几何
- 执行基于特征的引导或消融
- 分析安全相关特征（欺骗、偏见、有害内容）

**在以下情况下考虑替代方案：**
- 需要基本激活分析 → 直接使用**TransformerLens**
- 想要因果干预实验 → 使用**pyvene**或**TransformerLens**
- 需要生产引导 → 考虑直接激活工程

## 安装

```bash
pip install sae-lens
```

要求：Python 3.10+、transformer-lens>=2.0.0

## 核心概念

### SAE学习什么

SAE被训练通过稀疏瓶颈重建模型激活：

```
输入激活 → 编码器 → 稀疏特征 → 解码器 → 重建激活
    (d_model)       ↓        (d_sae >> d_model)    ↓         (d_model)
                 稀疏                          重建
                 惩罚                          损失
```

**损失函数**：`MSE(original, reconstructed) + L1_coefficient × L1(features)`

### 关键验证（Anthropic研究）

在"Towards Monosemanticity"中，人类评估者发现**70%的SAE特征是真正可解释的**。发现的特征包括：
- DNA序列、法律语言、HTTP请求
- 希伯来语文本、营养声明、代码语法
- 情感、命名实体、语法结构

## 工作流1：加载和分析预训练SAE

### 逐步指南

```python
from transformer_lens import HookedTransformer
from sae_lens import SAE

# 1. 加载模型和预训练SAE
model = HookedTransformer.from_pretrained("gpt2-small", device="cuda")
sae, cfg_dict, sparsity = SAE.from_pretrained(
    release="gpt2-small-res-jb",
    sae_id="blocks.8.hook_resid_pre",
    device="cuda"
)

# 2. 获取模型激活
tokens = model.to_tokens("法国的首都是巴黎")
_, cache = model.run_with_cache(tokens)
activations = cache["resid_pre", 8]  # [batch, pos, d_model]

# 3. 编码到SAE特征
sae_features = sae.encode(activations)  # [batch, pos, d_sae]
print(f"活跃特征：{(sae_features > 0).sum()}")

# 4. 找到每个位置的主要特征
for pos in range(tokens.shape[1]):
    top_features = sae_features[0, pos].topk(5)
    token = model.to_str_tokens(tokens[0, pos:pos+1])[0]
    print(f"词元'{token}'：特征{top_features.indices.tolist()}")

# 5. 重建激活
reconstructed = sae.decode(sae_features)
reconstruction_error = (activations - reconstructed).norm()
```

### 可用的预训练SAE

| 发布 | 模型 | 层 |
|---------|-------|--------|
| `gpt2-small-res-jb` | GPT-2 Small | 多个残差流 |
| `gemma-2b-res` | Gemma 2B | 残差流 |
| HuggingFace上的各种 | 搜索标签`saelens` | 各种 |

### 检查清单
- [ ] 使用TransformerLens加载模型
- [ ] 为目标层加载匹配的SAE
- [ ] 将激活编码为稀疏特征
- [ ] 识别每个词元的主要激活特征
- [ ] 验证重建质量

## 工作流2：训练自定义SAE

### 逐步指南

```python
from sae_lens import SAE, LanguageModelSAERunnerConfig, SAETrainingRunner

# 1. 配置训练
cfg = LanguageModelSAERunnerConfig(
    # 模型
    model_name="gpt2-small",
    hook_name="blocks.8.hook_resid_pre",
    hook_layer=8,
    d_in=768,  # 模型维度

    # SAE架构
    architecture="standard",  # 或 "gated", "topk"
    d_sae=768 * 8,  # 8倍扩展因子
    activation_fn="relu",

    # 训练
    lr=4e-4,
    l1_coefficient=8e-5,  # 稀疏惩罚
    l1_warm_up_steps=1000,
    train_batch_size_tokens=4096,
    training_tokens=100_000_000,

    # 数据
    dataset_path="monology/pile-uncopyrighted",
    context_size=128,

    # 日志
    log_to_wandb=True,
    wandb_project="sae-training",

    # 检查点
    checkpoint_path="checkpoints",
    n_checkpoints=5,
)

# 2. 训练
trainer = SAETrainingRunner(cfg)
sae = trainer.run()

# 3. 评估
print(f"L0（平均活跃特征）：{trainer.metrics['l0']}")
print(f"CE损失恢复：{trainer.metrics['ce_loss_score']}")
```

### 关键超参数

| 参数 | 典型值 | 效果 |
|-----------|---------------|--------|
| `d_sae` | 4-16× d_model | 更多特征，更高容量 |
| `l1_coefficient` | 5e-5到1e-4 | 更高=更稀疏，更少准确 |
| `lr` | 1e-4到1e-3 | 标准优化器LR |
| `l1_warm_up_steps` | 500-2000 | 防止早期特征死亡 |

### 评估指标

| 指标 | 目标 | 含义 |
|--------|--------|---------|
| **L0** | 50-200 | 每个词元平均活跃特征 |
| **CE损失分数** | 80-95% | 与原始模型相比恢复的交叉熵 |
| **死亡特征** | <5% | 从不激活的特征 |
| **解释方差** | >90% | 重建质量 |

### 检查清单
- [ ] 选择目标层和钩子点
- [ ] 设置扩展因子（d_sae = 4-16× d_model）
- [ ] 调整L1系数以获得期望的稀疏性
- [ ] 启用L1预热以防止死亡特征
- [ ] 在训练期间监控指标（W&B）
- [ ] 验证L0和CE损失恢复
- [ ] 检查死亡特征比率

## 工作流3：特征分析和引导

### 分析单个特征

```python
from transformer_lens import HookedTransformer
from sae_lens import SAE
import torch

model = HookedTransformer.from_pretrained("gpt2-small", device="cuda")
sae, _, _ = SAE.from_pretrained(
    release="gpt2-small-res-jb",
    sae_id="blocks.8.hook_resid_pre",
    device="cuda"
)

# 找到什么激活特定特征
feature_idx = 1234
test_texts = [
    "科学家进行了实验",
    "我喜欢巧克力蛋糕",
    "代码编译成功",
    "巴黎春天很美",
]

for text in test_texts:
    tokens = model.to_tokens(text)
    _, cache = model.run_with_cache(tokens)
    features = sae.encode(cache["resid_pre", 8])
    activation = features[0, :, feature_idx].max().item()
    print(f"{activation:.3f}: {text}")
```

### 特征引导

```python
def steer_with_feature(model, sae, prompt, feature_idx, strength=5.0):
    """将SAE特征方向添加到残差流。"""
    tokens = model.to_tokens(prompt)

    # 从解码器获取特征方向
    feature_direction = sae.W_dec[feature_idx]  # [d_model]

    def steering_hook(activation, hook):
        # 在所有位置添加缩放的特征方向
        activation += strength * feature_direction
        return activation

    # 使用引导生成
    output = model.generate(
        tokens,
        max_new_tokens=50,
        fwd_hooks=[("blocks.8.hook_resid_pre", steering_hook)]
    )
    return model.to_string(output[0])
```

### 特征归因

```python
# 哪些特征最影响特定输出？
tokens = model.to_tokens("法国的首都是")
_, cache = model.run_with_cache(tokens)

# 获取最终位置的特征
features = sae.encode(cache["resid_pre", 8])[0, -1]  # [d_sae]

# 获取每个特征的logit归因
# 特征贡献 = 特征激活 × 解码器权重 × 反嵌入
W_dec = sae.W_dec  # [d_sae, d_model]
W_U = model.W_U    # [d_model, vocab]

# 对"Paris"logit的贡献
paris_token = model.to_single_token(" Paris")
feature_contributions = features * (W_dec @ W_U[:, paris_token])

top_features = feature_contributions.topk(10)
print("'Paris'预测的主要特征：")
for idx, val in zip(top_features.indices, top_features.values):
    print(f"  特征{idx.item()}：{val.item():.3f}")
```

## 常见问题与解决方案

### 问题：高死亡特征比率
```python
# 错误：无预热，特征早期死亡
cfg = LanguageModelSAERunnerConfig(
    l1_coefficient=1e-4,
    l1_warm_up_steps=0,  # 不好！
)

# 正确：预热L1惩罚
cfg = LanguageModelSAERunnerConfig(
    l1_coefficient=8e-5,
    l1_warm_up_steps=1000,  # 逐渐增加
    use_ghost_grads=True,   # 复活死亡特征
)
```

### 问题：重建差（低CE恢复）
```python
# 减少稀疏惩罚
cfg = LanguageModelSAERunnerConfig(
    l1_coefficient=5e-5,  # 更低=更好重建
    d_sae=768 * 16,       # 更多容量
)
```

### 问题：特征不可解释
```python
# 增加稀疏性（更高L1）
cfg = LanguageModelSAERunnerConfig(
    l1_coefficient=1e-4,  # 更高=更稀疏，更可解释
)
# 或使用TopK架构
cfg = LanguageModelSAERunnerConfig(
    architecture="topk",
    activation_fn_kwargs={"k": 50},  # 完全50个活跃特征
)
```

### 问题：训练期间内存错误
```python
cfg = LanguageModelSAERunnerConfig(
    train_batch_size_tokens=2048,  # 减少批大小
    store_batch_size_prompts=4,    # 缓冲区中更少提示
    n_batches_in_buffer=8,         # 更小的激活缓冲区
)
```

## 与Neuronpedia集成

在[neuronpedia.org](https://neuronpedia.org)浏览预训练SAE特征：

```python
# 特征按SAE ID索引
# 示例：gpt2-small层8特征1234
# → neuronpedia.org/gpt2-small/8-res-jb/1234
```

## 关键类参考

| 类 | 用途 |
|-------|---------|
| `SAE` | 稀疏自编码器模型 |
| `LanguageModelSAERunnerConfig` | 训练配置 |
| `SAETrainingRunner` | 训练循环管理器 |
| `ActivationsStore` | 激活收集和批处理 |
| `HookedSAETransformer` | TransformerLens + SAE集成 |

## 参考文档

有关详细的API文档、教程和高级用法，请参见`references/`文件夹：

| 文件 | 内容 |
|------|----------|
| [references/README.md](references/README.md) | 概述和快速开始指南 |
| [references/api.md](references/api.md) | SAE、TrainingSAE、配置的完整API参考 |
| [references/tutorials.md](references/tutorials.md) | 训练、分析、引导的逐步教程 |

## 外部资源

### 教程
- [基本加载与分析](https://github.com/jbloomAus/SAELens/blob/main/tutorials/basic_loading_and_analysing.ipynb)
- [训练稀疏自编码器](https://github.com/jbloomAus/SAELens/blob/main/tutorials/training_a_sparse_autoencoder.ipynb)
- [ARENA SAE课程](https://www.lesswrong.com/posts/LnHowHgmrMbWtpkxx/intro-to-superposition-and-sparse-autoencoders-colab)

### 论文
- [Towards Monosemanticity](https://transformer-circuits.pub/2023/monosemantic-features) - Anthropic (2023)
- [Scaling Monosemanticity](https://transformer-circuits.pub/2024/scaling-monosemanticity/) - Anthropic (2024)
- [Sparse Autoencoders Find Highly Interpretable Features](https://arxiv.org/abs/2309.08600) - Cunningham等 (ICLR 2024)

### 官方文档
- [SAELens文档](https://jbloomaus.github.io/SAELens/)
- [Neuronpedia](https://neuronpedia.org) - 特征浏览器

## SAE架构

| 架构 | 描述 | 用例 |
|--------------|-------------|----------|
| **Standard** | ReLU + L1惩罚 | 通用 |
| **Gated** | 学习的门控机制 | 更好的稀疏性控制 |
| **TopK** | 完全K个活跃特征 | 一致稀疏性 |

```python
# TopK SAE（完全50个活跃特征）
cfg = LanguageModelSAERunnerConfig(
    architecture="topk",
    activation_fn="topk",
    activation_fn_kwargs={"k": 50},
)
```
