# Lambda Labs高级用法指南

## 多节点分布式训练

### 跨节点的PyTorch DDP

```python
# train_multi_node.py
import os
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP

def setup_distributed():
    # 启动器设置的环境变量
    rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    local_rank = int(os.environ["LOCAL_RANK"])

    dist.init_process_group(
        backend="nccl",
        rank=rank,
        world_size=world_size
    )

    torch.cuda.set_device(local_rank)
    return rank, world_size, local_rank

def main():
    rank, world_size, local_rank = setup_distributed()

    model = MyModel().cuda(local_rank)
    model = DDP(model, device_ids=[local_rank])

    # 带同步梯度的训练循环
    for epoch in range(num_epochs):
        train_one_epoch(model, dataloader)

        # 仅在rank 0保存检查点
        if rank == 0:
            torch.save(model.module.state_dict(), f"checkpoint_{epoch}.pt")

    dist.destroy_process_group()

if __name__ == "__main__":
    main()
```

### 在多个实例上启动

```bash
# 在节点0（主节点）
export MASTER_ADDR=<节点0私有IP>
export MASTER_PORT=29500

torchrun \
    --nnodes=2 \
    --nproc_per_node=8 \
    --node_rank=0 \
    --master_addr=$MASTER_ADDR \
    --master_port=$MASTER_PORT \
    train_multi_node.py

# 在节点1
export MASTER_ADDR=<节点0私有IP>
export MASTER_PORT=29500

torchrun \
    --nnodes=2 \
    --nproc_per_node=8 \
    --node_rank=1 \
    --master_addr=$MASTER_ADDR \
    --master_port=$MASTER_PORT \
    train_multi_node.py
```

### FSDP用于大模型

```python
from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy
from transformers.models.llama.modeling_llama import LlamaDecoderLayer

# transformer模型的包装策略
auto_wrap_policy = functools.partial(
    transformer_auto_wrap_policy,
    transformer_layer_cls={LlamaDecoderLayer}
)

model = FSDP(
    model,
    auto_wrap_policy=auto_wrap_policy,
    mixed_precision=MixedPrecision(
        param_dtype=torch.bfloat16,
        reduce_dtype=torch.bfloat16,
        buffer_dtype=torch.bfloat16,
    ),
    device_id=local_rank,
)
```

### DeepSpeed ZeRO

```python
# ds_config.json
{
    "train_batch_size": 64,
    "gradient_accumulation_steps": 4,
    "fp16": {"enabled": true},
    "zero_optimization": {
        "stage": 3,
        "offload_optimizer": {"device": "cpu"},
        "offload_param": {"device": "cpu"}
    }
}
```

```bash
# 使用DeepSpeed启动
deepspeed --num_nodes=2 \
    --num_gpus=8 \
    --hostfile=hostfile.txt \
    train.py --deepspeed ds_config.json
```

### 多节点主机文件

```bash
# hostfile.txt
node0_ip slots=8
node1_ip slots=8
```

## API自动化

### 自动启动训练作业

```python
import os
import time
import lambda_cloud_client
from lambda_cloud_client.models import LaunchInstanceRequest

class LambdaJobManager:
    def __init__(self, api_key: str):
        self.config = lambda_cloud_client.Configuration(
            host="https://cloud.lambdalabs.com/api/v1",
            access_token=api_key
        )

    def find_available_gpu(self, gpu_types: list[str], regions: list[str] = None):
        """跨区域查找第一个可用GPU类型。"""
        with lambda_cloud_client.ApiClient(self.config) as client:
            api = lambda_cloud_client.DefaultApi(client)
            types = api.instance_types()

            for gpu_type in gpu_types:
                if gpu_type in types.data:
                    info = types.data[gpu_type]
                    for region in info.regions_with_capacity_available:
                        if regions is None or region.name in regions:
                            return gpu_type, region.name

        return None, None

    def launch_and_wait(self, instance_type: str, region: str,
                        ssh_key: str, filesystem: str = None,
                        timeout: int = 900) -> dict:
        """启动实例并等待其就绪。"""
        with lambda_cloud_client.ApiClient(self.config) as client:
            api = lambda_cloud_client.DefaultApi(client)

            request = LaunchInstanceRequest(
                region_name=region,
                instance_type_name=instance_type,
                ssh_key_names=[ssh_key],
                file_system_names=[filesystem] if filesystem else [],
            )

            response = api.launch_instance(request)
            instance_id = response.data.instance_ids[0]

            # 轮询直到就绪
            start = time.time()
            while time.time() - start < timeout:
                instance = api.get_instance(instance_id)
                if instance.data.status == "active":
                    return {
                        "id": instance_id,
                        "ip": instance.data.ip,
                        "status": "active"
                    }
                time.sleep(30)

            raise TimeoutError(f"实例 {instance_id} 在{timeout}秒后未就绪")

    def terminate(self, instance_ids: list[str]):
        """终止实例。"""
        from lambda_cloud_client.models import TerminateInstanceRequest

        with lambda_cloud_client.ApiClient(self.config) as client:
            api = lambda_cloud_client.DefaultApi(client)
            request = TerminateInstanceRequest(instance_ids=instance_ids)
            api.terminate_instance(request)


# 使用
manager = LambdaJobManager(os.environ["LAMBDA_API_KEY"])

# 查找可用的H100或A100
gpu_type, region = manager.find_available_gpu(
    ["gpu_8x_h100_sxm5", "gpu_8x_a100_80gb_sxm4"],
    regions=["us-west-1", "us-east-1"]
)

if gpu_type:
    instance = manager.launch_and_wait(
        gpu_type, region,
        ssh_key="my-key",
        filesystem="training-data"
    )
    print(f"就绪: ssh ubuntu@{instance['ip']}")
```

