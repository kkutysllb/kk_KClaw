# TorchTitan中的FSDP2

## 为什么使用FSDP2？

FSDP2是PyTorch完全分片数据并行（FSDP）API的重写，移除了`FlatParameter`抽象以实现更好的可组合性和更简单的实现。

### 相对于FSDP1的关键改进

- **基于DTensor的分片**：分片参数是dim-0上的`DTensor`，实现轻松操作和无通信的分片状态字典
- **更好的内存管理**：通过避免`recordStream`实现确定性和更低的GPU内存（减少7%）
- **简化的API**：更少的参数，无需包装器类

### 性能

在8x H100上的Llama-7B中，FSDP2以比FSDP1低7%的峰值内存实现更高的MFU，匹配相同的损失曲线。

## API参考

```python
from torch.distributed._composable.fsdp import fully_shard, MixedPrecisionPolicy, OffloadPolicy

@contract(state_cls=FSDPState)
def fully_shard(
    module: nn.Module,
    *,
    mesh: Optional[DeviceMesh] = None,
    reshard_after_forward: Union[bool, int] = True,
    mp_policy: MixedPrecisionPolicy = MixedPrecisionPolicy(),
    offload_policy: OffloadPolicy = OffloadPolicy(),
) -> nn.Module:
```

## 分片策略（ZeRO等价物）

| FSDP2配置 | FSDP1等价物 | DeepSpeed |
|---------------------|------------------|-----------|
| 1D mesh + `reshard_after_forward=True` | FULL_SHARD | ZeRO-3 |
| 1D mesh + `reshard_after_forward=False` | SHARD_GRAD_OP | ZeRO-2 |
| 2D mesh + `reshard_after_forward=True` | HYBRID_SHARD | MiCS |
| 1D/2D mesh + `reshard_after_forward=8`（int） | - | ZeRO++ hpZ |

## 元设备初始化

FSDP2支持在分片后将张量具体化到GPU上：

```python
# 在元设备上初始化（无内存）
with torch.device("meta"):
    model = Transformer()

# 应用FSDP2分片
for module in model.modules():
    if isinstance(module, TransformerBlock):
        fully_shard(module)
fully_shard(model)

# 参数仍在元设备上
for tensor in itertools.chain(model.parameters(), model.buffers()):
    assert tensor.device == torch.device("meta")

# 在GPU上分配分片参数
model.to_empty(device="cuda")

# 初始化权重
model.init_weights()
```

## 状态字典差异

| 操作 | FSDP1 | FSDP2 |
|-----------|-------|-------|
| `model.state_dict()` | 完整状态字典 | 分片状态字典（无通信） |
| `optim.state_dict()` | 本地状态字典 | 分片状态字典（无通信） |
| `summon_full_params()` | 支持 | 使用`DTensor` API如`full_tensor()` |
| 梯度裁剪 | `FSDP.clip_grad_norm_()` | `nn.utils.clip_grad_norm_()` |

## 混合精度

```python
from torch.distributed._composable.fsdp import MixedPrecisionPolicy

mp_policy = MixedPrecisionPolicy(
    param_dtype=torch.bfloat16,
    reduce_dtype=torch.float32,
    output_dtype=torch.bfloat16,
    cast_forward_inputs=True,
)

fully_shard(model, mp_policy=mp_policy)
```

## HSDP（混合分片数据并行）

用于具有复制+分片的2D并行：

```python
from torch.distributed.device_mesh import init_device_mesh

# 跨4组复制，每组8个GPU分片
mesh = init_device_mesh("cuda", (4, 8), mesh_dim_names=("replicate", "shard"))

fully_shard(model, mesh=mesh)
```

## TorchTitan中的配置

```toml
[parallelism]
# FSDP分片度（-1 = 自动，使用所有可用GPU）
data_parallel_shard_degree = -1

# HSDP复制度（1 = 纯FSDP，>1 = HSDP）
data_parallel_replicate_degree = 1
```

## 从FSDP1移除的参数

这些FSDP1参数不再需要：

- `auto_wrap_policy`：直接对模块应用`fully_shard`
- `backward_prefetch`：始终使用BACKWARD_PRE
- `param_init_fn`：使用元设备初始化
- `device_id`：自动使用mesh的设备
- `sync_module_states`：DTensor不需要
- `limit_all_gathers`：新内存管理不需要
- `use_orig_params`：始终为true（无FlatParameter）
