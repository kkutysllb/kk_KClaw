---
sidebar_position: 11
sidebar_label: "Plugins"
title: "Plugins"
description: "Extend KClaw with custom tools, hooks, and integrations via the plugin system"
---

# Plugins

KClaw has a plugin system for adding custom tools, hooks, and integrations without modifying core code.

**→ [Build a KClaw Plugin](/docs/guides/build-a-kclaw-plugin)** — step-by-step guide with a complete working example.

## Quick overview

Drop a directory into `~/.kclaw/plugins/` with a `plugin.yaml` and Python code:

```
~/.kclaw/plugins/my-plugin/
├── plugin.yaml      # manifest
├── __init__.py      # register() — wires schemas to handlers
├── schemas.py       # tool schemas (what the LLM sees)
└── tools.py         # tool handlers (what runs when called)
```

Start KClaw — your tools appear alongside built-in tools. The model can call them immediately.

### Minimal working example

Here is a complete plugin that adds a `hello_world` tool and logs every tool call via a hook.

**`~/.kclaw/plugins/hello-world/plugin.yaml`**

```yaml
name: hello-world
version: "1.0"
description: A minimal example plugin
```

**`~/.kclaw/plugins/hello-world/__init__.py`**

```python
"""Minimal KClaw plugin — registers a tool and a hook."""


def register(ctx):
    # --- Tool: hello_world ---
    schema = {
        "name": "hello_world",
        "description": "Returns a friendly greeting for the given name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name to greet",
                }
            },
            "required": ["name"],
        },
    }

    def handle_hello(params):
        name = params.get("name", "World")
        return f"Hello, {name}! 👋  (from the hello-world plugin)"

    ctx.register_tool("hello_world", schema, handle_hello)

    # --- Hook: log every tool call ---
    def on_tool_call(tool_name, params, result):
        print(f"[hello-world] tool called: {tool_name}")

    ctx.register_hook("post_tool_call", on_tool_call)
```

Drop both files into `~/.kclaw/plugins/hello-world/`, restart KClaw, and the model can immediately call `hello_world`. The hook prints a log line after every tool invocation.

Project-local plugins under `./.kclaw/plugins/` are disabled by default. Enable them only for trusted repositories by setting `KCLAW_ENABLE_PROJECT_PLUGINS=true` before starting KClaw.

## What plugins can do

| Capability | How |
|-----------|-----|
| Add tools | `ctx.register_tool(name, schema, handler)` |
| Add hooks | `ctx.register_hook("post_tool_call", callback)` |
| Add CLI commands | `ctx.register_cli_command(name, help, setup_fn, handler_fn)` — adds `kclaw <plugin> <subcommand>` |
| Inject messages | `ctx.inject_message(content, role="user")` — see [Injecting Messages](#injecting-messages) |
| Ship data files | `Path(__file__).parent / "data" / "file.yaml"` |
| Bundle skills | Copy `skill.md` to `~/.kclaw/skills/` at load time |
| Gate on env vars | `requires_env: [API_KEY]` in plugin.yaml — prompted during `kclaw plugins install` |
| Distribute via pip | `[project.entry-points."kclaw_agent.plugins"]` |

## Plugin discovery

| Source | Path | Use case |
|--------|------|----------|
| User | `~/.kclaw/plugins/` | Personal plugins |
| Project | `.kclaw/plugins/` | Project-specific plugins (requires `KCLAW_ENABLE_PROJECT_PLUGINS=true`) |
| pip | `kclaw_agent.plugins` entry_points | Distributed packages |

## Available hooks

Plugins can register callbacks for these lifecycle events. See the **[Event Hooks page](/docs/user-guide/features/hooks#plugin-hooks)** for full details, callback signatures, and examples.

| Hook | Fires when |
|------|-----------|
| [`pre_tool_call`](/docs/user-guide/features/hooks#pre_tool_call) | Before any tool executes |
| [`post_tool_call`](/docs/user-guide/features/hooks#post_tool_call) | After any tool returns |
| [`pre_llm_call`](/docs/user-guide/features/hooks#pre_llm_call) | Once per turn, before the LLM loop — can return `{"context": "..."}` to [inject context into the user message](/docs/user-guide/features/hooks#pre_llm_call) |
| [`post_llm_call`](/docs/user-guide/features/hooks#post_llm_call) | Once per turn, after the LLM loop (successful turns only) |
| [`on_session_start`](/docs/user-guide/features/hooks#on_session_start) | New session created (first turn only) |
| [`on_session_end`](/docs/user-guide/features/hooks#on_session_end) | End of every `run_conversation` call + CLI exit handler |

## Managing plugins

```bash
kclaw plugins                  # interactive toggle UI — enable/disable with checkboxes
kclaw plugins list             # table view with enabled/disabled status
kclaw plugins install user/repo  # install from Git
kclaw plugins update my-plugin   # pull latest
kclaw plugins remove my-plugin   # uninstall
kclaw plugins enable my-plugin   # re-enable a disabled plugin
kclaw plugins disable my-plugin  # disable without removing
```

Running `kclaw plugins` with no arguments launches an interactive curses checklist (same UI as `kclaw tools`) where you can toggle plugins on/off with arrow keys and space.

Disabled plugins remain installed but are skipped during loading. The disabled list is stored in `config.yaml` under `plugins.disabled`:

```yaml
plugins:
  disabled:
    - my-noisy-plugin
```

In a running session, `/plugins` shows which plugins are currently loaded.

## Injecting Messages

Plugins can inject messages into the active conversation using `ctx.inject_message()`:

```python
ctx.inject_message("New data arrived from the webhook", role="user")
```

**Signature:** `ctx.inject_message(content: str, role: str = "user") -> bool`

How it works:

- If the agent is **idle** (waiting for user input), the message is queued as the next input and starts a new turn.
- If the agent is **mid-turn** (actively running), the message interrupts the current operation — the same as a user typing a new message and pressing Enter.
- For non-`"user"` roles, the content is prefixed with `[role]` (e.g. `[system] ...`).
- Returns `True` if the message was queued successfully, `False` if no CLI reference is available (e.g. in gateway mode).

This enables plugins like remote control viewers, messaging bridges, or webhook receivers to feed messages into the conversation from external sources.

:::note
`inject_message` is only available in CLI mode. In gateway mode, there is no CLI reference and the method returns `False`.
:::

See the **[full guide](/docs/guides/build-a-kclaw-plugin)** for handler contracts, schema format, hook behavior, error handling, and common mistakes.
