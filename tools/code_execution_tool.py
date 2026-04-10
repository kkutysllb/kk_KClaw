#!/usr/bin/env python3
"""
代码执行工具 — 编程式工具调用 (PTC)

让 LLM 编写调用 KClaw 工具的 Python 脚本，通过 RPC
将多步工具链压缩为单个推理轮次。

架构（两种传输方式）：

  **本地后端 (UDS)：**
  1. 父进程生成带有 UDS RPC 函数的 `kclaw_tools.py` 存根模块
  2. 父进程打开 Unix 域套接字并启动 RPC 监听线程
  3. 父进程生成运行 LLM 脚本的子进程
  4. 工具调用通过 UDS 传回父进程进行分发

  **远程后端（基于文件的 RPC）：**
  1. 父进程生成带有基于文件的 RPC 存根的 `kclaw_tools.py`
  2. 父进程将两个文件发送到远程环境
  3. 脚本在终端后端内运行（Docker/SSH/Modal/Daytona 等）
  4. 工具调用作为请求文件写入；父进程上的轮询线程
     通过 env.execute() 读取它们，分发后写入响应文件
  5. 脚本轮询响应文件并继续

在这两种情况下，只有脚本的 stdout 返回给 LLM；中间
工具结果不会进入上下文窗口。

平台：仅 Linux / macOS（本地使用 Unix 域套接字）。在 Windows 上禁用。
远程执行还需要终端后端中的 Python 3。
"""

import base64
import json
import logging
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

_IS_WINDOWS = platform.system() == "Windows"
from typing import Any, Dict, List, Optional

# 可用性门控：UDS 需要 POSIX 操作系统
logger = logging.getLogger(__name__)

SANDBOX_AVAILABLE = sys.platform != "win32"

# 沙盒内允许的 7 个工具。此列表与会话启用工具的交集
# 决定了哪些存根被生成。
SANDBOX_ALLOWED_TOOLS = frozenset([
    "web_search",
    "web_extract",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "terminal",
])

# 资源限制默认值（可通过 config.yaml → code_execution.* 覆盖）
DEFAULT_TIMEOUT = 300        # 5 分钟
DEFAULT_MAX_TOOL_CALLS = 50
MAX_STDOUT_BYTES = 50_000    # 50 KB
MAX_STDERR_BYTES = 10_000    # 10 KB


def check_sandbox_requirements() -> bool:
    """代码执行沙盒需要 POSIX 操作系统以支持 Unix 域套接字。"""
    return SANDBOX_AVAILABLE


# ---------------------------------------------------------------------------
# kclaw_tools.py 代码生成器
# ---------------------------------------------------------------------------

# 每个工具的存根模板：(function_name, signature, docstring, args_dict_expr)
# args_dict_expr 构建通过 RPC 套接字发送的 JSON 负载。
_TOOL_STUBS = {
    "web_search": (
        "web_search",
        "query: str, limit: int = 5",
        '"""Search the web. Returns dict with data.web list of {url, title, description}."""',
        '{"query": query, "limit": limit}',
    ),
    "web_extract": (
        "web_extract",
        "urls: list",
        '"""Extract content from URLs. Returns dict with results list of {url, title, content, error}."""',
        '{"urls": urls}',
    ),
    "read_file": (
        "read_file",
        "path: str, offset: int = 1, limit: int = 500",
        '"""Read a file (1-indexed lines). Returns dict with "content" and "total_lines"."""',
        '{"path": path, "offset": offset, "limit": limit}',
    ),
    "write_file": (
        "write_file",
        "path: str, content: str",
        '"""Write content to a file (always overwrites). Returns dict with status."""',
        '{"path": path, "content": content}',
    ),
    "search_files": (
        "search_files",
        'pattern: str, target: str = "content", path: str = ".", file_glob: str = None, limit: int = 50, offset: int = 0, output_mode: str = "content", context: int = 0',
        '"""Search file contents (target="content") or find files by name (target="files"). Returns dict with "matches"."""',
        '{"pattern": pattern, "target": target, "path": path, "file_glob": file_glob, "limit": limit, "offset": offset, "output_mode": output_mode, "context": context}',
    ),
    "patch": (
        "patch",
        'path: str = None, old_string: str = None, new_string: str = None, replace_all: bool = False, mode: str = "replace", patch: str = None',
        '"""Targeted find-and-replace (mode="replace") or V4A multi-file patches (mode="patch"). Returns dict with status."""',
        '{"path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all, "mode": mode, "patch": patch}',
    ),
    "terminal": (
        "terminal",
        "command: str, timeout: int = None, workdir: str = None",
        '"""Run a shell command (foreground only). Returns dict with "output" and "exit_code"."""',
        '{"command": command, "timeout": timeout, "workdir": workdir}',
    ),
}


def generate_kclaw_tools_module(enabled_tools: List[str],
                                 transport: str = "uds") -> str:
    """
    构建 kclaw_tools.py 存根模块的源代码。

    只有同时在 SANDBOX_ALLOWED_TOOLS 和 enabled_tools 中的工具才会生成存根。

    参数:
        enabled_tools: 当前会话中启用的工具名称。
        transport: ``"uds"`` 用于 Unix 域套接字（本地后端）或
                   ``"file"`` 用于基于文件的 RPC（远程后端）。
    """
    tools_to_generate = sorted(SANDBOX_ALLOWED_TOOLS & set(enabled_tools))

    stub_functions = []
    export_names = []
    for tool_name in tools_to_generate:
        if tool_name not in _TOOL_STUBS:
            continue
        func_name, sig, doc, args_expr = _TOOL_STUBS[tool_name]
        stub_functions.append(
            f"def {func_name}({sig}):\n"
            f"    {doc}\n"
            f"    return _call({func_name!r}, {args_expr})\n"
        )
        export_names.append(func_name)

    if transport == "file":
        header = _FILE_TRANSPORT_HEADER
    else:
        header = _UDS_TRANSPORT_HEADER

    return header + "\n".join(stub_functions)


