# LLaVA训练指南

训练和微调LLaVA模型的指南。

## 训练阶段

### 阶段1：特征对齐（预训练）

**目的**：将视觉编码器与语言模型对齐

**数据**：558K图像-说明对（CC3M子集）

```bash
# 下载预训练投影器或从头训练
bash scripts/v1_5/pretrain.sh
```

**配置**：
- 基础模型：Vicuna-7B或LLaMA-2-7B
- 视觉编码器：CLIP ViT-L/14
- 训练时间：在8× A100上约20小时

### 阶段2：视觉指令调优

**目的**：教模型遵循视觉指令

**数据**：150K GPT生成的多模态指令数据

```bash
# 使用指令数据微调
bash scripts/v1_5/finetune.sh
```

**配置**：
- 轮次：1
- 批大小：128（跨8个GPU）
- 学习率：2e-5
- 训练时间：在8× A100上约24小时

## 数据格式

### 指令数据格式

```json
[
    {
        "id": "001",
        "image": "path/to/image.jpg",
        "conversations": [
            {
                "from": "human",
                "value": "<image>\n这张图片里有什么？"
            },
            {
                "from": "gpt",
                "value": "图片显示一只狗在公园里玩耍。"
            },
            {
                "from": "human",
                "value": "这只狗是什么品种？"
            },
            {
                "from": "gpt",
                "value": "看起来像是金毛猎犬。"
            }
        ]
    }
]
```

## 在自定义数据上微调

### 准备数据

```python
import json

# 创建指令数据
data = []
for image_path, qa_pairs in your_dataset:
    conversations = []
    for q, a in qa_pairs:
        conversations.append({"from": "human", "value": f"<image>\n{q}"})
        conversations.append({"from": "gpt", "value": a})

    data.append({
        "id": str(len(data)),
        "image": image_path,
        "conversations": conversations
    })

# 保存
with open("custom_data.json", "w") as f:
    json.dump(data, f, indent=2)
```

### 微调脚本

```bash
#!/bin/bash

# 设置路径
DATA_PATH="custom_data.json"
IMAGE_FOLDER="path/to/images"
MODEL_PATH="liuhaotian/llava-v1.5-7b"
OUTPUT_DIR="./checkpoints/llava-custom"

# 微调
deepspeed llava/train/train_mem.py \
    --deepspeed ./scripts/zero2.json \
    --model_name_or_path $MODEL_PATH \
    --version v1 \
    --data_path $DATA_PATH \
    --image_folder $IMAGE_FOLDER \
    --vision_tower openai/clip-vit-large-patch14-336 \
    --mm_projector_type mlp2x_gelu \
    --mm_vision_select_layer -2 \
    --mm_use_im_start_end False \
    --mm_use_im_patch_token False \
    --image_aspect_ratio pad \
    --group_by_modality_length True \
    --bf16 True \
    --output_dir $OUTPUT_DIR \
    --num_train_epochs 1 \
    --per_device_train_batch_size 16 \
    --per_device_eval_batch_size 4 \
    --gradient_accumulation_steps 1 \
    --evaluation_strategy "no" \
    --save_strategy "steps" \
    --save_steps 50000 \
    --save_total_limit 1 \
    --learning_rate 2e-5 \
    --weight_decay 0. \
    --warmup_ratio 0.03 \
    --lr_scheduler_type "cosine" \
    --logging_steps 1 \
    --tf32 True \
    --model_max_length 2048 \
    --gradient_checkpointing True \
    --dataloader_num_workers 4 \
    --lazy_preprocess True \
    --report_to wandb
```

## LoRA微调（内存高效）

```python
from peft import LoraConfig, get_peft_model

# LoRA配置
lora_config = LoraConfig(
    r=8,  # LoRA rank
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)

# 应用LoRA
model = get_peft_model(base_model, lora_config)

# 以低得多的内存训练
```

## 硬件要求

### 全量微调

- **7B模型**：8× A100（40GB）
- **13B模型**：8× A100（80GB）
- **训练时间**：20-48小时

### LoRA微调

- **7B模型**：1× A100（40GB）
- **13B模型**：2× A100（40GB）
- **训练时间**：10-24小时

## 最佳实践

1. **从预训练开始** - 不要从头训练
2. **使用LoRA提高效率** - 减少10倍内存
3. **质量优先于数量** - 1K高质量 > 10K低质量
4. **多轮对话** - 比单一问答更吸引人
5. **多样化图像** - 覆盖不同场景
6. **清晰的指令** - 具体问题获得更好答案
7. **监控损失** - 应该平稳下降
8. **保存检查点** - 训练可能失败
9. **定期测试** - 在保留集上验证
10. **使用DeepSpeed** - 用于多GPU训练

## 资源

- **训练脚本**：https://github.com/haotian-liu/LLaVA/tree/main/scripts
- **数据格式**：https://github.com/haotian-liu/LLaVA/blob/main/docs/Data.md
- **论文**：https://arxiv.org/abs/2304.08485
