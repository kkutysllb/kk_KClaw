"""内置的 boot-md 钩子 — 在网关启动时运行 ~/.kclaw/BOOT.md。

此钩子始终注册。如果不存在 BOOT.md，则静默跳过。
要激活，请在 ``~/.kclaw/BOOT.md`` 中创建指令，供代理
在每次网关重启时执行。

BOOT.md 示例::

    # 启动检查清单

    1. 检查昨夜是否有 cron 作业失败
    2. 发送状态更新到 Discord #general
    3. 如果 /opt/app/deploy.log 中有错误，总结它们

代理在后台线程中运行，因此不会阻塞网关
启动。如果不需要注意，代理会回复 [SILENT]
以取消传递。
"""

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("hooks.boot-md")

from kclaw_constants import get_kclaw_home
KCLAW_HOME = get_kclaw_home()
BOOT_FILE = KCLAW_HOME / "BOOT.md"


def _build_boot_prompt(content: str) -> str:
    """将 BOOT.md 内容包装在系统级指令中。"""
    return (
        "你正在运行启动检查清单。请严格按照以下 BOOT.md "
        "指令执行。\n\n"
        "---\n"
        f"{content}\n"
        "---\n\n"
        "执行每条指令。如果需要向平台发送消息，请使用 send_message 工具。\n"
        "如果不需要注意且没有什么要报告的，"
        "请仅回复: [SILENT]"
    )


def _run_boot_agent(content: str) -> None:
    """生成一次性代理会话来执行启动指令。"""
    try:
        from run_agent import AIAgent

        prompt = _build_boot_prompt(content)
        agent = AIAgent(
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=20,
        )
        result = agent.run_conversation(prompt)
        response = result.get("final_response", "")
        if response and "[SILENT]" not in response:
            logger.info("boot-md 完成: %s", response[:200])
        else:
            logger.info("boot-md 完成（无需报告）")
    except Exception as e:
        logger.error("boot-md 代理失败: %s", e)


async def handle(event_type: str, context: dict) -> None:
    """网关启动处理器 — 如果 BOOT.md 存在则运行。"""
    if not BOOT_FILE.exists():
        return

    content = BOOT_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return

    logger.info("正在运行 BOOT.md (%d 个字符)", len(content))

    # 在后台线程中运行，这样不会阻塞网关启动。
    thread = threading.Thread(
        target=_run_boot_agent,
        args=(content,),
        name="boot-md",
        daemon=True,
    )
    thread.start()
