---
name: llava
description: 大型语言和视觉助手。实现视觉指令调优和基于图像的对话。结合CLIP视觉编码器与Vicuna/LLaMA语言模型。支持多轮图像聊天、视觉问答和指令跟随。用于视觉语言聊天机器人或图像理解任务。最适合对话式图像分析。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [transformers, torch, pillow]
metadata:
  kclaw:
    tags: [LLaVA, 视觉-语言, 多模态, 视觉问答, 图像聊天, CLIP, Vicuna, 对话AI, 指令调优, VQA]

---

# LLaVA - 大型语言和视觉助手

用于对话式图像理解的开源视觉语言模型。

## 何时使用LLaVA

**在以下情况下使用：**
- 构建视觉语言聊天机器人
- 视觉问答（VQA）
- 图像描述和说明
- 多轮图像对话
- 视觉指令跟随
- 带图像的文档理解

**指标：**
- **23,000+ GitHub stars**
- GPT-4V级别能力（目标）
- Apache 2.0许可证
- 多种模型规模（7B-34B参数）

**使用替代方案：**
- **GPT-4V**：最高质量，基于API
- **CLIP**：简单的零样本分类
- **BLIP-2**：更适合仅说明
- **Flamingo**：研究用，非开源

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/haotian-liu/LLaVA
cd LLaVA

# 安装
pip install -e .
```

### 基础用法

```python
from llava.model.builder import load_pretrained_model
from llava.mm_utils import get_model_name_from_path, process_images, tokenizer_image_token
from llava.constants import IMAGE_TOKEN_INDEX, DEFAULT_IMAGE_TOKEN
from llava.conversation import conv_templates
from PIL import Image
import torch

# 加载模型
model_path = "liuhaotian/llava-v1.5-7b"
tokenizer, model, image_processor, context_len = load_pretrained_model(
    model_path=model_path,
    model_base=None,
    model_name=get_model_name_from_path(model_path)
)

# 加载图像
image = Image.open("image.jpg")
image_tensor = process_images([image], image_processor, model.config)
image_tensor = image_tensor.to(model.device, dtype=torch.float16)

# 创建对话
conv = conv_templates["llava_v1"].copy()
conv.append_message(conv.roles[0], DEFAULT_IMAGE_TOKEN + "\n这张图片里有什么？")
conv.append_message(conv.roles[1], None)
prompt = conv.get_prompt()

# 生成响应
input_ids = tokenizer_image_token(prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors='pt').unsqueeze(0).to(model.device)

with torch.inference_mode():
    output_ids = model.generate(
        input_ids,
        images=image_tensor,
        do_sample=True,
        temperature=0.2,
        max_new_tokens=512
    )

response = tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()
print(response)
```

## 可用模型

| 模型 | 参数 | 显存 | 质量 |
|-------|------------|------|---------|
| LLaVA-v1.5-7B | 7B | ~14 GB | 好 |
| LLaVA-v1.5-13B | 13B | ~28 GB | 更好 |
| LLaVA-v1.6-34B | 34B | ~70 GB | 最好 |

```python
# 加载不同模型
model_7b = "liuhaotian/llava-v1.5-7b"
model_13b = "liuhaotian/llava-v1.5-13b"
model_34b = "liuhaotian/llava-v1.6-34b"

# 4位量化以降低显存
load_4bit = True  # 减少显存约4倍
```

## CLI用法

```bash
# 单图像查询
python -m llava.serve.cli \
    --model-path liuhaotian/llava-v1.5-7b \
    --image-file image.jpg \
    --query "这张图片里有什么？"

# 多轮对话
python -m llava.serve.cli \
    --model-path liuhaotian/llava-v1.5-7b \
    --image-file image.jpg
# 然后交互式输入问题
```

## Web UI (Gradio)

```bash
# 启动Gradio界面
python -m llava.serve.gradio_web_server \
    --model-path liuhaotian/llava-v1.5-7b \
    --load-4bit  # 可选：减少显存

# 访问 http://localhost:7860
```

## 多轮对话

```python
# 初始化对话
conv = conv_templates["llava_v1"].copy()

