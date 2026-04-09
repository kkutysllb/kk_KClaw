# Lambda Labs故障排除指南

## 实例启动问题

### 没有可用实例

**错误**："No capacity available"或实例类型未列出

**解决方案**：
```bash
# 通过API检查可用性
curl -u $LAMBDA_API_KEY: \
  https://cloud.lambdalabs.com/api/v1/instance-types | jq '.data | to_entries[] | select(.value.regions_with_capacity_available | length > 0) | .key'

# 尝试不同区域
# 美国区域: us-west-1, us-east-1, us-south-1
# 国际: eu-west-1, asia-northeast-1, 等

# 尝试替代GPU类型
# H100不可用？尝试A100
# A100不可用？尝试A10或A6000
```

### 实例启动卡住

**问题**：实例显示"booting"超过20分钟

**解决方案**：
```bash
# 单GPU：应在3-5分钟内就绪
# 多GPU（8x）：可能需要10-15分钟

# 如果卡住更久：
# 1. 终止实例
# 2. 尝试不同区域
# 3. 尝试不同实例类型
# 4. 如果持续，联系Lambda支持
```

### API认证失败

**错误**：`401 Unauthorized`或`403 Forbidden`

**解决方案**：
```bash
# 验证API密钥格式（应以特定前缀开头）
echo $LAMBDA_API_KEY

# 测试API密钥
curl -u $LAMBDA_API_KEY: \
  https://cloud.lambdalabs.com/api/v1/instance-types

# 如需要，从Lambda控制台生成新API密钥
# 设置 > API密钥 > 生成
```

### 达到配额限制

**错误**："Instance limit reached"或"Quota exceeded"

**解决方案**：
- 在控制台检查当前运行实例
- 终止未使用的实例
- 联系Lambda支持请求增加配额
- 大规模需求使用1-Click集群

## SSH连接问题

### 连接被拒绝

**错误**：`ssh: connect to host <IP> port 22: Connection refused`

**解决方案**：
```bash
# 等待实例完全初始化
# 单GPU：3-5分钟
# 多GPU：10-15分钟

# 在控制台检查实例状态（应为"active"）

# 验证正确的IP地址
curl -u $LAMBDA_API_KEY: \
  https://cloud.lambdalabs.com/api/v1/instances | jq '.data[].ip'
```

### 权限被拒绝

**错误**：`Permission denied (publickey)`

**解决方案**：
```bash
# 验证SSH密钥匹配
ssh -v -i ~/.ssh/lambda_key ubuntu@<IP>

# 检查密钥权限
chmod 600 ~/.ssh/lambda_key
chmod 644 ~/.ssh/lambda_key.pub

# 验证密钥在启动前已添加到Lambda控制台
# 密钥必须在启动前添加

# 检查实例上的authorized_keys（如有其他方式进入）
cat ~/.ssh/authorized_keys
```

### 主机密钥验证失败

**错误**：`WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`

**解决方案**：
```bash
# 当IP被不同实例重用时会发生
# 移除旧密钥
ssh-keygen -R <IP>

# 然后重新连接
ssh ubuntu@<IP>
```

### SSH超时

**错误**：`ssh: connect to host <IP> port 22: Operation timed out`

**解决方案**：
```bash
# 检查实例是否为"active"状态

# 验证防火墙允许SSH（端口22）
# Lambda控制台 > 防火墙

# 检查本地网络允许出站SSH

# 从不同网络/ VPN尝试
```

## GPU问题

### GPU未检测到

**错误**：`nvidia-smi: command not found`或不显示GPU

**解决方案**：
```bash
# 重启实例
sudo reboot

# 如需要，重新安装NVIDIA驱动程序
wget -nv -O- https://lambdalabs.com/install-lambda-stack.sh | sh -
sudo reboot

# 检查驱动程序状态
nvidia-smi
lsmod | grep nvidia
```

### CUDA内存不足

**错误**：`torch.cuda.OutOfMemoryError: CUDA out of memory`

**解决方案**：
```python
# 检查GPU内存
import torch
print(torch.cuda.get_device_properties(0).total_memory / 1e9, "GB")

# 清除缓存
torch.cuda.empty_cache()

# 减少批量大小
batch_size = batch_size // 2

# 启用梯度检查点
model.gradient_checkpointing_enable()

# 使用混合精度
from torch.cuda.amp import autocast
with autocast():
    outputs = model(**inputs)

# 使用更大GPU实例
# A100-40GB → A100-80GB → H100
```