### 批量作业提交

```python
import subprocess
import paramiko

def run_remote_job(ip: str, ssh_key_path: str, commands: list[str]):
    """在远程实例上执行命令。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username="ubuntu", key_filename=ssh_key_path)

    for cmd in commands:
        stdin, stdout, stderr = client.exec_command(cmd)
        print(stdout.read().decode())
        if stderr.read():
            print(f"错误: {stderr.read().decode()}")

    client.close()

# 提交训练作业
commands = [
    "cd /lambda/nfs/storage/project",
    "git pull",
    "pip install -r requirements.txt",
    "nohup torchrun --nproc_per_node=8 train.py > train.log 2>&1 &"
]

run_remote_job(instance["ip"], "~/.ssh/lambda_key", commands)
```

### 监控训练进度

```python
def monitor_job(ip: str, ssh_key_path: str, log_file: str = "train.log"):
    """从远程实例流式传输训练日志。"""
    import time

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username="ubuntu", key_filename=ssh_key_path)

    # 尾随日志文件
    stdin, stdout, stderr = client.exec_command(f"tail -f {log_file}")

    try:
        for line in stdout:
            print(line.strip())
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
```

## 1-Click集群工作流程

### Slurm作业提交

```bash
#!/bin/bash
#SBATCH --job-name=llm-training
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=8
#SBATCH --gpus-per-node=8
#SBATCH --time=24:00:00
#SBATCH --output=logs/%j.out
#SBATCH --error=logs/%j.err

# 设置分布式环境
export MASTER_ADDR=$(scontrol show hostnames $SLURM_JOB_NODELIST | head -n 1)
export MASTER_PORT=29500

# 启动训练
srun torchrun \
    --nnodes=$SLURM_NNODES \
    --nproc_per_node=$SLURM_GPUS_PER_NODE \
    --rdzv_backend=c10d \
    --rdzv_endpoint=$MASTER_ADDR:$MASTER_PORT \
    train.py \
    --config config.yaml
```

### 交互式集群会话

```bash
# 请求交互式会话
srun --nodes=1 --ntasks=1 --gpus=8 --time=4:00:00 --pty bash

# 现在在有8 GPU的计算节点上
nvidia-smi
python train.py
```

### 监控集群作业

```bash
# 查看作业队列
squeue

# 查看作业详情
scontrol show job <作业ID>

# 取消作业
scancel <作业ID>

# 查看节点状态
sinfo

# 跨集群查看GPU使用情况
srun --nodes=4 nvidia-smi --query-gpu=name,utilization.gpu --format=csv
```

## 高级文件系统用法

### 数据分级工作流程

```bash
# 从S3将数据分级到文件系统（一次性）
aws s3 sync s3://my-bucket/dataset /lambda/nfs/storage/datasets/

# 或使用rclone
rclone sync s3:my-bucket/dataset /lambda/nfs/storage/datasets/
```

### 跨实例共享文件系统

```python
# 实例1：写入检查点
checkpoint_path = "/lambda/nfs/shared/checkpoints/model_step_1000.pt"
torch.save(model.state_dict(), checkpoint_path)

# 实例2：读取检查点
model.load_state_dict(torch.load(checkpoint_path))
```

### 文件系统最佳实践

```bash
# 为ML工作流组织
/lambda/nfs/storage/
├── datasets/
│   ├── raw/           # 原始数据
│   └── processed/     # 预处理数据
├── models/
│   ├── pretrained/    # 基础模型
│   └── fine-tuned/    # 训练后的模型
├── checkpoints/
│   └── experiment_1/  # 每个实验的检查点
├── logs/
│   └── tensorboard/   # 训练日志
└── outputs/
    └── inference/     # 推理结果
```

## 环境管理

### 自定义Python环境

```bash
# 不要修改系统Python，创建venv
python -m venv ~/myenv
source ~/myenv/bin/activate

# 安装包
pip install torch transformers accelerate

# 保存到文件系统以便重用
cp -r ~/myenv /lambda/nfs/storage/envs/myenv
```

### Conda环境

```bash
# 安装miniconda（如果不存在）
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p ~/miniconda3

# 创建环境
~/miniconda3/bin/conda create -n ml python=3.10 pytorch pytorch-cuda=12.1 -c pytorch -c nvidia -y

# 激活
source ~/miniconda3/bin/activate ml
```

### Docker容器

