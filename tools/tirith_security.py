"""Tirith 执行前安全扫描包装器。

作为子进程运行 tirith 二进制文件以扫描命令中的内容级
威胁（同构 URL、管道到解释器、终端注入等）。

退出代码是裁决的真实来源：
  0 = 允许，1 = 阻止，2 = 警告

JSON stdout 丰富了发现/摘要，但从不覆盖裁决。
操作失败（生成错误、超时、未知退出代码）遵循
fail_open 配置设置。编程错误会传播。

自动安装：如果在 PATH 或配置路径上找不到 tirith，
则自动从 GitHub releases 下载到 $KCLAW_HOME/bin/tirith。
下载始终验证 SHA-256 校验和。当 cosign 在
PATH 上可用时，也执行来源验证（GitHub Actions 工作流签名）。
如果 cosign 未安装，下载仅使用 SHA-256 验证继续 —
仍然通过 HTTPS + 校验和安全，只是没有供应链来源证明。
安装在后台线程中运行，因此启动永远不会阻塞。
"""

import hashlib
import json
import logging
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request

from kclaw_constants import get_kclaw_home

logger = logging.getLogger(__name__)

_REPO = "sheeki03/tirith"

# Cosign 来源验证 — 固定到特定发布工作流
_COSIGN_IDENTITY_REGEXP = f"^https://github.com/{_REPO}/\\.github/workflows/release\\.yml@refs/tags/v"
_COSIGN_ISSUER = "https://token.actions.githubusercontent.com"

# ---------------------------------------------------------------------------
# 配置辅助函数
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _load_security_config() -> dict:
    """从 config.yaml 加载安全设置，优先使用环境变量覆盖。"""
    defaults = {
        "tirith_enabled": True,
        "tirith_path": "tirith",
        "tirith_timeout": 5,
        "tirith_fail_open": True,
    }
    try:
        from kclaw_cli.config import load_config
        cfg = load_config().get("security", {}) or {}
    except Exception:
        cfg = {}

    return {
        "tirith_enabled": _env_bool("TIRITH_ENABLED", cfg.get("tirith_enabled", defaults["tirith_enabled"])),
        "tirith_path": os.getenv("TIRITH_BIN", cfg.get("tirith_path", defaults["tirith_path"])),
        "tirith_timeout": _env_int("TIRITH_TIMEOUT", cfg.get("tirith_timeout", defaults["tirith_timeout"])),
        "tirith_fail_open": _env_bool("TIRITH_FAIL_OPEN", cfg.get("tirith_fail_open", defaults["tirith_fail_open"])),
    }


# ---------------------------------------------------------------------------
# 自动安装
# ---------------------------------------------------------------------------

# 首次解析后的缓存路径（避免每个命令重复 shutil.which）。
# _INSTALL_FAILED 意味着"我们尝试过但失败了" — 防止每次命令重试。
_resolved_path: str | None | bool = None
_INSTALL_FAILED = False  # 标记：与"尚未尝试"不同
_install_failure_reason: str = ""  # 当 _resolved_path 是 _INSTALL_FAILED 时的原因标签

# 后台安装线程协调
_install_lock = threading.Lock()
_install_thread: threading.Thread | None = None

# 磁盘持久化失败标记 — 避免跨进程重启重试
_MARKER_TTL = 86400  # 24 小时


def _get_kclaw_home() -> str:
    """返回 KClaw 主目录，优先使用 KCLAW_HOME 环境变量。"""
    return str(get_kclaw_home())


def _failure_marker_path() -> str:
    """返回安装失败标记文件的路径。"""
    return os.path.join(_get_kclaw_home(), ".tirith-install-failed")