### CUDA版本不匹配

**错误**：`CUDA driver version is insufficient for CUDA runtime version`

**解决方案**：
```bash
# 检查版本
nvidia-smi  # 显示驱动CUDA版本
nvcc --version  # 显示工具包版本

# Lambda Stack应该有兼容版本
# 如不匹配，重新安装Lambda Stack
wget -nv -O- https://lambdalabs.com/install-lambda-stack.sh | sh -
sudo reboot

# 或安装特定PyTorch版本
pip install torch==2.1.0+cu121 -f https://download.pytorch.org/whl/torch_stable.html
```

### 多GPU不工作

**错误**：仅使用一个GPU

**解决方案**：
```python
# 检查所有可见GPU
import torch
print(f"可用GPU: {torch.cuda.device_count()}")

# 验证CUDA_VISIBLE_DEVICES未限制性设置
import os
print(os.environ.get("CUDA_VISIBLE_DEVICES", "not set"))

# 使用DataParallel或DistributedDataParallel
model = torch.nn.DataParallel(model)
# 或
model = torch.nn.parallel.DistributedDataParallel(model)
```

## 文件系统问题

### 文件系统未挂载

**错误**：`/lambda/nfs/<name>`不存在

**解决方案**：
```bash
# 文件系统必须在启动时附加
# 无法附加到运行中的实例

# 验证启动时选择了文件系统

# 检查挂载点
df -h | grep lambda

# 如缺失，终止并使用文件系统重新启动
```

### 文件系统性能慢

**问题**：读写文件系统慢

**解决方案**：
```bash
# 使用本地SSD用于临时/中间文件
# /home/ubuntu有快速NVMe存储

# 将频繁访问的数据复制到本地存储
cp -r /lambda/nfs/storage/dataset /home/ubuntu/dataset

# 仅将文件系统用于检查点和最终输出

# 检查网络带宽
iperf3 -c <filesystem_server>
```

### 终止后数据丢失

**问题**：实例终止后文件消失

**解决方案**：
```bash
# 根卷（/home/ubuntu）是临时的
# 终止时那里的数据会丢失

# 始终使用文件系统用于持久数据
/lambda/nfs/<filesystem_name>/

# 终止前同步重要的本地文件
rsync -av /home/ubuntu/outputs/ /lambda/nfs/storage/outputs/
```

### 文件系统满

**错误**：`No space left on device`

**解决方案**：
```bash
# 检查文件系统使用情况
df -h /lambda/nfs/storage

# 查找大文件
du -sh /lambda/nfs/storage/* | sort -h

# 清理旧检查点
find /lambda/nfs/storage/checkpoints -mtime +7 -delete

# 在Lambda控制台增加文件系统大小
# （可能需要支持请求）
```

## 网络问题

### 端口不可访问

**错误**：无法连接到服务（TensorBoard、Jupyter等）

**解决方案**：
```bash
# Lambda默认：仅端口22开放
# 在Lambda控制台配置防火墙

# 或使用SSH隧道（推荐）
ssh -L 6006:localhost:6006 ubuntu@<IP>
# 访问 http://localhost:6006

# 对于Jupyter
ssh -L 8888:localhost:8888 ubuntu@<IP>
```

### 数据下载慢

**问题**：下载数据集慢

**解决方案**：
```bash
# 检查可用带宽
speedtest-cli

# 使用多线程下载
aria2c -x 16 <URL>

# 对于HuggingFace模型
export HF_HUB_ENABLE_HF_TRANSFER=1
pip install hf_transfer

# 对于S3，使用并行传输
aws s3 sync s3://bucket/data /local/data --quiet
```

### 节点间通信失败

**错误**：分布式训练无法连接节点

**解决方案**：
```bash
# 验证节点在同一区域（必需）

# 检查私有IP可以通信
ping <other_node_private_ip>

# 验证NCCL设置
export NCCL_DEBUG=INFO
export NCCL_IB_DISABLE=0  # 如可用，启用InfiniBand

# 检查防火墙允许分布式端口
# 需要: 29500（PyTorch）或配置的MASTER_PORT
```

## 软件问题

### 包安装失败

