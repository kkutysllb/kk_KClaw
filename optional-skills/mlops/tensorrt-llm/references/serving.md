# 生产服务指南

在生产环境中部署TensorRT-LLM的完整指南。

## 服务器模式

### trtllm-serve（推荐）

**特性**：
- OpenAI兼容API
- 自动模型下载和编译
- 内置负载均衡
- Prometheus指标
- 健康检查

**基础用法**：
```bash
trtllm-serve meta-llama/Meta-Llama-3-8B \
    --tp_size 1 \
    --max_batch_size 256 \
    --port 8000
```

**高级配置**：
```bash
trtllm-serve meta-llama/Meta-Llama-3-70B \
    --tp_size 4 \
    --dtype fp8 \
    --max_batch_size 256 \
    --max_num_tokens 4096 \
    --enable_chunked_context \
    --scheduler_policy max_utilization \
    --port 8000 \
    --api_key $API_KEY  # 可选认证
```

### Python LLM API（用于嵌入）

```python
from tensorrt_llm import LLM

class LLMService:
    def __init__(self):
        self.llm = LLM(
            model="meta-llama/Meta-Llama-3-8B",
            dtype="fp8"
        )

    def generate(self, prompt, max_tokens=100):
        from tensorrt_llm import SamplingParams

        params = SamplingParams(
            max_tokens=max_tokens,
            temperature=0.7
        )
        outputs = self.llm.generate([prompt], params)
        return outputs[0].text

# 在FastAPI、Flask等中使用
from fastapi import FastAPI
app = FastAPI()
service = LLMService()

@app.post("/generate")
def generate(prompt: str):
    return {"response": service.generate(prompt)}
```

## OpenAI兼容API

### 聊天补全

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-8B",
    "messages": [
      {"role": "system", "content": "你是一个有用的助手。"},
      {"role": "user", "content": "解释量子计算"}
    ],
    "temperature": 0.7,
    "max_tokens": 500,
    "stream": false
  }'
```

**响应**：
```json
{
  "id": "chat-abc123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "meta-llama/Meta-Llama-3-8B",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "量子计算是..."
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 150,
    "total_tokens": 175
  }
}
```

### 流式传输

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-8B",
    "messages": [{"role": "user", "content": "数到10"}],
    "stream": true
  }'
```

**响应**（SSE流）：
```
data: {"choices":[{"delta":{"content":"1"}}]}

data: {"choices":[{"delta":{"content":", 2"}}]}

data: {"choices":[{"delta":{"content":", 3"}}]}

data: [DONE]
```

### 补全

```bash
curl -X POST http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-8B",
    "prompt": "法国的首都是",
    "max_tokens": 10,
    "temperature": 0.0
  }'
```

## 监控

### Prometheus指标

**启用指标**：
```bash
trtllm-serve meta-llama/Meta-Llama-3-8B \
    --enable_metrics \
    --metrics_port 9090
```

**关键指标**：
```bash
# 抓取指标
curl http://localhost:9090/metrics

# 重要指标：
# - trtllm_request_success_total - 成功请求总数
# - trtllm_request_latency_seconds - 请求延迟直方图
# - trtllm_tokens_generated_total - 生成的词元总数
# - trtllm_active_requests - 当前活动请求
# - trtllm_queue_size - 队列中等待的请求
# - trtllm_gpu_memory_usage_bytes - GPU内存使用
# - trtllm_kv_cache_usage_ratio - KV缓存利用率
```

### 健康检查

```bash
# 就绪探针
curl http://localhost:8000/health/ready

# 存活探针
curl http://localhost:8000/health/live

# 模型信息
curl http://localhost:8000/v1/models
```

**Kubernetes探针**：
```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 60
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 5
```

## 生产部署

### Docker部署

**Dockerfile**：
```dockerfile
FROM nvidia/tensorrt_llm:latest

# 复制任何自定义配置
COPY config.yaml /app/config.yaml

# 暴露端口
EXPOSE 8000 9090

# 启动服务器
CMD ["trtllm-serve", "meta-llama/Meta-Llama-3-8B", \
     "--tp_size", "4", \
     "--dtype", "fp8", \
     "--max_batch_size", "256", \
     "--enable_metrics", \
     "--metrics_port", "9090"]
```

**运行容器**：
```bash
docker run --gpus all -p 8000:8000 -p 9090:9090 \
    tensorrt-llm:latest
```

### Kubernetes部署

