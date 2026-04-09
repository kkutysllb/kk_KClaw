# 向TorchTitan添加自定义模型

本指南解释如何按照既定模式向TorchTitan添加新模型。

## 目录结构

```
torchtitan/models/your_model/
├── model/
│   ├── __init__.py
│   ├── args.py          # 模型参数
│   ├── model.py         # 模型定义
│   └── state_dict_adapter.py  # HF转换（可选）
├── infra/
│   ├── __init__.py
│   ├── parallelize.py   # TP、FSDP、compile应用
│   └── pipeline.py      # PP应用（可选）
├── train_configs/
│   ├── debug_model.toml
│   └── your_model_XB.toml
├── __init__.py          # TrainSpec注册
└── README.md
```

## 步骤1：定义模型参数

继承自`BaseModelArgs`：

```python
# model/args.py
from torchtitan.protocols.model import BaseModelArgs
from dataclasses import dataclass

@dataclass
class YourModelArgs(BaseModelArgs):
    dim: int = 4096
    n_layers: int = 32
    n_heads: int = 32
    vocab_size: int = 128256

    def get_nparams_and_flops(self, seq_len: int) -> tuple[int, int]:
        """返回（参数数量，每词元FLOPs）用于吞吐量计算。"""
        nparams = self.vocab_size * self.dim + ...  # 计算参数
        flops = 6 * nparams  # 近似：forward+backward的6 * params
        return nparams, flops

    def update_from_config(self, job_config) -> "YourModelArgs":
        """从训练配置更新参数。"""
        # 如需要从job_config覆盖特定参数
        return self
```

## 步骤2：定义模型

继承自`ModelProtocol`：

```python
# model/model.py
import torch.nn as nn
from torchtitan.protocols.model import ModelProtocol
from .args import YourModelArgs

class YourModel(ModelProtocol):
    def __init__(self, args: YourModelArgs):
        super().__init__()
        self.args = args
        self.tok_embeddings = nn.Embedding(args.vocab_size, args.dim)
        self.layers = nn.ModuleDict({
            str(i): TransformerBlock(args) for i in range(args.n_layers)
        })
        self.norm = RMSNorm(args.dim)
        self.output = nn.Linear(args.dim, args.vocab_size, bias=False)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        h = self.tok_embeddings(tokens)
        for layer in self.layers.values():
            h = layer(h)
        h = self.norm(h)
        return self.output(h)

    def init_weights(self):
        """递归初始化权重。"""
        for module in self.modules():
            if hasattr(module, 'init_weights') and module is not self:
                module.init_weights()
            elif isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, std=0.02)
```

**重要指南**：
- 编写单设备模型代码（并行性外部应用）
- 对层使用`nn.ModuleDict`（删除用于PP时保留FQN）
- 使输入/输出层可选以兼容PP
- 递归定义`init_weights()`

## 步骤3：并行化函数

```python
# infra/parallelize.py
from torch.distributed._composable.fsdp import fully_shard
from torch.distributed.tensor.parallel import parallelize_module

def parallelize_your_model(
    model: YourModel,
    world_mesh: DeviceMesh,
    parallel_dims: ParallelDims,
    job_config: JobConfig,
):
    # 按此顺序应用：TP -> AC -> compile -> FSDP

    # 1. 张量并行
    if parallel_dims.tp_enabled:
        apply_tp(model, world_mesh["tp"], job_config)

    # 2. 激活检查点
    if job_config.activation_checkpoint.mode == "full":
        apply_ac(model, job_config)

    # 3. torch.compile
    if job_config.compile.enable:
        model = torch.compile(model)

    # 4. FSDP
    if parallel_dims.dp_enabled:
        apply_fsdp(model, world_mesh["dp"], job_config)

    return model
```

## 步骤4：创建TrainSpec

```python
# __init__.py
from torchtitan.protocols.train_spec import TrainSpec, register_train_spec
from .model.model import YourModel
from .model.args import YourModelArgs
from .infra.parallelize import parallelize_your_model

MODEL_CONFIGS = {
    "8B": YourModelArgs(dim=4096, n_layers=32, n_heads=32),
    "70B": YourModelArgs(dim=8192, n_layers=80, n_heads=64),
}

def get_train_spec(flavor: str) -> TrainSpec:
    return TrainSpec(
        model_cls=YourModel,
        model_args=MODEL_CONFIGS[flavor],
        parallelize_fn=parallelize_your_model,
        pipeline_fn=None,  # 或用于PP的your_pipeline_fn
        build_optimizer_fn=build_optimizer,  # 重用现有
        build_lr_scheduler_fn=build_lr_scheduler,  # 重用现有
        build_dataloader_fn=build_dataloader,  # 重用现有
        build_tokenizer_fn=build_tokenizer,  # 重用现有
        build_loss_fn=build_loss,  # 重用现有
        state_dict_adapter=None,  # 或YourStateDictAdapter
    )

# 注册以便train.py可以找到
register_train_spec("your_model", get_train_spec)
```

## 步骤5：状态字典适配器（可选）

用于HuggingFace检查点转换：

```python
# model/state_dict_adapter.py
from torchtitan.protocols.state_dict_adapter import BaseStateDictAdapter

class YourStateDictAdapter(BaseStateDictAdapter):
    def to_hf(self, state_dict: dict) -> dict:
        """将torchtitan状态字典转换为HF格式。"""
        hf_state_dict = {}
        for key, value in state_dict.items():
            hf_key = self._convert_key_to_hf(key)
            hf_state_dict[hf_key] = value
        return hf_state_dict

    def from_hf(self, state_dict: dict) -> dict:
        """将HF状态字典转换为torchtitan格式。"""
        tt_state_dict = {}
        for key, value in state_dict.items():
            tt_key = self._convert_key_from_hf(key)
            tt_state_dict[tt_key] = value
        return tt_state_dict
```

## 步骤6：训练配置

```toml
# train_configs/your_model_8b.toml
[job]
dump_folder = "./outputs"
description = "Your Model 8B训练"

[model]
name = "your_model"
flavor = "8B"

[optimizer]
name = "AdamW"
lr = 3e-4

[training]
local_batch_size = 2
seq_len = 8192
steps = 1000
dataset = "c4"

[parallelism]
data_parallel_shard_degree = -1
tensor_parallel_degree = 1
```

## 步骤7：注册模型

添加到`torchtitan/models/__init__.py`：

```python
from .your_model import get_train_spec as get_your_model_train_spec

MODEL_REGISTRY["your_model"] = get_your_model_train_spec
```

## 测试

### 数值测试

与HuggingFace实现比较输出：

```python
def test_numerics():
    # 将相同检查点加载到两个实现
    tt_model = YourModel(args).load_checkpoint(...)
    hf_model = HFYourModel.from_pretrained(...)

    # 比较输出
    input_ids = torch.randint(0, vocab_size, (1, 128))
    tt_output = tt_model(input_ids)
    hf_output = hf_model(input_ids).logits

    torch.testing.assert_close(tt_output, hf_output, atol=1e-4, rtol=1e-4)
```

### 损失收敛

与经验证的基线比较损失曲线（参见`docs/converging.md`）。

### 性能基准

将基准配置添加到`benchmarks/`文件夹。

## 指导原则

1. **可读性优先于灵活性**：不要过度抽象
2. **最小的模型更改**：并行性外部应用
3. **简洁的代码库**：尽可能重用现有组件
4. **单设备语义**：模型代码应在单个GPU上工作
