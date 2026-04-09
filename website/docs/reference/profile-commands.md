---
sidebar_position: 7
---

# Profile Commands Reference

This page covers all commands related to [KClaw profiles](../user-guide/profiles.md). For general CLI commands, see [CLI Commands Reference](./cli-commands.md).

## `kclaw profile`

```bash
kclaw profile <subcommand>
```

Top-level command for managing profiles. Running `kclaw profile` without a subcommand shows help.

| Subcommand | Description |
|------------|-------------|
| `list` | List all profiles. |
| `use` | Set the active (default) profile. |
| `create` | Create a new profile. |
| `delete` | Delete a profile. |
| `show` | Show details about a profile. |
| `alias` | Regenerate the shell alias for a profile. |
| `rename` | Rename a profile. |
| `export` | Export a profile to a tar.gz archive. |
| `import` | Import a profile from a tar.gz archive. |

## `kclaw profile list`

```bash
kclaw profile list
```

Lists all profiles. The currently active profile is marked with `*`.

**Example:**

```bash
$ kclaw profile list
  default
* work
  dev
  personal
```

No options.

## `kclaw profile use`

```bash
kclaw profile use <name>
```

Sets `<name>` as the active profile. All subsequent `kclaw` commands (without `-p`) will use this profile.

| Argument | Description |
|----------|-------------|
| `<name>` | Profile name to activate. Use `default` to return to the base profile. |

**Example:**

```bash
kclaw profile use work
kclaw profile use default
```

## `kclaw profile create`

```bash
kclaw profile create <name> [options]
```

Creates a new profile.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Name for the new profile. Must be a valid directory name (alphanumeric, hyphens, underscores). |
| `--clone` | Copy `config.yaml`, `.env`, and `SOUL.md` from the current profile. |
| `--clone-all` | Copy everything (config, memories, skills, sessions, state) from the current profile. |
| `--clone-from <profile>` | Clone from a specific profile instead of the current one. Used with `--clone` or `--clone-all`. |

**Examples:**

```bash
# Blank profile — needs full setup
kclaw profile create mybot

# Clone config only from current profile
kclaw profile create work --clone

# Clone everything from current profile
kclaw profile create backup --clone-all

# Clone config from a specific profile
kclaw profile create work2 --clone --clone-from work
```

## `kclaw profile delete`

```bash
kclaw profile delete <name> [options]
```

Deletes a profile and removes its shell alias.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Profile to delete. |
| `--yes`, `-y` | Skip confirmation prompt. |

**Example:**

```bash
kclaw profile delete mybot
kclaw profile delete mybot --yes
```

:::warning
This permanently deletes the profile's entire directory including all config, memories, sessions, and skills. Cannot delete the currently active profile.
:::

## `kclaw profile show`

```bash
kclaw profile show <name>
```

Displays details about a profile including its home directory, configured model, gateway status, skills count, and configuration file status.

| Argument | Description |
|----------|-------------|
| `<name>` | Profile to inspect. |

**Example:**

```bash
$ kclaw profile show work
Profile: work
Path:    ~/.kclaw/profiles/work
Model:   anthropic/claude-sonnet-4 (anthropic)
Gateway: stopped
Skills:  12
.env:    exists
SOUL.md: exists
Alias:   ~/.local/bin/work
```

## `kclaw profile alias`

```bash
kclaw profile alias <name> [options]
```

Regenerates the shell alias script at `~/.local/bin/<name>`. Useful if the alias was accidentally deleted or if you need to update it after moving your KClaw installation.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Profile to create/update the alias for. |
| `--remove` | Remove the wrapper script instead of creating it. |
| `--name <alias>` | Custom alias name (default: profile name). |

**Example:**

```bash
kclaw profile alias work
# Creates/updates ~/.local/bin/work

kclaw profile alias work --name mywork
# Creates ~/.local/bin/mywork

kclaw profile alias work --remove
# Removes the wrapper script
```

## `kclaw profile rename`

```bash
kclaw profile rename <old-name> <new-name>
```

Renames a profile. Updates the directory and shell alias.

| Argument | Description |
|----------|-------------|
| `<old-name>` | Current profile name. |
| `<new-name>` | New profile name. |

**Example:**

```bash
kclaw profile rename mybot assistant
# ~/.kclaw/profiles/mybot → ~/.kclaw/profiles/assistant
# ~/.local/bin/mybot → ~/.local/bin/assistant
```

## `kclaw profile export`

```bash
kclaw profile export <name> [options]
```

Exports a profile as a compressed tar.gz archive.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Profile to export. |
| `-o`, `--output <path>` | Output file path (default: `<name>.tar.gz`). |

**Example:**

```bash
kclaw profile export work
# Creates work.tar.gz in the current directory

kclaw profile export work -o ./work-2026-03-29.tar.gz
```

## `kclaw profile import`

```bash
kclaw profile import <archive> [options]
```

Imports a profile from a tar.gz archive.

| Argument / Option | Description |
|-------------------|-------------|
| `<archive>` | Path to the tar.gz archive to import. |
| `--name <name>` | Name for the imported profile (default: inferred from archive). |

**Example:**

```bash
kclaw profile import ./work-2026-03-29.tar.gz
# Infers profile name from the archive

kclaw profile import ./work-2026-03-29.tar.gz --name work-restored
```

## `kclaw -p` / `kclaw --profile`

```bash
kclaw -p <name> <command> [options]
kclaw --profile <name> <command> [options]
```

Global flag to run any KClaw command under a specific profile without changing the sticky default. This overrides the active profile for the duration of the command.

| Option | Description |
|--------|-------------|
| `-p <name>`, `--profile <name>` | Profile to use for this command. |

**Examples:**

```bash
kclaw -p work chat -q "Check the server status"
kclaw --profile dev gateway start
kclaw -p personal skills list
kclaw -p work config edit
```

## `kclaw completion`

```bash
kclaw completion <shell>
```

Generates shell completion scripts. Includes completions for profile names and profile subcommands.

| Argument | Description |
|----------|-------------|
| `<shell>` | Shell to generate completions for: `bash` or `zsh`. |

**Examples:**

```bash
# Install completions
kclaw completion bash >> ~/.bashrc
kclaw completion zsh >> ~/.zshrc

# Reload shell
source ~/.bashrc
```

After installation, tab completion works for:
- `kclaw profile <TAB>` — subcommands (list, use, create, etc.)
- `kclaw profile use <TAB>` — profile names
- `kclaw -p <TAB>` — profile names

## See also

- [Profiles User Guide](../user-guide/profiles.md)
- [CLI Commands Reference](./cli-commands.md)
- [FAQ — Profiles section](./faq.md#profiles)
