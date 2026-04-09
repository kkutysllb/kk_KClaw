# PyTorch Lightning回调

## 概述

回调在不修改LightningModule的情况下为训练添加功能。它们处理**非必要逻辑**，如检查点保存、早停和日志记录。

## 内置回调

### 1. ModelCheckpoint

**在训练期间保存最佳模型**：

```python
from lightning.pytorch.callbacks import ModelCheckpoint

# 基于验证损失保存前3个模型
checkpoint = ModelCheckpoint(
    dirpath='checkpoints/',
    filename='model-{epoch:02d}-{val_loss:.2f}',
    monitor='val_loss',
    mode='min',
    save_top_k=3,
    save_last=True,  # 也保存最后一个epoch
    verbose=True
)

trainer = L.Trainer(callbacks=[checkpoint])
trainer.fit(model, train_loader, val_loader)
```

**配置选项**：
```python
checkpoint = ModelCheckpoint(
    monitor='val_acc',        # 要监控的指标
    mode='max',               # 准确率为'max'，损失为'min'
    save_top_k=5,             # 保留最佳5个模型
    save_last=True,           # 单独保存最后一个epoch
    every_n_epochs=1,         # 每N个epoch保存一次
    save_on_train_epoch_end=False,  # 改为在验证结束时保存
    filename='best-{epoch}-{val_acc:.3f}',  # 命名模式
    auto_insert_metric_name=False  # 不自动添加指标到文件名
)
```

**加载检查点**：
```python
# 加载最佳模型
best_model_path = checkpoint.best_model_path
model = LitModel.load_from_checkpoint(best_model_path)

# 恢复训练
trainer = L.Trainer(callbacks=[checkpoint])
trainer.fit(model, train_loader, val_loader, ckpt_path='checkpoints/last.ckpt')
```

### 2. EarlyStopping

**当指标停止改善时停止训练**：

```python
from lightning.pytorch.callbacks import EarlyStopping

early_stop = EarlyStopping(
    monitor='val_loss',
    patience=5,               # 等待5个epoch
    mode='min',
    min_delta=0.001,          # 最小变化量才算改善
    verbose=True,
    strict=True,              # 如果找不到监控的指标则崩溃
    check_on_train_epoch_end=False  # 在验证结束时检查
)

trainer = L.Trainer(callbacks=[early_stop])
trainer.fit(model, train_loader, val_loader)
# 如果5个epoch没有改善则自动停止
```

**高级用法**：
```python
early_stop = EarlyStopping(
    monitor='val_loss',
    patience=10,
    min_delta=0.0,
    verbose=True,
    mode='min',
    stopping_threshold=0.1,   # 如果val_loss < 0.1则停止
    divergence_threshold=5.0, # 如果val_loss > 5.0则停止
    check_finite=True         # NaN/Inf时停止
)
```

### 3. LearningRateMonitor

**记录学习率**：

```python
from lightning.pytorch.callbacks import LearningRateMonitor

lr_monitor = LearningRateMonitor(
    logging_interval='epoch',  # 或'step'
    log_momentum=True          # 也记录动量
)

trainer = L.Trainer(callbacks=[lr_monitor])
# 学习率自动记录到TensorBoard/WandB
```

### 4. TQDMProgressBar

**自定义进度条**：

```python
from lightning.pytorch.callbacks import TQDMProgressBar

progress_bar = TQDMProgressBar(
    refresh_rate=10,  # 每10个batch更新一次
    process_position=0
)

trainer = L.Trainer(callbacks=[progress_bar])
```

### 5. GradientAccumulationScheduler

**动态梯度累积**：

```python
from lightning.pytorch.callbacks import GradientAccumulationScheduler

# 随着训练进展累积更多梯度
accumulator = GradientAccumulationScheduler(
    scheduling={
        0: 8,   # Epoch 0-4：累积8个batch
        5: 4,   # Epoch 5-9：累积4个batch
        10: 2   # Epoch 10+：累积2个batch
    }
)

trainer = L.Trainer(callbacks=[accumulator])
```

