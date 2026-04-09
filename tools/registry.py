"""所有 kclaw 工具的中央注册表。

每个工具文件在模块级别调用 ``registry.register()`` 来声明其
schema、处理器、工具集成员资格和可用性检查。``model_tools.py``
查询注册表而不是维护自己的并行数据结构。

导入链（循环导入安全）：
    tools/registry.py  （不从 model_tools 或工具文件导入）
           ^
    tools/*.py  （在模块级别从 tools.registry 导入）
           ^
    model_tools.py  （导入 tools.registry + 所有工具模块）
           ^
    run_agent.py, cli.py, batch_runner.py 等
"""

import json
import logging
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ToolEntry:
    """单个注册工具的元数据。"""

    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars",
    )

    def __init__(self, name, toolset, schema, handler, check_fn,
                 requires_env, is_async, description, emoji,
                 max_result_size_chars=None):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env
        self.is_async = is_async
        self.description = description
        self.emoji = emoji
        self.max_result_size_chars = max_result_size_chars


class ToolRegistry:
    """从工具文件收集工具 schema + 处理器的单例注册表。"""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}
        self._toolset_checks: Dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Callable = None,
        requires_env: list = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        max_result_size_chars: int | float | None = None,
    ):
        """注册一个工具。由每个工具文件在模块导入时调用。"""
        existing = self._tools.get(name)
        if existing and existing.toolset != toolset:
            logger.warning(
                "工具名称冲突：'%s'（工具集 '%s'）正被工具集 '%s' 覆盖",
                name, existing.toolset, toolset,
            )
        self._tools[name] = ToolEntry(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            requires_env=requires_env or [],
            is_async=is_async,
            description=description or schema.get("description", ""),
            emoji=emoji,
            max_result_size_chars=max_result_size_chars,
        )
        if check_fn and toolset not in self._toolset_checks:
            self._toolset_checks[toolset] = check_fn

    def deregister(self, name: str) -> None:
        """从注册表中移除一个工具。

        同时清理工具集检查，如果该工具集中没有其他工具的话。
        用于 MCP 动态工具发现，以便在服务器发送
        ``notifications/tools/list_changed`` 时进行彻底替换。
        """
        entry = self._tools.pop(name, None)
        if entry is None:
            return
        # 如果这是该工具集中的最后一个工具，则删除工具集检查
        if entry.toolset in self._toolset_checks and not any(
            e.toolset == entry.toolset for e in self._tools.values()
        ):
            self._toolset_checks.pop(entry.toolset, None)
        logger.debug("已注销工具：%s", name)

    # ------------------------------------------------------------------
    # Schema 检索
    # ------------------------------------------------------------------

    def get_definitions(self, tool_names: Set[str], quiet: bool = False) -> List[dict]:
        """返回请求工具名称的 OpenAI 格式工具 schema。

        仅包含其 ``check_fn()`` 返回 True（或没有 check_fn）的工具。
        """
        result = []
        check_results: Dict[Callable, bool] = {}
        for name in sorted(tool_names):
            entry = self._tools.get(name)
            if not entry:
                continue
            if entry.check_fn:
                if entry.check_fn not in check_results:
                    try:
                        check_results[entry.check_fn] = bool(entry.check_fn())
                    except Exception:
                        check_results[entry.check_fn] = False
                        if not quiet:
                            logger.debug("工具 %s 检查引发异常；跳过", name)
                if not check_results[entry.check_fn]:
                    if not quiet:
                        logger.debug("工具 %s 不可用（检查失败）", name)
                    continue
            # 确保 schema 始终有 "name" 字段——使用 entry.name 作为后备
            schema_with_name = {**entry.schema, "name": entry.name}
            result.append({"type": "function", "function": schema_with_name})
        return result

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """按名称执行工具处理器。

        * 异步处理器通过 ``_run_async()`` 自动桥接。
        * 所有异常都被捕获并作为 ``{"error": "..."}`` 返回
          以保持一致的错误格式。
        """
        entry = self._tools.get(name)
        if not entry:
            return json.dumps({"error": f"未知工具：{name}"})
        try:
            if entry.is_async:
                from model_tools import _run_async
                return _run_async(entry.handler(args, **kwargs))
            return entry.handler(args, **kwargs)
        except Exception as e:
            logger.exception("工具 %s 分发错误：%s", name, e)
            return json.dumps({"error": f"工具执行失败：{type(e).__name__}: {e}"})

    # ------------------------------------------------------------------
    # 查询辅助函数  （替换 model_tools.py 中的冗余字典）
    # ------------------------------------------------------------------

    def get_max_result_size(self, name: str, default: int | float | None = None) -> int | float:
        """返回每个工具的最大结果大小，或 *default*（或全局默认值）。"""
        entry = self._tools.get(name)
        if entry and entry.max_result_size_chars is not None:
            return entry.max_result_size_chars
        if default is not None:
            return default
        from tools.budget_config import DEFAULT_RESULT_SIZE_CHARS
        return DEFAULT_RESULT_SIZE_CHARS

    def get_all_tool_names(self) -> List[str]:
        """返回所有已注册工具名称的排序列表。"""
        return sorted(self._tools.keys())

    def get_schema(self, name: str) -> Optional[dict]:
        """返回工具的原始 schema 字典，绕过 check_fn 过滤。

        对于令牌估算和内省很有用，可用性不重要——只有 schema 内容重要。
        """
        entry = self._tools.get(name)
        return entry.schema if entry else None

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        """返回工具所属的工具集，或 None。"""
        entry = self._tools.get(name)
        return entry.toolset if entry else None

    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """返回工具的表情符号，如果未设置则返回 *default*。"""
        entry = self._tools.get(name)
        return (entry.emoji if entry and entry.emoji else default)

    def get_tool_to_toolset_map(self) -> Dict[str, str]:
        """返回每个已注册工具的 ``{tool_name: toolset_name}``。"""
        return {name: e.toolset for name, e in self._tools.items()}

    def is_toolset_available(self, toolset: str) -> bool:
        """检查工具集的需求是否满足。

        当检查函数引发意外异常时返回 False（而不是崩溃），
        例如网络错误、缺少导入、配置错误。
        """
        check = self._toolset_checks.get(toolset)
        if not check:
            return True
        try:
            return bool(check())
        except Exception:
            logger.debug("工具集 %s 检查引发异常；标记为不可用", toolset)
            return False

    def check_toolset_requirements(self) -> Dict[str, bool]:
        """返回每个工具集的 ``{toolset: available_bool}``。"""
        toolsets = set(e.toolset for e in self._tools.values())
        return {ts: self.is_toolset_available(ts) for ts in sorted(toolsets)}

    def get_available_toolsets(self) -> Dict[str, dict]:
        """返回用于 UI 显示的工具集元数据。"""
        toolsets: Dict[str, dict] = {}
        for entry in self._tools.values():
            ts = entry.toolset
            if ts not in toolsets:
                toolsets[ts] = {
                    "available": self.is_toolset_available(ts),
                    "tools": [],
                    "description": "",
                    "requirements": [],
                }
            toolsets[ts]["tools"].append(entry.name)
            if entry.requires_env:
                for env in entry.requires_env:
                    if env not in toolsets[ts]["requirements"]:
                        toolsets[ts]["requirements"].append(env)
        return toolsets

    def get_toolset_requirements(self) -> Dict[str, dict]:
        """构建向后兼容的 TOOLSET_REQUIREMENTS 兼容字典。"""
        result: Dict[str, dict] = {}
        for entry in self._tools.values():
            ts = entry.toolset
            if ts not in result:
                result[ts] = {
                    "name": ts,
                    "env_vars": [],
                    "check_fn": self._toolset_checks.get(ts),
                    "setup_url": None,
                    "tools": [],
                }
            if entry.name not in result[ts]["tools"]:
                result[ts]["tools"].append(entry.name)
            for env in entry.requires_env:
                if env not in result[ts]["env_vars"]:
                    result[ts]["env_vars"].append(env)
        return result

    def check_tool_availability(self, quiet: bool = False):
        """返回（available_toolsets, unavailable_info），格式同旧函数。"""
        available = []
        unavailable = []
        seen = set()
        for entry in self._tools.values():
            ts = entry.toolset
            if ts in seen:
                continue
            seen.add(ts)
            if self.is_toolset_available(ts):
                available.append(ts)
            else:
                unavailable.append({
                    "name": ts,
                    "env_vars": entry.requires_env,
                    "tools": [e.name for e in self._tools.values() if e.toolset == ts],
                })
        return available, unavailable


# 模块级单例
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# 工具响应序列化的辅助函数
# ---------------------------------------------------------------------------
# 每个工具处理器必须返回 JSON 字符串。这些辅助函数消除了
# 在工具文件中出现数百次的样板代码
# ``json.dumps({"error": msg}, ensure_ascii=False)``。
#
# 用法：
#   from tools.registry import registry, tool_error, tool_result
#
#   return tool_error("出错了")
#   return tool_error("未找到", code=404)
#   return tool_result(success=True, data=payload)
#   return tool_result(items)            # 直接传递字典


def tool_error(message, **extra) -> str:
    """返回工具处理器的 JSON 错误字符串。

    >>> tool_error("文件未找到")
    '{"error": "文件未找到"}'
    >>> tool_error("输入错误", success=False)
    '{"error": "输入错误", "success": false}'
    """
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """返回工具处理器的 JSON 结果字符串。

    接受字典位置参数*或*关键字参数（不能同时两者）：

    >>> tool_result(success=True, count=42)
    '{"success": true, "count": 42}'
    >>> tool_result({"key": "value"})
    '{"key": "value"}'
    """
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
