"""
私信配对系统

用于在消息平台上授权新用户基于代码的审批流程。
不是带有用户 ID 的静态允许列表，未知用户会收到一次性
配对代码，所有者通过 CLI 审批。

安全功能（基于 OWASP + NIST SP 800-63-4 指南）：
  - 来自 32 字符无歧义字母表的 8 字符代码（无 0/O/1/I）
  - 通过 secrets.choice() 的加密随机性
  - 1 小时代码过期
  - 每个平台最多 3 个待处理代码
  - 速率限制：每个用户每 10 分钟 1 个请求
  - 5 次失败审批尝试后锁定（1 小时）
  - 文件权限：所有数据文件 chmod 0600
  - 代码从不记录到 stdout

存储：~/.kclaw/pairing/
"""

import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from kclaw_constants import get_kclaw_dir


# 无歧义字母表 — 排除 0/O、1/I 以防止混淆
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

# 时间常量
CODE_TTL_SECONDS = 3600             # 代码 1 小时后过期
RATE_LIMIT_SECONDS = 600            # 每个用户每 10 分钟 1 个请求
LOCKOUT_SECONDS = 3600              # 失败次数过多后的锁定时长

# 限制
MAX_PENDING_PER_PLATFORM = 3        # 每个平台最多待处理代码数
MAX_FAILED_ATTEMPTS = 5             # 锁定前的失败审批次数

PAIRING_DIR = get_kclaw_dir("platforms/pairing", "pairing")


