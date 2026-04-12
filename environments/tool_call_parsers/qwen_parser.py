"""
Qwen 2.5 工具调用解析器。

使用与 KClaw 相同的 <tool_call>格式。
注册为单独的解析器名称，以便使用 --tool-parser=qwen 时更清晰。
"""

from environments.tool_call_parsers import register_parser
from environments.tool_call_parsers.kclaw_parser import KClawToolCallParser


@register_parser("qwen")
class QwenToolCallParser(KClawToolCallParser):
    """
    Qwen 2.5 工具调用解析器。
    与 KClaw 相同的 <tool_call>{"name": ..., "arguments": ...}</tool_call>格式。
    """

    pass  # 格式相同 -- 继承 KClaw 的所有内容
