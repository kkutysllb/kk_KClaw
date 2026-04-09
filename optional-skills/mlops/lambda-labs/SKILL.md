---
name: lambda-labs-gpu-cloud
description: 用于ML训练和推理的预留和按需GPU云实例。当需要具有简单SSH访问、持久文件系统或高性能多节点集群的大规模训练专用GPU实例时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [lambda-cloud-client>=1.0.0]
metadata:
  kclaw:
    tags: [基础设施, GPU云, 训练, 推理, Lambda Labs]

---

# Lambda Labs GPU云

在Lambda Labs GPU云上运行ML工作负载的完整指南，提供按需实例和1-Click集群。

## 何时使用Lambda Labs

**在以下情况下使用Lambda Labs：**
- 需要具有完全SSH访问权限的专用GPU实例
- 运行长时间训练任务（数小时到数天）
- 想要简单的定价且无出口费
- 需要跨会话的持久存储
- 需要高性能多节点集群（16-512 GPU）
- 想要预安装的ML堆栈（带PyTorch、CUDA、NCCL的Lambda Stack）

**关键特性：**
- **GPU种类**：B200、H100、GH200、A100、A10、A6000、V100
- **Lambda Stack**：预安装PyTorch、TensorFlow、CUDA、cuDNN、NCCL
- **持久文件系统**：跨实例重启保持数据
- **1-Click集群**：带InfiniBand的16-512 GPU Slurm集群
- **简单定价**：按分钟付费，无出口费
- **全球区域**：全球12+个区域

**使用替代方案：**
- **Modal**：用于无服务器、自动扩展工作负载
- **SkyPilot**：用于多云编排和成本优化
- **RunPod**：用于更便宜的spot实例和无服务器端点
- **Vast.ai**：用于具有最低价格的GPU市场

## 快速开始

### 账户设置

1. 在https://lambda.ai创建账户
2. 添加付款方式
3. 从仪表板生成API密钥
4. 添加SSH密钥（启动实例前必需）

### 通过控制台启动

1. 转到https://cloud.lambda.ai/instances
2. 点击"启动实例"
3. 选择GPU类型和区域
4. 选择SSH密钥
5. 可选附加文件系统
6. 启动并等待3-15分钟

### 通过SSH连接

```bash
# 从控制台获取实例IP
ssh ubuntu@<实例IP>

# 或使用特定密钥
ssh -i ~/.ssh/lambda_key ubuntu@<实例IP>
```

## GPU实例

### 可用GPU

| GPU | VRAM | 价格/GPU/小时 | 最佳用途 |
|-----|------|--------------|----------|
| B200 SXM6 | 180 GB | $4.99 | 最大模型，最快训练 |
| H100 SXM | 80 GB | $2.99-3.29 | 大模型训练 |
| H100 PCIe | 80 GB | $2.49 | 成本效益H100 |
| GH200 | 96 GB | $1.49 | 单GPU大模型 |
| A100 80GB | 80 GB | $1.79 | 生产训练 |
| A100 40GB | 40 GB | $1.29 | 标准训练 |
| A10 | 24 GB | $0.75 | 推理、微调 |
| A6000 | 48 GB | $0.80 | 良好VRAM/价格比 |
| V100 | 16 GB | $0.55 | 预算训练 |

### 实例配置

```
8x GPU：分布式训练最佳（DDP、FSDP）
4x GPU：大模型、多GPU训练
2x GPU：中等工作负载
1x GPU：微调、推理、开发
```

### 启动时间

- 单GPU：3-5分钟
- 多GPU：10-15分钟

## Lambda Stack

所有实例都预装了Lambda Stack：

```bash
# 包含的软件
- Ubuntu 22.04 LTS
- NVIDIA驱动程序（最新）
- CUDA 12.x
- cuDNN 8.x
- NCCL（用于多GPU）
- PyTorch（最新）
- TensorFlow（最新）
- JAX
- JupyterLab
```

### 验证安装

```bash
# 检查GPU
nvidia-smi

# 检查PyTorch
python -c "import torch; print(torch.cuda.is_available())"

# 检查CUDA版本
nvcc --version
```

## Python API

### 安装

```bash
pip install lambda-cloud-client
```

### 认证

```python
import os
import lambda_cloud_client

# 使用API密钥配置
configuration = lambda_cloud_client.Configuration(
    host="https://cloud.lambdalabs.com/api/v1",
    access_token=os.environ["LAMBDA_API_KEY"]
)
```

### 列出可用实例

```python
with lambda_cloud_client.ApiClient(configuration) as api_client:
    api = lambda_cloud_client.DefaultApi(api_client)

    # 获取可用实例类型
    types = api.instance_types()
    for name, info in types.data.items():
        print(f"{name}: {info.instance_type.description}")
```

