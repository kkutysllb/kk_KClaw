"""
Process Registry -- In-memory registry for managed background processes.

Tracks processes spawned via terminal(background=true), providing:
  - Output buffering (rolling 200KB window)
  - Status polling and log retrieval
  - Blocking wait with interrupt support
  - Process killing
  - Crash recovery via JSON checkpoint file
  - Session-scoped tracking for gateway reset protection

Background processes execute THROUGH the environment interface -- nothing
runs on the host machine unless TERMINAL_ENV=local. For Docker, Singularity,
Modal, Daytona, and SSH backends, the command runs inside the sandbox.

Usage:
    from tools.process_registry import process_registry

    # Spawn a background process (called from terminal_tool)
    session = process_registry.spawn(env, "pytest -v", task_id="task_123")

    # Poll for status
    result = process_registry.poll(session.id)

    # Block until done
    result = process_registry.wait(session.id, timeout=300)

    # Kill it
    process_registry.kill(session.id)
"""

import json
import logging
import os
import platform
import shlex
import signal
import subprocess
import threading
import time
import uuid

_IS_WINDOWS = platform.system() == "Windows"
from tools.environments.local import _find_shell, _sanitize_subprocess_env
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kclaw_cli.config import get_kclaw_home

logger = logging.getLogger(__name__)


# 检查点文件用于崩溃恢复（仅限网关）
CHECKPOINT_PATH = get_kclaw_home() / "processes.json"

# 限制
MAX_OUTPUT_CHARS = 200_000      # 200KB 滚动输出缓冲区
FINISHED_TTL_SECONDS = 1800     # 将完成的进程保留 30 分钟
MAX_PROCESSES = 64              # 最大并发跟踪进程数（LRU 修剪）


@dataclass
class ProcessSession:
    """具有输出缓冲的跟踪后台进程。"""
    id: str                                     # 唯一会话 ID（"proc_xxxxxxxxxxxx"）
    command: str                                 # 原始命令字符串
    task_id: str = ""                           # 任务/沙箱隔离键
    session_key: str = ""                       # 网关会话键（用于重置保护）
    pid: Optional[int] = None                   # 操作系统进程 ID
    process: Optional[subprocess.Popen] = None  # Popen 句柄（仅本地）
    env_ref: Any = None                         # 环境对象引用
    cwd: Optional[str] = None                   # 工作目录
    started_at: float = 0.0                     # 生成的时间.time()
    exited: bool = False                        # 进程是否已结束
    exit_code: Optional[int] = None             # 退出代码（如果仍在运行则为 None）
    output_buffer: str = ""                     # 滚动输出（最后 MAX_OUTPUT_CHARS）
    max_output_chars: int = MAX_OUTPUT_CHARS
    detached: bool = False                      # 如果从崩溃中恢复则为 True（无管道）
    pid_scope: str = "host"                     # "host" 表示本地/PTY PID，"sandbox" 表示环境本地 PID
    # 看门狗/通知元数据（为崩溃恢复而持久化）
    watcher_platform: str = ""
    watcher_chat_id: str = ""
    watcher_thread_id: str = ""
    watcher_interval: int = 0                   # 0 = 未配置看门狗
    notify_on_complete: bool = False             # 退出时队列代理通知
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _pty: Any = field(default=None, repr=False)  # ptyprocess 句柄（当 use_pty=True 时）


