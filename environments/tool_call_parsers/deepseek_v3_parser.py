"""
DeepSeek V3 工具调用解析器。

格式使用特殊的 unicode 标记：
    <｜tool▁calls▁begin｜>
    <｜tool▁call▁begin｜>type<｜tool▁sep｜>function_name
    ```json
    {"arg": "value"}
    ```
    <｜tool▁call▁end｜>
    <｜tool▁calls▁end｜>

修复 Issue #989: 支持多个同时工具调用。
"""

import re
import uuid
import logging
from typing import List, Optional, Tuple

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser

logger = logging.getLogger(__name__)

@register_parser("deepseek_v3")
class DeepSeekV3ToolCallParser(ToolCallParser):
    """
    DeepSeek V3 工具调用解析器。

    使用带有全角尖括号和块元素的特殊 unicode 标记。
    从结构化格式中提取类型、函数名和 JSON 参数。
    确保在模型执行多个操作时捕获所有工具调用。
    """

    START_TOKEN = "<｜tool▁calls▁begin｜>"

    # 更新的 PATTERN: 使用 \s* 代替字面 \n 以增强对模型格式变化的鲁棒性
    # (Issue #989)。
    PATTERN = re.compile(
        r"<｜tool▁call▁begin｜>(?P<type>.*?)<｜tool▁sep｜>(?P<function_name>.*?)\s*```json\s*(?P<function_arguments>.*?)\s*```\s*<｜tool▁call▁end｜>",
        re.DOTALL,
    )

    def parse(self, text: str) -> ParseResult:
        """
        解析输入文本并提取所有可用的工具调用。
        """
        if self.START_TOKEN not in text:
            return text, None

        try:
            # 使用 finditer 捕获序列中的所有工具调用
            matches = list(self.PATTERN.finditer(text))
            if not matches:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []
            
            for match in matches:
                func_name = match.group("function_name").strip()
                func_args = match.group("function_arguments").strip()
                
                tool_calls.append(
                    ChatCompletionMessageToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=Function(
                            name=func_name,
                            arguments=func_args,
                        ),
                    )
                )

            if tool_calls:
                # Content 是第一个工具调用块之前的文本
                content_index = text.find(self.START_TOKEN)
                content = text[:content_index].strip()
                return content if content else None, tool_calls

            return text, None

        except Exception as e:
            logger.error("解析 DeepSeek V3 工具调用时出错: %s", e)
            return text, None
