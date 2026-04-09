---
name: meme-generation
description: 通过选择模板并使用 Pillow 叠加文本来生成真实的 meme 图片。生成实际的 .png meme 文件。
version: 2.0.0
author: adanaleycio
license: MIT
metadata:
  kclaw:
    tags: [创意, meme, 幽默, 图片]
    related_skills: [ascii-art, generative-widgets]
    category: creative
---

# Meme 生成

从某个主题生成真实的 meme 图片。选择模板、撰写字幕，并使用文本叠加渲染真实的 .png 文件。

## 何时使用

- 用户要求您制作或生成 meme
- 用户想要关于特定主题、情况或挫折的 meme
- 用户说"meme 一下这个"或类似的话

## 可用模板

脚本支持**约 100 个流行 imgflip 模板**中的任何一个（按名称或 ID），以及 10 个精心调整文本位置的精选模板。

### 精选模板（自定义文本位置）

| ID | 名称 | 字段 | 最适合 |
|----|------|--------|----------|
| `this-is-fine` | This is Fine | top, bottom | 混乱、否认 |
| `drake` | Drake Hotline Bling | reject, approve | 拒绝/偏好 |
| `distracted-boyfriend` | Distracted Boyfriend | distraction, current, person | 诱惑、转移优先级 |
| `two-buttons` | Two Buttons | left, right, person | 艰难选择 |
| `expanding-brain` | Expanding Brain | 4 levels | 升级讽刺 |
| `change-my-mind` | Change My Mind | statement | hot takes |
| `woman-yelling-at-cat` | Woman Yelling at Cat | woman, cat | 争论 |
| `one-does-not-simply` | One Does Not Simply | top, bottom | 看似容易实则难 |
| `grus-plan` | Gru's Plan | step1-3, realization | 会适得其反的计划 |
| `batman-slapping-robin` | Batman Slapping Robin | robin, batman | 否定坏主意 |

### 动态模板（来自 imgflip API）

任何不在精选列表中的模板都可以通过名称或 imgflip ID 使用。这些使用智能默认文本位置（2 字段的 top/bottom，3 个以上的均匀分布）。使用以下方式搜索：
```bash
python "$SKILL_DIR/scripts/generate_meme.py" --search "disaster"
```

## 程序

### 模式 1：经典模板（默认）

1. 阅读用户的主题并识别核心动态（混乱、困境、偏好、讽刺等）
2. 选择最匹配的模板。使用"最适合"列，或使用 `--search` 搜索。
3. 为每个字段撰写简短字幕（每个字段最多 8-12 个词，越短越好）。
4. 找到技能的脚本目录：
   ```
   SKILL_DIR=$(dirname "$(find ~/.kclaw/skills -path '*/meme-generation/SKILL.md' 2>/dev/null | head -1)")
   ```
5. 运行生成器：
   ```bash
   python "$SKILL_DIR/scripts/generate_meme.py" <template_id> /tmp/meme.png "caption 1" "caption 2" ...
   ```
6. 使用 `MEDIA:/tmp/meme.png` 返回图片

### 模式 2：自定义 AI 图片（当 image_generate 可用时）

当没有经典模板适合时使用，或当用户想要原创的东西时。

1. 先撰写字幕。
2. 使用 `image_generate` 创建与 meme 概念匹配的场景。不要在图片提示中包含任何文本 — 文本将由脚本添加。仅描述视觉场景。
3. 从 image_generate 结果 URL 获取生成的图片路径。如果需要，下载到本地路径。
4. 使用 `--image` 运行脚本叠加文本，选择模式：
   - **Overlay**（文本直接在图片上，白色带黑色轮廓）：
     ```bash
     python "$SKILL_DIR/scripts/generate_meme.py" --image /path/to/scene.png /tmp/meme.png "top text" "bottom text"
     ```
   - **Bars**（黑色条在上方/下方，白色文本 — 更干净，始终可读）：
     ```bash
     python "$SKILL_DIR/scripts/generate_meme.py" --image /path/to/scene.png --bars /tmp/meme.png "top text" "bottom text"
     ```
   当图片繁忙/复杂且文本难以阅读时使用 `--bars`。
5. **用 vision 验证**（如果 `vision_analyze` 可用）：检查结果看起来不错：
   ```
   vision_analyze(image_url="/tmp/meme.png", question="Is the text legible and well-positioned? Does the meme work visually?")
   ```
   如果 vision 模型标记问题（文本难以阅读、位置不佳等），尝试其他模式（在 overlay 和 bars 之间切换）或重新生成场景。
6. 使用 `MEDIA:/tmp/meme.png` 返回图片

## 示例

**"凌晨 2 点调试生产"：**
```bash
python generate_meme.py this-is-fine /tmp/meme.png "SERVERS ARE ON FIRE" "This is fine"
```

**"在睡眠和一集之间选择"：**
```bash
python generate_meme.py drake /tmp/meme.png "Getting 8 hours of sleep" "One more episode at 3 AM"
```

**"周一早上的阶段"：**
```bash
python generate_meme.py expanding-brain /tmp/meme.png "Setting an alarm" "Setting 5 alarms" "Sleeping through all alarms" "Working from bed"
```

## 列出模板

查看所有可用模板：
```bash
python generate_meme.py --list
```

## 陷阱

- 保持字幕**简短**。长文本的 meme 看起来很糟糕。
- 将文本参数数量与模板的字段数量匹配。
- 选择适合笑话结构的模板，而不仅仅是主题。
- 不要生成仇恨、虐待或针对个人的内容。
- 脚本在首次下载后将模板图片缓存在 `scripts/.cache/` 中。

## 验证

输出正确的条件：
- 在输出路径创建了 .png 文件
- 文本在模板上清晰可读（白色带黑色轮廓）
- 笑话成功 — 字幕与模板的预期结构匹配
- 文件可以通过 MEDIA: 路径传递
