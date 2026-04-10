"""所有工具的共享中断信号。

提供一个全局 threading.Event，任何工具都可以检查它以确定
用户是否已请求中断。代理的 interrupt() 方法设置此事件，
工具在长时间运行操作期间轮询它。

在工具中的用法：
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"output": "[interrupted]", "returncode": 130}
"""

import threading

_interrupt_event = threading.Event()


def set_interrupt(active: bool) -> None:
    """由代理调用以发出中断信号或清除中断。"""
    if active:
        _interrupt_event.set()
    else:
        _interrupt_event.clear()


def is_interrupted() -> bool:
    """检查是否已请求中断。可从任何线程安全调用。"""
    return _interrupt_event.is_set()
