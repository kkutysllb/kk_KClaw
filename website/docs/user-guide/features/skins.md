---
sidebar_position: 10
title: "皮肤与主题"
description: "使用内置和用户定义的皮肤自定义 KClaw CLI"
---

# 皮肤与主题

皮肤控制 KClaw CLI 的**视觉呈现**：横幅颜色、微调器面孔和动词、响应框标签、品牌文字和工具活动前缀。

对话风格和视觉风格是分开的概念：

- **人格**改变代理的语气和措辞。
- **皮肤**改变 CLI 的外观。

## 更改皮肤

```bash
/skin                # 显示当前皮肤并列出可用皮肤
/skin ares           # 切换到内置皮肤
/skin mytheme        # 切换到 ~/.kclaw/skins/mytheme.yaml 的自定义皮肤
```

或在 `~/.kclaw/config.yaml` 中设置默认皮肤：

```yaml
display:
  skin: default
```

## 内置皮肤

| 皮肤 | 描述 | 代理品牌 | 视觉特征 |
|------|-------------|----------------|------------------|
| `default` | 经典 KClaw — 金色和 kawaii | `KClaw Agent` | 暖金色边框，玉米丝色文本，kawaii 面孔微调器。熟悉的杖杖横幅。简洁而吸引人。 |
| `ares` | 战神主题 — 深红和青铜 | `Ares Agent` | 深红边框配青铜色强调。激进的微调器动词（"forging"、"marching"、"tempering steel"）。自定义剑盾 ASCII 艺术横幅。 |
| `mono` | 单色 — 干净灰阶 | `KClaw Agent` | 全灰——无颜色。边框是 `#555555`，文本是 `#c9d1d9`。适合最小化终端设置或屏幕录制。 |
| `slate` | 冷蓝 — 开发者导向 | `KClaw Agent` | 皇家蓝边框（`#4169e1`），柔和蓝色文本。冷静专业。没有自定义微调器——使用默认面孔。 |
| `poseidon` | 海神主题 — 深蓝和海沫 | `Poseidon Agent` | 深蓝到海沫渐变。海洋主题微调器（"charting currents"、"sounding the depth"）。三叉戟 ASCII 艺术横幅。 |
| `sisyphus` | 西西弗斯主题 — 严格灰阶与坚持 | `Sisyphus Agent` | 浅灰与强烈对比。巨石主题微调器（"pushing uphill"、"resetting the boulder"、"enduring the loop"）。巨石和山丘 ASCII 艺术横幅。 |
| `charizard` | 火山主题 — 焦橙和余烬 | `Charizard Agent` | 暖焦橙到余烬渐变。火主题微调器（"banking into the draft"、"measuring burn"）。龙剪影 ASCII 艺术横幅。 |

## 可配置键的完整列表

### 颜色（`colors:`）

控制 CLI 中所有颜色值。值是十六进制颜色字符串。

| 键 | 描述 | 默认（`default` 皮肤） |
|-----|-------------|--------------------------|
| `banner_border` | 启动横幅周围的面板边框 | `#CD7F32`（青铜） |
| `banner_title` | 横幅中的标题文本颜色 | `#FFD700`（金色） |
| `banner_accent` | 横幅中的部分标题（Available Tools 等） | `#FFBF00`（琥珀色） |
| `banner_dim` | 横幅中柔和文本（分隔符、次要标签） | `#B8860B`（暗金合欢） |
| `banner_text` | 横幅中的正文文本（工具名称、技能名称） | `#FFF8DC`（玉米丝色） |
| `ui_accent` | 通用 UI 强调色（高亮、活跃元素） | `#FFBF00` |
| `ui_label` | UI 标签和标签 | `#4dd0e1`（青色） |
| `ui_ok` | 成功指示器（勾选、完成） | `#4caf50`（绿色） |
| `ui_error` | 错误指示器（失败、阻止） | `#ef5350`（红色） |
| `ui_warn` | 警告指示器（谨慎、批准提示） | `#ffa726`（橙色） |
| `prompt` | 交互式提示文本颜色 | `#FFF8DC` |
| `input_rule` | 输入区域上方的水平线 | `#CD7F32` |
| `response_border` | 代理响应框边框（ANSI 转义） | `#FFD700` |
| `session_label` | 会话标签颜色 | `#DAA520` |
| `session_border` | 会话 ID 暗淡边框颜色 | `#8B8682` |

