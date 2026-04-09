# 多GPU部署指南

在多个GPU和节点上扩展TensorRT-LLM的完整指南。

## 并行策略

### 张量并行（TP）

**作用**：跨GPU水平分割模型层。

**用例**：
- 模型的总内存可以容纳但单GPU放不下
- 需要低延迟（单次前向传播）
- GPU在同一节点上（NVLink实现最佳性能）

**示例**（4× A100上的Llama 3-70B）：
```python
from tensorrt_llm import LLM

llm = LLM(
    model="meta-llama/Meta-Llama-3-70B",
    tensor_parallel_size=4,  # 跨4个GPU分割
    dtype="fp16"
)

# 模型自动分片到GPU
# 单次前向传播，低延迟
```

**性能**：
- 延迟：与单GPU大致相同
- 吞吐量：4倍更高（4个GPU）
- 通信：高（每层同步激活）

### 流水线并行（PP）

**作用**：跨GPU垂直分割模型层（按层）。

**用例**：
- 非常大的模型（175B+）
- 可以容忍更高延迟
- GPU跨多个节点

**示例**（8× H100上的Llama 3-405B）：
```python
llm = LLM(
    model="meta-llama/Meta-Llama-3-405B",
    tensor_parallel_size=4,   # 节点内TP=4
    pipeline_parallel_size=2, # 跨节点PP=2
    dtype="fp8"
)

# 总计：8个GPU（4×2）
# 层0-40：节点1（4个GPU，TP）
# 层41-80：节点2（4个GPU，TP）
```

**性能**：
- 延迟：更高（通过流水线的顺序）
- 吞吐量：使用微批处理实现高吞吐量
- 通信：比TP低

### 专家并行（EP）

**作用**：将MoE专家分布到GPU上。

**用例**：混合专家模型（Mixtral、DeepSeek-V2）

**示例**（8× A100上的Mixtral-8x22B）：
```python
llm = LLM(
    model="mistralai/Mixtral-8x22B",
    tensor_parallel_size=4,
    expert_parallel_size=2,  # 将8个专家分布到2组
    dtype="fp8"
)
```

## 配置示例

### 小模型（7-13B）- 单GPU

```python
# 1× A100 80GB上的Llama 3-8B
llm = LLM(
    model="meta-llama/Meta-Llama-3-8B",
    dtype="fp16"  # 或H100上用fp8
)
```

**资源**：
- GPU：1× A100 80GB
- 内存：约16GB模型 + 30GB KV缓存
- 吞吐量：3,000-5,000词元/秒

### 中等模型（70B）- 多GPU同节点

```python
# 4× A100 80GB（NVLink）上的Llama 3-70B
llm = LLM(
    model="meta-llama/Meta-Llama-3-70B",
    tensor_parallel_size=4,
    dtype="fp8"  # 70GB → 每GPU 35GB
)
```

**资源**：
- GPU：4× A100 80GB，带NVLink
- 内存：每GPU约35GB（FP8）
- 吞吐量：10,000-15,000词元/秒
- 延迟：每词元15-20ms

### 大模型（405B）- 多节点

```python
# 2节点 × 8 H100 = 16 GPU
llm = LLM(
    model="meta-llama/Meta-Llama-3-405B",
    tensor_parallel_size=8,    # 每个节点内TP
    pipeline_parallel_size=2,  # 跨2个节点PP
    dtype="fp8"
)
```

**资源**：
- GPU：2节点 × 8 H100 80GB
- 内存：每GPU约25GB（FP8）
- 吞吐量：20,000-30,000词元/秒
- 网络：建议使用InfiniBand

## 服务器部署

### 单节点多GPU

```bash
# 4个GPU上自动TP的Llama 3-70B
trtllm-serve meta-llama/Meta-Llama-3-70B \
    --tp_size 4 \
    --max_batch_size 256 \
    --dtype fp8

# 监听http://localhost:8000
```

### 使用Ray的多节点

