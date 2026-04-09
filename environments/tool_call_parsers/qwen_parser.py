"""
Qwen 2.5 tool call parser.

Uses the same <tool_call> format as KClaw.
Registered as a separate parser name for clarity when using --tool-parser=qwen.
"""

from environments.tool_call_parsers import register_parser
from environments.tool_call_parsers.kclaw_parser import KClawToolCallParser


@register_parser("qwen")
class QwenToolCallParser(KClawToolCallParser):
    """
    Parser for Qwen 2.5 tool calls.
    Same <tool_call>{"name": ..., "arguments": ...}</tool_call> format as KClaw.
    """

    pass  # Identical format -- inherits everything from KClaw
