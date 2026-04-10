---
sidebar_position: 3
title: "Nix 和 NixOS 设置"
description: "使用 Nix 安装和部署 KClaw Agent——从快速的 `nix run` 到完全声明式的 NixOS 模块和容器模式"
---

# Nix 和 NixOS 设置

KClaw Agent 附带一个 Nix flake，具有三个集成级别：

| 级别 | 适用对象 | 您获得的内容 |
|-------|-------------|--------------|
| **`nix run` / `nix profile install`** | 任何 Nix 用户（macOS、Linux） | 预构建二进制文件，包含所有依赖项——然后使用标准 CLI 工作流程 |
| **NixOS 模块（原生）** | NixOS 服务器部署 | 声明式配置、强化 systemd 服务、托管密钥 |
| **NixOS 模块（容器）** | 需要自我修改的代理 | 以上所有内容，加上一个持久化 Ubuntu 容器，代理可以在其中 `apt`/`pip`/`npm install` |

:::info 与标准安装有什么不同
`curl | bash` 安装程序自行管理 Python、Node 和依赖项。Nix flake 取代了所有这些——每个 Python 依赖都是通过 [uv2nix](https://github.com/pyproject-nix/uv2nix) 构建的 Nix 派生项，运行时工具（Node.js、git、ripgrep、ffmpeg）被包装到二进制文件的 PATH 中。没有运行时 pip，没有 venv 激活，没有 `npm install`。

**对于非 NixOS 用户**，这仅更改安装步骤。之后的所有内容（`kclaw setup`、`kclaw gateway install`、配置编辑）与标准安装完全相同。

**对于 NixOS 模块用户**，整个生命周期是不同的：配置位于 `configuration.nix` 中，密钥通过 sops-nix/agenix 处理，服务是 systemd 单元，CLI 配置命令被阻止。您以与管理任何其他 NixOS 服务相同的方式管理 kclaw。
:::

## 前置要求

- **启用 flakes 的 Nix** — 推荐 [Determinate Nix](https://install.determinate.systems)（默认启用 flakes）
- **您想要使用的服务的 API 密钥**（至少需要一个 OpenRouter 或 Anthropic 密钥）

---

## 快速开始（任何 Nix 用户）

无需克隆。Nix 获取、构建并运行一切：

```bash
# 直接运行（首次使用时构建，之后缓存）
nix run github:NousResearch/kclaw -- setup
nix run github:NousResearch/kclaw -- chat

# 或持久安装
nix profile install github:NousResearch/kclaw
kclaw setup
kclaw chat
```

`nix profile install` 后，`kclaw`、`kclaw` 和 `kclaw-acp` 在您的 PATH 上。从这里开始，工作流程与 [标准安装](./installation.md) 相同——`kclaw setup` 引导您完成提供商选择，`kclaw gateway install` 设置 launchd（macOS）或 systemd 用户服务，配置位于 `~/.kclaw/`。

<details>
<summary><strong>从本地克隆构建</strong></summary>

```bash
git clone https://github.com/kkutysllb/kk_KClaw.git
cd kclaw
nix build
./result/bin/kclaw setup
```

</details>

---

## NixOS 模块

flake 导出 `nixosModules.default`——一个完整的 NixOS 服务模块，声明式管理用户创建、目录、配置生成、密钥、文档和服务生命周期。

:::note
此模块需要 NixOS。对于非 NixOS 系统（macOS、其他 Linux 发行版），使用 `nix profile install` 和上面的标准 CLI 工作流程。
:::

### 添加 Flake 输入

```nix
# /etc/nixos/flake.nix（或您的系统 flake）
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    kclaw.url = "github:NousResearch/kclaw";
  };

  outputs = { nixpkgs, kclaw, ... }: {
    nixosConfigurations.your-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        kclaw.nixosModules.default
        ./configuration.nix
      ];
    };
  };
}
```

### 最小配置

```nix
# configuration.nix
{ config, ... }: {
  services.kclaw = {
    enable = true;
    settings.model.default = "anthropic/claude-sonnet-4";
    environmentFiles = [ config.sops.secrets."kclaw-env".path ];
    addToSystemPackages = true;
  };
}
```

就这样。`nixos-rebuild switch` 创建 `kclaw` 用户，生成 `config.yaml`，连接密钥，并启动网关——一个长期运行的服务，将代理连接到消息平台（Telegram、Discord 等）并监听传入消息。

:::warning 密钥是必需的
上面的 `environmentFiles` 行假设您已配置 [sops-nix](https://github.com/Mic92/sops-nix) 或 [agenix](https://github.com/ryantm/agenix)。文件应至少包含一个 LLM 提供商密钥（例如 `OPENROUTER_API_KEY=sk-or-...`）。请参阅 [密钥管理](#secrets-management) 了解完整设置。如果您还没有密钥管理器，您可以使用普通文件作为起点——只需确保它不是全局可读的：

```bash
echo "OPENROUTER_API_KEY=sk-or-your-key" | sudo install -m 0600 -o kclaw /dev/stdin /var/lib/kclaw/env
```

```nix
services.kclaw.environmentFiles = [ "/var/lib/kclaw/env" ];
```
:::

:::tip addToSystemPackages
设置 `addToSystemPackages = true` 会做两件事：将 `kclaw` CLI 放在系统 PATH 上**并且**系统范围内设置 `KCLAW_HOME`，以便交互式 CLI 与网关服务共享状态（会话、技能、cron）。如果没有它，在 shell 中运行 `kclaw` 会创建一个单独的 `~/.kclaw/` 目录。
:::

### 验证它是否工作

`nixos-rebuild switch` 后，检查服务是否正在运行：

```bash
# 检查服务状态
systemctl status kclaw

# 观看日志（Ctrl+C 停止）
journalctl -u kclaw -f

# 如果 addToSystemPackages 为 true，测试 CLI
kclaw version
kclaw config       # 显示生成的配置
```

### 选择部署模式

模块支持两种模式，由 `container.enable` 控制：

| | **原生**（默认） | **容器** |
|---|---|---|
| 如何运行 | 主机上的强化 systemd 服务 | 具有 `/nix/store` 挂载的持久化 Ubuntu 容器 |
| 安全性 | `NoNewPrivileges`、`ProtectSystem=strict`、`PrivateTmp` | 容器隔离，以非特权用户身份运行 |
| 代理可以自我安装包 | 否——只有 Nix 提供的 PATH 上的工具 | 是——`apt`、`pip`、`npm` 安装在重启之间持久化 |
| 配置表面 | 相同 | 相同 |
| 何时选择 | 标准部署、最大安全性、可重现性 | 代理需要运行时包安装、可变环境、实验性工具 |

要启用容器模式，添加一行：

```nix
{
  services.kclaw = {
    enable = true;
    container.enable = true;
    # ... rest of config is identical
  };
}
```

:::info
容器模式通过 `mkDefault` 自动启用 `virtualisation.docker.enable`。如果您使用 Podman，请设置 `container.backend = "podman"` 和 `virtualisation.docker.enable = false`。
:::

---

## 配置

### 声明式设置

`settings` 选项接受任意属性集，渲染为 `config.yaml`。它支持跨多个模块定义的深度合并（通过 `lib.recursiveUpdate`），因此您可以将配置拆分到多个文件中：

```nix
# base.nix
services.kclaw.settings = {
  model.default = "anthropic/claude-sonnet-4";
  toolsets = [ "all" ];
  terminal = { backend = "local"; timeout = 180; };
};

# personality.nix
services.kclaw.settings = {
  display = { compact = false; personality = "kawaii"; };
  memory = { memory_enabled = true; user_profile_enabled = true; };
};
```

两者在评估时深度合并。Nix 声明的键总是优先于磁盘上现有 `config.yaml` 中的键，但 **Nix 不触及的用户添加的键会被保留**。这意味着如果代理或手动编辑添加了像 `skills.disabled` 或 `streaming.enabled` 这样的键，它们会在 `nixos-rebuild switch` 中保留。

:::note 模型命名
`settings.model.default` 使用您的提供商期望的模型标识符。使用 [OpenRouter](https://openrouter.ai)（默认），这些看起来像 `"anthropic/claude-sonnet-4"` 或 `"google/gemini-3-flash"`。如果您直接使用提供商（Anthropic、OpenAI），请设置 `settings.model.base_url` 指向他们的 API 并使用他们原生的模型 ID（例如 `"claude-sonnet-4-20250514"`）。当没有设置 `base_url` 时，KClaw 默认为 OpenRouter。
:::

:::tip 发现可用的配置键
运行 `nix build .#configKeys && cat result` 查看从 Python 的 `DEFAULT_CONFIG` 提取的每个叶配置键。您可以将现有的 `config.yaml` 粘贴到 `settings` 属性集中——结构一一映射。
:::

<details>
<summary><strong>完整示例：所有常用自定义设置</strong></summary>

```nix
{ config, ... }: {
  services.kclaw = {
    enable = true;
    container.enable = true;

    # ── Model ──────────────────────────────────────────────────────────
    settings = {
      model = {
        base_url = "https://openrouter.ai/api/v1";
        default = "anthropic/claude-opus-4.6";
      };
      toolsets = [ "all" ];
      max_turns = 100;
      terminal = { backend = "local"; cwd = "."; timeout = 180; };
      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "google/gemini-3-flash-preview";
      };
      memory = { memory_enabled = true; user_profile_enabled = true; };
      display = { compact = false; personality = "kawaii"; };
      agent = { max_turns = 60; verbose = false; };
    };

    # ── Secrets ────────────────────────────────────────────────────────
    environmentFiles = [ config.sops.secrets."kclaw-env".path ];

    # ── Documents ──────────────────────────────────────────────────────
    documents = {
      "SOUL.md" = builtins.readFile /home/user/.kclaw/SOUL.md;
      "USER.md" = ./documents/USER.md;
    };

    # ── MCP Servers ────────────────────────────────────────────────────
    mcpServers.filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/data/workspace" ];
    };

    # ── Container options ──────────────────────────────────────────────
    container = {
      image = "ubuntu:24.04";
      backend = "docker";
      extraVolumes = [ "/home/user/projects:/projects:rw" ];
      extraOptions = [ "--gpus" "all" ];
    };

    # ── Service tuning ─────────────────────────────────────────────────
    addToSystemPackages = true;
    extraArgs = [ "--verbose" ];
    restart = "always";
    restartSec = 5;
  };
}
```

</details>

### 逃生舱：自带配置

如果您宁愿完全在 Nix 之外管理 `config.yaml`，请使用 `configFile`：

```nix
services.kclaw.configFile = /etc/kclaw/config.yaml;
```

这完全绕过 `settings`——无合并，无生成。文件在每次激活时原样复制到 `$KCLAW_HOME/config.yaml`。

### 自定义速查表

Nix 用户最常自定义的内容的快速参考：

| 我想要... | 选项 | 示例 |
|---|---|---|
| 更改 LLM 模型 | `settings.model.default` | `"anthropic/claude-sonnet-4"` |
| 使用不同的提供商端点 | `settings.model.base_url` | `"https://openrouter.ai/api/v1"` |
| 添加 API 密钥 | `environmentFiles` | `[ config.sops.secrets."kclaw-env".path ]` |
| 给代理一个人格 | `documents."SOUL.md"` | `builtins.readFile ./my-soul.md` |
| 添加 MCP 工具服务器 | `mcpServers.<name>` | 请参阅 [MCP 服务器](#mcp-servers) |
| 将主机目录挂载到容器 | `container.extraVolumes` | `[ "/data:/data:rw" ]` |
| 传递 GPU 访问权限到容器 | `container.extraOptions` | `[ "--gpus" "all" ]` |
| 使用 Podman 而不是 Docker | `container.backend` | `"podman"` |
| 将工具添加到服务 PATH（仅原生） | `extraPackages` | `[ pkgs.pandoc pkgs.imagemagick ]` |
| 使用自定义基础镜像 | `container.image` | `"ubuntu:24.04"` |
| 覆盖 kclaw 包 | `package` | `inputs.kclaw.packages.${system}.default.override { ... }` |
| 更改状态目录 | `stateDir` | `"/opt/kclaw"` |
| 设置代理的工作目录 | `workingDirectory` | `"/home/user/projects"` |

---

## 密钥管理

:::danger 永远不要将 API 密钥放在 `settings` 或 `environment` 中
Nix 表达式中的值最终进入 `/nix/store`，这是全局可读的。始终使用带有密钥管理器的 `environmentFiles`。
:::

`environment`（非密钥变量）和 `environmentFiles`（密钥文件）都在激活时（`nixos-rebuild switch`）合并到 `$KCLAW_HOME/.env`。KClaw 在每次启动时读取此文件，因此更改需要 `systemctl restart kclaw`——无需重新创建容器。

### sops-nix

```nix
{
  sops = {
    defaultSopsFile = ./secrets/kclaw.yaml;
    age.keyFile = "/home/user/.config/sops/age/keys.txt";
    secrets."kclaw-env" = { format = "yaml"; };
  };

  services.kclaw.environmentFiles = [
    config.sops.secrets."kclaw-env".path
  ];
}
```

密钥文件包含键值对：

```yaml
# secrets/kclaw.yaml（用 sops 加密）
kclaw-env: |
    OPENROUTER_API_KEY=sk-or-...
    TELEGRAM_BOT_TOKEN=123456:ABC...
    ANTHROPIC_API_KEY=sk-ant-...
```

### agenix

```nix
{
  age.secrets.kclaw-env.file = ./secrets/kclaw-env.age;

  services.kclaw.environmentFiles = [
    config.age.secrets.kclaw-env.path
  ];
}
```

### OAuth / Auth 种子

对于需要 OAuth 的平台（例如 Discord），使用 `authFile` 在首次部署时植入凭证：

```nix
{
  services.kclaw = {
    authFile = config.sops.secrets."kclaw/auth.json".path;
    # authFileForceOverwrite = true;  # 每次激活时覆盖
  };
}
```

文件仅在 `auth.json` 不存在时被复制（除非 `authFileForceOverwrite = true`）。运行时 OAuth 令牌刷新写入状态目录并在重建之间保留。

---

## 文档

`documents` 选项将文件安装到代理的工作目录（`workingDirectory`，代理将其作为 workspace 读取）。KClaw 按约定查找特定文件名：

- **`SOUL.md`** — 代理的系统提示/人格。KClaw 在启动时读取此文件，并将其用作持久指令，在所有对话中塑造其行为。
- **`USER.md`** — 代理正在与之交互的用户的相关上下文。
- 您在此处放置的任何其他文件对代理可见为 workspace 文件。

```nix
{
  services.kclaw.documents = {
    "SOUL.md" = ''
      You are a helpful research assistant specializing in NixOS packaging.
      Always cite sources and prefer reproducible solutions.
    '';
    "USER.md" = ./documents/USER.md;  # 路径引用，从 Nix store 复制
  };
}
```

值可以是内联字符串或路径引用。文件在每次 `nixos-rebuild switch` 时安装。

---

## MCP 服务器

`mcpServers` 选项声明式配置 [MCP（模型上下文协议）](https://modelcontextprotocol.io) 服务器。每个服务器使用 **stdio**（本地命令）或 **HTTP**（远程 URL）传输。

### Stdio 传输（本地服务器）

```nix
{
  services.kclaw.mcpServers = {
    filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/data/workspace" ];
    };
    github = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-github" ];
      env.GITHUB_PERSONAL_ACCESS_TOKEN = "\${GITHUB_TOKEN}"; # 从 .env 解析
    };
  };
}
```

:::tip
`env` 值中的环境变量在运行时从 `$KCLAW_HOME/.env` 解析。使用 `environmentFiles` 注入密钥——永远不要将令牌直接放在 Nix 配置中。
:::

### HTTP 传输（远程服务器）

```nix
{
  services.kclaw.mcpServers.remote-api = {
    url = "https://mcp.example.com/v1/mcp";
    headers.Authorization = "Bearer \${MCP_REMOTE_API_KEY}";
    timeout = 180;
  };
}
```

### 带 OAuth 的 HTTP 传输

对使用 OAuth 2.1 的服务器设置 `auth = "oauth"`。KClaw 实现完整的 PKCE 流程——元数据发现、动态客户端注册、令牌交换和自动刷新。

```nix
{
  services.kclaw.mcpServers.my-oauth-server = {
    url = "https://mcp.example.com/mcp";
    auth = "oauth";
  };
}
```

令牌存储在 `$KCLAW_HOME/mcp-tokens/<server-name>.json` 中，并在重启和重建之间保留。

<details>
<summary><strong>在无头服务器上进行初始 OAuth 授权</strong></summary>

首次 OAuth 授权需要基于浏览器的同意流程。在无头部署中，KClaw 将授权 URL 打印到 stdout/日志而不是打开浏览器。

**选项 A：交互式引导**——通过 `docker exec`（容器）或 `sudo -u kclaw`（原生）运行一次流程：

```bash
# 容器模式
docker exec -it kclaw \
  kclaw mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth

# 原生模式
sudo -u kclaw KCLAW_HOME=/var/lib/kclaw/.kclaw \
  kclaw mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
```

容器使用 `--network=host`，因此 `127.0.0.1` 上的 OAuth 回调监听器可从主机浏览器访问。

**选项 B：预植入令牌**——在工作站的浏览器中完成流程，然后复制令牌：

```bash
kclaw mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
scp ~/.kclaw/mcp-tokens/my-oauth-server{,.client}.json \
    server:/var/lib/kclaw/.kclaw/mcp-tokens/
# 确保：chown kclaw:kclaw, chmod 0600
```

</details>

### 采样（服务器发起的 LLM 请求）

一些 MCP 服务器可以向代理请求 LLM 完成：

```nix
{
  services.kclaw.mcpServers.analysis = {
    command = "npx";
    args = [ "-y" "analysis-server" ];
    sampling = {
      enabled = true;
      model = "google/gemini-3-flash";
      max_tokens_cap = 4096;
      timeout = 30;
      max_rpm = 10;
    };
  };
}
```

---

## 托管模式

当 kclaw 通过 NixOS 模块运行时，以下 CLI 命令被**阻止**，并显示指向 `configuration.nix` 的描述性错误：

| 阻止的命令 | 原因 |
|---|---|
| `kclaw setup` | 配置是声明式的——在 Nix 配置中编辑 `settings` |
| `kclaw config edit` | 配置是从 `settings` 生成的 |
| `kclaw config set <key> <value>` | 配置是从 `settings` 生成的 |
| `kclaw gateway install` | systemd 服务由 NixOS 管理 |
| `kclaw gateway uninstall` | systemd 服务由 NixOS 管理 |

这防止了 Nix 声明的内容与磁盘上的内容之间的漂移。检测使用两个信号：

1. **`KCLAW_MANAGED=true`** 环境变量——由 systemd 服务设置，对网关进程可见
2. **`KCLAW_HOME` 中的 `.managed`** 标记文件——由激活脚本设置，对交互式 shell 可见（例如，`docker exec -it kclaw kclaw config set ...` 也被阻止）

要更改配置，请编辑您的 Nix 配置并运行 `sudo nixos-rebuild switch`。

---

## 容器架构

:::info
本节仅在您使用 `container.enable = true` 时相关。对于原生模式部署，请跳过。
:::

启用容器模式时，kclaw 在持久化 Ubuntu 容器内运行，Nix 构建的二进制文件从主机以只读方式挂载：

```
主机                                    容器
────                                    ─────────
/nix/store/...-kclaw-0.1.0  ──►  /nix/store/... (ro)
/var/lib/kclaw/                    ──►  /data/          (rw)
  ├── current-package -> /nix/store/...    (符号链接，每次重建时更新)
  ├── .gc-root -> /nix/store/...           (防止 nix-collect-garbage)
  ├── .container-identity                  (sha256 哈希，触发重新创建)
  ├── .kclaw/                             (KCLAW_HOME)
  │   ├── .env                             (从 environment + environmentFiles 合并)
  │   ├── config.yaml                      (Nix 生成，由激活深度合并)
  │   ├── .managed                         (标记文件)
  │   ├── state.db, sessions/, memories/   (运行时状态)
  │   └── mcp-tokens/                      (MCP 服务器的 OAuth 令牌)
  ├── home/                                ──►  /home/kclaw    (rw)
  └── workspace/                           (MESSAGING_CWD)
      ├── SOUL.md                          (来自 documents 选项)
      └── (代理创建的文件)

容器可写层（apt/pip/npm）：   /usr, /usr/local, /tmp
```

Nix 构建的二进制文件在 Ubuntu 容器内工作，因为 `/nix/store` 是挂载的——它带来自己的解释器和所有依赖项，因此不依赖容器的系统库。容器入口点通过 `current-package` 符号链接解析：`/data/current-package/bin/kclaw gateway run --replace`。在 `nixos-rebuild switch` 时，只有符号链接被更新——容器继续运行。

### 什么跨什么持久化

| 事件 | 容器重新创建？ | `/data`（状态） | `/home/kclaw` | 可写层（`apt`/`pip`/`npm`） |
|---|---|---|---|---|
| `systemctl restart kclaw` | 否 | 持久化 | 持久化 | 持久化 |
| `nixos-rebuild switch`（代码更改） | 否（符号链接已更新） | 持久化 | 持久化 | 持久化 |
| 主机重启 | 否 | 持久化 | 持久化 | 持久化 |
| `nix-collect-garbage` | 否（GC 根） | 持久化 | 持久化 | 持久化 |
| 镜像更改（`container.image`） | **是** | 持久化 | 持久化 | **丢失** |
| 卷/选项更改 | **是** | 持久化 | 持久化 | **丢失** |
| `environment`/`environmentFiles` 更改 | 否 | 持久化 | 持久化 | 持久化 |

容器仅在其**身份哈希**更改时重新创建。哈希涵盖：架构版本、镜像、`extraVolumes`、`extraOptions` 和入口点脚本。更改环境变量、设置、文档或 kclaw 包本身**不会**触发重新创建。

:::warning 可写层丢失
当身份哈希更改时（镜像升级、新卷、新容器选项），容器被销毁并从 `container.image` 的全新拉取重新创建。可写层中的任何 `apt install`、`pip install` 或 `npm install` 包都会丢失。`/data` 和 `/home/kclaw` 中的状态被保留（这些是挂载绑定）。

如果代理依赖特定包，请考虑将它们烘焙到自定义镜像中（`container.image = "my-registry/kclaw-base:latest"`），或在代理的 SOUL.md 中编写它们的安装脚本。
:::

### GC 根保护

`preStart` 脚本在 `${stateDir}/.gc-root` 创建一个 GC 根，指向当前 kclaw 包。这防止 `nix-collect-garbage` 移除运行的二进制文件。如果 GC 根以某种方式损坏，重启服务会重新创建它。

---

## 开发

### 开发 Shell

flake 提供一个带有 Python 3.11、uv、Node.js 和所有运行时工具的开发 shell：

```bash
cd kclaw
nix develop

# Shell 提供：
#   - Python 3.11 + uv（首次进入时依赖安装到 .venv）
#   - Node.js 20、ripgrep、git、openssh、ffmpeg 在 PATH 上
#   - Stamp-file 优化：如果依赖未更改，重新进入几乎是即时的

kclaw setup
kclaw chat
```

### direnv（推荐）

包含的 `.envrc` 自动激活开发 shell：

```bash
cd kclaw
direnv allow    # 一次性的
# 后续进入几乎是即时的（stamp 文件跳过依赖安装）
```

### Flake 检查

flake 包含在 CI 中运行的构建时验证，也在本地运行：

```bash
# 运行所有检查
nix flake check

# 单个检查
nix build .#checks.x86_64-linux.package-contents   # 二进制文件存在 + 版本
nix build .#checks.x86_64-linux.entry-points-sync  # pyproject.toml ↔ Nix 包同步
nix build .#checks.x86_64-linux.cli-commands        # gateway/config 子命令
nix build .#checks.x86_64-linux.managed-guard       # KCLAW_MANAGED 阻止变更
nix build .#checks.x86_64-linux.bundled-skills      # 包中的技能存在
nix build .#checks.x86_64-linux.config-roundtrip    # 合并脚本保留用户键
```

<details>
<summary><strong>每个检查验证什么</strong></summary>

| 检查 | 测试内容 |
|---|---|
| `package-contents` | `kclaw` 和 `kclaw` 二进制文件存在且 `kclaw version` 运行 |
| `entry-points-sync` | `pyproject.toml` 中的每个 `[project.scripts]` 条目在 Nix 包中有一个包装二进制文件 |
| `cli-commands` | `kclaw --help` 暴露 `gateway` 和 `config` 子命令 |
| `managed-guard` | `KCLAW_MANAGED=true kclaw config set ...` 打印 NixOS 错误 |
| `bundled-skills` | 技能目录存在，包含 SKILL.md 文件，`KCLAW_BUNDLED_SKILLS` 在包装器中设置 |
| `config-roundtrip` | 7 个合并场景：全新安装、Nix 覆盖、用户键保留、混合合并、MCP 添加性合并、嵌套深度合并、幂等性 |

</details>

---

## 选项参考

### 核心

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `enable` | `bool` | `false` | 启用 kclaw 服务 |
| `package` | `package` | `kclaw` | 要使用的 kclaw 包 |
| `user` | `str` | `"kclaw"` | 系统用户 |
| `group` | `str` | `"kclaw"` | 系统组 |
| `createUser` | `bool` | `true` | 自动创建用户/组 |
| `stateDir` | `str` | `"/var/lib/kclaw"` | 状态目录（`KCLAW_HOME` 父级） |
| `workingDirectory` | `str` | `"${stateDir}/workspace"` | 代理工作目录（`MESSAGING_CWD`） |
| `addToSystemPackages` | `bool` | `false` | 将 `kclaw` CLI 添加到系统 PATH 并系统范围内设置 `KCLAW_HOME` |

### 配置

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `settings` | `attrs`（深度合并） | `{}` | 声明式配置，渲染为 `config.yaml`。支持任意嵌套；多个定义通过 `lib.recursiveUpdate` 合并 |
| `configFile` | `null` 或 `path` | `null` | 现有 `config.yaml` 的路径。如果设置，则完全覆盖 `settings` |

### 密钥和环境

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `environmentFiles` | `listOf str` | `[]` | 带有密钥的环境文件路径。在激活时合并到 `$KCLAW_HOME/.env` |
| `environment` | `attrsOf str` | `{}` | 非密钥环境变量。**在 Nix store 中可见**——不要将密钥放在这里 |
| `authFile` | `null` 或 `path` | `null` | OAuth 凭证种子。仅在首次部署时复制 |
| `authFileForceOverwrite` | `bool` | `false` | 每次激活时从 `authFile` 强制覆盖 `auth.json` |

### 文档

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `documents` | `attrsOf (either str path)` | `{}` | Workspace 文件。键是文件名，值是内联字符串或路径。在激活时安装到 `workingDirectory` |

### MCP 服务器

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `mcpServers` | `attrsOf submodule` | `{}` | MCP 服务器定义，合并到 `settings.mcp_servers` |
| `mcpServers.<name>.command` | `null` 或 `str` | `null` | 服务器命令（stdio 传输） |
| `mcpServers.<name>.args` | `listOf str` | `[]` | 命令参数 |
| `mcpServers.<name>.env` | `attrsOf str` | `{}` | 服务器进程的环境变量 |
| `mcpServers.<name>.url` | `null` 或 `str` | `null` | 服务器端点 URL（HTTP/StreamableHTTP 传输） |
| `mcpServers.<name>.headers` | `attrsOf str` | `{}` | HTTP 头，例如 `Authorization` |
| `mcpServers.<name>.auth` | `null` 或 `"oauth"` | `null` | 认证方法。`"oauth"` 启用 OAuth 2.1 PKCE |
| `mcpServers.<name>.enabled` | `bool` | `true` | 启用或禁用此服务器 |
| `mcpServers.<name>.timeout` | `null` 或 `int` | `null` | 工具调用超时秒数（默认：120） |
| `mcpServers.<name>.connect_timeout` | `null` 或 `int` | `null` | 连接超时秒数（默认：60） |
| `mcpServers.<name>.tools` | `null` 或 `submodule` | `null` | 工具过滤（`include`/`exclude` 列表） |
| `mcpServers.<name>.sampling` | `null` 或 `submodule` | `null` | 服务器发起的 LLM 请求的采样配置 |

### 服务行为

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `extraArgs` | `listOf str` | `[]` | `kclaw gateway` 的额外参数 |
| `extraPackages` | `listOf package` | `[]` | 服务 PATH 上的额外包（仅原生模式） |
| `restart` | `str` | `"always"` | systemd `Restart=` 策略 |
| `restartSec` | `int` | `5` | systemd `RestartSec=` 值 |

### 容器

| 选项 | 类型 | 默认 | 描述 |
|---|---|---|---|
| `container.enable` | `bool` | `false` | 启用 OCI 容器模式 |
| `container.backend` | `enum ["docker" "podman"]` | `"docker"` | 容器运行时 |
| `container.image` | `str` | `"ubuntu:24.04"` | 基础镜像（运行时拉取） |
| `container.extraVolumes` | `listOf str` | `[]` | 额外卷挂载（`host:container:mode`） |
| `container.extraOptions` | `listOf str` | `[]` | 传递给 `docker create` 的额外参数 |

---

## 目录布局

### 原生模式

```
/var/lib/kclaw/                     # stateDir（由 kclaw:kclaw 拥有，0750）
├── .kclaw/                         # KCLAW_HOME
│   ├── config.yaml                  # Nix 生成（每次重建时深度合并）
│   ├── .managed                     # 标记：CLI 配置变更被阻止
│   ├── .env                         # 从 environment + environmentFiles 合并
│   ├── auth.json                    # OAuth 凭证（种子，然后自我管理）
│   ├── gateway.pid
│   ├── state.db
│   ├── mcp-tokens/                  # MCP 服务器的 OAuth 令牌
│   ├── sessions/
│   ├── memories/
│   ├── skills/
│   ├── cron/
│   └── logs/
├── home/                            # 代理 HOME
└── workspace/                       # MESSAGING_CWD
    ├── SOUL.md                      # 来自 documents 选项
    └── (代理创建的文件)
```

### 容器模式

相同的布局，挂载到容器中：

| 容器路径 | 主机路径 | 模式 | 备注 |
|---|---|---|---|
| `/nix/store` | `/nix/store` | `ro` | KClaw 二进制文件 + 所有 Nix 依赖项 |
| `/data` | `/var/lib/kclaw` | `rw` | 所有状态、配置、workspace |
| `/home/kclaw` | `${stateDir}/home` | `rw` | 持久化代理主目录——`pip install --user`、工具缓存 |
| `/usr`、`/usr/local`、`/tmp` | （可写层） | `rw` | `apt`/`pip`/`npm` 安装——在重启之间持久化，重新创建时丢失 |

---

## 更新

```bash
# 更新 flake 输入
nix flake update kclaw --flake /etc/nixos

# 重建
sudo nixos-rebuild switch
```

在容器模式下，`current-package` 符号链接被更新，代理在重启时获取新的二进制文件。不重新创建容器，不丢失已安装的包。

---

## 故障排除

:::tip Podman 用户
下面的所有 `docker` 命令在 `podman` 中也一样工作。如果您设置 `container.backend = "podman"`，请相应替换。
:::

### 服务日志

```bash
# 两种模式使用相同的 systemd 单元
journalctl -u kclaw -f

# 容器模式：也可直接使用
docker logs -f kclaw
```

### 容器检查

```bash
systemctl status kclaw
docker ps -a --filter name=kclaw
docker inspect kclaw --format='{{.State.Status}}'
docker exec -it kclaw bash
docker exec kclaw readlink /data/current-package
docker exec kclaw cat /data/.container-identity
```

### 强制容器重新创建

如果您需要重置可写层（全新的 Ubuntu）：

```bash
sudo systemctl stop kclaw
docker rm -f kclaw
sudo rm /var/lib/kclaw/.container-identity
sudo systemctl start kclaw
```

### 验证密钥是否已加载

如果代理启动但无法与 LLM 提供商进行身份验证，请检查 `.env` 文件是否正确合并：

```bash
# 原生模式
sudo -u kclaw cat /var/lib/kclaw/.kclaw/.env

# 容器模式
docker exec kclaw cat /data/.kclaw/.env
```

### GC 根验证

```bash
nix-store --query --roots $(docker exec kclaw readlink /data/current-package)
```

### 常见问题

| 症状 | 原因 | 修复 |
|---|---|---|
| `Cannot save configuration: managed by NixOS` | CLI 守卫激活 | 编辑 `configuration.nix` 并 `nixos-rebuild switch` |
| 容器意外重新创建 | `extraVolumes`、`extraOptions` 或 `image` 更改 | 预期——可写层重置。重新安装包或使用自定义镜像 |
| `kclaw version` 显示旧版本 | 容器未重启 | `systemctl restart kclaw` |
| `/var/lib/kclaw` 上的权限被拒绝 | 状态目录是 `0750 kclaw:kclaw` | 使用 `docker exec` 或 `sudo -u kclaw` |
| `nix-collect-garbage` 移除了 kclaw | 缺少 GC 根 | 重启服务（preStart 重新创建 GC 根） |