# ---- Shared helpers section (embedded in both transport headers) ----------

_COMMON_HELPERS = '''\

# ---------------------------------------------------------------------------
# 方便辅助函数（避免常见脚本陷阱）
# ---------------------------------------------------------------------------

def json_parse(text: str):
    """解析 JSON，对控制字符宽容（strict=False）。
    在解析来自 terminal() 或 web_extract() 的输出时使用此函数，
    而不是 json.loads()，因为这些输出可能在字符串中包含原始制表符/换行符。"""
    return json.loads(text, strict=False)


def shell_quote(s: str) -> str:
    """对字符串进行 shell 转义，以便安全地插入命令中。
    在将动态内容插入 terminal() 命令时使用：
        terminal(f"echo {shell_quote(user_input)}")
    """
    return shlex.quote(s)


def retry(fn, max_attempts=3, delay=2):
    """使用指数退避重试函数最多 max_attempts 次。
    用于临时故障（网络错误、API 速率限制）：
        result = retry(lambda: terminal("gh issue list ..."))
    """
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err

'''

# ---- UDS 传输（本地后端） ---------------------------------------

_UDS_TRANSPORT_HEADER = '''\
"""自动生成的 KClaw 工具 RPC 存根。"""
import json, os, socket, shlex, time

_sock = None
''' + _COMMON_HELPERS + '''\

def _connect():
    global _sock
    if _sock is None:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _sock.connect(os.environ["KCLAW_RPC_SOCKET"])
        _sock.settimeout(300)
    return _sock

def _call(tool_name, args):
    """向父进程发送工具调用并返回解析后的结果。"""
    conn = _connect()
    request = json.dumps({"tool": tool_name, "args": args}) + "\\n"
    conn.sendall(request.encode())
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            raise RuntimeError("Agent process disconnected")
        buf += chunk
        if buf.endswith(b"\\n"):
            break
    raw = buf.decode().strip()
    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''

# ---- 基于文件的传输（远程后端） -------------------------------

_FILE_TRANSPORT_HEADER = '''\
"""自动生成的 KClaw 工具 RPC 存根（基于文件的传输）。"""
import json, os, shlex, time

_RPC_DIR = os.environ.get("KCLAW_RPC_DIR", "/tmp/kclaw_rpc")
_seq = 0
''' + _COMMON_HELPERS + '''\

def _call(tool_name, args):
    """通过基于文件的 RPC 发送工具调用请求并等待响应。"""
    global _seq
    _seq += 1
    seq_str = f"{_seq:06d}"
    req_file = os.path.join(_RPC_DIR, f"req_{seq_str}")
    res_file = os.path.join(_RPC_DIR, f"res_{seq_str}")

    # 原子化写入请求（写入 .tmp，然后重命名）
    tmp = req_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"tool": tool_name, "args": args, "seq": _seq}, f)
    os.rename(tmp, req_file)

    # 使用自适应轮询等待响应
    deadline = time.monotonic() + 300  # 每个工具调用 5 分钟超时
    poll_interval = 0.05  # 从 50ms 开始
    while not os.path.exists(res_file):
        if time.monotonic() > deadline:
            raise RuntimeError(f"RPC 超时：{tool_name} 在 300 秒后无响应")
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.2, 0.25)  # 退避至 250ms

    with open(res_file) as f:
        raw = f.read()

    # 清理响应文件
    try:
        os.unlink(res_file)
    except OSError:
        pass

    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''


# ---------------------------------------------------------------------------
# RPC 服务器（在父进程内的线程中运行）
# ---------------------------------------------------------------------------

# 临时沙盒脚本中必须禁用的终端参数
_TERMINAL_BLOCKED_PARAMS = {"background", "check_interval", "pty", "notify_on_complete"}


