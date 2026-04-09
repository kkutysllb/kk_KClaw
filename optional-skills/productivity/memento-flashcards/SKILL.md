---
name: memento-flashcards
description: >-
  间隔重复闪卡系统。从事实或文本创建卡片，使用自由文本答案由代理评分的闪卡聊天，
  从YouTube字幕生成测验，使用自适应调度复习到期卡片，以及
  作为CSV导出/导入牌组。
version: 1.0.0
author: Memento AI
license: MIT
platforms: [macos, linux]
metadata:
  kclaw:
    tags: [教育, 闪卡, 间隔重复, 学习, 测验, YouTube]
    requires_toolsets: [terminal]
    category: productivity
---

# Memento闪卡 — 间隔重复闪卡技能

## 概述

Memento为您提供基于本地文件的闪卡系统，具有间隔重复调度。
用户可以通过自由文本回答问题，让代理评分后再安排下次复习来与闪卡聊天。
在以下情况下使用：

- **记住一个事实** — 将任何陈述变成Q/A闪卡
- **使用间隔重复学习** — 使用自适应间隔和代理评分的自由文本答案复习到期卡片
- **从YouTube视频生成测验** — 获取字幕并生成5个问题的测验
- **管理牌组** — 将卡片组织成集合，导出/导入CSV

所有卡片数据存储在单个JSON文件中。无需外部API密钥 — 您（代理）直接生成闪卡内容和测验问题。

Memento闪卡的用户面向响应风格：
- 仅使用纯文本。不要在回复用户时使用Markdown格式。
- 保持复习和测验反馈简短中性。避免额外的赞美、鼓励或长解释。

## 何时使用

在以下情况下使用此技能：
- 将事实保存为闪卡以供以后复习
- 使用间隔重复复习到期卡片
- 从YouTube视频字幕生成测验
- 导入、导出、检查或删除闪卡数据

不要将此技能用于一般问答、编码帮助或非记忆任务。

## 快速参考

| 用户意图 | 行动 |
|---|---|
| "记住X" / "将其保存为闪卡" | 生成Q/A卡片，调用`memento_cards.py add` |
| 发送事实但未提及闪卡 | 问"要将其保存为Memento闪卡吗？" — 仅在确认后创建 |
| "创建闪卡" | 问Q、A、集合；调用`memento_cards.py add` |
| "复习我的卡片" | 调用`memento_cards.py due`，逐个呈现卡片 |
| "对我进行[YouTube URL]测验" | 调用`youtube_quiz.py fetch VIDEO_ID`，生成5个问题，调用`memento_cards.py add-quiz` |
| "导出我的卡片" | 调用`memento_cards.py export --output PATH` |
| "从CSV导入卡片" | 调用`memento_cards.py import --file PATH --collection NAME` |
| "显示我的统计" | 调用`memento_cards.py stats` |
| "删除卡片" | 调用`memento_cards.py delete --id ID` |
| "删除集合" | 调用`memento_cards.py delete-collection --collection NAME` |

## 卡片存储

卡片存储在以下JSON文件中：

```
~/.kclaw/skills/productivity/memento-flashcards/data/cards.json
```

**永远不要直接编辑此文件。** 始终使用`memento_cards.py`子命令。脚本处理原子写入（写入临时文件，然后重命名）以防止损坏。

文件在首次使用时自动创建。

## 程序

### 从事实创建卡片

### 激活规则

并非每个事实陈述都应该成为闪卡。使用这个三层检查：

1. **明确意图** — 用户提到"memento"、"flashcard"、"记住这个"、"保存这个卡片"、"添加卡片"或类似措辞明确请求闪卡 → **直接创建卡片**，无需确认。
2. **隐含意图** — 用户发送事实陈述但未提及闪卡（例如"光速是299,792 km/s"）→ **先问**："要将其保存为Memento闪卡吗？"仅在用户确认后创建卡片。
3. **无意图** — 消息是编码任务、问题、指令、正常对话，或任何明显不是要记忆的事实 → **完全不激活此技能**。让其他技能或默认行为处理它。

