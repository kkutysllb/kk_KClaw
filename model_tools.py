#!/usr/bin/env python3
"""
模型工具模块

工具注册表之上的精简编排层。tools/ 目录中的每个工具文件
通过 tools.registry.register() 自注册其 schema、handler 和元数据。
本模块触发发现（通过导入所有工具模块），然后提供
run_agent.py、cli.py、batch_runner.py 和 RL 环境消费的公共 API。

公共 API（保留自原始 2400 行版本的签名）：
    get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode) -> list
    handle_function_call(function_name, function_args, task_id, user_task) -> str
    TOOL_TO_TOOLSET_MAP: dict          (用于 batch_runner.py)
    TOOLSET_REQUIREMENTS: dict         (用于 cli.py, doctor.py)
    get_all_tool_names() -> list
    get_toolset_for_tool(name) -> str
    get_available_toolsets() -> dict
    check_toolset_requirements() -> dict
    check_tool_availability(quiet) -> tuple
"""

import json
import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple

from tools.registry import registry
from toolsets import resolve_toolset, validate_toolset

logger = logging.getLogger(__name__)


# =============================================================================
# 异步桥接  （单一真相来源 — 也被 registry.dispatch 使用）
# =============================================================================

_tool_loop = None          # 主（CLI）线程的持久化循环
_tool_loop_lock = threading.Lock()
_worker_thread_local = threading.local()  # 每个工作线程的持久化循环


def _get_tool_loop():
    """返回用于运行异步工具处理程序的持久化事件循环。

    使用持久化循环（而非 asyncio.run() 每次创建和
    *关闭*一个新循环）可防止当缓存的 httpx/AsyncOpenAI 客户端尝试
    在垃圾回收期间关闭已死循环上的传输时出现的"Event loop is closed"错误。
    """
    global _tool_loop
    with _tool_loop_lock:
        if _tool_loop is None or _tool_loop.is_closed():
            _tool_loop = asyncio.new_event_loop()
        return _tool_loop


