#!/usr/bin/env python3
"""
澄清工具模块 - 交互式澄清问题

允许代理向用户呈现结构化的多选问题或开放式提示。
在 CLI 模式下,可以通过方向键导航选择。
在消息平台上,选择以编号列表的形式呈现。

实际的用户交互逻辑位于平台层 (CLI 为 cli.py,消息为 gateway/run.py)。
本模块定义了 schema、验证和一个将调用委托给平台提供的回调的薄调度层。
"""

import json
from typing import List, Optional, Callable


# 代理可以提供的预定义选择的最大数量。
# UI 始终会附加第5个"其他(输入您的答案)"选项。
MAX_CHOICES = 4


def clarify_tool(
    question: str,
    choices: Optional[List[str]] = None,
    callback: Optional[Callable] = None,
) -> str:
    """
    向用户提问,可以提供多选选项。

    参数:
        question: 要呈现的问题文本。
        choices:  最多4个预定义的答案选项。省略时为纯开放式问题。
        callback: 平台提供的函数,处理实际的 UI 交互。
                  签名: callback(question, choices) -> str。
                  由代理运行器注入 (cli.py / gateway)。

    返回:
        包含用户响应的 JSON 字符串。
    """
    if not question or not question.strip():
        return tool_error("Question text is required.")

    question = question.strip()

    # 验证并修剪选项
    if choices is not None:
        if not isinstance(choices, list):
            return tool_error("choices must be a list of strings.")
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None  # empty list → open-ended

    if callback is None:
        return json.dumps(
            {"error": "Clarify tool is not available in this execution context."},
            ensure_ascii=False,
        )

    try:
        user_response = callback(question, choices)
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to get user input: {exc}"},
            ensure_ascii=False,
        )

    return json.dumps({
        "question": question,
        "choices_offered": choices,
        "user_response": str(user_response).strip(),
    }, ensure_ascii=False)


def check_clarify_requirements() -> bool:
    """澄清工具没有外部依赖 -- 始终可用。"""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

CLARIFY_SCHEMA = {
    "name": "clarify",
    "description": (
        "Ask the user a question when you need clarification, feedback, or a "
        "decision before proceeding. Supports two modes:\n\n"
        "1. **Multiple choice** — provide up to 4 choices. The user picks one "
        "or types their own answer via a 5th 'Other' option.\n"
        "2. **Open-ended** — omit choices entirely. The user types a free-form "
        "response.\n\n"
        "Use this tool when:\n"
        "- The task is ambiguous and you need the user to choose an approach\n"
        "- You want post-task feedback ('How did that work out?')\n"
        "- You want to offer to save a skill or update memory\n"
        "- A decision has meaningful trade-offs the user should weigh in on\n\n"
        "Do NOT use this tool for simple yes/no confirmation of dangerous "
        "commands (the terminal tool handles that). Prefer making a reasonable "
        "default choice yourself when the decision is low-stakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to present to the user.",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CHOICES,
                "description": (
                    "Up to 4 answer choices. Omit this parameter entirely to "
                    "ask an open-ended question. When provided, the UI "
                    "automatically appends an 'Other (type your answer)' option."
                ),
            },
        },
        "required": ["question"],
    },
}


# --- 注册 ---
from tools.registry import registry, tool_error

registry.register(
    name="clarify",
    toolset="clarify",
    schema=CLARIFY_SCHEMA,
    handler=lambda args, **kw: clarify_tool(
        question=args.get("question", ""),
        choices=args.get("choices"),
        callback=kw.get("callback")),
    check_fn=check_clarify_requirements,
    emoji="❓",
)
