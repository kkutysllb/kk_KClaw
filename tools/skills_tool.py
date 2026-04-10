#!/usr/bin/env python3
"""
技能工具模块

本模块提供用于列出和查看技能文档的工具。
技能被组织为包含 SKILL.md 文件（主要说明）的目录，
以及可选的支持文件，如参考资料、模板和示例。

灵感来自 Anthropic 的 Claude Skills 系统，采用渐进式披露架构：
- 元数据（名称 ≤64 字符，描述 ≤1024 字符）——在 skills_list 中显示
- 完整说明——在需要时通过 skill_view 加载
- 链接文件（参考资料、模板）——按需加载

目录结构：
    skills/
    ├── my-skill/
    │   ├── SKILL.md           # 主要说明（必需）
    │   ├── references/        # 支持文档
    │   │   ├── api.md
    │   │   └── examples.md
    │   ├── templates/         # 输出模板
    │   │   └── template.md
    │   └── assets/            # 补充文件（agentskills.io 标准）
    └── category/              # 用于组织的类别文件夹
        └── another-skill/
            └── SKILL.md

SKILL.md 格式（YAML Frontmatter，兼容 agentskills.io）：
    ---
    name: skill-name              # 必需，最多 64 字符
    description: Brief description # 必需，最多 1024 字符
    version: 1.0.0                # 可选
    license: MIT                  # 可选（agentskills.io）
    platforms: [macos]            # 可选——限制特定 OS 平台
                                  #   有效值：macos, linux, windows
                                  #   省略则在所有平台加载（默认）
    prerequisites:                # 可选——旧版运行时需求
      env_vars: [API_KEY]         #   旧版环境变量名在加载时会被规范化到
                                  #   required_environment_variables。
      commands: [curl, jq]        #   命令检查仅作为建议保留。
    compatibility: Requires X     # 可选（agentskills.io）
    metadata:                     # 可选，任意键值（agentskills.io）
      kclaw:
        tags: [fine-tuning, llm]
        related_skills: [peft, lora]
    ---

    # Skill Title

    完整说明和内容在此...

可用工具：
- skills_list：列出带元数据的技能（渐进式披露第 1 层）
- skill_view：加载完整技能内容（渐进式披露第 2-3 层）

用法：
    from tools.skills_tool import skills_list, skill_view, check_skills_requirements

    # 列出所有技能（仅返回元数据——节省 token）
    result = skills_list()

    # 查看技能的主要内容（加载完整说明）
    content = skill_view("axolotl")

    # 查看技能中的参考文件（加载链接文件）
    content = skill_view("axolotl", "references/dataset-formats.md")
"""

import json
import logging

from kclaw_constants import get_kclaw_home
import os
import re
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


# 所有技能位于 ~/.kclaw/skills/（安装时从绑定的 skills/ 填充）。
# 这是唯一的真实来源——代理编辑、hub 安装和绑定的
# 技能都共存于此，不会污染 git 仓库。
KCLAW_HOME = get_kclaw_home()
SKILLS_DIR = KCLAW_HOME / "skills"

# Anthropic 推荐的渐进式披露效率限制
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

# 'platforms' frontmatter 字段的平台标识符。
# 将用户友好名称映射到 sys.platform 前缀。
_PLATFORM_MAP = {
    "macos": "darwin",
    "linux": "linux",
    "windows": "win32",
}
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXCLUDED_SKILL_DIRS = frozenset((".git", ".github", ".hub"))
_REMOTE_ENV_BACKENDS = frozenset({"docker", "singularity", "modal", "ssh", "daytona"})
_secret_capture_callback = None


def load_env() -> Dict[str, str]:
    """从 KCLAW_HOME/.env 加载配置作用域的环境变量。"""
    env_path = get_kclaw_home() / ".env"
    env_vars: Dict[str, str] = {}
    if not env_path.exists():
        return env_vars

    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip().strip("\"'")
    return env_vars


class SkillReadinessStatus(str, Enum):
    AVAILABLE = "available"
    SETUP_NEEDED = "setup_needed"
    UNSUPPORTED = "unsupported"


def set_secret_capture_callback(callback) -> None:
    global _secret_capture_callback
    _secret_capture_callback = callback


def skill_matches_platform(frontmatter: Dict[str, Any]) -> bool:
    """检查技能是否与当前 OS 平台兼容。

    委托给 ``agent.skill_utils.skill_matches_platform``——在此保留
    作为公共重导出，以便现有调用者无需更新。
    """
    from agent.skill_utils import skill_matches_platform as _impl
    return _impl(frontmatter)


def _normalize_prerequisite_values(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item) for item in value if str(item).strip()]


