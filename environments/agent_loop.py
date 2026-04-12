"""
KClawAgentLoop -- 可复用的多轮 Agent 引擎

使用标准 OpenAI 规范的工具调用来运行 kclaw 工具调用循环。
可与任何返回带有 tool_calls 的 ChatCompletion 对象的服务器配合使用:
    - 第一阶段: OpenAI 服务器类型 (VLLM, SGLang, OpenRouter, OpenAI API)
    - 第二阶段: ManagedServer 带客户端工具调用解析器

该循环传递 tools= 并检查 response.choices[0].message.tool_calls，
与 kclaw 的 run_agent.py 完全相同。工具执行通过
model_tools.py 中的 handle_function_call() 进行分发。
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from model_tools import handle_function_call
from tools.terminal_tool import get_active_env
from tools.tool_result_storage import maybe_persist_tool_result, enforce_turn_budget

# 用于运行同步工具调用的线程池，这些工具调用内部使用 asyncio.run()
#（例如 Modal/Docker/Daytona 终端后端）。在独立线程中运行它们
# 可以获得干净的事件循环，避免它们在 Atropos 的循环内死锁。
# 大小必须足够大以支持并发评估任务（例如，89个 TB2 任务同时进行工具调用）。
# 太小会导致线程池耗尽，任务排队等待数分钟。
# 由 KClawAgentBaseEnv.__init__ 通过 resize_tool_pool() 在运行时调整大小。
_tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=128)


def resize_tool_pool(max_workers: int):
    """
    使用给定大小的新执行器替换全局工具执行器。

    根据 config.tool_pool_size 由 KClawAgentBaseEnv.__init__ 调用。
    可在任何任务提交前安全调用。
    """
    global _tool_executor
    old_executor = _tool_executor
    _tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    old_executor.shutdown(wait=False)
    logger.info("工具线程池已调整为 %d 个工作线程", max_workers)

logger = logging.getLogger(__name__)


@dataclass
class ToolError:
    """记录 Agent 循环期间工具执行错误的记录。"""

    turn: int                  # 发生错误的轮次
    tool_name: str             # 被调用的工具名称
    arguments: str             # 传递的参数（已截断）
    error: str                 # 错误消息
    tool_result: str           # 返回给模型的原始结果


@dataclass
class AgentResult:
    """运行 Agent 循环的结果。"""

    # 完整的对话历史，使用 OpenAI 消息格式
    messages: List[Dict[str, Any]]
    # ManagedServer.get_state()（如果可用，第二阶段），否则为 None
    managed_state: Optional[Dict[str, Any]] = None
    # 进行的 LLM 调用次数
    turns_used: int = 0
    # 模型是否自然停止调用工具（而非达到 max_turns）
    finished_naturally: bool = False
    # 每轮提取的推理内容（来自 PR #297 的辅助函数）
    reasoning_per_turn: List[Optional[str]] = field(default_factory=list)
    # 循环期间遇到的工具错误
    tool_errors: List[ToolError] = field(default_factory=list)


def _extract_reasoning_from_message(message) -> Optional[str]:
    """
    从 ChatCompletion 消息中提取推理内容。

    处理多种 provider 格式:
    1. message.reasoning_content 字段（某些 provider）
    2. message.reasoning 字段（某些 provider）
    3. message.reasoning_details[].text（OpenRouter 风格）

    注意: 从内容中提取 <think> 块的操作不在此处完成 — 那由第一阶段中
    已有的响应处理（服务器完成）或由 ManagedServer 的补丁在第二阶段完成。

    参数:
        message: ChatCompletion 响应中的助手消息

    返回:
        提取的推理文本，如果未找到则返回 None
    """
    # 检查 reasoning_content 字段（各 provider 通用）
    if hasattr(message, "reasoning_content") and message.reasoning_content:
        return message.reasoning_content

    # 检查 reasoning 字段
    if hasattr(message, "reasoning") and message.reasoning:
        return message.reasoning

    # 检查 reasoning_details（OpenRouter 风格）
    if hasattr(message, "reasoning_details") and message.reasoning_details:
        for detail in message.reasoning_details:
            if hasattr(detail, "text") and detail.text:
                return detail.text
            if isinstance(detail, dict) and detail.get("text"):
                return detail["text"]

    return None


class KClawAgentLoop:
    """
    使用标准 OpenAI 规范的工具调用来运行 kclaw 工具调用循环。

    与 run_agent.py 相同的模式:
    - 传递 tools= 给 API
    - 检查 response.choices[0].message.tool_calls
    - 通过 handle_function_call() 分发

    与任何服务器类型的工作方式相同 — OpenAI、VLLM、SGLang、OpenRouter，
    或带解析器的 ManagedServer。服务器决定如何填充响应中的 tool_calls。
    """

    def __init__(
        self,
        server,
        tool_schemas: List[Dict[str, Any]],
        valid_tool_names: Set[str],
        max_turns: int = 30,
        task_id: Optional[str] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        budget_config: Optional["BudgetConfig"] = None,
    ):
        """
        初始化 Agent 循环。

        参数:
            server: 具有 chat_completion() 方法的服务器对象（OpenAIServer、
                    ManagedServer、ServerManager 等）
            tool_schemas: 来自 get_tool_definitions() 的 OpenAI 格式工具定义
            valid_tool_names: 模型允许调用的工具名称集合
            max_turns: 停止前的最大 LLM 调用次数
            task_id: 用于终端/浏览器会话隔离的唯一 ID
            temperature: 采样的温度参数
            max_tokens: 每次生成的最大 token 数（None 使用服务器默认值）
            extra_body: 传递给 OpenAI 客户端 create() 调用的额外参数。
                        用于 OpenRouter provider 偏好设置、transforms 等。
                        例如 {"provider": {"ignore": ["DeepInfra"]}}
            budget_config: 工具结果持久化预算。控制每个工具的阈值、
                        每轮聚合预算和预览大小。
                        如果为 None，使用 DEFAULT_BUDGET（当前硬编码的值）。
        """
        from tools.budget_config import DEFAULT_BUDGET
        self.server = server
        self.tool_schemas = tool_schemas
        self.valid_tool_names = valid_tool_names
        self.max_turns = max_turns
        self.task_id = task_id or str(uuid.uuid4())
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body
        self.budget_config = budget_config or DEFAULT_BUDGET

    async def run(self, messages: List[Dict[str, Any]]) -> AgentResult:
        """
        使用标准 OpenAI 工具调用执行完整的 Agent 循环。

        参数:
            messages: 初始对话消息（系统 + 用户）。
                      随着对话进行会被原地修改。

        返回:
            AgentResult，包含完整的对话历史、管理状态和元数据
        """
        reasoning_per_turn = []
        tool_errors: List[ToolError] = []

        # 每个循环的 TodoStore，用于 todo 工具（临时的，随循环消亡）
        from tools.todo_tool import TodoStore, todo_tool as _todo_tool
        _todo_store = TodoStore()

        # 从第一条用户消息中提取用户任务，用于浏览器快照上下文
        _user_task = None
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    _user_task = content.strip()[:500]  # Cap to avoid huge strings
                break

        import time as _time

        for turn in range(self.max_turns):
            turn_start = _time.monotonic()

            # 构建 chat_completion 的关键字参数
            chat_kwargs = {
                "messages": messages,
                "n": 1,
                "temperature": self.temperature,
            }

            # 仅在我们有工具时传递 tools
            if self.tool_schemas:
                chat_kwargs["tools"] = self.tool_schemas

            # 仅在显式设置时传递 max_tokens
            if self.max_tokens is not None:
                chat_kwargs["max_tokens"] = self.max_tokens

            # 注入 extra_body 用于 provider 特定参数（例如 OpenRouter
            # provider 偏好设置，如 banned/preferred providers、transforms）
            if self.extra_body:
                chat_kwargs["extra_body"] = self.extra_body

            # 进行 API 调用 — 标准 OpenAI 规范
            api_start = _time.monotonic()
            try:
                response = await self.server.chat_completion(**chat_kwargs)
            except Exception as e:
                api_elapsed = _time.monotonic() - api_start
                logger.error("第 %d 轮 API 调用失败 (%.1fs): %s", turn + 1, api_elapsed, e)
                return AgentResult(
                    messages=messages,
                    managed_state=self._get_managed_state(),
                    turns_used=turn + 1,
                    finished_naturally=False,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

            api_elapsed = _time.monotonic() - api_start

            if not response or not response.choices:
                logger.warning("第 %d 轮收到空响应 (api=%.1fs)", turn + 1, api_elapsed)
                return AgentResult(
                    messages=messages,
                    managed_state=self._get_managed_state(),
                    turns_used=turn + 1,
                    finished_naturally=False,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

            assistant_msg = response.choices[0].message

            # 从响应中提取推理内容（所有 provider 格式）
            reasoning = _extract_reasoning_from_message(assistant_msg)
            reasoning_per_turn.append(reasoning)

            # 检查工具调用 — 标准 OpenAI 规范。
            # 后备方案: 如果响应没有结构化的 tool_calls 但内容
            # 包含原始工具调用标签（例如 <tool_call>），则使用
            # kclaw 的独立解析器解析它们。这处理了 ManagedServer 的
            # ToolCallTranslator 因未安装 vLLM 而无法解析的情况。
            if (
                not assistant_msg.tool_calls
                and assistant_msg.content
                and self.tool_schemas
                and "<tool_call>" in (assistant_msg.content or "")
            ):
                try:
                    from environments.tool_call_parsers import get_parser
                    fallback_parser = get_parser("kclaw")
                    parsed_content, parsed_calls = fallback_parser.parse(
                        assistant_msg.content
                    )
                    if parsed_calls:
                        assistant_msg.tool_calls = parsed_calls
                        if parsed_content is not None:
                            assistant_msg.content = parsed_content
                        logger.debug(
                            "后备解析器从原始内容中提取了 %d 个工具调用",
                            len(parsed_calls),
                        )
                except Exception:
                    pass  # Fall through to no tool calls

            if assistant_msg.tool_calls:
                # 将工具调用规范化为字典 — 它们可能以对象（OpenAI API）
                # 或字典（vLLM ToolCallTranslator）的形式出现。
                def _tc_to_dict(tc):
                    if isinstance(tc, dict):
                        return {
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name", tc.get("name", "")),
                                "arguments": tc.get("function", {}).get("arguments", tc.get("arguments", "{}")),
                            },
                        }
                    return {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }

                # 为对话历史构建助手消息字典
                msg_dict: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                    "tool_calls": [_tc_to_dict(tc) for tc in assistant_msg.tool_calls],
                }

                # 为多轮聊天模板处理保留 reasoning_content
                #（例如 Kimi-K2 的模板根据此字段以不同方式渲染 <think> 块
                # 用于历史记录与最新轮次）
                if reasoning:
                    msg_dict["reasoning_content"] = reasoning

                messages.append(msg_dict)

                # 通过 kclaw 的分发机制执行每个工具调用
                for tc in assistant_msg.tool_calls:
                    # 同时处理对象（OpenAI）和字典（vLLM）格式
                    if isinstance(tc, dict):
                        tool_name = tc.get("function", {}).get("name", tc.get("name", ""))
                        tool_args_raw = tc.get("function", {}).get("arguments", tc.get("arguments", "{}"))
                    else:
                        tool_name = tc.function.name
                        tool_args_raw = tc.function.arguments

                    # 验证工具名称
                    if tool_name not in self.valid_tool_names:
                        tool_result = json.dumps(
                            {
                                "error": f"Unknown tool '{tool_name}'. "
                                f"Available tools: {sorted(self.valid_tool_names)}"
                            }
                        )
                        tool_errors.append(ToolError(
                            turn=turn + 1, tool_name=tool_name,
                            arguments=tool_args_raw[:200],
                            error=f"Unknown tool '{tool_name}'",
                            tool_result=tool_result,
                        ))
                        logger.warning(
                            "模型在第 %d 轮调用了未知工具 '%s'",
                            tool_name, turn + 1,
                        )
                    else:
                        # 解析参数
                        try:
                            args = json.loads(tool_args_raw)
                        except json.JSONDecodeError as e:
                            args = None
                            tool_result = json.dumps(
                                {"error": f"工具参数中的无效 JSON: {e}。请使用有效的 JSON 重试。"}
                            )
                            tool_errors.append(ToolError(
                                turn=turn + 1, tool_name=tool_name,
                                arguments=tool_args_raw[:200],
                                error=f"Invalid JSON: {e}",
                                tool_result=tool_result,
                            ))
                            logger.warning(
                                "工具 '%s' 的参数中存在无效 JSON: %s",
                                tool_name, tool_args_raw[:200],
                            )

                        # 仅在参数成功解析后才分发工具
                        if args is not None:
                            try:
                                if tool_name == "terminal":
                                    backend = os.getenv("TERMINAL_ENV", "local")
                                    cmd_preview = args.get("command", "")[:80]
                                    logger.info(
                                        "[%s] $ %s", self.task_id[:8], cmd_preview,
                                    )

                                tool_submit_time = _time.monotonic()

                                # Todo 工具 — 本地处理（需要每个循环的 TodoStore）
                                if tool_name == "todo":
                                    tool_result = _todo_tool(
                                        todos=args.get("todos"),
                                        merge=args.get("merge", False),
                                        store=_todo_store,
                                    )
                                    tool_elapsed = _time.monotonic() - tool_submit_time
                                elif tool_name == "memory":
                                    tool_result = json.dumps({"error": "记忆功能在强化学习环境中不可用。"})
                                    tool_elapsed = _time.monotonic() - tool_submit_time
                                elif tool_name == "session_search":
                                    tool_result = json.dumps({"error": "会话搜索在强化学习环境中不可用。"})
                                    tool_elapsed = _time.monotonic() - tool_submit_time
                                else:
                                    # 在线程池中运行工具调用，以便内部使用
                                    # asyncio.run() 的后端（modal、docker、daytona）获得
                                    # 干净的事件循环而不是死锁。
                                    loop = asyncio.get_event_loop()
                                # 捕获当前的 tool_name/args 用于 lambda
                                    _tn, _ta, _tid = tool_name, args, self.task_id
                                    tool_result = await loop.run_in_executor(
                                        _tool_executor,
                                        lambda: handle_function_call(
                                            _tn, _ta, task_id=_tid,
                                            user_task=_user_task,
                                        ),
                                    )
                                    tool_elapsed = _time.monotonic() - tool_submit_time

                                # 记录慢速工具和线程池统计信息用于调试
                                pool_active = _tool_executor._work_queue.qsize()
                                if tool_elapsed > 30:
                                    logger.warning(
                                        "[%s] 第 %d 轮: %s 耗时 %.1fs（池队列=%d）",
                                        self.task_id[:8], turn + 1, tool_name,
                                        tool_elapsed, pool_active,
                                    )
                            except Exception as e:
                                tool_result = json.dumps(
                                    {"error": f"工具执行失败: {type(e).__name__}: {str(e)}"}
                                )
                                tool_errors.append(ToolError(
                                    turn=turn + 1, tool_name=tool_name,
                                    arguments=tool_args_raw[:200],
                                    error=f"{type(e).__name__}: {str(e)}",
                                    tool_result=tool_result,
                                ))
                                logger.error(
                                    "工具 '%s' 在第 %d 轮执行失败: %s",
                                    tool_name, turn + 1, e,
                                )

                        # 还要检查工具是否在其 JSON 结果中返回了错误
                        try:
                            result_data = json.loads(tool_result)
                            if isinstance(result_data, dict):
                                err = result_data.get("error")
                                exit_code = result_data.get("exit_code")
                                if err and exit_code and exit_code < 0:
                                    tool_errors.append(ToolError(
                                        turn=turn + 1, tool_name=tool_name,
                                        arguments=tool_args_raw[:200],
                                        error=str(err),
                                        tool_result=tool_result[:500],
                                    ))
                        except (json.JSONDecodeError, TypeError):
                            pass

                    tc_id = tc.get("id", "") if isinstance(tc, dict) else tc.id
                    tool_result = maybe_persist_tool_result(
                        content=tool_result,
                        tool_name=tool_name,
                        tool_use_id=tc_id,
                        env=get_active_env(self.task_id),
                        config=self.budget_config,
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": tool_result,
                        }
                    )

                num_tcs = len(assistant_msg.tool_calls)
                if num_tcs > 0:
                    enforce_turn_budget(
                        messages[-num_tcs:],
                        env=get_active_env(self.task_id),
                        config=self.budget_config,
                    )

                turn_elapsed = _time.monotonic() - turn_start
                logger.info(
                    "[%s] 第 %d 轮: api=%.1fs, %d 个工具, 轮次总计=%.1fs",
                    self.task_id[:8], turn + 1, api_elapsed,
                    len(assistant_msg.tool_calls), turn_elapsed,
                )

            else:
                # 没有工具调用 — 模型已完成
                msg_dict = {
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                }
                if reasoning:
                    msg_dict["reasoning_content"] = reasoning
                messages.append(msg_dict)

                turn_elapsed = _time.monotonic() - turn_start
                logger.info(
                    "[%s] 第 %d 轮: api=%.1fs, 无工具（已完成）, 轮次总计=%.1fs",
                    self.task_id[:8], turn + 1, api_elapsed, turn_elapsed,
                )

                return AgentResult(
                    messages=messages,
                    managed_state=self._get_managed_state(),
                    turns_used=turn + 1,
                    finished_naturally=True,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

        # 达到最大轮次但模型未停止
        logger.info("Agent 达到最大轮次 (%d) 但未完成", self.max_turns)
        return AgentResult(
            messages=messages,
            managed_state=self._get_managed_state(),
            turns_used=self.max_turns,
            finished_naturally=False,
            reasoning_per_turn=reasoning_per_turn,
            tool_errors=tool_errors,
        )

    def _get_managed_state(self) -> Optional[Dict[str, Any]]:
        """
        如果服务器支持，获取 ManagedServer 状态。

        返回包含 SequenceNodes 的状态字典，其中包含 tokens/logprobs/masks，
        如果服务器不支持 get_state()（例如普通 OpenAI 服务器）则返回 None。
        """
        if hasattr(self.server, "get_state"):
            return self.server.get_state()
        return None
