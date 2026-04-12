"""kclaw ACP 适配器的 CLI 入口点。

从 ``~/.kclaw/.env`` 加载环境变量，配置日志输出到 stderr
（以便 stdout 专用于 ACP JSON-RPC 传输），
并启动 ACP 代理服务器。

用法::

    python -m acp_adapter.entry
    # 或
    kclaw acp
    # 或
    kclaw-acp
"""

import asyncio
import logging
import sys
from pathlib import Path
from kclaw_constants import get_kclaw_home


def _setup_logging() -> None:
    """将所有日志路由到 stderr，保持 stdout 专用于 ACP stdio 传输。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # 降低噪音库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _load_env() -> None:
    """从 KCLAW_HOME（默认 ``~/.kclaw``）加载 .env 环境变量。"""
    from kclaw_cli.env_loader import load_kclaw_dotenv

    kclaw_home = get_kclaw_home()
    loaded = load_kclaw_dotenv(kclaw_home=kclaw_home)
    if loaded:
        for env_file in loaded:
            logging.getLogger(__name__).info("已从 %s 加载环境变量", env_file)
    else:
        logging.getLogger(__name__).info(
            "未找到 .env 文件: %s，使用系统环境变量", kclaw_home / ".env"
        )


def main() -> None:
    """入口点：加载环境变量、配置日志、启动 ACP 代理服务器。"""
    _setup_logging()
    _load_env()

    logger = logging.getLogger(__name__)
    logger.info("正在启动 kclaw ACP 适配器")

    # 确保项目根目录在 sys.path 中，使 ``from run_agent import AIAgent`` 可用
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import acp
    from .server import KClawACPAgent

    agent = KClawACPAgent()
    try:
        asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))
    except KeyboardInterrupt:
        logger.info("正在关闭 (KeyboardInterrupt)")
    except Exception:
        logger.exception("ACP 代理崩溃")
        sys.exit(1)


if __name__ == "__main__":
    main()