def _collect_prerequisite_values(
    frontmatter: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    prereqs = frontmatter.get("prerequisites")
    if not prereqs or not isinstance(prereqs, dict):
        return [], []
    return (
        _normalize_prerequisite_values(prereqs.get("env_vars")),
        _normalize_prerequisite_values(prereqs.get("commands")),
    )


def _normalize_setup_metadata(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    setup = frontmatter.get("setup")
    if not isinstance(setup, dict):
        return {"help": None, "collect_secrets": []}

    help_text = setup.get("help")
    normalized_help = (
        str(help_text).strip()
        if isinstance(help_text, str) and help_text.strip()
        else None
    )

    collect_secrets_raw = setup.get("collect_secrets")
    if isinstance(collect_secrets_raw, dict):
        collect_secrets_raw = [collect_secrets_raw]
    if not isinstance(collect_secrets_raw, list):
        collect_secrets_raw = []

    collect_secrets: List[Dict[str, Any]] = []
    for item in collect_secrets_raw:
        if not isinstance(item, dict):
            continue

        env_var = str(item.get("env_var") or "").strip()
        if not env_var:
            continue

        prompt = str(item.get("prompt") or f"Enter value for {env_var}").strip()
        provider_url = str(item.get("provider_url") or item.get("url") or "").strip()

        entry: Dict[str, Any] = {
            "env_var": env_var,
            "prompt": prompt,
            "secret": bool(item.get("secret", True)),
        }
        if provider_url:
            entry["provider_url"] = provider_url
        collect_secrets.append(entry)

    return {
        "help": normalized_help,
        "collect_secrets": collect_secrets,
    }


def _get_required_environment_variables(
    frontmatter: Dict[str, Any],
    legacy_env_vars: List[str] | None = None,
) -> List[Dict[str, Any]]:
    setup = _normalize_setup_metadata(frontmatter)
    required_raw = frontmatter.get("required_environment_variables")
    if isinstance(required_raw, dict):
        required_raw = [required_raw]
    if not isinstance(required_raw, list):
        required_raw = []

    required: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _append_required(entry: Dict[str, Any]) -> None:
        env_name = str(entry.get("name") or entry.get("env_var") or "").strip()
        if not env_name or env_name in seen:
            return
        if not _ENV_VAR_NAME_RE.match(env_name):
            return

        normalized: Dict[str, Any] = {
            "name": env_name,
            "prompt": str(entry.get("prompt") or f"Enter value for {env_name}").strip(),
        }

        help_text = (
            entry.get("help")
            or entry.get("provider_url")
            or entry.get("url")
            or setup.get("help")
        )
        if isinstance(help_text, str) and help_text.strip():
            normalized["help"] = help_text.strip()

        required_for = entry.get("required_for")
        if isinstance(required_for, str) and required_for.strip():
            normalized["required_for"] = required_for.strip()

        seen.add(env_name)
        required.append(normalized)

    for item in required_raw:
        if isinstance(item, str):
            _append_required({"name": item})
            continue
        if isinstance(item, dict):
            _append_required(item)

    for item in setup["collect_secrets"]:
        _append_required(
            {
                "name": item.get("env_var"),
                "prompt": item.get("prompt"),
                "help": item.get("provider_url") or setup.get("help"),
            }
        )

    if legacy_env_vars is None:
        legacy_env_vars, _ = _collect_prerequisite_values(frontmatter)
    for env_var in legacy_env_vars:
        _append_required({"name": env_var})

    return required


def _capture_required_environment_variables(
    skill_name: str,
    missing_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not missing_entries:
        return {
            "missing_names": [],
            "setup_skipped": False,
            "gateway_setup_hint": None,
        }

    missing_names = [entry["name"] for entry in missing_entries]
    if _is_gateway_surface():
        return {
            "missing_names": missing_names,
            "setup_skipped": False,
            "gateway_setup_hint": _gateway_setup_hint(),
        }

    if _secret_capture_callback is None:
        return {
            "missing_names": missing_names,
            "setup_skipped": False,
            "gateway_setup_hint": None,
        }

    setup_skipped = False
    remaining_names: List[str] = []

    for entry in missing_entries:
        metadata = {"skill_name": skill_name}
        if entry.get("help"):
            metadata["help"] = entry["help"]
        if entry.get("required_for"):
            metadata["required_for"] = entry["required_for"]

        try:
            callback_result = _secret_capture_callback(
                entry["name"],
                entry["prompt"],
                metadata,
            )
        except Exception:
            logger.warning(
                f"Secret capture callback failed for {entry['name']}", exc_info=True
            )
            callback_result = {
                "success": False,
                "stored_as": entry["name"],
                "validated": False,
                "skipped": True,
            }

        success = isinstance(callback_result, dict) and bool(
            callback_result.get("success")
        )
        skipped = isinstance(callback_result, dict) and bool(
            callback_result.get("skipped")
        )
        if success and not skipped:
            continue

        setup_skipped = True
        remaining_names.append(entry["name"])

    return {
        "missing_names": remaining_names,
        "setup_skipped": setup_skipped,
        "gateway_setup_hint": None,
    }


def _is_gateway_surface() -> bool:
    if os.getenv("KCLAW_GATEWAY_SESSION"):
        return True
    return bool(os.getenv("KCLAW_SESSION_PLATFORM"))


def _get_terminal_backend_name() -> str:
    return str(os.getenv("TERMINAL_ENV", "local")).strip().lower() or "local"


def _is_env_var_persisted(
    var_name: str, env_snapshot: Dict[str, str] | None = None
) -> bool:
    if env_snapshot is None:
        env_snapshot = load_env()
    if var_name in env_snapshot:
        return bool(env_snapshot.get(var_name))
    return bool(os.getenv(var_name))


def _remaining_required_environment_names(
    required_env_vars: List[Dict[str, Any]],
    capture_result: Dict[str, Any],
    *,
    env_snapshot: Dict[str, str] | None = None,
) -> List[str]:
    missing_names = set(capture_result["missing_names"])

    if env_snapshot is None:
        env_snapshot = load_env()
    remaining = []
    for entry in required_env_vars:
        name = entry["name"]
        if name in missing_names or not _is_env_var_persisted(name, env_snapshot):
            remaining.append(name)
    return remaining


def _gateway_setup_hint() -> str:
    try:
        from gateway.platforms.base import GATEWAY_SECRET_CAPTURE_UNSUPPORTED_MESSAGE

        return GATEWAY_SECRET_CAPTURE_UNSUPPORTED_MESSAGE
    except Exception:
        return "Secure secret entry is not available. Load this skill in the local CLI to be prompted, or add the key to ~/.kclaw/.env manually."


def _build_setup_note(
    readiness_status: SkillReadinessStatus,
    missing: List[str],
    setup_help: str | None = None,
) -> str | None:
    if readiness_status == SkillReadinessStatus.SETUP_NEEDED:
        missing_str = ", ".join(missing) if missing else "required prerequisites"
        note = f"Setup needed before using this skill: missing {missing_str}."
        if setup_help:
            return f"{note} {setup_help}"
        return note
    return None


def check_skills_requirements() -> bool:
    """技能始终可用——如果需要，目录会在首次使用时创建。"""
    return True


def _parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """从 markdown 内容解析 YAML frontmatter。

    委托给 ``agent.skill_utils.parse_frontmatter``——在此保留
    作为公共重导出，以便现有调用者无需更新。
    """
    from agent.skill_utils import parse_frontmatter
    return parse_frontmatter(content)


def _get_category_from_path(skill_path: Path) -> Optional[str]:
    """
    基于目录结构从技能路径提取类别。

    对于路径如：~/.kclaw/skills/mlops/axolotl/SKILL.md -> "mlops"
    也适用于通过 skills.external_dirs 配置的外部技能目录。
    """
    # 首先尝试模块级 SKILLS_DIR（尊重测试中的 monkeypatching），
    # 然后回退到配置中的外部目录。
    dirs_to_check = [SKILLS_DIR]
    try:
        from agent.skill_utils import get_external_skills_dirs
        dirs_to_check.extend(get_external_skills_dirs())
    except Exception:
        pass
    for skills_dir in dirs_to_check:
        try:
            rel_path = skill_path.relative_to(skills_dir)
            parts = rel_path.parts
            if len(parts) >= 3:
                return parts[0]
        except ValueError:
            continue
    return None


def _estimate_tokens(content: str) -> int:
    """
    粗略的 token 估算（平均每 token 4 字符）。

    参数：
        content: 文本内容

    返回：
        估算的 token 数量
    """
    return len(content) // 4


def _parse_tags(tags_value) -> List[str]:
    """
    从 frontmatter 值解析标签。

    处理：
    - 已解析的列表（来自 yaml.safe_load）：[tag1, tag2]
    - 带括号的字符串："[tag1, tag2]"
    - 逗号分隔的字符串："tag1, tag2"

    参数：
        tags_value: 原始标签值——可能是列表或字符串

    返回：
        标签字符串列表
    """
    if not tags_value:
        return []

    # yaml.safe_load 已经为 [tag1, tag2] 返回一个列表
    if isinstance(tags_value, list):
        return [str(t).strip() for t in tags_value if t]

    # 字符串回退——处理带括号或逗号分隔的情况
    tags_value = str(tags_value).strip()
    if tags_value.startswith("[") and tags_value.endswith("]"):
        tags_value = tags_value[1:-1]

    return [t.strip().strip("\"'") for t in tags_value.split(",") if t.strip()]



def _get_disabled_skill_names() -> Set[str]:
    """从配置加载禁用的技能名称。

    委托给 ``agent.skill_utils.get_disabled_skill_names``——在此保留
    作为公共重导出，以便现有调用者无需更新。
    """
    from agent.skill_utils import get_disabled_skill_names
    return get_disabled_skill_names()


def _is_skill_disabled(name: str, platform: str = None) -> bool:
    """检查技能是否在配置中被禁用。"""
    import os
    try:
        from kclaw_cli.config import load_config
        config = load_config()
        skills_cfg = config.get("skills", {})
        resolved_platform = platform or os.getenv("KCLAW_PLATFORM")
        if resolved_platform:
            platform_disabled = skills_cfg.get("platform_disabled", {}).get(resolved_platform)
            if platform_disabled is not None:
                return name in platform_disabled
        return name in skills_cfg.get("disabled", [])
    except Exception:
        return False


def _find_all_skills(*, skip_disabled: bool = False) -> List[Dict[str, Any]]:
    """递归查找 ~/.kclaw/skills/ 和外部目录中的所有技能。

    参数：
        skip_disabled: 如果为 True，返回所有技能而不管禁用
            状态（由 ``kclaw skills`` 配置 UI 使用）。默认为 False，
            会过滤掉禁用的技能。

    返回：
        技能元数据字典列表（name, description, category）。
    """
    from agent.skill_utils import get_external_skills_dirs

    skills = []
    seen_names: set = set()

    # 加载禁用集合一次（不是每个技能一次）
    disabled = set() if skip_disabled else _get_disabled_skill_names()

    # 首先扫描本地目录，然后是外部目录（本地优先）
    dirs_to_scan = []
    if SKILLS_DIR.exists():
        dirs_to_scan.append(SKILLS_DIR)
    dirs_to_scan.extend(get_external_skills_dirs())

    for scan_dir in dirs_to_scan:
        for skill_md in scan_dir.rglob("SKILL.md"):
            if any(part in _EXCLUDED_SKILL_DIRS for part in skill_md.parts):
                continue

            skill_dir = skill_md.parent

            try:
                content = skill_md.read_text(encoding="utf-8")[:4000]
                frontmatter, body = _parse_frontmatter(content)

                if not skill_matches_platform(frontmatter):
                    continue

                name = frontmatter.get("name", skill_dir.name)[:MAX_NAME_LENGTH]
                if name in seen_names:
                    continue
                if name in disabled:
                    continue

                description = frontmatter.get("description", "")
                if not description:
                    for line in body.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            description = line
                            break

                if len(description) > MAX_DESCRIPTION_LENGTH:
                    description = description[:MAX_DESCRIPTION_LENGTH - 3] + "..."

                category = _get_category_from_path(skill_md)

                seen_names.add(name)
                skills.append({
                    "name": name,
                    "description": description,
                    "category": category,
                })

            except (UnicodeDecodeError, PermissionError) as e:
                logger.debug("Failed to read skill file %s: %s", skill_md, e)
                continue
            except Exception as e:
                logger.debug(
                    "Skipping skill at %s: failed to parse: %s", skill_md, e, exc_info=True
                )
                continue

    return skills


def _load_category_description(category_dir: Path) -> Optional[str]:
    """
    如果存在，从 DESCRIPTION.md 加载类别描述。

    参数：
        category_dir: 类别目录的路径

    返回：
        描述字符串，如果未找到则返回 None
    """
    desc_file = category_dir / "DESCRIPTION.md"
    if not desc_file.exists():
        return None

    try:
        content = desc_file.read_text(encoding="utf-8")
        # 如果存在则解析 frontmatter
        frontmatter, body = _parse_frontmatter(content)

        # 优先使用 frontmatter 描述，回退到第一个非标题行
        description = frontmatter.get("description", "")
        if not description:
            for line in body.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line
                    break

        # 截断到合理长度
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[: MAX_DESCRIPTION_LENGTH - 3] + "..."

        return description if description else None
    except (UnicodeDecodeError, PermissionError) as e:
        logger.debug("Failed to read category description %s: %s", desc_file, e)
        return None
    except Exception as e:
        logger.warning(
            "Error parsing category description %s: %s", desc_file, e, exc_info=True
        )
        return None


def skills_categories(verbose: bool = False, task_id: str = None) -> str:
    """
    列出可用的技能类别及其描述（渐进式披露第 0 层）。

    返回类别名称和描述，以便在深入了解之前进行高效发现。
    类别可以有一个 DESCRIPTION.md 文件，其中包含描述 frontmatter 字段
    或第一段来解释该类别中有哪些技能。

    参数：
        verbose: 如果为 True，包含每个类别的技能计数（默认：False，但目前始终包含）
        task_id: 用于探测活动后端的可选任务标识符

    返回：
        包含类别列表及其描述的 JSON 字符串
    """
    try:
        # 使用模块级 SKILLS_DIR（尊重 monkeypatching）+ 外部目录
        all_dirs = [SKILLS_DIR] if SKILLS_DIR.exists() else []
        try:
            from agent.skill_utils import get_external_skills_dirs
            all_dirs.extend(d for d in get_external_skills_dirs() if d.exists())
        except Exception:
            pass
        if not all_dirs:
            return json.dumps(
                {
                    "success": True,
                    "categories": [],
                    "message": "No skills directory found.",
                },
                ensure_ascii=False,
            )

        category_dirs = {}
        category_counts: Dict[str, int] = {}
        for scan_dir in all_dirs:
            for skill_md in scan_dir.rglob("SKILL.md"):
                if any(part in _EXCLUDED_SKILL_DIRS for part in skill_md.parts):
                    continue

                try:
                    frontmatter, _ = _parse_frontmatter(
                        skill_md.read_text(encoding="utf-8")[:4000]
                    )
                except Exception:
                    frontmatter = {}

                if not skill_matches_platform(frontmatter):
                    continue

                category = _get_category_from_path(skill_md)
                if category:
                    category_counts[category] = category_counts.get(category, 0) + 1
                    if category not in category_dirs:
                        category_dirs[category] = skill_md.parent.parent

        categories = []
        for name in sorted(category_dirs.keys()):
            category_dir = category_dirs[name]
            description = _load_category_description(category_dir)

            cat_entry = {"name": name, "skill_count": category_counts[name]}
            if description:
                cat_entry["description"] = description
            categories.append(cat_entry)

        return json.dumps(
            {
                "success": True,
                "categories": categories,
                "hint": "If a category is relevant to your task, use skills_list with that category to see available skills",
            },
            ensure_ascii=False,
        )

    except Exception as e:
        return tool_error(str(e), success=False)


def skills_list(category: str = None, task_id: str = None) -> str:
    """
    列出所有可用技能（渐进式披露第 1 层——最小元数据）。

    仅返回名称 + 描述以最小化 token 使用。使用 skill_view()
    加载完整内容、标签、相关文件等。

    参数：
        category: 可选的类别过滤器（例如 "mlops"）
        task_id: 用于探测活动后端的可选任务标识符

    返回：
        包含最小技能信息的 JSON 字符串：name, description, category
    """
    try:
        if not SKILLS_DIR.exists():
            SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            return json.dumps(
                {
                    "success": True,
                    "skills": [],
                    "categories": [],
                    "message": "No skills found. Skills directory created at ~/.kclaw/skills/",
                },
                ensure_ascii=False,
            )

        # 查找所有技能
        all_skills = _find_all_skills()

        if not all_skills:
            return json.dumps(
                {
                    "success": True,
                    "skills": [],
                    "categories": [],
                    "message": "No skills found in skills/ directory.",
                },
                ensure_ascii=False,
            )

        # 如果指定了则按类别过滤
        if category:
            all_skills = [s for s in all_skills if s.get("category") == category]

        # 按类别然后名称排序
        all_skills.sort(key=lambda s: (s.get("category") or "", s["name"]))

        # 提取唯一样类别
        categories = sorted(
            set(s.get("category") for s in all_skills if s.get("category"))
        )

        return json.dumps(
            {
                "success": True,
                "skills": all_skills,
                "categories": categories,
                "count": len(all_skills),
                "hint": "Use skill_view(name) to see full content, tags, and linked files",
            },
            ensure_ascii=False,
        )

    except Exception as e:
        return tool_error(str(e), success=False)


def skill_view(name: str, file_path: str = None, task_id: str = None) -> str:
    """
    查看技能的内容或技能目录中的特定文件。

    参数：
        name: 技能的名称或路径（例如 "axolotl" 或 "03-fine-tuning/axolotl"）
        file_path: 技能内特定文件的可选路径（例如 "references/api.md"）
        task_id: 用于探测活动后端的可选任务标识符

    返回：
        包含技能内容或错误消息的 JSON 字符串
    """
    try:
        from agent.skill_utils import get_external_skills_dirs

        # 构建要搜索的所有技能目录列表
        all_dirs = []
        if SKILLS_DIR.exists():
            all_dirs.append(SKILLS_DIR)
        all_dirs.extend(get_external_skills_dirs())

        if not all_dirs:
            return json.dumps(
                {
                    "success": False,
                    "error": "Skills directory does not exist yet. It will be created on first install.",
                },
                ensure_ascii=False,
            )

        skill_dir = None
        skill_md = None

        # 搜索所有目录：本地优先，然后是外部（首次匹配优先）
        for search_dir in all_dirs:
            # 首先尝试直接路径（例如 "mlops/axolotl"）
            direct_path = search_dir / name
            if direct_path.is_dir() and (direct_path / "SKILL.md").exists():
                skill_dir = direct_path
                skill_md = direct_path / "SKILL.md"
                break
            elif direct_path.with_suffix(".md").exists():
                skill_md = direct_path.with_suffix(".md")
                break

        # 通过目录名在所有目录中搜索
        if not skill_md:
            for search_dir in all_dirs:
                for found_skill_md in search_dir.rglob("SKILL.md"):
                    if found_skill_md.parent.name == name:
                        skill_dir = found_skill_md.parent
                        skill_md = found_skill_md
                        break
                if skill_md:
                    break

        # 遗留：平面 .md 文件
        if not skill_md:
            for search_dir in all_dirs:
                for found_md in search_dir.rglob(f"{name}.md"):
                    if found_md.name != "SKILL.md":
                        skill_md = found_md
                        break
                if skill_md:
                    break

        if not skill_md or not skill_md.exists():
            available = [s["name"] for s in _find_all_skills()[:20]]
            return json.dumps(
                {
                    "success": False,
                    "error": f"Skill '{name}' not found.",
                    "available_skills": available,
                    "hint": "Use skills_list to see all available skills",
                },
                ensure_ascii=False,
            )

        # 读取文件一次——在下面的平台检查和主要内容中重用
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Failed to read skill '{name}': {e}",
                },
                ensure_ascii=False,
            )

        # 安全：如果技能从受信任目录外部加载则发出警告
        #（本地 skills 目录 + 配置的 external_dirs 都是受信任的）
        _outside_skills_dir = True
        _trusted_dirs = [SKILLS_DIR.resolve()]
        try:
            _trusted_dirs.extend(d.resolve() for d in all_dirs[1:])
        except Exception:
            pass
        for _td in _trusted_dirs:
            try:
                skill_md.resolve().relative_to(_td)
                _outside_skills_dir = False
                break
            except ValueError:
                continue

        # 安全：检测常见的提示注入模式
        _INJECTION_PATTERNS = [
            "ignore previous instructions",
            "ignore all previous",
            "you are now",
            "disregard your",
            "forget your instructions",
            "new instructions:",
            "system prompt:",
            "<system>",
            "]]>",
        ]
        _content_lower = content.lower()
        _injection_detected = any(p in _content_lower for p in _INJECTION_PATTERNS)

        if _outside_skills_dir or _injection_detected:
            _warnings = []
            if _outside_skills_dir:
                _warnings.append(f"skill file is outside the trusted skills directory (~/.kclaw/skills/): {skill_md}")
            if _injection_detected:
                _warnings.append("skill content contains patterns that may indicate prompt injection")
            import logging as _logging
            _logging.getLogger(__name__).warning("Skill security warning for '%s': %s", name, "; ".join(_warnings))

        parsed_frontmatter: Dict[str, Any] = {}
        try:
            parsed_frontmatter, _ = _parse_frontmatter(content)
        except Exception:
            parsed_frontmatter = {}

        if not skill_matches_platform(parsed_frontmatter):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Skill '{name}' is not supported on this platform.",
                    "readiness_status": SkillReadinessStatus.UNSUPPORTED.value,
                },
                ensure_ascii=False,
            )

        # Check if the skill is disabled by the user
        resolved_name = parsed_frontmatter.get("name", skill_md.parent.name)
        if _is_skill_disabled(resolved_name):
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"Skill '{resolved_name}' is disabled. "
                        "Enable it with `kclaw skills` or inspect the files directly on disk."
                    ),
                },
                ensure_ascii=False,
            )

        # 如果请求了特定文件路径，则读取该文件
        if file_path and skill_dir:
            # 安全：防止路径遍历攻击
            normalized_path = Path(file_path)
            if ".." in normalized_path.parts:
                return json.dumps(
                    {
                        "success": False,
                        "error": "Path traversal ('..') is not allowed.",
                        "hint": "Use a relative path within the skill directory",
                    },
                    ensure_ascii=False,
                )

            target_file = skill_dir / file_path

            # 安全：验证解析后的路径仍在技能目录内
            try:
                resolved = target_file.resolve()
                skill_dir_resolved = skill_dir.resolve()
                if not resolved.is_relative_to(skill_dir_resolved):
                    return json.dumps(
                        {
                            "success": False,
                            "error": "Path escapes skill directory boundary.",
                            "hint": "Use a relative path within the skill directory",
                        },
                        ensure_ascii=False,
                    )
            except (OSError, ValueError):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Invalid file path: '{file_path}'",
                        "hint": "Use a valid relative path within the skill directory",
                    },
                    ensure_ascii=False,
                )
            if not target_file.exists():
                # 列出技能目录中可用的文件，按类型组织
                available_files = {
                    "references": [],
                    "templates": [],
                    "assets": [],
                    "scripts": [],
                    "other": [],
                }

                # Scan for all readable files
                for f in skill_dir.rglob("*"):
                    if f.is_file() and f.name != "SKILL.md":
                        rel = str(f.relative_to(skill_dir))
                        if rel.startswith("references/"):
                            available_files["references"].append(rel)
                        elif rel.startswith("templates/"):
                            available_files["templates"].append(rel)
                        elif rel.startswith("assets/"):
                            available_files["assets"].append(rel)
                        elif rel.startswith("scripts/"):
                            available_files["scripts"].append(rel)
                        elif f.suffix in [
                            ".md",
                            ".py",
                            ".yaml",
                            ".yml",
                            ".json",
                            ".tex",
                            ".sh",
                        ]:
                            available_files["other"].append(rel)

                # 移除空类别
                available_files = {k: v for k, v in available_files.items() if v}

                return json.dumps(
                    {
                        "success": False,
                        "error": f"File '{file_path}' not found in skill '{name}'.",
                        "available_files": available_files,
                        "hint": "Use one of the available file paths listed above",
                    },
                    ensure_ascii=False,
                )

            # 读取文件内容
            try:
                content = target_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # 二进制文件——返回关于它的信息
                return json.dumps(
                    {
                        "success": True,
                        "name": name,
                        "file": file_path,
                        "content": f"[Binary file: {target_file.name}, size: {target_file.stat().st_size} bytes]",
                        "is_binary": True,
                    },
                    ensure_ascii=False,
                )

            return json.dumps(
                {
                    "success": True,
                    "name": name,
                    "file": file_path,
                    "content": content,
                    "file_type": target_file.suffix,
                },
                ensure_ascii=False,
            )

        # Reuse the parse from the platform check above
        frontmatter = parsed_frontmatter

        # Get reference, template, asset, and script files if this is a directory-based skill
        reference_files = []
        template_files = []
        asset_files = []
        script_files = []

        if skill_dir:
            references_dir = skill_dir / "references"
            if references_dir.exists():
                reference_files = [
                    str(f.relative_to(skill_dir)) for f in references_dir.glob("*.md")
                ]

            templates_dir = skill_dir / "templates"
            if templates_dir.exists():
                for ext in [
                    "*.md",
                    "*.py",
                    "*.yaml",
                    "*.yml",
                    "*.json",
                    "*.tex",
                    "*.sh",
                ]:
                    template_files.extend(
                        [
                            str(f.relative_to(skill_dir))
                            for f in templates_dir.rglob(ext)
                        ]
                    )

            # assets/ — agentskills.io standard directory for supplementary files
            assets_dir = skill_dir / "assets"
            if assets_dir.exists():
                for f in assets_dir.rglob("*"):
                    if f.is_file():
                        asset_files.append(str(f.relative_to(skill_dir)))

            scripts_dir = skill_dir / "scripts"
            if scripts_dir.exists():
                for ext in ["*.py", "*.sh", "*.bash", "*.js", "*.ts", "*.rb"]:
                    script_files.extend(
                        [str(f.relative_to(skill_dir)) for f in scripts_dir.glob(ext)]
                    )

        # 读取标签/相关技能时保持向后兼容：
        # 首先检查 metadata.kclaw.*（agentskills.io 约定），回退到顶层
        kclaw_meta = {}
        metadata = frontmatter.get("metadata")
        if isinstance(metadata, dict):
            kclaw_meta = metadata.get("kclaw", {}) or {}

        tags = _parse_tags(kclaw_meta.get("tags") or frontmatter.get("tags", ""))
        related_skills = _parse_tags(
            kclaw_meta.get("related_skills") or frontmatter.get("related_skills", "")
        )

        # Build linked files structure for clear discovery
        linked_files = {}
        if reference_files:
            linked_files["references"] = reference_files
        if template_files:
            linked_files["templates"] = template_files
        if asset_files:
            linked_files["assets"] = asset_files
        if script_files:
            linked_files["scripts"] = script_files

        try:
            rel_path = str(skill_md.relative_to(SKILLS_DIR))
        except ValueError:
            # 外部技能——使用相对于技能自身父目录的路径
            rel_path = str(skill_md.relative_to(skill_md.parent.parent)) if skill_md.parent.parent else skill_md.name
        skill_name = frontmatter.get(
            "name", skill_md.stem if not skill_dir else skill_dir.name
        )
        legacy_env_vars, _ = _collect_prerequisite_values(frontmatter)
        required_env_vars = _get_required_environment_variables(
            frontmatter, legacy_env_vars
        )
        backend = _get_terminal_backend_name()
        env_snapshot = load_env()
        missing_required_env_vars = [
            e
            for e in required_env_vars
            if not _is_env_var_persisted(e["name"], env_snapshot)
        ]
        capture_result = _capture_required_environment_variables(
            skill_name,
            missing_required_env_vars,
        )
        if missing_required_env_vars:
            env_snapshot = load_env()
        remaining_missing_required_envs = _remaining_required_environment_names(
            required_env_vars,
            capture_result,
            env_snapshot=env_snapshot,
        )
        setup_needed = bool(remaining_missing_required_envs)

        # 注册可用的技能环境变量，以便它们传递到沙箱
        # 执行环境（execute_code、terminal）。只有实际设置的变量才会被注册——
        # 缺失的会被报告为需要设置。
        available_env_names = [
            e["name"]
            for e in required_env_vars
            if e["name"] not in remaining_missing_required_envs
        ]
        if available_env_names:
            try:
                from tools.env_passthrough import register_env_passthrough

                register_env_passthrough(available_env_names)
            except Exception:
                logger.debug(
                    "Could not register env passthrough for skill %s",
                    skill_name,
                    exc_info=True,
                )

        # 注册凭据文件以挂载到远程沙箱
        #（Modal、Docker）。主机上存在的文件会被注册；
        # 缺失的会被添加到 setup_needed 指示器中。
        required_cred_files_raw = frontmatter.get("required_credential_files", [])
        if not isinstance(required_cred_files_raw, list):
            required_cred_files_raw = []
        missing_cred_files: list = []
        if required_cred_files_raw:
            try:
                from tools.credential_files import register_credential_files

                missing_cred_files = register_credential_files(required_cred_files_raw)
                if missing_cred_files:
                    setup_needed = True
            except Exception:
                logger.debug(
                    "Could not register credential files for skill %s",
                    skill_name,
                    exc_info=True,
                )

        result = {
            "success": True,
            "name": skill_name,
            "description": frontmatter.get("description", ""),
            "tags": tags,
            "related_skills": related_skills,
            "content": content,
            "path": rel_path,
            "linked_files": linked_files if linked_files else None,
            "usage_hint": "To view linked files, call skill_view(name, file_path) where file_path is e.g. 'references/api.md' or 'assets/config.yaml'"
            if linked_files
            else None,
            "required_environment_variables": required_env_vars,
            "required_commands": [],
            "missing_required_environment_variables": remaining_missing_required_envs,
            "missing_credential_files": missing_cred_files,
            "missing_required_commands": [],
            "setup_needed": setup_needed,
            "setup_skipped": capture_result["setup_skipped"],
            "readiness_status": SkillReadinessStatus.SETUP_NEEDED.value
            if setup_needed
            else SkillReadinessStatus.AVAILABLE.value,
        }

        setup_help = next((e["help"] for e in required_env_vars if e.get("help")), None)
        if setup_help:
            result["setup_help"] = setup_help

        if capture_result["gateway_setup_hint"]:
            result["gateway_setup_hint"] = capture_result["gateway_setup_hint"]

        if setup_needed:
            missing_items = [
                f"env ${env_name}" for env_name in remaining_missing_required_envs
            ] + [
                f"file {path}" for path in missing_cred_files
            ]
            setup_note = _build_setup_note(
                SkillReadinessStatus.SETUP_NEEDED,
                missing_items,
                setup_help,
            )
            if backend in _REMOTE_ENV_BACKENDS and setup_note:
                setup_note = f"{setup_note} {backend.upper()}-backed skills need these requirements available inside the remote environment as well."
            if setup_note:
                result["setup_note"] = setup_note

        # 当存在时显示 agentskills.io 可选字段
        if frontmatter.get("compatibility"):
            result["compatibility"] = frontmatter["compatibility"]
        if isinstance(metadata, dict):
            result["metadata"] = metadata

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return tool_error(str(e), success=False)