def _secure_write(path: Path, data: str) -> None:
    """使用受限权限（仅所有者读写）将数据写入文件。

    使用临时文件 + 原子重命名，因此读者总是看到旧的
    完整文件或新的 — 永远不会看到部分写入。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows doesn't support chmod the same way
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class PairingStore:
    """
    管理配对代码和已批准用户列表。

    每个平台的数据文件：
      - {platform}-pending.json   : 待处理的配对请求
      - {platform}-approved.json  : 已批准（已配对）的用户
      - _rate_limits.json         : 速率限制跟踪
    """

    def __init__(self):
        PAIRING_DIR.mkdir(parents=True, exist_ok=True)
        # 保护所有读-修改-写周期。网关在共享一个 PairingStore 的
        # 线程中并发运行多个平台适配器。
        self._lock = threading.RLock()

    def _pending_path(self, platform: str) -> Path:
        return PAIRING_DIR / f"{platform}-pending.json"

    def _approved_path(self, platform: str) -> Path:
        return PAIRING_DIR / f"{platform}-approved.json"

    def _rate_limit_path(self) -> Path:
        return PAIRING_DIR / "_rate_limits.json"

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_json(self, path: Path, data: dict) -> None:
        _secure_write(path, json.dumps(data, indent=2, ensure_ascii=False))

    # ----- Approved users -----

    def is_approved(self, platform: str, user_id: str) -> bool:
        """检查用户是否在平台上被批准（已配对）。"""
        approved = self._load_json(self._approved_path(platform))
        return user_id in approved

    def list_approved(self, platform: str = None) -> list:
        """列出已批准的用户，可选择按平台过滤。"""
        results = []
        platforms = [platform] if platform else self._all_platforms("approved")
        for p in platforms:
            approved = self._load_json(self._approved_path(p))
            for uid, info in approved.items():
                results.append({"platform": p, "user_id": uid, **info})
        return results

    def _approve_user(self, platform: str, user_id: str, user_name: str = "") -> None:
        """将用户添加到批准列表。必须在 self._lock 下调用。"""
        approved = self._load_json(self._approved_path(platform))
        approved[user_id] = {
            "user_name": user_name,
            "approved_at": time.time(),
        }
        self._save_json(self._approved_path(platform), approved)

    def revoke(self, platform: str, user_id: str) -> bool:
        """从批准列表中移除用户。如果找到则返回 True。"""
        path = self._approved_path(platform)
        with self._lock:
            approved = self._load_json(path)
            if user_id in approved:
                del approved[user_id]
                self._save_json(path, approved)
                return True
        return False

    # ----- Pending codes -----

    def generate_code(
        self, platform: str, user_id: str, user_name: str = ""
    ) -> Optional[str]:
        """
        为新用户生成配对代码。

        返回代码字符串，或在以下情况下返回 None：
          - 用户被速率限制（请求太频繁）
          - 此平台已达到最大待处理代码数
          - 用户/平台因失败尝试而处于锁定状态
        """
        with self._lock:
            self._cleanup_expired(platform)

            # Check lockout
            if self._is_locked_out(platform):
                return None

            # Check rate limit for this specific user
            if self._is_rate_limited(platform, user_id):
                return None

            # Check max pending
            pending = self._load_json(self._pending_path(platform))
            if len(pending) >= MAX_PENDING_PER_PLATFORM:
                return None

            # Generate cryptographically random code
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))

            # Store pending request
            pending[code] = {
                "user_id": user_id,
                "user_name": user_name,
                "created_at": time.time(),
            }
            self._save_json(self._pending_path(platform), pending)

            # Record rate limit
            self._record_rate_limit(platform, user_id)

            return code

    def approve_code(self, platform: str, code: str) -> Optional[dict]:
        """
        审批配对代码。将用户添加到批准列表。

        成功时返回 {user_id, user_name}，如果代码无效/过期则返回 None。
        """
        with self._lock:
            self._cleanup_expired(platform)
            code = code.upper().strip()

            pending = self._load_json(self._pending_path(platform))
            if code not in pending:
                self._record_failed_attempt(platform)
                return None

            entry = pending.pop(code)
            self._save_json(self._pending_path(platform), pending)

            # Add to approved list
            self._approve_user(platform, entry["user_id"], entry.get("user_name", ""))

            return {
                "user_id": entry["user_id"],
                "user_name": entry.get("user_name", ""),
            }

    def list_pending(self, platform: str = None) -> list:
        """列出待处理的配对请求，可选择按平台过滤。"""
        results = []
        platforms = [platform] if platform else self._all_platforms("pending")
        for p in platforms:
            self._cleanup_expired(p)
            pending = self._load_json(self._pending_path(p))
            for code, info in pending.items():
                age_min = int((time.time() - info["created_at"]) / 60)
                results.append({
                    "platform": p,
                    "code": code,
                    "user_id": info["user_id"],
                    "user_name": info.get("user_name", ""),
                    "age_minutes": age_min,
                })
        return results

    def clear_pending(self, platform: str = None) -> int:
        """清除所有待处理请求。返回移除的数量。"""
        with self._lock:
            count = 0
            platforms = [platform] if platform else self._all_platforms("pending")
            for p in platforms:
                pending = self._load_json(self._pending_path(p))
                count += len(pending)
                self._save_json(self._pending_path(p), {})
        return count

    # ----- Rate limiting and lockout -----

    def _is_rate_limited(self, platform: str, user_id: str) -> bool:
        """检查用户是否最近请求了代码。"""
        limits = self._load_json(self._rate_limit_path())
        key = f"{platform}:{user_id}"
        last_request = limits.get(key, 0)
        return (time.time() - last_request) < RATE_LIMIT_SECONDS

    def _record_rate_limit(self, platform: str, user_id: str) -> None:
        """记录配对请求的时间以进行速率限制。"""
        limits = self._load_json(self._rate_limit_path())
        key = f"{platform}:{user_id}"
        limits[key] = time.time()
        self._save_json(self._rate_limit_path(), limits)

    def _is_locked_out(self, platform: str) -> bool:
        """检查平台是否因失败审批尝试而处于锁定状态。"""
        limits = self._load_json(self._rate_limit_path())
        lockout_key = f"_lockout:{platform}"
        lockout_until = limits.get(lockout_key, 0)
        return time.time() < lockout_until

    def _record_failed_attempt(self, platform: str) -> None:
        """记录失败的审批尝试。在达到 MAX_FAILED_ATTEMPTS 次后触发锁定。"""
        limits = self._load_json(self._rate_limit_path())
        fail_key = f"_failures:{platform}"
        fails = limits.get(fail_key, 0) + 1
        limits[fail_key] = fails
        if fails >= MAX_FAILED_ATTEMPTS:
            lockout_key = f"_lockout:{platform}"
            limits[lockout_key] = time.time() + LOCKOUT_SECONDS
            limits[fail_key] = 0  # 重置计数器
            print(f"[配对] 平台 {platform} 因 {MAX_FAILED_ATTEMPTS} 次失败尝试而被锁定 {LOCKOUT_SECONDS} 秒", flush=True)
        self._save_json(self._rate_limit_path(), limits)

    # ----- Cleanup -----

    def _cleanup_expired(self, platform: str) -> None:
        """移除过期的待处理代码。"""
        path = self._pending_path(platform)
        pending = self._load_json(path)
        now = time.time()
        expired = [
            code for code, info in pending.items()
            if (now - info["created_at"]) > CODE_TTL_SECONDS
        ]
        if expired:
            for code in expired:
                del pending[code]
            self._save_json(path, pending)

    def _all_platforms(self, suffix: str) -> list:
        """列出具有给定后缀数据文件的所有平台。"""
        platforms = []
        for f in PAIRING_DIR.iterdir():
            if f.name.endswith(f"-{suffix}.json"):
                platform = f.name.replace(f"-{suffix}.json", "")
                if not platform.startswith("_"):
                    platforms.append(platform)
        return platforms