def _read_failure_reason() -> str | None:
    """从磁盘标记读取失败原因。

    返回原因字符串，如果标记不存在或超过
    _MARKER_TTL 则返回 None。
    """
    try:
        p = _failure_marker_path()
        mtime = os.path.getmtime(p)
        if (time.time() - mtime) >= _MARKER_TTL:
            return None
        with open(p, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def _is_install_failed_on_disk() -> bool:
    """检查最近的安装失败是否已持久化到磁盘。

    在以下情况下返回 False（允许重试）：
    - 不存在标记
    - 标记超过 _MARKER_TTL（24h）
    - 标记原因为 'cosign_missing' 且 cosign 现在在 PATH 上
    """
    reason = _read_failure_reason()
    if reason is None:
        return False
    if reason == "cosign_missing" and shutil.which("cosign"):
        _clear_install_failed()
        return False
    return True


def _mark_install_failed(reason: str = ""):
    """将安装失败持久化到磁盘以避免下次进程重试。

    参数:
        reason: 标识失败原因的简短标签。当 cosign 不在 PATH 上时使用
                "cosign_missing"，以便一旦 cosign 可用时标记可以自动清除。
    """
    try:
        p = _failure_marker_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(reason)
    except OSError:
        pass


def _clear_install_failed():
    """成功安装后删除失败标记。"""
    try:
        os.unlink(_failure_marker_path())
    except OSError:
        pass


def _kclaw_bin_dir() -> str:
    """返回 $KCLAW_HOME/bin，必要时创建。"""
    d = os.path.join(_get_kclaw_home(), "bin")
    os.makedirs(d, exist_ok=True)
    return d


def _detect_target() -> str | None:
    """返回当前平台的 Rust 目标三元组，或 None。"""
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin":
        plat = "apple-darwin"
    elif system == "Linux":
        plat = "unknown-linux-gnu"
    else:
        return None

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        return None

    return f"{arch}-{plat}"


def _download_file(url: str, dest: str, timeout: int = 10):
    """将 URL 下载到本地文件。"""
    req = urllib.request.Request(url)
    token = os.getenv("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _verify_cosign(checksums_path: str, sig_path: str, cert_path: str) -> bool | None:
    """验证 checksums.txt 上的 cosign 来源签名。

    返回:
        True  — cosign 验证成功
        False — 找到 cosign 但验证失败
        None  — cosign 不可用（不在 PATH 上，或执行失败）

    调用者将 False 和 None 都视为"中止自动安装" — 只有
    True 允许安装继续。
    """
    cosign = shutil.which("cosign")
    if not cosign:
        logger.info("cosign not found on PATH")
        return None

    try:
        result = subprocess.run(
            [cosign, "verify-blob",
             "--certificate", cert_path,
             "--signature", sig_path,
             "--certificate-identity-regexp", _COSIGN_IDENTITY_REGEXP,
             "--certificate-oidc-issuer", _COSIGN_ISSUER,
             checksums_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("cosign provenance verification passed")
            return True
        else:
            logger.warning("cosign verification failed (exit %d): %s",
                          result.returncode, result.stderr.strip())
            return False
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("cosign execution failed: %s", exc)
        return None


def _verify_checksum(archive_path: str, checksums_path: str, archive_name: str) -> bool:
    """根据 checksums.txt 验证存档的 SHA-256。"""
    expected = None
    with open(checksums_path) as f:
        for line in f:
            # 格式: "<hash>  <filename>"
            parts = line.strip().split("  ", 1)
            if len(parts) == 2 and parts[1] == archive_name:
                expected = parts[0]
                break
    if not expected:
        logger.warning("No checksum entry for %s", archive_name)
        return False

    sha = hashlib.sha256()
    with open(archive_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    actual = sha.hexdigest()
    if actual != expected:
        logger.warning("Checksum mismatch: expected %s, got %s", expected, actual)
        return False
    return True


def _install_tirith(*, log_failures: bool = True) -> tuple[str | None, str]:
    """下载并安装 tirith 到 $KCLAW_HOME/bin/tirith。

    通过 cosign 和 SHA-256 校验和验证来源。
    返回 (installed_path, failure_reason)。成功时 failure_reason 为 ""。
    failure_reason 是一个短标签，由磁盘标记使用以决定
    失败是否可重试（例如"cosign_missing"在 cosign 出现时清除）。
    """
    log = logger.warning if log_failures else logger.debug

    target = _detect_target()
    if not target:
        logger.info("tirith auto-install: unsupported platform %s/%s",
                     platform.system(), platform.machine())
        return None, "unsupported_platform"

    archive_name = f"tirith-{target}.tar.gz"
    base_url = f"https://github.com/{_REPO}/releases/latest/download"

    tmpdir = tempfile.mkdtemp(prefix="tirith-install-")
    try:
        archive_path = os.path.join(tmpdir, archive_name)
        checksums_path = os.path.join(tmpdir, "checksums.txt")
        sig_path = os.path.join(tmpdir, "checksums.txt.sig")
        cert_path = os.path.join(tmpdir, "checksums.txt.pem")

        logger.info("tirith not found — downloading latest release for %s...", target)

        try:
            _download_file(f"{base_url}/{archive_name}", archive_path)
            _download_file(f"{base_url}/checksums.txt", checksums_path)
        except Exception as exc:
            log("tirith download failed: %s", exc)
            return None, "download_failed"

        # Cosign 来源验证 — 首选但非强制。
        # 当 cosign 可用时，我们验证发布是否由
        # 预期的 GitHub Actions 工作流生成（完整供应链证明）。
        # 没有 cosign，SHA-256 校验和 + HTTPS 仍然提供完整性
        # 和传输层真实性。
        cosign_verified = False
        if shutil.which("cosign"):
            try:
                _download_file(f"{base_url}/checksums.txt.sig", sig_path)
                _download_file(f"{base_url}/checksums.txt.pem", cert_path)
            except Exception as exc:
                logger.info("cosign artifacts unavailable (%s), proceeding with SHA-256 only", exc)
            else:
                cosign_result = _verify_cosign(checksums_path, sig_path, cert_path)
                if cosign_result is True:
                    cosign_verified = True
                elif cosign_result is False:
                    # 验证明确拒绝 — 中止，发布
                    # 可能已被篡改。
                    log("tirith install aborted: cosign provenance verification failed")
                    return None, "cosign_verification_failed"
                else:
                    # None = 执行失败（timeout/OSError）— 继续
                    # 仅使用 SHA-256，因为 cosign 本身已损坏。
                    logger.info("cosign execution failed, proceeding with SHA-256 only")
        else:
            logger.info("cosign not on PATH — installing tirith with SHA-256 verification only "
                        "(install cosign for full supply chain verification)")

        if not _verify_checksum(archive_path, checksums_path, archive_name):
            return None, "checksum_failed"

        with tarfile.open(archive_path, "r:gz") as tar:
            # 仅提取 tirith 二进制文件（安全：拒绝包含 .. 的路径）
            for member in tar.getmembers():
                if member.name == "tirith" or member.name.endswith("/tirith"):
                    if ".." in member.name:
                        continue
                    member.name = "tirith"
                    tar.extract(member, tmpdir)
                    break
            else:
                log("tirith binary not found in archive")
                return None, "binary_not_in_archive"

        src = os.path.join(tmpdir, "tirith")
        dest = os.path.join(_kclaw_bin_dir(), "tirith")
        shutil.move(src, dest)
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        verification = "cosign + SHA-256" if cosign_verified else "SHA-256 only"
        logger.info("tirith installed to %s (%s)", dest, verification)
        return dest, ""

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _is_explicit_path(configured_path: str) -> bool:
    """如果用户明确配置了非默认的 tirith 路径则返回 True。"""
    return configured_path != "tirith"


def _resolve_tirith_path(configured_path: str) -> str:
    """解析 tirith 二进制路径，必要时自动安装。

    如果用户明确设置了路径（除了裸的 "tirith" 默认值之外的任何内容），
    该路径是权威的 — 我们永远不会回退到自动下载不同的二进制文件。

    对于默认的 "tirith"：
    1. 通过 shutil.which 进行 PATH 查找
    2. $KCLAW_HOME/bin/tirith（之前自动安装的）
    3. 从 GitHub releases 自动安装 → $KCLAW_HOME/bin/tirith

    失败的安装被缓存在进程生命周期中（并持久化到磁盘 24h）
    以避免重复的网络尝试。
    """
    global _resolved_path, _install_failure_reason

    # 快速路径：之前调用已成功解析。
    if _resolved_path is not None and _resolved_path is not _INSTALL_FAILED:
        return _resolved_path

    expanded = os.path.expanduser(configured_path)
    explicit = _is_explicit_path(configured_path)
    install_failed = _resolved_path is _INSTALL_FAILED

    # 显式路径：检查它并停止。永不自动下载替换品。
    if explicit:
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            _resolved_path = expanded
            return expanded
        # 也尝试 shutil.which，以防它是 PATH 上的裸名称
        found = shutil.which(expanded)
        if found:
            _resolved_path = found
            return found
        logger.warning("Configured tirith path %r not found; scanning disabled", configured_path)
        _resolved_path = _INSTALL_FAILED
        _install_failure_reason = "explicit_path_missing"
        return expanded

    # 默认 "tirith" — 始终重新运行廉价的本地检查，以便即使在之前的
    # 网络失败后也能拾取手动安装（P2 修复：长期运行的 gateway/CLI 无需重启即可恢复）。
    found = shutil.which("tirith")
    if found:
        _resolved_path = found
        _install_failure_reason = ""
        _clear_install_failed()
        return found

    kclaw_bin = os.path.join(_kclaw_bin_dir(), "tirith")
    if os.path.isfile(kclaw_bin) and os.access(kclaw_bin, os.X_OK):
        _resolved_path = kclaw_bin
        _install_failure_reason = ""
        _clear_install_failed()
        return kclaw_bin

    # 本地检查失败。如果之前的安装尝试已经失败，
    # 跳过网络重试 — 除非失败是 "cosign_missing" 且
    # cosign 现在可用（可重试原因在进程内已解决）。
    if install_failed:
        if _install_failure_reason == "cosign_missing" and shutil.which("cosign"):
            # 可重试原因已解决 — 清除标记并继续重试
            _resolved_path = None
            _install_failure_reason = ""
            _clear_install_failed()
            install_failed = False
        else:
            return expanded

    # 如果后台安装线程正在运行，不要启动并行线程 —
    # 返回配置的路径；check_command_security 中的 OSError 处理程序
    # 将应用 fail_open 直到线程完成。
    if _install_thread is not None and _install_thread.is_alive():
        return expanded

    # 在尝试网络下载前检查磁盘失败标记。
    # 保留标记的真实原因，以便内存重试逻辑可以
    # 检测可重试原因（例如 cosign_missing）而无需重启。
    disk_reason = _read_failure_reason()
    if disk_reason is not None and _is_install_failed_on_disk():
        _resolved_path = _INSTALL_FAILED
        _install_failure_reason = disk_reason
        return expanded

    installed, reason = _install_tirith()
    if installed:
        _resolved_path = installed
        _install_failure_reason = ""
        _clear_install_failed()
        return installed

    # 安装失败 — 缓存未命中并将原因持久化到磁盘
    _resolved_path = _INSTALL_FAILED
    _install_failure_reason = reason
    _mark_install_failed(reason)
    return expanded


def _background_install(*, log_failures: bool = True):
    """后台线程目标：下载并安装 tirith。"""
    global _resolved_path, _install_failure_reason
    with _install_lock:
        # 获取锁后双重检查（另一个线程可能已解析）
        if _resolved_path is not None:
            return

        # 重新检查本地路径（可能已由另一个进程安装）
        found = shutil.which("tirith")
        if found:
            _resolved_path = found
            _install_failure_reason = ""
            return

        kclaw_bin = os.path.join(_kclaw_bin_dir(), "tirith")
        if os.path.isfile(kclaw_bin) and os.access(kclaw_bin, os.X_OK):
            _resolved_path = kclaw_bin
            _install_failure_reason = ""
            return

        installed, reason = _install_tirith(log_failures=log_failures)
        if installed:
            _resolved_path = installed
            _install_failure_reason = ""
            _clear_install_failed()
        else:
            _resolved_path = _INSTALL_FAILED
            _install_failure_reason = reason
            _mark_install_failed(reason)


def ensure_installed(*, log_failures: bool = True):
    """确保 tirith 可用，必要时在后台下载。

    快速 PATH/本地检查是同步的；网络下载在守护线程中运行，
    因此启动永远不会阻塞。可安全多次调用。
    如果可用则立即返回解析后的路径，或 None。
    """
    global _resolved_path, _install_thread, _install_failure_reason

    cfg = _load_security_config()
    if not cfg["tirith_enabled"]:
        return None

    # 已从之前的调用解析
    if _resolved_path is not None and _resolved_path is not _INSTALL_FAILED:
        path = _resolved_path
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        return None

    configured_path = cfg["tirith_path"]
    explicit = _is_explicit_path(configured_path)
    expanded = os.path.expanduser(configured_path)

    # 显式路径：仅同步检查，不下载
    if explicit:
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            _resolved_path = expanded
            return expanded
        found = shutil.which(expanded)
        if found:
            _resolved_path = found
            return found
        _resolved_path = _INSTALL_FAILED
        _install_failure_reason = "explicit_path_missing"
        return None

    # 默认 "tirith" — 首先快速本地检查（不涉及网络）
    found = shutil.which("tirith")
    if found:
        _resolved_path = found
        _install_failure_reason = ""
        _clear_install_failed()
        return found

    kclaw_bin = os.path.join(_kclaw_bin_dir(), "tirith")
    if os.path.isfile(kclaw_bin) and os.access(kclaw_bin, os.X_OK):
        _resolved_path = kclaw_bin
        _install_failure_reason = ""
        _clear_install_failed()
        return kclaw_bin

    # 如果之前在内存中失败，检查原因是否现在已解决
    if _resolved_path is _INSTALL_FAILED:
        if _install_failure_reason == "cosign_missing" and shutil.which("cosign"):
            _resolved_path = None
            _install_failure_reason = ""
            _clear_install_failed()
        else:
            return None

    # 检查磁盘失败标记（24 小时内跳过网络尝试，除非
    # cosign_missing 原因已解决 — 由 _is_install_failed_on_disk 处理）。
    # 为内存重试逻辑保留标记的真实原因。
    disk_reason = _read_failure_reason()
    if disk_reason is not None and _is_install_failed_on_disk():
        _resolved_path = _INSTALL_FAILED
        _install_failure_reason = disk_reason
        return None

    # 需要下载 — 启动后台线程以使启动不阻塞
    if _install_thread is None or not _install_thread.is_alive():
        _install_thread = threading.Thread(
            target=_background_install,
            kwargs={"log_failures": log_failures},
            daemon=True,
        )
        _install_thread.start()

    return None  # 尚不可用；命令将失败开放直到就绪


# ---------------------------------------------------------------------------
# 主 API
# ---------------------------------------------------------------------------

_MAX_FINDINGS = 50
_MAX_SUMMARY_LEN = 500


def check_command_security(command: str) -> dict:
    """对命令运行 tirith 安全扫描。

    退出代码决定动作（0=允许，1=阻止，2=警告）。JSON 丰富了
    findings/summary。生成失败和超时遵循 fail_open 配置。
    编程错误会传播。

    返回:
        {"action": "allow"|"warn"|"block", "findings": [...], "summary": str}
    """
    cfg = _load_security_config()

    if not cfg["tirith_enabled"]:
        return {"action": "allow", "findings": [], "summary": ""}

    tirith_path = _resolve_tirith_path(cfg["tirith_path"])
    timeout = cfg["tirith_timeout"]
    fail_open = cfg["tirith_fail_open"]

    try:
        result = subprocess.run(
            [tirith_path, "check", "--json", "--non-interactive",
             "--shell", "posix", "--", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except OSError as exc:
        # 涵盖 FileNotFoundError、PermissionError、exec 格式错误
        logger.warning("tirith spawn failed: %s", exc)
        if fail_open:
            return {"action": "allow", "findings": [], "summary": f"tirith unavailable: {exc}"}
        return {"action": "block", "findings": [], "summary": f"tirith spawn failed (fail-closed): {exc}"}
    except subprocess.TimeoutExpired:
        logger.warning("tirith timed out after %ds", timeout)
        if fail_open:
            return {"action": "allow", "findings": [], "summary": f"tirith timed out ({timeout}s)"}
        return {"action": "block", "findings": [], "summary": "tirith timed out (fail-closed)"}

    # 将退出代码映射到动作
    exit_code = result.returncode
    if exit_code == 0:
        action = "allow"
    elif exit_code == 1:
        action = "block"
    elif exit_code == 2:
        action = "warn"
    else:
        # Unknown exit code — respect fail_open
        logger.warning("tirith returned unexpected exit code %d", exit_code)
        if fail_open:
            return {"action": "allow", "findings": [], "summary": f"tirith exit code {exit_code} (fail-open)"}
        return {"action": "block", "findings": [], "summary": f"tirith exit code {exit_code} (fail-closed)"}

    # 解析 JSON 以进行丰富（从不覆盖退出代码裁决）
    findings = []
    summary = ""
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        raw_findings = data.get("findings", [])
        findings = raw_findings[:_MAX_FINDINGS]
        summary = (data.get("summary", "") or "")[:_MAX_SUMMARY_LEN]
    except (json.JSONDecodeError, AttributeError):
        # JSON 解析失败会降级 findings/summary，而不是裁决
        logger.debug("tirith JSON parse failed, using exit code only")
        if action == "block":
            summary = "security issue detected (details unavailable)"
        elif action == "warn":
            summary = "security warning detected (details unavailable)"

    return {"action": action, "findings": findings, "summary": summary}
