"""轻量级技能元数据工具,由 prompt_builder 和 skills_tool 共享。

此模块有意避免导入工具注册表、CLI 配置或任何
重度依赖链。可以安全地在模块级别导入而不会触发
工具注册或提供者解析。
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from kclaw_constants import get_kclaw_home

logger = logging.getLogger(__name__)

# ── 平台映射 ──────────────────────────────────────────────────────

PLATFORM_MAP = {
    "macos": "darwin",
    "linux": "linux",
    "windows": "win32",
}

EXCLUDED_SKILL_DIRS = frozenset((".git", ".github", ".hub"))

# ── 懒加载 YAML 加载器 ─────────────────────────────────────────────────────

_yaml_load_fn = None


def yaml_load(content: str):
    """使用懒导入和 CSafeLoader 偏好解析 YAML。"""
    global _yaml_load_fn
    if _yaml_load_fn is None:
        import yaml

        loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader

        def _load(value: str):
            return yaml.load(value, Loader=loader)

        _yaml_load_fn = _load
    return _yaml_load_fn(content)


# ── Frontmatter 解析 ──────────────────────────────────────────────────


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """从 markdown 字符串解析 YAML frontmatter。

    使用带 CSafeLoader 的 yaml 以获得完整的 YAML 支持(嵌套元数据、列表),
    并回退到简单的 key:value 分割以确保健壮性。

    Returns:
        (frontmatter_dict, remaining_body)
    """
    frontmatter: Dict[str, Any] = {}
    body = content

    if not content.startswith("---"):
        return frontmatter, body

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return frontmatter, body

    yaml_content = content[3 : end_match.start() + 3]
    body = content[end_match.end() + 3 :]

    try:
        parsed = yaml_load(yaml_content)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except Exception:
        # 回退: 对格式错误的 YAML 进行简单的 key:value 解析
        for line in yaml_content.strip().split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()

    return frontmatter, body


# ── 平台匹配 ─────────────────────────────────────────────────────


def skill_matches_platform(frontmatter: Dict[str, Any]) -> bool:
    """当技能与当前 OS 兼容时返回 True。

    技能通过 YAML frontmatter 中的顶级 ``platforms`` 列表声明平台要求::

        platforms: [macos]          # 仅 macOS
        platforms: [macos, linux]   # macOS 和 Linux

    如果字段不存在或为空,则技能与**所有**平台兼容
    (向后兼容的默认值)。
    """
    platforms = frontmatter.get("platforms")
    if not platforms:
        return True
    if not isinstance(platforms, list):
        platforms = [platforms]
    current = sys.platform
    for platform in platforms:
        normalized = str(platform).lower().strip()
        mapped = PLATFORM_MAP.get(normalized, normalized)
        if current.startswith(mapped):
            return True
    return False


# ── 禁用的技能 ───────────────────────────────────────────────────────


def get_disabled_skill_names(platform: str | None = None) -> Set[str]:
    """从 config.yaml 读取禁用的技能名称。

    Args:
        platform: 显式平台名称(例如 ``"telegram"``)。当为 *None* 时,
            从 ``KCLAW_PLATFORM`` 或 ``KCLAW_SESSION_PLATFORM`` 环境变量解析。
            当无法确定平台时,回退到全局禁用列表。

    直接读取配置文件(无 CLI 配置导入)以保持轻量。
    """
    config_path = get_kclaw_home() / "config.yaml"
    if not config_path.exists():
        return set()
    try:
        parsed = yaml_load(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("无法读取技能配置 %s: %s", config_path, e)
        return set()
    if not isinstance(parsed, dict):
        return set()

    skills_cfg = parsed.get("skills")
    if not isinstance(skills_cfg, dict):
        return set()

    resolved_platform = (
        platform
        or os.getenv("KCLAW_PLATFORM")
        or os.getenv("KCLAW_SESSION_PLATFORM")
    )
    if resolved_platform:
        platform_disabled = (skills_cfg.get("platform_disabled") or {}).get(
            resolved_platform
        )
        if platform_disabled is not None:
            return _normalize_string_set(platform_disabled)
    return _normalize_string_set(skills_cfg.get("disabled"))


def _normalize_string_set(values) -> Set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(v).strip() for v in values if str(v).strip()}


# ── 外部技能目录 ──────────────────────────────────────────────────


def get_external_skills_dirs() -> List[Path]:
    """从 config.yaml 读取 ``skills.external_dirs`` 并返回验证后的路径。

    每个条目被展开(``~`` 和 ``${VAR}``)并解析为绝对路径。
    只有实际存在的目录才会被返回。重复路径和解析为本地
    ``~/.kclaw/skills/`` 的路径会被静默跳过。
    """
    config_path = get_kclaw_home() / "config.yaml"
    if not config_path.exists():
        return []
    try:
        parsed = yaml_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []

    skills_cfg = parsed.get("skills")
    if not isinstance(skills_cfg, dict):
        return []

    raw_dirs = skills_cfg.get("external_dirs")
    if not raw_dirs:
        return []
    if isinstance(raw_dirs, str):
        raw_dirs = [raw_dirs]
    if not isinstance(raw_dirs, list):
        return []

    local_skills = (get_kclaw_home() / "skills").resolve()
    seen: Set[Path] = set()
    result: List[Path] = []

    for entry in raw_dirs:
        entry = str(entry).strip()
        if not entry:
            continue
        # 展开 ~ 和环境变量
        expanded = os.path.expanduser(os.path.expandvars(entry))
        p = Path(expanded).resolve()
        if p == local_skills:
            continue
        if p in seen:
            continue
        if p.is_dir():
            seen.add(p)
            result.append(p)
        else:
            logger.debug("外部技能目录不存在,跳过: %s", p)

    return result


def get_all_skills_dirs() -> List[Path]:
    """返回所有技能目录: 本地 ``~/.kclaw/skills/`` 优先,然后是外部目录。

    本地目录始终在前(即使不存在也会包含 — 调用者处理)。
    外部目录按配置顺序跟随。
    """
    dirs = [get_kclaw_home() / "skills"]
    dirs.extend(get_external_skills_dirs())
    return dirs


# ── 条件提取 ──────────────────────────────────────────────────


def extract_skill_conditions(frontmatter: Dict[str, Any]) -> Dict[str, List]:
    """从解析的 frontmatter 中提取条件激活字段。"""
    metadata = frontmatter.get("metadata")
    # 处理 metadata 不是 dict 的情况(例如格式错误的 YAML 产生的字符串)
    if not isinstance(metadata, dict):
        metadata = {}
    kclaw = metadata.get("kclaw") or {}
    if not isinstance(kclaw, dict):
        kclaw = {}
    return {
        "fallback_for_toolsets": kclaw.get("fallback_for_toolsets", []),
        "requires_toolsets": kclaw.get("requires_toolsets", []),
        "fallback_for_tools": kclaw.get("fallback_for_tools", []),
        "requires_tools": kclaw.get("requires_tools", []),
    }


# ── 技能配置提取 ───────────────────────────────────────────────────────


def extract_skill_config_vars(frontmatter: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从解析的 frontmatter 中提取配置变量声明。

    技能通过以下方式声明它们需要的 config.yaml 设置::

        metadata:
          kclaw:
            config:
              - key: wiki.path
                description: Path to the LLM Wiki knowledge base directory
                default: "~/wiki"
                prompt: Wiki directory path

    返回包含 ``key``、``description``、``default``、``prompt`` 键的
    dict 列表。无效或不完整的条目会被静默跳过。
    """
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return []
    kclaw = metadata.get("kclaw")
    if not isinstance(kclaw, dict):
        return []
    raw = kclaw.get("config")
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    result: List[Dict[str, Any]] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        if not key or key in seen:
            continue
        # 必须至少有 key 和 description
        desc = str(item.get("description", "")).strip()
        if not desc:
            continue
        entry: Dict[str, Any] = {
            "key": key,
            "description": desc,
        }
        default = item.get("default")
        if default is not None:
            entry["default"] = default
        prompt_text = item.get("prompt")
        if isinstance(prompt_text, str) and prompt_text.strip():
            entry["prompt"] = prompt_text.strip()
        else:
            entry["prompt"] = desc
        seen.add(key)
        result.append(entry)
    return result


