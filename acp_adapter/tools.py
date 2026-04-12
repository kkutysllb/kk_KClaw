"""ACP 工具调用辅助函数 — 将 kclaw 工具映射到 ACP ToolKind 并构建内容。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import acp
from acp.schema import (
    ToolCallLocation,
    ToolCallStart,
    ToolCallProgress,
    ToolKind,
)

# ---------------------------------------------------------------------------
# kclaw 工具名称 -> ACP ToolKind 映射
# ---------------------------------------------------------------------------

TOOL_KIND_MAP: Dict[str, ToolKind] = {
    # 文件操作
    "read_file": "read",
    "write_file": "edit",
    "patch": "edit",
    "search_files": "search",
    # 终端 / 执行
    "terminal": "execute",
    "process": "execute",
    "execute_code": "execute",
    # Web / 抓取
    "web_search": "fetch",
    "web_extract": "fetch",
    # 浏览器
    "browser_navigate": "fetch",
    "browser_click": "execute",
    "browser_type": "execute",
    "browser_snapshot": "read",
    "browser_vision": "read",
    "browser_scroll": "execute",
    "browser_press": "execute",
    "browser_back": "execute",
    "browser_get_images": "read",
    # Agent 内部
    "delegate_task": "execute",
    "vision_analyze": "read",
    "image_generate": "execute",
    "text_to_speech": "execute",
    # 思考 / 元数据
    "_thinking": "think",
}


def get_tool_kind(tool_name: str) -> ToolKind:
    """返回 kclaw 工具对应的 ACP ToolKind，默认为 'other'。"""
    return TOOL_KIND_MAP.get(tool_name, "other")


def make_tool_call_id() -> str:
    """生成唯一的工具调用 ID。"""
    return f"tc-{uuid.uuid4().hex[:12]}"


def build_tool_title(tool_name: str, args: Dict[str, Any]) -> str:
    """构建工具调用的可读标题。"""
    if tool_name == "terminal":
        cmd = args.get("command", "")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"terminal: {cmd}"
    if tool_name == "read_file":
        return f"read: {args.get('path', '?')}"
    if tool_name == "write_file":
        return f"write: {args.get('path', '?')}"
    if tool_name == "patch":
        mode = args.get("mode", "replace")
        path = args.get("path", "?")
        return f"patch ({mode}): {path}"
    if tool_name == "search_files":
        return f"search: {args.get('pattern', '?')}"
    if tool_name == "web_search":
        return f"web search: {args.get('query', '?')}"
    if tool_name == "web_extract":
        urls = args.get("urls", [])
        if urls:
            return f"extract: {urls[0]}" + (f" (+{len(urls)-1})" if len(urls) > 1 else "")
        return "web extract"
    if tool_name == "delegate_task":
        goal = args.get("goal", "")
        if goal and len(goal) > 60:
            goal = goal[:57] + "..."
        return f"delegate: {goal}" if goal else "delegate task"
    if tool_name == "execute_code":
        return "execute code"
    if tool_name == "vision_analyze":
        return f"analyze image: {args.get('question', '?')[:50]}"
    return tool_name


# ---------------------------------------------------------------------------
# 构建 ACP 工具调用事件的内容对象
# ---------------------------------------------------------------------------


def build_tool_start(
    tool_call_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> ToolCallStart:
    """为给定的 kclaw 工具调用创建 ToolCallStart 事件。"""
    kind = get_tool_kind(tool_name)
    title = build_tool_title(tool_name, arguments)
    locations = extract_locations(arguments)

    if tool_name == "patch":
        mode = arguments.get("mode", "replace")
        if mode == "replace":
            path = arguments.get("path", "")
            old = arguments.get("old_string", "")
            new = arguments.get("new_string", "")
            content = [acp.tool_diff_content(path=path, new_text=new, old_text=old)]
        else:
            # Patch 模式 — 将补丁内容显示为文本
            patch_text = arguments.get("patch", "")
            content = [acp.tool_content(acp.text_block(patch_text))]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
            raw_input=arguments,
        )

    if tool_name == "write_file":
        path = arguments.get("path", "")
        file_content = arguments.get("content", "")
        content = [acp.tool_diff_content(path=path, new_text=file_content)]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
            raw_input=arguments,
        )

    if tool_name == "terminal":
        command = arguments.get("command", "")
        content = [acp.tool_content(acp.text_block(f"$ {command}"))]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
            raw_input=arguments,
        )

    if tool_name == "read_file":
        path = arguments.get("path", "")
        content = [acp.tool_content(acp.text_block(f"读取 {path}"))]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
            raw_input=arguments,
        )

    if tool_name == "search_files":
        pattern = arguments.get("pattern", "")
        target = arguments.get("target", "content")
        content = [acp.tool_content(acp.text_block(f"搜索 '{pattern}' ({target})"))]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
            raw_input=arguments,
        )

    # 通用回退
    import json
    try:
        args_text = json.dumps(arguments, indent=2, default=str)
    except (TypeError, ValueError):
        args_text = str(arguments)
    content = [acp.tool_content(acp.text_block(args_text))]
    return acp.start_tool_call(
        tool_call_id, title, kind=kind, content=content, locations=locations,
        raw_input=arguments,
    )


def build_tool_complete(
    tool_call_id: str,
    tool_name: str,
    result: Optional[str] = None,
) -> ToolCallProgress:
    """为已完成的工具调用创建 ToolCallUpdate（进度）事件。"""
    kind = get_tool_kind(tool_name)

    # 截断过长的结果用于界面显示
    display_result = result or ""
    if len(display_result) > 5000:
        display_result = display_result[:4900] + f"\n... (共 {len(result)} 字符，已截断)"

    content = [acp.tool_content(acp.text_block(display_result))]
    return acp.update_tool_call(
        tool_call_id,
        kind=kind,
        status="completed",
        content=content,
        raw_output=result,
    )


# ---------------------------------------------------------------------------
# 位置提取
# ---------------------------------------------------------------------------


def extract_locations(
    arguments: Dict[str, Any],
) -> List[ToolCallLocation]:
    """从工具参数中提取文件系统位置。"""
    locations: List[ToolCallLocation] = []
    path = arguments.get("path")
    if path:
        line = arguments.get("offset") or arguments.get("line")
        locations.append(ToolCallLocation(path=path, line=line))
    return locations