### 启动实例

```python
from lambda_cloud_client.models import LaunchInstanceRequest

request = LaunchInstanceRequest(
    region_name="us-west-1",
    instance_type_name="gpu_1x_h100_sxm5",
    ssh_key_names=["my-ssh-key"],
    file_system_names=["my-filesystem"],  # 可选
    name="training-job"
)

response = api.launch_instance(request)
instance_id = response.data.instance_ids[0]
print(f"已启动: {instance_id}")
```

### 列出运行中的实例

```python
instances = api.list_instances()
for instance in instances.data:
    print(f"{instance.name}: {instance.ip} ({instance.status})")
```

### 终止实例

```python
from lambda_cloud_client.models import TerminateInstanceRequest

request = TerminateInstanceRequest(
    instance_ids=[instance_id]
)
api.terminate_instance(request)
```

### SSH密钥管理

```python
from lambda_cloud_client.models import AddSshKeyRequest

# 添加SSH密钥
request = AddSshKeyRequest(
    name="my-key",
    public_key="ssh-rsa AAAA..."
)
api.add_ssh_key(request)

# 列出密钥
keys = api.list_ssh_keys()

# 删除密钥
api.delete_ssh_key(key_id)
```

## 使用curl的CLI

### 列出实例类型

```bash
curl -u $LAMBDA_API_KEY: \
  https://cloud.lambdalabs.com/api/v1/instance-types | jq
```

### 启动实例

```bash
curl -u $LAMBDA_API_KEY: \
  -X POST https://cloud.lambdalabs.com/api/v1/instance-operations/launch \
  -H "Content-Type: application/json" \
  -d '{
    "region_name": "us-west-1",
    "instance_type_name": "gpu_1x_h100_sxm5",
    "ssh_key_names": ["my-key"]
  }' | jq
```

### 终止实例

```bash
curl -u $LAMBDA_API_KEY: \
  -X POST https://cloud.lambdalabs.com/api/v1/instance-operations/terminate \
  -H "Content-Type: application/json" \
  -d '{"instance_ids": ["<实例ID>"]}' | jq
```

## 持久存储

### 文件系统

文件系统跨实例重启保持数据：

```bash
# 挂载位置
/lambda/nfs/<文件系统名称>

# 示例：保存检查点
python train.py --checkpoint-dir /lambda/nfs/my-storage/checkpoints
```

### 创建文件系统

1. 转到Lambda控制台中的存储
2. 点击"创建文件系统"
3. 选择区域（必须与实例区域匹配）
4. 命名并创建

### 附加到实例

文件系统必须在实例启动时附加：
- 通过控制台：启动时选择文件系统
- 通过API：在启动请求中包含`file_system_names`

### 最佳实践

```bash
# 存储在文件系统（持久）
/lambda/nfs/storage/
  ├── datasets/
  ├── checkpoints/
  ├── models/
  └── outputs/

# 本地SSD（更快，临时）
/home/ubuntu/
  └── working/  # 临时文件
```

## SSH配置

### 添加SSH密钥

```bash
# 本地生成密钥
ssh-keygen -t ed25519 -f ~/.ssh/lambda_key

# 添加公钥到Lambda控制台
# 或通过API
```

### 多密钥

```bash
# 在实例上，添加更多密钥
echo 'ssh-rsa AAAA...' >> ~/.ssh/authorized_keys
```

### 从GitHub导入

```bash
# 在实例上
ssh-import-id gh:username
```

### SSH隧道

```bash
# 转发Jupyter
ssh -L 8888:localhost:8888 ubuntu@<IP>

# 转发TensorBoard
ssh -L 6006:localhost:6006 ubuntu@<IP>

# 多端口
ssh -L 8888:localhost:8888 -L 6006:localhost:6006 ubuntu@<IP>
```

## JupyterLab

### 从控制台启动

1. 转到实例页面
2. 点击Cloud IDE列中的"启动"
3. JupyterLab在浏览器中打开

### 手动访问

```bash
# 在实例上
jupyter lab --ip=0.0.0.0 --port=8888

# 从本地机器通过隧道
ssh -L 8888:localhost:8888 ubuntu@<IP>
# 打开 http://localhost:8888
```

## 训练工作流程

### 单GPU训练

```bash
# SSH到实例
ssh ubuntu@<IP>

# 克隆仓库
git clone https://github.com/user/project
cd project

# 安装依赖
pip install -r requirements.txt

# 训练
python train.py --epochs 100 --checkpoint-dir /lambda/nfs/storage/checkpoints
```

