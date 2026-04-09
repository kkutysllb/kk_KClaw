"""KClaw Agent 的集中日志设置模块。

提供一个单一的 ``setup_logging()`` 入口点，CLI 和网关都在启动路径早期调用。
所有日志文件位于 ``~/.kclaw/logs/`` 下（通过 ``get_kclaw_home()`` 支持配置）。

生成的日志文件：
    agent.log   — INFO+，所有智能体/工具/会话活动（主要日志）
    errors.log  — WARNING+，仅错误和警告（快速分类）

两个文件都使用带有 ``RedactingFormatter`` 的 ``RotatingFileHandler``，
因此 secrets 永远不会写入磁盘。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from kclaw_constants import get_kclaw_home

# 标记追踪 setup_logging() 是否已运行。该函数是幂等的——
# 调用两次是安全的，但第二次调用是空操作，除非 ``force=True``。
_logging_initialized = False

# 默认日志格式——包括时间戳、级别、日志器名称和消息。
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_LOG_FORMAT_VERBOSE = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# 在 DEBUG/INFO 级别很吵的第三方日志器。
_NOISY_LOGGERS = (
    "openai",
    "openai._base_client",
    "httpx",
    "httpcore",
    "asyncio",
    "hpack",
    "hpack.hpack",
    "grpc",
    "modal",
    "urllib3",
    "urllib3.connectionpool",
    "websockets",
    "charset_normalizer",
    "markdown_it",
)


def setup_logging(
    *,
    kclaw_home: Optional[Path] = None,
    log_level: Optional[str] = None,
    max_size_mb: Optional[int] = None,
    backup_count: Optional[int] = None,
    mode: Optional[str] = None,
    force: bool = False,
) -> Path:
    """配置 KClaw 日志子系统。

    可以安全地多次调用——第二次调用是空操作，除非
    *force* 为 ``True``。

    参数
    ----------
    kclaw_home
        覆盖 KClaw 主目录。默认为
        ``get_kclaw_home()``（支持配置）。
    log_level
        ``agent.log`` 文件处理器的最低级别。接受任何
        标准 Python 级别名称（``"DEBUG"``、``"INFO"``、``"WARNING"``）。
        默认为 ``"INFO"`` 或 config.yaml 中的 ``logging.level`` 值。
    max_size_mb
        轮转前每个日志文件的最大大小（兆字节）。
        默认为 5 或 config.yaml 中的 ``logging.max_size_mb`` 值。
    backup_count
        要保留的轮转备份文件数量。
        默认为 3 或 config.yaml 中的 ``logging.backup_count`` 值。
    mode
        调用者上下文的提示：``"cli"``、``"gateway"``、``"cron"``。
        目前仅用于日志格式调整（gateway 包含 PID）。
    force
        即使已经调用过也重新运行设置。

    返回
    -------
    Path
        写入文件的 ``logs/`` 目录。
    """
    global _logging_initialized
    if _logging_initialized and not force:
        home = kclaw_home or get_kclaw_home()
        return home / "logs"

    home = kclaw_home or get_kclaw_home()
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Read config defaults (best-effort — config may not be loaded yet).
    cfg_level, cfg_max_size, cfg_backup = _read_logging_config()

    level_name = (log_level or cfg_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    max_bytes = (max_size_mb or cfg_max_size or 5) * 1024 * 1024
    backups = backup_count or cfg_backup or 3

    # Lazy import to avoid circular dependency at module load time.
    from agent.redact import RedactingFormatter

    root = logging.getLogger()

    # --- agent.log (INFO+) — the main activity log -------------------------
    _add_rotating_handler(
        root,
        log_dir / "agent.log",
        level=level,
        max_bytes=max_bytes,
        backup_count=backups,
        formatter=RedactingFormatter(_LOG_FORMAT),
    )

    # --- errors.log (WARNING+) — quick triage log --------------------------
    _add_rotating_handler(
        root,
        log_dir / "errors.log",
        level=logging.WARNING,
        max_bytes=2 * 1024 * 1024,
        backup_count=2,
        formatter=RedactingFormatter(_LOG_FORMAT),
    )

    # Ensure root logger level is low enough for the handlers to fire.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    # Suppress noisy third-party loggers.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _logging_initialized = True
    return log_dir


def setup_verbose_logging() -> None:
    """为 ``--verbose`` / ``-v`` 模式启用 DEBUG 级别的控制台日志记录。

    当 ``verbose_logging=True`` 时由 ``AIAgent.__init__()`` 调用。
    """
    from agent.redact import RedactingFormatter

    root = logging.getLogger()

    # Avoid adding duplicate stream handlers.
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            if getattr(h, "_kclaw_verbose", False):
                return

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT_VERBOSE, datefmt="%H:%M:%S"))
    handler._kclaw_verbose = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # Lower root logger level so DEBUG records reach all handlers.
    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    # Keep third-party libraries at WARNING to reduce noise.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # rex-deploy at INFO for sandbox status.
    logging.getLogger("rex-deploy").setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _add_rotating_handler(
    logger: logging.Logger,
    path: Path,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
) -> None:
    """向 *logger* 添加 ``RotatingFileHandler``，如果已存在相同解析文件路径的处理程序则跳过（幂等）。
    """
    resolved = path.resolve()
    for existing in logger.handlers:
        if (
            isinstance(existing, RotatingFileHandler)
            and Path(getattr(existing, "baseFilename", "")).resolve() == resolved
        ):
            return  # already attached

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(path), maxBytes=max_bytes, backupCount=backup_count,
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _read_logging_config():
    """尽力从 config.yaml 读取 ``logging.*`` 配置。

    返回 ``(level, max_size_mb, backup_count)`` —— 任何值都可能是 ``None``。
    """
    try:
        import yaml
        config_path = get_kclaw_home() / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            log_cfg = cfg.get("logging", {})
            if isinstance(log_cfg, dict):
                return (
                    log_cfg.get("level"),
                    log_cfg.get("max_size_mb"),
                    log_cfg.get("backup_count"),
                )
    except Exception:
        pass
    return (None, None, None)
