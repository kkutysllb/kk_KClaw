"""工具后端选择的共享辅助函数。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from utils import env_var_enabled

_DEFAULT_BROWSER_PROVIDER = "local"
_DEFAULT_MODAL_MODE = "auto"
_VALID_MODAL_MODES = {"auto", "direct", "managed"}


def managed_nous_tools_enabled() -> bool:
    """当隐藏的 Nous 托管工具特性标志启用时返回 True。"""
    return env_var_enabled("KCLAW_ENABLE_NOUS_MANAGED_TOOLS")


def normalize_browser_cloud_provider(value: object | None) -> str:
    """返回标准化的浏览器提供者密钥。"""
    provider = str(value or _DEFAULT_BROWSER_PROVIDER).strip().lower()
    return provider or _DEFAULT_BROWSER_PROVIDER


def coerce_modal_mode(value: object | None) -> str:
    """返回请求的模态模式（如果有效），否则返回默认值。"""
    mode = str(value or _DEFAULT_MODAL_MODE).strip().lower()
    if mode in _VALID_MODAL_MODES:
        return mode
    return _DEFAULT_MODAL_MODE


def normalize_modal_mode(value: object | None) -> str:
    """返回标准化的模态执行模式。"""
    return coerce_modal_mode(value)


def has_direct_modal_credentials() -> bool:
    """当直接 Modal 凭证/配置可用时返回 True。"""
    return bool(
        (os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"))
        or (Path.home() / ".modal.toml").exists()
    )


def resolve_modal_backend_state(
    modal_mode: object | None,
    *,
    has_direct: bool,
    managed_ready: bool,
) -> Dict[str, Any]:
    """解析直接与托管 Modal 后端的选择。

    语义：
    - ``direct`` 表示仅直接模式
    - ``managed`` 表示仅托管模式
    - ``auto`` 优先使用托管模式（如果可用），否则回退到直接模式
    """
    requested_mode = coerce_modal_mode(modal_mode)
    normalized_mode = normalize_modal_mode(modal_mode)
    managed_mode_blocked = (
        requested_mode == "managed" and not managed_nous_tools_enabled()
    )

    if normalized_mode == "managed":
        selected_backend = "managed" if managed_nous_tools_enabled() and managed_ready else None
    elif normalized_mode == "direct":
        selected_backend = "direct" if has_direct else None
    else:
        selected_backend = "managed" if managed_nous_tools_enabled() and managed_ready else "direct" if has_direct else None

    return {
        "requested_mode": requested_mode,
        "mode": normalized_mode,
        "has_direct": has_direct,
        "managed_ready": managed_ready,
        "managed_mode_blocked": managed_mode_blocked,
        "selected_backend": selected_backend,
    }


def resolve_openai_audio_api_key() -> str:
    """优先使用 voice-tools 密钥，但回退到普通的 OpenAI 密钥。"""
    return (
        os.getenv("VOICE_TOOLS_OPENAI_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    ).strip()
