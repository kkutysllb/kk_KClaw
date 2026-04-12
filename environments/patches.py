"""
用于在异步框架（Atropos）内使 kclaw 工具工作的猴子补丁。

问题:
    某些工具在内部使用 asyncio.run()（例如通过 SWE-ReX 的 Modal 后端、
    web_extract）。当从 Atropos 的事件循环内部调用时这会导致崩溃，
    因为 asyncio.run() 不能嵌套。

解决方案:
    Modal 环境（tools/environments/modal.py）现在在内部使用专用的
    _AsyncWorker 线程，使其对 CLI 和 Atropos 使用都是安全的。
    不需要猴子补丁。

    此模块为向后兼容而保留。apply_patches() 是一个空操作。

用法:
    在导入时调用一次 apply_patches()（由 kclaw_base_env.py 自动完成）。
    这是幂等的，安全地可以多次调用。
"""

import logging

logger = logging.getLogger(__name__)

_patches_applied = False


def apply_patches():
    """应用 Atropos 兼容性所需的所有猴子补丁。"""
    global _patches_applied
    if _patches_applied:
        return

    logger.debug("apply_patches() 被调用；无需补丁（异步安全性已内置）")
    _patches_applied = True
