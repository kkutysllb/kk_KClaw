"""KClaw Agent 的共享常量模块。

一个导入安全的模块，没有任何依赖项——可以从任何地方导入，
而无需担心循环导入的问题。
"""

import os
from pathlib import Path


def get_kclaw_home() -> Path:
    """返回 KClaw 主目录（默认：~/.kclaw）。

    读取 KCLAW_HOME 环境变量，默认为 ~/.kclaw。
    这是唯一的真实来源——所有其他副本都应该导入此函数。
    """
    return Path(os.getenv("KCLAW_HOME", Path.home() / ".kclaw"))


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """返回可选技能目录，尊重包管理器的封装。

    打包安装可能将 ``optional-skills`` 放在 Python 包目录树之外，
    并通过 ``KCLAW_OPTIONAL_SKILLS`` 环境变量暴露。
    """
    override = os.getenv("KCLAW_OPTIONAL_SKILLS", "").strip()
    if override:
        return Path(override)
    if default is not None:
        return default
    return get_kclaw_home() / "optional-skills"


def get_kclaw_dir(new_subpath: str, old_name: str) -> Path:
    """解析 KClaw 子目录，保持向后兼容。

    新安装使用合并后的布局（例如 ``cache/images``）。
    已经使用旧路径（例如 ``image_cache``）的现有安装继续使用它——无需迁移。

    参数:
        new_subpath: 相对于 KCLAW_HOME 的首选路径（例如 ``"cache/images"``）。
        old_name: 相对于 KCLAW_HOME 的旧路径（例如 ``"image_cache"``）。

    返回:
        绝对 ``Path`` —— 如果旧路径存在于磁盘上则返回旧路径，否则返回新路径。
    """
    home = get_kclaw_home()
    old_path = home / old_name
    if old_path.exists():
        return old_path
    return home / new_subpath


def display_kclaw_home() -> str:
    """返回当前 KCLAW_HOME 的用户友好显示字符串。

    为可读性使用 ``~/`` 简写::

        默认:  ``~/.kclaw``
        配置:  ``~/.kclaw/profiles/coder``
        自定义: ``/opt/kclaw-custom``

    在**面向用户**的打印/日志消息中使用此函数，而不是硬编码
    ``~/.kclaw``。对于需要真实 ``Path`` 的代码，请改用
    :func:`get_kclaw_home`。
    """
    home = get_kclaw_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


VALID_REASONING_EFFORTS = ("xhigh", "high", "medium", "low", "minimal")


def parse_reasoning_effort(effort: str) -> dict | None:
    """将推理努力级别解析为配置字典。

    有效级别："xhigh"、"high"、"medium"、"low"、"minimal"、"none"。
    当输入为空或无法识别时返回 None（调用方使用默认值）。
    对于 "none" 返回 {"enabled": False}。
    对于有效努力级别返回 {"enabled": True, "effort": <级别>}。
    """
    if not effort or not effort.strip():
        return None
    effort = effort.strip().lower()
    if effort == "none":
        return {"enabled": False}
    if effort in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": effort}
    return None


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE_URL}/chat/completions"

AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
AI_GATEWAY_MODELS_URL = f"{AI_GATEWAY_BASE_URL}/models"
AI_GATEWAY_CHAT_URL = f"{AI_GATEWAY_BASE_URL}/chat/completions"

NOUS_API_BASE_URL = "https://inference-api.nousresearch.com/v1"
NOUS_API_CHAT_URL = f"{NOUS_API_BASE_URL}/chat/completions"
