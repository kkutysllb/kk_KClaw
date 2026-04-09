#!/usr/bin/env python3
"""
委托工具 — 子智能体架构

生成具有隔离上下文、受限工具集和自有终端会话的子 AIAgent 实例。
支持单任务和批处理（并行）模式。父进程阻塞直到所有子进程完成。

每个子进程获得：
  - 新对话（无父进程历史）
  - 自有的 task_id（自有终端会话、文件操作缓存）
  - 受限工具集（可配置，始终剥离被阻止的工具）
  - 从委托目标 + 上下文构建的专注系统提示

父进程的上下文仅看到委托调用和摘要结果，
从不看到子进程的中间工具调用或推理。
"""

import json
import logging
logger = logging.getLogger(__name__)
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional


# 子进程永远不能访问的工具
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",   # 无递归委托
    "clarify",         # 无用户交互
    "memory",          # 不写入共享 MEMORY.md
    "send_message",    # 无跨平台副作用
    "execute_code",    # 子进程应逐步推理，而不是编写脚本
])

MAX_CONCURRENT_CHILDREN = 3
MAX_DEPTH = 2  # 父进程 (0) -> 子进程 (1) -> 孙子被拒绝 (2)
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_TOOLSETS = ["terminal", "file", "web"]


def check_delegate_requirements() -> bool:
    """委托没有外部要求——始终可用。"""
    return True


def _build_child_system_prompt(
    goal: str,
    context: Optional[str] = None,
    *,
    workspace_path: Optional[str] = None,
) -> str:
    """为子智能体构建专注的系统提示。"""
    parts = [
        "你是一个专注于特定委托任务的子智能体。",
        "",
        f"你的任务：\n{goal}",
    ]
    if context and context.strip():
        parts.append(f"\n上下文：\n{context}")
    if workspace_path and str(workspace_path).strip():
        parts.append(
            "\n工作区路径：\n"
            f"{workspace_path}\n"
            "除非任务/上下文明确给出该路径，否则使用此确切路径进行本地仓库/workdir 操作。"
        )
    parts.append(
        "\n使用可用的工具完成此任务。"
        "完成后，提供清晰简洁的摘要，包括：\n"
        "- 你做了什么\n"
        "- 你发现了什么或完成了什么\n"
        "- 你创建或修改的任何文件\n"
        "- 遇到的任何问题\n\n"
        "重要的工作区规则：除非任务/上下文明确给出路径，否则不要假设仓库位于 /workspace/... 或任何其他容器风格路径。"
        "如果未提供确切的本地路径，先发现它，然后再发出 git/workdir 特定命令。\n\n"
        "要彻底但简洁——你的响应作为摘要返回给父智能体。"
    )
    return "\n".join(parts)