```bash
# 拉取并运行NVIDIA容器
docker run --gpus all -it --rm \
    -v /lambda/nfs/storage:/data \
    nvcr.io/nvidia/pytorch:24.01-py3

# 在容器中运行训练
docker run --gpus all -d \
    -v /lambda/nfs/storage:/data \
    -v $(pwd):/workspace \
    nvcr.io/nvidia/pytorch:24.01-py3 \
    python /workspace/train.py
```

## 监控和可观测性

### GPU监控

```bash
# 实时GPU统计
watch -n 1 nvidia-smi

# GPU利用率随时间
nvidia-smi dmon -s u -d 1

# 详细GPU信息
nvidia-smi -q
```

### 系统监控

```bash
# CPU和内存
htop

# 磁盘I/O
iostat -x 1

# 网络
iftop

# 所有资源
glances
```

### TensorBoard集成

```bash
# 启动TensorBoard
tensorboard --logdir /lambda/nfs/storage/logs --port 6006 --bind_all

# 从本地机器SSH隧道
ssh -L 6006:localhost:6006 ubuntu@<IP>

# 访问 http://localhost:6006
```

### Weights & Biases集成

```python
import wandb

# 使用API密钥初始化
wandb.login(key=os.environ["WANDB_API_KEY"])

# 开始运行
wandb.init(
    project="lambda-training",
    config={"learning_rate": 1e-4, "epochs": 100}
)

# 记录指标
wandb.log({"loss": loss, "accuracy": acc})

# 将工件保存到文件系统 + W&B
wandb.save("/lambda/nfs/storage/checkpoints/best_model.pt")
```

## 成本优化策略

### 用于中断恢复的检查点

```python
import os

def save_checkpoint(model, optimizer, epoch, loss, path):
    torch.save({
        'epoch': epoch,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'loss': loss,
    }, path)

def load_checkpoint(path, model, optimizer):
    if os.path.exists(path):
        checkpoint = torch.load(path)
        model.load_state_dict(checkpoint['model_state_dict'])
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        return checkpoint['epoch'], checkpoint['loss']
    return 0, float('inf')

# 每N步保存到文件系统
checkpoint_path = "/lambda/nfs/storage/checkpoints/latest.pt"
if step % 1000 == 0:
    save_checkpoint(model, optimizer, epoch, loss, checkpoint_path)
```

### 按工作负载选择实例

```python
def recommend_instance(model_params: int, batch_size: int, task: str) -> str:
    """根据工作负载推荐Lambda实例。"""

    if task == "inference":
        if model_params < 7e9:
            return "gpu_1x_a10"  # $0.75/hr
        elif model_params < 13e9:
            return "gpu_1x_a6000"  # $0.80/hr
        else:
            return "gpu_1x_h100_pcie"  # $2.49/hr

    elif task == "fine-tuning":
        if model_params < 7e9:
            return "gpu_1x_a100"  # $1.29/hr
        elif model_params < 13e9:
            return "gpu_4x_a100"  # $5.16/hr
        else:
            return "gpu_8x_h100_sxm5"  # $23.92/hr

    elif task == "pretraining":
        return "gpu_8x_h100_sxm5"  # 最大性能

    return "gpu_1x_a100"  # 默认
```

### 自动终止空闲实例

```python
import time
from datetime import datetime, timedelta

def auto_terminate_idle(api_key: str, idle_threshold_hours: float = 2):
    """终止空闲时间过长的实例。"""
    manager = LambdaJobManager(api_key)

    with lambda_cloud_client.ApiClient(manager.config) as client:
        api = lambda_cloud_client.DefaultApi(client)
        instances = api.list_instances()

        for instance in instances.data:
            # 检查实例是否运行时间过长而无活动
            # （你需要单独跟踪这个）
            launch_time = instance.launched_at
            if datetime.now() - launch_time > timedelta(hours=idle_threshold_hours):
                print(f"终止空闲实例: {instance.id}")
                manager.terminate([instance.id])
```

## 安全最佳实践

### SSH密钥轮换

```bash
# 生成新密钥对
ssh-keygen -t ed25519 -f ~/.ssh/lambda_key_new -C "lambda-$(date +%Y%m)"

# 通过Lambda控制台或API添加新密钥
# 在运行中的实例上更新authorized_keys
ssh ubuntu@<IP> "echo '$(cat ~/.ssh/lambda_key_new.pub)' >> ~/.ssh/authorized_keys"

# 测试新密钥
ssh -i ~/.ssh/lambda_key_new ubuntu@<IP>

# 从Lambda控制台删除旧密钥
```

### 防火墙配置

```bash
# Lambda控制台：仅开放必要端口
# 推荐：
# - 22 (SSH) - 始终需要
# - 6006 (TensorBoard) - 如使用
# - 8888 (Jupyter) - 如使用
# - 29500 (PyTorch分布式) - 仅多节点
```

### 秘密管理

```bash
# 不要在代码中硬编码API密钥
# 使用环境变量
export HF_TOKEN="hf_..."
export WANDB_API_KEY="..."

# 或使用.env文件（添加到.gitignore）
source .env

# 在实例上，存储在~/.bashrc中
echo 'export HF_TOKEN="..."' >> ~/.bashrc
```
