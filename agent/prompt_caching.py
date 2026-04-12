"""Anthropic 提示词缓存(system_and_3 策略)。

通过缓存对话前缀,在多轮对话中减少约 75% 的输入 token 成本。
使用 4 个 cache_control 断点(Anthropic 最大值):
  1. 系统提示词(在所有轮次中稳定)
  2-4. 最后 3 条非系统消息(滚动窗口)

纯函数 — 无类状态,无 AIAgent 依赖。
"""

import copy
from typing import Any, Dict, List


def _apply_cache_marker(msg: dict, cache_marker: dict, native_anthropic: bool = False) -> None:
    """向单条消息添加 cache_control,处理所有格式变体。"""
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        if native_anthropic:
            msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_marker


def apply_anthropic_cache_control(
    api_messages: List[Dict[str, Any]],
    cache_ttl: str = "5m",
    native_anthropic: bool = False,
) -> List[Dict[str, Any]]:
    """对 Anthropic 模型的消息应用 system_and_3 缓存策略。

    放置最多 4 个 cache_control 断点: 系统提示词 + 最后 3 条非系统消息。

    Returns:
        注入了 cache_control 断点的消息深拷贝。
    """
    messages = copy.deepcopy(api_messages)
    if not messages:
        return messages

    marker = {"type": "ephemeral"}
    if cache_ttl == "1h":
        marker["ttl"] = "1h"

    breakpoints_used = 0

    if messages[0].get("role") == "system":
        _apply_cache_marker(messages[0], marker, native_anthropic=native_anthropic)
        breakpoints_used += 1

    remaining = 4 - breakpoints_used
    non_sys = [i for i in range(len(messages)) if messages[i].get("role") != "system"]
    for idx in non_sys[-remaining:]:
        _apply_cache_marker(messages[idx], marker, native_anthropic=native_anthropic)

    return messages
