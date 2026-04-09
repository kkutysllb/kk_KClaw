---
name: 1password
description: 设置并使用 1Password CLI (op)。在安装 CLI、启用桌面应用集成、登录以及读取/注入命令秘密时使用。
version: 1.0.0
author: arceus77-7, enhanced by KClaw Agent
license: MIT
metadata:
  kclaw:
    tags: [security, secrets, 1password, op, cli]
    category: security
setup:
  help: "在 https://my.1password.com → Settings → Service Accounts 创建服务账户"
  collect_secrets:
    - env_var: OP_SERVICE_ACCOUNT_TOKEN
      prompt: "1Password 服务账户令牌"
      provider_url: "https://developer.1password.com/docs/service-accounts/"
      secret: true
---

# 1Password CLI

当用户希望通过 1Password 而不是明文环境变量或文件管理秘密时使用此技能。

## 需求

- 1Password 账户
- 已安装 1Password CLI (`op`)
- 以下之一：桌面应用集成、服务账户令牌 (`OP_SERVICE_ACCOUNT_TOKEN`) 或 Connect 服务器
- `tmux` 可用于在 KClaw 终端调用期间保持稳定的认证会话（仅限桌面应用流程）

## 何时使用

- 安装或配置 1Password CLI
- 使用 `op signin` 登录
- 读取如 `op://Vault/Item/field` 的秘密引用
- 使用 `op inject` 将秘密注入配置/模板
- 通过 `op run` 使用秘密环境变量运行命令

## 认证方法

### 服务账户（推荐用于 KClaw）

在 `~/.kclaw/.env` 中设置 `OP_SERVICE_ACCOUNT_TOKEN`（技能将在首次加载时提示）。
无需桌面应用。支持 `op read`、`op inject`、`op run`。

```bash
export OP_SERVICE_ACCOUNT_TOKEN="your-token-here"
op whoami  # 验证 — 应显示 Type: SERVICE_ACCOUNT
```

### 桌面应用集成（交互式）

1. 在 1Password 桌面应用中启用：Settings → Developer → Integrate with 1Password CLI
2. 确保应用已解锁
3. 运行 `op signin` 并批准生物识别提示

### Connect 服务器（自托管）

```bash
export OP_CONNECT_HOST="http://localhost:8080"
export OP_CONNECT_TOKEN="your-connect-token"
```

## 设置

1. 安装 CLI：

```bash
# macOS
brew install 1password-cli

# Linux（官方包/安装文档）
# 参见 references/get-started.md 获取特定发行版的链接。

# Windows (winget)
winget install AgileBits.1Password.CLI
```

2. 验证：

```bash
op --version
```

3. 选择上述一种认证方法并进行配置。

## KClaw 执行模式（桌面应用流程）

KClaw 终端命令默认是非交互式的，可能在调用之间丢失认证上下文。
为了在使用桌面应用集成时可靠地使用 `op`，在专用 tmux 会话中运行登录和秘密操作。

注意：使用 `OP_SERVICE_ACCOUNT_TOKEN` 时不需要 — token 在终端调用之间自动持久化。

```bash
SOCKET_DIR="${TMPDIR:-/tmp}/kclaw-tmux-sockets"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/kclaw-op.sock"
SESSION="op-auth-$(date +%Y%m%d-%H%M%S)"

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell

# 登录（提示时在桌面应用中批准）
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- "eval \"\$(op signin --account my.1password.com)\"" Enter

# 验证认证
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- "op whoami" Enter

# 示例读取
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- "op read 'op://Private/Npmjs/one-time password?attribute=otp'" Enter

# 需要时捕获输出
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200

# 清理
tmux -S "$SOCKET" kill-session -t "$SESSION"
```

## 常用操作

### 读取秘密

```bash
op read "op://app-prod/db/password"
```

### 获取 OTP

```bash
op read "op://app-prod/npm/one-time password?attribute=otp"
```

### 注入模板

```bash
echo "db_password: {{ op://app-prod/db/password }}" | op inject
```

### 使用秘密运行命令

```bash
export DB_PASSWORD="op://app-prod/db/password"
op run -- sh -c '[ -n "$DB_PASSWORD" ] && echo "DB_PASSWORD is set" || echo "DB_PASSWORD missing"'
```

## 防护栏

- 除非用户明确要求，否则不要将原始秘密打印回用户。
- 优先使用 `op run` / `op inject` 而不是将秘密写入文件。
- 如果命令失败并显示"未登录账户"，在同一个 tmux 会话中重新运行 `op signin`。
- 如果桌面应用集成不可用（无头/CI），使用服务账户令牌流程。

## CI / 无头注意

对于非交互式使用，使用 `OP_SERVICE_ACCOUNT_TOKEN` 进行认证，避免交互式 `op signin`。
服务账户需要 CLI v2.18.0+。

## 参考

- `references/get-started.md`
- `references/cli-examples.md`
- https://developer.1password.com/docs/cli/
- https://developer.1password.com/docs/service-accounts/
