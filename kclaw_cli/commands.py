"""KClaw CLI 的斜杠命令定义和自动补全。

所有斜杠命令的中央注册表。每个消费者 — CLI 帮助、网关
分发、Telegram BotCommands、Slack 子命令映射、自动补全 —
都从 ``COMMAND_REGISTRY`` 派生其数据。

添加命令：在 ``COMMAND_REGISTRY`` 中添加 ``CommandDef`` 条目。
添加别名：在现有的 ``CommandDef`` 上设置 ``aliases=("short",)``。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import Completer, Completion


# ---------------------------------------------------------------------------
# CommandDef 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandDef:
    """单个斜杠命令的定义。"""

    name: str                          # 不带斜杠的规范名称: "background"
    description: str                   # 人类可读的描述
    category: str                      # "Session"、"Configuration" 等
    aliases: tuple[str, ...] = ()      # 替代名称: ("bg",)
    args_hint: str = ""                # 参数占位符: "<prompt>"、"[name]"
    subcommands: tuple[str, ...] = ()  # Tab 可补全的子命令
    cli_only: bool = False             # 仅在 CLI 中可用
    gateway_only: bool = False         # 仅在 gateway/消息中可用
    gateway_config_gate: str | None = None  # 配置点路径；为真时覆盖 gateway 的 cli_only


# ---------------------------------------------------------------------------
# 中央注册表 — 唯一真实来源
# -----------------------------------------------------------------------------

COMMAND_REGISTRY: list[CommandDef] = [
    # 会话
    CommandDef("new", "开始新会话（全新会话 ID + 历史）", "Session",
               aliases=("reset",)),
    CommandDef("clear", "清除屏幕并开始新会话", "Session",
               cli_only=True),
    CommandDef("history", "显示对话历史", "Session",
               cli_only=True),
    CommandDef("save", "保存当前对话", "Session",
               cli_only=True),
    CommandDef("retry", "重试上一条消息（重新发送给智能体）", "Session"),
    CommandDef("undo", "删除上一对用户/助手交互", "Session"),
    CommandDef("title", "为当前会话设置标题", "Session",
               args_hint="[name]"),
    CommandDef("branch", "分支当前会话（探索不同路径）", "Session",
               aliases=("fork",), args_hint="[name]"),
    CommandDef("compress", "手动压缩对话上下文", "Session"),
    CommandDef("rollback", "列出或恢复文件系统检查点", "Session",
               args_hint="[number]"),
    CommandDef("stop", "终止所有运行中的后台进程", "Session"),
    CommandDef("approve", "批准待处理的危险命令", "Session",
               gateway_only=True, args_hint="[session|always]"),
    CommandDef("deny", "拒绝待处理的危险命令", "Session",
               gateway_only=True),
    CommandDef("background", "在后台运行提示", "Session",
               aliases=("bg",), args_hint="<prompt>"),
    CommandDef("btw", "使用会话上下文的临时附带问题（无工具，不持久化）", "Session",
               args_hint="<question>"),
    CommandDef("queue", "将提示加入队列以供下一轮（不会中断）", "Session",
               aliases=("q",), args_hint="<prompt>"),
    CommandDef("status", "显示会话信息", "Session",
               gateway_only=True),
    CommandDef("profile", "显示活动 profile 名称和主目录", "Info"),
    CommandDef("sethome", "将此聊天设置为家庭频道", "Session",
               gateway_only=True, aliases=("set-home",)),
    CommandDef("resume", "恢复之前命名的会话", "Session",
               args_hint="[name]"),

    # 配置
    CommandDef("config", "显示当前配置", "Configuration",
               cli_only=True),
    CommandDef("model", "切换此会话的模型", "Configuration", args_hint="[model] [--global]"),
    CommandDef("provider", "显示可用提供者和当前提供者",
               "Configuration"),
    CommandDef("prompt", "查看/设置自定义系统提示词", "Configuration",
               cli_only=True, args_hint="[text]", subcommands=("clear",)),
    CommandDef("personality", "设置预定义人格", "Configuration",
               args_hint="[name]"),
    CommandDef("statusbar", "切换上下文/模型状态栏", "Configuration",
               cli_only=True, aliases=("sb",)),
    CommandDef("verbose", "循环工具进度显示: off -> new -> all -> verbose",
               "Configuration", cli_only=True,
               gateway_config_gate="display.tool_progress_command"),
    CommandDef("yolo", "切换 YOLO 模式（跳过所有危险命令审批）",
               "Configuration"),
    CommandDef("reasoning", "管理推理努力和显示", "Configuration",
               args_hint="[level|show|hide]",
               subcommands=("none", "low", "minimal", "medium", "high", "xhigh", "show", "hide", "on", "off")),
    CommandDef("skin", "显示或更改显示皮肤/主题", "Configuration",
               cli_only=True, args_hint="[name]"),
    CommandDef("voice", "切换语音模式", "Configuration",
               args_hint="[on|off|tts|status]", subcommands=("on", "off", "tts", "status")),

    # 工具与技能
    CommandDef("tools", "管理工具: /tools [list|disable|enable] [name...]", "Tools & Skills",
               args_hint="[list|disable|enable] [name...]", cli_only=True),
    CommandDef("toolsets", "列出可用工具集", "Tools & Skills",
               cli_only=True),
    CommandDef("skills", "搜索、安装、检查或管理技能",
               "Tools & Skills", cli_only=True,
               subcommands=("search", "browse", "inspect", "install")),
    CommandDef("cron", "管理计划任务", "Tools & Skills",
               cli_only=True, args_hint="[subcommand]",
               subcommands=("list", "add", "create", "edit", "pause", "resume", "run", "remove")),
    CommandDef("reload-mcp", "从配置重新加载 MCP 服务器", "Tools & Skills",
               aliases=("reload_mcp",)),
    CommandDef("browser", "通过 CDP 将浏览器工具连接到实时 Chrome", "Tools & Skills",
               cli_only=True, args_hint="[connect|disconnect|status]",
               subcommands=("connect", "disconnect", "status")),
    CommandDef("plugins", "列出已安装的插件及其状态",
               "Tools & Skills", cli_only=True),

    # 信息
    CommandDef("commands", "浏览所有命令和技能（分页）", "Info",
               gateway_only=True, args_hint="[page]"),
    CommandDef("help", "显示可用命令", "Info"),
    CommandDef("usage", "显示当前会话的令牌使用率和速率限制", "Info"),
    CommandDef("insights", "显示使用洞察和分析", "Info",
               args_hint="[days]"),
    CommandDef("platforms", "显示网关/消息平台状态", "Info",
               cli_only=True, aliases=("gateway",)),
    CommandDef("paste", "检查剪贴板是否有图像并附加", "Info",
               cli_only=True),
    CommandDef("update", "更新 KClaw Agent 到最新版本", "Info",
               gateway_only=True),

    # 退出
    CommandDef("quit", "退出 CLI", "Exit",
               cli_only=True, aliases=("exit", "q")),
]


# ---------------------------------------------------------------------------
# 派生查找 — 在导入时重建一次，由 rebuild_lookups() 刷新
# -----------------------------------------------------------------------------

def _build_command_lookup() -> dict[str, CommandDef]:
    """Map every name and alias to its CommandDef."""
    lookup: dict[str, CommandDef] = {}
    for cmd in COMMAND_REGISTRY:
        lookup[cmd.name] = cmd
        for alias in cmd.aliases:
            lookup[alias] = cmd
    return lookup


_COMMAND_LOOKUP: dict[str, CommandDef] = _build_command_lookup()


def resolve_command(name: str) -> CommandDef | None:
    """Resolve a command name or alias to its CommandDef.

    Accepts names with or without the leading slash.
    """
    return _COMMAND_LOOKUP.get(name.lower().lstrip("/"))


def register_plugin_command(cmd: CommandDef) -> None:
    """Append a plugin-defined command to the registry and refresh lookups."""
    COMMAND_REGISTRY.append(cmd)
    rebuild_lookups()


def rebuild_lookups() -> None:
    """Rebuild all derived lookup dicts from the current COMMAND_REGISTRY.

    Called after plugin commands are registered so they appear in help,
    autocomplete, gateway dispatch, Telegram menu, and Slack mapping.
    """
    global GATEWAY_KNOWN_COMMANDS

    _COMMAND_LOOKUP.clear()
    _COMMAND_LOOKUP.update(_build_command_lookup())

    COMMANDS.clear()
    for cmd in COMMAND_REGISTRY:
        if not cmd.gateway_only:
            COMMANDS[f"/{cmd.name}"] = _build_description(cmd)
            for alias in cmd.aliases:
                COMMANDS[f"/{alias}"] = f"{cmd.description} (alias for /{cmd.name})"

    COMMANDS_BY_CATEGORY.clear()
    for cmd in COMMAND_REGISTRY:
        if not cmd.gateway_only:
            cat = COMMANDS_BY_CATEGORY.setdefault(cmd.category, {})
            cat[f"/{cmd.name}"] = COMMANDS[f"/{cmd.name}"]
            for alias in cmd.aliases:
                cat[f"/{alias}"] = COMMANDS[f"/{alias}"]

    SUBCOMMANDS.clear()
    for cmd in COMMAND_REGISTRY:
        if cmd.subcommands:
            SUBCOMMANDS[f"/{cmd.name}"] = list(cmd.subcommands)
    for cmd in COMMAND_REGISTRY:
        key = f"/{cmd.name}"
        if key in SUBCOMMANDS or not cmd.args_hint:
            continue
        m = _PIPE_SUBS_RE.search(cmd.args_hint)
        if m:
            SUBCOMMANDS[key] = m.group(0).split("|")

    GATEWAY_KNOWN_COMMANDS = frozenset(
        name
        for cmd in COMMAND_REGISTRY
        if not cmd.cli_only or cmd.gateway_config_gate
        for name in (cmd.name, *cmd.aliases)
    )


def _build_description(cmd: CommandDef) -> str:
    """Build a CLI-facing description string including usage hint."""
    if cmd.args_hint:
        return f"{cmd.description} (usage: /{cmd.name} {cmd.args_hint})"
    return cmd.description


# Backwards-compatible flat dict: "/command" -> description
COMMANDS: dict[str, str] = {}
for _cmd in COMMAND_REGISTRY:
    if not _cmd.gateway_only:
        COMMANDS[f"/{_cmd.name}"] = _build_description(_cmd)
        for _alias in _cmd.aliases:
            COMMANDS[f"/{_alias}"] = f"{_cmd.description} (alias for /{_cmd.name})"

# Backwards-compatible categorized dict
COMMANDS_BY_CATEGORY: dict[str, dict[str, str]] = {}
for _cmd in COMMAND_REGISTRY:
    if not _cmd.gateway_only:
        _cat = COMMANDS_BY_CATEGORY.setdefault(_cmd.category, {})
        _cat[f"/{_cmd.name}"] = COMMANDS[f"/{_cmd.name}"]
        for _alias in _cmd.aliases:
            _cat[f"/{_alias}"] = COMMANDS[f"/{_alias}"]


# Subcommands lookup: "/cmd" -> ["sub1", "sub2", ...]
SUBCOMMANDS: dict[str, list[str]] = {}
for _cmd in COMMAND_REGISTRY:
    if _cmd.subcommands:
        SUBCOMMANDS[f"/{_cmd.name}"] = list(_cmd.subcommands)

# Also extract subcommands hinted in args_hint via pipe-separated patterns
# e.g. args_hint="[on|off|tts|status]" for commands that don't have explicit subcommands.
# NOTE: If a command already has explicit subcommands, this fallback is skipped.
# Use the `subcommands` field on CommandDef for intentional tab-completable args.
_PIPE_SUBS_RE = re.compile(r"[a-z]+(?:\|[a-z]+)+")
for _cmd in COMMAND_REGISTRY:
    key = f"/{_cmd.name}"
    if key in SUBCOMMANDS or not _cmd.args_hint:
        continue
    m = _PIPE_SUBS_RE.search(_cmd.args_hint)
    if m:
        SUBCOMMANDS[key] = m.group(0).split("|")


# ---------------------------------------------------------------------------
# Gateway helpers
# ---------------------------------------------------------------------------

# Set of all command names + aliases recognized by the gateway.
# Includes config-gated commands so the gateway can dispatch them
# (the handler checks the config gate at runtime).
GATEWAY_KNOWN_COMMANDS: frozenset[str] = frozenset(
    name
    for cmd in COMMAND_REGISTRY
    if not cmd.cli_only or cmd.gateway_config_gate
    for name in (cmd.name, *cmd.aliases)
)


def _resolve_config_gates() -> set[str]:
    """Return canonical names of commands whose ``gateway_config_gate`` is truthy.

    Reads ``config.yaml`` and walks the dot-separated key path for each
    config-gated command.  Returns an empty set on any error so callers
    degrade gracefully.
    """
    gated = [c for c in COMMAND_REGISTRY if c.gateway_config_gate]
    if not gated:
        return set()
    try:
        from kclaw_cli.config import read_raw_config
        cfg = read_raw_config()
    except Exception:
        return set()
    result: set[str] = set()
    for cmd in gated:
        val: Any = cfg
        for key in cmd.gateway_config_gate.split("."):
            if isinstance(val, dict):
                val = val.get(key)
            else:
                val = None
                break
        if val:
            result.add(cmd.name)
    return result


def _is_gateway_available(cmd: CommandDef, config_overrides: set[str] | None = None) -> bool:
    """Check if *cmd* should appear in gateway surfaces (help, menus, mappings).

    Unconditionally available when ``cli_only`` is False.  When ``cli_only``
    is True but ``gateway_config_gate`` is set, the command is available only
    when the config value is truthy.  Pass *config_overrides* (from
    ``_resolve_config_gates()``) to avoid re-reading config for every command.
    """
    if not cmd.cli_only:
        return True
    if cmd.gateway_config_gate:
        overrides = config_overrides if config_overrides is not None else _resolve_config_gates()
        return cmd.name in overrides
    return False


def gateway_help_lines() -> list[str]:
    """Generate gateway help text lines from the registry."""
    overrides = _resolve_config_gates()
    lines: list[str] = []
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        args = f" {cmd.args_hint}" if cmd.args_hint else ""
        alias_parts: list[str] = []
        for a in cmd.aliases:
            # Skip internal aliases like reload_mcp (underscore variant)
            if a.replace("-", "_") == cmd.name.replace("-", "_") and a != cmd.name:
                continue
            alias_parts.append(f"`/{a}`")
        alias_note = f" (alias: {', '.join(alias_parts)})" if alias_parts else ""
        lines.append(f"`/{cmd.name}{args}` -- {cmd.description}{alias_note}")
    return lines


def telegram_bot_commands() -> list[tuple[str, str]]:
    """Return (command_name, description) pairs for Telegram setMyCommands.

    Telegram command names cannot contain hyphens, so they are replaced with
    underscores.  Aliases are skipped -- Telegram shows one menu entry per
    canonical command.
    """
    overrides = _resolve_config_gates()
    result: list[tuple[str, str]] = []
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        tg_name = _sanitize_telegram_name(cmd.name)
        if tg_name:
            result.append((tg_name, cmd.description))
    return result


_CMD_NAME_LIMIT = 32
"""Max command name length shared by Telegram and Discord."""

# Backward-compat alias — tests and external code may reference the old name.
_TG_NAME_LIMIT = _CMD_NAME_LIMIT

# Telegram Bot API allows only lowercase a-z, 0-9, and underscores in
# command names.  This regex strips everything else after initial conversion.
_TG_INVALID_CHARS = re.compile(r"[^a-z0-9_]")
_TG_MULTI_UNDERSCORE = re.compile(r"_{2,}")


def _sanitize_telegram_name(raw: str) -> str:
    """Convert a command/skill/plugin name to a valid Telegram command name.

    Telegram requires: 1-32 chars, lowercase a-z, digits 0-9, underscores only.
    Steps: lowercase → replace hyphens with underscores → strip all other
    invalid characters → collapse consecutive underscores → strip leading/
    trailing underscores.
    """
    name = raw.lower().replace("-", "_")
    name = _TG_INVALID_CHARS.sub("", name)
    name = _TG_MULTI_UNDERSCORE.sub("_", name)
    return name.strip("_")


def _clamp_command_names(
    entries: list[tuple[str, str]],
    reserved: set[str],
) -> list[tuple[str, str]]:
    """Enforce 32-char command name limit with collision avoidance.

    Both Telegram and Discord cap slash command names at 32 characters.
    Names exceeding the limit are truncated.  If truncation creates a duplicate
    (against *reserved* names or earlier entries in the same batch), the name is
    shortened to 31 chars and a digit ``0``-``9`` is appended to differentiate.
    If all 10 digit slots are taken the entry is silently dropped.
    """
    used: set[str] = set(reserved)
    result: list[tuple[str, str]] = []
    for name, desc in entries:
        if len(name) > _CMD_NAME_LIMIT:
            candidate = name[:_CMD_NAME_LIMIT]
            if candidate in used:
                prefix = name[:_CMD_NAME_LIMIT - 1]
                for digit in range(10):
                    candidate = f"{prefix}{digit}"
                    if candidate not in used:
                        break
                else:
                    # All 10 digit slots exhausted — skip entry
                    continue
            name = candidate
        if name in used:
            continue
        used.add(name)
        result.append((name, desc))
    return result


# Backward-compat alias.
_clamp_telegram_names = _clamp_command_names


# ---------------------------------------------------------------------------
# Shared skill/plugin collection for gateway platforms
# ---------------------------------------------------------------------------

def _collect_gateway_skill_entries(
    platform: str,
    max_slots: int,
    reserved_names: set[str],
    desc_limit: int = 100,
    sanitize_name: "Callable[[str], str] | None" = None,
) -> tuple[list[tuple[str, str, str]], int]:
    """Collect plugin + skill entries for a gateway platform.

    Priority order:
      1. Plugin slash commands (take precedence over skills)
      2. Built-in skill commands (fill remaining slots, alphabetical)

    Only skills are trimmed when the cap is reached.
    Hub-installed skills are excluded.  Per-platform disabled skills are
    excluded.

    Args:
        platform: Platform identifier for per-platform skill filtering
            (``"telegram"``, ``"discord"``, etc.).
        max_slots: Maximum number of entries to return (remaining slots after
            built-in/core commands).
        reserved_names: Names already taken by built-in commands.  Mutated
            in-place as new names are added.
        desc_limit: Max description length (40 for Telegram, 100 for Discord).
        sanitize_name: Optional name transform applied before clamping, e.g.
            :func:`_sanitize_telegram_name` for Telegram.  May return an
            empty string to signal "skip this entry".

    Returns:
        ``(entries, hidden_count)`` where *entries* is a list of
        ``(name, description, cmd_key)`` triples and *hidden_count* is the
        number of skill entries dropped due to the cap.  ``cmd_key`` is the
        original ``/skill-name`` key from :func:`get_skill_commands`.
    """
    all_entries: list[tuple[str, str, str]] = []

    # --- Tier 1: Plugin slash commands (never trimmed) ---------------------
    plugin_pairs: list[tuple[str, str]] = []
    try:
        from kclaw_cli.plugins import get_plugin_manager
        pm = get_plugin_manager()
        plugin_cmds = getattr(pm, "_plugin_commands", {})
        for cmd_name in sorted(plugin_cmds):
            name = sanitize_name(cmd_name) if sanitize_name else cmd_name
            if not name:
                continue
            desc = "Plugin command"
            if len(desc) > desc_limit:
                desc = desc[:desc_limit - 3] + "..."
            plugin_pairs.append((name, desc))
    except Exception:
        pass

    plugin_pairs = _clamp_command_names(plugin_pairs, reserved_names)
    reserved_names.update(n for n, _ in plugin_pairs)
    # Plugins have no cmd_key — use empty string as placeholder
    for n, d in plugin_pairs:
        all_entries.append((n, d, ""))

    # --- Tier 2: Built-in skill commands (trimmed at cap) -----------------
    _platform_disabled: set[str] = set()
    try:
        from agent.skill_utils import get_disabled_skill_names
        _platform_disabled = get_disabled_skill_names(platform=platform)
    except Exception:
        pass

    skill_triples: list[tuple[str, str, str]] = []
    try:
        from agent.skill_commands import get_skill_commands
        from tools.skills_tool import SKILLS_DIR
        _skills_dir = str(SKILLS_DIR.resolve())
        _hub_dir = str((SKILLS_DIR / ".hub").resolve())
        skill_cmds = get_skill_commands()
        for cmd_key in sorted(skill_cmds):
            info = skill_cmds[cmd_key]
            skill_path = info.get("skill_md_path", "")
            if not skill_path.startswith(_skills_dir):
                continue
            if skill_path.startswith(_hub_dir):
                continue
            skill_name = info.get("name", "")
            if skill_name in _platform_disabled:
                continue
            raw_name = cmd_key.lstrip("/")
            name = sanitize_name(raw_name) if sanitize_name else raw_name
            if not name:
                continue
            desc = info.get("description", "")
            if len(desc) > desc_limit:
                desc = desc[:desc_limit - 3] + "..."
            skill_triples.append((name, desc, cmd_key))
    except Exception:
        pass

    # Clamp names; _clamp_command_names works on (name, desc) pairs so we
    # need to zip/unzip.
    skill_pairs = [(n, d) for n, d, _ in skill_triples]
    key_by_pair = {(n, d): k for n, d, k in skill_triples}
    skill_pairs = _clamp_command_names(skill_pairs, reserved_names)

    # Skills fill remaining slots — only tier that gets trimmed
    remaining = max(0, max_slots - len(all_entries))
    hidden_count = max(0, len(skill_pairs) - remaining)
    for n, d in skill_pairs[:remaining]:
        all_entries.append((n, d, key_by_pair.get((n, d), "")))

    return all_entries[:max_slots], hidden_count


# ---------------------------------------------------------------------------
# Platform-specific wrappers
# ---------------------------------------------------------------------------

def telegram_menu_commands(max_commands: int = 100) -> tuple[list[tuple[str, str]], int]:
    """Return Telegram menu commands capped to the Bot API limit.

    Priority order (higher priority = never bumped by overflow):
      1. Core CommandDef commands (always included)
      2. Plugin slash commands (take precedence over skills)
      3. Built-in skill commands (fill remaining slots, alphabetical)

    Skills are the only tier that gets trimmed when the cap is hit.
    User-installed hub skills are excluded — accessible via /skills.
    Skills disabled for the ``"telegram"`` platform (via ``kclaw skills
    config``) are excluded from the menu entirely.

    Returns:
        (menu_commands, hidden_count) where hidden_count is the number of
        skill commands omitted due to the cap.
    """
    core_commands = list(telegram_bot_commands())
    reserved_names = {n for n, _ in core_commands}
    all_commands = list(core_commands)

    remaining_slots = max(0, max_commands - len(all_commands))
    entries, hidden_count = _collect_gateway_skill_entries(
        platform="telegram",
        max_slots=remaining_slots,
        reserved_names=reserved_names,
        desc_limit=40,
        sanitize_name=_sanitize_telegram_name,
    )
    # Drop the cmd_key — Telegram only needs (name, desc) pairs.
    all_commands.extend((n, d) for n, d, _k in entries)
    return all_commands[:max_commands], hidden_count


def discord_skill_commands(
    max_slots: int,
    reserved_names: set[str],
) -> tuple[list[tuple[str, str, str]], int]:
    """Return skill entries for Discord slash command registration.

    Same priority and filtering logic as :func:`telegram_menu_commands`
    (plugins > skills, hub excluded, per-platform disabled excluded), but
    adapted for Discord's constraints:

    - Hyphens are allowed in names (no ``-`` → ``_`` sanitization)
    - Descriptions capped at 100 chars (Discord's per-field max)

    Args:
        max_slots: Available command slots (100 minus existing built-in count).
        reserved_names: Names of already-registered built-in commands.

    Returns:
        ``(entries, hidden_count)`` where *entries* is a list of
        ``(discord_name, description, cmd_key)`` triples.  ``cmd_key`` is
        the original ``/skill-name`` key needed for the slash handler callback.
    """
    return _collect_gateway_skill_entries(
        platform="discord",
        max_slots=max_slots,
        reserved_names=set(reserved_names),  # copy — don't mutate caller's set
        desc_limit=100,
    )


def slack_subcommand_map() -> dict[str, str]:
    """Return subcommand -> /command mapping for Slack /kclaw handler.

    Maps both canonical names and aliases so /kclaw bg do stuff works
    the same as /kclaw background do stuff.
    """
    overrides = _resolve_config_gates()
    mapping: dict[str, str] = {}
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        mapping[cmd.name] = f"/{cmd.name}"
        for alias in cmd.aliases:
            mapping[alias] = f"/{alias}"
    return mapping


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------

class SlashCommandCompleter(Completer):
    """Autocomplete for built-in slash commands, subcommands, and skill commands."""

    def __init__(
        self,
        skill_commands_provider: Callable[[], Mapping[str, dict[str, Any]]] | None = None,
    ) -> None:
        self._skill_commands_provider = skill_commands_provider

    def _iter_skill_commands(self) -> Mapping[str, dict[str, Any]]:
        if self._skill_commands_provider is None:
            return {}
        try:
            return self._skill_commands_provider() or {}
        except Exception:
            return {}

    @staticmethod
    def _completion_text(cmd_name: str, word: str) -> str:
        """Return replacement text for a completion.

        When the user has already typed the full command exactly (``/help``),
        returning ``help`` would be a no-op and prompt_toolkit suppresses the
        menu. Appending a trailing space keeps the dropdown visible and makes
        backspacing retrigger it naturally.
        """
        return f"{cmd_name} " if cmd_name == word else cmd_name

    @staticmethod
    def _extract_path_word(text: str) -> str | None:
        """Extract the current word if it looks like a file path.

        Returns the path-like token under the cursor, or None if the
        current word doesn't look like a path.  A word is path-like when
        it starts with ``./``, ``../``, ``~/``, ``/``, or contains a
        ``/`` separator (e.g. ``src/main.py``).
        """
        if not text:
            return None
        # Walk backwards to find the start of the current "word".
        # Words are delimited by spaces, but paths can contain almost anything.
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word:
            return None
        # Only trigger path completion for path-like tokens
        if word.startswith(("./", "../", "~/", "/")) or "/" in word:
            return word
        return None

    @staticmethod
    def _path_completions(word: str, limit: int = 30):
        """Yield Completion objects for file paths matching *word*."""
        expanded = os.path.expanduser(word)
        # Split into directory part and prefix to match inside it
        if expanded.endswith("/"):
            search_dir = expanded
            prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = prefix.lower()
        for entry in sorted(entries):
            if prefix and not entry.lower().startswith(prefix_lower):
                continue
            if count >= limit:
                break

            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)

            # Build the completion text (what replaces the typed word)
            if word.startswith("~"):
                display_path = "~/" + os.path.relpath(full_path, os.path.expanduser("~"))
            elif os.path.isabs(word):
                display_path = full_path
            else:
                # Keep relative
                display_path = os.path.relpath(full_path)

            if is_dir:
                display_path += "/"

            suffix = "/" if is_dir else ""
            meta = "dir" if is_dir else _file_size_label(full_path)

            yield Completion(
                display_path,
                start_position=-len(word),
                display=entry + suffix,
                display_meta=meta,
            )
            count += 1

    @staticmethod
    def _extract_context_word(text: str) -> str | None:
        """Extract a bare ``@`` token for context reference completions."""
        if not text:
            return None
        # Walk backwards to find the start of the current word
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word.startswith("@"):
            return None
        return word

    @staticmethod
    def _context_completions(word: str, limit: int = 30):
        """Yield Claude Code-style @ context completions.

        Bare ``@`` or ``@partial`` shows static references and matching
        files/folders.  ``@file:path`` and ``@folder:path`` are handled
        by the existing path completion path.
        """
        lowered = word.lower()

        # Static context references
        _STATIC_REFS = (
            ("@diff", "Git working tree diff"),
            ("@staged", "Git staged diff"),
            ("@file:", "Attach a file"),
            ("@folder:", "Attach a folder"),
            ("@git:", "Git log with diffs (e.g. @git:5)"),
            ("@url:", "Fetch web content"),
        )
        for candidate, meta in _STATIC_REFS:
            if candidate.lower().startswith(lowered) and candidate.lower() != lowered:
                yield Completion(
                    candidate,
                    start_position=-len(word),
                    display=candidate,
                    display_meta=meta,
                )

        # If the user typed @file: or @folder:, delegate to path completions
        for prefix in ("@file:", "@folder:"):
            if word.startswith(prefix):
                path_part = word[len(prefix):] or "."
                expanded = os.path.expanduser(path_part)
                if expanded.endswith("/"):
                    search_dir, match_prefix = expanded, ""
                else:
                    search_dir = os.path.dirname(expanded) or "."
                    match_prefix = os.path.basename(expanded)

                try:
                    entries = os.listdir(search_dir)
                except OSError:
                    return

                count = 0
                prefix_lower = match_prefix.lower()
                for entry in sorted(entries):
                    if match_prefix and not entry.lower().startswith(prefix_lower):
                        continue
                    if count >= limit:
                        break
                    full_path = os.path.join(search_dir, entry)
                    is_dir = os.path.isdir(full_path)
                    display_path = os.path.relpath(full_path)
                    suffix = "/" if is_dir else ""
                    kind = "folder" if is_dir else "file"
                    meta = "dir" if is_dir else _file_size_label(full_path)
                    completion = f"@{kind}:{display_path}{suffix}"
                    yield Completion(
                        completion,
                        start_position=-len(word),
                        display=entry + suffix,
                        display_meta=meta,
                    )
                    count += 1
                return

        # Bare @ or @partial — show matching files/folders from cwd
        query = word[1:]  # strip the @
        if not query:
            search_dir, match_prefix = ".", ""
        else:
            expanded = os.path.expanduser(query)
            if expanded.endswith("/"):
                search_dir, match_prefix = expanded, ""
            else:
                search_dir = os.path.dirname(expanded) or "."
                match_prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = match_prefix.lower()
        for entry in sorted(entries):
            if match_prefix and not entry.lower().startswith(prefix_lower):
                continue
            if entry.startswith("."):
                continue  # skip hidden files in bare @ mode
            if count >= limit:
                break
            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)
            display_path = os.path.relpath(full_path)
            suffix = "/" if is_dir else ""
            kind = "folder" if is_dir else "file"
            meta = "dir" if is_dir else _file_size_label(full_path)
            completion = f"@{kind}:{display_path}{suffix}"
            yield Completion(
                completion,
                start_position=-len(word),
                display=entry + suffix,
                display_meta=meta,
            )
            count += 1

    def _model_completions(self, sub_text: str, sub_lower: str):
        """Yield completions for /model from config aliases + built-in aliases."""
        seen = set()
        # Config-based direct aliases (preferred — include provider info)
        try:
            from kclaw_cli.model_switch import (
                _ensure_direct_aliases, DIRECT_ALIASES, MODEL_ALIASES,
            )
            _ensure_direct_aliases()
            for name, da in DIRECT_ALIASES.items():
                if name.startswith(sub_lower) and name != sub_lower:
                    seen.add(name)
                    yield Completion(
                        name,
                        start_position=-len(sub_text),
                        display=name,
                        display_meta=f"{da.model} ({da.provider})",
                    )
            # Built-in catalog aliases not already covered
            for name in sorted(MODEL_ALIASES.keys()):
                if name in seen:
                    continue
                if name.startswith(sub_lower) and name != sub_lower:
                    identity = MODEL_ALIASES[name]
                    yield Completion(
                        name,
                        start_position=-len(sub_text),
                        display=name,
                        display_meta=f"{identity.vendor}/{identity.family}",
                    )
        except Exception:
            pass

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            # Try @ context completion (Claude Code-style)
            ctx_word = self._extract_context_word(text)
            if ctx_word is not None:
                yield from self._context_completions(ctx_word)
                return
            # Try file path completion for non-slash input
            path_word = self._extract_path_word(text)
            if path_word is not None:
                yield from self._path_completions(path_word)
            return

        # Check if we're completing a subcommand (base command already typed)
        parts = text.split(maxsplit=1)
        base_cmd = parts[0].lower()
        if len(parts) > 1 or (len(parts) == 1 and text.endswith(" ")):
            sub_text = parts[1] if len(parts) > 1 else ""
            sub_lower = sub_text.lower()

            # Dynamic model alias completions for /model
            if " " not in sub_text and base_cmd == "/model":
                yield from self._model_completions(sub_text, sub_lower)
                return

            # Static subcommand completions
            if " " not in sub_text and base_cmd in SUBCOMMANDS:
                for sub in SUBCOMMANDS[base_cmd]:
                    if sub.startswith(sub_lower) and sub != sub_lower:
                        yield Completion(
                            sub,
                            start_position=-len(sub_text),
                            display=sub,
                        )
            return

        word = text[1:]

        for cmd, desc in COMMANDS.items():
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=desc,
                )

        for cmd, info in self._iter_skill_commands().items():
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                description = str(info.get("description", "Skill command"))
                short_desc = description[:50] + ("..." if len(description) > 50 else "")
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=f"⚡ {short_desc}",
                )


# ---------------------------------------------------------------------------
# Inline auto-suggest (ghost text) for slash commands
# ---------------------------------------------------------------------------

class SlashCommandAutoSuggest(AutoSuggest):
    """Inline ghost-text suggestions for slash commands and their subcommands.

    Shows the rest of a command or subcommand in dim text as you type.
    Falls back to history-based suggestions for non-slash input.
    """

    def __init__(
        self,
        history_suggest: AutoSuggest | None = None,
        completer: SlashCommandCompleter | None = None,
    ) -> None:
        self._history = history_suggest
        self._completer = completer  # Reuse its model cache

    def get_suggestion(self, buffer, document):
        text = document.text_before_cursor

        # Only suggest for slash commands
        if not text.startswith("/"):
            # Fall back to history for regular text
            if self._history:
                return self._history.get_suggestion(buffer, document)
            return None

        parts = text.split(maxsplit=1)
        base_cmd = parts[0].lower()

        if len(parts) == 1 and not text.endswith(" "):
            # Still typing the command name: /upd → suggest "ate"
            word = text[1:].lower()
            for cmd in COMMANDS:
                cmd_name = cmd[1:]  # strip leading /
                if cmd_name.startswith(word) and cmd_name != word:
                    return Suggestion(cmd_name[len(word):])
            return None

        # Command is complete — suggest subcommands or model names
        sub_text = parts[1] if len(parts) > 1 else ""
        sub_lower = sub_text.lower()

        # Static subcommands
        if base_cmd in SUBCOMMANDS and SUBCOMMANDS[base_cmd]:
            if " " not in sub_text:
                for sub in SUBCOMMANDS[base_cmd]:
                    if sub.startswith(sub_lower) and sub != sub_lower:
                        return Suggestion(sub[len(sub_text):])

        # Fall back to history
        if self._history:
            return self._history.get_suggestion(buffer, document)
        return None


def _file_size_label(path: str) -> str:
    """Return a compact human-readable file size, or '' on error."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"