当激活被确认后（层级1直接，层级2确认后），生成闪卡：

**步骤1：** 将陈述转换为Q/A对。在内部使用此格式：

```
将事实陈述转换为前端-后端对。
精确返回两行：
Q: <问题文本>
A: <答案文本>

陈述："<statement>"
```

规则：
- 问题应该测试对关键事实的回忆
- 答案应该简洁直接

**步骤2：** 调用脚本存储卡片：

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py add \
  --question "第二次世界大战哪一年结束？" \
  --answer "1945" \
  --collection "历史"
```

如果用户未指定集合，使用"General"作为默认值。

脚本输出确认创建卡片的JSON。

### 手动创建卡片

当用户明确要求创建闪卡时，问他们：
1. 问题（卡片正面）
2. 答案（卡片背面）
3. 集合名称（可选 — 默认为"General"）

然后像上面一样调用`memento_cards.py add`。

### 复习到期卡片

当用户想要复习时，获取所有到期的卡片：

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py due
```

这返回`next_review_at <= now`的卡片JSON数组。如果需要集合过滤器：

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py due --collection "历史"
```

**复习流程（自由文本评分）：**

这是您必须遵循的精确交互模式示例。用户回答，您评分，告诉他们正确答案，然后评级卡片。

**示例交互：**

> **代理：** 柏林墙是哪一年倒塌的？
>
> **用户：** 1991年
>
> **代理：** 不太对。柏林墙于1989年倒塌。下次复习是明天。
> *（代理调用：memento_cards.py rate --id ABC --rating hard --user-answer "1991"）*
>
> 下一个问题：谁是第一个登上月球的人？

**规则：**

1. 仅显示问题。等待用户回答。
2. 收到他们的答案后，与预期答案比较并评分：
   - **correct** → 用户正确获得了关键事实（即使措辞不同）
   - **partial** → 正确但缺少核心细节
   - **incorrect** → 错误或跑题
3. **您必须告诉用户正确答案和他们的表现。** 保持简短纯文本。使用此格式：
   - correct: "正确。答案：{answer}。下次复习在7天后。"
   - partial: "接近。答案：{answer}。{他们遗漏的内容}。下次复习在3天后。"
   - incorrect: "不太对。答案：{answer}。明天复习。"
4. 然后调用评级命令：correct→easy，partial→good，incorrect→hard。
5. 然后显示下一个问题。

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py rate \
  --id CARD_ID --rating easy --user-answer "用户说的内容"
```

**永远不要跳过步骤3。** 在继续之前，用户必须始终看到正确答案和反馈。

如果没有到期的卡片，告诉用户："现在没有到期的卡片需要复习。稍后再来！"

**退休覆盖：** 在任何时候用户都可以说"退休这张卡片"以永久从复习中移除它。为此使用`--rating retire`。

### 间隔重复算法

评级决定下次复习间隔：

| 评级 | 间隔 | ease_streak | 状态更改 |
|---|---|---|---|
| **hard** | +1天 | 重置为0 | 保持学习中 |
| **good** | +3天 | 重置为0 | 保持学习中 |
| **easy** | +7天 | +1 | 如果ease_streak >= 3 → 退休 |
| **retire** | 永久 | 重置为0 | → 退休 |

- **learning**：卡片在active旋转中
- **retired**：卡片不会出现在复习中（用户已掌握或手动退休）
- 连续三次"easy"评级自动退休卡片

### YouTube测验生成

当用户发送YouTube URL并想要测验时：

**步骤1：** 从URL中提取视频ID（例如从`https://www.youtube.com/watch?v=dQw4w9WgXcQ`中提取`dQw4w9WgXcQ`）。

**步骤2：** 获取字幕：

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/youtube_quiz.py fetch VIDEO_ID
```

这返回`{"title": "...", "transcript": "..."}`或错误。

如果脚本报告`missing_dependency`，告诉用户安装它：
```bash
pip install youtube-transcript-api
```

**步骤3：** 从字幕生成5个测验问题。使用这些规则：

```
你正在为播客剧集创建5个问题的测验。
只返回一个恰好5个对象的JSON数组。
每个对象必须包含'question'和'answer'键。

