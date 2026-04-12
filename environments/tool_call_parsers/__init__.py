"""
工具调用解析器注册表

客户端解析器，用于从原始模型输出文本中提取结构化的 tool_calls。
用于阶段2（VLLM 服务器类型），ManagedServer 的 /generate 端点返回
未解析工具调用的原始文本。

每个解析器都是对应 VLLM 解析器的非流式 extract_tool_calls() 逻辑的独立重新实现。
不依赖 VLLM -- 仅使用标准库（re, json, uuid）和 openai 类型。

用法:
    from environments.tool_call_parsers import get_parser

    parser = get_parser("kclaw")
    content, tool_calls = parser.parse(raw_model_output)
    # content = 已剥离工具调用标记的文本
    # tool_calls = ChatCompletionMessageToolCall 对象列表，如果没有则为 None
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Type

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
)

logger = logging.getLogger(__name__)

# 解析器返回值类型别名
ParseResult = Tuple[Optional[str], Optional[List[ChatCompletionMessageToolCall]]]


class ToolCallParser(ABC):
    """
    工具调用解析器基类。

    每个解析器都知道如何从特定模型系列的原始输出文本格式中提取结构化的 tool_calls。
    """

    @abstractmethod
    def parse(self, text: str) -> ParseResult:
        """
        解析原始模型输出文本中的工具调用。

        Args:
            text: 模型完成的原始解码文本

        Returns:
            (content, tool_calls) 元组，其中:
            - content: 已剥离工具调用标记的文本（消息的 'content' 字段），
                       如果整个输出都是工具调用则为 None
            - tool_calls: ChatCompletionMessageToolCall 对象列表，
                          如果没有找到工具调用则为 None
        """
        raise NotImplementedError


# 全局解析器注册表: name -> parser class
PARSER_REGISTRY: Dict[str, Type[ToolCallParser]] = {}


def register_parser(name: str):
    """
    装饰器，将解析器类注册到给定名称下。

    用法:
        @register_parser("kclaw")
        class KClawToolCallParser(ToolCallParser):
            ...
    """

    def decorator(cls: Type[ToolCallParser]) -> Type[ToolCallParser]:
        PARSER_REGISTRY[name] = cls
        return cls

    return decorator


def get_parser(name: str) -> ToolCallParser:
    """
    按名称获取解析器实例。

    Args:
        name: 解析器名称（例如 "kclaw", "mistral", "llama3_json"）

    Returns:
        已实例化的解析器

    Raises:
        KeyError: 如果在注册表中未找到解析器名称
    """
    if name not in PARSER_REGISTRY:
        available = sorted(PARSER_REGISTRY.keys())
        raise KeyError(
            f"Tool call parser '{name}' not found. Available parsers: {available}"
        )
    return PARSER_REGISTRY[name]()


def list_parsers() -> List[str]:
    """返回已注册解析器名称的排序列表。"""
    return sorted(PARSER_REGISTRY.keys())


# 导入所有解析器模块以通过 @register_parser 装饰器触发注册
# 每个模块在导入时自行注册
from environments.tool_call_parsers.kclaw_parser import KClawToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.longcat_parser import LongcatToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.mistral_parser import MistralToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.llama_parser import LlamaToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.qwen_parser import QwenToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.deepseek_v3_parser import DeepSeekV3ToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.deepseek_v3_1_parser import DeepSeekV31ToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.kimi_k2_parser import KimiK2ToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.glm45_parser import Glm45ToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.glm47_parser import Glm47ToolCallParser  # noqa: E402, F401
from environments.tool_call_parsers.qwen3_coder_parser import Qwen3CoderToolCallParser  # noqa: E402, F401
