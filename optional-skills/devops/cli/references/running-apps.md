# 运行应用

## 基本运行

```bash
infsh app run user/app-name --input input.json
```

## 内联 JSON

```bash
infsh app run falai/flux-dev-lora --input '{"prompt": "a sunset over mountains"}'
```

## 版本固定

```bash
infsh app run user/app-name@1.0.0 --input input.json
```

## 本地文件上传

CLI 在您提供文件路径而不是 URL 时自动上传本地文件。任何接受 URL 的字段也接受本地路径：

```bash
# 放大本地图像
infsh app run falai/topaz-image-upscaler --input '{"image": "/path/to/photo.jpg", "upscale_factor": 2}'

# 从本地文件进行图像到视频
infsh app run falai/wan-2-5-i2v --input '{"image": "./my-image.png", "prompt": "make it move"}'

# 带本地音频和图像的头像
infsh app run bytedance/omnihuman-1-5 --input '{"audio": "/path/to/speech.mp3", "image": "/path/to/face.jpg"}'

# 发布带有本地媒体的推文
infsh app run x/post-create --input '{"text": "Check this out!", "media": "./screenshot.png"}'
```

支持的路径：
- 绝对路径：`/home/user/images/photo.jpg`
- 相对路径：`./image.png`、`../data/video.mp4`
- 主目录：`~/Pictures/photo.jpg`

## 生成示例输入

运行前，生成示例输入文件：

```bash
infsh app sample falai/flux-dev-lora
```

保存到文件：

```bash
infsh app sample falai/flux-dev-lora --save input.json
```

然后编辑 `input.json` 并运行：

```bash
infsh app run falai/flux-dev-lora --input input.json
```

## 工作流示例

### 使用 FLUX 进行图像生成

```bash
# 1. 获取应用详情
infsh app get falai/flux-dev-lora

# 2. 生成示例输入
infsh app sample falai/flux-dev-lora --save input.json

# 3. 编辑 input.json
# {
#   "prompt": "a cat astronaut floating in space",
#   "num_images": 1,
#   "image_size": "landscape_16_9"
# }

# 4. 运行
infsh app run falai/flux-dev-lora --input input.json
```

### 使用 Veo 进行视频生成

```bash
# 1. 生成示例
infsh app sample google/veo-3-1-fast --save input.json

# 2. 编辑提示
# {
#   "prompt": "A drone shot flying over a forest at sunset"
# }

# 3. 运行
infsh app run google/veo-3-1-fast --input input.json
```

### 文本转语音

```bash
# 快速内联运行
infsh app run falai/kokoro-tts --input '{"text": "Hello, this is a test."}'
```

## 任务跟踪

运行应用时，CLI 显示任务 ID：

```
Running falai/flux-dev-lora
Task ID: abc123def456
```

对于长时间运行的任务，您可以随时检查状态：

```bash
# 检查任务状态
infsh task get abc123def456

# 获取 JSON 格式的结果
infsh task get abc123def456 --json

# 保存结果到文件
infsh task get abc123def456 --save result.json
```

### 不等待运行

对于非常长时间的任务，后台运行：

```bash
# 提交并立即返回
infsh app run google/veo-3 --input input.json --no-wait

# 稍后检查
infsh task get <task-id>
```

## 输出

CLI 直接返回应用输出。对于文件输出（图像、视频、音频），您将收到下载 URL。

示例输出：

```json
{
  "images": [
    {
      "url": "https://cloud.inference.sh/...",
      "content_type": "image/png"
    }
  ]
}
```

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| "输入无效" | 模式不匹配 | 检查 `infsh app get` 了解必填字段 |
| "应用未找到" | 错误的应用名称 | 检查 `infsh app list --search` |
| "配额超出" | 信用额度用完 | 检查账户余额 |

## 文档

- [运行应用](https://inference.sh/docs/apps/running) - 完整的运行应用指南
- [流式结果](https://inference.sh/docs/api/sdk/streaming) - 实时进度更新
- [设置参数](https://inference.sh/docs/apps/setup-parameters) - 配置应用输入
