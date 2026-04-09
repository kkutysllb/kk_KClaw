# TorchTitan中的检查点

TorchTitan使用PyTorch分布式检查点（DCP）进行容错、可互操作的检查点保存。

## 基本配置

```toml
[checkpoint]
enable = true
folder = "checkpoint"
interval = 500
```

## 仅保存模型（更小检查点）

排除优化器状态和训练元数据：

```toml
[checkpoint]
enable = true
last_save_model_only = true
export_dtype = "bfloat16"  # 可选：以更低精度导出
```

## 从加载中排除键

为修改的设置部分加载检查点：

```toml
[checkpoint]
enable = true
exclude_from_loading = ["data_loader", "lr_scheduler"]
```

CLI等价物：
```bash
--checkpoint.exclude_from_loading data_loader,lr_scheduler
```

## 创建种子检查点

流水线并行必需，以确保一致的初始化：

```bash
NGPU=1 CONFIG_FILE=<path_to_config> ./run_train.sh \
  --checkpoint.enable \
  --checkpoint.create_seed_checkpoint \
  --parallelism.data_parallel_replicate_degree 1 \
  --parallelism.data_parallel_shard_degree 1 \
  --parallelism.tensor_parallel_degree 1 \
  --parallelism.pipeline_parallel_degree 1 \
  --parallelism.context_parallel_degree 1 \
  --parallelism.expert_parallel_degree 1
```

这在单个CPU上初始化，以实现跨任何GPU数量的可重现初始化。

## 异步检查点

使用异步写入减少检查点开销：

```toml
[checkpoint]
enable = true
async_mode = "async"  # 选项："disabled", "async", "async_with_pinned_mem"
```

## HuggingFace转换

### 训练期间

直接以HuggingFace格式保存：

```toml
[checkpoint]
last_save_in_hf = true
last_save_model_only = true
```

从HuggingFace加载：

```toml
[checkpoint]
initial_load_in_hf = true

[model]
hf_assets_path = "./path/to/hf/checkpoint"
```

### 离线转换

在不运行训练的情况下转换：

```bash
# HuggingFace -> TorchTitan
python ./scripts/checkpoint_conversion/convert_from_hf.py \
  <input_dir> <output_dir> \
  --model_name llama3 \
  --model_flavor 8B

# TorchTitan -> HuggingFace
python ./scripts/checkpoint_conversion/convert_to_hf.py \
  <input_dir> <output_dir> \
  --hf_assets_path ./assets/hf/Llama3.1-8B \
  --model_name llama3 \
  --model_flavor 8B
```

### 示例

```bash
python ./scripts/convert_from_hf.py \
  ~/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B/snapshots/8cde5ca8380496c9a6cc7ef3a8b46a0372a1d920/ \
  ./initial_load_path/ \
  --model_name llama3 \
  --model_flavor 8B
```

## 转换为单个.pt文件

将DCP分片检查点转换为单个PyTorch文件：

```bash
python -m torch.distributed.checkpoint.format_utils \
  dcp_to_torch \
  torchtitan/outputs/checkpoint/step-1000 \
  checkpoint.pt
```

## 检查点结构

DCP保存可以重新分片为不同并行配置的分片检查点：

```
checkpoint/
├── step-500/
│   ├── .metadata
│   ├── __0_0.distcp
│   ├── __0_1.distcp
│   └── ...
└── step-1000/
    └── ...
```

## 恢复训练

训练自动从配置文件夹中的最新检查点恢复。要从特定步骤恢复：

```toml
[checkpoint]
load_step = 500  # 从步骤500恢复
```

## 与TorchTune的互操作性

使用`last_save_model_only = true`保存的检查点可以直接加载到[torchtune](https://github.com/pytorch/torchtune)进行微调。

## 完整配置示例

```toml
[checkpoint]
enable = true
folder = "checkpoint"
interval = 500
load_step = -1  # -1 = 最新，或指定步骤号
last_save_model_only = true
export_dtype = "bfloat16"
async_mode = "async"
exclude_from_loading = []
last_save_in_hf = false
initial_load_in_hf = false
create_seed_checkpoint = false
```

## 最佳实践

1. **大型模型**：使用`async_mode = "async"`以重叠检查点保存和训练
2. **微调导出**：启用`last_save_model_only`和`export_dtype = "bfloat16"`以获得更小文件
3. **流水线并行**：首先始终创建种子检查点
4. **调试**：开发期间保存频繁检查点，生产时减少
5. **HF互操作**：离线转换使用转换脚本，训练工作流使用直接保存/加载