def _rpc_server_loop(
    server_sock: socket.socket,
    task_id: str,
    tool_call_log: list,
    tool_call_counter: list,   # 可变的 [int]，以便线程可以递增
    max_tool_calls: int,
    allowed_tools: frozenset,
):
    """
    接受一个客户端连接并分发工具调用请求，直到
    客户端断开连接或达到调用限制。
    """

    from model_tools import handle_function_call

    conn = None
    try:
        server_sock.settimeout(5)
        conn, _ = server_sock.accept()
        conn.settimeout(300)

        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk

            # 处理缓冲区中所有完整的换行符分隔消息
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue

                call_start = time.monotonic()
                try:
                    request = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    resp = tool_error(f"无效的 RPC 请求：{exc}")
                    conn.sendall((resp + "\n").encode())
                    continue

                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})

                # 强制执行允许列表
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    resp = json.dumps({
                        "error": (
                            f"Tool '{tool_name}' is not available in execute_code. "
                            f"Available: {available}"
                        )
                    })
                    conn.sendall((resp + "\n").encode())
                    continue

                # 强制执行工具调用限制
                if tool_call_counter[0] >= max_tool_calls:
                    resp = json.dumps({
                        "error": (
                            f"Tool call limit reached ({max_tool_calls}). "
                            "No more tool calls allowed in this execution."
                        )
                    })
                    conn.sendall((resp + "\n").encode())
                    continue

                # 剥离禁用的终端参数
                if tool_name == "terminal" and isinstance(tool_args, dict):
                    for param in _TERMINAL_BLOCKED_PARAMS:
                        tool_args.pop(param, None)

                # 通过标准工具处理程序分发。
                # 抑制内部工具处理程序的 stdout/stderr，
                # 以便它们的状态打印不会泄漏到 CLI 旋转器中。
                try:
                    _real_stdout, _real_stderr = sys.stdout, sys.stderr
                    devnull = open(os.devnull, "w")
                    try:
                        sys.stdout = devnull
                        sys.stderr = devnull
                        result = handle_function_call(
                            tool_name, tool_args, task_id=task_id
                        )
                    finally:
                        sys.stdout, sys.stderr = _real_stdout, _real_stderr
                        devnull.close()
                except Exception as exc:
                    logger.error("Tool call failed in sandbox: %s", exc, exc_info=True)
                    result = tool_error(str(exc))

                tool_call_counter[0] += 1
                call_duration = time.monotonic() - call_start

                # 记录以便于观察
                args_preview = str(tool_args)[:80]
                tool_call_log.append({
                    "tool": tool_name,
                    "args_preview": args_preview,
                    "duration": round(call_duration, 2),
                })

                conn.sendall((result + "\n").encode())

    except socket.timeout:
        logger.debug("RPC listener socket timeout")
    except OSError as e:
        logger.debug("RPC listener socket error: %s", e, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except OSError as e:
                logger.debug("RPC conn close error: %s", e)


# ---------------------------------------------------------------------------
# 远程执行支持（通过终端后端的基于文件的 RPC）
# ---------------------------------------------------------------------------

def _get_or_create_env(task_id: str):
    """获取或创建 *task_id* 的终端环境。

    重用终端和文件工具使用的相同环境（容器/沙盒/SSH 会话），
    如果尚不存在则创建一个。返回 ``(env, env_type)`` 元组。
    """
    from tools.terminal_tool import (
        _active_environments, _env_lock, _create_environment,
        _get_env_config, _last_activity, _start_cleanup_thread,
        _creation_locks, _creation_locks_lock, _task_env_overrides,
    )

    effective_task_id = task_id or "default"

    # 快速路径：环境已存在
    with _env_lock:
        if effective_task_id in _active_environments:
            _last_activity[effective_task_id] = time.time()
            return _active_environments[effective_task_id], _get_env_config()["env_type"]

    # 慢速路径：创建环境（与 file_tools._get_file_ops 相同的模式）
    with _creation_locks_lock:
        if effective_task_id not in _creation_locks:
            _creation_locks[effective_task_id] = threading.Lock()
        task_lock = _creation_locks[effective_task_id]

    with task_lock:
        with _env_lock:
            if effective_task_id in _active_environments:
                _last_activity[effective_task_id] = time.time()
                return _active_environments[effective_task_id], _get_env_config()["env_type"]

        config = _get_env_config()
        env_type = config["env_type"]
        overrides = _task_env_overrides.get(effective_task_id, {})

        if env_type == "docker":
            image = overrides.get("docker_image") or config["docker_image"]
        elif env_type == "singularity":
            image = overrides.get("singularity_image") or config["singularity_image"]
        elif env_type == "modal":
            image = overrides.get("modal_image") or config["modal_image"]
        elif env_type == "daytona":
            image = overrides.get("daytona_image") or config["daytona_image"]
        else:
            image = ""

        cwd = overrides.get("cwd") or config["cwd"]

        container_config = None
        if env_type in ("docker", "singularity", "modal", "daytona"):
            container_config = {
                "container_cpu": config.get("container_cpu", 1),
                "container_memory": config.get("container_memory", 5120),
                "container_disk": config.get("container_disk", 51200),
                "container_persistent": config.get("container_persistent", True),
                "docker_volumes": config.get("docker_volumes", []),
            }

        ssh_config = None
        if env_type == "ssh":
            ssh_config = {
                "host": config.get("ssh_host", ""),
                "user": config.get("ssh_user", ""),
                "port": config.get("ssh_port", 22),
                "key": config.get("ssh_key", ""),
                "persistent": config.get("ssh_persistent", False),
            }

        local_config = None
        if env_type == "local":
            local_config = {
                "persistent": config.get("local_persistent", False),
            }

        logger.info("为 execute_code 任务 %s 创建新的 %s 环境...",
                     effective_task_id[:8], env_type)
        env = _create_environment(
            env_type=env_type,
            image=image,
            cwd=cwd,
            timeout=config["timeout"],
            ssh_config=ssh_config,
            container_config=container_config,
            local_config=local_config,
            task_id=effective_task_id,
            host_cwd=config.get("host_cwd"),
        )

        with _env_lock:
            _active_environments[effective_task_id] = env
            _last_activity[effective_task_id] = time.time()

        _start_cleanup_thread()
        logger.info("%s 环境已就绪，可用于 execute_code 任务 %s",
                     env_type, effective_task_id[:8])
        return env, env_type


def _ship_file_to_remote(env, remote_path: str, content: str) -> None:
    """将 *content* 写入远程环境上的 *remote_path*。

    使用 ``echo … | base64 -d`` 而不是 stdin 管道，因为某些
    后端（Modal）不能可靠地将 stdin_data 传递到链式
    命令。Base64 输出是 shell 安全的（[A-Za-z0-9+/=]），
    因此可以使用单引号。
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    env.execute(
        f"echo '{encoded}' | base64 -d > {remote_path}",
        cwd="/",
        timeout=30,
    )


def _rpc_poll_loop(
    env,
    rpc_dir: str,
    task_id: str,
    tool_call_log: list,
    tool_call_counter: list,
    max_tool_calls: int,
    allowed_tools: frozenset,
    stop_event: threading.Event,
):
    """轮询远程文件系统中的工具调用请求并分发它们。

    在后台线程中运行。每个 ``env.execute()`` 生成一个
    独立进程，因此这些调用可以与脚本执行线程安全地并发运行。
    """
    from model_tools import handle_function_call

    poll_interval = 0.1  # 100 毫秒

    while not stop_event.is_set():
        try:
            # 列出待处理的请求文件（跳过 .tmp 部分文件）
            ls_result = env.execute(
                f"ls -1 {rpc_dir}/req_* 2>/dev/null || true",
                cwd="/",
                timeout=10,
            )
            output = ls_result.get("output", "").strip()
            if not output:
                stop_event.wait(poll_interval)
                continue

            req_files = sorted([
                f.strip() for f in output.split("\n")
                if f.strip()
                and not f.strip().endswith(".tmp")
                and "/req_" in f.strip()
            ])

            for req_file in req_files:
                if stop_event.is_set():
                    break

                call_start = time.monotonic()

                # 读取请求
                read_result = env.execute(
                    f"cat {req_file}",
                    cwd="/",
                    timeout=10,
                )
                try:
                    request = json.loads(read_result.get("output", ""))
                except (json.JSONDecodeError, ValueError):
                    logger.debug("%s 中的格式错误的 RPC 请求", req_file)
                    # 删除坏的请求以避免无限重试
                    env.execute(f"rm -f {req_file}", cwd="/", timeout=5)
                    continue

                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})
                seq = request.get("seq", 0)
                seq_str = f"{seq:06d}"
                res_file = f"{rpc_dir}/res_{seq_str}"

                # 强制执行允许列表
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    tool_result = json.dumps({
                        "error": (
                            f"Tool '{tool_name}' is not available in execute_code. "
                            f"Available: {available}"
                        )
                    })
                # 强制执行工具调用限制
                elif tool_call_counter[0] >= max_tool_calls:
                    tool_result = json.dumps({
                        "error": (
                            f"Tool call limit reached ({max_tool_calls}). "
                            "No more tool calls allowed in this execution."
                        )
                    })
                else:
                    # 剥离禁用的终端参数
                    if tool_name == "terminal" and isinstance(tool_args, dict):
                        for param in _TERMINAL_BLOCKED_PARAMS:
                            tool_args.pop(param, None)

                    # 通过标准工具处理程序分发
                    try:
                        _real_stdout, _real_stderr = sys.stdout, sys.stderr
                        devnull = open(os.devnull, "w")
                        try:
                            sys.stdout = devnull
                            sys.stderr = devnull
                            tool_result = handle_function_call(
                                tool_name, tool_args, task_id=task_id
                            )
                        finally:
                            sys.stdout, sys.stderr = _real_stdout, _real_stderr
                            devnull.close()
                    except Exception as exc:
                        logger.error("Tool call failed in remote sandbox: %s",
                                     exc, exc_info=True)
                        tool_result = tool_error(str(exc))

                    tool_call_counter[0] += 1
                    call_duration = time.monotonic() - call_start
                    tool_call_log.append({
                        "tool": tool_name,
                        "args_preview": str(tool_args)[:80],
                        "duration": round(call_duration, 2),
                    })

                # 原子化写入响应（tmp + rename）。
                # 使用 echo 管道（不是 stdin_data），因为 Modal 不能
                # 可靠地将 stdin 传递到链式命令。
                encoded_result = base64.b64encode(
                    tool_result.encode("utf-8")
                ).decode("ascii")
                env.execute(
                    f"echo '{encoded_result}' | base64 -d > {res_file}.tmp"
                    f" && mv {res_file}.tmp {res_file}",
                    cwd="/",
                    timeout=60,
                )

                # 删除请求文件
                env.execute(f"rm -f {req_file}", cwd="/", timeout=5)

        except Exception as e:
            if not stop_event.is_set():
                logger.debug("RPC poll error: %s", e, exc_info=True)

        if not stop_event.is_set():
            stop_event.wait(poll_interval)


def _execute_remote(
    code: str,
    task_id: Optional[str],
    enabled_tools: Optional[List[str]],
) -> str:
    """通过基于文件的 RPC 在远程终端后端上运行脚本。

    脚本和生成的 kclaw_tools.py 模块被发送到
    远程环境，工具调用通过轮询线程代理，
    该线程通过请求/响应文件进行通信。
    """

    _cfg = _load_config()
    timeout = _cfg.get("timeout", DEFAULT_TIMEOUT)
    max_tool_calls = _cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)

    session_tools = set(enabled_tools) if enabled_tools else set()
    sandbox_tools = frozenset(SANDBOX_ALLOWED_TOOLS & session_tools)
    if not sandbox_tools:
        sandbox_tools = SANDBOX_ALLOWED_TOOLS

    effective_task_id = task_id or "default"
    env, env_type = _get_or_create_env(effective_task_id)

    sandbox_id = uuid.uuid4().hex[:12]
    sandbox_dir = f"/tmp/kclaw_exec_{sandbox_id}"

    tool_call_log: list = []
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    rpc_thread = None

    try:
        # 验证远程上是否可用 Python
        py_check = env.execute(
            "command -v python3 >/dev/null 2>&1 && echo OK",
            cwd="/", timeout=15,
        )
        if "OK" not in py_check.get("output", ""):
            return json.dumps({
                "status": "error",
                "error": (
                    f"Python 3 在 {env_type} 终端环境中不可用。"
                    "请安装 Python 以使用 execute_code 与远程后端。"
                ),
                "tool_calls_made": 0,
                "duration_seconds": 0,
            })

        # 在远程上创建沙盒目录
        env.execute(
            f"mkdir -p {sandbox_dir}/rpc", cwd="/", timeout=10,
        )

        # 生成并发送文件
        tools_src = generate_kclaw_tools_module(
            list(sandbox_tools), transport="file",
        )
        _ship_file_to_remote(env, f"{sandbox_dir}/kclaw_tools.py", tools_src)
        _ship_file_to_remote(env, f"{sandbox_dir}/script.py", code)

        # 启动 RPC 轮询线程
        rpc_thread = threading.Thread(
            target=_rpc_poll_loop,
            args=(
                env, f"{sandbox_dir}/rpc", effective_task_id,
                tool_call_log, tool_call_counter, max_tool_calls,
                sandbox_tools, stop_event,
            ),
            daemon=True,
        )
        rpc_thread.start()

        # 为脚本构建环境变量前缀
        env_prefix = (
            f"KCLAW_RPC_DIR={sandbox_dir}/rpc "
            f"PYTHONDONTWRITEBYTECODE=1"
        )
        tz = os.getenv("KCLAW_TIMEZONE", "").strip()
        if tz:
            env_prefix += f" TZ={tz}"

        # 在远程后端上执行脚本
        logger.info("在 %s 后端上执行代码（任务 %s）...",
                     env_type, effective_task_id[:8])
        script_result = env.execute(
            f"cd {sandbox_dir} && {env_prefix} python3 script.py",
            timeout=timeout,
        )

        stdout_text = script_result.get("output", "")
        exit_code = script_result.get("returncode", -1)
        status = "success"

        # 检查来自后端的超时/中断
        if exit_code == 124:
            status = "timeout"
        elif exit_code == 130:
            status = "interrupted"

    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code remote failed after %ss with %d tool calls: %s: %s",
            duration, tool_call_counter[0], type(exc).__name__, exc,
            exc_info=True,
        )
        return json.dumps({
            "status": "error",
            "error": str(exc),
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }, ensure_ascii=False)

    finally:
        # 停止轮询线程
        stop_event.set()
        if rpc_thread is not None:
            rpc_thread.join(timeout=5)

        # 清理远程沙盒目录
        try:
            env.execute(
                f"rm -rf {sandbox_dir}", cwd="/", timeout=15,
            )
        except Exception:
            logger.debug("Failed to clean up remote sandbox %s", sandbox_dir)

    duration = round(time.monotonic() - exec_start, 2)

    # --- 后处理输出（与本地路径相同） ---

    # 截断 stdout 以限制大小
    if len(stdout_text) > MAX_STDOUT_BYTES:
        head_bytes = int(MAX_STDOUT_BYTES * 0.4)
        tail_bytes = MAX_STDOUT_BYTES - head_bytes
        head = stdout_text[:head_bytes]
        tail = stdout_text[-tail_bytes:]
        omitted = len(stdout_text) - len(head) - len(tail)
        stdout_text = (
            head
            + f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted "
            f"out of {len(stdout_text):,} total] ...\n\n"
            + tail
        )

    # 剥离 ANSI 转义序列
    from tools.ansi_strip import strip_ansi
    stdout_text = strip_ansi(stdout_text)

    # 编辑秘密信息
    from agent.redact import redact_sensitive_text
    stdout_text = redact_sensitive_text(stdout_text)

    # 构建响应
    result: Dict[str, Any] = {
        "status": status,
        "output": stdout_text,
        "tool_calls_made": tool_call_counter[0],
        "duration_seconds": duration,
    }

    if status == "timeout":
        result["error"] = f"Script timed out after {timeout}s and was killed."
    elif status == "interrupted":
        result["output"] = (
            stdout_text + "\n[execution interrupted — user sent a new message]"
        )
    elif exit_code != 0:
        result["status"] = "error"
        result["error"] = f"Script exited with code {exit_code}"

    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 主入口点
# ---------------------------------------------------------------------------

def execute_code(
    code: str,
    task_id: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
) -> str:
    """
    在沙盒子进程中运行 Python 脚本，通过 RPC 访问部分 KClaw 工具。

    根据配置的终端后端，分发到本地（UDS）或远程（基于文件的 RPC）路径。

    参数:
        code:          要执行的 Python 源代码。
        task_id:       用于工具隔离的会话任务 ID（终端环境等）。
        enabled_tools: 当前会话中启用的工具名称。沙盒获取与 SANDBOX_ALLOWED_TOOLS 的交集。

    返回:
        包含执行结果的 JSON 字符串。
    """
    if not SANDBOX_AVAILABLE:
        return json.dumps({
            "error": "execute_code is not available on Windows. Use normal tool calls instead."
        })

    if not code or not code.strip():
        return tool_error("No code provided.")

    # 分发：远程后端使用基于文件的 RPC，本地使用 UDS
    from tools.terminal_tool import _get_env_config
    env_type = _get_env_config()["env_type"]
    if env_type != "local":
        return _execute_remote(code, task_id, enabled_tools)

    # --- 本地执行路径（UDS）--- 此行以下未更改 ---

    # 从 terminal_tool 导入中断事件（协作取消）
    from tools.terminal_tool import _interrupt_event

    # 解析配置
    _cfg = _load_config()
    timeout = _cfg.get("timeout", DEFAULT_TIMEOUT)
    max_tool_calls = _cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)

    # 确定沙盒可以调用哪些工具
    session_tools = set(enabled_tools) if enabled_tools else set()
    sandbox_tools = frozenset(SANDBOX_ALLOWED_TOOLS & session_tools)

    if not sandbox_tools:
        sandbox_tools = SANDBOX_ALLOWED_TOOLS

    # --- 设置包含 kclaw_tools.py 和 script.py 的临时目录 ---
    tmpdir = tempfile.mkdtemp(prefix="kclaw_sandbox_")
    # 在 macOS 上使用 /tmp 以避免长的 /var/folders/... 路径，
    # 该路径会使 Unix 域套接字路径超过 macOS AF_UNIX 的 104 字节限制。
    # 在 Linux 上，tempfile.gettempdir() 已经返回 /tmp。
    _sock_tmpdir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    sock_path = os.path.join(_sock_tmpdir, f"kclaw_rpc_{uuid.uuid4().hex}.sock")

    tool_call_log: list = []
    tool_call_counter = [0]  # 可变的，以便 RPC 线程可以递增
    exec_start = time.monotonic()
    server_sock = None

    try:
        # 写入自动生成的 kclaw_tools 模块
        # sandbox_tools 已经是正确的集合（与会话工具的交集，
        # 或 SANDBOX_ALLOWED_TOOLS 作为回退 — 见上面的行）。
        tools_src = generate_kclaw_tools_module(list(sandbox_tools))
        with open(os.path.join(tmpdir, "kclaw_tools.py"), "w") as f:
            f.write(tools_src)

        # 写入用户的脚本
        with open(os.path.join(tmpdir, "script.py"), "w") as f:
            f.write(code)

        # --- 启动 UDS 服务器 ---
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(sock_path)
        server_sock.listen(1)

        rpc_thread = threading.Thread(
            target=_rpc_server_loop,
            args=(
                server_sock, task_id, tool_call_log,
                tool_call_counter, max_tool_calls, sandbox_tools,
            ),
            daemon=True,
        )
        rpc_thread.start()

        # --- 生成子进程 ---
        # 为子进程构建最小环境。我们有意排除
        # API 密钥和令牌，以防止 LLM 生成脚本中的凭证泄露。
        # 子进程通过 RPC 访问工具，而不是直接 API。
        # 例外：通过 env_passthrough 注册表声明的加载技能的环境变量
        # 或用户在 config.yaml（terminal.env_passthrough）中明确允许的环境变量会被传递。
        _SAFE_ENV_PREFIXES = ("PATH", "HOME", "USER", "LANG", "LC_", "TERM",
                              "TMPDIR", "TMP", "TEMP", "SHELL", "LOGNAME",
                              "XDG_", "PYTHONPATH", "VIRTUAL_ENV", "CONDA")
        _SECRET_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
                              "PASSWD", "AUTH")
        try:
            from tools.env_passthrough import is_env_passthrough as _is_passthrough
        except Exception:
            _is_passthrough = lambda _: False  # noqa: E731
        child_env = {}
        for k, v in os.environ.items():
            # 直通变量（技能声明或用户配置的）始终通过。
            if _is_passthrough(k):
                child_env[k] = v
                continue
            # 阻止具有类似秘密名称的变量。
            if any(s in k.upper() for s in _SECRET_SUBSTRINGS):
                continue
            # 允许具有已知安全前缀的变量。
            if any(k.startswith(p) for p in _SAFE_ENV_PREFIXES):
                child_env[k] = v
        child_env["KCLAW_RPC_SOCKET"] = sock_path
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        # 确保 kclaw 根目录在沙盒中可导入，
        # 以便 repo-root 模块可用于子脚本。
        _kclaw_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _existing_pp = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = _kclaw_root + (os.pathsep + _existing_pp if _existing_pp else "")
        # 注入用户配置的时区，以便沙盒中代码的 datetime.now()
        # 反映正确的挂钟时间。
        _tz_name = os.getenv("KCLAW_TIMEZONE", "").strip()
        if _tz_name:
            child_env["TZ"] = _tz_name

        proc = subprocess.Popen(
            [sys.executable, "script.py"],
            cwd=tmpdir,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )

        # --- 轮询循环：监视退出、超时和中断 ---
        deadline = time.monotonic() + timeout
        stderr_chunks: list = []

        # 后台读取器以避免管道缓冲区死锁。
        # 对于 stdout，我们使用 head+tail 策略：保留第一个 HEAD_BYTES
        # 和最后一个 TAIL_BYTES 的滚动窗口，以便最终的 print()
        # 输出永远不会丢失。Stderr 仅保留头部（错误较早出现）。
        _STDOUT_HEAD_BYTES = int(MAX_STDOUT_BYTES * 0.4)   # 40% 头部
        _STDOUT_TAIL_BYTES = MAX_STDOUT_BYTES - _STDOUT_HEAD_BYTES  # 60% 尾部

        def _drain(pipe, chunks, max_bytes):
            """简单的仅头部排空（用于 stderr）。"""
            total = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    if total < max_bytes:
                        keep = max_bytes - total
                        chunks.append(data[:keep])
                    total += len(data)
            except (ValueError, OSError) as e:
                logger.debug("Error reading process output: %s", e, exc_info=True)

        stdout_total_bytes = [0]  # 用于跟踪所见字节总数的可变引用

        def _drain_head_tail(pipe, head_chunks, tail_chunks, head_bytes, tail_bytes, total_ref):
            """排空 stdout，同时保留头部和尾部数据。"""
            head_collected = 0
            from collections import deque
            tail_buf = deque()
            tail_collected = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    total_ref[0] += len(data)
                    # 首先填充头部缓冲区
                    if head_collected < head_bytes:
                        keep = min(len(data), head_bytes - head_collected)
                        head_chunks.append(data[:keep])
                        head_collected += keep
                        data = data[keep:]  # 剩余部分进入尾部
                        if not data:
                            continue
                    # 头部之后的所有内容进入滚动尾部缓冲区
                    tail_buf.append(data)
                    tail_collected += len(data)
                    # 驱逐旧的尾部数据以保持在 tail_bytes 预算内
                    while tail_collected > tail_bytes and tail_buf:
                        oldest = tail_buf.popleft()
                        tail_collected -= len(oldest)
            except (ValueError, OSError):
                pass
            # 将最终尾部转移到输出列表
            tail_chunks.extend(tail_buf)

        stdout_head_chunks: list = []
        stdout_tail_chunks: list = []

        stdout_reader = threading.Thread(
            target=_drain_head_tail,
            args=(proc.stdout, stdout_head_chunks, stdout_tail_chunks,
                  _STDOUT_HEAD_BYTES, _STDOUT_TAIL_BYTES, stdout_total_bytes),
            daemon=True
        )
        stderr_reader = threading.Thread(
            target=_drain, args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES), daemon=True
        )
        stdout_reader.start()
        stderr_reader.start()

        status = "success"
        while proc.poll() is None:
            if _interrupt_event.is_set():
                _kill_process_group(proc)
                status = "interrupted"
                break
            if time.monotonic() > deadline:
                _kill_process_group(proc, escalate=True)
                status = "timeout"
                break
            time.sleep(0.2)

        # 等待读取器完成排空
        stdout_reader.join(timeout=3)
        stderr_reader.join(timeout=3)

        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        # 组装带有 head+tail 截断的 stdout
        total_stdout = stdout_total_bytes[0]
        if total_stdout > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total_stdout - len(stdout_head) - len(stdout_tail)
            truncated_notice = (
                f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted "
                f"out of {total_stdout:,} total] ...\n\n"
            )
            stdout_text = stdout_head + truncated_notice + stdout_tail
        else:
            stdout_text = stdout_head + stdout_tail

        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = round(time.monotonic() - exec_start, 2)

        # 等待 RPC 线程完成
        server_sock.close()  # 中断 accept() 以便线程立即退出
        server_sock = None  # 防止在 finally 中重复关闭
        rpc_thread.join(timeout=3)

        # 剥离 ANSI 转义序列，以便模型永远不会看到终端
        # 格式 — 防止它将转义序列复制到文件写入中。
        from tools.ansi_strip import strip_ansi
        stdout_text = strip_ansi(stdout_text)
        stderr_text = strip_ansi(stderr_text)

        # 从沙盒输出中编辑秘密信息（API 密钥、令牌等）。
        # 沙盒环境变量过滤器（第 434-454 行）阻止 os.environ 访问，
        # 但脚本仍可以从磁盘读取秘密（例如 open('~/.kclaw/.env')）。
        # 这确保泄露的秘密永远不会进入模型上下文。
        from agent.redact import redact_sensitive_text
        stdout_text = redact_sensitive_text(stdout_text)
        stderr_text = redact_sensitive_text(stderr_text)

        # 构建响应
        result: Dict[str, Any] = {
            "status": status,
            "output": stdout_text,
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }

        if status == "timeout":
            result["error"] = f"Script timed out after {timeout}s and was killed."
        elif status == "interrupted":
            result["output"] = stdout_text + "\n[执行被中断 — 用户发送了新消息]"
        elif exit_code != 0:
            result["status"] = "error"
            result["error"] = stderr_text or f"脚本以代码 {exit_code} 退出"
            # 在输出中包含 stderr，以便 LLM 看到回溯
            if stderr_text:
                result["output"] = stdout_text + "\n--- stderr ---\n" + stderr_text

        return json.dumps(result, ensure_ascii=False)

    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code 在 %s 秒后失败，进行了 %d 次工具调用：%s: %s",
            duration,
            tool_call_counter[0],
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return json.dumps({
            "status": "error",
            "error": str(exc),
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }, ensure_ascii=False)

    finally:
        # 清理临时目录和套接字
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError as e:
                logger.debug("Server socket close error: %s", e)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            os.unlink(sock_path)
        except OSError:
            pass  # 已经清理或从未创建


def _kill_process_group(proc, escalate: bool = False):
    """终止子进程及其整个进程组。"""
    try:
        if _IS_WINDOWS:
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as e:
        logger.debug("无法终止进程组：%s", e, exc_info=True)
        try:
            proc.kill()
        except Exception as e2:
            logger.debug("无法终止进程：%s", e2, exc_info=True)

    if escalate:
        # 给进程 5 秒时间在 SIGTERM 后退出，然后发送 SIGKILL
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if _IS_WINDOWS:
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError) as e:
                logger.debug("Could not kill process group with SIGKILL: %s", e, exc_info=True)
                try:
                    proc.kill()
                except Exception as e2:
                    logger.debug("Could not kill process: %s", e2, exc_info=True)


def _load_config() -> dict:
    """从 CLI_CONFIG 加载 code_execution 配置（如果可用）。"""
    try:
        from cli import CLI_CONFIG
        return CLI_CONFIG.get("code_execution", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# OpenAI 函数调用模式
# ---------------------------------------------------------------------------

# execute_code 描述的每个工具文档行。
# 按与规范显示顺序匹配的顺序排列。
_TOOL_DOC_LINES = [
    ("web_search",
     "  web_search(query: str, limit: int = 5) -> dict\n"
     "    Returns {\"data\": {\"web\": [{\"url\", \"title\", \"description\"}, ...]}}"),
    ("web_extract",
     "  web_extract(urls: list[str]) -> dict\n"
     "    Returns {\"results\": [{\"url\", \"title\", \"content\", \"error\"}, ...]} where content is markdown"),
    ("read_file",
     "  read_file(path: str, offset: int = 1, limit: int = 500) -> dict\n"
     "    Lines are 1-indexed. Returns {\"content\": \"...\", \"total_lines\": N}"),
    ("write_file",
     "  write_file(path: str, content: str) -> dict\n"
     "    Always overwrites the entire file."),
    ("search_files",
     "  search_files(pattern: str, target=\"content\", path=\".\", file_glob=None, limit=50) -> dict\n"
     "    target: \"content\" (search inside files) or \"files\" (find files by name). Returns {\"matches\": [...]}"),
    ("patch",
     "  patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict\n"
     "    Replaces old_string with new_string in the file."),
    ("terminal",
     "  terminal(command: str, timeout=None, workdir=None) -> dict\n"
     "    Foreground only (no background/pty). Returns {\"output\": \"...\", \"exit_code\": N}"),
]


def build_execute_code_schema(enabled_sandbox_tools: set = None) -> dict:
    """构建 execute_code 模式，描述仅列出启用的工具。

    当工具通过 ``kclaw tools`` 被禁用时（例如 web 被关闭），
    模式描述不应提及 web_search / web_extract —
    否则模型认为它们可用并继续尝试使用它们。
    """
    if enabled_sandbox_tools is None:
        enabled_sandbox_tools = SANDBOX_ALLOWED_TOOLS

    # 为仅启用的工具构建工具文档行
    tool_lines = "\n".join(
        doc for name, doc in _TOOL_DOC_LINES if name in enabled_sandbox_tools
    )

    # 从启用的工具构建示例导入列表
    import_examples = [n for n in ("web_search", "terminal") if n in enabled_sandbox_tools]
    if not import_examples:
        import_examples = sorted(enabled_sandbox_tools)[:2]
    if import_examples:
        import_str = ", ".join(import_examples) + ", ..."
    else:
        import_str = "..."

    description = (
        "Run a Python script that can call KClaw tools programmatically. "
        "Use this when you need 3+ tool calls with processing logic between them, "
        "need to filter/reduce large tool outputs before they enter your context, "
        "need conditional branching (if X then Y else Z), or need to loop "
        "(fetch N pages, process N files, retry on failure).\n\n"
        "Use normal tool calls instead when: single tool call with no processing, "
        "you need to see the full result and apply complex reasoning, "
        "or the task requires interactive user input.\n\n"
        f"Available via `from kclaw_tools import ...`:\n\n"
        f"{tool_lines}\n\n"
        "Limits: 5-minute timeout, 50KB stdout cap, max 50 tool calls per script. "
        "terminal() is foreground-only (no background or pty). "
        "If the session uses a cloud sandbox backend, treat it as resumable task state rather than a durable always-on machine.\n\n"
        "Print your final result to stdout. Use Python stdlib (json, re, math, csv, "
        "datetime, collections, etc.) for processing between tool calls.\n\n"
        "Also available (no import needed — built into kclaw_tools):\n"
        "  json_parse(text: str) — json.loads with strict=False; use for terminal() output with control chars\n"
        "  shell_quote(s: str) — shlex.quote(); use when interpolating dynamic strings into shell commands\n"
        "  retry(fn, max_attempts=3, delay=2) — retry with exponential backoff for transient failures"
    )

    return {
        "name": "execute_code",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Import tools with "
                        f"`from kclaw_tools import {import_str}` "
                        "and print your final result to stdout."
                    ),
                },
            },
            "required": ["code"],
        },
    }


# 注册时使用的默认模式（列出所有沙盒工具）
EXECUTE_CODE_SCHEMA = build_execute_code_schema()


# --- 注册表 ---
from tools.registry import registry, tool_error

registry.register(
    name="execute_code",
    toolset="code_execution",
    schema=EXECUTE_CODE_SCHEMA,
    handler=lambda args, **kw: execute_code(
        code=args.get("code", ""),
        task_id=kw.get("task_id"),
        enabled_tools=kw.get("enabled_tools")),
    check_fn=check_sandbox_requirements,
    emoji="🐍",
    max_result_size_chars=100_000,
)
