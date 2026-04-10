#!/usr/bin/env python3
"""工具包命名空间。

保持包导入的副作用最小化。导入 ``tools`` 不应该
急切地导入完整的工具堆栈，因为几个子系统在
``kclaw_cli.config`` 仍在初始化时加载工具。

调用者应直接导入具体的子模块，例如：

    import tools.web_tools
    from tools import browser_tool

Python 将通过包路径解析这些子模块，而无需它们
在此处重新导出。
"""


def check_file_requirements():
    """文件工具仅需要终端后端可用。"""
    from .terminal_tool import check_terminal_requirements

    return check_terminal_requirements()


__all__ = ["check_file_requirements"]
