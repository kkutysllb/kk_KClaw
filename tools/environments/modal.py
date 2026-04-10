"""直接使用原生 Modal SDK 的 Modal 云执行环境。

使用 ``Sandbox.create()`` + ``Sandbox.exec()`` 而不是旧的运行时包装器，
同时保留 KClaw 在会话之间的持久快照行为。
"""

import asyncio
import logging
import shlex
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from kclaw_constants import get_kclaw_home
from tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
    _file_mtime_key,
    _load_json_store,
    _save_json_store,
)

logger = logging.getLogger(__name__)

_SNAPSHOT_STORE = get_kclaw_home() / "modal_snapshots.json"
_DIRECT_SNAPSHOT_NAMESPACE = "direct"


def _load_snapshots() -> dict:
    return _load_json_store(_SNAPSHOT_STORE)


def _save_snapshots(data: dict) -> None:
    _save_json_store(_SNAPSHOT_STORE, data)


def _direct_snapshot_key(task_id: str) -> str:
    return f"{_DIRECT_SNAPSHOT_NAMESPACE}:{task_id}"


def _get_snapshot_restore_candidate(task_id: str) -> tuple[str | None, bool]:
    snapshots = _load_snapshots()
    namespaced_key = _direct_snapshot_key(task_id)
    snapshot_id = snapshots.get(namespaced_key)
    if isinstance(snapshot_id, str) and snapshot_id:
        return snapshot_id, False
    legacy_snapshot_id = snapshots.get(task_id)
    if isinstance(legacy_snapshot_id, str) and legacy_snapshot_id:
        return legacy_snapshot_id, True
    return None, False


def _store_direct_snapshot(task_id: str, snapshot_id: str) -> None:
    snapshots = _load_snapshots()
    snapshots[_direct_snapshot_key(task_id)] = snapshot_id
    snapshots.pop(task_id, None)
    _save_snapshots(snapshots)


def _delete_direct_snapshot(task_id: str, snapshot_id: str | None = None) -> None:
    snapshots = _load_snapshots()
    updated = False
    for key in (_direct_snapshot_key(task_id), task_id):
        value = snapshots.get(key)
        if value is None:
            continue
        if snapshot_id is None or value == snapshot_id:
            snapshots.pop(key, None)
            updated = True
    if updated:
        _save_snapshots(snapshots)


def _resolve_modal_image(image_spec: Any) -> Any:
    """将注册表引用或快照 ID 转换为 Modal 镜像对象。

    包括对 ubuntu/debian 镜像的 add_python 支持（来自 PR 4511）。
    """
    import modal as _modal

    if not isinstance(image_spec, str):
        return image_spec

    if image_spec.startswith("im-"):
        return _modal.Image.from_id(image_spec)

    # PR 4511: add python to ubuntu/debian images that don't have it
    lower = image_spec.lower()
    add_python = any(base in lower for base in ("ubuntu", "debian"))

    setup_commands = [
        "RUN rm -rf /usr/local/lib/python*/site-packages/pip* 2>/dev/null; "
        "python -m ensurepip --upgrade --default-pip 2>/dev/null || true",
    ]
    if add_python:
        setup_commands.insert(0,
            "RUN apt-get update -qq && apt-get install -y -qq python3 python3-venv > /dev/null 2>&1 || true"
        )

    return _modal.Image.from_registry(
        image_spec,
        setup_dockerfile_commands=setup_commands,
    )