# Tool description for model_tools.py
SKILLS_TOOL_DESCRIPTION = """Access skill documents providing specialized instructions, guidelines, and executable knowledge.

Progressive disclosure workflow:
1. skills_list() - Returns metadata (name, description, tags, linked_file_count) for all skills
2. skill_view(name) - Loads full SKILL.md content + shows available linked_files
3. skill_view(name, file_path) - Loads specific linked file (e.g., 'references/api.md', 'scripts/train.py')

Skills may include:
- references/: Additional documentation, API specs, examples
- templates/: Output formats, config files, boilerplate code
- assets/: Supplementary files (agentskills.io standard)
- scripts/: Executable helpers (Python, shell scripts)"""


if __name__ == "__main__":
    """测试技能工具"""
    print("🎯 Skills Tool Test")
    print("=" * 60)

    # 测试列出技能
    print("\n📋 列出所有技能：")
    result = json.loads(skills_list())
    if result["success"]:
        print(
            f"Found {result['count']} skills in {len(result.get('categories', []))} categories"
        )
        print(f"Categories: {result.get('categories', [])}")
        print("\nFirst 10 skills:")
        for skill in result["skills"][:10]:
            cat = f"[{skill['category']}] " if skill.get("category") else ""
            print(f"  • {cat}{skill['name']}: {skill['description'][:60]}...")
    else:
        print(f"Error: {result['error']}")

    # 测试查看技能
    print("\n📖 查看技能 'axolotl'：")
    result = json.loads(skill_view("axolotl"))
    if result["success"]:
        print(f"Name: {result['name']}")
        print(f"Description: {result.get('description', 'N/A')[:100]}...")
        print(f"Content length: {len(result['content'])} chars")
        if result.get("linked_files"):
            print(f"Linked files: {result['linked_files']}")
    else:
        print(f"Error: {result['error']}")

    # 测试查看参考文件
    print("\n📄 查看参考文件 'axolotl/references/dataset-formats.md'：")
    result = json.loads(skill_view("axolotl", "references/dataset-formats.md"))
    if result["success"]:
        print(f"File: {result['file']}")
        print(f"Content length: {len(result['content'])} chars")
        print(f"Preview: {result['content'][:150]}...")
    else:
        print(f"Error: {result['error']}")


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

