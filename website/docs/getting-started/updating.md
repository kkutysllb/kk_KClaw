---
sidebar_position: 3
title: "更新和卸载"
description: "如何将 KClaw Agent 更新到最新版本或卸载它"
---

# 更新和卸载

## 更新

使用一条命令更新到最新版本：

```bash
kclaw update
```

这会拉取最新代码、更新依赖项，并提示您配置自上次更新以来添加的任何新选项。

:::tip
`kclaw update` 自动检测新配置选项并提示您添加它们。如果跳过了该提示，您可以手动运行 `kclaw config check` 查看缺失的选项，然后 `kclaw config migrate` 交互式添加它们。
:::

### 更新期间会发生什么

当您运行 `kclaw update` 时，会发生以下步骤：

1. **Git pull** — 从 `main` 分支拉取最新代码并更新子模块
2. **依赖安装** — 运行 `uv pip install -e ".[all]"` 以获取新的或更改的依赖项
3. **配置迁移** — 检测自您的版本以来添加的新配置选项，并提示您设置它们
4. **网关自动重启** — 如果网关服务正在运行（Linux 上的 systemd，macOS 上的 launchd），则在更新完成后**自动重启**，以便新代码立即生效

预期输出如下：

```
$ kclaw update
更新 KClaw Agent...
📥 拉取最新代码...
Already up to date.（或：Updating abc1234..def5678）
📦 更新依赖项...
✅ 依赖项已更新
🔍 检查新配置选项...
✅ 配置是最新的（或：发现 2 个新选项——运行迁移...）
🔄 重启网关服务...
✅ 网关已重启
✅ KClaw Agent 更新成功！
```

### 建议的更新后验证

`kclaw update` 处理主要更新路径，但快速验证可以确认一切都已干净落地：

1. `git status --short` — 如果树意外变脏，在继续之前检查
2. `kclaw doctor` — 检查配置、依赖项和服务健康状况
3. `kclaw --version` — 确认版本按预期更新
4. 如果您使用网关：`kclaw gateway status`
5. 如果 `doctor` 报告 npm 审计问题：在标记的目录中运行 `npm audit fix`

:::warning 更新后工作树变脏
如果 `kclaw update` 后 `git status --short` 显示意外更改，请停止并在继续之前检查。这通常意味着本地修改在更新代码之上被重新应用，或者依赖步骤刷新了锁文件。
:::

### 检查当前版本

```bash
kclaw version
```

与 [GitHub releases 页面](https://github.com/kkutysllb/kk_KClaw/releases) 上的最新版本进行比较，或检查可用更新：

```bash
kclaw update --check
```

### 从消息平台更新

您也可以通过发送以下内容直接从 Telegram、Discord、Slack 或 WhatsApp 更新：

```
/update
```

这会拉取最新代码、更新依赖项并重启网关。机器人在重启期间会短暂下线（通常 5-15 秒），然后恢复。

### 手动更新

如果您是手动安装的（不是通过快速安装程序）：

```bash
cd /path/to/kclaw
export VIRTUAL_ENV="$(pwd)/venv"

# 拉取最新代码和子模块
git pull origin main
git submodule update --init --recursive

# 重新安装（获取新依赖）
uv pip install -e ".[all]"
uv pip install -e "./tinker-atropos"

# 检查新配置选项
kclaw config check
kclaw config migrate   # 交互式添加任何缺失的选项
```

### 回滚说明

如果更新引入了问题，您可以回滚到以前的版本：

```bash
cd /path/to/kclaw

# 列出最近版本
git log --oneline -10

# 回滚到特定提交
git checkout <commit-hash>
git submodule update --init --recursive
uv pip install -e ".[all]"

# 如果网关正在运行则重启它
kclaw gateway restart
```

要回滚到特定发布标签：

```bash
git checkout v0.6.0
git submodule update --init --recursive
uv pip install -e ".[all]"
```

:::warning
如果添加了新选项，回滚可能会导致配置不兼容。回滚后运行 `kclaw config check`，如果遇到错误，请从 `config.yaml` 中删除任何无法识别的选项。
:::

### Nix 用户注意事项

如果您通过 Nix flake 安装，更新通过 Nix 包管理器管理：

```bash
# 更新 flake 输入
nix flake update kclaw

# 或使用最新版本重建
nix profile upgrade kclaw
```

Nix 安装是不可变的——回滚由 Nix 的生成系统处理：

```bash
nix profile rollback
```

请参阅 [Nix 设置](./nix-setup.md) 了解更多详细信息。

---

## 卸载

```bash
kclaw uninstall
```

卸载程序会为您提供保留配置文件的选项（`~/.kclaw/`）以供将来重新安装。

### 手动卸载

```bash
rm -f ~/.local/bin/kclaw
rm -rf /path/to/kclaw
rm -rf ~/.kclaw            # 可选——如果您计划重新安装则保留
```

:::info
如果您将网关安装为系统服务，请先停止并禁用它：
```bash
kclaw gateway stop
# Linux: systemctl --user disable kclaw-gateway
# macOS: launchctl remove ai.kclaw.gateway
```
:::
