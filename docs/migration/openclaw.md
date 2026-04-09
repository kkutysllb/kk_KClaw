# 从 OpenClaw 迁移到 KClaw Agent

本指南涵盖如何将您的 OpenClaw 设置、记忆、技能和 API 密钥导入 KClaw Agent。

## 三种迁移方式

### 1. 自动（首次设置期间）

当您首次运行 `kclaw setup` 并且 KClaw 检测到 `~/.openclaw` 时，它会自动提供在配置开始前导入您的 OpenClaw 数据的选项。只需接受提示，一切都会为您处理。

### 2. CLI 命令（快速、可脚本化）

```bash
kclaw claw migrate                      # 带确认提示的完整迁移
kclaw claw migrate --dry-run            # 预览将发生什么
kclaw claw migrate --preset user-data   # 不迁移 API 密钥/密钥的迁移
kclaw claw migrate --yes                # 跳过确认提示
```

**所有选项：**

| 标志 | 描述 |
|------|------|
| `--source PATH` | OpenClaw 目录路径（默认：`~/.openclaw`） |
| `--dry-run` | 仅预览 — 不修改文件 |
| `--preset {user-data,full}` | 迁移预设（默认：`full`）。`user-data` 排除密钥 |
| `--overwrite` | 覆盖现有文件（默认：跳过冲突） |
| `--migrate-secrets` | 包含白名单密钥（随 `full` 预设自动启用） |
| `--workspace-target PATH` | 将工作区指令（AGENTS.md）复制到此绝对路径 |
| `--skill-conflict {skip,overwrite,rename}` | 如何处理技能名称冲突（默认：`skip`） |
| `--yes`, `-y` | 跳过确认提示 |

### 3. 代理引导（交互式，带预览）

请代理为您运行迁移：

```
> 将我的 OpenClaw 设置迁移到 KClaw
```

代理将使用 `openclaw-migration` 技能来：
1. 首先运行 dry-run 预览更改
2. 询问冲突解决方案（SOUL.md、技能等）
3. 让您在 `user-data` 和 `full` 预设之间选择
4. 使用您的选择执行迁移
5. 打印已迁移内容的详细摘要

## 迁移内容

### `user-data` 预设
| 项目 | 源 | 目标 |
|------|--------|-------------|
| SOUL.md | `~/.openclaw/workspace/SOUL.md` | `~/.kclaw/SOUL.md` |
| 记忆条目 | `~/.openclaw/workspace/MEMORY.md` | `~/.kclaw/memories/MEMORY.md` |
| 用户 profile | `~/.openclaw/workspace/USER.md` | `~/.kclaw/memories/USER.md` |
| 技能 | `~/.openclaw/workspace/skills/` | `~/.kclaw/skills/openclaw-imports/` |
| 命令白名单 | `~/.openclaw/workspace/exec_approval_patterns.yaml` | 合并到 `~/.kclaw/config.yaml` |
| 消息设置 | `~/.openclaw/config.yaml`（TELEGRAM_ALLOWED_USERS, MESSAGING_CWD） | `~/.kclaw/.env` |
| TTS 资产 | `~/.openclaw/workspace/tts/` | `~/.kclaw/tts/` |

### `full` 预设（添加到 `user-data`）
| 项目 | 源 | 目标 |
|------|--------|------|
| Telegram bot token | `~/.openclaw/config.yaml` | `~/.kclaw/.env` |
| OpenRouter API 密钥 | `~/.openclaw/.env` 或配置 | `~/.kclaw/.env` |
| OpenAI API 密钥 | `~/.openclaw/.env` 或配置 | `~/.kclaw/.env` |
| Anthropic API 密钥 | `~/.openclaw/.env` 或配置 | `~/.kclaw/.env` |
| ElevenLabs API 密钥 | `~/.openclaw/.env` 或配置 | `~/.kclaw/.env` |

仅这 6 个白名单密钥会被导入。其他凭证会被跳过并报告。

## 冲突处理

默认情况下，迁移 **不会覆盖** 现有 KClaw 数据：

- **SOUL.md** — 如果 `~/.kclaw/` 中已存在则跳过
- **记忆条目** — 如果记忆已存在则跳过（避免重复）
- **技能** — 如果同名技能已存在则跳过
- **API 密钥** — 如果密钥已设置在 `~/.kclaw/.env` 中则跳过

要覆盖冲突，请使用 `--overwrite`。迁移在覆盖前会创建备份。

对于技能，您也可以使用 `--skill-conflict rename` 以新名称导入冲突技能（例如 `skill-name-imported`）。

## 迁移报告

每次迁移（包括 dry runs）都会生成报告，显示：
- **已迁移项目** — 成功导入的内容
- **冲突** — 因已存在而跳过的项目
- **跳过的项目** — 源中未找到的项目
- **错误** — 导入失败的项目

对于执行运行，完整报告保存到 `~/.kclaw/migration/openclaw/<timestamp>/`。

## 故障排除

### "OpenClaw 目录未找到"
迁移默认查找 `~/.openclaw`。如果您的 OpenClaw 安装在其他地方，请使用 `--source`：
```bash
kclaw claw migrate --source /path/to/.openclaw
```

### "找不到迁移脚本"
迁移脚本随 KClaw Agent 一起提供。如果您通过 pip 安装（不是 git clone），则可能不存在 `optional-skills/` 目录。从技能中心安装技能：
```bash
kclaw skills install openclaw-migration
```

### 记忆溢出
如果您的 OpenClaw MEMORY.md 或 USER.md 超过 KClaw 的字符限制，超出条目会导出到迁移报告目录中的溢出文件。您可以手动审查并添加最重要的条目。
