"""
KClaw Gateway - 多平台消息集成。

本模块提供统一的网关，用于将 KClaw 代理
连接到各种消息平台（Telegram、Discord、WhatsApp）：
- 会话管理（带重置策略的持久化对话）
- 动态上下文注入（代理知道消息来自哪里）
- 传递路由（cron 作业输出到适当的渠道）
- 平台特定工具集（每个平台不同的功能）
"""

from .config import GatewayConfig, PlatformConfig, HomeChannel, load_gateway_config
from .session import (
    SessionContext,
    SessionStore,
    SessionResetPolicy,
    build_session_context_prompt,
)
from .delivery import DeliveryRouter, DeliveryTarget

__all__ = [
    # Config
    "GatewayConfig",
    "PlatformConfig", 
    "HomeChannel",
    "load_gateway_config",
    # Session
    "SessionContext",
    "SessionStore",
    "SessionResetPolicy",
    "build_session_context_prompt",
    # Delivery
    "DeliveryRouter",
    "DeliveryTarget",
]