class ProcessRegistry:
    """
    运行中和已完成后台进程的内存注册表。

    线程安全。访问来源：
      - 执行器线程（terminal_tool、进程工具处理程序）
      - 网关 asyncio 循环（看门狗任务、会话重置检查）
      - 清理线程（沙箱回收协调）
    """

    _SHELL_NOISE_SUBSTRINGS = (
        "bash: cannot set terminal process group",
        "bash: no job control in this shell",
        "no job control in this shell",
        "cannot set terminal process group",
        "tcsetattr: Inappropriate ioctl for device",
    )

    def __init__(self):
        self._running: Dict[str, ProcessSession] = {}
        self._finished: Dict[str, ProcessSession] = {}
        self._lock = threading.Lock()

        # Side-channel for check_interval watchers (gateway reads after agent run)
        self.pending_watchers: List[Dict[str, Any]] = []

        # Completion notifications — processes with notify_on_complete push here
        # on exit.  CLI process_loop and gateway drain this after each agent turn
        # to auto-trigger a new agent turn with the process results.
        import queue as _queue_mod
        self.completion_queue: _queue_mod.Queue = _queue_mod.Queue()

    @staticmethod
    def _clean_shell_noise(text: str) -> str:
        """从输出开头剥离 shell 启动警告。"""
        lines = text.split("\n")
        while lines and any(noise in lines[0] for noise in ProcessRegistry._SHELL_NOISE_SUBSTRINGS):
            lines.pop(0)
        return "\n".join(lines)

    @staticmethod
    def _is_host_pid_alive(pid: Optional[int]) -> bool:
        """对主机可见 PID 的尽力而为的存活检查。"""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _refresh_detached_session(self, session: Optional[ProcessSession]) -> Optional[ProcessSession]:
        """当底层进程已退出时更新恢复的主机 PID 会话。"""
        if session is None or session.exited or not session.detached or session.pid_scope != "host":
            return session

        if self._is_host_pid_alive(session.pid):
            return session

        with session._lock:
            if session.exited:
                return session
            session.exited = True
            # 恢复的会话不再有可等待的句柄，因此一旦原始进程对象消失，
            # 真正的退出代码就不可用了。
            session.exit_code = None

        self._move_to_finished(session)
        return session

    @staticmethod
    def _terminate_host_pid(pid: int) -> None:
        """终止主机可见的 PID，无需原始进程句柄。"""
        if _IS_WINDOWS:
            os.kill(pid, signal.SIGTERM)
            return

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)

    # ----- 生成 -----

    def spawn_local(
        self,
        command: str,
        cwd: str = None,
        task_id: str = "",
        session_key: str = "",
        env_vars: dict = None,
        use_pty: bool = False,
    ) -> ProcessSession:
        """
        在本地生成后台进程。

        仅用于 TERMINAL_ENV=local。其他后端使用 spawn_via_env()。

        参数:
            use_pty: 如果为 True，使用 ptyprocess 的伪终端进行交互式
                     CLI 工具（Codex、Claude Code、Python REPL）。如果 ptyprocess
                     未安装，则回退到 subprocess.Popen。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=cwd or os.getcwd(),
            started_at=time.time(),
        )

        if use_pty:
            # 尝试 PTY 模式以进行交互式 CLI 工具
            try:
                if _IS_WINDOWS:
                    from winpty import PtyProcess as _PtyProcessCls
                else:
                    from ptyprocess import PtyProcess as _PtyProcessCls
                user_shell = _find_shell()
                pty_env = _sanitize_subprocess_env(os.environ, env_vars)
                pty_env["PYTHONUNBUFFERED"] = "1"
                pty_proc = _PtyProcessCls.spawn(
                    [user_shell, "-lic", command],
                    cwd=session.cwd,
                    env=pty_env,
                    dimensions=(30, 120),
                )
                session.pid = pty_proc.pid
                # 在会话上存储 pty 句柄以进行读/写
                session._pty = pty_proc

                # PTY 读取器线程
                reader = threading.Thread(
                    target=self._pty_reader_loop,
                    args=(session,),
                    daemon=True,
                    name=f"proc-pty-reader-{session.id}",
                )
                session._reader_thread = reader
                reader.start()

                with self._lock:
                    self._prune_if_needed()
                    self._running[session.id] = session

                self._write_checkpoint()
                return session

            except ImportError:
                logger.warning("ptyprocess not installed, falling back to pipe mode")
            except Exception as e:
                logger.warning("PTY spawn failed (%s), falling back to pipe mode", e)

        # 标准 Popen 路径（非 PTY 或 PTY 回退）
        # 使用用户的登录 shell 以与 LocalEnvironment 保持一致 —
        # 确保来源 rc 文件并且用户工具可用。
        user_shell = _find_shell()
        # 强制 Python 脚本无缓冲输出，以便在后台执行时可见进度
        # （tqdm/datasets 等库在 stdout 是管道时会缓冲，
        # 隐藏来自 process(action="poll") 的输出）。
        bg_env = _sanitize_subprocess_env(os.environ, env_vars)
        bg_env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [user_shell, "-lic", command],
            text=True,
            cwd=session.cwd,
            env=bg_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )

        session.process = proc
        session.pid = proc.pid

        # 启动输出读取器线程
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            daemon=True,
            name=f"proc-reader-{session.id}",
        )
        session._reader_thread = reader
        reader.start()

        with self._lock:
            self._prune_if_needed()
            self._running[session.id] = session

        self._write_checkpoint()
        return session

    def spawn_via_env(
        self,
        env: Any,
        command: str,
        cwd: str = None,
        task_id: str = "",
        session_key: str = "",
        timeout: int = 10,
    ) -> ProcessSession:
        """
        通过非本地环境后端生成后台进程。

        对于 Docker/Singularity/Modal/Daytona/SSH：使用环境的 execute() 接口
        在沙箱内运行命令。我们包装命令以捕获沙箱内 PID 并将输出重定向到
        沙箱内的日志文件，然后通过后续的 execute() 调用轮询日志。

        这比本地生成能力较弱（没有实时 stdout 管道、没有 stdin），
        但它确保命令在正确的沙箱上下文中运行。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=cwd,
            started_at=time.time(),
            env_ref=env,
            pid_scope="sandbox",
        )

        # 在沙箱中运行命令并捕获输出
        log_path = f"/tmp/kclaw_bg_{session.id}.log"
        pid_path = f"/tmp/kclaw_bg_{session.id}.pid"
        quoted_command = shlex.quote(command)
        bg_command = (
            f"nohup bash -c {quoted_command} > {log_path} 2>&1 & "
            f"echo $! > {pid_path} && cat {pid_path}"
        )

        try:
            result = env.execute(bg_command, timeout=timeout)
            output = result.get("output", "").strip()
            # 尝试从输出中提取 PID
            for line in output.splitlines():
                line = line.strip()
                if line.isdigit():
                    session.pid = int(line)
                    break
        except Exception as e:
            session.exited = True
            session.exit_code = -1
            session.output_buffer = f"Failed to start: {e}"

        if not session.exited:
            # 启动一个轮询线程，定期读取日志文件
            reader = threading.Thread(
                target=self._env_poller_loop,
                args=(session, env, log_path, pid_path),
                daemon=True,
                name=f"proc-poller-{session.id}",
            )
            session._reader_thread = reader
            reader.start()

        with self._lock:
            self._prune_if_needed()
            self._running[session.id] = session

        self._write_checkpoint()
        return session

    # ----- 读取器/轮询器线程 -----

    def _reader_loop(self, session: ProcessSession):
        """后台线程：从本地 Popen 进程读取 stdout。"""
        first_chunk = True
        try:
            while True:
                chunk = session.process.stdout.read(4096)
                if not chunk:
                    break
                if first_chunk:
                    chunk = self._clean_shell_noise(chunk)
                    first_chunk = False
                with session._lock:
                    session.output_buffer += chunk
                    if len(session.output_buffer) > session.max_output_chars:
                        session.output_buffer = session.output_buffer[-session.max_output_chars:]
        except Exception as e:
            logger.debug("Process stdout reader ended: %s", e)

        # Process exited
        try:
            session.process.wait(timeout=5)
        except Exception as e:
            logger.debug("Process wait timed out or failed: %s", e)
        session.exited = True
        session.exit_code = session.process.returncode
        self._move_to_finished(session)

    def _env_poller_loop(
        self, session: ProcessSession, env: Any, log_path: str, pid_path: str
    ):
        """后台线程：为非本地后端轮询沙箱日志文件。"""
        while not session.exited:
            time.sleep(2)  # Poll every 2 seconds
            try:
                # 从日志文件读取新输出
                result = env.execute(f"cat {log_path} 2>/dev/null", timeout=10)
                new_output = result.get("output", "")
                if new_output:
                    with session._lock:
                        session.output_buffer = new_output
                        if len(session.output_buffer) > session.max_output_chars:
                            session.output_buffer = session.output_buffer[-session.max_output_chars:]

                # 检查进程是否仍在运行
                check = env.execute(
                    f"kill -0 $(cat {pid_path} 2>/dev/null) 2>/dev/null; echo $?",
                    timeout=5,
                )
                check_output = check.get("output", "").strip()
                if check_output and check_output.splitlines()[-1].strip() != "0":
                    # 进程已退出 — 获取退出代码
                    exit_result = env.execute(
                        f"wait $(cat {pid_path} 2>/dev/null) 2>/dev/null; echo $?",
                        timeout=5,
                    )
                    exit_str = exit_result.get("output", "").strip()
                    try:
                        session.exit_code = int(exit_str.splitlines()[-1].strip())
                    except (ValueError, IndexError):
                        session.exit_code = -1
                    session.exited = True
                    self._move_to_finished(session)
                    return

            except Exception:
                # 环境可能已消失（沙箱被回收等）
                session.exited = True
                session.exit_code = -1
                self._move_to_finished(session)
                return

    def _pty_reader_loop(self, session: ProcessSession):
        """后台线程：从 PTY 进程读取输出。"""
        pty = session._pty
        try:
            while pty.isalive():
                try:
                    chunk = pty.read(4096)
                    if chunk:
                        # ptyprocess returns bytes
                        text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                        with session._lock:
                            session.output_buffer += text
                            if len(session.output_buffer) > session.max_output_chars:
                                session.output_buffer = session.output_buffer[-session.max_output_chars:]
                except EOFError:
                    break
                except Exception:
                    break
        except Exception as e:
            logger.debug("PTY stdout reader ended: %s", e)

        # Process exited
        try:
            pty.wait()
        except Exception as e:
            logger.debug("PTY wait timed out or failed: %s", e)
        session.exited = True
        session.exit_code = pty.exitstatus if hasattr(pty, 'exitstatus') else -1
        self._move_to_finished(session)

    def _move_to_finished(self, session: ProcessSession):
        """将会话从运行中移到已完成。"""
        with self._lock:
            self._running.pop(session.id, None)
            self._finished[session.id] = session
        self._write_checkpoint()

        # 如果调用者请求了代理通知，则将完成情况加入队列，
        # 以便 CLI/网关可以自动触发新的代理轮次。
        if session.notify_on_complete:
            from tools.ansi_strip import strip_ansi
            output_tail = strip_ansi(session.output_buffer[-2000:]) if session.output_buffer else ""
            self.completion_queue.put({
                "session_id": session.id,
                "command": session.command,
                "exit_code": session.exit_code,
                "output": output_tail,
            })

    # ----- 查询方法 -----

    def get(self, session_id: str) -> Optional[ProcessSession]:
        """按 ID 获取会话（运行中或已完成）。"""
        with self._lock:
            session = self._running.get(session_id) or self._finished.get(session_id)
        return self._refresh_detached_session(session)

    def poll(self, session_id: str) -> dict:
        """检查后台进程的状态并获取新输出。"""
        from tools.ansi_strip import strip_ansi

        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        with session._lock:
            output_preview = strip_ansi(session.output_buffer[-1000:]) if session.output_buffer else ""

        result = {
            "session_id": session.id,
            "command": session.command,
            "status": "exited" if session.exited else "running",
            "pid": session.pid,
            "uptime_seconds": int(time.time() - session.started_at),
            "output_preview": output_preview,
        }
        if session.exited:
            result["exit_code"] = session.exit_code
        if session.detached:
            result["detached"] = True
            result["note"] = "进程在重启后恢复 — 输出历史不可用"
        return result

    def read_log(self, session_id: str, offset: int = 0, limit: int = 200) -> dict:
        """读取完整的输出日志，可按行分页。"""
        from tools.ansi_strip import strip_ansi

        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        with session._lock:
            full_output = strip_ansi(session.output_buffer)

        lines = full_output.splitlines()
        total_lines = len(lines)

        # 默认：最后 N 行
        if offset == 0 and limit > 0:
            selected = lines[-limit:]
        else:
            selected = lines[offset:offset + limit]

        return {
            "session_id": session.id,
            "status": "exited" if session.exited else "running",
            "output": "\n".join(selected),
            "total_lines": total_lines,
            "showing": f"{len(selected)} lines",
        }

    def wait(self, session_id: str, timeout: int = None) -> dict:
        """
        阻塞直到进程退出、超时或中断。

        参数:
            session_id: 要等待的进程。
            timeout: 最大阻塞秒数。回退到 TERMINAL_TIMEOUT 配置。

        返回:
            包含状态（"exited"、"timeout"、"interrupted"、"not_found"）
            和输出快照的字典。
        """
        from tools.ansi_strip import strip_ansi
        from tools.terminal_tool import _interrupt_event

        default_timeout = int(os.getenv("TERMINAL_TIMEOUT", "180"))
        max_timeout = default_timeout
        requested_timeout = timeout
        timeout_note = None

        if requested_timeout and requested_timeout > max_timeout:
            effective_timeout = max_timeout
            timeout_note = (
                f"Requested wait of {requested_timeout}s was clamped "
                f"to configured limit of {max_timeout}s"
            )
        else:
            effective_timeout = requested_timeout or max_timeout

        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        deadline = time.monotonic() + effective_timeout

        while time.monotonic() < deadline:
            session = self._refresh_detached_session(session)
            if session.exited:
                result = {
                    "status": "exited",
                    "exit_code": session.exit_code,
                    "output": strip_ansi(session.output_buffer[-2000:]),
                }
                if timeout_note:
                    result["timeout_note"] = timeout_note
                return result

            if _interrupt_event.is_set():
                result = {
                    "status": "interrupted",
                    "output": strip_ansi(session.output_buffer[-1000:]),
                    "note": "用户发送了新消息 — 等待被中断",
                }
                if timeout_note:
                    result["timeout_note"] = timeout_note
                return result

            time.sleep(1)

        result = {
            "status": "timeout",
            "output": strip_ansi(session.output_buffer[-1000:]),
        }
        if timeout_note:
            result["timeout_note"] = timeout_note
        else:
            result["timeout_note"] = f"等待了 {effective_timeout} 秒，进程仍在运行"
        return result

    def kill_process(self, session_id: str) -> dict:
        """终止后台进程。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        if session.exited:
            return {
                "status": "already_exited",
                "exit_code": session.exit_code,
            }

        # 通过 PTY、Popen（本地）或 env execute（非本地）终止
        try:
            if session._pty:
                # PTY 进程 — 通过 ptyprocess 终止
                try:
                    session._pty.terminate(force=True)
                except Exception:
                    if session.pid:
                        os.kill(session.pid, signal.SIGTERM)
            elif session.process:
                # 本地进程 — 终止进程组
                try:
                    if _IS_WINDOWS:
                        session.process.terminate()
                    else:
                        os.killpg(os.getpgid(session.process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    session.process.kill()
            elif session.env_ref and session.pid:
                # 非本地 — 在沙箱内终止
                session.env_ref.execute(f"kill {session.pid} 2>/dev/null", timeout=5)
            elif session.detached and session.pid_scope == "host" and session.pid:
                if not self._is_host_pid_alive(session.pid):
                    with session._lock:
                        session.exited = True
                        session.exit_code = None
                    self._move_to_finished(session)
                    return {
                        "status": "already_exited",
                        "exit_code": session.exit_code,
                    }
                self._terminate_host_pid(session.pid)
            else:
                return {
                    "status": "error",
                    "error": (
                        "Recovered process cannot be killed after restart because "
                        "its original runtime handle is no longer available"
                    ),
                }
            session.exited = True
            session.exit_code = -15  # SIGTERM
            self._move_to_finished(session)
            self._write_checkpoint()
            return {"status": "killed", "session_id": session.id}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def write_stdin(self, session_id: str, data: str) -> dict:
        """向运行中进程的 stdin 发送原始数据（不追加换行符）。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "进程已经结束"}

        # PTY 模式 — 通过 pty 句柄写入（期望字节）
        if hasattr(session, '_pty') and session._pty:
            try:
                pty_data = data.encode("utf-8") if isinstance(data, str) else data
                session._pty.write(pty_data)
                return {"status": "ok", "bytes_written": len(data)}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Popen 模式 — 通过 stdin 管道写入
        if not session.process or not session.process.stdin:
            return {"status": "error", "error": "进程 stdin 不可用（非本地后端或 stdin 已关闭）"}
        try:
            session.process.stdin.write(data)
            session.process.stdin.flush()
            return {"status": "ok", "bytes_written": len(data)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def submit_stdin(self, session_id: str, data: str = "") -> dict:
        """向运行中进程的 stdin 发送数据 + 换行符（就像按 Enter）。"""
        return self.write_stdin(session_id, data + "\n")

    def list_sessions(self, task_id: str = None) -> list:
        """列出所有运行中和最近完成的进程。"""
        with self._lock:
            all_sessions = list(self._running.values()) + list(self._finished.values())

        all_sessions = [self._refresh_detached_session(s) for s in all_sessions]

        if task_id:
            all_sessions = [s for s in all_sessions if s.task_id == task_id]

        result = []
        for s in all_sessions:
            entry = {
                "session_id": s.id,
                "command": s.command[:200],
                "cwd": s.cwd,
                "pid": s.pid,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.started_at)),
                "uptime_seconds": int(time.time() - s.started_at),
                "status": "exited" if s.exited else "running",
                "output_preview": s.output_buffer[-200:] if s.output_buffer else "",
            }
            if s.exited:
                entry["exit_code"] = s.exit_code
            if s.detached:
                entry["detached"] = True
            result.append(entry)
        return result

    # ----- 会话/任务查询（用于网关集成） -----

    def has_active_processes(self, task_id: str) -> bool:
        """检查是否存在 task_id 的活动（运行中）进程。"""
        with self._lock:
            sessions = list(self._running.values())

        for session in sessions:
            self._refresh_detached_session(session)

        with self._lock:
            return any(
                s.task_id == task_id and not s.exited
                for s in self._running.values()
            )

    def has_active_for_session(self, session_key: str) -> bool:
        """检查是否存在网关会话密钥的活动进程。"""
        with self._lock:
            sessions = list(self._running.values())

        for session in sessions:
            self._refresh_detached_session(session)

        with self._lock:
            return any(
                s.session_key == session_key and not s.exited
                for s in self._running.values()
            )

    def kill_all(self, task_id: str = None) -> int:
        """终止所有运行中的进程，可按 task_id 过滤。返回被杀死的数量。"""
        with self._lock:
            targets = [
                s for s in self._running.values()
                if (task_id is None or s.task_id == task_id) and not s.exited
            ]

        killed = 0
        for session in targets:
            result = self.kill_process(session.id)
            if result.get("status") in ("killed", "already_exited"):
                killed += 1
        return killed

    # ----- 清理/修剪 -----

    def _prune_if_needed(self):
        """如果超过 MAX_PROCESSES 则删除最旧的已完成会话。必须持有 _lock。"""
        # 首先修剪过期的已完成会话
        now = time.time()
        expired = [
            sid for sid, s in self._finished.items()
            if (now - s.started_at) > FINISHED_TTL_SECONDS
        ]
        for sid in expired:
            del self._finished[sid]

        # 如果仍然超过限制，则删除最旧的已完成会话
        total = len(self._running) + len(self._finished)
        if total >= MAX_PROCESSES and self._finished:
            oldest_id = min(self._finished, key=lambda sid: self._finished[sid].started_at)
            del self._finished[oldest_id]

    # ----- 检查点（崩溃恢复） -----

    def _write_checkpoint(self):
        """以原子方式将运行进程元数据写入检查点文件。"""
        try:
            with self._lock:
                entries = []
                for s in self._running.values():
                    if not s.exited:
                        entries.append({
                            "session_id": s.id,
                            "command": s.command,
                            "pid": s.pid,
                            "pid_scope": s.pid_scope,
                            "cwd": s.cwd,
                            "started_at": s.started_at,
                            "task_id": s.task_id,
                            "session_key": s.session_key,
                            "watcher_platform": s.watcher_platform,
                            "watcher_chat_id": s.watcher_chat_id,
                            "watcher_thread_id": s.watcher_thread_id,
                            "watcher_interval": s.watcher_interval,
                            "notify_on_complete": s.notify_on_complete,
                        })
            
            # 原子写入以避免崩溃时损坏
            from utils import atomic_json_write
            atomic_json_write(CHECKPOINT_PATH, entries)
        except Exception as e:
            logger.debug("Failed to write checkpoint file: %s", e, exc_info=True)

    def recover_from_checkpoint(self) -> int:
        """
        在网关启动时，从检查点文件探测 PID。

        返回作为分离进程恢复的数量。
        """
        if not CHECKPOINT_PATH.exists():
            return 0

        try:
            entries = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return 0

        recovered = 0
        for entry in entries:
            pid = entry.get("pid")
            if not pid:
                continue

            pid_scope = entry.get("pid_scope", "host")
            if pid_scope != "host":
                # 沙箱支持的进程只在检查点中保留沙箱内 PID，
                # 一旦原始环境句柄消失，这些对重启的主机进程就没有意义了。
                logger.info(
                    "Skipping recovery for non-host process: %s (pid=%s, scope=%s)",
                    entry.get("command", "unknown")[:60],
                    pid,
                    pid_scope,
                )
                continue

            # Check if PID is still alive
            alive = self._is_host_pid_alive(pid)

            if alive:
                session = ProcessSession(
                    id=entry["session_id"],
                    command=entry.get("command", "unknown"),
                    task_id=entry.get("task_id", ""),
                    session_key=entry.get("session_key", ""),
                    pid=pid,
                    pid_scope=pid_scope,
                    cwd=entry.get("cwd"),
                    started_at=entry.get("started_at", time.time()),
                    detached=True,  # 无法读取输出，但可以报告状态 + 终止
                    watcher_platform=entry.get("watcher_platform", ""),
                    watcher_chat_id=entry.get("watcher_chat_id", ""),
                    watcher_thread_id=entry.get("watcher_thread_id", ""),
                    watcher_interval=entry.get("watcher_interval", 0),
                    notify_on_complete=entry.get("notify_on_complete", False),
                )
                with self._lock:
                    self._running[session.id] = session
                recovered += 1
                logger.info("Recovered detached process: %s (pid=%d)", session.command[:60], pid)

                # 重新加入看门狗队列，以便网关可以恢复通知
                if session.watcher_interval > 0:
                    self.pending_watchers.append({
                        "session_id": session.id,
                        "check_interval": session.watcher_interval,
                        "session_key": session.session_key,
                        "platform": session.watcher_platform,
                        "chat_id": session.watcher_chat_id,
                        "thread_id": session.watcher_thread_id,
                        "notify_on_complete": session.notify_on_complete,
                    })

        self._write_checkpoint()

        return recovered