def _resolve_workspace_hint(parent_agent) -> Optional[str]:
    """为子提示尽力提供本地工作区提示。

    仅在我们有具体绝对目录时才注入路径。这样可以避免
    在为本地仓库任务引导子智能体时教它们虚假的容器路径，
    同时仍帮助它们避免猜测 `/workspace/...`。
    """
    candidates = [
        os.getenv("TERMINAL_CWD"),
        getattr(getattr(parent_agent, "_subdirectory_hints", None), "working_dir", None),
        getattr(parent_agent, "terminal_cwd", None),
        getattr(parent_agent, "cwd", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            text = os.path.abspath(os.path.expanduser(str(candidate)))
        except Exception:
            continue
        if os.path.isabs(text) and os.path.isdir(text):
            return text
    return None


def _strip_blocked_tools(toolsets: List[str]) -> List[str]:
    """移除仅包含被阻止工具的工具集。"""
    blocked_toolset_names = {
        "delegation", "clarify", "memory", "code_execution",
    }
    return [t for t in toolsets if t not in blocked_toolset_names]


def _build_child_progress_callback(task_index: int, parent_agent, task_count: int = 1) -> Optional[callable]:
    """构建将子智能体工具调用中继到父进程显示的回调。

    两个显示路径：
      CLI：     在父进程的委托微调器上方打印树形视图行
      网关：  批量工具名称并中继到父进程进度回调

    如果没有可用的显示机制则返回 None，在这种情况下
    子智能体运行无进度回调（与当前行为相同）。
    """
    spinner = getattr(parent_agent, '_delegate_spinner', None)
    parent_cb = getattr(parent_agent, 'tool_progress_callback', None)

    if not spinner and not parent_cb:
        return None  # 无显示 → 无回调 → 零行为更改

    # 仅在批处理模式下显示 1 开头的索引前缀（多个任务）
    prefix = f"[{task_index + 1}] " if task_count > 1 else ""

    # 网关：批量工具名称，定期刷新
    _BATCH_SIZE = 5
    _batch: List[str] = []

    def _callback(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs):
        # event_type 是以下之一："tool.started"、"tool.completed"、
        # "reasoning.available"、"_thinking"、"subagent_progress"

        # "_thinking" / 推理事件
        if event_type in ("_thinking", "reasoning.available"):
            text = preview or tool_name or ""
            if spinner:
                short = (text[:55] + "...") if len(text) > 55 else text
                try:
                    spinner.print_above(f" {prefix}├─ 💭 \"{short}\"")
                except Exception as e:
                    logger.debug("Spinner print_above 失败：%s", e)
            # 不中继 thinking 到网关（对聊天来说太吵）
            return

        # tool.completed — 此处无需显示（微调器在 started 时显示）
        if event_type == "tool.completed":
            return

        # tool.started — 显示并批量中继到父进程
        if spinner:
            short = (preview[:35] + "...") if preview and len(preview) > 35 else (preview or "")
            from agent.display import get_tool_emoji
            emoji = get_tool_emoji(tool_name or "")
            line = f" {prefix}├─ {emoji} {tool_name}"
            if short:
                line += f"  \"{short}\""
            try:
                spinner.print_above(line)
            except Exception as e:
                logger.debug("Spinner print_above 失败：%s", e)

        if parent_cb:
            _batch.append(tool_name or "")
            if len(_batch) >= _BATCH_SIZE:
                summary = ", ".join(_batch)
                try:
                    parent_cb("subagent_progress", f"🔀 {prefix}{summary}")
                except Exception as e:
                    logger.debug("父回调失败：%s", e)
                _batch.clear()

    def _flush():
        """在完成时将剩余的批量工具名称刷新到网关。"""
        if parent_cb and _batch:
            summary = ", ".join(_batch)
            try:
                parent_cb("subagent_progress", f"🔀 {prefix}{summary}")
            except Exception as e:
                logger.debug("父回调刷新失败：%s", e)
            _batch.clear()

    _callback._flush = _flush
    return _callback


def _build_child_agent(
    task_index: int,
    goal: str,
    context: Optional[str],
    toolsets: Optional[List[str]],
    model: Optional[str],
    max_iterations: int,
    parent_agent,
    # 委托配置中的凭据覆盖（provider:model 解析）
    override_provider: Optional[str] = None,
    override_base_url: Optional[str] = None,
    override_api_key: Optional[str] = None,
    override_api_mode: Optional[str] = None,
    # ACP 传输覆盖 — 允许非 ACP 父进程生成 ACP 子智能体
    override_acp_command: Optional[str] = None,
    override_acp_args: Optional[List[str]] = None,
):
    """
    在主线程上构建子 AIAgent（线程安全构造）。
    返回构建的子智能体而不运行它。

    当设置了 override_* 参数（来自委托配置）时，子进程使用
    这些凭据而不是从父进程继承。这使得
    可以将子智能体路由到不同的 provider:model 对（例如，
    父进程在 Nous Portal 上运行，而子进程在 OpenRouter 上使用廉价/快速模型）。
    """
    from run_agent import AIAgent

    # 当没有给出明确工具集时，从父进程启用的工具集继承
    # 以便被禁用的工具（例如 web）不会泄露到子智能体。
    # 注意：enabled_toolsets=None 意味着"所有工具已启用"（默认），
    # 因此我们必须从加载的工具名称派生有效的工具集。
    parent_enabled = getattr(parent_agent, "enabled_toolsets", None)
    if parent_enabled is not None:
        parent_toolsets = set(parent_enabled)
    elif parent_agent and hasattr(parent_agent, "valid_tool_names"):
        # enabled_toolsets 为 None（所有工具）— 从加载的工具名称派生
        import model_tools
        parent_toolsets = {
            ts for name in parent_agent.valid_tool_names
            if (ts := model_tools.get_toolset_for_tool(name)) is not None
        }
    else:
        parent_toolsets = set(DEFAULT_TOOLSETS)

    if toolsets:
        # 与父进程交集——子智能体不得获得父进程缺少的工具
        child_toolsets = _strip_blocked_tools([t for t in toolsets if t in parent_toolsets])
    elif parent_agent and parent_enabled is not None:
        child_toolsets = _strip_blocked_tools(parent_enabled)
    elif parent_toolsets:
        child_toolsets = _strip_blocked_tools(sorted(parent_toolsets))
    else:
        child_toolsets = _strip_blocked_tools(DEFAULT_TOOLSETS)

    workspace_hint = _resolve_workspace_hint(parent_agent)
    child_prompt = _build_child_system_prompt(goal, context, workspace_path=workspace_hint)
    # 提取父进程的 API 密钥，以便子智能体继承认证（例如 Nous Portal）。
    parent_api_key = getattr(parent_agent, "api_key", None)
    if (not parent_api_key) and hasattr(parent_agent, "_client_kwargs"):
        parent_api_key = parent_agent._client_kwargs.get("api_key")

    # 构建进度回调以将工具调用中继到父进程显示
    child_progress_cb = _build_child_progress_callback(task_index, parent_agent)

    # 每个子智能体获得自己的迭代预算，上限为 max_iterations
    #（可通过 delegation.max_iterations 配置，默认为 50）。这意味着
    # 父进程 + 子智能体的总迭代次数可能超过父进程的
    # max_iterations。用户可在 config.yaml 中控制每个子智能体的上限。

    child_thinking_cb = None
    if child_progress_cb:
        def _child_thinking(text: str) -> None:
            if not text:
                return
            try:
                child_progress_cb("_thinking", text)
            except Exception as e:
                logger.debug("子进程 thinking 回调中继失败：%s", e)

        child_thinking_cb = _child_thinking

    # 解析有效凭据：配置覆盖 > 父进程继承
    effective_model = model or parent_agent.model
    effective_provider = override_provider or getattr(parent_agent, "provider", None)
    effective_base_url = override_base_url or parent_agent.base_url
    effective_api_key = override_api_key or parent_api_key
    effective_api_mode = override_api_mode or getattr(parent_agent, "api_mode", None)
    effective_acp_command = override_acp_command or getattr(parent_agent, "acp_command", None)
    effective_acp_args = list(override_acp_args if override_acp_args is not None else (getattr(parent_agent, "acp_args", []) or []))

    child = AIAgent(
        base_url=effective_base_url,
        api_key=effective_api_key,
        model=effective_model,
        provider=effective_provider,
        api_mode=effective_api_mode,
        acp_command=effective_acp_command,
        acp_args=effective_acp_args,
        max_iterations=max_iterations,
        max_tokens=getattr(parent_agent, "max_tokens", None),
        reasoning_config=getattr(parent_agent, "reasoning_config", None),
        prefill_messages=getattr(parent_agent, "prefill_messages", None),
        enabled_toolsets=child_toolsets,
        quiet_mode=True,
        ephemeral_system_prompt=child_prompt,
        log_prefix=f"[子智能体-{task_index}]",
        platform=parent_agent.platform,
        skip_context_files=True,
        skip_memory=True,
        clarify_callback=None,
        thinking_callback=child_thinking_cb,
        session_db=getattr(parent_agent, '_session_db', None),
        parent_session_id=getattr(parent_agent, 'session_id', None),
        providers_allowed=parent_agent.providers_allowed,
        providers_ignored=parent_agent.providers_ignored,
        providers_order=parent_agent.providers_order,
        provider_sort=parent_agent.provider_sort,
        tool_progress_callback=child_progress_cb,
        iteration_budget=None,  # 每个子智能体新的预算
    )
    child._print_fn = getattr(parent_agent, '_print_fn', None)
    # 设置委托深度，以便子智能体不能生成孙智能体
    child._delegate_depth = getattr(parent_agent, '_delegate_depth', 0) + 1

    # 尽可能与子进程共享凭据池，以便子智能体
    # 可以在速率限制时轮换凭据，而不是被固定到一个密钥。
    child_pool = _resolve_child_credential_pool(effective_provider, parent_agent)
    if child_pool is not None:
        child._credential_pool = child_pool

    # 注册子进程以进行中断传播
    if hasattr(parent_agent, '_active_children'):
        lock = getattr(parent_agent, '_active_children_lock', None)
        if lock:
            with lock:
                parent_agent._active_children.append(child)
        else:
            parent_agent._active_children.append(child)

    return child

def _run_single_child(
    task_index: int,
    goal: str,
    child=None,
    parent_agent=None,
    **_kwargs,
) -> Dict[str, Any]:
    """
    运行预构建的子智能体。从线程内调用。
    返回结构化结果字典。
    """
    child_start = time.monotonic()

    # 从子智能体获取进度回调
    child_progress_cb = getattr(child, 'tool_progress_callback', None)

    # 在子智能体构造改变全局变量之前恢复父进程工具名称
    # 这是正确的父进程工具集，而不是子进程的。
    import model_tools
    _saved_tool_names = getattr(child, "_delegate_saved_tool_names",
                                list(model_tools._last_resolved_tool_names))

    child_pool = getattr(child, '_credential_pool', None)
    leased_cred_id = None
    if child_pool is not None:
        leased_cred_id = child_pool.acquire_lease()
        if leased_cred_id is not None:
            try:
                leased_entry = child_pool.current()
                if leased_entry is not None and hasattr(child, '_swap_credential'):
                    child._swap_credential(leased_entry)
            except Exception as exc:
                logger.debug("无法将子进程绑定到租用的凭据：%s", exc)

    try:
        result = child.run_conversation(user_message=goal)

        # 将任何剩余的批量进度刷新到网关
        if child_progress_cb and hasattr(child_progress_cb, '_flush'):
            try:
                child_progress_cb._flush()
            except Exception as e:
                logger.debug("进度回调刷新失败：%s", e)

        duration = round(time.monotonic() - child_start, 2)

        summary = result.get("final_response") or ""
        completed = result.get("completed", False)
        interrupted = result.get("interrupted", False)
        api_calls = result.get("api_calls", 0)

        if interrupted:
            status = "interrupted"
        elif summary:
            # 摘要意味着子智能体产生了可用的输出。
            # exit_reason（"completed" vs "max_iterations"）已经
            # 告诉父进程任务如何结束。
            status = "completed"
        else:
            status = "failed"

        # 从对话消息构建工具跟踪（已在内存中）。
        # 使用 tool_call_id 正确配对并行工具调用及其结果。
        tool_trace: list[Dict[str, Any]] = []
        trace_by_id: Dict[str, Dict[str, Any]] = {}
        messages = result.get("messages") or []
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") == "assistant":
                    for tc in (msg.get("tool_calls") or []):
                        fn = tc.get("function", {})
                        entry_t = {
                            "tool": fn.get("name", "unknown"),
                            "args_bytes": len(fn.get("arguments", "")),
                        }
                        tool_trace.append(entry_t)
                        tc_id = tc.get("id")
                        if tc_id:
                            trace_by_id[tc_id] = entry_t
                elif msg.get("role") == "tool":
                    content = msg.get("content", "")
                    is_error = bool(
                        content and "error" in content[:80].lower()
                    )
                    result_meta = {
                        "result_bytes": len(content),
                        "status": "error" if is_error else "ok",
                    }
                    # 通过 tool_call_id 配对并行调用
                    tc_id = msg.get("tool_call_id")
                    target = trace_by_id.get(tc_id) if tc_id else None
                    if target is not None:
                        target.update(result_meta)
                    elif tool_trace:
                        # 没有 tool_call_id 的消息的回退
                        tool_trace[-1].update(result_meta)

        # 确定退出原因
        if interrupted:
            exit_reason = "interrupted"
        elif completed:
            exit_reason = "completed"
        else:
            exit_reason = "max_iterations"

        # 提取令牌计数（对模拟对象安全）
        _input_tokens = getattr(child, "session_prompt_tokens", 0)
        _output_tokens = getattr(child, "session_completion_tokens", 0)
        _model = getattr(child, "model", None)

        entry: Dict[str, Any] = {
            "task_index": task_index,
            "status": status,
            "summary": summary,
            "api_calls": api_calls,
            "duration_seconds": duration,
            "model": _model if isinstance(_model, str) else None,
            "exit_reason": exit_reason,
            "tokens": {
                "input": _input_tokens if isinstance(_input_tokens, (int, float)) else 0,
                "output": _output_tokens if isinstance(_output_tokens, (int, float)) else 0,
            },
            "tool_trace": tool_trace,
        }
        if status == "failed":
            entry["error"] = result.get("error", "子智能体未产生响应。")

        return entry

    except Exception as exc:
        duration = round(time.monotonic() - child_start, 2)
        logging.exception(f"[子智能体-{task_index}] 失败")
        return {
            "task_index": task_index,
            "status": "error",
            "summary": None,
            "error": str(exc),
            "api_calls": 0,
            "duration_seconds": duration,
        }

    finally:
        if child_pool is not None and leased_cred_id is not None:
            try:
                child_pool.release_lease(leased_cred_id)
            except Exception as exc:
                logger.debug("释放凭据租约失败：%s", exc)

        # 恢复父进程的工具名称，以便进程全局变量对任何后续 execute_code 调用或其他使用者正确


def delegate_task(
    goal: Optional[str] = None,
    context: Optional[str] = None,
    toolsets: Optional[List[str]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    max_iterations: Optional[int] = None,
    acp_command: Optional[str] = None,
    acp_args: Optional[List[str]] = None,
    parent_agent=None,
) -> str:
    """
    生成一个或多个子智能体来处理委托任务。

    支持两种模式：
      - 单个：提供 goal（+ 可选 context、toolsets）
      - 批处理：提供 tasks 数组 [{goal, context, toolsets}, ...]

    返回结果数组的 JSON，每个任务一个条目。
    """
    if parent_agent is None:
        return tool_error("delegate_task 需要父智能体上下文。")

    # 深度限制
    depth = getattr(parent_agent, '_delegate_depth', 0)
    if depth >= MAX_DEPTH:
        return json.dumps({
            "error": (
                f"达到委托深度限制 ({MAX_DEPTH})。"
                "子智能体不能生成更多子智能体。"
            )
        })

    # 加载配置
    cfg = _load_config()
    default_max_iter = cfg.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    effective_max_iter = max_iterations or default_max_iter

    # 解析委托凭据（provider:model 对）。
    # 当配置了 delegation.provider 时，这会通过与 CLI/gateway 启动使用的
    # 相同运行时提供商系统解析完整凭据包（base_url、api_key、api_mode）。
    # 当未配置时，返回 None 值，以便子进程从父进程继承。
    try:
        creds = _resolve_delegation_credentials(cfg, parent_agent)
    except ValueError as exc:
        return tool_error(str(exc))

    # 规范化为任务列表
    if tasks and isinstance(tasks, list):
        task_list = tasks[:MAX_CONCURRENT_CHILDREN]
    elif goal and isinstance(goal, str) and goal.strip():
        task_list = [{"goal": goal, "context": context, "toolsets": toolsets}]
    else:
        return tool_error("提供 'goal'（单个任务）或 'tasks'（批处理）。")

    if not task_list:
        return tool_error("未提供任务。")

    # 验证每个任务有 goal
    for i, task in enumerate(task_list):
        if not task.get("goal", "").strip():
            return tool_error(f"任务 {i} 缺少 'goal'。")

    overall_start = time.monotonic()
    results = []

    n_tasks = len(task_list)
    # 跟踪任务标签以显示进度（为可读性截断）
    task_labels = [t["goal"][:40] for t in task_list]

    # 在任何子智能体构造改变全局变量之前保存父进程工具名称。
    # _build_child_agent() 调用 AIAgent()，后者调用 get_tool_definitions()，
    # 后者用子智能体的工具集覆盖 model_tools._last_resolved_tool_names。
    import model_tools as _model_tools
    _parent_tool_names = list(_model_tools._last_resolved_tool_names)

    # 在主线程上构建所有子智能体（线程安全构造）
    # 包装在 try/finally 中，以便即使子智能体构建引发异常，全局变量也始终被恢复。
    children = []
    try:
        for i, t in enumerate(task_list):
            child = _build_child_agent(
                task_index=i, goal=t["goal"], context=t.get("context"),
                toolsets=t.get("toolsets") or toolsets, model=creds["model"],
                max_iterations=effective_max_iter, parent_agent=parent_agent,
                override_provider=creds["provider"], override_base_url=creds["base_url"],
                override_api_key=creds["api_key"],
                override_api_mode=creds["api_mode"],
                override_acp_command=t.get("acp_command") or acp_command,
                override_acp_args=t.get("acp_args") or acp_args,
            )
            # 使用正确的父进程工具名称覆盖（子智能体构造改变全局变量之前）
            child._delegate_saved_tool_names = _parent_tool_names
            children.append((i, t, child))
    finally:
        # 权威恢复：构建所有子智能体后将全局变量重置为父进程的工具名称
        _model_tools._last_resolved_tool_names = _parent_tool_names

    if n_tasks == 1:
        # 单个任务——直接运行（无线程池开销）
        _i, _t, child = children[0]
        result = _run_single_child(0, _t["goal"], child, parent_agent)
        results.append(result)
    else:
        # 批处理——并行运行，每个任务有进度行
        completed_count = 0
        spinner_ref = getattr(parent_agent, '_delegate_spinner', None)

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHILDREN) as executor:
            futures = {}
            for i, t, child in children:
                future = executor.submit(
                    _run_single_child,
                    task_index=i,
                    goal=t["goal"],
                    child=child,
                    parent_agent=parent_agent,
                )
                futures[future] = i

            for future in as_completed(futures):
                try:
                    entry = future.result()
                except Exception as exc:
                    idx = futures[future]
                    entry = {
                        "task_index": idx,
                        "status": "error",
                        "summary": None,
                        "error": str(exc),
                        "api_calls": 0,
                        "duration_seconds": 0,
                    }
                results.append(entry)
                completed_count += 1

                # 在微调器上方打印每个任务完成行
                idx = entry["task_index"]
                label = task_labels[idx] if idx < len(task_labels) else f"任务 {idx}"
                dur = entry.get("duration_seconds", 0)
                status = entry.get("status", "?")
                icon = "✓" if status == "completed" else "✗"
                remaining = n_tasks - completed_count
                completion_line = f"{icon} [{idx+1}/{n_tasks}] {label}  ({dur}s)"
                if spinner_ref:
                    try:
                        spinner_ref.print_above(completion_line)
                    except Exception:
                        print(f"  {completion_line}")
                else:
                    print(f"  {completion_line}")

                # 更新微调器文本以显示剩余数量
                if spinner_ref and remaining > 0:
                    try:
                        spinner_ref.update_text(f"🔀 {remaining} 个任务剩余")
                    except Exception as e:
                        logger.debug("微调器 update_text 失败：%s", e)

        # 按 task_index 排序，以便结果与输入顺序匹配
        results.sort(key=lambda r: r["task_index"])

    # 通知父进程的内存提供者委托结果
    if parent_agent and hasattr(parent_agent, '_memory_manager') and parent_agent._memory_manager:
        for entry in results:
            try:
                _task_goal = task_list[entry["task_index"]]["goal"] if entry["task_index"] < len(task_list) else ""
                parent_agent._memory_manager.on_delegation(
                    task=_task_goal,
                    result=entry.get("summary", "") or "",
                    child_session_id=getattr(children[entry["task_index"]][2], "session_id", "") if entry["task_index"] < len(children) else "",
                )
            except Exception:
                pass

    total_duration = round(time.monotonic() - overall_start, 2)

    return json.dumps({
        "results": results,
        "total_duration_seconds": total_duration,
    }, ensure_ascii=False)


def _resolve_child_credential_pool(effective_provider: Optional[str], parent_agent):
    """为子智能体解析凭据池。

    规则：
    1. 与父进程相同 provider -> 共享父进程的池，以便冷却状态
       和轮换保持同步。
    2. 不同 provider -> 尝试加载该 provider 自己的池。
    3. 没有可用池 -> 返回 None，让子进程保持继承的
       固定凭据行为。
    """
    if not effective_provider:
        return getattr(parent_agent, "_credential_pool", None)

    parent_provider = getattr(parent_agent, "provider", None) or ""
    parent_pool = getattr(parent_agent, "_credential_pool", None)
    if parent_pool is not None and effective_provider == parent_provider:
        return parent_pool

    try:
        from agent.credential_pool import load_pool
        pool = load_pool(effective_provider)
        if pool is not None and pool.has_credentials():
            return pool
    except Exception as exc:
        logger.debug(
            "无法为子进程 provider '%s' 加载凭据池：%s",
            effective_provider,
            exc,
        )
    return None


def _resolve_delegation_credentials(cfg: dict, parent_agent) -> dict:
    """为子智能体委托解析凭据。

    如果配置了 ``delegation.base_url``，子智能体使用该直接
    OpenAI 兼容端点。否则，如果配置了 ``delegation.provider``，
    完整凭据包（base_url、api_key、api_mode、provider）
    通过运行时提供商系统解析——与 CLI/gateway 启动使用的
    相同路径。这允许子智能体在完全不同的
    provider:model 对上运行。

    如果 base_url 和 provider 都未配置，返回 None 值，以便
    子进程从父智能体继承一切。

    在凭据失败时抛出带用户友好消息的 ValueError。
    """
    configured_model = str(cfg.get("model") or "").strip() or None
    configured_provider = str(cfg.get("provider") or "").strip() or None
    configured_base_url = str(cfg.get("base_url") or "").strip() or None
    configured_api_key = str(cfg.get("api_key") or "").strip() or None

    if configured_base_url:
        api_key = (
            configured_api_key
            or os.getenv("OPENAI_API_KEY", "").strip()
        )
        if not api_key:
            raise ValueError(
                "配置了委托 base_url但未找到 API 密钥。"
                "设置 delegation.api_key 或 OPENAI_API_KEY。"
            )

        base_lower = configured_base_url.lower()
        provider = "custom"
        api_mode = "chat_completions"
        if "chatgpt.com/backend-api/codex" in base_lower:
            provider = "openai-codex"
            api_mode = "codex_responses"
        elif "api.anthropic.com" in base_lower:
            provider = "anthropic"
            api_mode = "anthropic_messages"

        return {
            "model": configured_model,
            "provider": provider,
            "base_url": configured_base_url,
            "api_key": api_key,
            "api_mode": api_mode,
        }

    if not configured_provider:
        # 无 provider 覆盖 — 子进程从父进程继承一切
        return {
            "model": configured_model,
            "provider": None,
            "base_url": None,
            "api_key": None,
            "api_mode": None,
        }

    # 配置了 provider — 解析完整凭据
    try:
        from kclaw_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider(requested=configured_provider)
    except Exception as exc:
        raise ValueError(
            f"无法解析委托 provider '{configured_provider}'：{exc}。"
            f"检查 provider 是否配置（设置了 API 密钥、有效 provider 名称），"
            f"或为直接端点设置 delegation.base_url/delegation.api_key。"
            f"可用 provider：openrouter、nous、zai、kimi-coding、minimax。"
        ) from exc

    api_key = runtime.get("api_key", "")
    if not api_key:
        raise ValueError(
            f"委托 provider '{configured_provider}' 已解析但没有 API 密钥。"
            f"设置适当的环境变量或运行 'kclaw auth'。"
        )

    return {
        "model": configured_model,
        "provider": runtime.get("provider"),
        "base_url": runtime.get("base_url"),
        "api_key": api_key,
        "api_mode": runtime.get("api_mode"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
    }


def _load_config() -> dict:
    """从 CLI_CONFIG 或持久配置加载委托配置。

    首先检查运行时配置（cli.py CLI_CONFIG），然后回退
    到持久配置（kclaw_cli/config.py load_config()），以便
    无论入口点如何（CLI、gateway、cron）都能获取
    ``delegation.model`` / ``delegation.provider``。
    """
    try:
        from cli import CLI_CONFIG
        cfg = CLI_CONFIG.get("delegation", {})
        if cfg:
            return cfg
    except Exception:
        pass
    try:
        from kclaw_cli.config import load_config
        full = load_config()
        return full.get("delegation", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# OpenAI 函数调用 Schema
# ---------------------------------------------------------------------------

DELEGATE_TASK_SCHEMA = {
    "name": "delegate_task",
    "description": (
        "生成一个或多个子智能体在隔离上下文中处理任务。"
        "每个子智能体获得自己的对话、终端会话和工具集。"
        "仅返回最终摘要 — 中间工具结果"
        "永远不会进入你的上下文窗口。\n\n"
        "两种模式（需要 'goal' 或 'tasks' 之一）：\n"
        "1. 单个任务：提供 'goal'（+ 可选 context、toolsets）\n"
        "2. 批处理（并行）：提供 'tasks' 数组，最多 3 项。"
        "全部并发运行，结果一起返回。\n\n"
        "何时使用 delegate_task：\n"
        "- 推理密集型子任务（调试、代码审查、研究综合）\n"
        "- 会用中间数据淹没你上下文的任务\n"
        "- 并行独立工作流（同时研究 A 和 B）\n\n"
        "何时不使用（用这些代替）：\n"
        "- 无需推理的机械多步工作 -> 使用 execute_code\n"
        "- 单个工具调用 -> 直接调用工具\n"
        "- 需要用户交互的任务 -> 子智能体不能使用 clarify\n\n"
        "重要：\n"
        "- 子智能体没有你对话的记忆。通过 'context' 字段传递所有相关信息"
        "（文件路径、错误消息、约束）。\n"
        "- 子智能体不能调用：delegate_task、clarify、memory、send_message、"
        "execute_code。\n"
        "- 每个子智能体获得自己的终端会话（独立的工作目录和状态）。\n"
        "- 结果始终作为数组返回，每个任务一个条目。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "子智能体应该完成什么。要具体且自包含 — "
                    "子智能体不知道你的对话历史。"
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "子智能体需要的背景信息：文件路径、"
                    "错误消息、项目结构、约束。你越具体，"
                    "子智能体表现越好。"
                ),
            },
            "toolsets": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "为这个子智能体启用的工具集。"
                    "默认：继承你启用的工具集。"
                    "常见模式：['terminal', 'file'] 用于代码工作，"
                    "['web'] 用于研究，['terminal', 'file', 'web'] 用于"
                    "全栈任务。"
                ),
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "任务目标"},
                        "context": {"type": "string", "description": "任务特定上下文"},
                        "toolsets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "此特定任务的工具集。使用 'web' 进行网络访问，'terminal' 进行 shell。",
                        },
                        "acp_command": {
                            "type": "string",
                            "description": "每个任务的 ACP 命令覆盖（例如 'claude'）。仅覆盖此任务的顶级 acp_command。",
                        },
                        "acp_args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ACP 命令参数覆盖。",
                        },
                    },
                    "required": ["goal"],
                },
                "maxItems": 3,
                "description": (
                    "批处理模式：最多 3 个任务并行运行。每个获得"
                    "自己的子智能体，具有隔离的上下文和终端会话。"
                    "提供时，顶级 goal/context/toolsets 被忽略。"
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    "每个子智能体的最大工具调用回合数（默认：50）。"
                    "仅对简单任务设置较低值。"
                ),
            },
            "acp_command": {
                "type": "string",
                "description": (
                    "覆盖子智能体的 ACP 命令（例如 'claude'、'copilot'）。"
                    "设置时，子进程使用 ACP 子进程传输而不是继承"
                    "父进程的传输。允许从任何父进程（包括 Discord/Telegram/CLI）"
                    "生成 Claude Code（claude --acp --stdio）或其他支持 ACP 的智能体。"
                ),
            },
            "acp_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "ACP 命令的参数（默认：['--acp', '--stdio']）。"
                    "仅在设置 acp_command 时使用。示例：['--acp', '--stdio', '--model', 'claude-opus-4-6']"
                ),
            },
        },
        "required": [],
    },
}


# --- 注册 ---
from tools.registry import registry, tool_error

registry.register(
    name="delegate_task",
    toolset="delegation",
    schema=DELEGATE_TASK_SCHEMA,
    handler=lambda args, **kw: delegate_task(
        goal=args.get("goal"),
        context=args.get("context"),
        toolsets=args.get("toolsets"),
        tasks=args.get("tasks"),
        max_iterations=args.get("max_iterations"),
        acp_command=args.get("acp_command"),
        acp_args=args.get("acp_args"),
        parent_agent=kw.get("parent_agent")),
    check_fn=check_delegate_requirements,
    emoji="🔀",
)
