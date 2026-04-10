"""云浏览器提供商的抽象基类。"""

from abc import ABC, abstractmethod
from typing import Dict


class CloudBrowserProvider(ABC):
    """云浏览器后端接口（Browserbase、Steel 等）。

    实现在同级模块中，注册在
    ``browser_tool._PROVIDER_REGISTRY``。用户通过
    ``kclaw setup`` / ``kclaw tools`` 选择提供商；选择保存在
    ``config["browser"]["cloud_provider"]`` 中。
    """

    @abstractmethod
    def provider_name(self) -> str:
        """简短的人类可读名称，显示在日志和诊断信息中。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """当所有必需的环境变量/凭证都存在时返回 True。

        在工具注册时（``check_browser_requirements``）调用以
        控制可用性。必须廉价 — 无网络调用。
        """

    @abstractmethod
    def create_session(self, task_id: str) -> Dict[str, object]:
        """创建云浏览器会话并返回会话元数据。

        必须返回至少包含以下内容的字典::

            {
                "session_name": str,   # agent-browser --session 的唯一名称
                "bb_session_id": str,  # 提供商会话 ID（用于关闭/清理）
                "cdp_url": str,        # CDP WebSocket URL
                "features": dict,      # 已启用的功能标志
            }

        ``bb_session_id`` 是为保持与 browser_tool.py 其余部分向后兼容而保留的旧键名
        — 它保存提供商的会话 ID，无论使用哪个提供商。
        """

    @abstractmethod
    def close_session(self, session_id: str) -> bool:
        """通过提供商的会话 ID 释放/终止云会话。

        成功返回 True，失败返回 False。不应抛出异常。
        """

    @abstractmethod
    def emergency_cleanup(self, session_id: str) -> None:
        """进程退出时尽力清理会话。

        从 atexit/信号处理器调用。必须容忍
        凭证缺失、网络错误等 — 记录日志并继续。
        """
