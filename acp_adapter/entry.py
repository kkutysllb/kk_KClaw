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
    """Route all logging to stderr so stdout stays clean for ACP stdio."""
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

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _load_env() -> None:
    """Load .env from KCLAW_HOME (default ``~/.kclaw``)."""
    from kclaw_cli.env_loader import load_kclaw_dotenv

    kclaw_home = get_kclaw_home()
    loaded = load_kclaw_dotenv(kclaw_home=kclaw_home)
    if loaded:
        for env_file in loaded:
            logging.getLogger(__name__).info("Loaded env from %s", env_file)
    else:
        logging.getLogger(__name__).info(
            "No .env found at %s, using system env", kclaw_home / ".env"
        )


def main() -> None:
    """Entry point: load env, configure logging, run the ACP agent."""
    _setup_logging()
    _load_env()

    logger = logging.getLogger(__name__)
    logger.info("Starting kclaw ACP adapter")

    # Ensure the project root is on sys.path so ``from run_agent import AIAgent`` works
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import acp
    from .server import KClawACPAgent

    agent = KClawACPAgent()
    try:
        asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
    except Exception:
        logger.exception("ACP agent crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
