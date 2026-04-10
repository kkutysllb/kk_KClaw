"""所有 KClaw 执行环境后端的基类。

统一的每次调用生成模型：每个命令都会生成一个新的 ``bash -c`` 进程。
会话快照（环境变量、函数、别名）在初始化时捕获一次，并在每次命令前重新获取。
CWD 通过带内 stdout 标记（远程）或临时文件（本地）保持。
"""

import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Callable, Protocol

from kclaw_constants import get_kclaw_home
from tools.interrupt import is_interrupted

logger = logging.getLogger(__name__)


def get_sandbox_dir() -> Path:
    """返回所有沙箱存储的主机端根目录（Docker 工作区、
    Singularity overlay/SIF 缓存等）。

    可通过 TERMINAL_SANDBOX_DIR 配置。默认为 {KCLAW_HOME}/sandboxes/。
    """
    custom = os.getenv("TERMINAL_SANDBOX_DIR")
    if custom:
        p = Path(custom)
    else:
        p = get_kclaw_home() / "sandboxes"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# 共享常量和工具
# ---------------------------------------------------------------------------

_SYNC_INTERVAL_SECONDS = 5.0


def _pipe_stdin(proc: subprocess.Popen, data: str) -> None:
    """在守护线程上将 *data* 写入 proc.stdin 以避免管道缓冲区死锁。"""

    def _write():
        try:
            proc.stdin.write(data)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=_write, daemon=True).start()


def _popen_bash(
    cmd: list[str], stdin_data: str | None = None, **kwargs
) -> subprocess.Popen:
    """使用标准 stdout/stderr/stdin 设置生成子进程。

    如果提供了 *stdin_data*，通过 :func:`_pipe_stdin` 异步写入。
    有特殊 Popen 需求的后端（例如 local 的 ``preexec_fn``）可以绕过
    此函数直接调用 :func:`_pipe_stdin`。
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        text=True,
        **kwargs,
    )
    if stdin_data is not None:
        _pipe_stdin(proc, stdin_data)
    return proc


def _load_json_store(path: Path) -> dict:
    """将 JSON 文件加载为字典，遇到任何错误时返回 ``{}``。"""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_json_store(path: Path, data: dict) -> None:
    """将 *data* 作为格式化的 JSON 写入 *path*。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _file_mtime_key(host_path: str) -> tuple[float, int] | None:
    """返回用于缓存比较的 ``(mtime, size)``，如果不可读则返回 ``None``。"""
    try:
        st = Path(host_path).stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# ProcessHandle 协议
# ---------------------------------------------------------------------------


class ProcessHandle(Protocol):
    """每个后端的 _run_bash() 必须返回的鸭式类型。

    subprocess.Popen 原生满足此协议。SDK 后端（Modal、Daytona）
    返回 _ThreadedProcessHandle 来适配它们的阻塞调用。
    """

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...

    @property
    def stdout(self) -> IO[str] | None: ...

    @property
    def returncode(self) -> int | None: ...


class _ThreadedProcessHandle:
    """没有真实子进程的 SDK 后端（Modal、Daytona）的适配器。

    在后台线程中包装阻塞的 ``exec_fn() -> (output_str, exit_code)`` 并暴露
    ProcessHandle 兼容的接口。可选的 ``cancel_fn`` 在 ``kill()`` 时调用，
    用于特定后端的取消（例如 Modal sandbox.terminate、Daytona sandbox.stop）。
    """

    def __init__(
        self,
        exec_fn: Callable[[], tuple[str, int]],
        cancel_fn: Callable[[], None] | None = None,
    ):
        self._cancel_fn = cancel_fn
        self._done = threading.Event()
        self._returncode: int | None = None
        self._error: Exception | None = None

        # Pipe for stdout — drain thread in _wait_for_process reads the read end.
        read_fd, write_fd = os.pipe()
        self._stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        self._write_fd = write_fd

        def _worker():
            try:
                output, exit_code = exec_fn()
                self._returncode = exit_code
                # Write output into the pipe so drain thread picks it up.
                try:
                    os.write(self._write_fd, output.encode("utf-8", errors="replace"))
                except OSError:
                    pass
            except Exception as exc:
                self._error = exc
                self._returncode = 1
            finally:
                try:
                    os.close(self._write_fd)
                except OSError:
                    pass
                self._done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    @property
    def stdout(self):
        return self._stdout

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode if self._done.is_set() else None

    def kill(self):
        if self._cancel_fn:
            try:
                self._cancel_fn()
            except Exception:
                pass

    def wait(self, timeout: float | None = None) -> int:
        self._done.wait(timeout=timeout)
        return self._returncode


