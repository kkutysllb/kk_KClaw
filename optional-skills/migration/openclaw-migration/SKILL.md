---
name: openclaw-migration
description: 将用户的OpenClaw自定义足迹迁移到KClaw Agent。从~/.openclaw导入KClaw兼容的记忆、SOUL.md、命令允许列表、用户技能和选定的workspace资产，然后准确报告什么无法迁移及原因。
version: 1.0.0
author: KClaw Agent (Nous Research)
license: MIT
metadata:
  kclaw:
    tags: [迁移, OpenClaw, KClaw, 记忆, 角色, 导入]
    related_skills: [kclaw]
---

# OpenClaw -> KClaw 迁移

当用户想要将OpenClaw设置迁移到KClaw Agent且手动清理最少时使用此技能。

## CLI命令

对于快速、非交互式迁移，使用内置CLI命令：

```bash
kclaw claw migrate              # 完全交互式迁移
kclaw claw migrate --dry-run    # 预览将要迁移的内容
kclaw claw migrate --preset user-data   # 不迁移密钥
kclaw claw migrate --overwrite  # 覆盖现有冲突
kclaw claw migrate --source /custom/path/.openclaw  # 自定义源
```

CLI命令运行下面描述的相同迁移脚本。当您想要交互式、有指导的迁移并带有dry-run预览和每个项目的冲突解决时，使用此技能（通过代理）。

**首次设置：** `kclaw setup`向导自动检测`~/.openclaw`并在配置开始前提供迁移。

## 此技能的作用

它使用`scripts/openclaw_to_kclaw.py`来：

- 将`SOUL.md`导入KClaw主目录作为`SOUL.md`
- 将OpenClaw `MEMORY.md`和`USER.md`转换为KClaw记忆条目
- 将OpenClaw命令批准模式合并到KClaw `command_allowlist`
- 迁移KClaw兼容的消息设置，如`TELEGRAM_ALLOWED_USERS`和`MESSAGING_CWD`
- 将OpenClaw技能复制到`~/.kclaw/skills/openclaw-imports/`
- 可选地将OpenClaw workspace指令文件复制到选定的KClaw workspace
- 镜像兼容的workspace资产，如`workspace/tts/`到`~/.kclaw/tts/`
- 归档没有直接KClaw目标点的非秘密文档
- 生成结构化报告，列出已迁移项目、冲突、跳过的项目及原因

## 路径解析

辅助脚本位于此技能目录中：

- `scripts/openclaw_to_kclaw.py`

当从此技能中心安装时，正常位置是：

- `~/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py`

不要猜测更短的路径如`~/.kclaw/skills/openclaw-migration/...`。

在运行辅助脚本之前：

1. 优先使用`~/.kclaw/skills/migration/openclaw-migration/`下的安装路径。
2. 如果该路径失败，检查已安装的技能目录并相对于已安装的`SKILL.md`解析脚本。
3. 仅在安装位置缺失或技能被手动移动时才使用`find`作为后备。
4. 当调用终端工具时，不要传递`workdir: "~"`。使用绝对目录如用户的主目录，或完全省略`workdir`。

使用`--migrate-secrets`时，它还将导入一小部分KClaw兼容的密钥，目前包括：

- `TELEGRAM_BOT_TOKEN`

## 默认工作流

1. 首先用dry run检查。
2. 展示可以迁移什么、不能迁移什么以及将归档什么的简单总结。
3. 如果`clarify`工具可用，使用它进行用户决策，而不是请求自由格式的散文回复。
4. 如果dry run发现导入的技能目录冲突，在执行之前问这些应该如何处理。
5. 在执行之前问用户选择两种支持的迁移模式。
6. 仅在用户想要带来workspace指令文件时才请求目标workspace路径。
7. 使用匹配的预设和标志执行迁移。
8. 总结结果，特别是：
   - 迁移了什么
   - 什么被归档供手动审查
   - 什么被跳过及原因

## 用户交互协议

KClaw CLI支持`clarify`工具用于交互式提示，但它限于：

- 一次一个选择
- 最多4个预定义选择
- 自动的`Other`自由文本选项

它**不支持**真正的多选复选框。

对于每个`clarify`调用：

- 始终包含非空的`question`
- 仅对真正的可选择提示包含`choices`
- 将`choices`保持在2-4个纯字符串选项
- 永远不要发出占位符或截断的选项如`...`
- 永远不要用额外空白填充或样式化选项
- 永远不要在问题中包含假表单字段如`enter directory here`、空行或下划线如`_____`
- 对于开放式路径问题，仅问纯句子；用户在面板下方的正常CLI提示中输入

如果`clarify`调用返回错误，检查错误文本，纠正有效载荷，然后用有效的`question`和干净的选项重试一次。

当`clarify`可用且dry run揭示任何需要的用户决策时，**您的下一个行动必须是`clarify`工具调用**。
不要用正常的助手消息结束回合如：

