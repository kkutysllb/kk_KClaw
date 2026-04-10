"""远程终端后端的文件传递注册表。

远程后端（Docker、Modal、SSH）创建没有主机文件的沙箱。
本模块确保凭证文件、技能目录和主机端缓存目录（文档、图片、音频、截图）
被挂载或同步到这些沙箱中，以便 agent 可以访问它们。

**凭证和技能** — 由技能声明（``required_credential_files``）
和用户配置（``terminal.credential_files``）提供的会话作用域注册表。

**缓存目录** — 网关缓存的上传、浏览器截图、TTS 音频和处理过的图片。
以只读方式挂载，以便远程终端可以引用主机端创建的文件
（例如 ``unzip`` 上传的归档）。

远程后端在沙箱创建时和每个命令之前调用
:func:`get_credential_file_mounts`、
:func:`get_skills_directory_mount` / :func:`iter_skills_files` 以及
:func:`get_cache_directory_mounts` / :func:`iter_cache_files`（用于 Modal 上的重新同步）。
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# 要挂载的会话作用域凭证文件列表。
# 由 ContextVar 支持，以防止网关管道中的跨会话数据渗透。
_registered_files_var: ContextVar[Dict[str, str]] = ContextVar("_registered_files")


def _get_registered() -> Dict[str, str]:
    """获取或创建当前上下文/会话的已注册凭证文件字典。"""
    try:
        return _registered_files_var.get()
    except LookupError:
        val: Dict[str, str] = {}
        _registered_files_var.set(val)
        return val


# Cache for config-based file list (loaded once per process).
_config_files: List[Dict[str, str]] | None = None


def _resolve_kclaw_home() -> Path:
    from kclaw_constants import get_kclaw_home
    return get_kclaw_home()


def register_credential_file(
    relative_path: str,
    container_base: str = "/root/.kclaw",
) -> bool:
    """注册要挂载到远程沙箱的凭证文件。

    *relative_path* 相对于 ``KCLAW_HOME``（例如 ``google_token.json``）。
    如果文件在主机上存在并已被注册，则返回 True。

    安全性：拒绝绝对路径和路径遍历序列（``..``）。
    解析后的主机路径必须保持在 KCLAW_HOME 内，
    以便恶意技能无法声明
    ``required_credential_files: ['../../.ssh/id_rsa']``
    并将敏感主机文件泄露到容器沙箱中。
    """
    kclaw_home = _resolve_kclaw_home()

    # Reject absolute paths — they bypass the KCLAW_HOME sandbox entirely.
    if os.path.isabs(relative_path):
        logger.warning(
            "credential_files: rejected absolute path %r (must be relative to KCLAW_HOME)",
            relative_path,
        )
        return False

    host_path = kclaw_home / relative_path

    # Resolve symlinks and normalise ``..`` before the containment check so
    # that traversal like ``../. ssh/id_rsa`` cannot escape KCLAW_HOME.
    try:
        resolved = host_path.resolve()
        kclaw_home_resolved = kclaw_home.resolve()
        resolved.relative_to(kclaw_home_resolved)  # raises ValueError if outside
    except ValueError:
        logger.warning(
            "credential_files: rejected path traversal %r "
            "(resolves to %s, outside KCLAW_HOME %s)",
            relative_path,
            resolved,
            kclaw_home_resolved,
        )
        return False

    if not resolved.is_file():
        logger.debug("credential_files: skipping %s (not found)", resolved)
        return False

    container_path = f"{container_base.rstrip('/')}/{relative_path}"
    _get_registered()[container_path] = str(resolved)
    logger.debug("credential_files: registered %s -> %s", resolved, container_path)
    return True


def register_credential_files(
    entries: list,
    container_base: str = "/root/.kclaw",
) -> List[str]:
    """从技能 frontmatter 条目注册多个凭证文件。

    每个条目可以是字符串（相对路径）或带有 ``path`` 键的字典。
    返回在主机上未找到的相对路径列表（即缺失的文件）。
    """
    missing = []
    for entry in entries:
        if isinstance(entry, str):
            rel_path = entry.strip()
        elif isinstance(entry, dict):
            rel_path = (entry.get("path") or entry.get("name") or "").strip()
        else:
            continue
        if not rel_path:
            continue
        if not register_credential_file(rel_path, container_base):
            missing.append(rel_path)
    return missing


def _load_config_files() -> List[Dict[str, str]]:
    """从 config.yaml 加载 ``terminal.credential_files``（已缓存）。"""
    global _config_files
    if _config_files is not None:
        return _config_files

    result: List[Dict[str, str]] = []
    try:
        from kclaw_cli.config import read_raw_config
        kclaw_home = _resolve_kclaw_home()
        cfg = read_raw_config()
        cred_files = cfg.get("terminal", {}).get("credential_files")
        if isinstance(cred_files, list):
            kclaw_home_resolved = kclaw_home.resolve()
            for item in cred_files:
                if isinstance(item, str) and item.strip():
                    rel = item.strip()
                    if os.path.isabs(rel):
                        logger.warning(
                            "credential_files: rejected absolute config path %r", rel,
                        )
                        continue
                    host_path = (kclaw_home / rel).resolve()
                    try:
                        host_path.relative_to(kclaw_home_resolved)
                    except ValueError:
                        logger.warning(
                            "credential_files: rejected config path traversal %r "
                            "(resolves to %s, outside KCLAW_HOME %s)",
                            rel, host_path, kclaw_home_resolved,
                        )
                        continue
                    if host_path.is_file():
                        container_path = f"/root/.kclaw/{rel}"
                        result.append({
                            "host_path": str(host_path),
                            "container_path": container_path,
                        })
    except Exception as e:
        logger.debug("Could not read terminal.credential_files from config: %s", e)

    _config_files = result
    return _config_files


def get_credential_file_mounts() -> List[Dict[str, str]]:
    """返回所有应挂载到远程沙箱的凭证文件。

    每个条目都有 ``host_path`` 和 ``container_path`` 键。
    结合了技能注册的文件和用户配置。
    """
    mounts: Dict[str, str] = {}

    # Skill-registered files
    for container_path, host_path in _get_registered().items():
        # Re-check existence (file may have been deleted since registration)
        if Path(host_path).is_file():
            mounts[container_path] = host_path

    # Config-based files
    for entry in _load_config_files():
        cp = entry["container_path"]
        if cp not in mounts and Path(entry["host_path"]).is_file():
            mounts[cp] = entry["host_path"]

    return [
        {"host_path": hp, "container_path": cp}
        for cp, hp in mounts.items()
    ]


def get_skills_directory_mount(
    container_base: str = "/root/.kclaw",
) -> list[Dict[str, str]]:
    """返回所有技能目录的挂载信息（本地 + 外部）。

    技能可能包含 ``scripts/``、``templates/`` 和 ``references/`` 子目录，
    agent 需要在远程沙箱内执行这些目录。

    **安全性：** 绑定挂载跟随符号链接，因此技能树内的恶意符号链接
    可能将任意主机文件暴露给容器。当检测到符号链接时，
    此函数会在临时目录中创建一个清理后的副本（仅常规文件）并返回该路径。
    当没有符号链接时（常见情况），直接返回原始目录，零开销。

    返回包含 ``host_path`` 和 ``container_path`` 键的字典列表。
    本地技能目录挂载在 ``<container_base>/skills``，
    外部目录挂载在 ``<container_base>/external_skills/<index>``。
    """
    mounts = []
    kclaw_home = _resolve_kclaw_home()
    skills_dir = kclaw_home / "skills"
    if skills_dir.is_dir():
        host_path = _safe_skills_path(skills_dir)
        mounts.append({
            "host_path": host_path,
            "container_path": f"{container_base.rstrip('/')}/skills",
        })

    # Mount external skill dirs
    try:
        from agent.skill_utils import get_external_skills_dirs
        for idx, ext_dir in enumerate(get_external_skills_dirs()):
            if ext_dir.is_dir():
                host_path = _safe_skills_path(ext_dir)
                mounts.append({
                    "host_path": host_path,
                    "container_path": f"{container_base.rstrip('/')}/external_skills/{idx}",
                })
    except ImportError:
        pass

    return mounts


_safe_skills_tempdir: Path | None = None


def _safe_skills_path(skills_dir: Path) -> str:
    """如果无符号链接则返回 *skills_dir*，否则返回清理后的临时副本。"""
    global _safe_skills_tempdir

    symlinks = [p for p in skills_dir.rglob("*") if p.is_symlink()]
    if not symlinks:
        return str(skills_dir)

    for link in symlinks:
        logger.warning("credential_files: skipping symlink in skills dir: %s -> %s",
                       link, os.readlink(link))

    import atexit
    import shutil
    import tempfile

    # Reuse the same temp dir across calls to avoid accumulation.
    if _safe_skills_tempdir and _safe_skills_tempdir.is_dir():
        shutil.rmtree(_safe_skills_tempdir, ignore_errors=True)

    safe_dir = Path(tempfile.mkdtemp(prefix="kclaw-skills-safe-"))
    _safe_skills_tempdir = safe_dir

    for item in skills_dir.rglob("*"):
        if item.is_symlink():
            continue
        rel = item.relative_to(skills_dir)
        target = safe_dir / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(target))

    def _cleanup():
        if safe_dir.is_dir():
            shutil.rmtree(safe_dir, ignore_errors=True)

    atexit.register(_cleanup)
    logger.info("credential_files: created symlink-safe skills copy at %s", safe_dir)
    return str(safe_dir)


def iter_skills_files(
    container_base: str = "/root/.kclaw",
) -> List[Dict[str, str]]:
    """为技能文件生成单独的 (host_path, container_path) 条目。

    包括本地技能目录和通过 skills.external_dirs 配置的任何外部目录。
    完全跳过符号链接。适用于单独上传文件的后端
   （Daytona、Modal），而非挂载目录。
    """
    result: List[Dict[str, str]] = []

    kclaw_home = _resolve_kclaw_home()
    skills_dir = kclaw_home / "skills"
    if skills_dir.is_dir():
        container_root = f"{container_base.rstrip('/')}/skills"
        for item in skills_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            rel = item.relative_to(skills_dir)
            result.append({
                "host_path": str(item),
                "container_path": f"{container_root}/{rel}",
            })

    # Include external skill dirs
    try:
        from agent.skill_utils import get_external_skills_dirs
        for idx, ext_dir in enumerate(get_external_skills_dirs()):
            if not ext_dir.is_dir():
                continue
            container_root = f"{container_base.rstrip('/')}/external_skills/{idx}"
            for item in ext_dir.rglob("*"):
                if item.is_symlink() or not item.is_file():
                    continue
                rel = item.relative_to(ext_dir)
                result.append({
                    "host_path": str(item),
                    "container_path": f"{container_root}/{rel}",
                })
    except ImportError:
        pass

    return result


# ---------------------------------------------------------------------------
# Cache directory mounts (documents, images, audio, screenshots)
# ---------------------------------------------------------------------------

# The four cache subdirectories that should be mirrored into remote backends.
# Each tuple is (new_subpath, old_name) matching kclaw_constants.get_kclaw_dir().
_CACHE_DIRS: list[tuple[str, str]] = [
    ("cache/documents", "document_cache"),
    ("cache/images", "image_cache"),
    ("cache/audio", "audio_cache"),
    ("cache/screenshots", "browser_screenshots"),
]


def get_cache_directory_mounts(
    container_base: str = "/root/.kclaw",
) -> List[Dict[str, str]]:
    """返回磁盘上存在的每个缓存目录的挂载条目。

    由 Docker 用于创建绑定挂载。每个条目都有 ``host_path`` 和
    ``container_path`` 键。主机路径通过 ``get_kclaw_dir()`` 解析，
    以保持与旧目录布局的向后兼容。
    """
    from kclaw_constants import get_kclaw_dir

    mounts: List[Dict[str, str]] = []
    for new_subpath, old_name in _CACHE_DIRS:
        host_dir = get_kclaw_dir(new_subpath, old_name)
        if host_dir.is_dir():
            # Always map to the *new* container layout regardless of host layout.
            container_path = f"{container_base.rstrip('/')}/{new_subpath}"
            mounts.append({
                "host_path": str(host_dir),
                "container_path": container_path,
            })
    return mounts


def iter_cache_files(
    container_base: str = "/root/.kclaw",
) -> List[Dict[str, str]]:
    """返回缓存文件的单独 (host_path, container_path) 条目。

    由 Modal 用于单独上传文件并在每个命令之前重新同步。
    跳过符号链接。容器路径使用新的 ``cache/<subdir>`` 布局。
    """
    from kclaw_constants import get_kclaw_dir

    result: List[Dict[str, str]] = []
    for new_subpath, old_name in _CACHE_DIRS:
        host_dir = get_kclaw_dir(new_subpath, old_name)
        if not host_dir.is_dir():
            continue
        container_root = f"{container_base.rstrip('/')}/{new_subpath}"
        for item in host_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            rel = item.relative_to(host_dir)
            result.append({
                "host_path": str(item),
                "container_path": f"{container_root}/{rel}",
            })
    return result


def clear_credential_files() -> None:
    """重置技能作用域的注册表（例如在会话重置时）。"""
    _get_registered().clear()


def reset_config_cache() -> None:
    """强制在下次访问时重新读取配置（用于测试）。"""
    global _config_files
    _config_files = None