SKILLS_LIST_SCHEMA = {
    "name": "skills_list",
    "description": "List available skills (name + description). Use skill_view(name) to load full content.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Optional category filter to narrow results",
            }
        },
        "required": [],
    },
}

SKILL_VIEW_SCHEMA = {
    "name": "skill_view",
    "description": "Skills allow for loading information about specific tasks and workflows, as well as scripts and templates. Load a skill's full content or access its linked files (references, templates, scripts). First call returns SKILL.md content plus a 'linked_files' dict showing available references/templates/scripts. To access those, call again with file_path parameter.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name (use skills_list to see available skills)",
            },
            "file_path": {
                "type": "string",
                "description": "OPTIONAL: Path to a linked file within the skill (e.g., 'references/api.md', 'templates/config.yaml', 'scripts/validate.py'). Omit to get the main SKILL.md content.",
            },
        },
        "required": ["name"],
    },
}

registry.register(
    name="skills_list",
    toolset="skills",
    schema=SKILLS_LIST_SCHEMA,
    handler=lambda args, **kw: skills_list(
        category=args.get("category"), task_id=kw.get("task_id")
    ),
    check_fn=check_skills_requirements,
    emoji="📚",
)
registry.register(
    name="skill_view",
    toolset="skills",
    schema=SKILL_VIEW_SCHEMA,
    handler=lambda args, **kw: skill_view(
        args.get("name", ""), file_path=args.get("file_path"), task_id=kw.get("task_id")
    ),
    check_fn=check_skills_requirements,
    emoji="📚",
)
