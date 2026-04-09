---
name: pytorch-lightning
description: 带Trainer类的高级PyTorch框架，自动分布式训练（DDP/FSDP/DeepSpeed）、回调系统和最少样板代码。从笔记本到超级计算机使用相同代码。当想要带内置最佳实践的干净训练循环时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [lightning, torch, transformers]
metadata:
  kclaw:
    tags: [PyTorch Lightning, 训练框架, 分布式训练, DDP, FSDP, DeepSpeed, 高级API, 回调, 最佳实践, 可扩展]

---

# PyTorch Lightning - 高级训练框架

## 快速开始

PyTorch Lightning组织PyTorch代码以消除样板代码，同时保持灵活性。

**安装**：
```bash
pip install lightning
```

**转换为Lightning**（3步）：

```python
import lightning as L
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

# 步骤1：定义LightningModule（组织你的PyTorch代码）
class LitModel(L.LightningModule):
    def __init__(self, hidden_size=128):
        super().__init__()
        self.model = nn.Sequential(
            nn.Linear(28 * 28, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, 10)
        )

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)
        loss = nn.functional.cross_entropy(y_hat, y)
        self.log('train_loss', loss)  # 自动记录到TensorBoard
        return loss

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=1e-3)

# 步骤2：创建数据
train_loader = DataLoader(train_dataset, batch_size=32)

# 步骤3：用Trainer训练（处理其他一切！）
trainer = L.Trainer(max_epochs=10, accelerator='gpu', devices=2)
model = LitModel()
trainer.fit(model, train_loader)
```

**就这样！** Trainer处理：
- GPU/TPU/CPU切换
- 分布式训练（DDP, FSDP, DeepSpeed）
- 混合精度（FP16, BF16）
- 梯度累积
- 检查点保存
- 日志记录
- 进度条

## 常见工作流

### 工作流1：从PyTorch到Lightning

**原始PyTorch代码**：
```python
model = MyModel()
optimizer = torch.optim.Adam(model.parameters())
model.to('cuda')

for epoch in range(max_epochs):
    for batch in train_loader:
        batch = batch.to('cuda')
        optimizer.zero_grad()
        loss = model(batch)
        loss.backward()
        optimizer.step()
```

**Lightning版本**：
```python
class LitModel(L.LightningModule):
    def __init__(self):
        super().__init__()
        self.model = MyModel()

    def training_step(self, batch, batch_idx):
        loss = self.model(batch)  # 不需要.to('cuda')！
        return loss

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters())

# 训练
trainer = L.Trainer(max_epochs=10, accelerator='gpu')
trainer.fit(LitModel(), train_loader)
```

**好处**：40+行 → 15行，无需设备管理，自动分布式

### 工作流2：验证和测试

```python
class LitModel(L.LightningModule):
    def __init__(self):
        super().__init__()
        self.model = MyModel()

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)
        loss = nn.functional.cross_entropy(y_hat, y)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)
        val_loss = nn.functional.cross_entropy(y_hat, y)
        acc = (y_hat.argmax(dim=1) == y).float().mean()
        self.log('val_loss', val_loss)
        self.log('val_acc', acc)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)
        test_loss = nn.functional.cross_entropy(y_hat, y)
        self.log('test_loss', test_loss)

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=1e-3)

# 带验证的训练
trainer = L.Trainer(max_epochs=10)
trainer.fit(model, train_loader, val_loader)

# 测试
trainer.test(model, test_loader)
```

**自动功能**：
- 验证默认每epoch运行
- 指标记录到TensorBoard
- 基于val_loss的最佳模型检查点保存

### 工作流3：分布式训练（DDP）

```python
# 与单GPU相同的代码！
model = LitModel()

# 8个GPU配DDP（自动！）
trainer = L.Trainer(
    accelerator='gpu',
    devices=8,
    strategy='ddp'  # 或 'fsdp', 'deepspeed'
)

trainer.fit(model, train_loader)
```

**启动**：
```bash
# 单条命令，Lightning处理其余
python train.py
```

**无需更改**：
- 自动数据分发
- 梯度同步
- 多节点支持（只需设置`num_nodes=2`）

### 工作流4：用于监控的回调