class _AsyncWorker:
    """具有自己事件循环的后台线程，用于异步安全的 Modal 调用。"""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._started.wait(timeout=30)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def run_coroutine(self, coro, timeout=600):
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("AsyncWorker loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def stop(self):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)


class ModalEnvironment(BaseEnvironment):
    """通过原生 Modal 沙箱进行 Modal 云执行。

    通过 _ThreadedProcessHandle 包装异步 SDK 调用实现每次调用生成。
    cancel_fn 连接到 sandbox.terminate 以支持中断。
    """

    _stdin_mode = "heredoc"
    _snapshot_timeout = 60  # Modal cold starts can be slow

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        modal_sandbox_kwargs: Optional[Dict[str, Any]] = None,
        persistent_filesystem: bool = True,
        task_id: str = "default",
    ):
        super().__init__(cwd=cwd, timeout=timeout)

        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._base_image = image
        self._sandbox = None
        self._app = None
        self._worker = _AsyncWorker()
        self._synced_files: Dict[str, tuple] = {}
        self._last_sync_time: float = 0

        sandbox_kwargs = dict(modal_sandbox_kwargs or {})

        restored_snapshot_id = None
        restored_from_legacy_key = False
        if self._persistent:
            restored_snapshot_id, restored_from_legacy_key = _get_snapshot_restore_candidate(
                self._task_id
            )
            if restored_snapshot_id:
                logger.info("Modal: restoring from snapshot %s", restored_snapshot_id[:20])

        import modal as _modal

        cred_mounts = []
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                iter_skills_files,
                iter_cache_files,
            )

            for mount_entry in get_credential_file_mounts():
                cred_mounts.append(
                    _modal.Mount.from_local_file(
                        mount_entry["host_path"],
                        remote_path=mount_entry["container_path"],
                    )
                )
            for entry in iter_skills_files():
                cred_mounts.append(
                    _modal.Mount.from_local_file(
                        entry["host_path"],
                        remote_path=entry["container_path"],
                    )
                )
            cache_files = iter_cache_files()
            for entry in cache_files:
                cred_mounts.append(
                    _modal.Mount.from_local_file(
                        entry["host_path"],
                        remote_path=entry["container_path"],
                    )
                )
        except Exception as e:
            logger.debug("Modal: could not load credential file mounts: %s", e)

        self._worker.start()

        async def _create_sandbox(image_spec: Any):
            app = await _modal.App.lookup.aio("kclaw", create_if_missing=True)
            create_kwargs = dict(sandbox_kwargs)
            if cred_mounts:
                existing_mounts = list(create_kwargs.pop("mounts", []))
                existing_mounts.extend(cred_mounts)
                create_kwargs["mounts"] = existing_mounts
            sandbox = await _modal.Sandbox.create.aio(
                "sleep", "infinity",
                image=image_spec,
                app=app,
                timeout=int(create_kwargs.pop("timeout", 3600)),
                **create_kwargs,
            )
            return app, sandbox

        try:
            target_image_spec = restored_snapshot_id or image
            try:
                effective_image = _resolve_modal_image(target_image_spec)
                self._app, self._sandbox = self._worker.run_coroutine(
                    _create_sandbox(effective_image), timeout=300,
                )
            except Exception as exc:
                if not restored_snapshot_id:
                    raise
                logger.warning(
                    "Modal: failed to restore snapshot %s, retrying with base image: %s",
                    restored_snapshot_id[:20], exc,
                )
                _delete_direct_snapshot(self._task_id, restored_snapshot_id)
                base_image = _resolve_modal_image(image)
                self._app, self._sandbox = self._worker.run_coroutine(
                    _create_sandbox(base_image), timeout=300,
                )
            else:
                if restored_snapshot_id and restored_from_legacy_key:
                    _store_direct_snapshot(self._task_id, restored_snapshot_id)
        except Exception:
            self._worker.stop()
            raise

        logger.info("Modal: sandbox created (task=%s)", self._task_id)
        self.init_session()

    def _push_file_to_sandbox(self, host_path: str, container_path: str) -> bool:
        """如果有变化，将单个文件推送到沙箱。"""
        file_key = _file_mtime_key(host_path)
        if file_key is None:
            return False
        if self._synced_files.get(container_path) == file_key:
            return False
        try:
            content = Path(host_path).read_bytes()
        except Exception:
            return False

        import base64
        b64 = base64.b64encode(content).decode("ascii")
        container_dir = str(Path(container_path).parent)
        cmd = (
            f"mkdir -p {shlex.quote(container_dir)} && "
            f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(container_path)}"
        )

        async def _write():
            proc = await self._sandbox.exec.aio("bash", "-c", cmd)
            await proc.wait.aio()

        self._worker.run_coroutine(_write(), timeout=15)
        self._synced_files[container_path] = file_key
        return True

    def _sync_files(self) -> None:
        """将凭据、技能和缓存文件推送到正在运行的沙箱。"""
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                iter_skills_files,
                iter_cache_files,
            )
            for entry in get_credential_file_mounts():
                self._push_file_to_sandbox(entry["host_path"], entry["container_path"])
            for entry in iter_skills_files():
                self._push_file_to_sandbox(entry["host_path"], entry["container_path"])
            for entry in iter_cache_files():
                self._push_file_to_sandbox(entry["host_path"], entry["container_path"])
        except Exception as e:
            logger.debug("Modal: file sync failed: %s", e)

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None):
        """返回包装异步 Modal 沙箱 exec 的 _ThreadedProcessHandle。"""
        sandbox = self._sandbox
        worker = self._worker

        def cancel():
            worker.run_coroutine(sandbox.terminate.aio(), timeout=15)

        def exec_fn() -> tuple[str, int]:
            async def _do():
                args = ["bash"]
                if login:
                    args.extend(["-l", "-c", cmd_string])
                else:
                    args.extend(["-c", cmd_string])
                process = await sandbox.exec.aio(*args, timeout=timeout)
                stdout = await process.stdout.read.aio()
                stderr = await process.stderr.read.aio()
                exit_code = await process.wait.aio()
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                output = stdout
                if stderr:
                    output = f"{stdout}\n{stderr}" if stdout else stderr
                return output, exit_code

            return worker.run_coroutine(_do(), timeout=timeout + 30)

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        """如果持久化则对文件系统进行快照，然后停止沙箱。"""
        if self._sandbox is None:
            return

        if self._persistent:
            try:
                async def _snapshot():
                    img = await self._sandbox.snapshot_filesystem.aio()
                    return img.object_id

                try:
                    snapshot_id = self._worker.run_coroutine(_snapshot(), timeout=60)
                except Exception:
                    snapshot_id = None

                if snapshot_id:
                    _store_direct_snapshot(self._task_id, snapshot_id)
                    logger.info(
                        "Modal: saved filesystem snapshot %s for task %s",
                        snapshot_id[:20], self._task_id,
                    )
            except Exception as e:
                logger.warning("Modal: filesystem snapshot failed: %s", e)

        try:
            self._worker.run_coroutine(self._sandbox.terminate.aio(), timeout=15)
        except Exception:
            pass
        finally:
            self._worker.stop()
            self._sandbox = None
            self._app = None
