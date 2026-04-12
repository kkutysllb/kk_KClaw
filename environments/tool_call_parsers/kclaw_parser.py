"""
KClaw 工具调用解析器。

Format: <tool_call>{"name": "func", "arguments": {...}}</tool_call>
基于 VLLM 的 KClaw2ProToolParser.extract_tool_calls()
"""

import json
import re
import uuid
from typing import List, Optional, Tuple

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


@register_parser("kclaw")
class KClawToolCallParser(ToolCallParser):
    """
    KClaw 格式工具调用解析器。

    Matches <tool_call>...</tool_call> tags containing JSON with "name" and "arguments".
    也处理字符串末尾未闭合的 <tool_call>（截断生成）。
    """

    # 匹配已闭合和未闭合的 tool_call 标签
    PATTERN = re.compile(
        r"<tool_call>\s*(.*?)\s*</tool_call>|<tool_call>\s*(.*)", re.DOTALL
    )

    def parse(self, text: str) -> ParseResult:
        if "<tool_call>" not in text:
            return text, None

        try:
            matches = self.PATTERN.findall(text)
            if not matches:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []
            for match in matches:
                # match 是一个元组: (closed_content, unclosed_content)
                raw_json = match[0] if match[0] else match[1]
                if not raw_json.strip():
                    continue

                tc_data = json.loads(raw_json)
                tool_calls.append(
                    ChatCompletionMessageToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=Function(
                            name=tc_data["name"],
                            arguments=json.dumps(
                                tc_data.get("arguments", {}), ensure_ascii=False
                            ),
                        ),
                    )
                )

            if not tool_calls:
                return text, None

            # Content 是第一个 <tool_call>标签之前的所有文本
            content = text[: text.find("<tool_call>")].strip()
            return content if content else None, tool_calls

        except Exception:
            return text, None
