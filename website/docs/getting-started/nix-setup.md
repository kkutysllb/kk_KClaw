---
sidebar_position: 3
title: "Nix & NixOS Setup"
description: "Install and deploy KClaw Agent with Nix — from quick `nix run` to fully declarative NixOS module with container mode"
---

# Nix & NixOS Setup

KClaw Agent ships a Nix flake with three levels of integration:

| Level | Who it's for | What you get |
|-------|-------------|--------------|
| **`nix run` / `nix profile install`** | Any Nix user (macOS, Linux) | Pre-built binary with all deps — then use the standard CLI workflow |
| **NixOS module (native)** | NixOS server deployments | Declarative config, hardened systemd service, managed secrets |
| **NixOS module (container)** | Agents that need self-modification | Everything above, plus a persistent Ubuntu container where the agent can `apt`/`pip`/`npm install` |

:::info What's different from the standard install
The `curl | bash` installer manages Python, Node, and dependencies itself. The Nix flake replaces all of that — every Python dependency is a Nix derivation built by [uv2nix](https://github.com/pyproject-nix/uv2nix), and runtime tools (Node.js, git, ripgrep, ffmpeg) are wrapped into the binary's PATH. There is no runtime pip, no venv activation, no `npm install`.

**For non-NixOS users**, this only changes the install step. Everything after (`kclaw setup`, `kclaw gateway install`, config editing) works identically to the standard install.

**For NixOS module users**, the entire lifecycle is different: configuration lives in `configuration.nix`, secrets go through sops-nix/agenix, the service is a systemd unit, and CLI config commands are blocked. You manage kclaw the same way you manage any other NixOS service.
:::

## Prerequisites

- **Nix with flakes enabled** — [Determinate Nix](https://install.determinate.systems) recommended (enables flakes by default)
- **API keys** for the services you want to use (at minimum: an OpenRouter or Anthropic key)

---

## Quick Start (Any Nix User)

No clone needed. Nix fetches, builds, and runs everything:

```bash
# Run directly (builds on first use, cached after)
nix run github:NousResearch/kclaw -- setup
nix run github:NousResearch/kclaw -- chat

# Or install persistently
nix profile install github:NousResearch/kclaw
kclaw setup
kclaw chat
```

After `nix profile install`, `kclaw`, `kclaw`, and `kclaw-acp` are on your PATH. From here, the workflow is identical to the [standard installation](./installation.md) — `kclaw setup` walks you through provider selection, `kclaw gateway install` sets up a launchd (macOS) or systemd user service, and config lives in `~/.kclaw/`.

<details>
<summary><strong>Building from a local clone</strong></summary>

```bash
git clone https://github.com/NousResearch/kclaw.git
cd kclaw
nix build
./result/bin/kclaw setup
```

</details>

---

## NixOS Module

The flake exports `nixosModules.default` — a full NixOS service module that declaratively manages user creation, directories, config generation, secrets, documents, and service lifecycle.

:::note
This module requires NixOS. For non-NixOS systems (macOS, other Linux distros), use `nix profile install` and the standard CLI workflow above.
:::

### Add the Flake Input

```nix
# /etc/nixos/flake.nix (or your system flake)
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

### Minimal Configuration

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

That's it. `nixos-rebuild switch` creates the `kclaw` user, generates `config.yaml`, wires up secrets, and starts the gateway — a long-running service that connects the agent to messaging platforms (Telegram, Discord, etc.) and listens for incoming messages.

:::warning Secrets are required
The `environmentFiles` line above assumes you have [sops-nix](https://github.com/Mic92/sops-nix) or [agenix](https://github.com/ryantm/agenix) configured. The file should contain at least one LLM provider key (e.g., `OPENROUTER_API_KEY=sk-or-...`). See [Secrets Management](#secrets-management) for full setup. If you don't have a secrets manager yet, you can use a plain file as a starting point — just ensure it's not world-readable:

```bash
echo "OPENROUTER_API_KEY=sk-or-your-key" | sudo install -m 0600 -o kclaw /dev/stdin /var/lib/kclaw/env
```

```nix
services.kclaw.environmentFiles = [ "/var/lib/kclaw/env" ];
```
:::

:::tip addToSystemPackages
Setting `addToSystemPackages = true` does two things: puts the `kclaw` CLI on your system PATH **and** sets `KCLAW_HOME` system-wide so the interactive CLI shares state (sessions, skills, cron) with the gateway service. Without it, running `kclaw` in your shell creates a separate `~/.kclaw/` directory.
:::

### Verify It Works

After `nixos-rebuild switch`, check that the service is running:

```bash
# Check service status
systemctl status kclaw

# Watch logs (Ctrl+C to stop)
journalctl -u kclaw -f

# If addToSystemPackages is true, test the CLI
kclaw version
kclaw config       # shows the generated config
```

### Choosing a Deployment Mode

The module supports two modes, controlled by `container.enable`:

| | **Native** (default) | **Container** |
|---|---|---|
| How it runs | Hardened systemd service on the host | Persistent Ubuntu container with `/nix/store` bind-mounted |
| Security | `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp` | Container isolation, runs as unprivileged user inside |
| Agent can self-install packages | No — only tools on the Nix-provided PATH | Yes — `apt`, `pip`, `npm` installs persist across restarts |
| Config surface | Same | Same |
| When to choose | Standard deployments, maximum security, reproducibility | Agent needs runtime package installation, mutable environment, experimental tools |

To enable container mode, add one line:

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
Container mode auto-enables `virtualisation.docker.enable` via `mkDefault`. If you use Podman instead, set `container.backend = "podman"` and `virtualisation.docker.enable = false`.
:::

---

## Configuration

### Declarative Settings

The `settings` option accepts an arbitrary attrset that is rendered as `config.yaml`. It supports deep merging across multiple module definitions (via `lib.recursiveUpdate`), so you can split config across files:

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

Both are deep-merged at evaluation time. Nix-declared keys always win over keys in an existing `config.yaml` on disk, but **user-added keys that Nix doesn't touch are preserved**. This means if the agent or a manual edit adds keys like `skills.disabled` or `streaming.enabled`, they survive `nixos-rebuild switch`.

:::note Model naming
`settings.model.default` uses the model identifier your provider expects. With [OpenRouter](https://openrouter.ai) (the default), these look like `"anthropic/claude-sonnet-4"` or `"google/gemini-3-flash"`. If you're using a provider directly (Anthropic, OpenAI), set `settings.model.base_url` to point at their API and use their native model IDs (e.g., `"claude-sonnet-4-20250514"`). When no `base_url` is set, KClaw defaults to OpenRouter.
:::

:::tip Discovering available config keys
Run `nix build .#configKeys && cat result` to see every leaf config key extracted from Python's `DEFAULT_CONFIG`. You can paste your existing `config.yaml` into the `settings` attrset — the structure maps 1:1.
:::

<details>
<summary><strong>Full example: all commonly customized settings</strong></summary>

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

### Escape Hatch: Bring Your Own Config

If you'd rather manage `config.yaml` entirely outside Nix, use `configFile`:

```nix
services.kclaw.configFile = /etc/kclaw/config.yaml;
```

This bypasses `settings` entirely — no merge, no generation. The file is copied as-is to `$KCLAW_HOME/config.yaml` on each activation.

### Customization Cheatsheet

Quick reference for the most common things Nix users want to customize:

| I want to... | Option | Example |
|---|---|---|
| Change the LLM model | `settings.model.default` | `"anthropic/claude-sonnet-4"` |
| Use a different provider endpoint | `settings.model.base_url` | `"https://openrouter.ai/api/v1"` |
| Add API keys | `environmentFiles` | `[ config.sops.secrets."kclaw-env".path ]` |
| Give the agent a personality | `documents."SOUL.md"` | `builtins.readFile ./my-soul.md` |
| Add MCP tool servers | `mcpServers.<name>` | See [MCP Servers](#mcp-servers) |
| Mount host directories into container | `container.extraVolumes` | `[ "/data:/data:rw" ]` |
| Pass GPU access to container | `container.extraOptions` | `[ "--gpus" "all" ]` |
| Use Podman instead of Docker | `container.backend` | `"podman"` |
| Add tools to the service PATH (native only) | `extraPackages` | `[ pkgs.pandoc pkgs.imagemagick ]` |
| Use a custom base image | `container.image` | `"ubuntu:24.04"` |
| Override the kclaw package | `package` | `inputs.kclaw.packages.${system}.default.override { ... }` |
| Change state directory | `stateDir` | `"/opt/kclaw"` |
| Set the agent's working directory | `workingDirectory` | `"/home/user/projects"` |

---

## Secrets Management

:::danger Never put API keys in `settings` or `environment`
Values in Nix expressions end up in `/nix/store`, which is world-readable. Always use `environmentFiles` with a secrets manager.
:::

Both `environment` (non-secret vars) and `environmentFiles` (secret files) are merged into `$KCLAW_HOME/.env` at activation time (`nixos-rebuild switch`). KClaw reads this file on every startup, so changes take effect with a `systemctl restart kclaw` — no container recreation needed.

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

The secrets file contains key-value pairs:

```yaml
# secrets/kclaw.yaml (encrypted with sops)
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

### OAuth / Auth Seeding

For platforms requiring OAuth (e.g., Discord), use `authFile` to seed credentials on first deploy:

```nix
{
  services.kclaw = {
    authFile = config.sops.secrets."kclaw/auth.json".path;
    # authFileForceOverwrite = true;  # overwrite on every activation
  };
}
```

The file is only copied if `auth.json` doesn't already exist (unless `authFileForceOverwrite = true`). Runtime OAuth token refreshes are written to the state directory and preserved across rebuilds.

---

## Documents

The `documents` option installs files into the agent's working directory (the `workingDirectory`, which the agent reads as its workspace). KClaw looks for specific filenames by convention:

- **`SOUL.md`** — the agent's system prompt / personality. KClaw reads this on startup and uses it as persistent instructions that shape its behavior across all conversations.
- **`USER.md`** — context about the user the agent is interacting with.
- Any other files you place here are visible to the agent as workspace files.

```nix
{
  services.kclaw.documents = {
    "SOUL.md" = ''
      You are a helpful research assistant specializing in NixOS packaging.
      Always cite sources and prefer reproducible solutions.
    '';
    "USER.md" = ./documents/USER.md;  # path reference, copied from Nix store
  };
}
```

Values can be inline strings or path references. Files are installed on every `nixos-rebuild switch`.

---

## MCP Servers

The `mcpServers` option declaratively configures [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers. Each server uses either **stdio** (local command) or **HTTP** (remote URL) transport.

### Stdio Transport (Local Servers)

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
      env.GITHUB_PERSONAL_ACCESS_TOKEN = "\${GITHUB_TOKEN}"; # resolved from .env
    };
  };
}
```

:::tip
Environment variables in `env` values are resolved from `$KCLAW_HOME/.env` at runtime. Use `environmentFiles` to inject secrets — never put tokens directly in Nix config.
:::

### HTTP Transport (Remote Servers)

```nix
{
  services.kclaw.mcpServers.remote-api = {
    url = "https://mcp.example.com/v1/mcp";
    headers.Authorization = "Bearer \${MCP_REMOTE_API_KEY}";
    timeout = 180;
  };
}
```

### HTTP Transport with OAuth

Set `auth = "oauth"` for servers using OAuth 2.1. KClaw implements the full PKCE flow — metadata discovery, dynamic client registration, token exchange, and automatic refresh.

```nix
{
  services.kclaw.mcpServers.my-oauth-server = {
    url = "https://mcp.example.com/mcp";
    auth = "oauth";
  };
}
```

Tokens are stored in `$KCLAW_HOME/mcp-tokens/<server-name>.json` and persist across restarts and rebuilds.

<details>
<summary><strong>Initial OAuth authorization on headless servers</strong></summary>

The first OAuth authorization requires a browser-based consent flow. In a headless deployment, KClaw prints the authorization URL to stdout/logs instead of opening a browser.

**Option A: Interactive bootstrap** — run the flow once via `docker exec` (container) or `sudo -u kclaw` (native):

```bash
# Container mode
docker exec -it kclaw \
  kclaw mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth

# Native mode
sudo -u kclaw KCLAW_HOME=/var/lib/kclaw/.kclaw \
  kclaw mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
```

The container uses `--network=host`, so the OAuth callback listener on `127.0.0.1` is reachable from the host browser.

**Option B: Pre-seed tokens** — complete the flow on a workstation, then copy tokens:

```bash
kclaw mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
scp ~/.kclaw/mcp-tokens/my-oauth-server{,.client}.json \
    server:/var/lib/kclaw/.kclaw/mcp-tokens/
# Ensure: chown kclaw:kclaw, chmod 0600
```

</details>

### Sampling (Server-Initiated LLM Requests)

Some MCP servers can request LLM completions from the agent:

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

## Managed Mode

When kclaw runs via the NixOS module, the following CLI commands are **blocked** with a descriptive error pointing you to `configuration.nix`:

| Blocked command | Why |
|---|---|
| `kclaw setup` | Config is declarative — edit `settings` in your Nix config |
| `kclaw config edit` | Config is generated from `settings` |
| `kclaw config set <key> <value>` | Config is generated from `settings` |
| `kclaw gateway install` | The systemd service is managed by NixOS |
| `kclaw gateway uninstall` | The systemd service is managed by NixOS |

This prevents drift between what Nix declares and what's on disk. Detection uses two signals:

1. **`KCLAW_MANAGED=true`** environment variable — set by the systemd service, visible to the gateway process
2. **`.managed` marker file** in `KCLAW_HOME` — set by the activation script, visible to interactive shells (e.g., `docker exec -it kclaw kclaw config set ...` is also blocked)

To change configuration, edit your Nix config and run `sudo nixos-rebuild switch`.

---

## Container Architecture

:::info
This section is only relevant if you're using `container.enable = true`. Skip it for native mode deployments.
:::

When container mode is enabled, kclaw runs inside a persistent Ubuntu container with the Nix-built binary bind-mounted read-only from the host:

```
Host                                    Container
────                                    ─────────
/nix/store/...-kclaw-0.1.0  ──►  /nix/store/... (ro)
/var/lib/kclaw/                    ──►  /data/          (rw)
  ├── current-package -> /nix/store/...    (symlink, updated each rebuild)
  ├── .gc-root -> /nix/store/...           (prevents nix-collect-garbage)
  ├── .container-identity                  (sha256 hash, triggers recreation)
  ├── .kclaw/                             (KCLAW_HOME)
  │   ├── .env                             (merged from environment + environmentFiles)
  │   ├── config.yaml                      (Nix-generated, deep-merged by activation)
  │   ├── .managed                         (marker file)
  │   ├── state.db, sessions/, memories/   (runtime state)
  │   └── mcp-tokens/                      (OAuth tokens for MCP servers)
  ├── home/                                ──►  /home/kclaw    (rw)
  └── workspace/                           (MESSAGING_CWD)
      ├── SOUL.md                          (from documents option)
      └── (agent-created files)

Container writable layer (apt/pip/npm):   /usr, /usr/local, /tmp
```

The Nix-built binary works inside the Ubuntu container because `/nix/store` is bind-mounted — it brings its own interpreter and all dependencies, so there's no reliance on the container's system libraries. The container entrypoint resolves through a `current-package` symlink: `/data/current-package/bin/kclaw gateway run --replace`. On `nixos-rebuild switch`, only the symlink is updated — the container keeps running.

### What Persists Across What

| Event | Container recreated? | `/data` (state) | `/home/kclaw` | Writable layer (`apt`/`pip`/`npm`) |
|---|---|---|---|---|
| `systemctl restart kclaw` | No | Persists | Persists | Persists |
| `nixos-rebuild switch` (code change) | No (symlink updated) | Persists | Persists | Persists |
| Host reboot | No | Persists | Persists | Persists |
| `nix-collect-garbage` | No (GC root) | Persists | Persists | Persists |
| Image change (`container.image`) | **Yes** | Persists | Persists | **Lost** |
| Volume/options change | **Yes** | Persists | Persists | **Lost** |
| `environment`/`environmentFiles` change | No | Persists | Persists | Persists |

The container is only recreated when its **identity hash** changes. The hash covers: schema version, image, `extraVolumes`, `extraOptions`, and the entrypoint script. Changes to environment variables, settings, documents, or the kclaw package itself do **not** trigger recreation.

:::warning Writable layer loss
When the identity hash changes (image upgrade, new volumes, new container options), the container is destroyed and recreated from a fresh pull of `container.image`. Any `apt install`, `pip install`, or `npm install` packages in the writable layer are lost. State in `/data` and `/home/kclaw` is preserved (these are bind mounts).

If the agent relies on specific packages, consider baking them into a custom image (`container.image = "my-registry/kclaw-base:latest"`) or scripting their installation in the agent's SOUL.md.
:::

### GC Root Protection

The `preStart` script creates a GC root at `${stateDir}/.gc-root` pointing to the current kclaw package. This prevents `nix-collect-garbage` from removing the running binary. If the GC root somehow breaks, restarting the service recreates it.

---

## Development

### Dev Shell

The flake provides a development shell with Python 3.11, uv, Node.js, and all runtime tools:

```bash
cd kclaw
nix develop

# Shell provides:
#   - Python 3.11 + uv (deps installed into .venv on first entry)
#   - Node.js 20, ripgrep, git, openssh, ffmpeg on PATH
#   - Stamp-file optimization: re-entry is near-instant if deps haven't changed

kclaw setup
kclaw chat
```

### direnv (Recommended)

The included `.envrc` activates the dev shell automatically:

```bash
cd kclaw
direnv allow    # one-time
# Subsequent entries are near-instant (stamp file skips dep install)
```

### Flake Checks

The flake includes build-time verification that runs in CI and locally:

```bash
# Run all checks
nix flake check

# Individual checks
nix build .#checks.x86_64-linux.package-contents   # binaries exist + version
nix build .#checks.x86_64-linux.entry-points-sync  # pyproject.toml ↔ Nix package sync
nix build .#checks.x86_64-linux.cli-commands        # gateway/config subcommands
nix build .#checks.x86_64-linux.managed-guard       # KCLAW_MANAGED blocks mutation
nix build .#checks.x86_64-linux.bundled-skills      # skills present in package
nix build .#checks.x86_64-linux.config-roundtrip    # merge script preserves user keys
```

<details>
<summary><strong>What each check verifies</strong></summary>

| Check | What it tests |
|---|---|
| `package-contents` | `kclaw` and `kclaw` binaries exist and `kclaw version` runs |
| `entry-points-sync` | Every `[project.scripts]` entry in `pyproject.toml` has a wrapped binary in the Nix package |
| `cli-commands` | `kclaw --help` exposes `gateway` and `config` subcommands |
| `managed-guard` | `KCLAW_MANAGED=true kclaw config set ...` prints the NixOS error |
| `bundled-skills` | Skills directory exists, contains SKILL.md files, `KCLAW_BUNDLED_SKILLS` is set in wrapper |
| `config-roundtrip` | 7 merge scenarios: fresh install, Nix override, user key preservation, mixed merge, MCP additive merge, nested deep merge, idempotency |

</details>

---

## Options Reference

### Core

| Option | Type | Default | Description |
|---|---|---|---|
| `enable` | `bool` | `false` | Enable the kclaw service |
| `package` | `package` | `kclaw` | The kclaw package to use |
| `user` | `str` | `"kclaw"` | System user |
| `group` | `str` | `"kclaw"` | System group |
| `createUser` | `bool` | `true` | Auto-create user/group |
| `stateDir` | `str` | `"/var/lib/kclaw"` | State directory (`KCLAW_HOME` parent) |
| `workingDirectory` | `str` | `"${stateDir}/workspace"` | Agent working directory (`MESSAGING_CWD`) |
| `addToSystemPackages` | `bool` | `false` | Add `kclaw` CLI to system PATH and set `KCLAW_HOME` system-wide |

### Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `settings` | `attrs` (deep-merged) | `{}` | Declarative config rendered as `config.yaml`. Supports arbitrary nesting; multiple definitions are merged via `lib.recursiveUpdate` |
| `configFile` | `null` or `path` | `null` | Path to an existing `config.yaml`. Overrides `settings` entirely if set |

### Secrets & Environment

| Option | Type | Default | Description |
|---|---|---|---|
| `environmentFiles` | `listOf str` | `[]` | Paths to env files with secrets. Merged into `$KCLAW_HOME/.env` at activation time |
| `environment` | `attrsOf str` | `{}` | Non-secret env vars. **Visible in Nix store** — do not put secrets here |
| `authFile` | `null` or `path` | `null` | OAuth credentials seed. Only copied on first deploy |
| `authFileForceOverwrite` | `bool` | `false` | Always overwrite `auth.json` from `authFile` on activation |

### Documents

| Option | Type | Default | Description |
|---|---|---|---|
| `documents` | `attrsOf (either str path)` | `{}` | Workspace files. Keys are filenames, values are inline strings or paths. Installed into `workingDirectory` on activation |

### MCP Servers

| Option | Type | Default | Description |
|---|---|---|---|
| `mcpServers` | `attrsOf submodule` | `{}` | MCP server definitions, merged into `settings.mcp_servers` |
| `mcpServers.<name>.command` | `null` or `str` | `null` | Server command (stdio transport) |
| `mcpServers.<name>.args` | `listOf str` | `[]` | Command arguments |
| `mcpServers.<name>.env` | `attrsOf str` | `{}` | Environment variables for the server process |
| `mcpServers.<name>.url` | `null` or `str` | `null` | Server endpoint URL (HTTP/StreamableHTTP transport) |
| `mcpServers.<name>.headers` | `attrsOf str` | `{}` | HTTP headers, e.g. `Authorization` |
| `mcpServers.<name>.auth` | `null` or `"oauth"` | `null` | Authentication method. `"oauth"` enables OAuth 2.1 PKCE |
| `mcpServers.<name>.enabled` | `bool` | `true` | Enable or disable this server |
| `mcpServers.<name>.timeout` | `null` or `int` | `null` | Tool call timeout in seconds (default: 120) |
| `mcpServers.<name>.connect_timeout` | `null` or `int` | `null` | Connection timeout in seconds (default: 60) |
| `mcpServers.<name>.tools` | `null` or `submodule` | `null` | Tool filtering (`include`/`exclude` lists) |
| `mcpServers.<name>.sampling` | `null` or `submodule` | `null` | Sampling config for server-initiated LLM requests |

### Service Behavior

| Option | Type | Default | Description |
|---|---|---|---|
| `extraArgs` | `listOf str` | `[]` | Extra args for `kclaw gateway` |
| `extraPackages` | `listOf package` | `[]` | Extra packages on service PATH (native mode only) |
| `restart` | `str` | `"always"` | systemd `Restart=` policy |
| `restartSec` | `int` | `5` | systemd `RestartSec=` value |

### Container

| Option | Type | Default | Description |
|---|---|---|---|
| `container.enable` | `bool` | `false` | Enable OCI container mode |
| `container.backend` | `enum ["docker" "podman"]` | `"docker"` | Container runtime |
| `container.image` | `str` | `"ubuntu:24.04"` | Base image (pulled at runtime) |
| `container.extraVolumes` | `listOf str` | `[]` | Extra volume mounts (`host:container:mode`) |
| `container.extraOptions` | `listOf str` | `[]` | Extra args passed to `docker create` |

---

## Directory Layout

### Native Mode

```
/var/lib/kclaw/                     # stateDir (owned by kclaw:kclaw, 0750)
├── .kclaw/                         # KCLAW_HOME
│   ├── config.yaml                  # Nix-generated (deep-merged each rebuild)
│   ├── .managed                     # Marker: CLI config mutation blocked
│   ├── .env                         # Merged from environment + environmentFiles
│   ├── auth.json                    # OAuth credentials (seeded, then self-managed)
│   ├── gateway.pid
│   ├── state.db
│   ├── mcp-tokens/                  # OAuth tokens for MCP servers
│   ├── sessions/
│   ├── memories/
│   ├── skills/
│   ├── cron/
│   └── logs/
├── home/                            # Agent HOME
└── workspace/                       # MESSAGING_CWD
    ├── SOUL.md                      # From documents option
    └── (agent-created files)
```

### Container Mode

Same layout, mounted into the container:

| Container path | Host path | Mode | Notes |
|---|---|---|---|
| `/nix/store` | `/nix/store` | `ro` | KClaw binary + all Nix deps |
| `/data` | `/var/lib/kclaw` | `rw` | All state, config, workspace |
| `/home/kclaw` | `${stateDir}/home` | `rw` | Persistent agent home — `pip install --user`, tool caches |
| `/usr`, `/usr/local`, `/tmp` | (writable layer) | `rw` | `apt`/`pip`/`npm` installs — persists across restarts, lost on recreation |

---

## Updating

```bash
# Update the flake input
nix flake update kclaw --flake /etc/nixos

# Rebuild
sudo nixos-rebuild switch
```

In container mode, the `current-package` symlink is updated and the agent picks up the new binary on restart. No container recreation, no loss of installed packages.

---

## Troubleshooting

:::tip Podman users
All `docker` commands below work the same with `podman`. Substitute accordingly if you set `container.backend = "podman"`.
:::

### Service Logs

```bash
# Both modes use the same systemd unit
journalctl -u kclaw -f

# Container mode: also available directly
docker logs -f kclaw
```

### Container Inspection

```bash
systemctl status kclaw
docker ps -a --filter name=kclaw
docker inspect kclaw --format='{{.State.Status}}'
docker exec -it kclaw bash
docker exec kclaw readlink /data/current-package
docker exec kclaw cat /data/.container-identity
```

### Force Container Recreation

If you need to reset the writable layer (fresh Ubuntu):

```bash
sudo systemctl stop kclaw
docker rm -f kclaw
sudo rm /var/lib/kclaw/.container-identity
sudo systemctl start kclaw
```

### Verify Secrets Are Loaded

If the agent starts but can't authenticate with the LLM provider, check that the `.env` file was merged correctly:

```bash
# Native mode
sudo -u kclaw cat /var/lib/kclaw/.kclaw/.env

# Container mode
docker exec kclaw cat /data/.kclaw/.env
```

### GC Root Verification

```bash
nix-store --query --roots $(docker exec kclaw readlink /data/current-package)
```

### Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot save configuration: managed by NixOS` | CLI guards active | Edit `configuration.nix` and `nixos-rebuild switch` |
| Container recreated unexpectedly | `extraVolumes`, `extraOptions`, or `image` changed | Expected — writable layer resets. Reinstall packages or use a custom image |
| `kclaw version` shows old version | Container not restarted | `systemctl restart kclaw` |
| Permission denied on `/var/lib/kclaw` | State dir is `0750 kclaw:kclaw` | Use `docker exec` or `sudo -u kclaw` |
| `nix-collect-garbage` removed kclaw | GC root missing | Restart the service (preStart recreates the GC root) |
