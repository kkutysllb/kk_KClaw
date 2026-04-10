#!/usr/bin/env python3
"""
待办事项工具模块 - 规划与任务管理

提供一个内存任务列表，供代理用于分解复杂任务、
跟踪进度并在长时间对话中保持专注。状态保存在
AIAgent 实例上（每个会话一个），并在上下文
压缩事件后重新注入对话。

设计：
- 单一 `todo` 工具：提供 `todos` 参数为写入，省略为读取
- 每次调用返回完整的当前列表
- 不修改系统提示，不修改工具响应
- 行为指导完全存在于工具 schema 描述中
"""

import json
from typing import Dict, Any, List, Optional


# 待办事项的有效状态值
VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


class TodoStore:
    """
    内存中的待办列表。每个 AIAgent 一个实例（每个会话一个）。

    项目是有序的——列表位置即优先级。每个项目包含：
      - id: 唯一字符串标识符（由代理选择）
      - content: 任务描述
      - status: pending | in_progress | completed | cancelled
    """

    def __init__(self):
        self._items: List[Dict[str, str]] = []

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        """
        写入待办事项。写入后返回完整的当前列表。

        参数：
            todos: {id, content, status} 字典的列表
            merge: 如果为 False，替换整个列表。如果为 True，
                   按 id 更新现有项目并追加新项目。
        """
        if not merge:
            # 替换模式：完全新列表
            self._items = [self._validate(t) for t in todos]
        else:
            # 合并模式：按 id 更新现有项目，追加新项目
            existing = {item["id"]: item for item in self._items}
            for t in todos:
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue  # 无法合并（没有 id）

                if item_id in existing:
                    # 只更新 LLM 实际提供的字段
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = str(t["content"]).strip()
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    # 新项目——完全验证并追加到末尾
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            # 重建 _items，为现有项目保持顺序
            seen = set()
            rebuilt = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        return self.read()

    def read(self) -> List[Dict[str, str]]:
        """返回当前列表的副本。"""
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        """检查列表中是否有项目。"""
        return bool(self._items)

    def format_for_injection(self) -> Optional[str]:
        """
        渲染待办列表以供压缩后注入。

        返回一个人类可读的字符串，追加到压缩后的
        消息历史中，如果列表为空则返回 None。
        """
        if not self._items:
            return None

        # 用于紧凑显示的状态标记
        markers = {
            "completed": "[x]",
            "in_progress": "[>]",
            "pending": "[ ]",
            "cancelled": "[~]",
        }

        # 只注入 pending/in_progress 项目——completed/cancelled 项目
        # 会导致模型在压缩后重新执行已完成的工作。
        active_items = [
            item for item in self._items
            if item["status"] in ("pending", "in_progress")
        ]
        if not active_items:
            return None

        lines = ["[Your active task list was preserved across context compression]"]
        for item in active_items:
            marker = markers.get(item["status"], "[?]")
            lines.append(f"- {marker} {item['id']}. {item['content']} ({item['status']})")

        return "\n".join(lines)

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        """
        验证并规范化待办事项项目。

        确保必填字段存在且状态有效。
        返回只包含 {id, content, status} 的干净字典。
        """
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            item_id = "?"

        content = str(item.get("content", "")).strip()
        if not content:
            content = "(no description)"

        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"

        return {"id": item_id, "content": content, "status": status}


def todo_tool(
    todos: Optional[List[Dict[str, Any]]] = None,
    merge: bool = False,
    store: Optional[TodoStore] = None,
) -> str:
    """
    待办事项工具的单一入口点。根据参数读取或写入。

    参数：
        todos: 如果提供，写入这些项目。如果为 None，读取当前列表。
        merge: 如果为 True，按 id 更新。如果为 False（默认），替换整个列表。
        store: 来自 AIAgent 的 TodoStore 实例。

    返回：
        包含完整当前列表和摘要元数据的 JSON 字符串。
    """
    if store is None:
        return tool_error("TodoStore not initialized")

    if todos is not None:
        items = store.write(todos, merge)
    else:
        items = store.read()

    # 构建摘要计数
    pending = sum(1 for i in items if i["status"] == "pending")
    in_progress = sum(1 for i in items if i["status"] == "in_progress")
    completed = sum(1 for i in items if i["status"] == "completed")
    cancelled = sum(1 for i in items if i["status"] == "cancelled")

    return json.dumps({
        "todos": items,
        "summary": {
            "total": len(items),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "cancelled": cancelled,
        },
    }, ensure_ascii=False)


def check_todo_requirements() -> bool:
    """待办事项工具没有外部依赖——始终可用。"""
    return True


# =============================================================================
# OpenAI 函数调用 Schema
# =============================================================================
# 行为指导被烘焙到描述中，因此它是静态工具 schema 的一部分
# （缓存的，会话中从不改变）。

TODO_SCHEMA = {
    "name": "todo",
    "description": (
        "Manage your task list for the current session. Use for complex tasks "
        "with 3+ steps or when the user provides multiple tasks. "
        "Call with no parameters to read the current list.\n\n"
        "Writing:\n"
        "- Provide 'todos' array to create/update items\n"
        "- merge=false (default): replace the entire list with a fresh plan\n"
        "- merge=true: update existing items by id, add any new ones\n\n"
        "Each item: {id: string, content: string, "
        "status: pending|in_progress|completed|cancelled}\n"
        "List order is priority. Only ONE item in_progress at a time.\n"
        "Mark items completed immediately when done. If something fails, "
        "cancel it and add a revised item.\n\n"
        "Always returns the full current list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write. Omit to read current list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique item identifier"
                        },
                        "content": {
                            "type": "string",
                            "description": "Task description"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                            "description": "Current status"
                        }
                    },
                    "required": ["id", "content", "status"]
                }
            },
            "merge": {
                "type": "boolean",
                "description": (
                    "true: update existing items by id, add new ones. "
                    "false (default): replace the entire list."
                ),
                "default": False
            }
        },
        "required": []
    }
}


# --- 注册表 ---
from tools.registry import registry, tool_error

registry.register(
    name="todo",
    toolset="todo",
    schema=TODO_SCHEMA,
    handler=lambda args, **kw: todo_tool(
        todos=args.get("todos"), merge=args.get("merge", False), store=kw.get("store")),
    check_fn=check_todo_requirements,
    emoji="📋",
)
