# 发现应用

## 列出所有应用

```bash
infsh app list
```

## 分页

```bash
infsh app list --page 2
```

## 按类别筛选

```bash
infsh app list --category image
infsh app list --category video
infsh app list --category audio
infsh app list --category text
infsh app list --category other
```

## 搜索

```bash
infsh app search "flux"
infsh app search "video generation"
infsh app search "tts" -l
infsh app search "image" --category image
```

或使用标志形式：

```bash
infsh app list --search "flux"
infsh app list --search "video generation"
infsh app list --search "tts"
```

## 精选应用

```bash
infsh app list --featured
```

## 最新优先

```bash
infsh app list --new
```

## 详细视图

```bash
infsh app list -l
```

显示包含应用名称、类别、描述和精选状态的表格。

## 保存到文件

```bash
infsh app list --save apps.json
```

## 您的应用

列出您部署的应用：

```bash
infsh app my
infsh app my -l  # 详细
```

## 获取应用详情

```bash
infsh app get falai/flux-dev-lora
infsh app get falai/flux-dev-lora --json
```

显示完整应用信息，包括输入/输出模式。

## 按类别的热门应用

### 图像生成
- `falai/flux-dev-lora` - FLUX.2 Dev（高质量）
- `falai/flux-2-klein-lora` - FLUX.2 Klein（最快）
- `infsh/sdxl` - Stable Diffusion XL
- `google/gemini-3-pro-image-preview` - Gemini 3 Pro
- `xai/grok-imagine-image` - Grok 图像生成

### 视频生成
- `google/veo-3-1-fast` - Veo 3.1 Fast
- `google/veo-3` - Veo 3
- `bytedance/seedance-1-5-pro` - Seedance 1.5 Pro
- `infsh/ltx-video-2` - LTX Video 2（带音频）
- `bytedance/omnihuman-1-5` - OmniHuman 头像

### 音频
- `infsh/dia-tts` - 对话式 TTS
- `infsh/kokoro-tts` - Kokoro TTS
- `infsh/fast-whisper-large-v3` - 快速转录
- `infsh/diffrythm` - 音乐生成

## 文档

- [浏览网格](https://inference.sh/docs/apps/browsing-grid) - 可视化应用浏览
- [应用概览](https://inference.sh/docs/apps/overview) - 理解应用
- [运行应用](https://inference.sh/docs/apps/running) - 如何运行应用
