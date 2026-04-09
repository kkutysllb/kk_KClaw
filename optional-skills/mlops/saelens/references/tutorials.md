# SAELens教程

## 教程1：加载和分析预训练SAE

### 目标
加载预训练SAE并分析哪些特征在特定输入上激活。

### 逐步指南

```python
from transformer_lens import HookedTransformer
from sae_lens import SAE
import torch

# 1. 加载模型和SAE
model = HookedTransformer.from_pretrained("gpt2-small", device="cuda")
sae, cfg_dict, sparsity = SAE.from_pretrained(
    release="gpt2-small-res-jb",
    sae_id="blocks.8.hook_resid_pre",
    device="cuda"
)

print(f"SAE输入维度：{sae.cfg.d_in}")
print(f"SAE隐藏维度：{sae.cfg.d_sae}")
print(f"扩展因子：{sae.cfg.d_sae / sae.cfg.d_in:.1f}x")

# 2. 获取模型激活
prompt = "法国的首都是巴黎"
tokens = model.to_tokens(prompt)
_, cache = model.run_with_cache(tokens)
activations = cache["resid_pre", 8]  # [1, seq_len, 768]

# 3. 编码到SAE特征
features = sae.encode(activations)  # [1, seq_len, d_sae]

# 4. 分析稀疏性
active_per_token = (features > 0).sum(dim=-1)
print(f"每个词元的平均活跃特征：{active_per_token.float().mean():.1f}")

# 5. 找到每个词元的主要特征
str_tokens = model.to_str_tokens(prompt)
for pos in range(len(str_tokens)):
    top_features = features[0, pos].topk(5)
    print(f"\n词元'{str_tokens[pos]}'：")
    for feat_idx, feat_val in zip(top_features.indices, top_features.values):
        print(f"  特征{feat_idx.item()}：{feat_val.item():.3f}")

# 6. 检查重建质量
reconstructed = sae.decode(features)
mse = ((activations - reconstructed) ** 2).mean()
print(f"\n重建MSE：{mse.item():.6f}")
```

---

## 教程2：训练自定义SAE

### 目标
在GPT-2激活上训练稀疏自编码器。

### 逐步指南

```python
from sae_lens import LanguageModelSAERunnerConfig, SAETrainingRunner

# 1. 配置训练
cfg = LanguageModelSAERunnerConfig(
    # 模型
    model_name="gpt2-small",
    hook_name="blocks.6.hook_resid_pre",
    hook_layer=6,
    d_in=768,

    # SAE架构
    architecture="standard",
    d_sae=768 * 8,  # 8倍扩展
    activation_fn="relu",

    # 训练
    lr=4e-4,
    l1_coefficient=8e-5,
    l1_warm_up_steps=1000,
    train_batch_size_tokens=4096,
    training_tokens=10_000_000,  # 小规模演示运行

    # 数据
    dataset_path="monology/pile-uncopyrighted",
    streaming=True,
    context_size=128,

    # 死亡特征预防
    use_ghost_grads=True,
    dead_feature_window=5000,

    # 日志
    log_to_wandb=True,
    wandb_project="sae-training-demo",

    # 硬件
    device="cuda",
    dtype="float32",
)

# 2. 训练
runner = SAETrainingRunner(cfg)
sae = runner.run()

# 3. 保存
sae.save_model("./my_trained_sae")
```

### 超参数调优指南

| 如果看到... | 尝试... |
|---------------|--------|
| 高L0（>200） | 增加`l1_coefficient` |
| 低CE恢复（<80%） | 减少`l1_coefficient`，增加`d_sae` |
| 很多死亡特征（>5%） | 启用`use_ghost_grads`，增加`l1_warm_up_steps` |
| 训练不稳定 | 降低`lr`，增加`lr_warm_up_steps` |

---

## 教程3：特征归因和引导

### 目标
识别哪些SAE特征对特定预测有贡献，并使用它们进行引导。

### 逐步指南

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

# 1. 特定预测的特征归因
prompt = "法国的首都是"
tokens = model.to_tokens(prompt)
_, cache = model.run_with_cache(tokens)
activations = cache["resid_pre", 8]
features = sae.encode(activations)

# 目标词元
target_token = model.to_single_token(" Paris")

# 计算特征对目标logit的贡献
# 贡献 = 特征激活 * 解码器权重 * 反嵌入
W_dec = sae.W_dec  # [d_sae, d_model]
W_U = model.W_U    # [d_model, d_vocab]

# 投影到词汇的特征方向
feature_to_logit = W_dec @ W_U  # [d_sae, d_vocab]

# 每个特征对"Paris"在最终位置的贡献
feature_acts = features[0, -1]  # [d_sae]
contributions = feature_acts * feature_to_logit[:, target_token]