### 微调器（`spinner:`）

控制等待 API 响应时显示的动画微调器。

| 键 | 类型 | 描述 | 示例 |
|-----|------|-------------|---------|
| `waiting_faces` | 字符串列表 | 等待 API 响应时循环的面孔 | `["(⚔)", "(⛨)", "(▲)"]` |
| `thinking_faces` | 字符串列表 | 模型推理期间循环的面孔 | `["(⚔)", "(⌁)", "(<>)"]` |
| `thinking_verbs` | 字符串列表 | 微调器消息中显示的动词 | `["forging", "plotting", "hammering plans"]` |
| `wings` | [左，右] 对列表 | 微调器周围的装饰括号 | `[["⟪⚔", "⚔⟫"], ["⟪▲", "▲⟫"]]` |

当微调器值为空时（如 `default` 和 `mono`），使用 `display.py` 中的硬编码默认值。

### 品牌（`branding:`）

整个 CLI 界面使用的文本字符串。

| 键 | 描述 | 默认 |
|-----|-------------|---------|
| `agent_name` | 横幅标题和状态显示中显示的名称 | `KClaw Agent` |
| `welcome` | CLI 启动时显示的欢迎消息 | `Welcome to KClaw Agent! Type your message or /help for commands.` |
| `goodbye` | 退出时显示的消息 | `Goodbye! ⚕` |
| `response_label` | 响应框标题上的标签 | ` ⚕ KClaw ` |
| `prompt_symbol` | 用户输入提示前的符号 | `❯ ` |
| `help_header` | `/help` 命令输出的标题文本 | `(^_^)? Available Commands` |

### 其他顶级键

| 键 | 类型 | 描述 | 默认 |
|-----|------|-------------|---------|
| `tool_prefix` | 字符串 | CLI 中工具输出行前缀的字符 | `┊` |
| `tool_emojis` | dict | 微调器和进度的每工具表情符号覆盖（`{tool_name: emoji}`） | `{}` |
| `banner_logo` | 字符串 | 富标记 ASCII 艺术 logo（替换默认 KCLAW_AGENT 横幅） | `""` |
| `banner_hero` | 字符串 | 富标记英雄艺术（替换默认杖杖艺术） | `""` |

## 自定义皮肤

在 `~/.kclaw/skins/` 下创建 YAML 文件。用户皮肤继承内置 `default` 皮肤的缺失值，因此您只需要指定要更改的键。

### 完整自定义皮肤 YAML 模板

```yaml
# ~/.kclaw/skins/mytheme.yaml
# 完整皮肤模板——显示所有键。删除您不需要的；
# 缺失值自动从 'default' 皮肤继承。

name: mytheme
description: My custom theme

colors:
  banner_border: "#CD7F32"
  banner_title: "#FFD700"
  banner_accent: "#FFBF00"
  banner_dim: "#B8860B"
  banner_text: "#FFF8DC"
  ui_accent: "#FFBF00"
  ui_label: "#4dd0e1"
  ui_ok: "#4caf50"
  ui_error: "#ef5350"
  ui_warn: "#ffa726"
  prompt: "#FFF8DC"
  input_rule: "#CD7F32"
  response_border: "#FFD700"
  session_label: "#DAA520"
  session_border: "#8B8682"

spinner:
  waiting_faces:
    - "(⚔)"
    - "(⛨)"
    - "(▲)"
  thinking_faces:
    - "(⚔)"
    - "(⌁)"
    - "(<>)"
  thinking_verbs:
    - "processing"
    - "analyzing"
    - "computing"
    - "evaluating"
  wings:
    - ["⟪⚡", "⚡⟫"]
    - ["⟪●", "●⟫"]

branding:
  agent_name: "My Agent"
  welcome: "Welcome to My Agent! Type your message or /help for commands."
  goodbye: "See you later! ⚡"
  response_label: " ⚡ My Agent "
  prompt_symbol: "⚡ ❯ "
  help_header: "(⚡) Available Commands"

tool_prefix: "┊"

# 每工具表情符号覆盖（可选）
tool_emojis:
  terminal: "⚔"
  web_search: "🔮"
  read_file: "📄"

# 自定义 ASCII 艺术横幅（可选，支持 Rich 标记）
# banner_logo: |
#   [bold #FFD700] MY AGENT [/]
# banner_hero: |
#   [#FFD700]  Custom art here  [/]
```

