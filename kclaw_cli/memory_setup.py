"""kclaw memory setup|status — 配置记忆提供者插件。

通过插件系统自动检测已安装的记忆提供者。
使用交互式基于 curses 的 UI 选择提供者，然后按照提供者的
配置模式进行配置。写入 config.yaml + .env。
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from kclaw_constants import get_kclaw_home


# ---------------------------------------------------------------------------
# 基于 curses 的交互式选择器（与 kclaw tools 相同的模式）
# ---------------------------------------------------------------------------

def _curses_select(title: str, items: list[tuple[str, str]], default: int = 0) -> int:
    """使用方向键进行交互式单选。

    items: (标签, 描述) 元组列表。
    返回选中的索引，或在退出/取消时返回默认值。
    """
    try:
        import curses
        result = [default]

        def _menu(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, curses.COLOR_CYAN, -1)
            cursor = default

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                # Title
                try:
                    stdscr.addnstr(0, 0, title, max_x - 1,
                                   curses.A_BOLD | (curses.color_pair(2) if curses.has_colors() else 0))
                    stdscr.addnstr(1, 0, "  ↑↓ 导航  ⏎ 选择  q 退出", max_x - 1,
                                   curses.color_pair(3) if curses.has_colors() else curses.A_DIM)
                except curses.error:
                    pass

                for i, (label, desc) in enumerate(items):
                    y = i + 3
                    if y >= max_y - 1:
                        break
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow}  {label}"
                    if desc:
                        line += f"  {desc}"

                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line[:max_x - 1], max_x - 1, attr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord('k')):
                    cursor = (cursor - 1) % len(items)
                elif key in (curses.KEY_DOWN, ord('j')):
                    cursor = (cursor + 1) % len(items)
                elif key in (curses.KEY_ENTER, 10, 13):
                    result[0] = cursor
                    return
                elif key in (27, ord('q')):
                    return

        curses.wrapper(_menu)
        return result[0]

    except Exception:
        # 回退：带编号的输入
        print(f"\n  {title}\n")
        for i, (label, desc) in enumerate(items):
            marker = "→" if i == default else " "
            d = f"  {desc}" if desc else ""
            print(f"  {marker} {i + 1}. {label}{d}")
        while True:
            try:
                val = input(f"\n  选择 [1-{len(items)}] ({default + 1}): ")
                if not val:
                    return default
                idx = int(val) - 1
                if 0 <= idx < len(items):
                    return idx
            except (ValueError, EOFError):
                return default


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    """提示输入值，可选默认值和密钥掩码。"""
    suffix = f" [{default}]" if default else ""
    if secret:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        if sys.stdin.isatty():
            val = getpass.getpass(prompt="")
        else:
            val = sys.stdin.readline().strip()
    else:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        val = sys.stdin.readline().strip()
    return val or (default or "")


# ---------------------------------------------------------------------------
# 提供者发现
# ---------------------------------------------------------------------------

def _install_dependencies(provider_name: str) -> None:
    """安装 plugin.yaml 中声明的 pip 依赖。"""
    import subprocess
    from pathlib import Path as _Path

    plugin_dir = _Path(__file__).parent.parent / "plugins" / "memory" / provider_name
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        return

    try:
        import yaml
        with open(yaml_path) as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return

    pip_deps = meta.get("pip_dependencies", [])
    if not pip_deps:
        return

    # pip 包名 → 导入名的映射，用于包名不同的情况
    _IMPORT_NAMES = {
        "honcho-ai": "honcho",
        "mem0ai": "mem0",
        "hindsight-client": "hindsight_client",
        "hindsight-all": "hindsight",
    }

    # 检查缺失的包
    missing = []
    for dep in pip_deps:
        import_name = _IMPORT_NAMES.get(dep, dep.replace("-", "_").split("[")[0])
        try:
            __import__(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    print(f"\n  正在安装依赖: {', '.join(missing)}")

    import shutil
    uv_path = shutil.which("uv")
    if not uv_path:
        print(f"  ⚠ 未找到 uv — 无法安装依赖")
        print(f"  安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh")
        print(f"  然后重新运行: kclaw memory setup")
        return

    try:
        subprocess.run(
            [uv_path, "pip", "install", "--python", sys.executable, "--quiet"] + missing,
            check=True, timeout=120,
            capture_output=True,
        )
        print(f"  ✓ 已安装 {', '.join(missing)}")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ 安装 {', '.join(missing)} 失败")
        stderr = (e.stderr or b"").decode()[:200]
        if stderr:
            print(f"    {stderr}")
        print(f"  Run manually: uv pip install --python {sys.executable} {' '.join(missing)}")
    except Exception as e:
        print(f"  ⚠ 安装失败: {e}")
        print(f"  Run manually: uv pip install --python {sys.executable} {' '.join(missing)}")

    # 同时显示外部依赖（非 pip）如果有的话
    ext_deps = meta.get("external_dependencies", [])
    for dep in ext_deps:
        dep_name = dep.get("name", "")
        check_cmd = dep.get("check", "")
        install_cmd = dep.get("install", "")
        if check_cmd:
            try:
                subprocess.run(
                    check_cmd, shell=True, capture_output=True, timeout=5
                )
            except Exception:
                if install_cmd:
                    print(f"\n  ⚠ 未找到 '{dep_name}'。使用以下命令安装:")
                    print(f"    {install_cmd}")


def _get_available_providers() -> list:
    """从 plugins/memory/ 发现记忆提供者。

    返回 (名称, 描述, 提供者实例) 元组列表。
    """
    try:
        from plugins.memory import discover_memory_providers, load_memory_provider
        raw = discover_memory_providers()
    except Exception:
        raw = []

    results = []
    for name, desc, available in raw:
        try:
            provider = load_memory_provider(name)
            if not provider:
                continue
        except Exception:
            continue

        schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []
        has_secrets = any(f.get("secret") for f in schema)
        has_non_secrets = any(not f.get("secret") for f in schema)
        if has_secrets and has_non_secrets:
            setup_hint = "API 密钥 / 本地"
        elif has_secrets:
            setup_hint = "需要 API 密钥"
        elif not schema:
            setup_hint = "无需设置"
        else:
            setup_hint = "本地"

        results.append((name, setup_hint, provider))
    return results


# ---------------------------------------------------------------------------
# 设置向导
# ---------------------------------------------------------------------------

def cmd_setup_provider(provider_name: str) -> None:
    """为特定提供者运行记忆设置，跳过选择器。"""
    from kclaw_cli.config import load_config, save_config

    providers = _get_available_providers()
    match = None
    for name, desc, provider in providers:
        if name == provider_name:
            match = (name, desc, provider)
            break

    if not match:
        print(f"\n  未找到记忆提供者 '{provider_name}'。")
        print("  运行 'kclaw memory setup' 查看可用的提供者。\n")
        return

    name, _, provider = match

    _install_dependencies(name)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    if hasattr(provider, "post_setup"):
        kclaw_home = str(get_kclaw_home())
        provider.post_setup(kclaw_home, config)
        return

    # 回退：通用基于模式的设置（与 cmd_setup 相同）
    config["memory"]["provider"] = name
    save_config(config)
    print(f"\n  记忆提供者: {name}")
    print(f"  配置已保存到 config.yaml\n")


def cmd_setup(args) -> None:
    """交互式记忆提供者设置向导。"""
    from kclaw_cli.config import load_config, save_config

    providers = _get_available_providers()

    if not providers:
        print("\n  未检测到记忆提供者插件。")
        print("  安装插件到 ~/.kclaw/plugins/ 后重试。\n")
        return

    # Build picker items
    items = []
    for name, desc, _ in providers:
        items.append((name, f"— {desc}"))
    items.append(("Built-in only", "— MEMORY.md / USER.md (default)"))

    builtin_idx = len(items) - 1
    selected = _curses_select("Memory provider setup", items, default=builtin_idx)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    # Built-in only
    if selected >= len(providers) or selected < 0:
        config["memory"]["provider"] = ""
        save_config(config)
        print("\n  ✓ Memory provider: built-in only")
        print("  Saved to config.yaml\n")
        return

    name, _, provider = providers[selected]

    # Install pip dependencies if declared in plugin.yaml
    _install_dependencies(name)

    # If the provider has a post_setup hook, delegate entirely to it.
    # The hook handles its own config, connection test, and activation.
    if hasattr(provider, "post_setup"):
        kclaw_home = str(get_kclaw_home())
        provider.post_setup(kclaw_home, config)
        return

    schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []

    provider_config = config["memory"].get(name, {})
    if not isinstance(provider_config, dict):
        provider_config = {}

    env_path = get_kclaw_home() / ".env"
    env_writes = {}

    if schema:
        print(f"\n  Configuring {name}:\n")

        for field in schema:
            key = field["key"]
            desc = field.get("description", key)
            default = field.get("default")
            # Dynamic default: look up default from another field's value
            default_from = field.get("default_from")
            if default_from and isinstance(default_from, dict):
                ref_field = default_from.get("field", "")
                ref_map = default_from.get("map", {})
                ref_value = provider_config.get(ref_field, "")
                if ref_value and ref_value in ref_map:
                    default = ref_map[ref_value]
            is_secret = field.get("secret", False)
            choices = field.get("choices")
            env_var = field.get("env_var")
            url = field.get("url")

            # Skip fields whose "when" condition doesn't match
            when = field.get("when")
            if when and isinstance(when, dict):
                if not all(provider_config.get(k) == v for k, v in when.items()):
                    continue

            if choices and not is_secret:
                # Use curses picker for choice fields
                choice_items = [(c, "") for c in choices]
                current = provider_config.get(key, default)
                current_idx = 0
                if current and current in choices:
                    current_idx = choices.index(current)
                sel = _curses_select(f"  {desc}", choice_items, default=current_idx)
                provider_config[key] = choices[sel]
            elif is_secret:
                # Prompt for secret
                existing = os.environ.get(env_var, "") if env_var else ""
                if existing:
                    masked = f"...{existing[-4:]}" if len(existing) > 4 else "set"
                    val = _prompt(f"{desc} (current: {masked}, blank to keep)", secret=True)
                else:
                    hint = f"  Get yours at {url}" if url else ""
                    if hint:
                        print(hint)
                    val = _prompt(desc, secret=True)
                if val and env_var:
                    env_writes[env_var] = val
            else:
                # Regular text prompt
                current = provider_config.get(key)
                effective_default = current or default
                val = _prompt(desc, default=str(effective_default) if effective_default else None)
                if val:
                    provider_config[key] = val

    # Write activation key to config.yaml
    config["memory"]["provider"] = name
    save_config(config)

    # Write non-secret config to provider's native location
    kclaw_home = str(get_kclaw_home())
    if provider_config and hasattr(provider, "save_config"):
        try:
            provider.save_config(provider_config, kclaw_home)
        except Exception as e:
            print(f"  Failed to write provider config: {e}")

    # Write secrets to .env
    if env_writes:
        _write_env_vars(env_path, env_writes)

    print(f"\n  Memory provider: {name}")
    print(f"  Activation saved to config.yaml")
    if provider_config:
        print(f"  Provider config saved")
    if env_writes:
        print(f"  API keys saved to .env")
    print(f"\n  Start a new session to activate.\n")


def _write_env_vars(env_path: Path, env_writes: dict) -> None:
    """Append or update env vars in .env file."""
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in env_writes:
            new_lines.append(f"{key_match}={env_writes[key_match]}")
            updated_keys.add(key_match)
        else:
            new_lines.append(line)

    for key, val in env_writes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    """Show current memory provider config."""
    from kclaw_cli.config import load_config

    config = load_config()
    mem_config = config.get("memory", {})
    provider_name = mem_config.get("provider", "")

    print(f"\nMemory status\n" + "─" * 40)
    print(f"  Built-in:  always active")
    print(f"  Provider:  {provider_name or '(none — built-in only)'}")

    if provider_name:
        provider_config = mem_config.get(provider_name, {})
        if provider_config:
            print(f"\n  {provider_name} config:")
            for key, val in provider_config.items():
                print(f"    {key}: {val}")

        providers = _get_available_providers()
        found = any(name == provider_name for name, _, _ in providers)
        if found:
            print(f"\n  Plugin:    installed ✓")
            for pname, _, p in providers:
                if pname == provider_name:
                    if p.is_available():
                        print(f"  Status:    available ✓")
                    else:
                        print(f"  Status:    not available ✗")
                        schema = p.get_config_schema() if hasattr(p, "get_config_schema") else []
                        secrets = [f for f in schema if f.get("secret")]
                        if secrets:
                            print(f"  Missing:")
                            for s in secrets:
                                env_var = s.get("env_var", "")
                                url = s.get("url", "")
                                is_set = bool(os.environ.get(env_var))
                                mark = "✓" if is_set else "✗"
                                line = f"    {mark} {env_var}"
                                if url and not is_set:
                                    line += f"  → {url}"
                                print(line)
                    break
        else:
            print(f"\n  Plugin:    NOT installed ✗")
            print(f"  Install the '{provider_name}' memory plugin to ~/.kclaw/plugins/")

    providers = _get_available_providers()
    if providers:
        print(f"\n  Installed plugins:")
        for pname, desc, _ in providers:
            active = " ← active" if pname == provider_name else ""
            print(f"    • {pname}  ({desc}){active}")

    print()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def memory_command(args) -> None:
    """Route memory subcommands."""
    sub = getattr(args, "memory_command", None)
    if sub == "setup":
        cmd_setup(args)
    elif sub == "status":
        cmd_status(args)
    else:
        cmd_status(args)
