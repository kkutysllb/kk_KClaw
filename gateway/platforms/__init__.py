"""
消息集成的平台适配器。

每个适配器处理：
- 从平台接收消息
- 发送消息/响应
- 平台特定的身份验证
- 消息格式化和媒体处理
"""

from .base import BasePlatformAdapter, MessageEvent, SendResult

__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "SendResult",
]