**完整部署**：
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tensorrt-llm
spec:
  replicas: 2  # 多个副本以实现高可用性
  selector:
    matchLabels:
      app: tensorrt-llm
  template:
    metadata:
      labels:
        app: tensorrt-llm
    spec:
      containers:
      - name: trtllm
        image: nvidia/tensorrt_llm:latest
        command:
          - trtllm-serve
          - meta-llama/Meta-Llama-3-70B
          - --tp_size=4
          - --dtype=fp8
          - --max_batch_size=256
          - --enable_metrics
        ports:
        - containerPort: 8000
          name: http
        - containerPort: 9090
          name: metrics
        resources:
          limits:
            nvidia.com/gpu: 4
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8000
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
---
apiVersion: v1
kind: Service
metadata:
  name: tensorrt-llm
spec:
  selector:
    app: tensorrt-llm
  ports:
  - name: http
    port: 80
    targetPort: 8000
  - name: metrics
    port: 9090
    targetPort: 9090
  type: LoadBalancer
```

### 负载均衡

**NGINX配置**：
```nginx
upstream tensorrt_llm {
    least_conn;  # 路由到最空闲的服务器
    server trtllm-1:8000 max_fails=3 fail_timeout=30s;
    server trtllm-2:8000 max_fails=3 fail_timeout=30s;
    server trtllm-3:8000 max_fails=3 fail_timeout=30s;
}

server {
    listen 80;
    location / {
        proxy_pass http://tensorrt_llm;
        proxy_read_timeout 300s;  # 长时间生成的更长超时
        proxy_connect_timeout 10s;
    }
}
```

## 自动扩展

### 水平Pod自动扩展器（HPA）

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: tensorrt-llm-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: tensorrt-llm
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Pods
    pods:
      metric:
        name: trtllm_active_requests
      target:
        type: AverageValue
        averageValue: "50"  # 当平均>50个活动请求时扩展
```

### 自定义指标

```yaml
# 基于队列大小扩展
- type: Pods
  pods:
    metric:
      name: trtllm_queue_size
    target:
      type: AverageValue
      averageValue: "10"
```

## 成本优化

### GPU选择

**A100 80GB**（$3-4/小时）：
- 用于：带FP8的70B模型
- 吞吐量：10,000-15,000词元/秒（TP=4）
- 每100万词元成本：$0.20-0.30

**H100 80GB**（$6-8/小时）：
- 用于：带FP8的70B模型、405B模型
- 吞吐量：20,000-30,000词元/秒（TP=4）
- 每100万词元成本：$0.15-0.25（2倍更快=更低成本）

**L4**（$0.50-1/小时）：
- 用于：7-8B模型
- 吞吐量：1,000-2,000词元/秒
- 每100万词元成本：$0.25-0.50

### 批大小调优

**对成本的影响**：
- 批大小1：1,000词元/秒 → 每100万词元$3/小时 = $3/M词元
- 批大小64：5,000词元/秒 → 每100万词元$0.60/小时 = $0.60/M词元
- **使用批处理降低成本5倍**

**建议**：以32-128的批大小为目标以实现成本效率。

## 安全性

### API认证

```bash
# 生成API密钥
export API_KEY=$(openssl rand -hex 32)

# 使用认证启动服务器
trtllm-serve meta-llama/Meta-Llama-3-8B \
    --api_key $API_KEY

# 客户端请求
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "...", "messages": [...]}'
```

### 网络策略

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tensorrt-llm-policy
spec:
  podSelector:
    matchLabels:
      app: tensorrt-llm
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: api-gateway  # 仅允许来自网关
    ports:
    - protocol: TCP
      port: 8000
```

## 故障排除

### 高延迟

**诊断**：
```bash
# 检查队列大小
curl http://localhost:9090/metrics | grep queue_size

# 检查活动请求
curl http://localhost:9090/metrics | grep active_requests
```

**解决方案**：
- 水平扩展（更多副本）
- 增加批大小（如果GPU未充分利用）
- 启用分块上下文（如果提示很长）
- 使用FP8量化

### OOM崩溃

**解决方案**：
- 减少`max_batch_size`
- 减少`max_num_tokens`
- 启用FP8或INT4量化
- 增加`tensor_parallel_size`

### 超时错误

**NGINX配置**：
```nginx
proxy_read_timeout 600s;  # 非常长的生成10分钟
proxy_send_timeout 600s;
```

## 最佳实践

1. **在H100上使用FP8**可获得2倍加速和50%成本降低
2. **监控指标**- 设置Prometheus + Grafana
3. **设置就绪探针**- 防止路由到不健康的Pod
4. **使用负载均衡**- 跨副本分配负载
5. **调优批大小**- 平衡延迟和吞吐量
6. **启用流式传输**- 改善聊天应用的UX
7. **设置自动扩展**- 处理流量峰值
8. **使用持久卷**- 缓存编译的模型
9. **实现重试**- 处理瞬态故障
10. **监控成本**- 跟踪每词元成本