```bash
# 节点1（头节点）
ray start --head --port=6379

# 节点2（工作节点）
ray start --address='node1:6379'

# 跨集群部署
trtllm-serve meta-llama/Meta-Llama-3-405B \
    --tp_size 8 \
    --pp_size 2 \
    --num_workers 2 \  # 2个节点
    --dtype fp8
```

### Kubernetes部署

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tensorrt-llm-llama3-70b
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: trtllm
        image: nvidia/tensorrt_llm:latest
        command:
          - trtllm-serve
          - meta-llama/Meta-Llama-3-70B
          - --tp_size=4
          - --max_batch_size=256
        resources:
          limits:
            nvidia.com/gpu: 4  # 请求4个GPU
```

## 并行决策树

```
模型大小 < 20GB？
├─ 是：单GPU（无并行）
└─ 否：模型大小 < 80GB？
    ├─ 是：TP=2或TP=4（同节点）
    └─ 否：模型大小 < 320GB？
        ├─ 是：TP=4或TP=8（同节点，需要NVLink）
        └─ 否：TP=8 + PP=2（多节点）
```

## 通信优化

### NVLink vs PCIe

**NVLink**（DGX A100、HGX H100）：
- 带宽：600 GB/s（A100），900 GB/s（H100）
- TP的理想选择（高通信）
- **推荐用于所有多GPU设置**

**PCIe**：
- 带宽：64 GB/s（PCIe 4.0 x16）
- 比NVLink慢10倍
- 避免TP，改为使用PP

### 多节点InfiniBand

**HDR InfiniBand**（200 Gb/s）：
- 多节点TP或PP必需
- 延迟：<1μs
- **对于405B+模型必不可少**

## 监控多GPU

```python
# 监控GPU利用率
nvidia-smi dmon -s u

# 监控内存
nvidia-smi dmon -s m

# 监控NVLink利用率
nvidia-smi nvlink --status

# TensorRT-LLM内置指标
curl http://localhost:8000/metrics
```

**关键指标**：
- GPU利用率：目标80-95%
- 内存使用：应在GPU之间平衡
- NVLink流量：TP高，PP低
- 吞吐量：跨所有GPU的词元/秒

## 常见问题

### GPU内存不平衡

**症状**：GPU 0有90%内存，GPU 3有40%

**解决方案**：
- 验证TP/PP配置
- 检查模型分片（应该相等）
- 重启服务器以重置状态

### NVLink利用率低

**症状**：TP=4时NVLink带宽<100 GB/s

**解决方案**：
- 验证NVLink拓扑：`nvidia-smi topo -m`
- 检查是否有PCIe回退
- 确保GPU在同一NVSwitch上

### 多GPU OOM

**解决方案**：
- 增加TP大小（更多GPU）
- 减少批大小
- 启用FP8量化
- 使用流水线并行

## 性能扩展

### TP扩展（Llama 3-70B，FP8）

| GPU数 | TP大小 | 吞吐量 | 延迟 | 效率 |
|------|---------|------------|---------|------------|
| 1 | 1 | OOM | - | - |
| 2 | 2 | 6,000词元/秒 | 18ms | 85% |
| 4 | 4 | 11,000词元/秒 | 16ms | 78% |
| 8 | 8 | 18,000词元/秒 | 15ms | 64% |

**注意**：由于通信开销，更多GPU时效率下降。

### PP扩展（Llama 3-405B，FP8）

| 节点数 | TP | PP | 总GPU数 | 吞吐量 |
|-------|----|----|------------|------------|
| 1 | 8 | 1 | 8 | OOM |
| 2 | 8 | 2 | 16 | 25,000词元/秒 |
| 4 | 8 | 4 | 32 | 45,000词元/秒 |

## 最佳实践

1. **尽可能优先选择TP而非PP**（更低延迟）
2. **为所有TP部署使用NVLink**
3. **为多节点部署使用InfiniBand**
4. **从最小的TP开始**使模型内存容纳
5. **监控GPU平衡**- 所有GPU应有相似的利用率
6. **生产前用基准测试**
7. **在H100上使用FP8**实现2倍加速