### 最小自定义皮肤示例

由于一切从 `default` 继承，最小皮肤只需要更改不同的内容：

```yaml
name: cyberpunk
description: Neon terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

## KClaw Mod — 视觉皮肤编辑器

[KClaw Mod](https://github.com/cocktailpeanut/kclaw-mod) 是一个社区构建的 Web UI，用于可视地创建和管理皮肤。无需手动编写 YAML，您可以使用实时预览的点选编辑器。

![KClaw Mod 皮肤编辑器](https://raw.githubusercontent.com/cocktailpeanut/kclaw-mod/master/nous.png)

**功能：**

- 列出所有内置和自定义皮肤
- 将任何皮肤打开为可视化编辑器，包含所有 KClaw 皮肤字段（颜色、微调器、品牌、工具前缀、工具表情符号）
- 从文本提示生成 `banner_logo` 文字艺术
- 将上传的图像（PNG、JPG、GIF、WEBP）转换为多种渲染样式（盲文、ASCII 坡道、块、点）的 `banner_hero` ASCII 艺术
- 直接保存到 `~/.kclaw/skins/`
- 通过更新 `~/.kclaw/config.yaml` 激活皮肤
- 显示生成的 YAML 和实时预览

### 安装

**选项 1 — Pinokio（1-click）：**

在 [pinokio.computer](https://pinokio.computer) 上找到它并一键安装。

**选项 2 — npx（从终端最快）：**

```bash
npx -y kclaw-mod
```

**选项 3 — 手动：**

```bash
git clone https://github.com/cocktailpeanut/kclaw-mod.git
cd kclaw-mod/app
npm install
npm start
```

### 用法

1. 启动应用（通过 Pinokio 或终端）。
2. 打开 **Skin Studio**。
3. 选择要编辑的内置或自定义皮肤。
4. 从文本生成 logo 和/或上传图像作为英雄艺术。选择渲染样式和宽度。
5. 编辑颜色、微调器、品牌和其他字段。
6. 点击 **Save** 将皮肤 YAML 写入 `~/.kclaw/skins/`。
7. 点击 **Activate** 将其设为当前皮肤（更新 `config.yaml` 中的 `display.skin`）。

KClaw Mod 尊重 `KCLAW_HOME` 环境变量，因此它也可以与[配置文件](/docs/user-guide/profiles)配合使用。

## 操作说明

- 内置皮肤从 `kclaw_cli/skin_engine.py` 加载。
- 未知皮肤自动回退到 `default`。
- `/skin` 立即为当前会话更新活动 CLI 主题。
- `~/.kclaw/skins/` 中的用户皮肤优先于同名的内置皮肤。
- 通过 `/skin` 的皮肤更改仅限会话。要使皮肤成为永久默认，请在 `config.yaml` 中设置。
- `banner_logo` 和 `banner_hero` 字段支持 Rich 控制台标记（例如 `[bold #FF0000]text[/]`）用于彩色 ASCII 艺术。
