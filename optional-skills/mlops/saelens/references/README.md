# SAELens参考文档

本目录包含SAELens的综合参考材料。

## 内容

- [api.md](api.md) - SAE、TrainingSAE和配置类的完整API参考
- [tutorials.md](tutorials.md) - 训练和分析SAE的逐步教程
- [papers.md](papers.md) - 关于稀疏自编码器的关键研究论文

## 快速链接

- **GitHub仓库**：https://github.com/jbloomAus/SAELens
- **Neuronpedia**：https://neuronpedia.org（浏览预训练SAE特征）
- **HuggingFace SAEs**：搜索标签`saelens`

## 安装

```bash
pip install sae-lens
```

要求：Python 3.10+、transformer-lens>=2.0.0

## 基础用法

```python
from transformer_lens import HookedTransformer
from sae_lens import SAE

# 加载模型和SAE
model = HookedTransformer.from_pretrained("gpt2-small", device="cuda")
sae, cfg_dict, sparsity = SAE.from_pretrained(
    release="gpt2-small-res-jb",
    sae_id="blocks.8.hook_resid_pre",
    device="cuda"
)

# 将激活编码为稀疏特征
tokens = model.to_tokens("你好世界")
_, cache = model.run_with_cache(tokens)
activations = cache["resid_pre", 8]

features = sae.encode(activations)  # 稀疏特征激活
reconstructed = sae.decode(features)  # 重建的激活
```

## 关键概念

### 稀疏自编码器
SAE将密集神经激活分解为稀疏、可解释的特征：
- **编码器**：映射d_model → d_sae（通常4-16倍扩展）
- **ReLU/TopK**：强制稀疏性
- **解码器**：重建原始激活

### 训练损失
`Loss = MSE(original, reconstructed) + L1_coefficient × L1(features)`

### 关键指标
- **L0**：活跃特征的平均数量（目标：50-200）
- **CE损失分数**：与原始模型相比恢复的交叉熵（目标：80-95%）
- **死亡特征**：从不激活的特征（目标：<5%）

## 可用的预训练SAE

| 发布 | 模型 | 描述 |
|---------|-------|-------------|
| `gpt2-small-res-jb` | GPT-2 Small | 残差流SAE |
| `gemma-2b-res` | Gemma 2B | 残差流SAE |
| 各种 | 搜索HuggingFace | 社区训练的SAE |
