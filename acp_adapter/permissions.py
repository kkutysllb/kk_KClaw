"""ACP 权限桥接 — 将 ACP 审批请求映射为 kclaw 审批回调。"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable

from acp.schema import (
    AllowedOutcome,
    PermissionOption,
)

logger = logging.getLogger(__name__)

# ACP PermissionOptionKind -> kclaw 审批结果字符串的映射
_KIND_TO_KCLAW = {
    "allow_once": "once",
    "allow_always": "always",
    "reject_once": "deny",
    "reject_always": "deny",
}


def make_approval_callback(
    request_permission_fn: Callable,
    loop: asyncio.AbstractEventLoop,
    session_id: str,
    timeout: float = 60.0,
) -> Callable[[str, str], str]:
    """
    返回一个兼容 kclaw 的 ``approval_callback(command, description) -> str``，
    桥接到 ACP 客户端的 ``request_permission`` 调用。

    参数:
        request_permission_fn: ACP 连接的 ``request_permission`` 协程。
        loop: ACP 连接所在的事件循环。
        session_id: 当前 ACP 会话 ID。
        timeout: 等待响应的超时秒数，超时后自动拒绝。
    """

    def _callback(command: str, description: str) -> str:
        options = [
            PermissionOption(option_id="allow_once", kind="allow_once", name="允许一次"),
            PermissionOption(option_id="allow_always", kind="allow_always", name="始终允许"),
            PermissionOption(option_id="deny", kind="reject_once", name="拒绝"),
        ]
        import acp as _acp

        tool_call = _acp.start_tool_call("perm-check", command, kind="execute")

        coro = request_permission_fn(
            session_id=session_id,
            tool_call=tool_call,
            options=options,
        )

        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            response = future.result(timeout=timeout)
        except (FutureTimeout, Exception) as exc:
            logger.warning("权限请求超时或失败: %s", exc)
            return "deny"

        outcome = response.outcome
        if isinstance(outcome, AllowedOutcome):
            option_id = outcome.option_id
            # 从选项列表中查找 kind
            for opt in options:
                if opt.option_id == option_id:
                    return _KIND_TO_KCLAW.get(opt.kind, "deny")
            return "once"  # 未知 option_id 的回退值
        else:
            return "deny"

    return _callback