### 6. StochasticWeightAveraging（SWA）

**平均权重以获得更好的泛化**：

```python
from lightning.pytorch.callbacks import StochasticWeightAveraging

swa = StochasticWeightAveraging(
    swa_lrs=1e-2,  # SWA学习率
    swa_epoch_start=0.8,  # 在训练80%时开始
    annealing_epochs=10,  # 退火周期
    annealing_strategy='cos'  # 'cos'或'linear'
)

trainer = L.Trainer(callbacks=[swa])
```

## 自定义回调

### 基础自定义回调

```python
from lightning.pytorch.callbacks import Callback

class PrintingCallback(Callback):
    def on_train_start(self, trainer, pl_module):
        print("训练开始了！")

    def on_train_end(self, trainer, pl_module):
        print("训练完成了！")

    def on_epoch_end(self, trainer, pl_module):
        print(f"Epoch {trainer.current_epoch}结束")

# 使用
trainer = L.Trainer(callbacks=[PrintingCallback()])
```

### 高级自定义回调

```python
class MetricsCallback(Callback):
    """每N个batch记录一次自定义指标。"""

    def __init__(self, log_every_n_batches=100):
        self.log_every_n_batches = log_every_n_batches
        self.metrics = []

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        if batch_idx % self.log_every_n_batches == 0:
            # 计算自定义指标
            metric = self.compute_metric(outputs)
            self.metrics.append(metric)

            # 记录到Lightning
            pl_module.log('custom_metric', metric)

    def compute_metric(self, outputs):
        # 你的自定义逻辑
        return outputs['loss'].item()

    def state_dict(self):
        """在检查点中保存回调状态。"""
        return {'metrics': self.metrics}

    def load_state_dict(self, state_dict):
        """从检查点恢复回调状态。"""
        self.metrics = state_dict['metrics']
```

### 梯度监控回调

```python
class GradientMonitorCallback(Callback):
    """监控梯度范数。"""

    def on_after_backward(self, trainer, pl_module):
        # 计算梯度范数
        total_norm = 0.0
        for p in pl_module.parameters():
            if p.grad is not None:
                param_norm = p.grad.data.norm(2)
                total_norm += param_norm.item() ** 2
        total_norm = total_norm ** 0.5

        # 记录
        pl_module.log('grad_norm', total_norm)

        # 爆炸时警告
        if total_norm > 100:
            print(f"警告：大的梯度范数：{total_norm:.2f}")
```

### 模型检查回调

```python
class ModelInspectionCallback(Callback):
    """在训练期间检查模型激活。"""

    def on_train_batch_start(self, trainer, pl_module, batch, batch_idx):
        if batch_idx == 0:  # epoch的第一个batch
            # 注册钩子
            self.activations = {}

            def get_activation(name):
                def hook(model, input, output):
                    self.activations[name] = output.detach()
                return hook

            # 附加到特定层
            pl_module.model.layer1.register_forward_hook(get_activation('layer1'))
            pl_module.model.layer2.register_forward_hook(get_activation('layer2'))

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        if batch_idx == 0:
            # 记录激活统计
            for name, activation in self.activations.items():
                mean = activation.mean().item()
                std = activation.std().item()
                pl_module.log(f'{name}_mean', mean)
                pl_module.log(f'{name}_std', std)
```

## 回调钩子

**所有可用的钩子**：

