"""
Kimi K2 工具调用解析器。

格式：
    <|tool_calls_section_begin|>
    <|tool_call_begin|>function_id:0<|tool_call_argument_begin|>{"arg": "val"}<|tool_call_end|>
    <|tool_calls_section_end|>

function_id 格式通常为 "functions.func_name:index" 或 "func_name:index"。

基于 VLLM 的 KimiK2ToolParser.extract_tool_calls()
"""

import re
import uuid
from typing import List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


@register_parser("kimi_k2")
class KimiK2ToolCallParser(ToolCallParser):
    """
    Kimi K2 工具调用解析器。

    使用 section begin/end 标记包装单个工具调用的 begin/end 标记。
    tool_call_id 包含函数名（最后一个点之后、冒号之前的部分）。
    """

    # 支持单数和复数变体
    START_TOKENS = [
        "<|tool_calls_section_begin|>",
        "<|tool_call_section_begin|>",
    ]

    # 正则捕获: tool_call_id (例如 "functions.get_weather:0"), function_arguments
    PATTERN = re.compile(
        r"<\|tool_call_begin\|>\s*(?P<tool_call_id>[^<]+:\d+)\s*"
        r"<\|tool_call_argument_begin\|>\s*"
        r"(?P<function_arguments>(?:(?!<\|tool_call_begin\|>).)*?)\s*"
        r"<\|tool_call_end\|>",
        re.DOTALL,
    )

    def parse(self, text: str) -> ParseResult:
        # 检查是否存在任何变体的起始标记
        has_start = any(token in text for token in self.START_TOKENS)
        if not has_start:
            return text, None

        try:
            matches = self.PATTERN.findall(text)
            if not matches:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []
            for match in matches:
                function_id, function_args = match

                # 从 ID 格式中提取函数名: "functions.get_weather:0" -> "get_weather"
                function_name = function_id.split(":")[0].split(".")[-1]

                tool_calls.append(
                    ChatCompletionMessageToolCall(
                        id=function_id,  # 保留原始 ID 格式
                        type="function",
                        function=Function(
                            name=function_name,
                            arguments=function_args.strip(),
                        ),
                    )
                )

            if not tool_calls:
                return text, None

            # Content 是工具调用 section 之前的所有文本
            earliest_start = len(text)
            for token in self.START_TOKENS:
                idx = text.find(token)
                if idx >= 0 and idx < earliest_start:
                    earliest_start = idx

            content = text[:earliest_start].strip()
            return content if content else None, tool_calls

        except Exception:
            return text, None
