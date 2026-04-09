"""
KClaw 的时区感知时钟模块。

提供一个单一的 ``now()`` 辅助函数，返回基于用户配置的
IANA 时区（例如 ``Asia/Kolkata``）的时区感知 datetime。

解析顺序：
  1. ``KCLAW_TIMEZONE`` 环境变量
  2. ``~/.kclaw/config.yaml`` 中的 ``timezone`` 键
  3. 退回到服务器的本地时间（``datetime.now().astimezone()``）

无效的时区值会记录警告并安全回退——KClaw 不会因为错误的时区字符串而崩溃。
"""

import logging
import os
from datetime import datetime
from kclaw_constants import get_kclaw_home
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python 3.8 回退（应该不需要——KClaw 需要 3.9+）
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# 缓存状态——解析一次，每次调用时重用。
# 调用 reset_cache() 强制重新解析（例如配置更改后）。
_cached_tz: Optional[ZoneInfo] = None
_cached_tz_name: Optional[str] = None
_cache_resolved: bool = False


def _resolve_timezone_name() -> str:
    """读取配置的 IANA 时区字符串（或空字符串）。

    当回退到 config.yaml 时会进行文件 I/O，因此调用者
    应该缓存结果而不是在每次 ``now()`` 时调用。
    """
    # 1. 环境变量（最高优先级——由 Supervisor 等设置）
    tz_env = os.getenv("KCLAW_TIMEZONE", "").strip()
    if tz_env:
        return tz_env

    # 2. config.yaml ``timezone`` 键
    try:
        import yaml
        kclaw_home = get_kclaw_home()
        config_path = kclaw_home / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            tz_cfg = cfg.get("timezone", "")
            if isinstance(tz_cfg, str) and tz_cfg.strip():
                return tz_cfg.strip()
    except Exception:
        pass

    return ""


def _get_zoneinfo(name: str) -> Optional[ZoneInfo]:
    """验证并返回 ZoneInfo，如果无效则返回 None。"""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, Exception) as exc:
        logger.warning(
            "无效的时区 '%s': %s。退回到服务器本地时间。",
            name, exc,
        )
        return None


def get_timezone() -> Optional[ZoneInfo]:
    """返回用户配置的 ZoneInfo，或 None（表示服务器本地时间）。

    解析一次并缓存。配置更改后调用 ``reset_cache()``。
    """
    global _cached_tz, _cached_tz_name, _cache_resolved
    if not _cache_resolved:
        _cached_tz_name = _resolve_timezone_name()
        _cached_tz = _get_zoneinfo(_cached_tz_name)
        _cache_resolved = True
    return _cached_tz


def get_timezone_name() -> str:
    """返回已配置时区的 IANA 名称，或空字符串。"""
    if not _cache_resolved:
        get_timezone()  # 填充缓存
    return _cached_tz_name or ""


def now() -> datetime:
    """
    返回当前时间作为时区感知的 datetime。

    如果配置了有效时区，返回该时区的挂钟时间。
    否则返回服务器的本地时间（通过 ``astimezone()``）。
    """
    tz = get_timezone()
    if tz is not None:
        return datetime.now(tz)
    # 未配置时区——使用服务器本地时间（仍然是时区感知的）
    return datetime.now().astimezone()


def reset_cache() -> None:
    """清除缓存的时区。供测试和配置更改后使用。"""
    global _cached_tz, _cached_tz_name, _cache_resolved
    _cached_tz = None
    _cached_tz_name = None
    _cache_resolved = False
