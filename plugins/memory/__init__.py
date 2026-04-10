"""Memory provider 插件发现。

Scans ``plugins/memory/<name>/`` directories for memory provider plugins.
Each subdirectory must contain ``__init__.py`` with a class implementing
the MemoryProvider ABC.

Memory providers are separate from the general plugin system — they live
in the repo and are always available without user installation. Only ONE
can be active at a time, selected via ``memory.provider`` in config.yaml.

Usage:
    from plugins.memory import discover_memory_providers, load_memory_provider

    available = discover_memory_providers()   # [(name, desc, available), ...]
    provider = load_memory_provider("openviking")  # MemoryProvider instance
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_MEMORY_PLUGINS_DIR = Path(__file__).parent


def discover_memory_providers() -> List[Tuple[str, str, bool]]:
    """扫描 plugins/memory/ 查找可用的 providers。

    返回 (name, description, is_available) 元组列表。
    不会导入 providers — 仅读取 plugin.yaml 获取元数据
    并进行轻量级的可用性检查。
    """
    results = []
    if not _MEMORY_PLUGINS_DIR.is_dir():
        return results

    for child in sorted(_MEMORY_PLUGINS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        init_file = child / "__init__.py"
        if not init_file.exists():
            continue

        # Read description from plugin.yaml if available
        desc = ""
        yaml_file = child / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
            except Exception:
                pass

        # Quick availability check — try loading and calling is_available()
        available = True
        try:
            provider = _load_provider_from_dir(child)
            if provider:
                available = provider.is_available()
            else:
                available = False
        except Exception:
            available = False

        results.append((child.name, desc, available))

    return results


def load_memory_provider(name: str) -> Optional["MemoryProvider"]:
    """按名称加载并返回 MemoryProvider 实例。

    如果 provider 未找到或加载失败则返回 None。
    """
    provider_dir = _MEMORY_PLUGINS_DIR / name
    if not provider_dir.is_dir():
        logger.debug("Memory provider '%s' not found in %s", name, _MEMORY_PLUGINS_DIR)
        return None

    try:
        provider = _load_provider_from_dir(provider_dir)
        if provider:
            return provider
        logger.warning("Memory provider '%s' loaded but no provider instance found", name)
        return None
    except Exception as e:
        logger.warning("Failed to load memory provider '%s': %s", name, e)
        return None


def _load_provider_from_dir(provider_dir: Path) -> Optional["MemoryProvider"]:
    """导入 provider 模块并提取 MemoryProvider 实例。

    模块必须满足以下条件之一：
    - 一个 register(ctx) 函数（plugin-style）— 我们模拟一个 ctx
    - 一个扩展 MemoryProvider 的顶层类 — 我们实例化它
    """
    name = provider_dir.name
    module_name = f"plugins.memory.{name}"
    init_file = provider_dir / "__init__.py"

    if not init_file.exists():
        return None

    # Check if already loaded
    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        # Handle relative imports within the plugin
        # First ensure the parent packages are registered
        for parent in ("plugins", "plugins.memory"):
            if parent not in sys.modules:
                parent_path = Path(__file__).parent
                if parent == "plugins":
                    parent_path = parent_path.parent
                parent_init = parent_path / "__init__.py"
                if parent_init.exists():
                    spec = importlib.util.spec_from_file_location(
                        parent, str(parent_init),
                        submodule_search_locations=[str(parent_path)]
                    )
                    if spec:
                        parent_mod = importlib.util.module_from_spec(spec)
                        sys.modules[parent] = parent_mod
                        try:
                            spec.loader.exec_module(parent_mod)
                        except Exception:
                            pass

        # Now load the provider module
        spec = importlib.util.spec_from_file_location(
            module_name, str(init_file),
            submodule_search_locations=[str(provider_dir)]
        )
        if not spec:
            return None

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod

        # Register submodules so relative imports work
        # e.g., "from .store import MemoryStore" in holographic plugin
        for sub_file in provider_dir.glob("*.py"):
            if sub_file.name == "__init__.py":
                continue
            sub_name = sub_file.stem
            full_sub_name = f"{module_name}.{sub_name}"
            if full_sub_name not in sys.modules:
                sub_spec = importlib.util.spec_from_file_location(
                    full_sub_name, str(sub_file)
                )
                if sub_spec:
                    sub_mod = importlib.util.module_from_spec(sub_spec)
                    sys.modules[full_sub_name] = sub_mod
                    try:
                        sub_spec.loader.exec_module(sub_mod)
                    except Exception as e:
                        logger.debug("Failed to load submodule %s: %s", full_sub_name, e)

        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.debug("Failed to exec_module %s: %s", module_name, e)
            sys.modules.pop(module_name, None)
            return None

    # Try register(ctx) pattern first (how our plugins are written)
    if hasattr(mod, "register"):
        collector = _ProviderCollector()
        try:
            mod.register(collector)
            if collector.provider:
                return collector.provider
        except Exception as e:
            logger.debug("register() failed for %s: %s", name, e)

    # Fallback: find a MemoryProvider subclass and instantiate it
    from agent.memory_provider import MemoryProvider
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if (isinstance(attr, type) and issubclass(attr, MemoryProvider)
                and attr is not MemoryProvider):
            try:
                return attr()
            except Exception:
                pass

    return None


class _ProviderCollector:
    """伪造的插件上下文，用于捕获 register_memory_provider 调用。"""

    def __init__(self):
        self.provider = None

    def register_memory_provider(self, provider):
        self.provider = provider

    # No-op for other registration methods
    def register_tool(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass  # CLI 注册通过 discover_plugin_cli_commands() 实现


def _get_active_memory_provider() -> Optional[str]:
    """从 config.yaml 读取活动的 memory provider 名称。

    返回 provider 名称（例如 ``"honcho"``），如果没有配置外部 provider 则返回 None。
    轻量级 — 仅读取配置，不加载插件。
    """
    try:
        from kclaw_cli.config import load_config
        config = load_config()
        return config.get("memory", {}).get("provider") or None
    except Exception:
        return None


def discover_plugin_cli_commands() -> List[dict]:
    """仅为**活动的** memory 插件返回 CLI 命令。

    一次只能有一个 memory provider 处于活动状态（通过
    config.yaml 中的 ``memory.provider`` 设置）。此函数读取该值，
    仅加载匹配插件的 CLI 注册。如果没有 provider 处于活动状态，则不注册任何命令。

    在活动插件的 ``cli.py`` 中查找 ``register_cli(subparser)`` 函数。
    返回最多一个 dict 的列表，键为：``name``、``help``、``description``、``setup_fn``、``handler_fn``。

    这是一个轻量级扫描 — 它仅导入 ``cli.py``，而非完整插件模块。
    可在 argparse 设置期间安全调用，在任何 provider 加载之前。
    """
    results: List[dict] = []
    if not _MEMORY_PLUGINS_DIR.is_dir():
        return results

    active_provider = _get_active_memory_provider()
    if not active_provider:
        return results

    # Only look at the active provider's directory
    plugin_dir = _MEMORY_PLUGINS_DIR / active_provider
    if not plugin_dir.is_dir():
        return results

    cli_file = plugin_dir / "cli.py"
    if not cli_file.exists():
        return results

    module_name = f"plugins.memory.{active_provider}.cli"
    try:
        # Import the CLI module (lightweight — no SDK needed)
        if module_name in sys.modules:
            cli_mod = sys.modules[module_name]
        else:
            spec = importlib.util.spec_from_file_location(
                module_name, str(cli_file)
            )
            if not spec or not spec.loader:
                return results
            cli_mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = cli_mod
            spec.loader.exec_module(cli_mod)

        register_cli = getattr(cli_mod, "register_cli", None)
        if not callable(register_cli):
            return results

        # Read metadata from plugin.yaml if available
        help_text = f"Manage {active_provider} memory plugin"
        description = ""
        yaml_file = plugin_dir / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
                if desc:
                    help_text = desc
                    description = desc
            except Exception:
                pass

        handler_fn = getattr(cli_mod, f"{active_provider}_command", None) or \
                     getattr(cli_mod, "honcho_command", None)

        results.append({
            "name": active_provider,
            "help": help_text,
            "description": description,
            "setup_fn": register_cli,
            "handler_fn": handler_fn,
            "plugin": active_provider,
        })
    except Exception as e:
        logger.debug("Failed to scan CLI for memory plugin '%s': %s", active_provider, e)

    return results
