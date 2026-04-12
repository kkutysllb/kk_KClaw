"""检测当前配置的 KClaw 提供者。"""

from __future__ import annotations

from typing import Optional


def detect_provider() -> Optional[str]:
    """解析当前活动的 KClaw 运行时提供者，如果不可用则返回 None。"""
    try:
        from kclaw_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider()
        api_key = runtime.get("api_key")
        provider = runtime.get("provider")
        if isinstance(api_key, str) and api_key.strip() and isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    except Exception:
        return None
    return None


def has_provider() -> bool:
    """如果 KClaw 能够解析任何运行时提供者凭据，则返回 True。"""
    return detect_provider() is not None
