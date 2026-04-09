---
name: tensorrt-llm
description: 使用NVIDIA TensorRT优化LLM推理以获得最高吞吐量和最低延迟。用于在NVIDIA GPU（A100/H100）上进行生产部署，需要比PyTorch快10-100倍的推理，或使用量化（FP8/INT4）、飞行批处理和多GPU扩展来服务模型。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [tensorrt-llm, torch]
metadata:
  kclaw:
    tags: [推理服务, TensorRT-LLM, NVIDIA, 推理优化, 高吞吐量, 低延迟, 生产环境, FP8, INT4, 飞行批处理, 多GPU]

---

# TensorRT-LLM

NVIDIA的开源库，用于在NVIDIA GPU上以最先进的性能优化LLM推理。

## 何时使用TensorRT-LLM

**在以下情况下使用TensorRT-LLM：**
- 部署在NVIDIA GPU上（A100、H100、GB200）
- 需要最高吞吐量（Llama 3上24,000+词元/秒）
- 需要实时应用的低延迟
- 使用量化模型（FP8、INT4、FP4）
- 跨多个GPU或节点扩展

**在以下情况下使用vLLM：**
- 需要更简单的设置和Python优先的API
- 想要PagedAttention而不使用TensorRT编译
- 使用AMD GPU或非NVIDIA硬件

**在以下情况下使用llama.cpp：**
- 部署在CPU或Apple Silicon上
- 需要在无NVIDIA GPU的情况下进行边缘部署
- 想要更简单的GGUF量化格式

## 快速开始

### 安装

```bash
# Docker（推荐）
docker pull nvidia/tensorrt_llm:latest

# pip安装
pip install tensorrt_llm==1.2.0rc3

# 需要CUDA 13.0.0、TensorRT 10.13.2、Python 3.10-3.12
```

### 基础推理

```python
from tensorrt_llm import LLM, SamplingParams

# 初始化模型
llm = LLM(model="meta-llama/Meta-Llama-3-8B")

# 配置采样
sampling_params = SamplingParams(
    max_tokens=100,
    temperature=0.7,
    top_p=0.9
)

# 生成
prompts = ["解释量子计算"]
outputs = llm.generate(prompts, sampling_params)

for output in outputs:
    print(output.text)
```

### 使用trtllm-serve服务

```bash
# 启动服务器（自动下载和编译模型）
trtllm-serve meta-llama/Meta-Llama-3-8B \
    --tp_size 4 \              # 张量并行（4个GPU）
    --max_batch_size 256 \
    --max_num_tokens 4096

# 客户端请求
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-8B",
    "messages": [{"role": "user", "content": "你好！"}],
    "temperature": 0.7,
    "max_tokens": 100
  }'
```

## 关键特性

### 性能优化
- **飞行批处理**：生成过程中动态批处理
- **分页KV缓存**：高效的内存管理
- **Flash Attention**：优化的注意力内核
- **量化**：FP8、INT4、FP4实现2-4倍更快的推理
- **CUDA图**：减少内核启动开销

### 并行性
- **张量并行（TP）**：跨GPU分割模型
- **流水线并行（PP）**：按层分布
- **专家并行**：用于混合专家模型
- **多节点**：扩展到单机之外

### 高级特性
- **推测解码**：使用草稿模型更快生成
- **LoRA服务**：高效的多适配器部署
- **分离服务**：分离预填充和生成

## 常见模式

### 量化模型（FP8）

```python
from tensorrt_llm import LLM

# 加载FP8量化模型（2倍更快，50%内存）
llm = LLM(
    model="meta-llama/Meta-Llama-3-70B",
    dtype="fp8",
    max_num_tokens=8192
)

# 推理与之前相同
outputs = llm.generate(["总结这篇文章..."])
```

### 多GPU部署

```python
# 跨8个GPU的张量并行
llm = LLM(
    model="meta-llama/Meta-Llama-3-405B",
    tensor_parallel_size=8,
    dtype="fp8"
)
```

### 批量推理

```python
# 高效处理100个提示
prompts = [f"问题{i}：..." for i in range(100)]

outputs = llm.generate(
    prompts,
    sampling_params=SamplingParams(max_tokens=200)
)

# 自动飞行批处理以获得最大吞吐量
```

## 性能基准

**Meta Llama 3-8B**（H100 GPU）：
- 吞吐量：24,000词元/秒
- 延迟：约10ms/词元
- vs PyTorch：**快100倍**

**Llama 3-70B**（8× A100 80GB）：
- FP8量化：比FP16快2倍
- 内存：FP8减少50%

## 支持的模型

- **LLaMA系列**：Llama 2、Llama 3、CodeLlama
- **GPT系列**：GPT-2、GPT-J、GPT-NeoX
- **Qwen**：Qwen、Qwen2、QwQ
- **DeepSeek**：DeepSeek-V2、DeepSeek-V3
- **Mixtral**：Mixtral-8x7B、Mixtral-8x22B
- **视觉**：LLaVA、Phi-3-vision
- **HuggingFace上100+模型**

## 参考

- **[优化指南](references/optimization.md)** - 量化、批处理、KV缓存调优
- **[多GPU设置](references/multi-gpu.md)** - 张量/流水线并行、多节点
- **[服务指南](references/serving.md)** - 生产部署、监控、自动扩展

## 资源

- **文档**：https://nvidia.github.io/TensorRT-LLM/
- **GitHub**：https://github.com/NVIDIA/TensorRT-LLM
- **模型**：https://huggingface.co/models?library=tensorrt_llm
