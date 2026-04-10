"""用于沙箱化命令执行的 Docker 执行环境。

安全加固（cap-drop ALL、no-new-privileges、PID 限制）、
可配置的资源限制（CPU、内存、磁盘）以及可选的
通过绑定挂载实现文件系统持久化。
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from typing import Optional

from tools.environments.base import BaseEnvironment, _popen_bash
from tools.environments.local import _KCLAW_PROVIDER_ENV_BLOCKLIST

logger = logging.getLogger(__name__)


# 当 'docker' 不在 PATH 中时检查的常见 Docker Desktop 安装路径。
# macOS Intel: /usr/local/bin, macOS Apple Silicon (Homebrew): /opt/homebrew/bin,
# Docker Desktop 应用包: /Applications/Docker.app/Contents/Resources/bin
_DOCKER_SEARCH_PATHS = [
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
]

_docker_executable: Optional[str] = None  # resolved once, cached
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_forward_env_names(forward_env: list[str] | None) -> list[str]:
    """返回去重的有效环境变量名列表。"""
    normalized: list[str] = []
    seen: set[str] = set()

    for item in forward_env or []:
        if not isinstance(item, str):
            logger.warning("Ignoring non-string docker_forward_env entry: %r", item)
            continue

        key = item.strip()
        if not key:
            continue
        if not _ENV_VAR_NAME_RE.match(key):
            logger.warning("Ignoring invalid docker_forward_env entry: %r", item)
            continue
        if key in seen:
            continue

        seen.add(key)
        normalized.append(key)

    return normalized


def _normalize_env_dict(env: dict | None) -> dict[str, str]:
    """将 docker_env 字典验证并规范化为 {str: str}。

    过滤掉具有无效变量名或非字符串值的条目。
    """
    if not env:
        return {}
    if not isinstance(env, dict):
        logger.warning("docker_env is not a dict: %r", env)
        return {}

    normalized: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_VAR_NAME_RE.match(key.strip()):
            logger.warning("Ignoring invalid docker_env key: %r", key)
            continue
        key = key.strip()
        if not isinstance(value, str):
            # Coerce simple scalar types (int, bool, float) to string;
            # reject complex types.
            if isinstance(value, (int, float, bool)):
                value = str(value)
            else:
                logger.warning("Ignoring non-string docker_env value for %r: %r", key, value)
                continue
        normalized[key] = value

    return normalized


def _load_kclaw_env_vars() -> dict[str, str]:
    """加载 ~/.kclaw/.env 值而不导致 Docker 命令执行失败。"""
    try:
        from kclaw_cli.config import load_env

        return load_env() or {}
    except Exception:
        return {}


def find_docker() -> Optional[str]:
    """定位 docker CLI 二进制文件。

    首先检查 ``shutil.which``（尊重 PATH），然后探测 macOS 上已知的
    安装位置，Docker Desktop 可能不在 PATH 中（例如，通过 launchd 作为
    网关服务运行时）。

    返回绝对路径，如果找不到 docker 则返回 ``None``。
    """
    global _docker_executable
    if _docker_executable is not None:
        return _docker_executable

    found = shutil.which("docker")
    if found:
        _docker_executable = found
        return found

    for path in _DOCKER_SEARCH_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _docker_executable = path
            logger.info("Found docker at non-PATH location: %s", path)
            return path

    return None


# 应用于每个容器的安全标志。
# 容器本身就是安全边界（与主机隔离）。
# 我们丢弃所有权限，然后添加回最少需要的权限：
#   DAC_OVERRIDE - root 可以写入绑定挂载的由主机用户拥有的目录
#   CHOWN/FOWNER - 包管理器（pip、npm、apt）需要设置文件所有权
# 阻止权限提升并限制 PID。
# /tmp 大小有限制且 nosuid 但允许 exec（pip/npm 构建需要）。
_SECURITY_ARGS = [
    "--cap-drop", "ALL",
    "--cap-add", "DAC_OVERRIDE",
    "--cap-add", "CHOWN",
    "--cap-add", "FOWNER",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "256",
    "--tmpfs", "/tmp:rw,nosuid,size=512m",
    "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=256m",
    "--tmpfs", "/run:rw,noexec,nosuid,size=64m",
]


_storage_opt_ok: Optional[bool] = None  # cached result across instances


def _ensure_docker_available() -> None:
    """使用前尽最大努力检查 docker CLI 是否可用。

    重用 ``find_docker()``，因此此预检与 Docker 后端的其余部分保持一致，
    包括已知的非 PATH Docker Desktop 位置。
    """
    docker_exe = find_docker()
    if not docker_exe:
        logger.error(
            "Docker backend selected but no docker executable was found in PATH "
            "or known install locations. Install Docker Desktop and ensure the "
            "CLI is available."
        )
        raise RuntimeError(
            "Docker executable not found in PATH or known install locations. "
            "Install Docker and ensure the 'docker' command is available."
        )

    try:
        result = subprocess.run(
            [docker_exe, "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.error(
            "Docker backend selected but the resolved docker executable '%s' could "
            "not be executed.",
            docker_exe,
            exc_info=True,
        )
        raise RuntimeError(
            "Docker executable could not be executed. Check your Docker installation."
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Docker backend selected but '%s version' timed out. "
            "The Docker daemon may not be running.",
            docker_exe,
            exc_info=True,
        )
        raise RuntimeError(
            "Docker daemon is not responding. Ensure Docker is running and try again."
        )
    except Exception:
        logger.error(
            "Unexpected error while checking Docker availability.",
            exc_info=True,
        )
        raise
    else:
        if result.returncode != 0:
            logger.error(
                "Docker backend selected but '%s version' failed "
                "(exit code %d, stderr=%s)",
                docker_exe,
                result.returncode,
                result.stderr.strip(),
            )
            raise RuntimeError(
                "Docker command is available but 'docker version' failed. "
                "Check your Docker installation."
            )


class DockerEnvironment(BaseEnvironment):
    """具有资源限制和持久化的安全加固 Docker 容器执行。

    安全性：降低所有权限、无权限提升、PID 限制、
    用于临时目录的大小有限制的 tmpfs。容器本身就是安全边界
    — 内部文件系统可写，以便代理可以按需安装包
    （pip、npm、apt）。通过 tmpfs 或绑定挂载实现可写工作区。

    持久化：启用时，绑定挂载在容器重启之间保留 /workspace 和 /root。
    """

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        cpu: float = 0,
        memory: int = 0,
        disk: int = 0,
        persistent_filesystem: bool = False,
        task_id: str = "default",
        volumes: list = None,
        forward_env: list[str] | None = None,
        env: dict | None = None,
        network: bool = True,
        host_cwd: str = None,
        auto_mount_cwd: bool = False,
    ):
        if cwd == "~":
            cwd = "/root"
        super().__init__(cwd=cwd, timeout=timeout)
        self._base_image = image
        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._forward_env = _normalize_forward_env_names(forward_env)
        self._env = _normalize_env_dict(env)
        self._container_id: Optional[str] = None
        logger.info(f"DockerEnvironment volumes: {volumes}")
        # Ensure volumes is a list (config.yaml could be malformed)
        if volumes is not None and not isinstance(volumes, list):
            logger.warning(f"docker_volumes config is not a list: {volumes!r}")
            volumes = []

        # Fail fast if Docker is not available.
        _ensure_docker_available()

        # Build resource limit args
        resource_args = []
        if cpu > 0:
            resource_args.extend(["--cpus", str(cpu)])
        if memory > 0:
            resource_args.extend(["--memory", f"{memory}m"])
        if disk > 0 and sys.platform != "darwin":
            if self._storage_opt_supported():
                resource_args.extend(["--storage-opt", f"size={disk}m"])
            else:
                logger.warning(
                    "Docker storage driver does not support per-container disk limits "
                    "(requires overlay2 on XFS with pquota). Container will run without disk quota."
                )
        if not network:
            resource_args.append("--network=none")

        # Persistent workspace via bind mounts from a configurable host directory
        # (TERMINAL_SANDBOX_DIR, default ~/.kclaw/sandboxes/). Non-persistent
        # mode uses tmpfs (ephemeral, fast, gone on cleanup).
        from tools.environments.base import get_sandbox_dir

        # User-configured volume mounts (from config.yaml docker_volumes)
        volume_args = []
        workspace_explicitly_mounted = False
        for vol in (volumes or []):
            if not isinstance(vol, str):
                logger.warning(f"Docker volume entry is not a string: {vol!r}")
                continue
            vol = vol.strip()
            if not vol:
                continue
            if ":" in vol:
                volume_args.extend(["-v", vol])
                if ":/workspace" in vol:
                    workspace_explicitly_mounted = True
            else:
                logger.warning(f"Docker volume '{vol}' missing colon, skipping")

        host_cwd_abs = os.path.abspath(os.path.expanduser(host_cwd)) if host_cwd else ""
        bind_host_cwd = (
            auto_mount_cwd
            and bool(host_cwd_abs)
            and os.path.isdir(host_cwd_abs)
            and not workspace_explicitly_mounted
        )
        if auto_mount_cwd and host_cwd and not os.path.isdir(host_cwd_abs):
            logger.debug(f"Skipping docker cwd mount: host_cwd is not a valid directory: {host_cwd}")

        self._workspace_dir: Optional[str] = None
        self._home_dir: Optional[str] = None
        writable_args = []
        if self._persistent:
            sandbox = get_sandbox_dir() / "docker" / task_id
            self._home_dir = str(sandbox / "home")
            os.makedirs(self._home_dir, exist_ok=True)
            writable_args.extend([
                "-v", f"{self._home_dir}:/root",
            ])
            if not bind_host_cwd and not workspace_explicitly_mounted:
                self._workspace_dir = str(sandbox / "workspace")
                os.makedirs(self._workspace_dir, exist_ok=True)
                writable_args.extend([
                    "-v", f"{self._workspace_dir}:/workspace",
                ])
        else:
            if not bind_host_cwd and not workspace_explicitly_mounted:
                writable_args.extend([
                    "--tmpfs", "/workspace:rw,exec,size=10g",
                ])
            writable_args.extend([
                "--tmpfs", "/home:rw,exec,size=1g",
                "--tmpfs", "/root:rw,exec,size=1g",
            ])

        if bind_host_cwd:
            logger.info(f"Mounting configured host cwd to /workspace: {host_cwd_abs}")
            volume_args = ["-v", f"{host_cwd_abs}:/workspace", *volume_args]
        elif workspace_explicitly_mounted:
            logger.debug("Skipping docker cwd mount: /workspace already mounted by user config")

        # Mount credential files (OAuth tokens, etc.) declared by skills.
        # Read-only so the container can authenticate but not modify host creds.
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                get_skills_directory_mount,
                get_cache_directory_mounts,
            )

            for mount_entry in get_credential_file_mounts():
                volume_args.extend([
                    "-v",
                    f"{mount_entry['host_path']}:{mount_entry['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting credential %s -> %s",
                    mount_entry["host_path"],
                    mount_entry["container_path"],
                )

            # Mount skill directories (local + external) so skill
            # scripts/templates are available inside the container.
            for skills_mount in get_skills_directory_mount():
                volume_args.extend([
                    "-v",
                    f"{skills_mount['host_path']}:{skills_mount['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting skills dir %s -> %s",
                    skills_mount["host_path"],
                    skills_mount["container_path"],
                )

            # Mount host-side cache directories (documents, images, audio,
            # screenshots) so the agent can access uploaded files and other
            # cached media from inside the container.  Read-only — the
            # container reads these but the host gateway manages writes.
            for cache_mount in get_cache_directory_mounts():
                volume_args.extend([
                    "-v",
                    f"{cache_mount['host_path']}:{cache_mount['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting cache dir %s -> %s",
                    cache_mount["host_path"],
                    cache_mount["container_path"],
                )
        except Exception as e:
            logger.debug("Docker: could not load credential file mounts: %s", e)

        # Explicit environment variables (docker_env config) — set at container
        # creation so they're available to all processes (including entrypoint).
        env_args = []
        for key in sorted(self._env):
            env_args.extend(["-e", f"{key}={self._env[key]}"])

        logger.info(f"Docker volume_args: {volume_args}")
        all_run_args = list(_SECURITY_ARGS) + writable_args + resource_args + volume_args + env_args
        logger.info(f"Docker run_args: {all_run_args}")

        # Resolve the docker executable once so it works even when
        # /usr/local/bin is not in PATH (common on macOS gateway/service).
        self._docker_exe = find_docker() or "docker"

        # Start the container directly via `docker run -d`.
        container_name = f"kclaw-{uuid.uuid4().hex[:8]}"
        run_cmd = [
            self._docker_exe, "run", "-d",
            "--name", container_name,
            "-w", cwd,
            *all_run_args,
            image,
            "sleep", "2h",
        ]
        logger.debug(f"Starting container: {' '.join(run_cmd)}")
        result = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            timeout=120,  # image pull may take a while
            check=True,
        )
        self._container_id = result.stdout.strip()
        logger.info(f"Started container {container_name} ({self._container_id[:12]})")

        # Build the init-time env forwarding args (used only by init_session
        # to inject host env vars into the snapshot; subsequent commands get
        # them from the snapshot file).
        self._init_env_args = self._build_init_env_args()

        # Initialize session snapshot inside the container
        self.init_session()

    def _build_init_env_args(self) -> list[str]:
        """构建 -e KEY=VALUE 参数，用于将主机环境变量注入 init_session。

        这些在 init_session() 期间使用一次，以便 export -p 将它们捕获到快照中。
        后续的 execute() 调用不需要 -e 标志。
        """
        exec_env: dict[str, str] = dict(self._env)

        explicit_forward_keys = set(self._forward_env)
        passthrough_keys: set[str] = set()
        try:
            from tools.env_passthrough import get_all_passthrough
            passthrough_keys = set(get_all_passthrough())
        except Exception:
            pass
        # Explicit docker_forward_env entries are an intentional opt-in and must
        # win over the generic KClaw secret blocklist. Only implicit passthrough
        # keys are filtered.
        forward_keys = explicit_forward_keys | (passthrough_keys - _KCLAW_PROVIDER_ENV_BLOCKLIST)
        kclaw_env = _load_kclaw_env_vars() if forward_keys else {}
        for key in sorted(forward_keys):
            value = os.getenv(key)
            if value is None:
                value = kclaw_env.get(key)
            if value is not None:
                exec_env[key] = value

        args = []
        for key in sorted(exec_env):
            args.extend(["-e", f"{key}={exec_env[key]}"])
        return args

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        """在 Docker 容器内生成一个 bash 进程。"""
        assert self._container_id, "Container not started"
        cmd = [self._docker_exe, "exec"]
        if stdin_data is not None:
            cmd.append("-i")

        # Only inject -e env args during init_session (login=True).
        # Subsequent commands get env vars from the snapshot.
        if login:
            cmd.extend(self._init_env_args)

        cmd.extend([self._container_id])

        if login:
            cmd.extend(["bash", "-l", "-c", cmd_string])
        else:
            cmd.extend(["bash", "-c", cmd_string])

        return _popen_bash(cmd, stdin_data)

    @staticmethod
    def _storage_opt_supported() -> bool:
        """检查 Docker 的存储驱动程序是否支持 --storage-opt size=。

        只有带 pquota 的 XFS 上的 overlay2 支持按容器磁盘配额。
        Ubuntu（和大多数发行版）默认使用 ext4，此标志会出错。
        """
        global _storage_opt_ok
        if _storage_opt_ok is not None:
            return _storage_opt_ok
        try:
            docker = find_docker() or "docker"
            result = subprocess.run(
                [docker, "info", "--format", "{{.Driver}}"],
                capture_output=True, text=True, timeout=10,
            )
            driver = result.stdout.strip().lower()
            if driver != "overlay2":
                _storage_opt_ok = False
                return False
            # overlay2 only supports storage-opt on XFS with pquota.
            # Probe by attempting a dry-ish run — the fastest reliable check.
            probe = subprocess.run(
                [docker, "create", "--storage-opt", "size=1m", "hello-world"],
                capture_output=True, text=True, timeout=15,
            )
            if probe.returncode == 0:
                # Clean up the created container
                container_id = probe.stdout.strip()
                if container_id:
                    subprocess.run([docker, "rm", container_id],
                                   capture_output=True, timeout=5)
                _storage_opt_ok = True
            else:
                _storage_opt_ok = False
        except Exception:
            _storage_opt_ok = False
        logger.debug("Docker --storage-opt support: %s", _storage_opt_ok)
        return _storage_opt_ok

    def cleanup(self):
        """停止并移除容器。如果 persistent=True，绑定挂载目录会保留。"""
        if self._container_id:
            try:
                # Stop in background so cleanup doesn't block
                stop_cmd = (
                    f"(timeout 60 {self._docker_exe} stop {self._container_id} || "
                    f"{self._docker_exe} rm -f {self._container_id}) >/dev/null 2>&1 &"
                )
                subprocess.Popen(stop_cmd, shell=True)
            except Exception as e:
                logger.warning("Failed to stop container %s: %s", self._container_id, e)

            if not self._persistent:
                # Also schedule removal (stop only leaves it as stopped)
                try:
                    subprocess.Popen(
                        f"sleep 3 && {self._docker_exe} rm -f {self._container_id} >/dev/null 2>&1 &",
                        shell=True,
                    )
                except Exception:
                    pass
            self._container_id = None

        if not self._persistent:
            for d in (self._workspace_dir, self._home_dir):
                if d:
                    shutil.rmtree(d, ignore_errors=True)