# 主要贡献特征
top_features = contributions.topk(10)
print("对'Paris'贡献最多的特征：")
for idx, val in zip(top_features.indices, top_features.values):
    print(f"  特征{idx.item()}：{val.item():.3f}")

# 2. 特征引导
def steer_with_feature(feature_idx, strength=5.0):
    """将特征方向添加到残差流。"""
    feature_direction = sae.W_dec[feature_idx]  # [d_model]

    def hook(activation, hook_obj):
        activation[:, -1, :] += strength * feature_direction
        return activation

    output = model.generate(
        tokens,
        max_new_tokens=10,
        fwd_hooks=[("blocks.8.hook_resid_pre", hook)]
    )
    return model.to_string(output[0])

# 尝试用主要特征引导
top_feature_idx = top_features.indices[0].item()
print(f"\n用特征{top_feature_idx}引导：")
print(steer_with_feature(top_feature_idx, strength=10.0))
```

---

## 教程4：特征消融

### 目标
通过消融特征来测试特征的因果重要性。

### 逐步指南

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

prompt = "法国的首都是"
tokens = model.to_tokens(prompt)

# 基线预测
baseline_logits = model(tokens)
target_token = model.to_single_token(" Paris")
baseline_prob = torch.softmax(baseline_logits[0, -1], dim=-1)[target_token].item()
print(f"基线P(Paris)：{baseline_prob:.4f}")

# 获取要消融的特征
_, cache = model.run_with_cache(tokens)
activations = cache["resid_pre", 8]
features = sae.encode(activations)
top_features = features[0, -1].topk(10).indices

# 逐一消融主要特征
for feat_idx in top_features:
    def ablation_hook(activation, hook, feat_idx=feat_idx):
        # 编码 → 置零特征 → 解码
        feats = sae.encode(activation)
        feats[:, :, feat_idx] = 0
        return sae.decode(feats)

    ablated_logits = model.run_with_hooks(
        tokens,
        fwd_hooks=[("blocks.8.hook_resid_pre", ablation_hook)]
    )
    ablated_prob = torch.softmax(ablated_logits[0, -1], dim=-1)[target_token].item()
    change = (ablated_prob - baseline_prob) / baseline_prob * 100
    print(f"消融特征{feat_idx.item()}：P(Paris)={ablated_prob:.4f} ({change:+.1f}%)")
```

---

## 教程5：跨提示比较特征

### 目标
找到对概念一致激活的特征。

### 逐步指南

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

# 关于同一概念的测试提示
prompts = [
    "埃菲尔铁塔位于",
    "巴黎是...的首都",
    "法国最大的城市是",
    "卢浮宫在",
]

# 收集特征激活
all_features = []
for prompt in prompts:
    tokens = model.to_tokens(prompt)
    _, cache = model.run_with_cache(tokens)
    activations = cache["resid_pre", 8]
    features = sae.encode(activations)
    # 获取位置上的最大激活
    max_features = features[0].max(dim=0).values
    all_features.append(max_features)

all_features = torch.stack(all_features)  # [n_prompts, d_sae]

# 找到一致激活的特征
mean_activation = all_features.mean(dim=0)
min_activation = all_features.min(dim=0).values

# 在所有提示中激活的特征
consistent_features = (min_activation > 0.5).nonzero().squeeze(-1)
print(f"在所有提示中激活的特征：{len(consistent_features)}")

# 主要一致特征
top_consistent = mean_activation[consistent_features].topk(min(10, len(consistent_features)))
print("\n主要一致特征（可能与'法国/巴黎'相关）：")
for idx, val in zip(top_consistent.indices, top_consistent.values):
    feat_idx = consistent_features[idx].item()
    print(f"  特征{feat_idx}：平均激活{val.item():.3f}")
```

---

## 外部资源

### 官方教程
- [基础加载与分析](https://github.com/jbloomAus/SAELens/blob/main/tutorials/basic_loading_and_analysing.ipynb)
- [训练SAE](https://github.com/jbloomAus/SAELens/blob/main/tutorials/training_a_sparse_autoencoder.ipynb)
- [带特征的Logits Lens](https://github.com/jbloomAus/SAELens/blob/main/tutorials/logits_lens_with_features.ipynb)

### ARENA课程
综合SAE课程：https://www.lesswrong.com/posts/LnHowHgmrMbWtpkxx/intro-to-superposition-and-sparse-autoencoders-colab

### 关键论文
- [Towards Monosemanticity](https://transformer-circuits.pub/2023/monosemantic-features) - Anthropic (2023)
- [Scaling Monosemanticity](https://transformer-circuits.pub/2024/scaling-monosemanticity/) - Anthropic (2024)
- [Sparse Autoencoders Find Interpretable Features](https://arxiv.org/abs/2309.08600) - ICLR 2024