```python
from lightning.pytorch.callbacks import ModelCheckpoint, EarlyStopping, LearningRateMonitor

# 创建回调
checkpoint = ModelCheckpoint(
    monitor='val_loss',
    mode='min',
    save_top_k=3,
    filename='model-{epoch:02d}-{val_loss:.2f}'
)

early_stop = EarlyStopping(
    monitor='val_loss',
    patience=5,
    mode='min'
)

lr_monitor = LearningRateMonitor(logging_interval='epoch')

# 添加到Trainer
trainer = L.Trainer(
    max_epochs=100,
    callbacks=[checkpoint, early_stop, lr_monitor]
)

trainer.fit(model, train_loader, val_loader)
```

**结果**：
- 自动保存最佳3个模型
- 如果5个epoch无改进则提前停止
- 学习率记录到TensorBoard

### 工作流5：学习率调度

```python
class LitModel(L.LightningModule):
    # ... (training_step等)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=1e-3)

        # 余弦退火
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer,
            T_max=100,
            eta_min=1e-5
        )

        return {
            'optimizer': optimizer,
            'lr_scheduler': {
                'scheduler': scheduler,
                'interval': 'epoch',  # 每epoch更新
                'frequency': 1
            }
        }

# 学习率自动记录！
trainer = L.Trainer(max_epochs=100)
trainer.fit(model, train_loader)
```

## 何时使用与替代方案

**在以下情况下使用PyTorch Lightning**：
- 想要干净、有组织的代码
- 需要生产级训练循环
- 在单GPU、多GPU、TPU之间切换
- 想要内置回调和日志记录
- 团队协作（标准化结构）

**关键优势**：
- **有组织**：将研究代码与工程分离
- **自动化**：DDP、FSDP、DeepSpeed只需1行
- **回调**：模块化训练扩展
- **可重现**：更少样板代码 = 更少bug
- **经过测试**：每月100万+下载，经过实战检验

**使用替代方案**：
- **Accelerate**：对现有代码改动最小，更灵活
- **Ray Train**：多节点编排、超参数调优
- **原生PyTorch**：最大控制，学习目的
- **Keras**：TensorFlow生态系统

## 常见问题

**问题：损失不下降**

检查数据和模型设置：
```python
# 添加到training_step
def training_step(self, batch, batch_idx):
    if batch_idx == 0:
        print(f"Batch shape: {batch[0].shape}")
        print(f"Labels: {batch[1]}")
    loss = ...
    return loss
```

**问题：内存不足**

减小批量大小或使用梯度累积：
```python
trainer = L.Trainer(
    accumulate_grad_batches=4,  # 有效批量 = batch_size × 4
    precision='bf16'  # 或 'fp16'，减少50%内存
)
```

**问题：验证不运行**

确保传递了val_loader：
```python
# 错误
trainer.fit(model, train_loader)

# 正确
trainer.fit(model, train_loader, val_loader)
```

**问题：DDP产生意外的多进程**

Lightning自动检测GPU。明确设置设备：
```python
# 先在CPU上测试
trainer = L.Trainer(accelerator='cpu', devices=1)

# 然后用GPU
trainer = L.Trainer(accelerator='gpu', devices=1)
```

## 高级主题

**回调**：有关EarlyStopping、ModelCheckpoint、自定义回调和回调钩子，请参阅[references/callbacks.md](references/callbacks.md)。

**分布式策略**：有关DDP、FSDP、DeepSpeed ZeRO集成、多节点设置，请参阅[references/distributed.md](references/distributed.md)。

**超参数调优**：有关与Optuna、Ray Tune和WandB sweeps的集成，请参阅[references/hyperparameter-tuning.md](references/hyperparameter-tuning.md)。

## 硬件要求

- **CPU**：可用（适合调试）
- **单GPU**：可用
- **多GPU**：DDP（默认）、FSDP或DeepSpeed
- **多节点**：DDP、FSDP、DeepSpeed
- **TPU**：支持（8核）
- **Apple MPS**：支持

**精度选项**：
- FP32（默认）
- FP16（V100、旧GPU）
- BF16（A100/H100，推荐）
- FP8（H100）

## 资源

- 文档：https://lightning.ai/docs/pytorch/stable/
- GitHub：https://github.com/Lightning-AI/pytorch-lightning ⭐ 29,000+
- 版本：2.5.5+
- 示例：https://github.com/Lightning-AI/pytorch-lightning/tree/master/examples
- Discord：https://discord.gg/lightning-ai
- 使用者：Kaggle获胜者、研究实验室、生产团队
