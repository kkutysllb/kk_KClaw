"""
Longcat Flash Chat 工具调用解析器。

与 KClaw 相同，但使用 <longcat_tool_call> 标签代替 <tool_call>。
基于 VLLM 的 LongcatFlashToolParser（扩展 KClaw2ProToolParser）。
"""

import json
import re
import uuid
from typing import List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


@register_parser("longcat")
class LongcatToolCallParser(ToolCallParser):
    """
    Longcat Flash Chat 工具调用解析器。
    与 KClaw 逻辑相同，只是标签名不同。
    """

    PATTERN = re.compile(
        r"<longcat_tool_call>\s*(.*?)\s*</longcat_tool_call>|<longcat_tool_call>\s*(.*)",
        re.DOTALL,
    )

    def parse(self, text: str) -> ParseResult:
        if "<longcat_tool_call>" not in text:
            return text, None

        try:
            matches = self.PATTERN.findall(text)
            if not matches:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []
            for match in matches:
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

            content = text[: text.find("<longcat_tool_call>")].strip()
            return content if content else None, tool_calls

        except Exception:
            return text, None
