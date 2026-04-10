"""KClaw 托管的 Camofox 状态辅助函数。

为 Camofox 持久浏览器配置文件提供配置文件作用域的身份和状态目录路径。
当托管持久性启用时，KClaw 发送从活动配置文件派生的确定性 userId，
以便 Camofox 可以将其映射到相同的持久浏览器配置文件目录
（跨重启）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Optional

from kclaw_constants import get_kclaw_home

CAMOFOX_STATE_DIR_NAME = "browser_auth"
CAMOFOX_STATE_SUBDIR = "camofox"


def get_camofox_state_dir() -> Path:
    """返回 Camofox 持久化的配置文件作用域根目录。"""
    return get_kclaw_home() / CAMOFOX_STATE_DIR_NAME / CAMOFOX_STATE_SUBDIR


def get_camofox_identity(task_id: Optional[str] = None) -> Dict[str, str]:
    """返回此配置文件的稳定 KClaw 托管 Camofox 身份。

    用户身份是配置文件作用域的（相同的 KClaw 配置 = 相同的 userId）。
    会话密钥作用域为逻辑浏览器任务，以便在同一配置文件中
    新创建的标签页重用相同的身份契约。
    """
    scope_root = str(get_camofox_state_dir())
    logical_scope = task_id or "default"
    user_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"camofox-user:{scope_root}",
    ).hex[:10]
    session_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"camofox-session:{scope_root}:{logical_scope}",
    ).hex[:16]
    return {
        "user_id": f"kclaw_{user_digest}",
        "session_key": f"task_{session_digest}",
    }
