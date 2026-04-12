"""
事件钩子系统

一个轻量级的事件驱动系统，在关键生命周期点触发处理程序。
钩子从 ~/.kclaw/hooks/ 目录发现，每个包含：
  - HOOK.yaml（元数据：name、description、events 列表）
  - handler.py（带 async def handle(event_type, context) 的 Python 处理程序）

事件：
  - gateway:startup     -- 网关进程启动
  - session:start       -- 新会话创建（首个新会话消息）
  - session:end         -- 会话结束（用户运行了 /new 或 /reset）
  - session:reset       -- 会话重置完成（创建了新的会话条目）
  - agent:start         -- 代理开始处理消息
  - agent:step          -- 工具调用循环中的每个回合
  - agent:end           -- 代理完成处理
  - command:*           -- 执行的任何斜杠命令（通配符匹配）

钩子中的错误被捕获并记录，但从不阻塞主管道。
"""

import asyncio
import importlib.util
from typing import Any, Callable, Dict, List, Optional

import yaml

from kclaw_cli.config import get_kclaw_home


HOOKS_DIR = get_kclaw_home() / "hooks"


class HookRegistry:
    """
    发现、加载和触发事件钩子。

    用法：
        registry = HookRegistry()
        registry.discover_and_load()
        await registry.emit("agent:start", {"platform": "telegram", ...})
    """

    def __init__(self):
        # event_type -> [handler_fn, ...]
        self._handlers: Dict[str, List[Callable]] = {}
        self._loaded_hooks: List[dict] = []  # 用于列表的元数据

    @property
    def loaded_hooks(self) -> List[dict]:
        """返回所有已加载钩子的元数据。"""
        return list(self._loaded_hooks)

    def _register_builtin_hooks(self) -> None:
        """注册始终处于活动状态的内置钩子。"""
        try:
            from gateway.builtin_hooks.boot_md import handle as boot_md_handle

            self._handlers.setdefault("gateway:startup", []).append(boot_md_handle)
            self._loaded_hooks.append({
                "name": "boot-md",
                "description": "Run ~/.kclaw/BOOT.md on gateway startup",
                "events": ["gateway:startup"],
                "path": "(builtin)",
            })
        except Exception as e:
            print(f"[钩子] 无法加载内置 boot-md 钩子: {e}", flush=True)

    def discover_and_load(self) -> None:
        """
        扫描钩子目录以查找钩子目录并加载其处理程序。

        还注册始终处于活动状态的内置钩子。

        每个钩子目录必须包含：
          - HOOK.yaml 至少包含 'name' 和 'events' 键
          - handler.py 带顶级 'handle' 函数（同步或异步）
        """
        self._register_builtin_hooks()

        if not HOOKS_DIR.exists():
            return

        for hook_dir in sorted(HOOKS_DIR.iterdir()):
            if not hook_dir.is_dir():
                continue

            manifest_path = hook_dir / "HOOK.yaml"
            handler_path = hook_dir / "handler.py"

            if not manifest_path.exists() or not handler_path.exists():
                continue

            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                if not manifest or not isinstance(manifest, dict):
                    print(f"[钩子] 跳过 {hook_dir.name}: 无效的 HOOK.yaml", flush=True)
                    continue

                hook_name = manifest.get("name", hook_dir.name)
                events = manifest.get("events", [])
                if not events:
                    print(f"[钩子] 跳过 {hook_name}: 未声明事件", flush=True)
                    continue

                # 动态加载处理程序模块
                spec = importlib.util.spec_from_file_location(
                    f"kclaw_hook_{hook_name}", handler_path
                )
                if spec is None or spec.loader is None:
                    print(f"[钩子] 跳过 {hook_name}: 无法加载 handler.py", flush=True)
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                handle_fn = getattr(module, "handle", None)
                if handle_fn is None:
                    print(f"[钩子] 跳过 {hook_name}: 未找到 'handle' 函数", flush=True)
                    continue

                # 为每个声明的事件注册处理程序
                for event in events:
                    self._handlers.setdefault(event, []).append(handle_fn)

                self._loaded_hooks.append({
                    "name": hook_name,
                    "description": manifest.get("description", ""),
                    "events": events,
                    "path": str(hook_dir),
                })

                print(f"[钩子] 已加载钩子 '{hook_name}' 用于事件: {events}", flush=True)

            except Exception as e:
                print(f"[钩子] 加载钩子 {hook_dir.name} 时出错: {e}", flush=True)

    async def emit(self, event_type: str, context: Optional[Dict[str, Any]] = None) -> None:
        """
        触发为事件注册的所有处理程序。

        支持通配符匹配：注册用于 "command:*" 的处理程序将
        为任何 "command:..." 事件触发。为基本类型
        如 "agent" 注册的处理程序不会为 "agent:start" 触发 — 
        仅精确匹配和显式通配符。

        参数：
            event_type: 事件标识符（例如 "agent:start"）。
            context:    带有事件特定数据的可选字典。
        """
        if context is None:
            context = {}

        # Collect handlers: exact match + wildcard match
        handlers = list(self._handlers.get(event_type, []))

        # Check for wildcard patterns (e.g., "command:*" matches "command:reset")
        if ":" in event_type:
            base = event_type.split(":")[0]
            wildcard_key = f"{base}:*"
            handlers.extend(self._handlers.get(wildcard_key, []))

        for fn in handlers:
            try:
                result = fn(event_type, context)
                # Support both sync and async handlers
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[钩子] '{event_type}' 的处理程序出错: {e}", flush=True)