def _get_worker_loop():
    """返回当前工作线程的持久化事件循环。

    每个工作线程（例如 delegate_task 的 ThreadPoolExecutor 线程）
    在线程本地存储中获得自己的持久化循环。这样
    可以防止使用 asyncio.run() 时出现的"Event loop is closed"错误：
    asyncio.run() 创建一个循环，运行协程，然后*关闭*循环 — 但缓存的
    httpx/AsyncOpenAI 客户端仍然绑定到那个已死的循环，在垃圾回收
    或后续使用时引发 RuntimeError。

    通过在线程生命周期内保持循环存活，缓存的客户端
    保持有效，其清理在活跃循环上运行。
    """
    loop = getattr(_worker_thread_local, 'loop', None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _worker_thread_local.loop = loop
    return loop


def _run_async(coro):
    """从同步上下文运行异步协程。

    如果当前线程已有运行中的事件循环（例如在
    网关的异步堆栈或 Atropos 的事件循环中），我们启动一个
    临时线程以便 asyncio.run() 可以创建自己的循环而不冲突。

    对于常见的 CLI 路径（无运行中的循环），我们使用持久化事件
    循环，以便缓存的异步客户端（httpx / AsyncOpenAI）保持绑定
    到活跃循环，不会因 GC 而触发"Event loop is closed"。

    从工作线程调用时（并行工具执行），我们使用
    每线程持久化循环，以避免与主线程共享循环的竞争，
    同时避免 asyncio.run() 创建-销毁生命周期导致的"Event loop is closed"错误。

    这是工具处理程序中同步->异步桥接的单一真相来源。
    RL 路径（agent_loop.py、tool_context.py）也提供
    外层线程池包装作为纵深防御，但每个处理程序
    通过此函数自我保护。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 在异步上下文中（网关、RL 环境）— 在新线程中运行。
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=300)

    # 如果在 worker 线程上（例如 delegate_task 中的并行工具执行），
    # 使用每线程持久化循环。这避免了与主线程共享循环的竞争，
    # 同时在线程生命周期内保持缓存的 httpx/AsyncOpenAI 客户端绑定到活跃循环 —
    # 防止 GC 清理时的"Event loop is closed"。
    if threading.current_thread() is not threading.main_thread():
        worker_loop = _get_worker_loop()
        return worker_loop.run_until_complete(coro)

    tool_loop = _get_tool_loop()
    return tool_loop.run_until_complete(coro)


# =============================================================================
# 工具发现  （导入每个模块会触发其 registry.register 调用）
# =============================================================================

def _discover_tools():
    """导入所有工具模块以触发其 registry.register() 调用。

    包装在函数中，以便可选工具中的导入错误（例如未安装 fal_client）
    不会阻止其余部分加载。
    """
    _modules = [
        "tools.web_tools",
        "tools.terminal_tool",
        "tools.file_tools",
        "tools.vision_tools",
        "tools.mixture_of_agents_tool",
        "tools.image_generation_tool",
        "tools.skills_tool",
        "tools.skill_manager_tool",
        "tools.browser_tool",
        "tools.cronjob_tools",
        "tools.rl_training_tool",
        "tools.tts_tool",
        "tools.todo_tool",
        "tools.memory_tool",
        "tools.session_search_tool",
        "tools.clarify_tool",
        "tools.code_execution_tool",
        "tools.delegate_tool",
        "tools.process_registry",
        "tools.send_message_tool",
        # "tools.honcho_tools",  # 已移除 — Honcho 现在是内存 provider 插件
        "tools.homeassistant_tool",
    ]
    import importlib
    for mod_name in _modules:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            logger.warning("无法导入工具模块 %s: %s", mod_name, e)


_discover_tools()

# MCP 工具发现（来自配置文件的外部 MCP 服务器）
try:
    from tools.mcp_tool import discover_mcp_tools
    discover_mcp_tools()
except Exception as e:
    logger.debug("MCP 工具发现失败: %s", e)

# 插件工具发现（用户/项目/pip 插件）
try:
    from kclaw_cli.plugins import discover_plugins
    discover_plugins()
except Exception as e:
    logger.debug("插件发现失败: %s", e)


# =============================================================================
# 向后兼容常量  （发现后构建一次）
# =============================================================================

TOOL_TO_TOOLSET_MAP: Dict[str, str] = registry.get_tool_to_toolset_map()

TOOLSET_REQUIREMENTS: Dict[str, dict] = registry.get_toolset_requirements()

# 上次 get_tool_definitions() 调用解析的工具名称。
# 由 code_execution_tool 使用以了解此会话中哪些工具可用。
_last_resolved_tool_names: List[str] = []


# =============================================================================
# 旧工具集名称映射  （旧的 _tools 后缀名称 -> 工具名称列表）
# =============================================================================

_LEGACY_TOOLSET_MAP = {
    "web_tools": ["web_search", "web_extract"],
    "terminal_tools": ["terminal"],
    "vision_tools": ["vision_analyze"],
    "moa_tools": ["mixture_of_agents"],
    "image_tools": ["image_generate"],
    "skills_tools": ["skills_list", "skill_view", "skill_manage"],
    "browser_tools": [
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_type", "browser_scroll", "browser_back",
        "browser_press", "browser_get_images",
        "browser_vision", "browser_console"
    ],
    "cronjob_tools": ["cronjob"],
    "rl_tools": [
        "rl_list_environments", "rl_select_environment",
        "rl_get_current_config", "rl_edit_config",
        "rl_start_training", "rl_check_status",
        "rl_stop_training", "rl_get_results",
        "rl_list_runs", "rl_test_inference"
    ],
    "file_tools": ["read_file", "write_file", "patch", "search_files"],
    "tts_tools": ["text_to_speech"],
}


# =============================================================================
# get_tool_definitions  （主要 schema 提供者）
# =============================================================================

def get_tool_definitions(
    enabled_toolsets: List[str] = None,
    disabled_toolsets: List[str] = None,
    quiet_mode: bool = False,
) -> List[Dict[str, Any]]:
    """
    获取用于模型 API 调用的工具定义，支持基于工具集的过滤。

    所有工具必须属于某个工具集才能访问。

    参数：
        enabled_toolsets: 仅包含这些工具集中的工具。
        disabled_toolsets: 排除这些工具集中的工具（当 enabled_toolsets 为 None 时）。
        quiet_mode: 抑制状态打印。

    返回：
        过滤后的 OpenAI 格式工具定义列表。
    """
    # 确定调用者想要的工具名称集合
    tools_to_include: set = set()

    if enabled_toolsets is not None:
        for toolset_name in enabled_toolsets:
            if validate_toolset(toolset_name):
                resolved = resolve_toolset(toolset_name)
                tools_to_include.update(resolved)
                if not quiet_mode:
                    print(f"✅ 已启用工具集 '{toolset_name}': {', '.join(resolved) if resolved else '无工具'}")
            elif toolset_name in _LEGACY_TOOLSET_MAP:
                legacy_tools = _LEGACY_TOOLSET_MAP[toolset_name]
                tools_to_include.update(legacy_tools)
                if not quiet_mode:
                    print(f"✅ 已启用旧工具集 '{toolset_name}': {', '.join(legacy_tools)}")
            else:
                if not quiet_mode:
                    print(f"⚠️  未知工具集: {toolset_name}")

    elif disabled_toolsets:
        from toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

        for toolset_name in disabled_toolsets:
            if validate_toolset(toolset_name):
                resolved = resolve_toolset(toolset_name)
                tools_to_include.difference_update(resolved)
                if not quiet_mode:
                    print(f"🚫 已禁用工具集 '{toolset_name}': {', '.join(resolved) if resolved else '无工具'}")
            elif toolset_name in _LEGACY_TOOLSET_MAP:
                legacy_tools = _LEGACY_TOOLSET_MAP[toolset_name]
                tools_to_include.difference_update(legacy_tools)
                if not quiet_mode:
                    print(f"🚫 已禁用旧工具集 '{toolset_name}': {', '.join(legacy_tools)}")
            else:
                if not quiet_mode:
                    print(f"⚠️  未知工具集: {toolset_name}")
    else:
        from toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

    # 插件注册的工具现在通过正常工具集路径解析
    # — validate_toolset() / resolve_toolset() / get_all_toolsets()
    # 都检查插件提供的工具集的注册表。无需旁路；
    # 插件像任何其他工具集一样尊重 enabled_toolsets / disabled_toolsets。

    # 向注册表请求 schema（仅返回 check_fn 通过的工具）
    filtered_tools = registry.get_definitions(tools_to_include, quiet=quiet_mode)

    # 实际通过 check_fn 过滤的工具名称集合。
    # 用于任何按名称引用其他工具的下游 schema —
    # 否则模型会看到描述中提到但实际不存在的工具，并幻觉调用它们。
    available_tool_names = {t["function"]["name"] for t in filtered_tools}

    # 重建 execute_code schema，仅列出实际可用的沙箱工具。
    # 否则模型会看到"web_search 在 execute_code 中可用"，
    # 即使 API 密钥未配置或工具集被禁用（#560-discord）。
    if "execute_code" in available_tool_names:
        from tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS, build_execute_code_schema
        sandbox_enabled = SANDBOX_ALLOWED_TOOLS & available_tool_names
        dynamic_schema = build_execute_code_schema(sandbox_enabled)
        for i, td in enumerate(filtered_tools):
            if td.get("function", {}).get("name") == "execute_code":
                filtered_tools[i] = {"type": "function", "function": dynamic_schema}
                break

    # 当 web_search / web_extract 不可用时，从 browser_navigate 描述中
    # 去除 web 工具交叉引用。静态 schema 说"优先使用 web_search 或 web_extract"，
    # 这会导致模型在缺少这些工具时幻觉调用它们。
    if "browser_navigate" in available_tool_names:
        web_tools_available = {"web_search", "web_extract"} & available_tool_names
        if not web_tools_available:
            for i, td in enumerate(filtered_tools):
                if td.get("function", {}).get("name") == "browser_navigate":
                    desc = td["function"].get("description", "")
                    desc = desc.replace(
                        " For simple information retrieval, prefer web_search or web_extract (faster, cheaper).",
                        "",
                    )
                    filtered_tools[i] = {
                        "type": "function",
                        "function": {**td["function"], "description": desc},
                    }
                    break

    if not quiet_mode:
        if filtered_tools:
            tool_names = [t["function"]["name"] for t in filtered_tools]
            print(f"🛠️  最终工具选择（{len(filtered_tools)} 个工具）: {', '.join(tool_names)}")
        else:
            print("🛠️  未选择工具（全部被过滤或不可用）")

    global _last_resolved_tool_names
    _last_resolved_tool_names = [t["function"]["name"] for t in filtered_tools]

    return filtered_tools


# =============================================================================
# handle_function_call  （主要分发器）
# =============================================================================

# 其执行被 agent 循环（run_agent.py）拦截的工具
# 因为它们需要 agent 级状态（TodoStore、MemoryStore 等）。
# 注册表仍然保存它们的 schema；分发只是返回一个 stub 错误
# 以便如果有什么东西漏掉了，LLM 会看到一条合理的消息。
_AGENT_LOOP_TOOLS = {"todo", "memory", "session_search", "delegate_task"}
_READ_SEARCH_TOOLS = {"read_file", "search_files"}


# =========================================================================
# 工具参数类型强制转换
# =========================================================================

def coerce_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """将工具调用参数强制转换为匹配其 JSON Schema 类型。

    LLM 经常将数字作为字符串返回（``"42"`` 而非 ``42``），
    布尔值作为字符串（``"true"`` 而非 ``true``）。这会比较
    每个参数值与工具注册的 JSON Schema，并在值为字符串
    但 schema 期望不同类型时尝试安全强制转换。
    强制转换失败时保留原始值。

    处理 ``"type": "integer"``、``"type": "number"``、``"type": "boolean"``，
    和联合类型（``"type": ["integer", "string"]``）。
    """
    if not args or not isinstance(args, dict):
        return args

    schema = registry.get_schema(tool_name)
    if not schema:
        return args

    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        return args

    for key, value in args.items():
        if not isinstance(value, str):
            continue
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")
        if not expected:
            continue
        coerced = _coerce_value(value, expected)
        if coerced is not value:
            args[key] = coerced

    return args


def _coerce_value(value: str, expected_type):
    """尝试将字符串 *value* 强制转换为 *expected_type*。

    当强制转换不适用或失败时返回原始字符串。
    """
    if isinstance(expected_type, list):
        # 联合类型 — 按顺序尝试每个，返回第一个成功的强制转换
        for t in expected_type:
            result = _coerce_value(value, t)
            if result is not value:
                return result
        return value

    if expected_type in ("integer", "number"):
        return _coerce_number(value, integer_only=(expected_type == "integer"))
    if expected_type == "boolean":
        return _coerce_boolean(value)
    return value


def _coerce_number(value: str, integer_only: bool = False):
    """尝试将 *value* 解析为数字。失败时返回原始字符串。"""
    try:
        f = float(value)
    except (ValueError, OverflowError):
        return value
    # 在 int() 转换前防范 inf/nan
    if f != f or f == float("inf") or f == float("-inf"):
        return f
    # 如果看起来像整数（无小数部分），返回 int
    if f == int(f):
        return int(f)
    if integer_only:
        # Schema 想要整数但值有小数 — 保持为字符串
        return value
    return f


def _coerce_boolean(value: str):
    """尝试将 *value* 解析为布尔值。失败时返回原始字符串。"""
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return value


def handle_function_call(
    function_name: str,
    function_args: Dict[str, Any],
    task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_task: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
) -> str:
    """
    主要函数调用分发器，将调用路由到工具注册表。

    参数：
        function_name: 要调用的函数名称。
        function_args: 函数的参数。
        task_id: 终端/浏览器会话隔离的唯一标识符。
        user_task: 用户的原始任务（用于 browser_snapshot 上下文）。
        enabled_tools: 此会话启用的工具名称。提供时，
                       execute_code 使用此列表来确定要生成的沙箱工具。
                       回退到进程全局 ``_last_resolved_tool_names`` 以保持向后兼容。

    返回：
        函数结果的 JSON 字符串。
    """
    # 将字符串参数强制转换为其 schema 声明的类型（例如 "42"→42）
    function_args = coerce_tool_args(function_name, function_args)

    # 当非读/搜索工具运行时，通知读循环跟踪器，
    # 以便*连续*计数器重置（其他工作后的读操作是正常的）。
    if function_name not in _READ_SEARCH_TOOLS:
        try:
            from tools.file_tools import notify_other_tool_call
            notify_other_tool_call(task_id or "default")
        except Exception:
            pass  # file_tools 可能尚未加载

    try:
        if function_name in _AGENT_LOOP_TOOLS:
            return json.dumps({"error": f"{function_name} 必须由 agent 循环处理"})

        try:
            from kclaw_cli.plugins import invoke_hook
            invoke_hook(
                "pre_tool_call",
                tool_name=function_name,
                args=function_args,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
            )
        except Exception:
            pass

        if function_name == "execute_code":
            # 优先使用调用者提供的列表，以防子代理通过
            # 进程全局变量覆盖父级的工具集。
            sandbox_enabled = enabled_tools if enabled_tools is not None else _last_resolved_tool_names
            result = registry.dispatch(
                function_name, function_args,
                task_id=task_id,
                enabled_tools=sandbox_enabled,
            )
        else:
            result = registry.dispatch(
                function_name, function_args,
                task_id=task_id,
                user_task=user_task,
            )

        try:
            from kclaw_cli.plugins import invoke_hook
            invoke_hook(
                "post_tool_call",
                tool_name=function_name,
                args=function_args,
                result=result,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
            )
        except Exception:
            pass

        return result

    except Exception as e:
        error_msg = f"执行 {function_name} 时出错: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg}, ensure_ascii=False)


# =============================================================================
# 向后兼容包装函数
# =============================================================================

def get_all_tool_names() -> List[str]:
    """返回所有已注册的工具名称。"""
    return registry.get_all_tool_names()


def get_toolset_for_tool(tool_name: str) -> Optional[str]:
    """返回工具所属的工具集。"""
    return registry.get_toolset_for_tool(tool_name)


def get_available_toolsets() -> Dict[str, dict]:
    """返回用于 UI 显示的工具集可用性信息。"""
    return registry.get_available_toolsets()


def check_toolset_requirements() -> Dict[str, bool]:
    """返回每个已注册工具集的 {toolset: available_bool}。"""
    return registry.check_toolset_requirements()


def check_tool_availability(quiet: bool = False) -> Tuple[List[str], List[dict]]:
    """返回 (available_toolsets, unavailable_info)。"""
    return registry.check_tool_availability(quiet=quiet)