- "让我展示选项"
- "你想怎么做？"
- "以下是选项"

如果需要用户决策，通过`clarify`收集后再产生更多散文。
如果多个未解决的决策仍然存在，不要在它们之间插入解释性助手消息。收到一个`clarify`响应后，您的下一个行动通常是下一个必需的`clarify`调用。

当dry run报告时，将`workspace-agents`视为未解决的决策：

- `kind="workspace-agents"`
- `status="skipped"`
- 原因包含`No workspace target was provided`

在那种情况下，您必须在执行之前问关于workspace指令的内容。不要无声地将其视为跳过的决策。

由于该限制，使用这个简化的决策流：

1. 对于`SOUL.md`冲突，使用`clarify`与选项如：
   - `keep existing`
   - `overwrite with backup`
   - `review first`
2. 如果dry run显示一个或多个`kind="skill"`项目带有`status="conflict"`，使用`clarify`与选项如：
   - `keep existing skills`
   - `overwrite conflicting skills with backup`
   - `import conflicting skills under renamed folders`
3. 对于workspace指令，使用`clarify`与选项如：
   - `skip workspace instructions`
   - `copy to a workspace path`
   - `decide later`
4. 如果用户选择复制workspace指令，问后续开放式`clarify`问题请求**绝对路径**。
5. 如果用户选择`skip workspace instructions`或`decide later`， proceeding without `--workspace-target`。
5. 对于迁移模式，使用`clarify`与这3个选项：
   - `user-data only`
   - `full compatible migration`
   - `cancel`
6. `user-data only`意味着：迁移用户数据和兼容配置，但**不**导入允许列表中的密钥。
7. `full compatible migration`意味着：迁移相同的兼容用户数据以及存在的允许列表密钥。
8. 如果`clarify`不可用，在正常文本中问同样的问题，但仍将答案约束为`user-data only`、`full compatible migration`或`cancel`。

执行门槛：

- 当由`No workspace target was provided`引起的`workspace-agents`跳过仍然未解决时，不要执行。
- 解决它的唯一有效方式是：
  - 用户明确选择`skip workspace instructions`
  - 用户明确选择`decide later`
  - 用户在选择`copy to a workspace path`后提供workspace路径
- dry run中缺少workspace目标本身不是执行许可。
- 当任何需要的`clarify`决策仍然未解决时，不要执行。

使用这些确切的`clarify`有效载荷形状作为默认模式：

- `{"question":"Your existing SOUL.md conflicts with the imported one. What should I do?","choices":["keep existing","overwrite with backup","review first"]}`
- `{"question":"One or more imported OpenClaw skills already exist in KClaw. How should I handle those skill conflicts?","choices":["keep existing skills","overwrite conflicting skills with backup","import conflicting skills under renamed folders"]}`
- `{"question":"Choose migration mode: migrate only user data, or run the full compatible migration including allowlisted secrets?","choices":["user-data only","full compatible migration","cancel"]}`
- `{"question":"Do you want to copy the OpenClaw workspace instructions file into a KClaw workspace?","choices":["skip workspace instructions","copy to a workspace path","decide later"]}`
- `{"question":"Please provide an absolute path where the workspace instructions should be copied."}`

## 决策到命令映射

精确地将用户决策映射到命令标志：

- 如果用户为`SOUL.md`选择`keep existing`，**不要**添加`--overwrite`。
- 如果用户选择`overwrite with backup`，添加`--overwrite`。
- 如果用户选择`review first`，在执行之前停止并审查相关文件。
- 如果用户为技能冲突选择`keep existing skills`，添加`--skill-conflict skip`。
- 如果用户选择`overwrite conflicting skills with backup`，添加`--skill-conflict overwrite`。
- 如果用户选择`import conflicting skills under renamed folders`，添加`--skill-conflict rename`。
- 如果用户选择`user-data only`，使用`--preset user-data`执行，**不要**添加`--migrate-secrets`。
- 如果用户选择`full compatible migration`，使用`--preset full --migrate-secrets`执行。
- 仅在用户明确提供绝对workspace路径时才添加`--workspace-target`。
- 如果用户选择`skip workspace instructions`或`decide later`，不要添加`--workspace-target`。

在执行之前，用纯语言重述确切的命令计划并确保它与用户的选择匹配。

## 运行后报告规则

执行后，将脚本的JSON输出视为真实来源。