def discover_all_skill_config_vars() -> List[Dict[str, Any]]:
    """扫描所有已启用的技能并收集其配置变量声明。

    遍历每个技能目录,解析每个 SKILL.md frontmatter,并返回
    去重的配置变量 dict 列表。每个 dict 还包含一个 ``skill`` 键
    带有技能名称以归属来源。

    禁用和平台不兼容的技能被排除。
    """
    all_vars: List[Dict[str, Any]] = []
    seen_keys: set = set()

    disabled = get_disabled_skill_names()
    for skills_dir in get_all_skills_dirs():
        if not skills_dir.is_dir():
            continue
        for skill_file in iter_skill_index_files(skills_dir, "SKILL.md"):
            try:
                raw = skill_file.read_text(encoding="utf-8")
                frontmatter, _ = parse_frontmatter(raw)
            except Exception:
                continue

            skill_name = frontmatter.get("name") or skill_file.parent.name
            if str(skill_name) in disabled:
                continue
            if not skill_matches_platform(frontmatter):
                continue

            config_vars = extract_skill_config_vars(frontmatter)
            for var in config_vars:
                if var["key"] not in seen_keys:
                    var["skill"] = str(skill_name)
                    all_vars.append(var)
                    seen_keys.add(var["key"])

    return all_vars


