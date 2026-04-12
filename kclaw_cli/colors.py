"""KClaw CLI 模块共享的 ANSI 颜色工具。"""

import os
import sys


def should_use_color() -> bool:
    """判断是否应该使用彩色输出。

    遵循 NO_COLOR 环境变量（https://no-color.org/）
    和 TERM=dumb，并结合现有的 TTY 检查。
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return True


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def color(text: str, *codes) -> str:
    """为文本应用颜色代码（仅在适合彩色输出时）。"""
    if not should_use_color():
        return text
    return "".join(codes) + text + Colors.RESET
