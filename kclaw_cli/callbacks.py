"""终端工具集成的交互式提示回调。

这些函数将 terminal_tool 的交互式提示（澄清、sudo、审批）
桥接到 prompt_toolkit 的事件循环中。每个函数将 KClawCLI 实例
作为第一个参数，并使用其状态（队列、应用引用）来协调 TUI。
"""

import queue
import time as _time
import getpass

from kclaw_cli.banner import cprint, _DIM, _RST
from kclaw_cli.config import save_env_value_secure
from kclaw_constants import display_kclaw_home


def clarify_callback(cli, question, choices):
    """通过 TUI 提示澄清性问题。

    设置交互式选择界面，然后阻塞直到用户响应。
    返回用户的选择或超时消息。
    """
    from cli import CLI_CONFIG

    timeout = CLI_CONFIG.get("clarify", {}).get("timeout", 120)
    response_queue = queue.Queue()
    is_open_ended = not choices

    cli._clarify_state = {
        "question": question,
        "choices": choices if not is_open_ended else [],
        "selected": 0,
        "response_queue": response_queue,
    }
    cli._clarify_deadline = _time.monotonic() + timeout
    cli._clarify_freetext = is_open_ended

    if hasattr(cli, "_app") and cli._app:
        cli._app.invalidate()

    while True:
        try:
            result = response_queue.get(timeout=1)
            cli._clarify_deadline = 0
            return result
        except queue.Empty:
            remaining = cli._clarify_deadline - _time.monotonic()
            if remaining <= 0:
                break
            if hasattr(cli, "_app") and cli._app:
                cli._app.invalidate()

    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._clarify_deadline = 0
    if hasattr(cli, "_app") and cli._app:
        cli._app.invalidate()
    cprint(f"\n{_DIM}(澄清超时 {timeout} 秒后 — 智能体将自行决定){_RST}")
    return (
        "用户未在时间限制内提供响应。"
        "请运用最佳判断做出选择并继续。"
    )


def prompt_for_secret(cli, var_name: str, prompt: str, metadata=None) -> dict:
    """通过 TUI 提示输入密钥值（例如技能所需的 API 密钥）。

    返回包含以下键的字典：success, stored_as, validated, skipped, message。
    密钥存储在 ~/.kclaw/.env 中，绝不会暴露给模型。
    """
    if not getattr(cli, "_app", None):
        if not hasattr(cli, "_secret_state"):
            cli._secret_state = None
        if not hasattr(cli, "_secret_deadline"):
            cli._secret_deadline = 0
        try:
            value = getpass.getpass(f"{prompt} (隐藏，输入跳过): ")
        except (EOFError, KeyboardInterrupt):
            value = ""

        if not value:
            cprint(f"\n{_DIM}  ⏭ 跳过密钥输入{_RST}")
            return {
                "success": True,
                "reason": "cancelled",
                "stored_as": var_name,
                "validated": False,
                "skipped": True,
                "message": "跳过密钥设置。",
            }

        stored = save_env_value_secure(var_name, value)
        _dhh = display_kclaw_home()
        cprint(f"\n{_DIM}  ✓ 密钥已存储在 {_dhh}/.env 中，变量名为 {var_name}{_RST}")
        return {
            **stored,
            "skipped": False,
            "message": "Secret stored securely. The secret value was not exposed to the model.",
        }

    timeout = 120
    response_queue = queue.Queue()

    cli._secret_state = {
        "var_name": var_name,
        "prompt": prompt,
        "metadata": metadata or {},
        "response_queue": response_queue,
    }
    cli._secret_deadline = _time.monotonic() + timeout
    # 避免在按 Enter 键时将过时的草稿输入存储为密钥。
    if hasattr(cli, "_clear_secret_input_buffer"):
        try:
            cli._clear_secret_input_buffer()
        except Exception:
            pass
    elif hasattr(cli, "_app") and cli._app:
        try:
            cli._app.current_buffer.reset()
        except Exception:
            pass

    if hasattr(cli, "_app") and cli._app:
        cli._app.invalidate()

    while True:
        try:
            value = response_queue.get(timeout=1)
            cli._secret_state = None
            cli._secret_deadline = 0
            if hasattr(cli, "_app") and cli._app:
                cli._app.invalidate()

            if not value:
                cprint(f"\n{_DIM}  ⏭ 跳过密钥输入{_RST}")
                return {
                    "success": True,
                    "reason": "cancelled",
                    "stored_as": var_name,
                    "validated": False,
                    "skipped": True,
                    "message": "跳过密钥设置。",
                }

            stored = save_env_value_secure(var_name, value)
            _dhh = display_kclaw_home()
            cprint(f"\n{_DIM}  ✓ 密钥已存储在 {_dhh}/.env 中，变量名为 {var_name}{_RST}")
            return {
                **stored,
                "skipped": False,
                "message": "密钥已安全存储。密钥值未暴露给模型。",
            }
        except queue.Empty:
            remaining = cli._secret_deadline - _time.monotonic()
            if remaining <= 0:
                break
            if hasattr(cli, "_app") and cli._app:
                cli._app.invalidate()

    cli._secret_state = None
    cli._secret_deadline = 0
    if hasattr(cli, "_clear_secret_input_buffer"):
        try:
            cli._clear_secret_input_buffer()
        except Exception:
            pass
    elif hasattr(cli, "_app") and cli._app:
        try:
            cli._app.current_buffer.reset()
        except Exception:
            pass
    if hasattr(cli, "_app") and cli._app:
        cli._app.invalidate()
    cprint(f"\n{_DIM}  ⏱ 超时 — 密钥捕获取消{_RST}")
    return {
        "success": True,
        "reason": "timeout",
        "stored_as": var_name,
        "validated": False,
        "skipped": True,
        "message": "密钥设置超时并已跳过。",
    }


def approval_callback(cli, command: str, description: str) -> str:
    """通过 TUI 提示危险命令审批。

    显示带有选项的选择界面：一次 / 本次会话 / 始终 / 拒绝。
    当命令长度超过 70 个字符时，会包含一个"查看"选项，
    让用户可以在决定之前查看完整文本。

    使用 cli._approval_lock 来串行化并发请求（例如来自
    并行委托子任务），确保每个提示都能按顺序处理。
    """
    lock = getattr(cli, "_approval_lock", None)
    if lock is None:
        import threading
        cli._approval_lock = threading.Lock()
        lock = cli._approval_lock

    with lock:
        from cli import CLI_CONFIG
        timeout = CLI_CONFIG.get("approvals", {}).get("timeout", 60)
        response_queue = queue.Queue()
        choices = ["once", "session", "always", "deny"]
        if len(command) > 70:
            choices.append("view")

        cli._approval_state = {
            "command": command,
            "description": description,
            "choices": choices,
            "selected": 0,
            "response_queue": response_queue,
        }
        cli._approval_deadline = _time.monotonic() + timeout

        if hasattr(cli, "_app") and cli._app:
            cli._app.invalidate()

        while True:
            try:
                result = response_queue.get(timeout=1)
                cli._approval_state = None
                cli._approval_deadline = 0
                if hasattr(cli, "_app") and cli._app:
                    cli._app.invalidate()
                return result
            except queue.Empty:
                remaining = cli._approval_deadline - _time.monotonic()
                if remaining <= 0:
                    break
                if hasattr(cli, "_app") and cli._app:
                    cli._app.invalidate()

        cli._approval_state = None
        cli._approval_deadline = 0
        if hasattr(cli, "_app") and cli._app:
            cli._app.invalidate()
        cprint(f"\n{_DIM}  ⏱ 超时 — 拒绝命令{_RST}")
        return "deny"