# 模块级单例
process_registry = ProcessRegistry()


# ---------------------------------------------------------------------------
# 注册表 -- "process" 工具模式 + 处理程序
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

PROCESS_SCHEMA = {
    "name": "process",
    "description": (
        "Manage background processes started with terminal(background=true). "
        "Actions: 'list' (show all), 'poll' (check status + new output), "
        "'log' (full output with pagination), 'wait' (block until done or timeout), "
        "'kill' (terminate), 'write' (send raw stdin data without newline), "
        "'submit' (send data + Enter, for answering prompts)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "log", "wait", "kill", "write", "submit"],
                "description": "Action to perform on background processes"
            },
            "session_id": {
                "type": "string",
                "description": "Process session ID (from terminal background output). Required for all actions except 'list'."
            },
            "data": {
                "type": "string",
                "description": "Text to send to process stdin (for 'write' and 'submit' actions)"
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to block for 'wait' action. Returns partial output on timeout.",
                "minimum": 1
            },
            "offset": {
                "type": "integer",
                "description": "Line offset for 'log' action (default: last 200 lines)"
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return for 'log' action",
                "minimum": 1
            }
        },
        "required": ["action"]
    }
}


def _handle_process(args, **kw):
    import json as _json
    task_id = kw.get("task_id")
    action = args.get("action", "")
    # Coerce to string — some models send session_id as an integer
    session_id = str(args.get("session_id", "")) if args.get("session_id") is not None else ""

    if action == "list":
        return _json.dumps({"processes": process_registry.list_sessions(task_id=task_id)}, ensure_ascii=False)
    elif action in ("poll", "log", "wait", "kill", "write", "submit"):
        if not session_id:
            return tool_error(f"session_id is required for {action}")
        if action == "poll":
            return _json.dumps(process_registry.poll(session_id), ensure_ascii=False)
        elif action == "log":
            return _json.dumps(process_registry.read_log(
                session_id, offset=args.get("offset", 0), limit=args.get("limit", 200)), ensure_ascii=False)
        elif action == "wait":
            return _json.dumps(process_registry.wait(session_id, timeout=args.get("timeout")), ensure_ascii=False)
        elif action == "kill":
            return _json.dumps(process_registry.kill_process(session_id), ensure_ascii=False)
        elif action == "write":
            return _json.dumps(process_registry.write_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        elif action == "submit":
            return _json.dumps(process_registry.submit_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
    return tool_error(f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit")


registry.register(
    name="process",
    toolset="terminal",
    schema=PROCESS_SCHEMA,
    handler=_handle_process,
    emoji="⚙️",
)