# 存储前缀:所有技能配置变量存储在 config.yaml 中的 skills.config.* 下。
# 技能作者声明逻辑键(例如 "wiki.path");
# 系统添加此前缀用于存储,并在显示时剥离。
SKILL_CONFIG_PREFIX = "skills.config"


def _resolve_dotpath(config: Dict[str, Any], dotted_key: str):
    """沿点分隔键遍历嵌套字典。如果任何部分缺失则返回 None。"""
    parts = dotted_key.split(".")
    current = config
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def resolve_skill_config_values(
    config_vars: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """从 config.yaml 解析技能配置变量的当前值。

    技能配置存储在 config.yaml 的 ``skills.config.<key>`` 下。
    返回将**逻辑**键(技能声明的)映射到其当前值的 dict
    (如果键未设置,则为声明的默认值)。
    路径值通过 ``os.path.expanduser`` 展开。
    """
    config_path = get_kclaw_home() / "config.yaml"
    config: Dict[str, Any] = {}
    if config_path.exists():
        try:
            parsed = yaml_load(config_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                config = parsed
        except Exception:
            pass

    resolved: Dict[str, Any] = {}
    for var in config_vars:
        logical_key = var["key"]
        storage_key = f"{SKILL_CONFIG_PREFIX}.{logical_key}"
        value = _resolve_dotpath(config, storage_key)

        if value is None or (isinstance(value, str) and not value.strip()):
            value = var.get("default", "")

        # 在类路径值中展开 ~
        if isinstance(value, str) and ("~" in value or "${" in value):
            value = os.path.expanduser(os.path.expandvars(value))

        resolved[logical_key] = value

    return resolved


# ── 描述提取 ────────────────────────────────────────────────────────


def extract_skill_description(frontmatter: Dict[str, Any]) -> str:
    """从解析的 frontmatter 中提取截断的描述。"""
    raw_desc = frontmatter.get("description", "")
    if not raw_desc:
        return ""
    desc = str(raw_desc).strip().strip("'\"")
    if len(desc) > 60:
        return desc[:57] + "..."
    return desc


# ── 文件迭代 ────────────────────────────────────────────────────────


def iter_skill_index_files(skills_dir: Path, filename: str):
    """遍历 skills_dir,产生匹配 *filename* 的排序路径。

    排除 ``.git``、``.github``、``.hub`` 目录。
    """
    matches = []
    for root, dirs, files in os.walk(skills_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_SKILL_DIRS]
        if filename in files:
            matches.append(Path(root) / filename)
    for path in sorted(matches, key=lambda p: str(p.relative_to(skills_dir))):
        yield path