1. 基于`report.summary`计算所有计数。
2. 仅在项目状态正好是`migrated`时才将其列在"Successfully Migrated"下。
3. 不要声称冲突被解决，除非报告将该项目显示为`migrated`。
4. 不要说`SOUL.md`被覆盖，除非`kind="soul"`的报告项目有`status="migrated"`。
5. 如果`report.summary.conflict > 0`，包含冲突部分而不是无声地暗示成功。
6. 如果计数和列出的项目不一致，在回复之前修复列表以匹配报告。
7. 当可用时包含报告中的`output_dir`路径，以便用户可以检查`report.json`、`summary.md`、备份和归档文件。
8. 对于记忆或用户配置溢出，不要说条目被归档，除非报告明确显示归档路径。如果`details.overflow_file`存在，说完整的溢出列表已导出到那里。
9. 如果技能在重命名文件夹下导入，报告最终目标并提及`details.renamed_from`。
10. 如果`report.skill_conflict_mode`存在，使用它作为所选导入技能冲突策略的真实来源。
11. 如果项目有`status="skipped"`，不要将其描述为覆盖、备份、迁移或解决。
12. 如果`kind="soul"`有`status="skipped"`，原因为`Target already matches source`，说它保持不变，不要提及备份。
13. 如果重命名的导入技能有空的`details.backup`，不要暗示现有的KClaw技能被重命名或备份。仅说导入的副本被放在新目标并引用`details.renamed_from`作为保留的原文件夹。

## 迁移预设

在正常使用中优先使用这两个预设：

- `user-data`
- `full`

`user-data`包括：

- `soul`
- `workspace-agents`
- `memory`
- `user-profile`
- `messaging-settings`
- `command-allowlist`
- `skills`
- `tts-assets`
- `archive`

`full`包括`user-data`中的所有内容加上：

- `secret-settings`

辅助脚本仍然支持类别级`--include` / `--exclude`，但将其视为高级后备而不是正常UX。

## 命令

带完整发现的dry run：

```bash
python3 ~/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py
```

使用终端工具时，优先使用绝对调用模式如：

```json
{"command":"python3 /home/USER/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py","workdir":"/home/USER"}
```

使用user-data预设的dry run：

```bash
python3 ~/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py --preset user-data
```

执行user-data迁移：

```bash
python3 ~/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py --execute --preset user-data --skill-conflict skip
```

执行完全兼容迁移：

```bash
python3 ~/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py --execute --preset full --migrate-secrets --skill-conflict skip
```

执行包括workspace指令：

```bash
python3 ~/.kclaw/skills/migration/openclaw-migration/scripts/openclaw_to_kclaw.py --execute --preset user-data --skill-conflict rename --workspace-target "/absolute/workspace/path"
```

不要默认使用`$PWD`或主目录作为workspace目标。先请求明确的workspace路径。

## 重要规则

1. 在写入之前运行dry run，除非用户明确表示立即继续。
2. 默认不迁移密钥。令牌、auth blobs、设备凭证和原始网关配置应保持在KClaw之外，除非用户明确要求密钥迁移。
3. 除非用户明确希望，否则不要无声地覆盖非空KClaw目标。覆盖启用时辅助脚本将保留备份。
4. 始终为用户提供跳过项目报告。该报告是迁移的一部分，不是可选的附加项。
5. 优先使用主要OpenClaw workspace（`~/.openclaw/workspace/`）而不是`workspace.default/`。仅在主文件缺失时使用默认workspace作为后备。
6. 即使在密钥迁移模式下，也仅迁移有干净KClaw目标点的密钥。不支持的auth blobs仍必须报告为跳过。
7. 如果dry run显示大型资产复制、冲突的`SOUL.md`或溢出的记忆条目，在执行之前分别指出。
8. 如果用户不确定，默认`user-data only`。
9. 仅在用户明确提供目标workspace路径时才包含`workspace-agents`。
10. 将类别级`--include` / `--exclude`视为高级逃生舱，而不是正常流程。
11. 不要用模糊的"你想怎么做？"结束dry run摘要。如果`clarify`可用，使用结构化后续提示代替。
12. 当真正的选择提示可以工作时，不要使用开放式`clarify`提示。仅对绝对路径或文件审查请求使用自由文本。
13. dry run后，如果在仍然有未解决的决策时不要停止。在最高优先级的阻止决策上立即使用`clarify`。
14. 后续问题的优先顺序：
    - `SOUL.md`冲突
    - 导入技能冲突
    - 迁移模式
    - workspace指令目标
15. 不要承诺在同一条消息中稍后呈现选择。通过实际调用`clarify`来呈现它们。
16. 迁移模式答案后，明确检查`workspace-agents`是否仍然未解决。如果是，您的下一个行动必须是workspace-instructions `clarify`调用。
17. 在任何`clarify`答案后，如果另一个需要的决策仍然存在，不要叙述刚刚决定的内容。立即问下一个需要的问题。

## 预期结果

成功运行后，用户应该拥有：

- 导入的KClaw角色状态
- 用转换后的OpenClaw知识填充的KClaw记忆文件
- 在`~/.kclaw/skills/openclaw-imports/`下可用的OpenClaw技能
- 显示任何冲突、遗漏或不支持数据的迁移报告