选择标准：
- 优先考虑重要的、令人惊讶的或基础性的事实。
- 跳过填充词、明显细节和需要大量上下文的事实。
- 永远不要返回真/假问题。
- 永远不要只问日期。

问题规则：
- 每个问题必须正好测试一个离散事实。
- 使用清晰、无歧义的措辞。
- 优先使用What、Who、How many、Which。
- 避免开放式的Describe或Explain提示。

答案规则：
- 每个答案必须少于240个字符。
- 答案本身放在前面，而不是开场白。
- 仅在需要时添加最小的澄清细节。
```

使用字幕的前15,000个字符作为上下文。自己生成问题（您是LLM）。

**步骤4：** 验证输出是有效的JSON，正好有5个项目，每个项目都有非空的`question`和`answer`字符串。如果验证失败，重试一次。

**步骤5：** 存储测验卡片：

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py add-quiz \
  --video-id "VIDEO_ID" \
  --questions '[{"question":"...","answer":"..."},...]' \
  --collection "测验 - 剧集标题"
```

脚本通过`video_id`去重 — 如果该视频的卡片已存在，它跳过创建并报告现有卡片。

**步骤6：** 使用相同的自由文本评分流程逐个呈现问题：
1. 显示"问题1/5: ..."并等待用户的回答。永远不要包含答案或任何关于透露答案的提示。
2. 等待用户用自己的话回答
3. 使用评分提示对他们的答案进行评分（参见"复习到期卡片"部分）
4. **重要：在做任何其他事情之前，您必须回复用户反馈。** 显示评级、正确答案，以及卡片下次到期的复习时间。不要无声地跳到下一个问题。保持简短纯文本。示例："不太对。答案：{answer}。明天复习。"
5. **显示反馈后**，调用评级命令，然后在同一消息中显示下一个问题：
```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py rate \
  --id CARD_ID --rating easy --user-answer "用户说的内容"
```
6. 重复。每个答案必须在下一个问题之前收到可见的反馈。

### 导出/导入CSV

**导出：**
```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py export \
  --output ~/flashcards.csv
```

生成3列CSV：`question,answer,collection`（无标题行）。

**导入：**
```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py import \
  --file ~/flashcards.csv \
  --collection "已导入"
```

读取带有列question、answer和可选collection（第三列）的CSV。如果缺少collection列，使用`--collection`参数。

### 统计

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py stats
```

返回JSON包含：
- `total`：总卡片数
- `learning`：active旋转中的卡片
- `retired`：已掌握的卡片
- `due_now`：现在需要复习的卡片
- `collections`：按集合名称细分

## 陷阱

- **永远不要直接编辑`cards.json`** — 始终使用脚本子命令以避免损坏
- **字幕失败** — 一些YouTube视频没有英文字幕或字幕被禁用；通知用户并建议另一个视频
- **可选依赖** — `youtube_quiz.py`需要`youtube-transcript-api`；如果缺失，告诉用户运行`pip install youtube-transcript-api`
- **大型导入** — 数千行的CSV导入没问题但JSON输出可能很冗长；为用户总结结果
- **视频ID提取** — 同时支持`youtube.com/watch?v=ID`和`youtu.be/ID` URL格式

## 验证

直接验证辅助脚本：

```bash
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py stats
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py add --question "法国的首都是？" --answer "巴黎" --collection "General"
python3 ~/.kclaw/skills/productivity/memento-flashcards/scripts/memento_cards.py due
```

如果您从仓库检出测试，运行：

```bash
pytest tests/skills/test_memento_cards.py tests/skills/test_youtube_quiz.py -q
```

代理级别验证：
- 开始复习并确认反馈是纯文本、简短，且总是在下一张卡片之前包含正确答案
- 运行YouTube测验流程并确认每个答案在下一个问题之前收到可见反馈
