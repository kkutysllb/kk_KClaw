"""kclaw plugins CLI 子命令 — 安装、更新、移除和列出插件。

插件从 Git 仓库安装到 ~/.kclaw/plugins/。
支持完整 URL 和 owner/repo 简写形式（解析到 GitHub）。

安装后，如果插件附带 after-install.md 文件，会使用 Rich Markdown
渲染。否则显示默认确认。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kclaw_constants import get_kclaw_home

logger = logging.getLogger(__name__)

# 支持的清单版本。
# 插件可在 plugin.yaml 中声明 manifest_version: 1；
# 未来对清单模式的破坏性更改会增加此版本号。
_SUPPORTED_MANIFEST_VERSION = 1


def _plugins_dir() -> Path:
    """返回用户插件目录，如不存在则创建。"""
    plugins = get_kclaw_home() / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    return plugins


def _sanitize_plugin_name(name: str, plugins_dir: Path) -> Path:
    """验证插件名称并返回 plugins_dir 内的安全目标路径。

    如果名称包含路径遍历序列或将在插件目录外解析，
    则抛出 ValueError。
    """
    if not name:
        raise ValueError("插件名称不能为空。")

    if name in (".", ".."):
        raise ValueError(
            f"无效的插件名称 '{name}'：不能引用插件目录本身。"
        )

    # 拒绝明显的遍历字符
    for bad in ("/", "\\", ".."):
        if bad in name:
            raise ValueError(f"无效的插件名称 '{name}'：不能包含 '{bad}'。")

    target = (plugins_dir / name).resolve()
    plugins_resolved = plugins_dir.resolve()

    if target == plugins_resolved:
        raise ValueError(
            f"无效的插件名称 '{name}'：解析为插件目录本身。"
        )

    try:
        target.relative_to(plugins_resolved)
    except ValueError:
        raise ValueError(
            f"无效的插件名称 '{name}'：解析到插件目录外。"
        )

    return target


def _resolve_git_url(identifier: str) -> str:
    """将标识符转换为可克隆的 Git URL。

    接受的格式：
    - 完整 URL: https://github.com/owner/repo.git
    - 完整 URL: git@github.com:owner/repo.git
    - 完整 URL: ssh://git@github.com/owner/repo.git
    - 简写: owner/repo  →  https://github.com/owner/repo.git

    注意：接受 http:// 和 file:// 方案，但在安装时会触发安全警告。
    """
    # 已是 URL
    if identifier.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return identifier

    # owner/repo 简写
    parts = identifier.strip("/").split("/")
    if len(parts) == 2:
        owner, repo = parts
        return f"https://github.com/{owner}/{repo}.git"

    raise ValueError(
        f"无效的插件标识符：'{identifier}'。"
        "请使用 Git URL 或 owner/repo 简写。"
    )


def _repo_name_from_url(url: str) -> str:
    """从 Git URL 中提取仓库名称作为插件目录名称。"""
    # 去除末尾的 .git 和斜杠
    name = url.rstrip("/")
    if name.endswith(".git"):
        name = name[:-4]
    # 获取最后一个路径组件
    name = name.rsplit("/", 1)[-1]
    # 处理 SSH 风格 URL: git@github.com:owner/repo
    if ":" in name:
        name = name.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    return name


def _read_manifest(plugin_dir: Path) -> dict:
    """读取 plugin.yaml 并返回解析后的字典，如不存在则返回空字典。"""
    manifest_file = plugin_dir / "plugin.yaml"
    if not manifest_file.exists():
        return {}
    try:
        import yaml

        with open(manifest_file) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to read plugin.yaml in %s: %s", plugin_dir, e)
        return {}


def _copy_example_files(plugin_dir: Path, console) -> None:
    """将 .example 文件复制到实际名称（如尚不存在）。

    例如，config.yaml.example 变为 config.yaml。
    跳过已存在的文件以避免在重新安装时覆盖用户配置。
    """
    for example_file in plugin_dir.glob("*.example"):
        real_name = example_file.stem  # 例如 "config.yaml" 来自 "config.yaml.example"
        real_path = plugin_dir / real_name
        if not real_path.exists():
            try:
                shutil.copy2(example_file, real_path)
                console.print(
                    f"[dim]  已创建 {real_name} 来自 {example_file.name}[/dim]"
                )
            except OSError as e:
                console.print(
                    f"[yellow]警告：[/yellow] 复制 {example_file.name} 失败：{e}"
                )


def _prompt_plugin_env_vars(manifest: dict, console) -> None:
    """提示输入 plugin.yaml 中声明的必需环境变量。

    requires_env 接受两种格式：

    简单列表（向后兼容）：

        requires_env:
          - MY_API_KEY

    带元数据的丰富列表：

        requires_env:
          - name: MY_API_KEY
            description: "Acme 服务的 API 密钥"
            url: "https://acme.com/keys"
            secret: true

    已设置的变量会被跳过。值保存到用户的 .env 文件。
    """
    requires_env = manifest.get("requires_env") or []
    if not requires_env:
        return

    from kclaw_cli.config import get_env_value, save_env_value  # noqa: F811
    from kclaw_constants import display_kclaw_home

    # 规范化为字典列表
    env_specs: list[dict] = []
    for entry in requires_env:
        if isinstance(entry, str):
            env_specs.append({"name": entry})
        elif isinstance(entry, dict) and entry.get("name"):
            env_specs.append(entry)

    # 仅筛选尚未设置的变量
    missing = [s for s in env_specs if not get_env_value(s["name"])]
    if not missing:
        return

    plugin_name = manifest.get("name", "此插件")
    console.print(f"\n[bold]{plugin_name}[/bold] 需要以下环境变量：\n")

    for spec in missing:
        name = spec["name"]
        desc = spec.get("description", "")
        url = spec.get("url", "")
        secret = spec.get("secret", False)

        label = f"  {name}"
        if desc:
            label += f" — {desc}"
        console.print(label)
        if url:
            console.print(f"  [dim]获取地址：{url}[/dim]")

        try:
            if secret:
                import getpass
                value = getpass.getpass(f"  {name}: ").strip()
            else:
                value = input(f"  {name}: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n[dim]  已跳过（稍后可在 {display_kclaw_home()}/.env 中设置）[/dim]")
            return

        if value:
            save_env_value(name, value)
            os.environ[name] = value
            console.print(f"  [green]✓[/green] 已保存到 {display_kclaw_home()}/.env")
        else:
            console.print(f"  [dim]  已跳过（稍后在 {display_kclaw_home()}/.env 中设置 {name}）[/dim]")

    console.print()


def _display_after_install(plugin_dir: Path, identifier: str) -> None:
    """如果存在 after-install.md 则显示，否则显示默认消息。"""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    console = Console()
    after_install = plugin_dir / "after-install.md"

    if after_install.exists():
        content = after_install.read_text(encoding="utf-8")
        md = Markdown(content)
        console.print()
        console.print(Panel(md, border_style="green", expand=False))
        console.print()
    else:
        console.print()
        console.print(
            Panel(
                f"[green bold]Plugin installed:[/] {identifier}\n"
                f"[dim]Location:[/] {plugin_dir}",
                border_style="green",
                title="✓ Installed",
                expand=False,
            )
        )
        console.print()


def _display_removed(name: str, plugins_dir: Path) -> None:
    """移除插件后显示确认。"""
    from rich.console import Console

    console = Console()
    console.print()
    console.print(f"[red]✗[/red] 插件 [bold]{name}[/bold] 已从 {plugins_dir} 移除")
    console.print()


def _require_installed_plugin(name: str, plugins_dir: Path, console) -> Path:
    """如果插件存在则返回路径，否则退出并列出已安装的插件。"""
    target = _sanitize_plugin_name(name, plugins_dir)
    if not target.exists():
        installed = ", ".join(d.name for d in plugins_dir.iterdir() if d.is_dir()) or "（无）"
        console.print(
            f"[red]错误：[/red] 在 {plugins_dir} 中未找到插件 '{name}'。\n"
            f"已安装的插件：{installed}"
        )
        sys.exit(1)
    return target


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_install(identifier: str, force: bool = False) -> None:
    """从 Git URL 或 owner/repo 简写安装插件。"""
    import tempfile
    from rich.console import Console

    console = Console()

    try:
        git_url = _resolve_git_url(identifier)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # 警告不安全/本地 URL 方案
    if git_url.startswith(("http://", "file://")):
        console.print(
            "[yellow]警告：[/yellow] 使用不安全/本地 URL 方案。"
            "生产环境请考虑使用 https:// 或 git@。"
        )

    plugins_dir = _plugins_dir()

    # Clone into a temp directory first so we can read plugin.yaml for the name
    with tempfile.TemporaryDirectory() as tmp:
        tmp_target = Path(tmp) / "plugin"
        console.print(f"[dim]正在克隆 {git_url}...[/dim]")

        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", git_url, str(tmp_target)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            console.print("[red]错误：[/red] 未安装 git 或不在 PATH 中。")
            sys.exit(1)
        except subprocess.TimeoutExpired:
            console.print("[red]错误：[/red] Git 克隆在 60 秒后超时。")
            sys.exit(1)

        if result.returncode != 0:
            console.print(
                f"[red]错误：[/red] Git 克隆失败：\n{result.stderr.strip()}"
            )
            sys.exit(1)

        # Read manifest
        manifest = _read_manifest(tmp_target)
        plugin_name = manifest.get("name") or _repo_name_from_url(git_url)

        # Sanitize plugin name against path traversal
        try:
            target = _sanitize_plugin_name(plugin_name, plugins_dir)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        # 检查 manifest_version 兼容性
        mv = manifest.get("manifest_version")
        if mv is not None:
            try:
                mv_int = int(mv)
            except (ValueError, TypeError):
                console.print(
                    f"[red]错误：[/red] 插件 '{plugin_name}' 的 "
                    f"manifest_version '{mv}' 无效（应为整数）。"
                )
                sys.exit(1)
            if mv_int > _SUPPORTED_MANIFEST_VERSION:
                from kclaw_cli.config import recommended_update_command
                console.print(
                    f"[red]错误：[/red] 插件 '{plugin_name}' 需要 manifest_version "
                    f"{mv}，但此安装程序仅支持到 {_SUPPORTED_MANIFEST_VERSION}。\n"
                    f"运行 [bold]{recommended_update_command()}[/bold] 获取更新的安装程序。"
                )
                sys.exit(1)

        if target.exists():
            if not force:
                console.print(
                    f"[red]错误：[/red] 插件 '{plugin_name}' 已存在于 {target}。\n"
                    f"使用 [bold]--force[/bold] 移除并重新安装，或"
                    f"使用 [bold]kclaw plugins update {plugin_name}[/bold] 拉取最新版本。"
                )
                sys.exit(1)
            console.print(f"[dim]  正在移除现有的 {plugin_name}...[/dim]")
            shutil.rmtree(target)

        # 从临时目录移动到最终位置
        shutil.move(str(tmp_target), str(target))

    # 验证它看起来像插件
    if not (target / "plugin.yaml").exists() and not (target / "__init__.py").exists():
        console.print(
            f"[yellow]警告：[/yellow] {plugin_name} 不包含 plugin.yaml "
            f"或 __init__.py。它可能不是有效的 KClaw 插件。"
        )

    # 将 .example 文件复制到实际名称（例如 config.yaml.example → config.yaml）
    _copy_example_files(target, console)

    # 从安装位置重新读取清单（用于环境变量提示）
    installed_manifest = _read_manifest(target)

    # 在显示安装后文档前提示输入必需的环境变量
    _prompt_plugin_env_vars(installed_manifest, console)

    _display_after_install(target, identifier)

    console.print("[dim]重启网关以使插件生效：[/dim]")
    console.print("[dim]  kclaw gateway restart[/dim]")
    console.print()


def cmd_update(name: str) -> None:
    """通过从 git 远程拉取最新版本更新已安装的插件。"""
    from rich.console import Console

    console = Console()
    plugins_dir = _plugins_dir()

    try:
        target = _require_installed_plugin(name, plugins_dir, console)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if not (target / ".git").exists():
        console.print(
            f"[red]错误：[/red] 插件 '{name}' 不是通过 git 安装的 "
            f"（无 .git 目录）。无法更新。"
        )
        sys.exit(1)

    console.print(f"[dim]正在更新 {name}...[/dim]")

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(target),
        )
    except FileNotFoundError:
        console.print("[red]错误：[/red] 未安装 git 或不在 PATH 中。")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        console.print("[red]错误：[/red] Git 拉取在 60 秒后超时。")
        sys.exit(1)

    if result.returncode != 0:
        console.print(f"[red]错误：[/red] Git 拉取失败：\n{result.stderr.strip()}")
        sys.exit(1)

    # 复制任何新的 .example 文件
    _copy_example_files(target, console)

    output = result.stdout.strip()
    if "Already up to date" in output:
        console.print(
            f"[green]✓[/green] 插件 [bold]{name}[/bold] 已是最新版本。"
        )
    else:
        console.print(f"[green]✓[/green] 插件 [bold]{name}[/bold] 已更新。")
        console.print(f"[dim]{output}[/dim]")


def cmd_remove(name: str) -> None:
    """按名称移除已安装的插件。"""
    from rich.console import Console

    console = Console()
    plugins_dir = _plugins_dir()

    try:
        target = _require_installed_plugin(name, plugins_dir, console)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    shutil.rmtree(target)
    _display_removed(name, plugins_dir)


def _get_disabled_set() -> set:
    """从 config.yaml 读取禁用的插件集合。"""
    try:
        from kclaw_cli.config import load_config
        config = load_config()
        disabled = config.get("plugins", {}).get("disabled", [])
        return set(disabled) if isinstance(disabled, list) else set()
    except Exception:
        return set()


def _save_disabled_set(disabled: set) -> None:
    """将禁用的插件列表写入 config.yaml。"""
    from kclaw_cli.config import load_config, save_config
    config = load_config()
    if "plugins" not in config:
        config["plugins"] = {}
    config["plugins"]["disabled"] = sorted(disabled)
    save_config(config)


def cmd_enable(name: str) -> None:
    """启用之前禁用的插件。"""
    from rich.console import Console

    console = Console()
    plugins_dir = _plugins_dir()

    # 验证插件是否存在
    target = plugins_dir / name
    if not target.is_dir():
        console.print(f"[red]插件 '{name}' 未安装。[/red]")
        sys.exit(1)

    disabled = _get_disabled_set()
    if name not in disabled:
        console.print(f"[dim]插件 '{name}' 已启用。[/dim]")
        return

    disabled.discard(name)
    _save_disabled_set(disabled)
    console.print(f"[green]✓[/green] 插件 [bold]{name}[/bold] 已启用。下次会话生效。")


def cmd_disable(name: str) -> None:
    """在不移除的情况下禁用插件。"""
    from rich.console import Console

    console = Console()
    plugins_dir = _plugins_dir()

    # 验证插件是否存在
    target = plugins_dir / name
    if not target.is_dir():
        console.print(f"[red]插件 '{name}' 未安装。[/red]")
        sys.exit(1)

    disabled = _get_disabled_set()
    if name in disabled:
        console.print(f"[dim]插件 '{name}' 已禁用。[/dim]")
        return

    disabled.add(name)
    _save_disabled_set(disabled)
    console.print(f"[yellow]⊘[/yellow] 插件 [bold]{name}[/bold] 已禁用。下次会话生效。")


def cmd_list() -> None:
    """列出已安装的插件。"""
    from rich.console import Console
    from rich.table import Table

    try:
        import yaml
    except ImportError:
        yaml = None

    console = Console()
    plugins_dir = _plugins_dir()

    dirs = sorted(d for d in plugins_dir.iterdir() if d.is_dir())
    if not dirs:
        console.print("[dim]未安装任何插件。[/dim]")
        console.print("[dim]安装方式：[/dim] kclaw plugins install owner/repo")
        return

    disabled = _get_disabled_set()

    table = Table(title="已安装的插件", show_lines=False)
    table.add_column("名称", style="bold")
    table.add_column("状态")
    table.add_column("版本", style="dim")
    table.add_column("描述")
    table.add_column("来源", style="dim")

    for d in dirs:
        manifest_file = d / "plugin.yaml"
        name = d.name
        version = ""
        description = ""
        source = "本地"

        if manifest_file.exists() and yaml:
            try:
                with open(manifest_file) as f:
                    manifest = yaml.safe_load(f) or {}
                name = manifest.get("name", d.name)
                version = manifest.get("version", "")
                description = manifest.get("description", "")
            except Exception:
                pass

        # 检查是否是 git 仓库（通过 kclaw plugins install 安装）
        if (d / ".git").exists():
            source = "git"

        is_disabled = name in disabled or d.name in disabled
        status = "[red]已禁用[/red]" if is_disabled else "[green]已启用[/green]"
        table.add_row(name, status, str(version), description, source)

    console.print()
    console.print(table)
    console.print()
    console.print("[dim]交互式切换：[/dim] kclaw plugins")
    console.print("[dim]启用/禁用：[/dim] kclaw plugins enable/disable <name>")


def cmd_toggle() -> None:
    """交互式 curses 复选框，用于启用/禁用已安装的插件。"""
    from rich.console import Console

    try:
        import yaml
    except ImportError:
        yaml = None

    console = Console()
    plugins_dir = _plugins_dir()

    dirs = sorted(d for d in plugins_dir.iterdir() if d.is_dir())
    if not dirs:
        console.print("[dim]未安装任何插件。[/dim]")
        console.print("[dim]安装方式：[/dim] kclaw plugins install owner/repo")
        return

    disabled = _get_disabled_set()

    # 构建项目列表：显示用的 "名称 — 描述"
    names = []
    labels = []
    selected = set()

    for i, d in enumerate(dirs):
        manifest_file = d / "plugin.yaml"
        name = d.name
        description = ""

        if manifest_file.exists() and yaml:
            try:
                with open(manifest_file) as f:
                    manifest = yaml.safe_load(f) or {}
                name = manifest.get("name", d.name)
                description = manifest.get("description", "")
            except Exception:
                pass

        names.append(name)
        label = f"{name} — {description}" if description else name
        labels.append(label)

        if name not in disabled and d.name not in disabled:
            selected.add(i)

    from kclaw_cli.curses_ui import curses_checklist

    result = curses_checklist(
        title="插件 — 切换启用/禁用",
        items=labels,
        selected=selected,
    )

    # 从取消选择的项目计算新的禁用集合
    new_disabled = set()
    for i, name in enumerate(names):
        if i not in result:
            new_disabled.add(name)

    if new_disabled != disabled:
        _save_disabled_set(new_disabled)
        enabled_count = len(names) - len(new_disabled)
        console.print(
            f"\n[green]✓[/green] {enabled_count} 已启用，{len(new_disabled)} 已禁用。"
            f"下次会话生效。"
        )
    else:
        console.print("\n[dim]无更改。[/dim]")


def plugins_command(args) -> None:
    """分发 kclaw plugins 子命令。"""
    action = getattr(args, "plugins_action", None)

    if action == "install":
        cmd_install(args.identifier, force=getattr(args, "force", False))
    elif action == "update":
        cmd_update(args.name)
    elif action in ("remove", "rm", "uninstall"):
        cmd_remove(args.name)
    elif action == "enable":
        cmd_enable(args.name)
    elif action == "disable":
        cmd_disable(args.name)
    elif action in ("list", "ls"):
        cmd_list()
    elif action is None:
        cmd_toggle()
    else:
        from rich.console import Console

        Console().print(f"[red]未知的 plugins 操作：{action}[/red]")
        sys.exit(1)