# ---------------------------------------------------------------------------
# 远程后端的 CWD 标记
# ---------------------------------------------------------------------------


def _cwd_marker(session_id: str) -> str:
    return f"__KCLAW_CWD_{session_id}__"


# ---------------------------------------------------------------------------
# BaseEnvironment
# ---------------------------------------------------------------------------


class BaseEnvironment(ABC):
    """所有 KClaw 后端的通用接口和统一执行流程。

    子类实现 ``_run_bash()`` 和 ``cleanup()``。基类提供带有会话快照获取、
    CWD 跟踪、中断处理和超时执行的 ``execute()``。
    """

    # 子类将 stdin 作为 heredoc 嵌入（Modal、Daytona）时设置此属性。
    _stdin_mode: str = "pipe"  # "pipe" 或 "heredoc"

    # 快照创建超时（覆盖慢冷启动）。
    _snapshot_timeout: int = 30

    def __init__(self, cwd: str, timeout: int, env: dict = None):
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}

        self._session_id = uuid.uuid4().hex[:12]
        self._snapshot_path = f"/tmp/kclaw-snap-{self._session_id}.sh"
        self._cwd_file = f"/tmp/kclaw-cwd-{self._session_id}.txt"
        self._cwd_marker = _cwd_marker(self._session_id)
        self._snapshot_ready = False
        self._last_sync_time: float | None = (
            None  # set to 0 by backends that need file sync
        )

    # ------------------------------------------------------------------
    # 抽象方法
    # ------------------------------------------------------------------

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> ProcessHandle:
        """生成一个 bash 进程来运行 *cmd_string*。

        返回 ProcessHandle（subprocess.Popen 或 _ThreadedProcessHandle）。
        必须由每个后端覆盖。
        """
        raise NotImplementedError(f"{type(self).__name__} must implement _run_bash()")

    @abstractmethod
    def cleanup(self):
        """释放后端资源（容器、实例、连接）。"""
        ...

    # ------------------------------------------------------------------
    # 会话快照 (init_session)
    # ------------------------------------------------------------------

    def init_session(self):
        """将登录 shell 环境捕获到快照文件中。

        在后端构建后调用一次。成功后，设置
        ``_snapshot_ready = True``，以便后续命令获取快照，
        而不是用 ``bash -l`` 运行。
        """
        # 完整捕获：环境变量、函数（过滤后）、别名、shell 选项。
        bootstrap = (
            f"export -p > {self._snapshot_path}\n"
            f"declare -f | grep -vE '^_[^_]' >> {self._snapshot_path}\n"
            f"alias -p >> {self._snapshot_path}\n"
            f"echo 'shopt -s expand_aliases' >> {self._snapshot_path}\n"
            f"echo 'set +e' >> {self._snapshot_path}\n"
            f"echo 'set +u' >> {self._snapshot_path}\n"
            f"pwd -P > {self._cwd_file} 2>/dev/null || true\n"
            f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"\n"
        )
        try:
            proc = self._run_bash(bootstrap, login=True, timeout=self._snapshot_timeout)
            result = self._wait_for_process(proc, timeout=self._snapshot_timeout)
            self._snapshot_ready = True
            self._update_cwd(result)
            logger.info(
                "Session snapshot created (session=%s, cwd=%s)",
                self._session_id,
                self.cwd,
            )
        except Exception as exc:
            logger.warning(
                "init_session failed (session=%s): %s — "
                "falling back to bash -l per command",
                self._session_id,
                exc,
            )
            self._snapshot_ready = False

    # ------------------------------------------------------------------
    # 命令包装
    # ------------------------------------------------------------------

    def _wrap_command(self, command: str, cwd: str) -> str:
        """构建完整的 bash 脚本，用于获取快照、cd 到目录、运行命令、
        重新转储环境变量并发出 CWD 标记。"""
        escaped = command.replace("'", "'\\''")

        parts = []

        # Source snapshot (env vars from previous commands)
        if self._snapshot_ready:
            parts.append(f"source {self._snapshot_path} 2>/dev/null || true")

        # cd to working directory — let bash expand ~ natively
        quoted_cwd = (
            shlex.quote(cwd) if cwd != "~" and not cwd.startswith("~/") else cwd
        )
        parts.append(f"cd {quoted_cwd} || exit 126")

        # Run the actual command
        parts.append(f"eval '{escaped}'")
        parts.append("__kclaw_ec=$?")

        # Re-dump env vars to snapshot (last-writer-wins for concurrent calls)
        if self._snapshot_ready:
            parts.append(f"export -p > {self._snapshot_path} 2>/dev/null || true")

        # Write CWD to file (local reads this) and stdout marker (remote parses this)
        parts.append(f"pwd -P > {self._cwd_file} 2>/dev/null || true")
        # Use a distinct line for the marker. The leading \n ensures
        # the marker starts on its own line even if the command doesn't
        # end with a newline (e.g. printf 'exact'). We'll strip this
        # injected newline in _extract_cwd_from_output.
        parts.append(
            f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\""
        )
        parts.append("exit $__kclaw_ec")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Stdin heredoc 嵌入（用于 SDK 后端）
    # ------------------------------------------------------------------

    @staticmethod
    def _embed_stdin_heredoc(command: str, stdin_data: str) -> str:
        """将 stdin_data 作为 shell heredoc 附加到命令字符串。"""
        delimiter = f"KCLAW_STDIN_{uuid.uuid4().hex[:12]}"
        return f"{command} << '{delimiter}'\n{stdin_data}\n{delimiter}"

    # ------------------------------------------------------------------
    # 进程生命周期
    # ------------------------------------------------------------------

    def _wait_for_process(self, proc: ProcessHandle, timeout: int = 120) -> dict:
        """基于轮询的等待，包含中断检查和 stdout  draining。

        所有后端共享 — 不被覆盖。
        """
        output_chunks: list[str] = []

        def _drain():
            try:
                for line in proc.stdout:
                    output_chunks.append(line)
            except UnicodeDecodeError:
                output_chunks.clear()
                output_chunks.append(
                    "[binary output detected — raw bytes not displayable]"
                )
            except (ValueError, OSError):
                pass

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()
        deadline = time.monotonic() + timeout

        while proc.poll() is None:
            if is_interrupted():
                self._kill_process(proc)
                drain_thread.join(timeout=2)
                return {
                    "output": "".join(output_chunks) + "\n[Command interrupted]",
                    "returncode": 130,
                }
            if time.monotonic() > deadline:
                self._kill_process(proc)
                drain_thread.join(timeout=2)
                partial = "".join(output_chunks)
                timeout_msg = f"\n[Command timed out after {timeout}s]"
                return {
                    "output": partial + timeout_msg
                    if partial
                    else timeout_msg.lstrip(),
                    "returncode": 124,
                }
            time.sleep(0.2)

        drain_thread.join(timeout=5)

        try:
            proc.stdout.close()
        except Exception:
            pass

        return {"output": "".join(output_chunks), "returncode": proc.returncode}

    def _kill_process(self, proc: ProcessHandle):
        """终止一个进程。子类可以覆盖以进行进程组终止。"""
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # ------------------------------------------------------------------
    # CWD 提取
    # ------------------------------------------------------------------

    def _update_cwd(self, result: dict):
        """从命令输出中提取 CWD。针对本地文件读取进行覆盖。"""
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict):
        """从 stdout 输出中解析 __KCLAW_CWD_{session}__ 标记。

        更新 self.cwd 并从 result["output"] 中剥离标记。
        由远程后端使用（Docker、SSH、Modal、Daytona、Singularity）。
        """
        output = result.get("output", "")
        marker = self._cwd_marker
        last = output.rfind(marker)
        if last == -1:
            return

        # Find the opening marker before this closing one
        search_start = max(0, last - 4096)  # CWD path won't be >4KB
        first = output.rfind(marker, search_start, last)
        if first == -1 or first == last:
            return

        cwd_path = output[first + len(marker) : last].strip()
        if cwd_path:
            self.cwd = cwd_path

        # Strip the marker line AND the \n we injected before it.
        # The wrapper emits: printf '\n__MARKER__%s__MARKER__\n'
        # So the output looks like: <cmd output>\n__MARKER__path__MARKER__\n
        # We want to remove everything from the injected \n onwards.
        line_start = output.rfind("\n", 0, first)
        if line_start == -1:
            line_start = first
        line_end = output.find("\n", last + len(marker))
        line_end = line_end + 1 if line_end != -1 else len(output)

        result["output"] = output[:line_start] + output[line_end:]

    # ------------------------------------------------------------------
    # 钩子
    # ------------------------------------------------------------------

    def _before_execute(self):
        """每次命令前的速率限制文件同步。

        需要命令前同步的后端在 ``__init__`` 中设置 ``self._last_sync_time = 0``
        并覆盖 :meth:`_sync_files`。需要额外 exec 前逻辑的后端（例如 Daytona
        沙箱重启检查）覆盖此方法并调用 ``super()._before_execute()``。
        """
        if self._last_sync_time is not None:
            now = time.monotonic()
            if now - self._last_sync_time >= _SYNC_INTERVAL_SECONDS:
                self._sync_files()
                self._last_sync_time = now

    def _sync_files(self):
        """将文件推送到远程环境。由 _before_execute 进行速率限制调用。"""
        pass

    # ------------------------------------------------------------------
    # 统一 execute()
    # ------------------------------------------------------------------

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict:
        """执行一个命令，返回 {"output": str, "returncode": int}。"""
        self._before_execute()

        exec_command, sudo_stdin = self._prepare_command(command)
        effective_timeout = timeout or self.timeout
        effective_cwd = cwd or self.cwd

        # Merge sudo stdin with caller stdin
        if sudo_stdin is not None and stdin_data is not None:
            effective_stdin = sudo_stdin + stdin_data
        elif sudo_stdin is not None:
            effective_stdin = sudo_stdin
        else:
            effective_stdin = stdin_data

        # Embed stdin as heredoc for backends that need it
        if effective_stdin and self._stdin_mode == "heredoc":
            exec_command = self._embed_stdin_heredoc(exec_command, effective_stdin)
            effective_stdin = None

        wrapped = self._wrap_command(exec_command, effective_cwd)

        # Use login shell if snapshot failed (so user's profile still loads)
        login = not self._snapshot_ready

        proc = self._run_bash(
            wrapped, login=login, timeout=effective_timeout, stdin_data=effective_stdin
        )
        result = self._wait_for_process(proc, timeout=effective_timeout)
        self._update_cwd(result)

        return result

    # ------------------------------------------------------------------
    # 共享辅助函数
    # ------------------------------------------------------------------

    def stop(self):
        """cleanup 的别名（与旧调用者兼容）。"""
        self.cleanup()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    def _prepare_command(self, command: str) -> tuple[str, str | None]:
        """如果 SUDO_PASSWORD 可用，则转换 sudo 命令。"""
        from tools.terminal_tool import _transform_sudo_command

        return _transform_sudo_command(command)

    def _timeout_result(self, timeout: int | None) -> dict:
        """命令超时时返回的标准字典。"""
        return {
            "output": f"Command timed out after {timeout or self.timeout}s",
            "returncode": 124,
        }
