"""KClaw 工具共享的调试会话基础设施。

替换了之前在 web_tools、vision_tools、mixture_of_agents_tool 和
image_generation_tool 中重复的相同 DEBUG_MODE / _log_debug_call / _save_debug_log /
get_debug_session_info 样板代码。

在工具模块中的用法：

    from tools.debug_helpers import DebugSession

    _debug = DebugSession("web_tools", env_var="WEB_TOOLS_DEBUG")

    # 记录调用（调试模式关闭时无操作）
    _debug.log_call("web_search", {"query": q, "results": len(r)})

    # 保存调试日志（调试模式关闭时无操作）
    _debug.save()

    # 向外部调用者暴露调试信息
    def get_debug_session_info():
        return _debug.get_session_info()
"""

import datetime
import json
import logging
import os
import uuid
from typing import Any, Dict

from kclaw_constants import get_kclaw_home

logger = logging.getLogger(__name__)


class DebugSession:
    """每个工具的调试会话，将工具调用记录到 JSON 日志文件中。

    通过特定工具的环境变量激活（例如 WEB_TOOLS_DEBUG=true）。
    当禁用时，所有方法都是轻量无操作。
    """

    def __init__(self, tool_name: str, *, env_var: str) -> None:
        self.tool_name = tool_name
        self.enabled = os.getenv(env_var, "false").lower() == "true"
        self.session_id = str(uuid.uuid4()) if self.enabled else ""
        self.log_dir = get_kclaw_home() / "logs"
        self._calls: list[Dict[str, Any]] = []
        self._start_time = datetime.datetime.now().isoformat() if self.enabled else ""

        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("%s debug mode enabled - Session ID: %s",
                         tool_name, self.session_id)

    @property
    def active(self) -> bool:
        return self.enabled

    def log_call(self, call_name: str, call_data: Dict[str, Any]) -> None:
        """将工具调用条目追加到内存日志中。"""
        if not self.enabled:
            return
        self._calls.append({
            "timestamp": datetime.datetime.now().isoformat(),
            "tool_name": call_name,
            **call_data,
        })

    def save(self) -> None:
        """将内存日志刷新到 logs 目录中的 JSON 文件。"""
        if not self.enabled:
            return
        try:
            filename = f"{self.tool_name}_debug_{self.session_id}.json"
            filepath = self.log_dir / filename
            payload = {
                "session_id": self.session_id,
                "start_time": self._start_time,
                "end_time": datetime.datetime.now().isoformat(),
                "debug_enabled": True,
                "total_calls": len(self._calls),
                "tool_calls": self._calls,
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logger.debug("%s debug log saved: %s", self.tool_name, filepath)
        except Exception as e:
            logger.error("Error saving %s debug log: %s", self.tool_name, e)

    def get_session_info(self) -> Dict[str, Any]:
        """返回一个摘要字典，适合从 get_debug_session_info() 返回。"""
        if not self.enabled:
            return {
                "enabled": False,
                "session_id": None,
                "log_path": None,
                "total_calls": 0,
            }
        return {
            "enabled": True,
            "session_id": self.session_id,
            "log_path": str(self.log_dir / f"{self.tool_name}_debug_{self.session_id}.json"),
            "total_calls": len(self._calls),
        }
