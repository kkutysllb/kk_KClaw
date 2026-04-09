# nix/nixosModules.nix — NixOS module for kclaw
#
# Two modes:
#   container.enable = false (default) → native systemd service
#   container.enable = true            → OCI container (persistent writable layer)
#
# Container mode: kclaw runs from /nix/store bind-mounted read-only into a
# plain Ubuntu container. The writable layer (apt/pip/npm installs) persists
# across restarts and agent updates. Only image/volume/options changes trigger
# container recreation. Environment variables are written to $KCLAW_HOME/.env
# and read by kclaw at startup — no container recreation needed for env changes.
#
# Tool resolution: the kclaw wrapper uses --suffix PATH for nix store tools,
# so apt/uv-installed versions take priority. The container entrypoint provisions
# extensible tools on first boot: nodejs/npm via apt, uv via curl, and a Python
# 3.11 venv (bootstrapped entirely by uv) at ~/.venv with pip seeded. Agents get
# writable tool prefixes for npm i -g, pip install, uv tool install, etc.
#
# Usage:
#   services.kclaw = {
#     enable = true;
#     settings.model = "anthropic/claude-sonnet-4";
#     environmentFiles = [ config.sops.secrets."kclaw/env".path ];
#   };
#
{ inputs, ... }: {
  flake.nixosModules.default = { config, lib, pkgs, ... }:

  let
    cfg = config.services.kclaw;
    kclaw = inputs.self.packages.${pkgs.system}.default;

    # Deep-merge config type (from 0xrsydn/nix-kclaw)
    deepConfigType = lib.types.mkOptionType {
      name = "kclaw-config-attrs";
      description = "KClaw YAML config (attrset), merged deeply via lib.recursiveUpdate.";
      check = builtins.isAttrs;
      merge = _loc: defs: lib.foldl' lib.recursiveUpdate { } (map (d: d.value) defs);
    };

    # Generate config.yaml from Nix attrset (YAML is a superset of JSON)
    configJson = builtins.toJSON cfg.settings;
    generatedConfigFile = pkgs.writeText "kclaw-config.yaml" configJson;
    configFile = if cfg.configFile != null then cfg.configFile else generatedConfigFile;

    configMergeScript = pkgs.callPackage ./configMergeScript.nix { };

    # Generate .env from non-secret environment attrset
    envFileContent = lib.concatStringsSep "\n" (
      lib.mapAttrsToList (k: v: "${k}=${v}") cfg.environment
    );
    # Build documents derivation (from 0xrsydn)
    documentDerivation = pkgs.runCommand "kclaw-documents" { } (
      ''
        mkdir -p $out
      '' + lib.concatStringsSep "\n" (
        lib.mapAttrsToList (name: value:
          if builtins.isPath value || lib.isStorePath value
          then "cp ${value} $out/${name}"
          else "cat > $out/${name} <<'KCLAW_DOC_EOF'\n${value}\nKCLAW_DOC_EOF"
        ) cfg.documents
      )
    );

    containerName = "kclaw";
    containerDataDir = "/data";     # stateDir mount point inside container
    containerHomeDir = "/home/kclaw";

    # ── Container mode helpers ──────────────────────────────────────────
    containerBin = if cfg.container.backend == "docker"
      then "${pkgs.docker}/bin/docker"
      else "${pkgs.podman}/bin/podman";

    # Runs as root inside the container on every start. Provisions the
    # kclaw user + sudo on first boot (writable layer persists), then
    # drops privileges. Supports arbitrary base images (Debian, Alpine, etc).
    containerEntrypoint = pkgs.writeShellScript "kclaw-container-entrypoint" ''
      set -eu

      KCLAW_UID="''${KCLAW_UID:?KCLAW_UID must be set}"
      KCLAW_GID="''${KCLAW_GID:?KCLAW_GID must be set}"

      # ── Group: ensure a group with GID=$KCLAW_GID exists ──
      # Check by GID (not name) to avoid collisions with pre-existing groups
      # (e.g. GID 100 = "users" on Ubuntu)
      EXISTING_GROUP=$(getent group "$KCLAW_GID" 2>/dev/null | cut -d: -f1 || true)
      if [ -n "$EXISTING_GROUP" ]; then
        GROUP_NAME="$EXISTING_GROUP"
      else
        GROUP_NAME="kclaw"
        if command -v groupadd >/dev/null 2>&1; then
          groupadd -g "$KCLAW_GID" "$GROUP_NAME"
        elif command -v addgroup >/dev/null 2>&1; then
          addgroup -g "$KCLAW_GID" "$GROUP_NAME" 2>/dev/null || true
        fi
      fi

      # ── User: ensure a user with UID=$KCLAW_UID exists ──
      PASSWD_ENTRY=$(getent passwd "$KCLAW_UID" 2>/dev/null || true)
      if [ -n "$PASSWD_ENTRY" ]; then
        TARGET_USER=$(echo "$PASSWD_ENTRY" | cut -d: -f1)
        TARGET_HOME=$(echo "$PASSWD_ENTRY" | cut -d: -f6)
      else
        TARGET_USER="kclaw"
        TARGET_HOME="/home/kclaw"
        if command -v useradd >/dev/null 2>&1; then
          useradd -u "$KCLAW_UID" -g "$KCLAW_GID" -m -d "$TARGET_HOME" -s /bin/bash "$TARGET_USER"
        elif command -v adduser >/dev/null 2>&1; then
          adduser -u "$KCLAW_UID" -D -h "$TARGET_HOME" -s /bin/sh -G "$GROUP_NAME" "$TARGET_USER" 2>/dev/null || true
        fi
      fi
      mkdir -p "$TARGET_HOME"
      chown "$KCLAW_UID:$KCLAW_GID" "$TARGET_HOME"
      chmod 0750 "$TARGET_HOME"

      # Ensure KCLAW_HOME is owned by the target user
      if [ -n "''${KCLAW_HOME:-}" ] && [ -d "$KCLAW_HOME" ]; then
        chown -R "$KCLAW_UID:$KCLAW_GID" "$KCLAW_HOME"
      fi

      # ── Provision apt packages (first boot only, cached in writable layer) ──
      # sudo: agent self-modification
      # nodejs/npm: writable node so npm i -g works (nix store copies are read-only)
      # curl: needed for uv installer
      if [ ! -f /var/lib/kclaw-tools-provisioned ] && command -v apt-get >/dev/null 2>&1; then
        echo "First boot: provisioning agent tools..."
        apt-get update -qq
        apt-get install -y -qq sudo nodejs npm curl
        touch /var/lib/kclaw-tools-provisioned
      fi

      if command -v sudo >/dev/null 2>&1 && [ ! -f /etc/sudoers.d/kclaw ]; then
        mkdir -p /etc/sudoers.d
        echo "$TARGET_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/kclaw
        chmod 0440 /etc/sudoers.d/kclaw
      fi

      # uv (Python manager) — not in Ubuntu repos, retry-safe outside the sentinel
      if ! command -v uv >/dev/null 2>&1 && [ ! -x "$TARGET_HOME/.local/bin/uv" ] && command -v curl >/dev/null 2>&1; then
        su -s /bin/sh "$TARGET_USER" -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' || true
      fi

      # Python 3.11 venv — gives the agent a writable Python with pip.
      # Uses uv to install Python 3.11 (Ubuntu 24.04 ships 3.12).
      # --seed includes pip/setuptools so bare `pip install` works.
      _UV_BIN="$TARGET_HOME/.local/bin/uv"
      if [ ! -d "$TARGET_HOME/.venv" ] && [ -x "$_UV_BIN" ]; then
        su -s /bin/sh "$TARGET_USER" -c "
          export PATH=\"\$HOME/.local/bin:\$PATH\"
          uv python install 3.11
          uv venv --python 3.11 --seed \"\$HOME/.venv\"
        " || true
      fi

      # Put the agent venv first on PATH so python/pip resolve to writable copies
      if [ -d "$TARGET_HOME/.venv/bin" ]; then
        export PATH="$TARGET_HOME/.venv/bin:$PATH"
      fi

      if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid="$KCLAW_UID" --regid="$KCLAW_GID" --init-groups "$@"
      elif command -v su >/dev/null 2>&1; then
        exec su -s /bin/sh "$TARGET_USER" -c 'exec "$0" "$@"' -- "$@"
      else
        echo "WARNING: no privilege-drop tool (setpriv/su), running as root" >&2
        exec "$@"
      fi
    '';

    # Identity hash — only recreate container when structural config changes.
    # Package and entrypoint use stable symlinks (current-package, current-entrypoint)
    # so they can update without recreation. Env vars go through $KCLAW_HOME/.env.
    containerIdentity = builtins.hashString "sha256" (builtins.toJSON {
      schema = 3; # bump when identity inputs change
      image = cfg.container.image;
      extraVolumes = cfg.container.extraVolumes;
      extraOptions = cfg.container.extraOptions;
    });

    identityFile = "${cfg.stateDir}/.container-identity";

    # Default: /var/lib/kclaw/workspace → /data/workspace.
    # Custom paths outside stateDir pass through unchanged (user must add extraVolumes).
    containerWorkDir =
      if lib.hasPrefix "${cfg.stateDir}/" cfg.workingDirectory
      then "${containerDataDir}/${lib.removePrefix "${cfg.stateDir}/" cfg.workingDirectory}"
      else cfg.workingDirectory;

  in {
    options.services.kclaw = with lib; {
      enable = mkEnableOption "KClaw Agent gateway service";

      # ── Package ──────────────────────────────────────────────────────────
      package = mkOption {
        type = types.package;
        default = kclaw;
        description = "The kclaw package to use.";
      };

      # ── Service identity ─────────────────────────────────────────────────
      user = mkOption {
        type = types.str;
        default = "kclaw";
        description = "System user running the gateway.";
      };

      group = mkOption {
        type = types.str;
        default = "kclaw";
        description = "System group running the gateway.";
      };

      createUser = mkOption {
        type = types.bool;
        default = true;
        description = "Create the user/group automatically.";
      };

      # ── Directories ──────────────────────────────────────────────────────
      stateDir = mkOption {
        type = types.str;
        default = "/var/lib/kclaw";
        description = "State directory. Contains .kclaw/ subdir (KCLAW_HOME).";
      };

      workingDirectory = mkOption {
        type = types.str;
        default = "${cfg.stateDir}/workspace";
        defaultText = literalExpression ''"''${cfg.stateDir}/workspace"'';
        description = "Working directory for the agent (MESSAGING_CWD).";
      };

      # ── Declarative config ───────────────────────────────────────────────
      configFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Path to an existing config.yaml. If set, takes precedence over
          the declarative `settings` option.
        '';
      };

      settings = mkOption {
        type = deepConfigType;
        default = { };
        description = ''
          Declarative KClaw config (attrset). Deep-merged across module
          definitions and rendered as config.yaml.
        '';
        example = literalExpression ''
          {
            model = "anthropic/claude-sonnet-4";
            terminal.backend = "local";
            compression = { enabled = true; threshold = 0.85; };
            toolsets = [ "all" ];
          }
        '';
      };

      # ── Secrets / environment ────────────────────────────────────────────
      environmentFiles = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = ''
          Paths to environment files containing secrets (API keys, tokens).
          Contents are merged into $KCLAW_HOME/.env at activation time.
          KClaw reads this file on every startup via load_kclaw_dotenv().
        '';
      };

      environment = mkOption {
        type = types.attrsOf types.str;
        default = { };
        description = ''
          Non-secret environment variables. Merged into $KCLAW_HOME/.env
          at activation time. Do NOT put secrets here — use environmentFiles.
        '';
      };

      authFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Path to an auth.json seed file (OAuth credentials).
          Only copied on first deploy — existing auth.json is preserved.
        '';
      };

      authFileForceOverwrite = mkOption {
        type = types.bool;
        default = false;
        description = "Always overwrite auth.json from authFile on activation.";
      };

      # ── Documents ────────────────────────────────────────────────────────
      documents = mkOption {
        type = types.attrsOf (types.either types.str types.path);
        default = { };
        description = ''
          Workspace files (SOUL.md, USER.md, etc.). Keys are filenames,
          values are inline strings or paths. Installed into workingDirectory.
        '';
        example = literalExpression ''
          {
            "SOUL.md" = "You are a helpful AI assistant.";
            "USER.md" = ./documents/USER.md;
          }
        '';
      };

      # ── MCP Servers ──────────────────────────────────────────────────────
      mcpServers = mkOption {
        type = types.attrsOf (types.submodule {
          options = {
            # Stdio transport
            command = mkOption {
              type = types.nullOr types.str;
              default = null;
              description = "MCP server command (stdio transport).";
            };
            args = mkOption {
              type = types.listOf types.str;
              default = [ ];
              description = "Command-line arguments (stdio transport).";
            };
            env = mkOption {
              type = types.attrsOf types.str;
              default = { };
              description = "Environment variables for the server process (stdio transport).";
            };

            # HTTP/StreamableHTTP transport
            url = mkOption {
              type = types.nullOr types.str;
              default = null;
              description = "MCP server endpoint URL (HTTP/StreamableHTTP transport).";
            };
            headers = mkOption {
              type = types.attrsOf types.str;
              default = { };
              description = "HTTP headers, e.g. for authentication (HTTP transport).";
            };

            # Authentication
            auth = mkOption {
              type = types.nullOr (types.enum [ "oauth" ]);
              default = null;
              description = ''
                Authentication method. Set to "oauth" for OAuth 2.1 PKCE flow
                (remote MCP servers). Tokens are stored in $KCLAW_HOME/mcp-tokens/.
              '';
            };

            # Enable/disable
            enabled = mkOption {
              type = types.bool;
              default = true;
              description = "Enable or disable this MCP server.";
            };

            # Common options
            timeout = mkOption {
              type = types.nullOr types.int;
              default = null;
              description = "Tool call timeout in seconds (default: 120).";
            };
            connect_timeout = mkOption {
              type = types.nullOr types.int;
              default = null;
              description = "Initial connection timeout in seconds (default: 60).";
            };

            # Tool filtering
            tools = mkOption {
              type = types.nullOr (types.submodule {
                options = {
                  include = mkOption {
                    type = types.listOf types.str;
                    default = [ ];
                    description = "Tool allowlist — only these tools are registered.";
                  };
                  exclude = mkOption {
                    type = types.listOf types.str;
                    default = [ ];
                    description = "Tool blocklist — these tools are hidden.";
                  };
                };
              });
              default = null;
              description = "Filter which tools are exposed by this server.";
            };

            # Sampling (server-initiated LLM requests)
            sampling = mkOption {
              type = types.nullOr (types.submodule {
                options = {
                  enabled = mkOption { type = types.bool; default = true; description = "Enable sampling."; };
                  model = mkOption { type = types.nullOr types.str; default = null; description = "Override model for sampling requests."; };
                  max_tokens_cap = mkOption { type = types.nullOr types.int; default = null; description = "Max tokens per request."; };
                  timeout = mkOption { type = types.nullOr types.int; default = null; description = "LLM call timeout in seconds."; };
                  max_rpm = mkOption { type = types.nullOr types.int; default = null; description = "Max requests per minute."; };
                  max_tool_rounds = mkOption { type = types.nullOr types.int; default = null; description = "Max tool-use rounds per sampling request."; };
                  allowed_models = mkOption { type = types.listOf types.str; default = [ ]; description = "Models the server is allowed to request."; };
                  log_level = mkOption {
                    type = types.nullOr (types.enum [ "debug" "info" "warning" ]);
                    default = null;
                    description = "Audit log level for sampling requests.";
                  };
                };
              });
              default = null;
              description = "Sampling configuration for server-initiated LLM requests.";
            };
          };
        });
        default = { };
        description = ''
          MCP server configurations (merged into settings.mcp_servers).
          Each server uses either stdio (command/args) or HTTP (url) transport.
        '';
        example = literalExpression ''
          {
            filesystem = {
              command = "npx";
              args = [ "-y" "@modelcontextprotocol/server-filesystem" "/home/user" ];
            };
            remote-api = {
              url = "http://my-server:8080/v0/mcp";
              headers = { Authorization = "Bearer ..."; };
            };
            remote-oauth = {
              url = "https://mcp.example.com/mcp";
              auth = "oauth";
            };
          }
        '';
      };

      # ── Service behavior ─────────────────────────────────────────────────
      extraArgs = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Extra command-line arguments for `kclaw gateway`.";
      };

      extraPackages = mkOption {
        type = types.listOf types.package;
        default = [ ];
        description = "Extra packages available on PATH.";
      };

      restart = mkOption {
        type = types.str;
        default = "always";
        description = "systemd Restart= policy.";
      };

      restartSec = mkOption {
        type = types.int;
        default = 5;
        description = "systemd RestartSec= value.";
      };

      addToSystemPackages = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Add the kclaw CLI to environment.systemPackages and export
          KCLAW_HOME system-wide (via environment.variables) so interactive
          shells share state with the gateway service.
        '';
      };

      # ── OCI Container (opt-in) ──────────────────────────────────────────
      container = {
        enable = mkEnableOption "OCI container mode (Ubuntu base, full self-modification support)";

        backend = mkOption {
          type = types.enum [ "docker" "podman" ];
          default = "docker";
          description = "Container runtime.";
        };

        extraVolumes = mkOption {
          type = types.listOf types.str;
          default = [ ];
          description = "Extra volume mounts (host:container:mode format).";
          example = [ "/home/user/projects:/projects:rw" ];
        };

        extraOptions = mkOption {
          type = types.listOf types.str;
          default = [ ];
          description = "Extra arguments passed to docker/podman run.";
        };

        image = mkOption {
          type = types.str;
          default = "ubuntu:24.04";
          description = "OCI container image. The container pulls this at runtime via Docker/Podman.";
        };
      };
    };

    config = lib.mkIf cfg.enable (lib.mkMerge [

      # ── Merge MCP servers into settings ────────────────────────────────
      (lib.mkIf (cfg.mcpServers != { }) {
        services.kclaw.settings.mcp_servers = lib.mapAttrs (_name: srv:
          # Stdio transport
          lib.optionalAttrs (srv.command != null) { inherit (srv) command args; }
          // lib.optionalAttrs (srv.env != { }) { inherit (srv) env; }
          # HTTP transport
          // lib.optionalAttrs (srv.url != null) { inherit (srv) url; }
          // lib.optionalAttrs (srv.headers != { }) { inherit (srv) headers; }
          # Auth
          // lib.optionalAttrs (srv.auth != null) { inherit (srv) auth; }
          # Enable/disable
          // { inherit (srv) enabled; }
          # Common options
          // lib.optionalAttrs (srv.timeout != null) { inherit (srv) timeout; }
          // lib.optionalAttrs (srv.connect_timeout != null) { inherit (srv) connect_timeout; }
          # Tool filtering
          // lib.optionalAttrs (srv.tools != null) {
            tools = lib.filterAttrs (_: v: v != [ ]) {
              inherit (srv.tools) include exclude;
            };
          }
          # Sampling
          // lib.optionalAttrs (srv.sampling != null) {
            sampling = lib.filterAttrs (_: v: v != null && v != [ ]) {
              inherit (srv.sampling) enabled model max_tokens_cap timeout max_rpm
                max_tool_rounds allowed_models log_level;
            };
          }
        ) cfg.mcpServers;
      })

      # ── User / group ──────────────────────────────────────────────────
      (lib.mkIf cfg.createUser {
        users.groups.${cfg.group} = { };
        users.users.${cfg.user} = {
          isSystemUser = true;
          group = cfg.group;
          home = cfg.stateDir;
          createHome = true;
          shell = pkgs.bashInteractive;
        };
      })

      # ── Host CLI ──────────────────────────────────────────────────────
      # Add the kclaw CLI to system PATH and export KCLAW_HOME system-wide
      # so interactive shells share state (sessions, skills, cron) with the
      # gateway service instead of creating a separate ~/.kclaw/.
      (lib.mkIf cfg.addToSystemPackages {
        environment.systemPackages = [ cfg.package ];
        environment.variables.KCLAW_HOME = "${cfg.stateDir}/.kclaw";
      })

      # ── Directories ───────────────────────────────────────────────────
      {
        systemd.tmpfiles.rules = [
          "d ${cfg.stateDir}                0750 ${cfg.user} ${cfg.group} - -"
          "d ${cfg.stateDir}/.kclaw        0750 ${cfg.user} ${cfg.group} - -"
          "d ${cfg.stateDir}/home           0750 ${cfg.user} ${cfg.group} - -"
          "d ${cfg.workingDirectory}         0750 ${cfg.user} ${cfg.group} - -"
        ];
      }

      # ── Activation: link config + auth + documents ────────────────────
      {
        system.activationScripts."kclaw-setup" = lib.stringAfter [ "users" "setupSecrets" ] ''
          # Ensure directories exist (activation runs before tmpfiles)
          mkdir -p ${cfg.stateDir}/.kclaw
          mkdir -p ${cfg.stateDir}/home
          mkdir -p ${cfg.workingDirectory}
          chown ${cfg.user}:${cfg.group} ${cfg.stateDir} ${cfg.stateDir}/.kclaw ${cfg.stateDir}/home ${cfg.workingDirectory}
          chmod 0750 ${cfg.stateDir} ${cfg.stateDir}/.kclaw ${cfg.stateDir}/home ${cfg.workingDirectory}

          # Merge Nix settings into existing config.yaml.
          # Preserves user-added keys (skills, streaming, etc.); Nix keys win.
          # If configFile is user-provided (not generated), overwrite instead of merge.
          ${if cfg.configFile != null then ''
            install -o ${cfg.user} -g ${cfg.group} -m 0640 -D ${configFile} ${cfg.stateDir}/.kclaw/config.yaml
          '' else ''
            ${configMergeScript} ${generatedConfigFile} ${cfg.stateDir}/.kclaw/config.yaml
            chown ${cfg.user}:${cfg.group} ${cfg.stateDir}/.kclaw/config.yaml
            chmod 0640 ${cfg.stateDir}/.kclaw/config.yaml
          ''}

          # Managed mode marker (so interactive shells also detect NixOS management)
          touch ${cfg.stateDir}/.kclaw/.managed
          chown ${cfg.user}:${cfg.group} ${cfg.stateDir}/.kclaw/.managed
          chmod 0644 ${cfg.stateDir}/.kclaw/.managed

          # Seed auth file if provided
          ${lib.optionalString (cfg.authFile != null) ''
            ${if cfg.authFileForceOverwrite then ''
              install -o ${cfg.user} -g ${cfg.group} -m 0600 ${cfg.authFile} ${cfg.stateDir}/.kclaw/auth.json
            '' else ''
              if [ ! -f ${cfg.stateDir}/.kclaw/auth.json ]; then
                install -o ${cfg.user} -g ${cfg.group} -m 0600 ${cfg.authFile} ${cfg.stateDir}/.kclaw/auth.json
              fi
            ''}
          ''}

          # Seed .env from Nix-declared environment + environmentFiles.
          # KClaw reads $KCLAW_HOME/.env at startup via load_kclaw_dotenv(),
          # so this is the single source of truth for both native and container mode.
          ${lib.optionalString (cfg.environment != {} || cfg.environmentFiles != []) ''
            ENV_FILE="${cfg.stateDir}/.kclaw/.env"
            install -o ${cfg.user} -g ${cfg.group} -m 0640 /dev/null "$ENV_FILE"
            cat > "$ENV_FILE" <<'KCLAW_NIX_ENV_EOF'
${envFileContent}
KCLAW_NIX_ENV_EOF
            ${lib.concatStringsSep "\n" (map (f: ''
              if [ -f "${f}" ]; then
                echo "" >> "$ENV_FILE"
                cat "${f}" >> "$ENV_FILE"
              fi
            '') cfg.environmentFiles)}
          ''}

          # Link documents into workspace
          ${lib.concatStringsSep "\n" (lib.mapAttrsToList (name: _value: ''
            install -o ${cfg.user} -g ${cfg.group} -m 0640 ${documentDerivation}/${name} ${cfg.workingDirectory}/${name}
          '') cfg.documents)}
        '';
      }

      # ══════════════════════════════════════════════════════════════════
      # MODE A: Native systemd service (default)
      # ══════════════════════════════════════════════════════════════════
      (lib.mkIf (!cfg.container.enable) {
        systemd.services.kclaw = {
          description = "KClaw Agent Gateway";
          wantedBy = [ "multi-user.target" ];
          after = [ "network-online.target" ];
          wants = [ "network-online.target" ];

          environment = {
            HOME = cfg.stateDir;
            KCLAW_HOME = "${cfg.stateDir}/.kclaw";
            KCLAW_MANAGED = "true";
            MESSAGING_CWD = cfg.workingDirectory;
          };

          serviceConfig = {
            User = cfg.user;
            Group = cfg.group;
            WorkingDirectory = cfg.workingDirectory;

            # cfg.environment and cfg.environmentFiles are written to
            # $KCLAW_HOME/.env by the activation script. load_kclaw_dotenv()
            # reads them at Python startup — no systemd EnvironmentFile needed.

            ExecStart = lib.concatStringsSep " " ([
              "${cfg.package}/bin/kclaw"
              "gateway"
            ] ++ cfg.extraArgs);

            Restart = cfg.restart;
            RestartSec = cfg.restartSec;

            # Hardening
            NoNewPrivileges = true;
            ProtectSystem = "strict";
            ProtectHome = false;
            ReadWritePaths = [ cfg.stateDir ];
            PrivateTmp = true;
          };

          path = [
            cfg.package
            pkgs.bash
            pkgs.coreutils
            pkgs.git
          ] ++ cfg.extraPackages;
        };
      })

      # ══════════════════════════════════════════════════════════════════
      # MODE B: OCI container (persistent writable layer)
      # ══════════════════════════════════════════════════════════════════
      (lib.mkIf cfg.container.enable {
        # Ensure the container runtime is available
        virtualisation.docker.enable = lib.mkDefault (cfg.container.backend == "docker");

        systemd.services.kclaw = {
          description = "KClaw Agent Gateway (container)";
          wantedBy = [ "multi-user.target" ];
          after = [ "network-online.target" ]
            ++ lib.optional (cfg.container.backend == "docker") "docker.service";
          wants = [ "network-online.target" ];
          requires = lib.optional (cfg.container.backend == "docker") "docker.service";

          preStart = ''
            # Stable symlinks — container references these, not store paths directly
            ln -sfn ${cfg.package} ${cfg.stateDir}/current-package
            ln -sfn ${containerEntrypoint} ${cfg.stateDir}/current-entrypoint

            # GC roots so nix-collect-garbage doesn't remove store paths in use
            ${pkgs.nix}/bin/nix-store --add-root ${cfg.stateDir}/.gc-root --indirect -r ${cfg.package} 2>/dev/null || true
            ${pkgs.nix}/bin/nix-store --add-root ${cfg.stateDir}/.gc-root-entrypoint --indirect -r ${containerEntrypoint} 2>/dev/null || true

            # Check if container needs (re)creation
            NEED_CREATE=false
            if ! ${containerBin} inspect ${containerName} &>/dev/null; then
              NEED_CREATE=true
            elif [ ! -f ${identityFile} ] || [ "$(cat ${identityFile})" != "${containerIdentity}" ]; then
              echo "Container config changed, recreating..."
              ${containerBin} rm -f ${containerName} || true
              NEED_CREATE=true
            fi

            if [ "$NEED_CREATE" = "true" ]; then
              # Resolve numeric UID/GID — passed to entrypoint for in-container user setup
              KCLAW_UID=$(${pkgs.coreutils}/bin/id -u ${cfg.user})
              KCLAW_GID=$(${pkgs.coreutils}/bin/id -g ${cfg.user})

              echo "Creating container..."
              ${containerBin} create \
                --name ${containerName} \
                --network=host \
                --entrypoint ${containerDataDir}/current-entrypoint \
                --volume /nix/store:/nix/store:ro \
                --volume ${cfg.stateDir}:${containerDataDir} \
                --volume ${cfg.stateDir}/home:${containerHomeDir} \
                ${lib.concatStringsSep " " (map (v: "--volume ${v}") cfg.container.extraVolumes)} \
                --env KCLAW_UID="$KCLAW_UID" \
                --env KCLAW_GID="$KCLAW_GID" \
                --env KCLAW_HOME=${containerDataDir}/.kclaw \
                --env KCLAW_MANAGED=true \
                --env HOME=${containerHomeDir} \
                --env MESSAGING_CWD=${containerWorkDir} \
                ${lib.concatStringsSep " " cfg.container.extraOptions} \
                ${cfg.container.image} \
                ${containerDataDir}/current-package/bin/kclaw gateway run --replace ${lib.concatStringsSep " " cfg.extraArgs}

              echo "${containerIdentity}" > ${identityFile}
            fi
          '';

          script = ''
            exec ${containerBin} start -a ${containerName}
          '';

          preStop = ''
            ${containerBin} stop -t 10 ${containerName} || true
          '';

          serviceConfig = {
            Type = "simple";
            Restart = cfg.restart;
            RestartSec = cfg.restartSec;
            TimeoutStopSec = 30;
          };
        };
      })
    ]);
  };
}
