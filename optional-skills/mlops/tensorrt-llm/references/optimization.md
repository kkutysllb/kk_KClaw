# TensorRT-LLM优化指南

使用TensorRT-LLM优化LLM推理的完整指南。

## 量化

### FP8量化（推荐用于H100）

**优势**：
- 2倍更快的推理
- 50%内存减少
- 最小精度损失（<1%困惑度下降）

**用法**：
```python
from tensorrt_llm import LLM

# 自动FP8量化
llm = LLM(
    model="meta-llama/Meta-Llama-3-70B",
    dtype="fp8",
    quantization="fp8"
)
```

**性能**（8× H100上的Llama 3-70B）：
- FP16：5,000词元/秒
- FP8：**10,000词元/秒**（2倍加速）
- 内存：140GB → 70GB

### INT4量化（最大压缩）

**优势**：
- 4倍内存减少
- 3-4倍更快的推理
- 在相同硬件上容纳更大模型

**用法**：
```python
# 带AWQ校准的INT4
llm = LLM(
    model="meta-llama/Meta-Llama-3-405B",
    dtype="int4_awq",
    quantization="awq"
)

# 带GPTQ校准的INT4
llm = LLM(
    model="meta-llama/Meta-Llama-3-405B",
    dtype="int4_gptq",
    quantization="gptq"
)
```

**权衡**：
- 精度：困惑度增加1-3%
- 速度：比FP16快3-4倍
- 用例：内存关键时

## 飞行批处理

**作用**：在生成过程中动态批处理请求，而不是等待所有序列完成。

**配置**：
```python
# 服务器配置
trtllm-serve meta-llama/Meta-Llama-3-8B \
    --max_batch_size 256 \           # 最大并发序列数
    --max_num_tokens 4096 \           # 批处理中的总词元数
    --enable_chunked_context \        # 分割长提示
    --scheduler_policy max_utilization
```

**性能**：
- 吞吐量：**比静态批处理高4-8倍**
- 延迟：混合工作负载的P50/P99更低
- GPU利用率：80-95% vs 40-60%

## Paged KV缓存

**作用**：像操作系统管理虚拟内存一样管理KV缓存内存（分页）。

**优势**：
- 吞吐量提高40-60%
- 无内存碎片
- 支持更长序列

**配置**：
```python
# 自动分页KV缓存（默认）
llm = LLM(
    model="meta-llama/Meta-Llama-3-8B",
    kv_cache_free_gpu_mem_fraction=0.9,  # 使用90% GPU内存用于缓存
    enable_prefix_caching=True            # 缓存常见前缀
)
```

## 推测解码

**作用**：使用小草稿模型预测多个词元，由目标模型并行验证。

**加速**：长生成2-3倍更快

**用法**：
```python
from tensorrt_llm import LLM

# 目标模型（Llama 3-70B）
llm = LLM(
    model="meta-llama/Meta-Llama-3-70B",
    speculative_model="meta-llama/Meta-Llama-3-8B",  # 草稿模型
    num_speculative_tokens=5                          # 预测的词元数
)

# 相同API，2-3倍更快
outputs = llm.generate(prompts)
```

**最佳草稿模型**：
- 目标：Llama 3-70B → 草稿：Llama 3-8B
- 目标：Qwen2-72B → 草稿：Qwen2-7B
- 同一家族，8-10倍更小

## CUDA图

**作用**：通过记录GPU操作减少内核启动开销。

**优势**：
- 延迟降低10-20%
- 更稳定的P99延迟
- 更适合小批大小

**配置**（默认自动）：
```python
llm = LLM(
    model="meta-llama/Meta-Llama-3-8B",
    enable_cuda_graph=True,  # 默认：True
    cuda_graph_cache_size=2  # 缓存2个图变体
)
```

## 分块上下文

**作用**：将长提示分割成块以减少内存峰值。

**用例**：显存有限时>8K词元的提示

**配置**：
```bash
trtllm-serve meta-llama/Meta-Llama-3-8B \
    --max_num_tokens 4096 \
    --enable_chunked_context \
    --max_chunked_prefill_length 2048  # 每次处理2K词元
```

## 重叠调度

**作用**：重叠计算和内存操作。

**优势**：
- 吞吐量提高15-25%
- 更好的GPU利用率
- v1.2.0+中默认启用

**无需配置** - 自动启用。

## 量化对比表

| 方法 | 内存 | 速度 | 精度 | 用例 |
|--------|--------|-------|----------|----------|
| FP16 | 1×（基线） | 1× | 最好 | 需要高精度 |
| FP8 | 0.5× | 2× | -0.5%困惑度 | **H100默认** |
| INT4 AWQ | 0.25× | 3-4× | -1.5%困惑度 | 内存关键 |
| INT4 GPTQ | 0.25× | 3-4× | -2%困惑度 | 最大速度 |

## 调优工作流程

1. **从默认开始**：
   ```python
   llm = LLM(model="meta-llama/Meta-Llama-3-70B")
   ```

2. **启用FP8**（如果使用H100）：
   ```python
   llm = LLM(model="...", dtype="fp8")
   ```

3. **调优批大小**：
   ```python
   # 增加直到OOM，然后减少20%
   trtllm-serve ... --max_batch_size 256
   ```

4. **启用分块上下文**（如果提示很长）：
   ```bash
   --enable_chunked_context --max_chunked_prefill_length 2048
   ```

5. **尝试推测解码**（如果延迟关键）：
   ```python
   llm = LLM(model="...", speculative_model="...")
   ```

## 基准测试

```bash
# 安装基准测试工具
pip install tensorrt_llm[benchmark]

# 运行基准测试
python benchmarks/python/benchmark.py \
    --model meta-llama/Meta-Llama-3-8B \
    --batch_size 64 \
    --input_len 128 \
    --output_len 256 \
    --dtype fp8
```

**要跟踪的指标**：
- 吞吐量（词元/秒）
- 延迟P50/P90/P99（ms）
- GPU内存使用（GB）
- GPU利用率（%）

## 常见问题

**OOM错误**：
- 减少`max_batch_size`
- 减少`max_num_tokens`
- 启用INT4量化
- 增加`tensor_parallel_size`

**低吞吐量**：
- 增加`max_batch_size`
- 启用飞行批处理
- 验证CUDA图已启用
- 检查GPU利用率

**高延迟**：
- 尝试推测解码
- 减少`max_batch_size`（减少排队）
- 使用FP8而非FP16
