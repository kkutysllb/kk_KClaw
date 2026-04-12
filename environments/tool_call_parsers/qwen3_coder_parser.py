"""
Qwen3-Coder 工具调用解析器。

格式使用 XML 风格的嵌套标签：
    <tool_call>
    <function=function_name>
    <parameter=param_name>value</parameter>
    <parameter=param_name2>value2</parameter>
    </function>
    </tool_call>

参数从 <parameter=name>value</parameter> 标签中提取，
如果 schema 可用则进行类型转换，否则视为字符串。

基于 VLLM 的 Qwen3CoderToolParser.extract_tool_calls()
"""

import ast
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


def _try_convert_value(value: str) -> Any:
    """
    尝试将参数值字符串转换为原生 Python 类型。
    处理 null、数字、布尔值、JSON 对象/数组，回退为字符串。
    """
    stripped = value.strip()

    # 处理 null
    if stripped.lower() == "null":
        return None

    # 先尝试 JSON（处理对象、数组、字符串、数字、布尔值）
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试 Python 字面量求值（处理元组等）
    try:
        return ast.literal_eval(stripped)
    except (ValueError, SyntaxError, TypeError):
        pass

    # 返回字符串
    return stripped


@register_parser("qwen3_coder")
class Qwen3CoderToolCallParser(ToolCallParser):
    """
    Qwen3-Coder XML 格式工具调用解析器。

    使用嵌套 XML 标签: <tool_call><function=name><parameter=key>val</parameter></function></tool_call>
    """

    START_TOKEN = "<tool_call>"
    FUNCTION_PREFIX = "<function="

    # 查找完整的 tool_call 块（或末尾未闭合的）
    TOOL_CALL_REGEX = re.compile(
        r"<tool_call>(.*?)</tool_call>|<tool_call>(.*?)$", re.DOTALL
    )

    # 在 tool_call 中查找函数块
    FUNCTION_REGEX = re.compile(
        r"<function=(.*?)</function>|<function=(.*)$", re.DOTALL
    )

    # 在函数中查找参数块
    PARAMETER_REGEX = re.compile(
        r"<parameter=(.*?)(?:</parameter>|(?=<parameter=)|(?=</function>)|$)",
        re.DOTALL,
    )

    def _parse_function_call(self, function_str: str) -> Optional[ChatCompletionMessageToolCall]:
        """将单个 <function=name>...</function> 块解析为 ToolCall。"""
        try:
            # 提取函数名: 第一个 '>' 之前的所有内容
            gt_idx = function_str.index(">")
            func_name = function_str[:gt_idx].strip()
            params_str = function_str[gt_idx + 1:]

            # 提取参数
            param_dict: Dict[str, Any] = {}
            for match_text in self.PARAMETER_REGEX.findall(params_str):
                if ">" not in match_text:
                    continue
                eq_idx = match_text.index(">")
                param_name = match_text[:eq_idx].strip()
                param_value = match_text[eq_idx + 1:]

                # 清理空白
                if param_value.startswith("\n"):
                    param_value = param_value[1:]
                if param_value.endswith("\n"):
                    param_value = param_value[:-1]

                param_dict[param_name] = _try_convert_value(param_value)

            return ChatCompletionMessageToolCall(
                id=f"call_{uuid.uuid4().hex[:24]}",
                type="function",
                function=Function(
                    name=func_name,
                    arguments=json.dumps(param_dict, ensure_ascii=False),
                ),
            )
        except (ValueError, IndexError):
            return None

    def parse(self, text: str) -> ParseResult:
        if self.FUNCTION_PREFIX not in text:
            return text, None

        try:
            # 查找所有 tool_call 块
            tc_matches = self.TOOL_CALL_REGEX.findall(text)
            raw_blocks = [m[0] if m[0] else m[1] for m in tc_matches]

            # 回退: 如果没有 tool_call 标签，尝试整个文本
            if not raw_blocks:
                raw_blocks = [text]

            # 在每个 tool_call 中查找函数块
            function_strs: List[str] = []
            for block in raw_blocks:
                func_matches = self.FUNCTION_REGEX.findall(block)
                function_strs.extend(m[0] if m[0] else m[1] for m in func_matches)

            if not function_strs:
                return text, None

            # 解析每个函数调用
            tool_calls: List[ChatCompletionMessageToolCall] = []
            for func_str in function_strs:
                tc = self._parse_function_call(func_str)
                if tc is not None:
                    tool_calls.append(tc)

            if not tool_calls:
                return text, None

            # 工具调用之前的内容
            first_tc = text.find(self.START_TOKEN)
            if first_tc < 0:
                first_tc = text.find(self.FUNCTION_PREFIX)
            content = text[:first_tc].strip() if first_tc > 0 else None

            return content, tool_calls

        except Exception:
            return text, None
