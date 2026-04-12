"""
Gateway 运行时状态助手。

提供基于 PID 文件的检测，用于判断 gateway 守护进程是否正在运行，
供 send_message 的 check_fn 用于在 CLI 中控制可用性。

PID 文件位于 ``{KCLAW_HOME}/gateway.pid``。KCLAW_HOME 默认为
``~/.kclaw``，但可以通过环境变量覆盖。这意味着
不同的 KCLAW_HOME 目录自然会有不同的 PID 文件 — 这在添加命名 profiles
（多个代理在独立配置下并发运行）时会有用。
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from kclaw_constants import get_kclaw_home
from typing import Any, Optional

_GATEWAY_KIND = "kclaw-gateway"
_RUNTIME_STATUS_FILE = "gateway_state.json"
_LOCKS_DIRNAME = "gateway-locks"


def _get_pid_path() -> Path:
    """返回 gateway PID 文件的路径，遵循 KCLAW_HOME。"""
    home = get_kclaw_home()
    return home / "gateway.pid"


def _get_runtime_status_path() -> Path:
    """返回持久化的运行时健康/状态文件路径。"""
    return _get_pid_path().with_name(_RUNTIME_STATUS_FILE)


def _get_lock_dir() -> Path:
    """返回机器本地的令牌作用域 gateway 锁目录。"""
    override = os.getenv("KCLAW_GATEWAY_LOCK_DIR")
    if override:
        return Path(override)
    state_home = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "kclaw" / _LOCKS_DIRNAME


def _utc_now_iso() -> str:
    """返回当前 UTC 时间（ISO 格式）。"""
    return datetime.now(timezone.utc).isoformat()


def _scope_hash(identity: str) -> str:
    """计算标识的哈希值用于锁文件名。"""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _get_scope_lock_path(scope: str, identity: str) -> Path:
    """返回给定作用域和标识的锁文件路径。"""
    return _get_lock_dir() / f"{scope}-{_scope_hash(identity)}.lock"


def _get_process_start_time(pid: int) -> Optional[int]:
    """在可用时返回进程的 kernel 启动时间。"""
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        # /proc/<pid>/stat 中的第 22 个字段是进程启动时间（时钟滴答）。
        return int(stat_path.read_text().split()[21])
    except (FileNotFoundError, IndexError, PermissionError, ValueError, OSError):
        return None


def _read_process_cmdline(pid: int) -> Optional[str]:
    """返回进程命令行作为空格分隔的字符串。"""
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmdline_path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _looks_like_gateway_process(pid: int) -> bool:
    """当活动 PID 仍然看起来像 KClaw gateway 时返回 True。"""
    cmdline = _read_process_cmdline(pid)
    if not cmdline:
        return False

    patterns = (
        "kclaw_cli.main gateway",
        "kclaw_cli/main.py gateway",
        "kclaw gateway",
        "gateway/run.py",
    )
    return any(pattern in cmdline for pattern in patterns)


def _record_looks_like_gateway(record: dict[str, Any]) -> bool:
    """当 cmdline 不可用时，从 PID 文件元数据验证 gateway 身份。"""
    if record.get("kind") != _GATEWAY_KIND:
        return False

    argv = record.get("argv")
    if not isinstance(argv, list) or not argv:
        return False

    cmdline = " ".join(str(part) for part in argv)
    patterns = (
        "kclaw_cli.main gateway",
        "kclaw_cli/main.py gateway",
        "kclaw gateway",
        "gateway/run.py",
    )
    return any(pattern in cmdline for pattern in patterns)


def _build_pid_record() -> dict:
    return {
        "pid": os.getpid(),
        "kind": _GATEWAY_KIND,
        "argv": list(sys.argv),
        "start_time": _get_process_start_time(os.getpid()),
    }


def _build_runtime_status_record() -> dict[str, Any]:
    payload = _build_pid_record()
    payload.update({
        "gateway_state": "starting",
        "exit_reason": None,
        "platforms": {},
        "updated_at": _utc_now_iso(),
    })
    return payload


def _read_json_file(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _read_pid_record() -> Optional[dict]:
    pid_path = _get_pid_path()
    if not pid_path.exists():
        return None

    raw = pid_path.read_text().strip()
    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            return {"pid": int(raw)}
        except ValueError:
            return None

    if isinstance(payload, int):
        return {"pid": payload}
    if isinstance(payload, dict):
        return payload
    return None


def write_pid_file() -> None:
    """将当前进程 PID 和元数据写入 gateway PID 文件。"""
    _write_json_file(_get_pid_path(), _build_pid_record())


def write_runtime_status(
    *,
    gateway_state: Optional[str] = None,
    exit_reason: Optional[str] = None,
    platform: Optional[str] = None,
    platform_state: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """持久化 gateway 运行时健康信息以供诊断/状态使用。"""
    path = _get_runtime_status_path()
    payload = _read_json_file(path) or _build_runtime_status_record()
    payload.setdefault("platforms", {})
    payload.setdefault("kind", _GATEWAY_KIND)
    payload["pid"] = os.getpid()
    payload["start_time"] = _get_process_start_time(os.getpid())
    payload["updated_at"] = _utc_now_iso()

    if gateway_state is not None:
        payload["gateway_state"] = gateway_state
    if exit_reason is not None:
        payload["exit_reason"] = exit_reason

    if platform is not None:
        platform_payload = payload["platforms"].get(platform, {})
        if platform_state is not None:
            platform_payload["state"] = platform_state
        if error_code is not None:
            platform_payload["error_code"] = error_code
        if error_message is not None:
            platform_payload["error_message"] = error_message
        platform_payload["updated_at"] = _utc_now_iso()
        payload["platforms"][platform] = platform_payload

    _write_json_file(path, payload)


def read_runtime_status() -> Optional[dict[str, Any]]:
    """读取持久化的 gateway 运行时健康/状态信息。"""
    return _read_json_file(_get_runtime_status_path())


def remove_pid_file() -> None:
    """如果存在则删除 gateway PID 文件。"""
    try:
        _get_pid_path().unlink(missing_ok=True)
    except Exception:
        pass


def acquire_scoped_lock(scope: str, identity: str, metadata: Optional[dict[str, Any]] = None) -> tuple[bool, Optional[dict[str, Any]]]:
    """获取由 scope + identity 键控的机器本地锁。

    用于防止多个本地 gateway 同时使用相同的外部身份
    （例如跨不同 KCLAW_HOME 目录使用相同的 Telegram bot 令牌）。
    """
    lock_path = _get_scope_lock_path(scope, identity)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **_build_pid_record(),
        "scope": scope,
        "identity_hash": _scope_hash(identity),
        "metadata": metadata or {},
        "updated_at": _utc_now_iso(),
    }

    existing = _read_json_file(lock_path)
    if existing:
        try:
            existing_pid = int(existing["pid"])
        except (KeyError, TypeError, ValueError):
            existing_pid = None

        if existing_pid == os.getpid() and existing.get("start_time") == record.get("start_time"):
            _write_json_file(lock_path, record)
            return True, existing

        stale = existing_pid is None
        if not stale:
            try:
                os.kill(existing_pid, 0)
            except (ProcessLookupError, PermissionError):
                stale = True
            else:
                current_start = _get_process_start_time(existing_pid)
                if (
                    existing.get("start_time") is not None
                    and current_start is not None
                    and current_start != existing.get("start_time")
                ):
                    stale = True
                # 检查进程是否已停止（Ctrl+Z / SIGTSTP）— 停止的
                # 进程仍然响应 os.kill(pid, 0) 但实际未运行。
                # 将它们视为过期，以便 --replace 正常工作。
                if not stale:
                    try:
                        _proc_status = Path(f"/proc/{existing_pid}/status")
                        if _proc_status.exists():
                            for _line in _proc_status.read_text().splitlines():
                                if _line.startswith("State:"):
                                    _state = _line.split()[1]
                                    if _state in ("T", "t"):  # stopped or tracing stop
                                        stale = True
                                    break
                    except (OSError, PermissionError):
                        pass
        if stale:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            return False, existing

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False, _read_json_file(lock_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
    except Exception:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True, None


def release_scoped_lock(scope: str, identity: str) -> None:
    """在拥有此锁时释放之前获取的作用域锁。"""
    lock_path = _get_scope_lock_path(scope, identity)
    existing = _read_json_file(lock_path)
    if not existing:
        return
    if existing.get("pid") != os.getpid():
        return
    if existing.get("start_time") != _get_process_start_time(os.getpid()):
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def release_all_scoped_locks() -> int:
    """删除锁目录中的所有作用域锁文件。

    在 --replace 期间调用，以清理被停止/杀死且未正常释放锁的
    gateway 进程留下的过期锁。
    返回删除的锁文件数。
    """
    lock_dir = _get_lock_dir()
    removed = 0
    if lock_dir.exists():
        for lock_file in lock_dir.glob("*.lock"):
            try:
                lock_file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def get_running_pid() -> Optional[int]:
    """返回运行中 gateway 实例的 PID，或 ``None``。

    检查 PID 文件并验证进程是否实际存活。
    自动清理过期的 PID 文件。
    """
    record = _read_pid_record()
    if not record:
        remove_pid_file()
        return None

    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        remove_pid_file()
        return None

    try:
        os.kill(pid, 0)  # signal 0 = existence check, no actual signal sent
    except (ProcessLookupError, PermissionError):
        remove_pid_file()
        return None

    recorded_start = record.get("start_time")
    current_start = _get_process_start_time(pid)
    if recorded_start is not None and current_start is not None and current_start != recorded_start:
        remove_pid_file()
        return None

    if not _looks_like_gateway_process(pid):
        if not _record_looks_like_gateway(record):
            remove_pid_file()
            return None

    return pid


def is_gateway_running() -> bool:
    """检查 gateway 守护进程当前是否正在运行。"""
    return get_running_pid() is not None
