"""KClaw 工具的共享 OpenRouter API 客户端。

提供单个延迟初始化的 AsyncOpenAI 客户端，所有工具模块可以共享。
通过 agent/auxiliary_client.py 中的集中式提供商路由器路由，
因此 auth、headers 和 API 格式的处理是一致的。
"""

import os

_client = None


def get_async_client():
    """返回 OpenRouter 的共享异步 OpenAI 兼容客户端。

    客户端在首次调用时延迟创建，之后重复使用。
    使用集中式提供商路由器进行身份验证和客户端构建。
    如果未设置 OPENROUTER_API_KEY 则引发 ValueError。
    """
    global _client
    if _client is None:
        from agent.auxiliary_client import resolve_provider_client
        client, _model = resolve_provider_client("openrouter", async_mode=True)
        if client is None:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")
        _client = client
    return _client


def check_api_key() -> bool:
    """检查 OpenRouter API 密钥是否存在。"""
    return bool(os.getenv("OPENROUTER_API_KEY"))