### 多GPU训练（单节点）

```python
# train_ddp.py
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP

def main():
    dist.init_process_group("nccl")
    rank = dist.get_rank()
    device = rank % torch.cuda.device_count()

    model = MyModel().to(device)
    model = DDP(model, device_ids=[device])

    # 训练循环...

if __name__ == "__main__":
    main()
```

```bash
# 使用torchrun启动（8 GPU）
torchrun --nproc_per_node=8 train_ddp.py
```

### 检查点到文件系统

```python
import os

checkpoint_dir = "/lambda/nfs/my-storage/checkpoints"
os.makedirs(checkpoint_dir, exist_ok=True)

# 保存检查点
torch.save({
    'epoch': epoch,
    'model_state_dict': model.state_dict(),
    'optimizer_state_dict': optimizer.state_dict(),
    'loss': loss,
}, f"{checkpoint_dir}/checkpoint_{epoch}.pt")
```

## 1-Click集群

### 概述

高性能Slurm集群，具有：
- 16-512 NVIDIA H100或B200 GPU
- NVIDIA Quantum-2 400 Gb/s InfiniBand
- 3200 Gb/s GPUDirect RDMA
- 预安装的分布式ML堆栈

### 包含软件

- Ubuntu 22.04 LTS + Lambda Stack
- NCCL、Open MPI
- 带DDP和FSDP的PyTorch
- TensorFlow
- OFED驱动程序

### 存储

- 每计算节点24 TB NVMe（临时）
- Lambda文件系统用于持久数据

### 多节点训练

```bash
# 在Slurm集群上
srun --nodes=4 --ntasks-per-node=8 --gpus-per-node=8 \
  torchrun --nnodes=4 --nproc_per_node=8 \
  --rdzv_backend=c10d --rdzv_endpoint=$MASTER_ADDR:29500 \
  train.py
```

## 网络

### 带宽

- 跨实例（同区域）：最高200 Gbps
- 互联网出口：最高20 Gbps

### 防火墙

- 默认：仅端口22（SSH）开放
- 在Lambda控制台配置其他端口
- 默认允许ICMP流量

### 私有IP

```bash
# 查找私有IP
ip addr show | grep 'inet '
```

## 常见工作流程

### 工作流程1：微调LLM

```bash
# 1. 使用文件系统启动8x H100实例

# 2. SSH并设置
ssh ubuntu@<IP>
pip install transformers accelerate peft

# 3. 下载模型到文件系统
python -c "
from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained('meta-llama/Llama-2-7b-hf')
model.save_pretrained('/lambda/nfs/storage/models/llama-2-7b')
"

# 4. 在文件系统中使用检查点微调
accelerate launch --num_processes 8 train.py \
  --model_path /lambda/nfs/storage/models/llama-2-7b \
  --output_dir /lambda/nfs/storage/outputs \
  --checkpoint_dir /lambda/nfs/storage/checkpoints
```

### 工作流程2：批量推理

```bash
# 1. 启动A10实例（推理成本效益高）

# 2. 运行推理
python inference.py \
  --model /lambda/nfs/storage/models/fine-tuned \
  --input /lambda/nfs/storage/data/inputs.jsonl \
  --output /lambda/nfs/storage/data/outputs.jsonl
```

## 成本优化

### 选择正确的GPU

| 任务 | 推荐GPU |
|------|---------|
| LLM微调（7B） | A100 40GB |
| LLM微调（70B） | 8x H100 |
| 推理 | A10、A6000 |
| 开发 | V100、A10 |
| 最大性能 | B200 |

### 降低成本

1. **使用文件系统**：避免重新下载数据
2. **经常检查点**：恢复中断的训练
3. **合理大小**：不要过度配置GPU
4. **终止空闲**：无自动停止，手动终止

### 监控使用

- 仪表板显示实时GPU利用率
- API用于编程监控

## 常见问题

| 问题 | 解决方案 |
|------|----------|
| 实例无法启动 | 检查区域可用性，尝试不同GPU |
| SSH连接被拒绝 | 等待实例初始化（3-15分钟） |
| 终止后数据丢失 | 使用持久文件系统 |
| 数据传输慢 | 使用同区域文件系统 |
| GPU未检测到 | 重启实例，检查驱动程序 |

## 参考

- **[高级用法](references/advanced-usage.md)** - 多节点训练、API自动化
- **[故障排除](references/troubleshooting.md)** - 常见问题和解决方案

## 资源

- **文档**：https://docs.lambda.ai
- **控制台**：https://cloud.lambda.ai
- **定价**：https://lambda.ai/instances
- **支持**：https://support.lambdalabs.com
- **博客**：https://lambda.ai/blog
