"""回调工厂：桥接 AIAgent 事件到 ACP 通知。

每个工厂返回一个符合 AIAgent 回调签名的可调用对象。内部通过
``asyncio.run_coroutine_threadsafe()`` 向客户端推送 ACP 会话更新
（因为 AIAgent 运行在工作线程，而事件循环在主线程上）。
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Deque, Dict

import acp

from .tools import (
    build_tool_complete,
    build_tool_start,
    make_tool_call_id,
)

logger = logging.getLogger(__name__)


def _send_update(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    update: Any,
) -> None:
    """从工作线程发送 ACP 会话更新（即发即弃）。"""
    try:
        future = asyncio.run_coroutine_threadsafe(
            conn.session_update(session_id, update), loop
        )
        future.result(timeout=5)
    except Exception:
        logger.debug("发送 ACP 更新失败", exc_info=True)


# ------------------------------------------------------------------
# 工具进度回调
# ------------------------------------------------------------------

def make_tool_progress_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: Dict[str, Deque[str]],
) -> Callable:
    """为 AIAgent 创建 ``tool_progress_callback``。

    AIAgent 期望的签名::

        tool_progress_callback(event_type: str, name: str, preview: str, args: dict, **kwargs)

    对 ``tool.started`` 事件发送 ``ToolCallStart``，并按工具名称维护 FIFO 队列，
    使重复/并行同名调用仍能正确匹配对应的 ACP 工具调用。
    其他事件类型（``tool.completed``、``reasoning.available``）被静默忽略。
    """

    def _tool_progress(event_type: str, name: str = None, preview: str = None, args: Any = None, **kwargs) -> None:
        # 仅对 tool.started 发送 ACP ToolCallStart；忽略其他事件类型
        if event_type != "tool.started":
            return
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {"raw": args}
        if not isinstance(args, dict):
            args = {}

        tc_id = make_tool_call_id()
        queue = tool_call_ids.get(name)
        if queue is None:
            queue = deque()
            tool_call_ids[name] = queue
        elif isinstance(queue, str):
            queue = deque([queue])
            tool_call_ids[name] = queue
        queue.append(tc_id)

        update = build_tool_start(tc_id, name, args)
        _send_update(conn, session_id, loop, update)

    return _tool_progress


# ------------------------------------------------------------------
# 思考回调
# ------------------------------------------------------------------

def make_thinking_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
) -> Callable:
    """为 AIAgent 创建 ``thinking_callback``。"""

    def _thinking(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_thought_text(text)
        _send_update(conn, session_id, loop, update)

    return _thinking


# ------------------------------------------------------------------
# 步骤回调
# ------------------------------------------------------------------

def make_step_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: Dict[str, Deque[str]],
) -> Callable:
    """为 AIAgent 创建 ``step_callback``。

    AIAgent 期望的签名::

        step_callback(api_call_count: int, prev_tools: list)
    """

    def _step(api_call_count: int, prev_tools: Any = None) -> None:
        if prev_tools and isinstance(prev_tools, list):
            for tool_info in prev_tools:
                tool_name = None
                result = None

                if isinstance(tool_info, dict):
                    tool_name = tool_info.get("name") or tool_info.get("function_name")
                    result = tool_info.get("result") or tool_info.get("output")
                elif isinstance(tool_info, str):
                    tool_name = tool_info

                queue = tool_call_ids.get(tool_name or "")
                if isinstance(queue, str):
                    queue = deque([queue])
                    tool_call_ids[tool_name] = queue
                if tool_name and queue:
                    tc_id = queue.popleft()
                    update = build_tool_complete(
                        tc_id, tool_name, result=str(result) if result is not None else None
                    )
                    _send_update(conn, session_id, loop, update)
                    if not queue:
                        tool_call_ids.pop(tool_name, None)

    return _step


# ------------------------------------------------------------------
# Agent 消息回调
# ------------------------------------------------------------------

def make_message_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
) -> Callable:
    """创建回调，将 Agent 响应文本流式发送到编辑器。"""

    def _message(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_message_text(text)
        _send_update(conn, session_id, loop, update)

    return _message
