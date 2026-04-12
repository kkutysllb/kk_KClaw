"""技能和内置提示式模式的共享斜杠命令助手。

在 CLI (cli.py) 和 gateway (gateway/run.py) 之间共享，
使两个界面都可以通过 /skill-name 命令和纯提示内置模式（如
/plan）调用技能。
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_skill_commands: Dict[str, Dict[str, Any]] = {}
_PLAN_SLUG_RE = re.compile(r"[^a-z0-9]+")
# 将技能名称清理为干净的连字符分隔 slug 的模式。
_SKILL_INVALID_CHARS = re.compile(r"[^a-z0-9-]")
_SKILL_MULTI_HYPHEN = re.compile(r"-{2,}")


def build_plan_path(
    user_instruction: str = "",
    *,
    now: datetime | None = None,
) -> Path:
    """返回 /plan 调用的默认工作区相对 markdown 路径。

    使用相对路径是有意的：文件工具感知任务/后端，会根据
    活动工作目录为本地、docker、ssh、modal、daytona 等终端后端
    解析路径。这使计划文件保留在活动工作区中，
    而不是 KClaw 主机的全局主目录中。
    """
    slug_source = (user_instruction or "").strip().splitlines()[0] if user_instruction else ""
    slug = _PLAN_SLUG_RE.sub("-", slug_source.lower()).strip("-")
    if slug:
        slug = "-".join(part for part in slug.split("-")[:8] if part)[:48].strip("-")
    slug = slug or "conversation-plan"
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return Path(".kclaw") / "plans" / f"{timestamp}-{slug}.md"


def _load_skill_payload(skill_identifier: str, task_id: str | None = None) -> tuple[dict[str, Any], Path | None, str] | None:
    """按名称/路径加载技能，返回 (loaded_payload, skill_dir, display_name)。"""
    raw_identifier = (skill_identifier or "").strip()
    if not raw_identifier:
        return None

    try:
        from tools.skills_tool import SKILLS_DIR, skill_view

        identifier_path = Path(raw_identifier).expanduser()
        if identifier_path.is_absolute():
            try:
                normalized = str(identifier_path.resolve().relative_to(SKILLS_DIR.resolve()))
            except Exception:
                normalized = raw_identifier
        else:
            normalized = raw_identifier.lstrip("/")

        loaded_skill = json.loads(skill_view(normalized, task_id=task_id))
    except Exception:
        return None

    if not loaded_skill.get("success"):
        return None

    skill_name = str(loaded_skill.get("name") or normalized)
    skill_path = str(loaded_skill.get("path") or "")
    skill_dir = None
    if skill_path:
        try:
            skill_dir = SKILLS_DIR / Path(skill_path).parent
        except Exception:
            skill_dir = None

    return loaded_skill, skill_dir, skill_name


def _inject_skill_config(loaded_skill: dict[str, Any], parts: list[str]) -> None:
    """解析并注入技能声明的配置值到消息部分。

    如果加载的技能的 frontmatter 声明了 ``metadata.kclaw.config``
    条目，其当前值（来自 config.yaml 或默认值）将作为
    ``[Skill config: ...]`` 块追加，以便 agent 无需自己读取
    config.yaml 即可知道配置的值。
    """
    try:
        from agent.skill_utils import (
            extract_skill_config_vars,
            parse_frontmatter,
            resolve_skill_config_values,
        )

        # loaded_skill 字典包含包含 frontmatter 的原始内容
        raw_content = str(loaded_skill.get("raw_content") or loaded_skill.get("content") or "")
        if not raw_content:
            return

        frontmatter, _ = parse_frontmatter(raw_content)
        config_vars = extract_skill_config_vars(frontmatter)
        if not config_vars:
            return

        resolved = resolve_skill_config_values(config_vars)
        if not resolved:
            return

        lines = ["", "[Skill config (from ~/.kclaw/config.yaml):"]
        for key, value in resolved.items():
            display_val = str(value) if value else "(not set)"
            lines.append(f"  {key} = {display_val}")
        lines.append("]")
        parts.extend(lines)
    except Exception:
        pass  # 非关键 — 技能仍可在无配置注入的情况下加载


def _build_skill_message(
    loaded_skill: dict[str, Any],
    skill_dir: Path | None,
    activation_note: str,
    user_instruction: str = "",
    runtime_note: str = "",
) -> str:
    """将加载的技能格式化为用户/系统消息载荷。"""
    from tools.skills_tool import SKILLS_DIR

    content = str(loaded_skill.get("content") or "")

    parts = [activation_note, "", content.strip()]

    # ── 注入已解析的技能配置值 ──
    _inject_skill_config(loaded_skill, parts)

    if loaded_skill.get("setup_skipped"):
        parts.extend(
            [
                "",
                "[技能设置提示：必需的环境设置已跳过。继续加载技能，如果有影响则解释功能降级。]",
            ]
        )
    elif loaded_skill.get("gateway_setup_hint"):
        parts.extend(
            [
                "",
                f"[技能设置提示：{loaded_skill['gateway_setup_hint']}]",
            ]
        )
    elif loaded_skill.get("setup_needed") and loaded_skill.get("setup_note"):
        parts.extend(
            [
                "",
                f"[技能设置提示：{loaded_skill['setup_note']}]",
            ]
        )

    supporting = []
    linked_files = loaded_skill.get("linked_files") or {}
    for entries in linked_files.values():
        if isinstance(entries, list):
            supporting.extend(entries)

    if not supporting and skill_dir:
        for subdir in ("references", "templates", "scripts", "assets"):
            subdir_path = skill_dir / subdir
            if subdir_path.exists():
                for f in sorted(subdir_path.rglob("*")):
                    if f.is_file():
                        rel = str(f.relative_to(skill_dir))
                        supporting.append(rel)

    if supporting and skill_dir:
        try:
            skill_view_target = str(skill_dir.relative_to(SKILLS_DIR))
        except ValueError:
            # 技能来自外部目录 — 改用技能名称
            skill_view_target = skill_dir.name
        parts.append("")
        parts.append("[此技能有支持文件，可通过 skill_view 工具加载：]")
        for sf in supporting:
            parts.append(f"- {sf}")
        parts.append(
            f'\n要查看这些文件，请使用：skill_view(name="{skill_view_target}", file_path="<path>")'
        )

    if user_instruction:
        parts.append("")
        parts.append(f"用户在技能调用时提供了以下指令：{user_instruction}")

    if runtime_note:
        parts.append("")
        parts.append(f"[运行时提示：{runtime_note}]")

    return "\n".join(parts)


def scan_skill_commands() -> Dict[str, Dict[str, Any]]:
    """扫描 ~/.kclaw/skills/ 并返回 /command -> 技能信息的映射。

    返回:
        将 "/skill-name" 映射到 {name, description, skill_md_path, skill_dir} 的字典。
    """
    global _skill_commands
    _skill_commands = {}
    try:
        from tools.skills_tool import SKILLS_DIR, _parse_frontmatter, skill_matches_platform, _get_disabled_skill_names
        from agent.skill_utils import get_external_skills_dirs
        disabled = _get_disabled_skill_names()
        seen_names: set = set()

        # 先扫描本地目录，再扫描外部目录
        dirs_to_scan = []
        if SKILLS_DIR.exists():
            dirs_to_scan.append(SKILLS_DIR)
        dirs_to_scan.extend(get_external_skills_dirs())

        for scan_dir in dirs_to_scan:
            for skill_md in scan_dir.rglob("SKILL.md"):
                if any(part in ('.git', '.github', '.hub') for part in skill_md.parts):
                    continue
                try:
                    content = skill_md.read_text(encoding='utf-8')
                    frontmatter, body = _parse_frontmatter(content)
                    # 跳过与当前 OS 平台不兼容的技能
                    if not skill_matches_platform(frontmatter):
                        continue
                    name = frontmatter.get('name', skill_md.parent.name)
                    if name in seen_names:
                        continue
                    # 尊重用户禁用技能的配置
                    if name in disabled:
                        continue
                    description = frontmatter.get('description', '')
                    if not description:
                        for line in body.strip().split('\n'):
                            line = line.strip()
                            if line and not line.startswith('#'):
                                description = line[:80]
                                break
                    seen_names.add(name)
                    # 规范化为连字符分隔的 slug，去除
                    # 非字母数字字符（如 +, /），避免下游
                    # 出现无效的 Telegram 命令名称。
                    cmd_name = name.lower().replace(' ', '-').replace('_', '-')
                    cmd_name = _SKILL_INVALID_CHARS.sub('', cmd_name)
                    cmd_name = _SKILL_MULTI_HYPHEN.sub('-', cmd_name).strip('-')
                    if not cmd_name:
                        continue
                    _skill_commands[f"/{cmd_name}"] = {
                        "name": name,
                        "description": description or f"Invoke the {name} skill",
                        "skill_md_path": str(skill_md),
                        "skill_dir": str(skill_md.parent),
                    }
                except Exception:
                    continue
    except Exception:
        pass
    return _skill_commands


def get_skill_commands() -> Dict[str, Dict[str, Any]]:
    """返回当前的技能命令映射（如果为空则先扫描）。"""
    if not _skill_commands:
        scan_skill_commands()
    return _skill_commands


def resolve_skill_command_key(command: str) -> Optional[str]:
    """将用户输入的 /command 解析为其规范 skill_cmds 键。

    技能始终以连字符存储 — ``scan_skill_commands`` 在构建键时
    将空格和下划线规范化为连字符。用户输入中的连字符和
    下划线可互换使用：这与 ``_check_unavailable_skill`` 匹配，
    并适应 Telegram 机器人命令名称（不允许连字符，因此
    ``/claude-code`` 注册为 ``/claude_code`` 并以下划线形式返回）。

    返回 ``get_skill_commands()`` 中匹配的 ``/slug`` 键，
    如果没有匹配则返回 ``None``。
    """
    if not command:
        return None
    cmd_key = f"/{command.replace('_', '-')}"
    return cmd_key if cmd_key in get_skill_commands() else None


def build_skill_invocation_message(
    cmd_key: str,
    user_instruction: str = "",
    task_id: str | None = None,
    runtime_note: str = "",
) -> Optional[str]:
    """构建技能斜杠命令调用的用户消息内容。

    参数:
        cmd_key: 包含前导斜杠的命令键（如 "/gif-search"）。
        user_instruction: 用户在命令后输入的可选文本。

    返回:
        格式化的消息字符串，如果未找到技能则返回 None。
    """
    commands = get_skill_commands()
    skill_info = commands.get(cmd_key)
    if not skill_info:
        return None

    loaded = _load_skill_payload(skill_info["skill_dir"], task_id=task_id)
    if not loaded:
        return f"[加载技能失败：{skill_info['name']}]"

    loaded_skill, skill_dir, skill_name = loaded
    activation_note = (
        f'[系统：用户已调用 "{skill_name}" 技能，表示他们希望'
        "你遵循其指令。完整技能内容已加载如下。]"
    )
    return _build_skill_message(
        loaded_skill,
        skill_dir,
        activation_note,
        user_instruction=user_instruction,
        runtime_note=runtime_note,
    )


def build_preloaded_skills_prompt(
    skill_identifiers: list[str],
    task_id: str | None = None,
) -> tuple[str, list[str], list[str]]:
    """为一个或多个技能加载会话级 CLI 预加载。

    返回 (prompt_text, loaded_skill_names, missing_identifiers)。
    """
    prompt_parts: list[str] = []
    loaded_names: list[str] = []
    missing: list[str] = []

    seen: set[str] = set()
    for raw_identifier in skill_identifiers:
        identifier = (raw_identifier or "").strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)

        loaded = _load_skill_payload(identifier, task_id=task_id)
        if not loaded:
            missing.append(identifier)
            continue

        loaded_skill, skill_dir, skill_name = loaded
        activation_note = (
            f'[系统：用户启动此 CLI 会话时预加载了 "{skill_name}" 技能。'
            "将其指令视为本次会话的活动指引，除非用户覆盖。]"
        )
        prompt_parts.append(
            _build_skill_message(
                loaded_skill,
                skill_dir,
                activation_note,
            )
        )
        loaded_names.append(skill_name)

    return "\n\n".join(prompt_parts), loaded_names, missing
