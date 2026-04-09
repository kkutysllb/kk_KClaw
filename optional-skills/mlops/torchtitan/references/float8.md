# TorchTitan中的Float8训练

Float8训练为GEMM足够大的模型提供实质性加速，FP8张量核加速超过动态量化开销。

## 硬件要求

- NVIDIA H100或更新GPU（FP8张量核）
- Blackwell GPU用于MXFP8训练

## 安装

```bash
USE_CPP=0 pip install git+https://github.com/pytorch/ao.git
```

## 用法：张量级缩放

标准Float8，带张量级动态缩放：

```bash
CONFIG_FILE="./torchtitan/models/llama3/train_configs/llama3_8b.toml" ./run_train.sh \
  --model.converters="quantize.linear.float8" \
  --quantize.linear.float8.enable_fsdp_float8_all_gather \
  --quantize.linear.float8.precompute_float8_dynamic_scale_for_fsdp \
  --compile.enable
```

### 关键参数

| 参数 | 描述 |
|----------|-------------|
| `--model.converters="quantize.linear.float8"` | 将`nn.Linear`替换为`Float8Linear` |
| `--quantize.linear.float8.enable_fsdp_float8_all_gather` | 以float8通信以节省带宽 |
| `--quantize.linear.float8.precompute_float8_dynamic_scale_for_fsdp` | 所有AMAX/缩放的单次all-reduce |
| `--compile.enable` | 必需 - 融合float8缩放/转换内核 |

## 用法：行级缩放

比张量级缩放更高精度：

```bash
CONFIG_FILE="./torchtitan/models/llama3/train_configs/llama3_8b.toml" ./run_train.sh \
  --model.converters="quantize.linear.float8" \
  --quantize.linear.float8.recipe_name rowwise \
  --compile.enable
```

## 过滤层

并非所有层都从Float8受益。过滤小层：

```bash
--quantize.linear.float8.filter_fqns="attention.wk,attention.wv,output"
```

### 自动过滤

自动跳过太小的层：

```bash
--quantize.linear.float8.filter_fqns="auto_filter_small_kn"
```

基于H100微基准的阈值，其中加速>开销。

## TOML配置

```toml
[model]
converters = ["quantize.linear.float8"]

[quantize.linear.float8]
enable_fsdp_float8_all_gather = true
precompute_float8_dynamic_scale_for_fsdp = true
filter_fqns = ["output", "auto_filter_small_kn"]

[compile]
enable = true
components = ["model", "loss"]
```

## Float8如何与分布式训练配合工作

### 单设备

在调用`torch._scaled_mm`之前在forward内将输入和权重转换为float8：

```python
# Float8矩阵乘法需要缩放
torch._scaled_mm(input_fp8, weight_fp8, scale_a=scale_input, scale_b=scale_weight)
```

### FSDP + Float8

1. 将分片高精度权重（每rank 1/N）转换为float8
2. 执行float8 all-gather（与bf16/fp32相比节省带宽）
3. 跨rank通信`max(abs)`以计算缩放
4. 在forward开始时，准备好未分片的float8权重

**净收益**：Float8 all-gather + amax通信可以击败bf16/fp32 all-gather，取决于world大小和消息大小。

### TP + Float8

- **输入**：将分片输入转换为float8，在float8中all-gather
- **权重**：为分片权重通信`max(abs)`
- **矩阵乘法**：Float8输入（未分片）× float8权重（分片），带全局缩放

## 缩放策略

| 策略 | 状态 | 描述 |
|----------|--------|-------------|
| 张量级动态 | 稳定 | 每个张量单一缩放 |
| 行级动态 | Alpha | 每行缩放，更高精度 |

## 性能提升

来自H100基准测试：

| 配置 | TPS/GPU | vs基线 |
|---------------|---------|-------------|
| 仅FSDP | 5,762 | - |
| FSDP + compile | 6,667 | +16% |
| FSDP + compile + Float8 | 8,532 | +48% |

## 确定Float8优势

检查[torchao微基准](https://github.com/pytorch/ao/tree/main/torchao/float8#performance)以获取不同M,N,K大小的forward+backward pass加速。

经验法则：K,N > 4096的GEMM通常从Float8受益。

## MXFP8训练（Blackwell）

对于NVIDIA Blackwell GPU，TorchTitan支持密集和MoE模型的MXFP8（微缩放FP8）。详情请参见[docs/mxfp8.md](https://github.com/pytorch/torchtitan/blob/main/docs/mxfp8.md)。
