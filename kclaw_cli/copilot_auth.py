"""GitHub Copilot 认证工具。

实现 Copilot CLI 使用的 OAuth 设备代码流程，并处理
Copilot API 的令牌验证/交换。

令牌类型支持（根据 GitHub 文档）：
  gho_          OAuth 令牌           ✓  （默认通过 copilot login）
  github_pat_   细粒度 PAT          ✓  （需要 Copilot Requests 权限）
  ghu_          GitHub App 令牌      ✓  （通过环境变量）
  ghp_          经典 PAT            ✗  不支持

凭据搜索顺序（匹配 Copilot CLI 行为）：
  1. COPILOT_GITHUB_TOKEN 环境变量
  2. GH_TOKEN 环境变量
  3. GITHUB_TOKEN 环境变量
  4. gh auth token  CLI 回退
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# OAuth 设备代码流程常量（与 opencode/Copilot CLI 相同的客户端 ID）
COPILOT_OAUTH_CLIENT_ID = "Ov23li8tweQw6odWQebz"
COPILOT_DEVICE_CODE_URL = "https://github.com/login/device/code"
COPILOT_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

# Copilot API 常量
COPILOT_TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_API_BASE_URL = "https://api.githubcopilot.com"

# 令牌类型前缀
_CLASSIC_PAT_PREFIX = "ghp_"
_SUPPORTED_PREFIXES = ("gho_", "github_pat_", "ghu_")

# 环境变量搜索顺序（匹配 Copilot CLI）
COPILOT_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

# 轮询常量
_DEVICE_CODE_POLL_INTERVAL = 5  # seconds
_DEVICE_CODE_POLL_SAFETY_MARGIN = 3  # seconds


def is_classic_pat(token: str) -> bool:
    """检查令牌是否为经典 PAT（ghp_*），Copilot 不支持此类型。"""
    return token.strip().startswith(_CLASSIC_PAT_PREFIX)


def validate_copilot_token(token: str) -> tuple[bool, str]:
    """验证令牌是否可用于 Copilot API。

    返回 (有效, 消息)。
    """
    token = token.strip()
    if not token:
        return False, "空令牌"

    if token.startswith(_CLASSIC_PAT_PREFIX):
        return False, (
            "经典个人访问令牌（ghp_*）不被 Copilot API 支持。使用以下之一:\n"
            "  → `copilot login` 或 `kclaw model` 通过 OAuth 认证\n"
            "  → 具有 Copilot Requests 权限的细粒度 PAT（github_pat_*）\n"
            "  → `gh auth login` 使用默认设备代码流程（生成 gho_* 令牌）"
        )

    return True, "正常"


def resolve_copilot_token() -> tuple[str, str]:
    """解析适合 Copilot API 使用的 GitHub 令牌。

    返回 (令牌, 来源)，其中来源描述令牌的来源。
    如果只有经典 PAT 可用则抛出 ValueError。
    """
    # 1. 按优先级检查环境变量
    for env_var in COPILOT_ENV_VARS:
        val = os.getenv(env_var, "").strip()
        if val:
            valid, msg = validate_copilot_token(val)
            if not valid:
                logger.warning(
                    "Token from %s is not supported: %s", env_var, msg
                )
                continue
            return val, env_var

    # 2. 回退到 gh auth token
    token = _try_gh_cli_token()
    if token:
        valid, msg = validate_copilot_token(token)
        if not valid:
            raise ValueError(
                f"Token from `gh auth token` is a classic PAT (ghp_*). {msg}"
            )
        return token, "gh auth token"

    return "", ""


def _gh_cli_candidates() -> list[str]:
    """返回候选 ``gh`` 二进制路径，包括常见的 Homebrew 安装位置。"""
    candidates: list[str] = []

    resolved = shutil.which("gh")
    if resolved:
        candidates.append(resolved)

    for candidate in (
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        str(Path.home() / ".local" / "bin" / "gh"),
    ):
        if candidate in candidates:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            candidates.append(candidate)

    return candidates


def _try_gh_cli_token() -> Optional[str]:
    """当 GitHub CLI 可用时从 ``gh auth token`` 返回令牌。"""
    for gh_path in _gh_cli_candidates():
        try:
            result = subprocess.run(
                [gh_path, "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("gh CLI token lookup failed (%s): %s", gh_path, exc)
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


# ─── OAuth 设备代码流程 ────────────────────────────────────────────────

def copilot_device_code_login(
    *,
    host: str = "github.com",
    timeout_seconds: float = 300,
) -> Optional[str]:
    """运行 GitHub Copilot 的 OAuth 设备代码流程。

    打印用户说明，轮询完成情况，成功时返回
    OAuth 访问令牌，失败/取消时返回 None。

    这复刻了 opencode 和 Copilot CLI 使用的流程。
    """
    import urllib.request
    import urllib.parse

    domain = host.rstrip("/")
    device_code_url = f"https://{domain}/login/device/code"
    access_token_url = f"https://{domain}/login/oauth/access_token"

    # 步骤 1: 请求设备代码
    data = urllib.parse.urlencode({
        "client_id": COPILOT_OAUTH_CLIENT_ID,
        "scope": "read:user",
    }).encode()

    req = urllib.request.Request(
        device_code_url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "KClawAgent/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            device_data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.error("启动设备授权失败: %s", exc)
        print(f"  ✗ 启动设备授权失败: {exc}")
        return None

    verification_uri = device_data.get("verification_uri", "https://github.com/login/device")
    user_code = device_data.get("user_code", "")
    device_code = device_data.get("device_code", "")
    interval = max(device_data.get("interval", _DEVICE_CODE_POLL_INTERVAL), 1)

    if not device_code or not user_code:
        print("  ✗ GitHub 未返回设备代码。")
        return None

    # 步骤 2: 显示说明
    print()
    print(f"  在浏览器中打开此 URL: {verification_uri}")
    print(f"  输入此代码: {user_code}")
    print()
    print("  等待授权...", end="", flush=True)

    # 步骤 3: 轮询完成情况
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        time.sleep(interval + _DEVICE_CODE_POLL_SAFETY_MARGIN)

        poll_data = urllib.parse.urlencode({
            "client_id": COPILOT_OAUTH_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()

        poll_req = urllib.request.Request(
            access_token_url,
            data=poll_data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "KClawAgent/1.0",
            },
        )

        try:
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception:
            print(".", end="", flush=True)
            continue

        if result.get("access_token"):
            print(" ✓")
            return result["access_token"]

        error = result.get("error", "")
        if error == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error == "slow_down":
            # RFC 8628: 增加 5 秒到轮询间隔
            server_interval = result.get("interval")
            if isinstance(server_interval, (int, float)) and server_interval > 0:
                interval = int(server_interval)
            else:
                interval += 5
            print(".", end="", flush=True)
            continue
        elif error == "expired_token":
            print()
            print("  ✗ 设备代码已过期。请重试。")
            return None
        elif error == "access_denied":
            print()
            print("  ✗ 授权被拒绝。")
            return None
        elif error:
            print()
            print(f"  ✗ 授权失败: {error}")
            return None

    print()
    print("  ✗ 等待授权超时。")
    return None


# ─── Copilot API 头信息 ───────────────────────────────────────────────────

def copilot_request_headers(
    *,
    is_agent_turn: bool = True,
    is_vision: bool = False,
) -> dict[str, str]:
    """为 Copilot API 请求构建标准头信息。

    复刻 opencode 和 Copilot CLI 使用的头信息集。
    """
    headers: dict[str, str] = {
        "Editor-Version": "vscode/1.104.1",
        "User-Agent": "KClawAgent/1.0",
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent" if is_agent_turn else "user",
    }
    if is_vision:
        headers["Copilot-Vision-Request"] = "true"

    return headers