**错误**：`pip install`错误

**解决方案**：
```bash
# 使用虚拟环境（不要修改系统Python）
python -m venv ~/myenv
source ~/myenv/bin/activate
pip install <package>

# 对于CUDA包，匹配CUDA版本
pip install torch --index-url https://download.pytorch.org/whl/cu121

# 如果pip缓存损坏，清除
pip cache purge
```

### Python版本问题

**错误**：包需要不同Python版本

**解决方案**：
```bash
# 安装替代Python（不要替换系统Python）
sudo apt install python3.11 python3.11-venv python3.11-dev

# 使用特定Python创建venv
python3.11 -m venv ~/py311env
source ~/py311env/bin/activate
```

### ImportError或ModuleNotFoundError

**错误**：尽管已安装但找不到模块

**解决方案**：
```bash
# 验证正确的Python环境
which python
pip list | grep <module>

# 确保虚拟环境已激活
source ~/myenv/bin/activate

# 在正确环境中重新安装
pip uninstall <package>
pip install <package>
```

## 训练问题

### 训练挂起

**问题**：训练停止进展，无输出

**解决方案**：
```bash
# 检查GPU利用率
watch -n 1 nvidia-smi

# 如果GPU为0%，可能是数据加载瓶颈
# 增加DataLoader中的num_workers

# 检查分布式训练中的死锁
export NCCL_DEBUG=INFO

# 添加超时
dist.init_process_group(..., timeout=timedelta(minutes=30))
```

### 检查点损坏

**错误**：`RuntimeError: storage has wrong size`或类似

**解决方案**：
```python
# 使用安全保存模式
checkpoint_path = "/lambda/nfs/storage/checkpoint.pt"
temp_path = checkpoint_path + ".tmp"

# 先保存到临时文件
torch.save(state_dict, temp_path)
# 然后原子重命名
os.rename(temp_path, checkpoint_path)

# 对于加载损坏的检查点
try:
    state = torch.load(checkpoint_path)
except:
    # 回退到上一个检查点
    state = torch.load(checkpoint_path + ".backup")
```

### 内存泄漏

**问题**：内存使用随时间增长

**解决方案**：
```python
# 定期清除CUDA缓存
torch.cuda.empty_cache()

# 记录时分离张量
loss_value = loss.detach().cpu().item()

# 不要无意中累积梯度
optimizer.zero_grad(set_to_none=True)

# 正确使用梯度累积
if (step + 1) % accumulation_steps == 0:
    optimizer.step()
    optimizer.zero_grad()
```

## 计费问题

### 意外收费

**问题**：账单高于预期

**解决方案**：
```bash
# 检查遗忘的运行实例
curl -u $LAMBDA_API_KEY: \
  https://cloud.lambdalabs.com/api/v1/instances | jq '.data[].id'

# 终止所有实例
# Lambda控制台 > 实例 > 终止全部

# Lambda按分钟计费
# 停止的实例不收费（但没有"停止"功能 - 只能终止）
```

### 实例意外终止

**问题**：实例在未手动终止的情况下消失

**可能原因**：
- 付款问题（卡被拒）
- 账户暂停
- 实例健康检查失败

**解决方案**：
- 检查Lambda通知邮件
- 在控制台验证付款方式
- 联系Lambda支持
- 始终保存检查点到文件系统

## 常见错误消息

| 错误 | 原因 | 解决方案 |
|-------|-------|----------|
| `No capacity available` | 区域/GPU售罄 | 尝试不同区域或GPU类型 |
| `Permission denied (publickey)` | SSH密钥不匹配 | 重新添加密钥，检查权限 |
| `CUDA out of memory` | 模型太大 | 减少批量大小，使用更大GPU |
| `No space left on device` | 磁盘满 | 清理或使用文件系统 |
| `Connection refused` | 实例未就绪 | 等待3-15分钟启动 |
| `Module not found` | 错误Python环境 | 激活正确的虚拟环境 |

## 获取帮助

1. **文档**：https://docs.lambda.ai
2. **支持**：https://support.lambdalabs.com
3. **邮箱**：support@lambdalabs.com
4. **状态**：检查Lambda状态页面了解中断

### 联系支持时包含的信息

- 实例ID
- 区域
- 实例类型
- 错误消息（完整traceback）
- 重现步骤
- 发生时间