# 第1轮
conv.append_message(conv.roles[0], DEFAULT_IMAGE_TOKEN + "\n这张图片里有什么？")
conv.append_message(conv.roles[1], None)
response1 = generate(conv, model, image)  # "一只狗在公园里玩耍"

# 第2轮
conv.messages[-1][1] = response1  # 添加之前的响应
conv.append_message(conv.roles[0], "这只狗是什么品种？")
conv.append_message(conv.roles[1], None)
response2 = generate(conv, model, image)  # "金毛猎犬"

# 第3轮
conv.messages[-1][1] = response2
conv.append_message(conv.roles[0], "这是什么时候拍的？")
conv.append_message(conv.roles[1], None)
response3 = generate(conv, model, image)
```

## 常见任务

### 图像说明

```python
question = "详细描述这张图片。"
response = ask(model, image, question)
```

### 视觉问答

```python
question = "图片中有多少人？"
response = ask(model, image, question)
```

### 物体检测（文本）

```python
question = "列出你能看到的所有物体。"
response = ask(model, image, question)
```

### 场景理解

```python
question = "这个场景中发生了什么？"
response = ask(model, image, question)
```

### 文档理解

```python
question = "这篇文档的主题是什么？"
response = ask(model, document_image, question)
```

## 训练自定义模型

```bash
# 阶段1：特征对齐（558K图像-说明对）
bash scripts/v1_5/pretrain.sh

# 阶段2：视觉指令调优（150K指令数据）
bash scripts/v1_5/finetune.sh
```

## 量化（减少显存）

```python
# 4位量化
tokenizer, model, image_processor, context_len = load_pretrained_model(
    model_path="liuhaotian/llava-v1.5-13b",
    model_base=None,
    model_name=get_model_name_from_path("liuhaotian/llava-v1.5-13b"),
    load_4bit=True  # 减少显存约4倍
)

# 8位量化
load_8bit=True  # 减少显存约2倍
```

## 最佳实践

1. **从7B模型开始** - 质量好，显存可控
2. **使用4位量化** - 显著减少显存
3. **需要GPU** - CPU推理极慢
4. **清晰提示** - 具体问题获得更好答案
5. **多轮对话** - 保持对话上下文
6. **温度0.2-0.7** - 平衡创造性和一致性
7. **max_new_tokens 512-1024** - 获得详细响应
8. **批处理** - 顺序处理多个图像

## 性能

| 模型 | 显存（FP16） | 显存（4位） | 速度（词元/秒） |
|-------|-------------|--------------|------------------|
| 7B | ~14 GB | ~4 GB | ~20 |
| 13B | ~28 GB | ~8 GB | ~12 |
| 34B | ~70 GB | ~18 GB | ~5 |

*A100 GPU上

## 基准测试

LLaVA在以下方面取得竞争性分数：
- **VQAv2**：78.5%
- **GQA**：62.0%
- **MM-Vet**：35.4%
- **MMBench**：64.3%

## 局限性

1. **幻觉** - 可能描述图像中没有的东西
2. **空间推理** - 精确位置理解困难
3. **小文本** - 阅读小字困难
4. **物体计数** - 多个物体计数不精确
5. **显存要求** - 需要强大GPU
6. **推理速度** - 比CLIP慢

## 与框架集成

### LangChain

```python
from langchain.llms.base import LLM

class LLaVALLM(LLM):
    def _call(self, prompt, stop=None):
        # 自定义LLaVA推理
        return response

llm = LLaVALLM()
```

### Gradio应用

```python
import gradio as gr

def chat(image, text, history):
    response = ask_llava(model, image, text)
    return response

demo = gr.ChatInterface(
    chat,
    additional_inputs=[gr.Image(type="pil")],
    title="LLaVA Chat"
)
demo.launch()
```

## 资源

- **GitHub**：https://github.com/haotian-liu/LLaVA ⭐ 23,000+
- **论文**：https://arxiv.org/abs/2304.08485
- **演示**：https://llava.hliu.cc
- **模型**：https://huggingface.co/liuhaotian
- **许可证**：Apache 2.0