```python
class MyCallback(Callback):
    # 设置/拆卸
    def setup(self, trainer, pl_module, stage):
        """在fit/test/predict开始时调用。"""
        pass

    def teardown(self, trainer, pl_module, stage):
        """在fit/test/predict结束时调用。"""
        pass

    # 训练
    def on_train_start(self, trainer, pl_module):
        pass

    def on_train_epoch_start(self, trainer, pl_module):
        pass

    def on_train_batch_start(self, trainer, pl_module, batch, batch_idx):
        pass

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        pass

    def on_train_epoch_end(self, trainer, pl_module):
        pass

    def on_train_end(self, trainer, pl_module):
        pass

    # 验证
    def on_validation_start(self, trainer, pl_module):
        pass

    def on_validation_epoch_start(self, trainer, pl_module):
        pass

    def on_validation_batch_start(self, trainer, pl_module, batch, batch_idx, dataloader_idx):
        pass

    def on_validation_batch_end(self, trainer, pl_module, outputs, batch, batch_idx, dataloader_idx):
        pass

    def on_validation_epoch_end(self, trainer, pl_module):
        pass

    def on_validation_end(self, trainer, pl_module):
        pass

    # 测试（与验证结构相同）
    def on_test_start(self, trainer, pl_module):
        pass
    # ... (test_epoch_start, test_batch_start等)

    # 预测
    def on_predict_start(self, trainer, pl_module):
        pass
    # ... (predict_epoch_start, predict_batch_start等)

    # 反向传播
    def on_before_backward(self, trainer, pl_module, loss):
        pass

    def on_after_backward(self, trainer, pl_module):
        pass

    # 优化器
    def on_before_optimizer_step(self, trainer, pl_module, optimizer):
        pass

    # 检查点
    def on_save_checkpoint(self, trainer, pl_module, checkpoint):
        """向检查点添加数据。"""
        pass

    def on_load_checkpoint(self, trainer, pl_module, checkpoint):
        """从检查点恢复数据。"""
        pass
```

## 组合多个回调

```python
from lightning.pytorch.callbacks import ModelCheckpoint, EarlyStopping, LearningRateMonitor

# 创建所有回调
checkpoint = ModelCheckpoint(monitor='val_loss', mode='min', save_top_k=3)
early_stop = EarlyStopping(monitor='val_loss', patience=5)
lr_monitor = LearningRateMonitor(logging_interval='epoch')
custom_callback = MyCustomCallback()

# 将所有回调添加到Trainer
trainer = L.Trainer(
    callbacks=[checkpoint, early_stop, lr_monitor, custom_callback]
)

trainer.fit(model, train_loader, val_loader)
```

**执行顺序**：回调按添加顺序执行

## 最佳实践

### 1. 保持回调独立

**不好**（依赖于其他回调）：
```python
class BadCallback(Callback):
    def on_train_end(self, trainer, pl_module):
        # 假设ModelCheckpoint存在
        best_path = trainer.checkpoint_callback.best_model_path  # 脆弱！
```

**好**（自包含）：
```python
class GoodCallback(Callback):
    def on_train_end(self, trainer, pl_module):
        # 如果存在则找到检查点回调
        for callback in trainer.callbacks:
            if isinstance(callback, ModelCheckpoint):
                best_path = callback.best_model_path
                break
```

### 2. 使用状态字典进行持久化

```python
class StatefulCallback(Callback):
    def __init__(self):
        self.counter = 0
        self.history = []

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        self.counter += 1
        self.history.append(outputs['loss'].item())

    def state_dict(self):
        """保存状态。"""
        return {
            'counter': self.counter,
            'history': self.history
        }

    def load_state_dict(self, state_dict):
        """恢复状态。"""
        self.counter = state_dict['counter']
        self.history = state_dict['history']
```

### 3. 处理分布式训练

```python
class DistributedCallback(Callback):
    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        # 仅在主进程上运行
        if trainer.is_global_zero:
            print("在分布式训练中这只会打印一次")

        # 在所有进程上运行
        loss = outputs['loss']
        # ... 在每个GPU上处理loss
```

## 资源

- 回调API：https://lightning.ai/docs/pytorch/stable/extensions/callbacks.html
- 内置回调：https://lightning.ai/docs/pytorch/stable/api_references.html#callbacks
- 示例：https://github.com/Lightning-AI/pytorch-lightning/tree/master/examples/callbacks
