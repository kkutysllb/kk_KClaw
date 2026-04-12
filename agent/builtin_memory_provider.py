"""BuiltinMemoryProvider — 将 MEMORY.md / USER.md 包装为 MemoryProvider。

始终注册为第一个提供者。无法禁用或移除。
这是现有的 KClaw 记忆系统,通过提供者接口暴露
以兼容 MemoryManager。

实际的存储逻辑在 tools/memory_tool.py (MemoryStore) 中。
此提供者是一个薄适配器,委托给 MemoryStore 并
暴露记忆工具 schema。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)


class BuiltinMemoryProvider(MemoryProvider):
    """内置的文件支持记忆(MEMORY.md + USER.md)。

    始终激活,永远不会被其他提供者禁用。`memory` 工具
    由 run_agent.py 的 agent 级别工具拦截处理(不通过
    正常注册表),因此 get_tool_schemas() 返回空列表 —
    记忆工具已单独连接。
    """

    def __init__(
        self,
        memory_store=None,
        memory_enabled: bool = False,
        user_profile_enabled: bool = False,
    ):
        self._store = memory_store
        self._memory_enabled = memory_enabled
        self._user_profile_enabled = user_profile_enabled

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        """内置记忆始终可用。"""
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        """如果尚未加载,从磁盘加载记忆。"""
        if self._store is not None:
            self._store.load_from_disk()

    def system_prompt_block(self) -> str:
        """返回 MEMORY.md 和 USER.md 内容用于系统提示词。

        使用加载时捕获的冻结快照。这确保系统提示词
        在整个会话期间保持稳定(保留提示词缓存),
        即使实时条目可能通过工具调用更改。
        """
        if not self._store:
            return ""

        parts = []
        if self._memory_enabled:
            mem_block = self._store.format_for_system_prompt("memory")
            if mem_block:
                parts.append(mem_block)
        if self._user_profile_enabled:
            user_block = self._store.format_for_system_prompt("user")
            if user_block:
                parts.append(user_block)

        return "\n\n".join(parts)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """内置记忆不做基于查询的召回 — 它通过 system_prompt_block 注入。"""
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """内置记忆不自动同步轮次 — 写入通过记忆工具发生。"""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回空列表。

        `memory` 工具是 agent 级别拦截的工具,在正常工具分发之前
        在 run_agent.py 中特殊处理。它不是标准工具注册表的一部分。
        我们不在这里复制它。
        """
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """未使用 — 记忆工具在 run_agent.py 中拦截。"""
        return tool_error("Built-in memory tool is handled by the agent loop")

    def shutdown(self) -> None:
        """无需清理 — 文件在每次写入时保存。"""

    # -- 属性访问,用于向后兼容 --------------------------

    @property
    def store(self):
        """访问底层 MemoryStore,用于旧代码路径。"""
        return self._store

    @property
    def memory_enabled(self) -> bool:
        return self._memory_enabled

    @property
    def user_profile_enabled(self) -> bool:
        return self._user_profile_enabled
