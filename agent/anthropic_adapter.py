"""KClaw Agent 的 Anthropic Messages API 适配器。

在 KClaw 内部的 OpenAI 风格消息格式与 Anthropic 的 Messages API
之间进行转换。遵循与 codex_responses 适配器相同的模式 —
所有提供者特定的逻辑都被隔离在此处。

认证支持:
  - 常规 API 密钥 (sk-ant-api*) → x-api-key 头
  - OAuth setup-token (sk-ant-oat*) → Bearer 认证 + beta 头
  - Claude Code 凭据 (~/.claude.json 或 ~/.claude/.credentials.json) → Bearer 认证
"""

import copy
import json
import logging
import os
from pathlib import Path

from kclaw_constants import get_kclaw_home
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

try:
    import anthropic as _anthropic_sdk
except ImportError:
    _anthropic_sdk = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

THINKING_BUDGET = {"xhigh": 32000, "high": 16000, "medium": 8000, "low": 4000}
ADAPTIVE_EFFORT_MAP = {
    "xhigh": "max",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "minimal": "low",
}

# ── 每个 Anthropic 模型的最大输出 token 限制 ───────────────────────
# 来源：Anthropic 文档 + Cline 模型目录。Anthropic 的 API 要求
# max_tokens 作为必填字段。之前我们硬编码 16384，这会
# 导致启用思考的模型饥饿（思考 token 计入限制）。
_ANTHROPIC_OUTPUT_LIMITS = {
    # Claude 4.6
    "claude-opus-4-6":   128_000,
    "claude-sonnet-4-6":  64_000,
    # Claude 4.5
    "claude-opus-4-5":    64_000,
    "claude-sonnet-4-5":  64_000,
    "claude-haiku-4-5":   64_000,
    # Claude 4
    "claude-opus-4":      32_000,
    "claude-sonnet-4":    64_000,
    # Claude 3.7
    "claude-3-7-sonnet": 128_000,
    # Claude 3.5
    "claude-3-5-sonnet":   8_192,
    "claude-3-5-haiku":    8_192,
    # Claude 3
    "claude-3-opus":       4_096,
    "claude-3-sonnet":     4_096,
    "claude-3-haiku":      4_096,
}

# 对于表中未列出的模型，假设使用当前最高限制。
# 未来 Anthropic 模型不太可能有更少的输出能力。
_ANTHROPIC_DEFAULT_OUTPUT_LIMIT = 128_000


def _get_anthropic_max_output(model: str) -> int:
    """查找 Anthropic 模型的最大输出 token 限制。

    使用与 _ANTHROPIC_OUTPUT_LIMITS 的子字符串匹配，以便带日期戳的
    模型 ID（claude-sonnet-4-5-20250929）和变体后缀（:1m, :fast）
    能正确解析。最长前缀匹配优先，避免如 "claude-3-5"
    在 "claude-3-5-sonnet" 之前匹配。
    """
    m = model.lower()
    best_key = ""
    best_val = _ANTHROPIC_DEFAULT_OUTPUT_LIMIT
    for key, val in _ANTHROPIC_OUTPUT_LIMITS.items():
        if key in m and len(key) > len(best_key):
            best_key = key
            best_val = val
    return best_val


def _supports_adaptive_thinking(model: str) -> bool:
    """对于支持自适应思考的 Claude 4.6 模型返回 True。"""
    return any(v in model for v in ("4-6", "4.6"))


# 增强功能的 Beta 头（所有认证类型都会发送）
_COMMON_BETAS = [
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
]

# OAuth/订阅认证所需的额外 beta 头。
# 匹配 Claude Code（以及 pi-ai / OpenCode）发送的内容。
_OAUTH_ONLY_BETAS = [
    "claude-code-20250219",
    "oauth-2025-04-20",
]

# Claude Code 身份 — OAuth 请求正确路由所需。
# 没有这些，Anthropic 的基础设施会间歇性地对 OAuth 流量返回 500。
# 版本必须保持合理更新 — 当伪装的 user-agent 版本太落后时，
# Anthropic 会拒绝 OAuth 请求。
_CLAUDE_CODE_VERSION_FALLBACK = "2.1.74"
_claude_code_version_cache: Optional[str] = None


def _detect_claude_code_version() -> str:
    """检测已安装的 Claude Code 版本，回退到静态常量。

    Anthropic 的 OAuth 基础设施验证 user-agent 版本，可能会
    拒绝版本过旧的请求。动态检测意味着保持 Claude Code 更新的
    用户永远不会遇到过期版本的 400 错误。
    """
    import subprocess as _sp

    for cmd in ("claude", "claude-code"):
        try:
            result = _sp.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # 输出如 "2.1.74 (Claude Code)" 或纯 "2.1.74"
                version = result.stdout.strip().split()[0]
                if version and version[0].isdigit():
                    return version
        except Exception:
            pass
    return _CLAUDE_CODE_VERSION_FALLBACK


_CLAUDE_CODE_SYSTEM_PREFIX = "You are KClaw, a Super Agent independently developed by kkutysllb."
_MCP_TOOL_PREFIX = "mcp_"


def _get_claude_code_version() -> str:
    """当 OAuth 头需要时，惰性检测已安装的 Claude Code 版本。"""
    global _claude_code_version_cache
    if _claude_code_version_cache is None:
        _claude_code_version_cache = _detect_claude_code_version()
    return _claude_code_version_cache


def _is_oauth_token(key: str) -> bool:
    """检查密钥是否是 OAuth/setup token（而非常规 Console API 密钥）。

    常规 API 密钥以 'sk-ant-api' 开头。其他所有内容（以 'sk-ant-oat'
    开头的 setup-token、托管密钥、JWT 等）需要 Bearer 认证。
    """
    if not key:
        return False
    # 常规 Console API 密钥使用 x-api-key 头
    if key.startswith("sk-ant-api"):
        return False
    # 其他所有内容（setup-token、托管密钥、JWT）使用 Bearer 认证
    return True


def _normalize_base_url_text(base_url) -> str:
    """将 SDK/base 传输 URL 值规范化为纯字符串以供检查。

    某些客户端对象将 ``base_url`` 暴露为 ``httpx.URL`` 而非原始
    字符串。提供者/认证检测应接受两种格式。
    """
    if not base_url:
        return ""
    return str(base_url).strip()


def _is_third_party_anthropic_endpoint(base_url: str | None) -> bool:
    """对于使用 Anthropic Messages API 的非 Anthropic 端点返回 True。

    第三方代理（Azure AI Foundry、AWS Bedrock、自托管）使用自己的
    API 密钥通过 x-api-key 认证，而非 Anthropic OAuth token。
    对于这些端点应跳过 OAuth 检测。
    """
    normalized = _normalize_base_url_text(base_url)
    if not normalized:
        return False  # 无 base_url = 直接 Anthropic API
    normalized = normalized.rstrip("/").lower()
    if "anthropic.com" in normalized:
        return False  # 直接 Anthropic API — OAuth 适用
    return True  # 任何其他端点是第三方代理


def _requires_bearer_auth(base_url: str | None) -> bool:
    """对于需要 Bearer 认证的 Anthropic 兼容提供者返回 True。

    某些第三方 /anthropic 端点实现了 Anthropic 的 Messages API，但
    需要 Authorization: Bearer 而非 Anthropic 原生的 x-api-key 头。
    MiniMax 的全球和中国 Anthropic 兼容端点遵循此模式。
    """
    normalized = _normalize_base_url_text(base_url)
    if not normalized:
        return False
    normalized = normalized.rstrip("/").lower()
    return normalized.startswith(("https://api.minimax.io/anthropic", "https://api.minimaxi.com/anthropic"))


def build_anthropic_client(api_key: str, base_url: str = None):
    """创建 Anthropic 客户端，自动检测 setup-token 与 API 密钥。

    返回 anthropic.Anthropic 实例。
    """
    if _anthropic_sdk is None:
        raise ImportError(
            "'anthropic' 包是 Anthropic 提供者所必需的。"
            "请使用 pip install 'anthropic>=0.39.0' 安装"
        )
    from httpx import Timeout

    normalized_base_url = _normalize_base_url_text(base_url)
    kwargs = {
        "timeout": Timeout(timeout=900.0, connect=10.0),
    }
    if normalized_base_url:
        kwargs["base_url"] = normalized_base_url

    if _requires_bearer_auth(normalized_base_url):
        # 某些 Anthropic 兼容提供者（如 MiniMax）期望 API 密钥在
        # Authorization: Bearer 中，即使对于常规 API 密钥也是如此。将
        # 这些端点路由到 auth_token，以便 SDK 发送 Bearer 认证而非 x-api-key。
        # 在 OAuth token 形状检测之前检查此项，因为 MiniMax 密钥不使用
        # Anthropic 的 sk-ant-api 前缀，否则会被误读为
        # Anthropic OAuth/setup token。
        kwargs["auth_token"] = api_key
        if _COMMON_BETAS:
            kwargs["default_headers"] = {"anthropic-beta": ",".join(_COMMON_BETAS)}
    elif _is_third_party_anthropic_endpoint(base_url):
        # 第三方代理（Azure AI Foundry、AWS Bedrock 等）使用自己的
        # API 密钥通过 x-api-key 认证。跳过 OAuth 检测 — 它们的密钥
        # 不遵循 Anthropic 的 sk-ant-* 前缀约定，会被误分类为
        # OAuth token。
        kwargs["api_key"] = api_key
        if _COMMON_BETAS:
            kwargs["default_headers"] = {"anthropic-beta": ",".join(_COMMON_BETAS)}
    elif _is_oauth_token(api_key):
        # OAuth 访问 token / setup-token → Bearer 认证 + Claude Code 身份。
        # Anthropic 根据 user-agent 和头信息路由 OAuth 请求；
        # 没有 Claude Code 的指纹，请求会间歇性收到 500 错误。
        all_betas = _COMMON_BETAS + _OAUTH_ONLY_BETAS
        kwargs["auth_token"] = api_key
        kwargs["default_headers"] = {
            "anthropic-beta": ",".join(all_betas),
            "user-agent": f"claude-cli/{_get_claude_code_version()} (external, cli)",
            "x-app": "cli",
        }
    else:
        # 常规 API 密钥 → x-api-key 头 + 公共 beta
        kwargs["api_key"] = api_key
        if _COMMON_BETAS:
            kwargs["default_headers"] = {"anthropic-beta": ",".join(_COMMON_BETAS)}

    return _anthropic_sdk.Anthropic(**kwargs)


def read_claude_code_credentials() -> Optional[Dict[str, Any]]:
    """从 ~/.claude/.credentials.json 读取可刷新的 Claude Code OAuth 凭据。

    这有意排除 ~/.claude.json 的 primaryApiKey。OpenCode 的
    订阅流程是基于 OAuth/setup-token 的，使用可刷新凭据，
    原生直接 Anthropic 提供者使用也应遵循该路径，而不是
    自动检测 Claude 的第一方托管密钥。

    返回包含 {accessToken, refreshToken?, expiresAt?} 的字典，或 None。
    """
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            oauth_data = data.get("claudeAiOauth")
            if oauth_data and isinstance(oauth_data, dict):
                access_token = oauth_data.get("accessToken", "")
                if access_token:
                    return {
                        "accessToken": access_token,
                        "refreshToken": oauth_data.get("refreshToken", ""),
                        "expiresAt": oauth_data.get("expiresAt", 0),
                        "source": "claude_code_credentials_file",
                    }
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug("读取 ~/.claude/.credentials.json 失败：%s", e)

    return None


def read_claude_managed_key() -> Optional[str]:
    """从 ~/.claude.json 读取 Claude 的原生托管密钥（仅用于诊断）。"""
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
            primary_key = data.get("primaryApiKey", "")
            if isinstance(primary_key, str) and primary_key.strip():
                return primary_key.strip()
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug("读取 ~/.claude.json 失败：%s", e)
    return None


def is_claude_code_token_valid(creds: Dict[str, Any]) -> bool:
    """检查 Claude Code 凭据是否有过期时间且未过期的访问 token。"""
    import time

    expires_at = creds.get("expiresAt", 0)
    if not expires_at:
        # 无过期时间设置（托管密钥）— 如有 token 即有效
        return bool(creds.get("accessToken"))

    # expiresAt 是自纪元以来的毫秒数
    now_ms = int(time.time() * 1000)
    # 允许 60 秒缓冲
    return now_ms < (expires_at - 60_000)


def refresh_anthropic_oauth_pure(refresh_token: str, *, use_json: bool = False) -> Dict[str, Any]:
    """刷新 Anthropic OAuth token 而不修改本地凭据文件。"""
    import time
    import urllib.parse
    import urllib.request

    if not refresh_token:
        raise ValueError("refresh_token 是必需的")

    client_id = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    if use_json:
        data = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }).encode()
        content_type = "application/json"
    else:
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }).encode()
        content_type = "application/x-www-form-urlencoded"

    token_endpoints = [
        "https://platform.claude.com/v1/oauth/token",
        "https://console.anthropic.com/v1/oauth/token",
    ]
    last_error = None
    for endpoint in token_endpoints:
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": content_type,
                "User-Agent": f"claude-cli/{_get_claude_code_version()} (external, cli)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            logger.debug("Anthropic token refresh failed at %s: %s", endpoint, exc)
            continue

        access_token = result.get("access_token", "")
        if not access_token:
            raise ValueError("Anthropic 刷新响应缺少 access_token")
        next_refresh = result.get("refresh_token", refresh_token)
        expires_in = result.get("expires_in", 3600)
        return {
            "access_token": access_token,
            "refresh_token": next_refresh,
            "expires_at_ms": int(time.time() * 1000) + (expires_in * 1000),
        }

    if last_error is not None:
        raise last_error
    raise ValueError("Anthropic token 刷新失败")


def _refresh_oauth_token(creds: Dict[str, Any]) -> Optional[str]:
    """尝试刷新已过期的 Claude Code OAuth token。"""
    refresh_token = creds.get("refreshToken", "")
    if not refresh_token:
        logger.debug("无可用刷新 token — 无法刷新")
        return None

    try:
        refreshed = refresh_anthropic_oauth_pure(refresh_token, use_json=False)
        _write_claude_code_credentials(
            refreshed["access_token"],
            refreshed["refresh_token"],
            refreshed["expires_at_ms"],
        )
        logger.debug("成功刷新 Claude Code OAuth token")
        return refreshed["access_token"]
    except Exception as e:
        logger.debug("刷新 Claude Code token 失败：%s", e)
        return None


def _write_claude_code_credentials(
    access_token: str,
    refresh_token: str,
    expires_at_ms: int,
    *,
    scopes: Optional[list] = None,
) -> None:
    """将刷新的凭据写回 ~/.claude/.credentials.json。

    可选的 *scopes* 列表（如 ``["user:inference", "user:profile", ...]``）
    会被持久化，以便 Claude Code 自身的认证检查将凭据
    识别为有效。Claude Code >=2.1.81 在使用 token 前会检查
    存储的 scope 中是否包含 ``"user:inference"``。
    """
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        # 读取现有文件以保留其他字段
        existing = {}
        if cred_path.exists():
            existing = json.loads(cred_path.read_text(encoding="utf-8"))

        oauth_data: Dict[str, Any] = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
        }
        if scopes is not None:
            oauth_data["scopes"] = scopes
        elif "claudeAiOauth" in existing and "scopes" in existing["claudeAiOauth"]:
            # 当刷新响应不包含 scope 字段时，保留之前存储的 scope。
            oauth_data["scopes"] = existing["claudeAiOauth"]["scopes"]

        existing["claudeAiOauth"] = oauth_data

        cred_path.parent.mkdir(parents=True, exist_ok=True)
        cred_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        # 限制权限（凭据文件）
        cred_path.chmod(0o600)
    except (OSError, IOError) as e:
        logger.debug("写入刷新的凭据失败：%s", e)


def _resolve_claude_code_token_from_credentials(creds: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从 Claude Code 凭据文件解析 token，如需要则刷新。"""
    creds = creds or read_claude_code_credentials()
    if creds and is_claude_code_token_valid(creds):
        logger.debug("使用 Claude Code 凭据（自动检测）")
        return creds["accessToken"]
    if creds:
        logger.debug("Claude Code 凭据已过期 — 尝试刷新")
        refreshed = _refresh_oauth_token(creds)
        if refreshed:
            return refreshed
        logger.debug("Token 刷新失败 — 请重新运行 'claude setup-token' 以重新认证")
    return None


def _prefer_refreshable_claude_code_token(env_token: str, creds: Optional[Dict[str, Any]]) -> Optional[str]:
    """当持久化的环境 OAuth token 会遮蔽刷新时，优先使用 Claude Code 凭据。

    KClaw 历史上将 setup token 持久化到 ANTHROPIC_TOKEN。这导致后续刷新
    不可能，因为静态环境 token 会在我们检查 Claude Code 的可刷新凭据文件
    之前获胜。如果我们有可刷新的 Claude Code 凭据记录，优先使用它
    而非静态环境 OAuth token。
    """
    if not env_token or not _is_oauth_token(env_token) or not isinstance(creds, dict):
        return None
    if not creds.get("refreshToken"):
        return None

    resolved = _resolve_claude_code_token_from_credentials(creds)
    if resolved and resolved != env_token:
        logger.debug(
            "优先使用 Claude Code 凭据文件而非静态环境 OAuth token，以便刷新可以继续"
        )
        return resolved
    return None


def get_anthropic_token_source(token: Optional[str] = None) -> str:
    """Anthropic 凭据 token 的尽力来源分类。"""
    token = (token or "").strip()
    if not token:
        return "none"

    env_token = os.getenv("ANTHROPIC_TOKEN", "").strip()
    if env_token and env_token == token:
        return "anthropic_token_env"

    cc_env_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if cc_env_token and cc_env_token == token:
        return "claude_code_oauth_token_env"

    creds = read_claude_code_credentials()
    if creds and creds.get("accessToken") == token:
        return str(creds.get("source") or "claude_code_credentials")

    managed_key = read_claude_managed_key()
    if managed_key and managed_key == token:
        return "claude_json_primary_api_key"

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key and api_key == token:
        return "anthropic_api_key_env"

    return "unknown"


def resolve_anthropic_token() -> Optional[str]:
    """从所有可用来源解析 Anthropic token。

    优先级:
      1. ANTHROPIC_TOKEN 环境变量（KClaw 保存的 OAuth/setup token）
      2. CLAUDE_CODE_OAUTH_TOKEN 环境变量
      3. Claude Code 凭据（~/.claude.json 或 ~/.claude/.credentials.json）
         — 如果过期且有刷新 token 则自动刷新
      4. ANTHROPIC_API_KEY 环境变量（常规 API 密钥或旧版回退）

    返回 token 字符串或 None。
    """
    creds = read_claude_code_credentials()

    # 1. KClaw 管理的 OAuth/setup token 环境变量
    token = os.getenv("ANTHROPIC_TOKEN", "").strip()
    if token:
        preferred = _prefer_refreshable_claude_code_token(token, creds)
        if preferred:
            return preferred
        return token

    # 2. CLAUDE_CODE_OAUTH_TOKEN（Claude Code 用于 setup-token）
    cc_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if cc_token:
        preferred = _prefer_refreshable_claude_code_token(cc_token, creds)
        if preferred:
            return preferred
        return cc_token

    # 3. Claude Code 凭据文件
    resolved_claude_token = _resolve_claude_code_token_from_credentials(creds)
    if resolved_claude_token:
        return resolved_claude_token

    # 4. 常规 API 密钥，或保存在 ANTHROPIC_API_KEY 中的旧版 OAuth token。
    # 这作为迁移前 KClaw 配置的兼容性回退保留。
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return api_key

    return None


def run_oauth_setup_token() -> Optional[str]:
    """交互式运行 'claude setup-token' 并返回结果 token。

    子进程完成后检查多个来源:
      1. Claude Code 凭据文件（可能由子进程写入）
      2. CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_TOKEN 环境变量

    返回 token 字符串，如果未获取凭据则返回 None。
    如果 'claude' CLI 未安装则抛出 FileNotFoundError。
    """
    import shutil
    import subprocess

    claude_path = shutil.which("claude")
    if not claude_path:
        raise FileNotFoundError(
            "'claude' CLI 未安装。"
            "请使用 npm install -g @anthropic-ai/claude-code 安装"
        )

    # 交互式运行 — 继承 stdin/stdout/stderr 以便用户交互
    try:
        subprocess.run([claude_path, "setup-token"])
    except (KeyboardInterrupt, EOFError):
        return None

    # 检查凭据是否已保存到 Claude Code 的配置文件
    creds = read_claude_code_credentials()
    if creds and is_claude_code_token_valid(creds):
        return creds["accessToken"]

    # 检查可能已设置的环境变量
    for env_var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_TOKEN"):
        val = os.getenv(env_var, "").strip()
        if val:
            return val

    return None


# ── KClaw 原生 PKCE OAuth 流程 ────────────────────────────────────────
# 镜像 Claude Code、pi-ai 和 OpenCode 使用的流程。
# 将凭据存储在 ~/.kclaw/.anthropic_oauth.json（我们自己的文件）。

_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
_KCLAW_OAUTH_FILE = get_kclaw_home() / ".anthropic_oauth.json"


def _generate_pkce() -> tuple:
    """生成 PKCE code_verifier 和 code_challenge (S256)。"""
    import base64
    import hashlib
    import secrets

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def run_kclaw_oauth_login_pure() -> Optional[Dict[str, Any]]:
    """运行 KClaw 原生 OAuth PKCE 流程并返回凭据状态。"""
    import time
    import webbrowser

    verifier, challenge = _generate_pkce()

    params = {
        "code": "true",
        "client_id": _OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "scope": _OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    from urllib.parse import urlencode

    auth_url = f"https://claude.ai/oauth/authorize?{urlencode(params)}"

    print()
    print("使用您的 Claude Pro/Max 订阅授权 KClaw。")
    print()
    print("╭─ Claude Pro/Max 授权 ──────────────────────────────╮")
    print("│                                                   │")
    print("│  请在浏览器中打开此链接：                         │")
    print("╰───────────────────────────────────────────────────╯")
    print()
    print(f"  {auth_url}")
    print()

    try:
        webbrowser.open(auth_url)
        print("  （浏览器已自动打开）")
    except Exception:
        pass

    print()
    print("授权后，您将看到一个代码。请在下方粘贴。")
    print()
    try:
        auth_code = input("授权代码：").strip()
    except (KeyboardInterrupt, EOFError):
        return None

    if not auth_code:
        print("未输入代码。")
        return None

    splits = auth_code.split("#")
    code = splits[0]
    state = splits[1] if len(splits) > 1 else ""

    try:
        import urllib.request

        exchange_data = json.dumps({
            "grant_type": "authorization_code",
            "client_id": _OAUTH_CLIENT_ID,
            "code": code,
            "state": state,
            "redirect_uri": _OAUTH_REDIRECT_URI,
            "code_verifier": verifier,
        }).encode()

        req = urllib.request.Request(
            _OAUTH_TOKEN_URL,
            data=exchange_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"claude-cli/{_get_claude_code_version()} (external, cli)",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Token 交换失败：{e}")
        return None

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = result.get("expires_in", 3600)

    if not access_token:
        print("响应中没有访问 token。")
        return None

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at_ms": expires_at_ms,
    }


def _save_kclaw_oauth_credentials(access_token: str, refresh_token: str, expires_at_ms: int) -> None:
    """保存 OAuth 凭据到 ~/.kclaw/.anthropic_oauth.json。"""
    data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    }
    try:
        _KCLAW_OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KCLAW_OAUTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _KCLAW_OAUTH_FILE.chmod(0o600)
    except (OSError, IOError) as e:
        logger.debug("保存 KClaw OAuth 凭据失败：%s", e)


def read_kclaw_oauth_credentials() -> Optional[Dict[str, Any]]:
    """从 ~/.kclaw/.anthropic_oauth.json 读取 KClaw 管理的 OAuth 凭据。"""
    if _KCLAW_OAUTH_FILE.exists():
        try:
            data = json.loads(_KCLAW_OAUTH_FILE.read_text(encoding="utf-8"))
            if data.get("accessToken"):
                return data
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug("读取 KClaw OAuth 凭据失败：%s", e)
    return None


# ---------------------------------------------------------------------------
# 消息/工具/响应格式转换
# ---------------------------------------------------------------------------


def normalize_model_name(model: str, preserve_dots: bool = False) -> str:
    """规范化 Anthropic API 的模型名称。

    - 去除 'anthropic/' 前缀（OpenRouter 格式，不区分大小写）
    - 将版本号中的点转换为连字符（OpenRouter 使用点，
      Anthropic 使用连字符：claude-opus-4.6 → claude-opus-4-6），除非
      preserve_dots 为 True（如 Alibaba/DashScope：qwen3.5-plus）。
    """
    lower = model.lower()
    if lower.startswith("anthropic/"):
        model = model[len("anthropic/"):]
    if not preserve_dots:
        # OpenRouter 使用点作为版本分隔符（claude-opus-4.6），
        # Anthropic 使用连字符（claude-opus-4-6）。将点转换为连字符。
        model = model.replace(".", "-")
    return model


def _sanitize_tool_id(tool_id: str) -> str:
    """清理 Anthropic API 的工具调用 ID。

    Anthropic 要求 ID 匹配 [a-zA-Z0-9_-]。将无效字符替换为
    下划线并确保非空。
    """
    import re
    if not tool_id:
        return "tool_0"
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id)
    return sanitized or "tool_0"


def _convert_openai_image_part_to_anthropic(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """将 OpenAI 风格的图片块转换为 Anthropic 的图片源格式。"""
    image_data = part.get("image_url", {})
    url = image_data.get("url", "") if isinstance(image_data, dict) else str(image_data)
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()

    if url.startswith("data:"):
        header, sep, data = url.partition(",")
        if sep and ";base64" in header:
            media_type = header[5:].split(";", 1)[0] or "image/png"
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }

    if url.startswith(("http://", "https://")):
        return {
            "type": "image",
            "source": {
                "type": "url",
                "url": url,
            },
        }

    return None


def convert_tools_to_anthropic(tools: List[Dict]) -> List[Dict]:
    """将 OpenAI 工具定义转换为 Anthropic 格式。"""
    if not tools:
        return []
    result = []
    for t in tools:
        fn = t.get("function", {})
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _image_source_from_openai_url(url: str) -> Dict[str, str]:
    """将 OpenAI 风格的图片 URL/data URL 转换为 Anthropic 图片源。"""
    url = str(url or "").strip()
    if not url:
        return {"type": "url", "url": ""}

    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media_type = "image/jpeg"
        if header.startswith("data:"):
            mime_part = header[len("data:"):].split(";", 1)[0].strip()
            if mime_part.startswith("image/"):
                media_type = mime_part
        return {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        }

    return {"type": "url", "url": url}


def _convert_content_part_to_anthropic(part: Any) -> Optional[Dict[str, Any]]:
    """将单个 OpenAI 风格的内容部分转换为 Anthropic 格式。"""
    if part is None:
        return None
    if isinstance(part, str):
        return {"type": "text", "text": part}
    if not isinstance(part, dict):
        return {"type": "text", "text": str(part)}

    ptype = part.get("type")

    if ptype == "input_text":
        block: Dict[str, Any] = {"type": "text", "text": part.get("text", "")}
    elif ptype in {"image_url", "input_image"}:
        image_value = part.get("image_url", {})
        url = image_value.get("url", "") if isinstance(image_value, dict) else str(image_value or "")
        block = {"type": "image", "source": _image_source_from_openai_url(url)}
    else:
        block = dict(part)

    if isinstance(part.get("cache_control"), dict) and "cache_control" not in block:
        block["cache_control"] = dict(part["cache_control"])
    return block


def _to_plain_data(value: Any, *, _depth: int = 0, _path: Optional[set] = None) -> Any:
    """递归将 SDK 对象转换为纯 Python 数据结构。

    防止循环引用（``_path`` 跟踪 *当前* 递归路径上对象的 ``id()``）
    和失控深度（上限 20 层）。使用基于路径的跟踪，因此被多个
    兄弟引用的共享（但非循环）对象能正确转换而非被字符串化。
    """
    _MAX_DEPTH = 20
    if _depth > _MAX_DEPTH:
        return str(value)

    if _path is None:
        _path = set()

    obj_id = id(value)
    if obj_id in _path:
        return str(value)

    if hasattr(value, "model_dump"):
        _path.add(obj_id)
        result = _to_plain_data(value.model_dump(), _depth=_depth + 1, _path=_path)
        _path.discard(obj_id)
        return result
    if isinstance(value, dict):
        _path.add(obj_id)
        result = {k: _to_plain_data(v, _depth=_depth + 1, _path=_path) for k, v in value.items()}
        _path.discard(obj_id)
        return result
    if isinstance(value, (list, tuple)):
        _path.add(obj_id)
        result = [_to_plain_data(v, _depth=_depth + 1, _path=_path) for v in value]
        _path.discard(obj_id)
        return result
    if hasattr(value, "__dict__"):
        _path.add(obj_id)
        result = {
            k: _to_plain_data(v, _depth=_depth + 1, _path=_path)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
        _path.discard(obj_id)
        return result
    return value


def _extract_preserved_thinking_blocks(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回之前保留在消息上的 Anthropic 思考块。"""
    raw_details = message.get("reasoning_details")
    if not isinstance(raw_details, list):
        return []

    preserved: List[Dict[str, Any]] = []
    for detail in raw_details:
        if not isinstance(detail, dict):
            continue
        block_type = str(detail.get("type", "") or "").strip().lower()
        if block_type not in {"thinking", "redacted_thinking"}:
            continue
        preserved.append(copy.deepcopy(detail))
    return preserved


def _convert_content_to_anthropic(content: Any) -> Any:
    """将 OpenAI 风格的多模态内容数组转换为 Anthropic 块。"""
    if not isinstance(content, list):
        return content

    converted = []
    for part in content:
        block = _convert_content_part_to_anthropic(part)
        if block is not None:
            converted.append(block)
    return converted


def convert_messages_to_anthropic(
    messages: List[Dict],
    base_url: str | None = None,
) -> Tuple[Optional[Any], List[Dict]]:
    """将 OpenAI 格式的消息转换为 Anthropic 格式。

    返回 (system_prompt, anthropic_messages)。
    系统消息被提取，因为 Anthropic 将它们作为单独的参数接收。
    system_prompt 是字符串或内容块列表（当存在 cache_control 时）。

    当提供 *base_url* 且指向第三方 Anthropic 兼容端点时，
    所有思考块签名会被去除。签名是 Anthropic 专有的 — 第三方端点
    无法验证它们，会以 HTTP 400 "Invalid signature in thinking block" 拒绝。
    """
    system = None
    result = []

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if role == "system":
            if isinstance(content, list):
                # 在内容块上保留 cache_control 标记
                has_cache = any(
                    p.get("cache_control") for p in content if isinstance(p, dict)
                )
                if has_cache:
                    system = [p for p in content if isinstance(p, dict)]
                else:
                    system = "\n".join(
                        p["text"] for p in content if p.get("type") == "text"
                    )
            else:
                system = content
            continue

        if role == "assistant":
            blocks = _extract_preserved_thinking_blocks(m)
            if content:
                if isinstance(content, list):
                    converted_content = _convert_content_to_anthropic(content)
                    if isinstance(converted_content, list):
                        blocks.extend(converted_content)
                else:
                    blocks.append({"type": "text", "text": str(content)})
            for tc in m.get("tool_calls", []):
                if not tc or not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                try:
                    parsed_args = json.loads(args) if isinstance(args, str) else args
                except (json.JSONDecodeError, ValueError):
                    parsed_args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": _sanitize_tool_id(tc.get("id", "")),
                    "name": fn.get("name", ""),
                    "input": parsed_args,
                })
            # Anthropic 拒绝空的助手内容
            effective = blocks or content
            if not effective or effective == "":
                effective = [{"type": "text", "text": "(empty)"}]
            result.append({"role": "assistant", "content": effective})
            continue

        if role == "tool":
            # 清理 tool_use_id 并确保内容非空
            result_content = content if isinstance(content, str) else json.dumps(content)
            if not result_content:
                result_content = "(no output)"
            tool_result = {
                "type": "tool_result",
                "tool_use_id": _sanitize_tool_id(m.get("tool_call_id", "")),
                "content": result_content,
            }
            if isinstance(m.get("cache_control"), dict):
                tool_result["cache_control"] = dict(m["cache_control"])
            # 将连续的工具结果合并为一条用户消息
            if (
                result
                and result[-1]["role"] == "user"
                and isinstance(result[-1]["content"], list)
                and result[-1]["content"]
                and result[-1]["content"][0].get("type") == "tool_result"
            ):
                result[-1]["content"].append(tool_result)
            else:
                result.append({"role": "user", "content": [tool_result]})
            continue

        # 常规用户消息 — 验证内容非空（Anthropic 拒绝空内容）
        if isinstance(content, list):
            converted_blocks = _convert_content_to_anthropic(content)
            # 检查所有文本块是否为空
            if not converted_blocks or all(
                b.get("text", "").strip() == ""
                for b in converted_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ):
                converted_blocks = [{"type": "text", "text": "(empty message)"}]
            result.append({"role": "user", "content": converted_blocks})
        else:
            # 验证字符串内容非空
            if not content or (isinstance(content, str) and not content.strip()):
                content = "(empty message)"
            result.append({"role": "user", "content": content})

    # 去除孤立的 tool_use 块（没有匹配的 tool_result 跟随）
    tool_result_ids = set()
    for m in result:
        if m["role"] == "user" and isinstance(m["content"], list):
            for block in m["content"]:
                if block.get("type") == "tool_result":
                    tool_result_ids.add(block.get("tool_use_id"))
    for m in result:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            m["content"] = [
                b
                for b in m["content"]
                if b.get("type") != "tool_use" or b.get("id") in tool_result_ids
            ]
            if not m["content"]:
                m["content"] = [{"type": "text", "text": "(tool call removed)"}]

    # 去除孤立的 tool_result 块（没有匹配的 tool_use 在它们之前）。
    # 这是上述的镜像：上下文压缩或会话截断可能移除了包含 tool_use 的
    # 助手消息，但保留了后续的 tool_result。Anthropic 会以 400 拒绝这些。
    tool_use_ids = set()
    for m in result:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            for block in m["content"]:
                if block.get("type") == "tool_use":
                    tool_use_ids.add(block.get("id"))
    for m in result:
        if m["role"] == "user" and isinstance(m["content"], list):
            m["content"] = [
                b
                for b in m["content"]
                if b.get("type") != "tool_result" or b.get("tool_use_id") in tool_use_ids
            ]
            if not m["content"]:
                m["content"] = [{"type": "text", "text": "(tool result removed)"}]

    # 强制严格的角色交替（Anthropic 拒绝连续的同角色消息）
    fixed = []
    for m in result:
        if fixed and fixed[-1]["role"] == m["role"]:
            if m["role"] == "user":
                # 合并连续的用户消息
                prev_content = fixed[-1]["content"]
                curr_content = m["content"]
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    fixed[-1]["content"] = prev_content + "\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, list):
                    fixed[-1]["content"] = prev_content + curr_content
                else:
                    # 混合类型 — 将字符串包装为列表
                    if isinstance(prev_content, str):
                        prev_content = [{"type": "text", "text": prev_content}]
                    if isinstance(curr_content, str):
                        curr_content = [{"type": "text", "text": curr_content}]
                    fixed[-1]["content"] = prev_content + curr_content
            else:
                # 连续的助手消息 — 合并文本内容。
                # 从*第二条*消息中删除思考块：它们的签名
                # 是针对不同的轮次边界计算的，一旦合并就会失效。
                if isinstance(m["content"], list):
                    m["content"] = [
                        b for b in m["content"]
                        if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
                    ]
                prev_blocks = fixed[-1]["content"]
                curr_blocks = m["content"]
                if isinstance(prev_blocks, list) and isinstance(curr_blocks, list):
                    fixed[-1]["content"] = prev_blocks + curr_blocks
                elif isinstance(prev_blocks, str) and isinstance(curr_blocks, str):
                    fixed[-1]["content"] = prev_blocks + "\n" + curr_blocks
                else:
                    # 混合类型 — 将两者规范化为列表并合并
                    if isinstance(prev_blocks, str):
                        prev_blocks = [{"type": "text", "text": prev_blocks}]
                    if isinstance(curr_blocks, str):
                        curr_blocks = [{"type": "text", "text": curr_blocks}]
                    fixed[-1]["content"] = prev_blocks + curr_blocks
        else:
            fixed.append(m)
    result = fixed

    # ── 思考块签名管理 ──────────────────────────────────
    # Anthropic 根据完整的轮次内容对思考块签名。
    # 任何上游变更（上下文压缩、会话截断、孤立块去除、
    # 消息合并）都会使签名失效，导致 HTTP 400
    # "Invalid signature in thinking block"。
    #
    # 签名是 Anthropic 专有的。第三方端点（MiniMax、Azure AI Foundry、
    # 自托管代理）无法验证它们，会直接拒绝。当目标是第三方端点时，
    # 从每个助手消息中去除所有 thinking/redacted_thinking 块 —
    # 第三方如果支持扩展思考会生成自己的思考块。
    #
    # 对于直接 Anthropic（遵循 clawdbot/OpenClaw 策略）：
    # 1. 从所有助手消息中去除 thinking/redacted_thinking，
    #    除了最后一个 — 在当前工具使用链上保留推理连续性，
    #    同时避免过期签名错误。
    # 2. 将未签名的思考块（无签名）降级为文本 — Anthropic 无法
    #    验证它们并会拒绝。
    # 3. 从 thinking/redacted_thinking 块中去除 cache_control —
    #    缓存标记可能干扰签名验证。
    _THINKING_TYPES = frozenset(("thinking", "redacted_thinking"))
    _is_third_party = _is_third_party_anthropic_endpoint(base_url)

    last_assistant_idx = None
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "assistant":
            last_assistant_idx = i
            break

    for idx, m in enumerate(result):
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue

        if _is_third_party or idx != last_assistant_idx:
            # 第三方端点：从每个助手消息中去除所有思考块 —
            # 签名是 Anthropic 专有的。
            # 直接 Anthropic：仅从非最新的助手消息中去除。
            stripped = [
                b for b in m["content"]
                if not (isinstance(b, dict) and b.get("type") in _THINKING_TYPES)
            ]
            m["content"] = stripped or [{"type": "text", "text": "(thinking elided)"}]
        else:
            # 直接 Anthropic 上的最新助手消息：保留已签名的思考
            # 块以保持推理连续性；将未签名的降级为纯文本。
            new_content = []
            for b in m["content"]:
                if not isinstance(b, dict) or b.get("type") not in _THINKING_TYPES:
                    new_content.append(b)
                    continue
                if b.get("type") == "redacted_thinking":
                    # Redacted blocks use 'data' for the signature payload
                    if b.get("data"):
                        new_content.append(b)
                    # else: drop — no data means it can't be validated
                elif b.get("signature"):
                    # 已签名的思考块 — 保留它
                    new_content.append(b)
                else:
                    # 未签名的思考 — 降级为文本以免丢失
                    thinking_text = b.get("thinking", "")
                    if thinking_text:
                        new_content.append({"type": "text", "text": thinking_text})
            m["content"] = new_content or [{"type": "text", "text": "(empty)"}]

        # 从任何剩余的 thinking/redacted_thinking 块中去除 cache_control
        # — 缓存标记干扰签名验证。
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") in _THINKING_TYPES:
                b.pop("cache_control", None)

    return system, result


def build_anthropic_kwargs(
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]],
    max_tokens: Optional[int],
    reasoning_config: Optional[Dict[str, Any]],
    tool_choice: Optional[str] = None,
    is_oauth: bool = False,
    preserve_dots: bool = False,
    context_length: Optional[int] = None,
    base_url: str | None = None,
) -> Dict[str, Any]:
    """构建 anthropic.messages.create() 的 kwargs。

    当 *max_tokens* 为 None 时，使用模型的原生输出限制
    （如 Opus 4.6 为 128K，Sonnet 4.6 为 64K）。如果提供了
    *context_length*，有效限制会被钳制以不超过上下文窗口。

    当 *is_oauth* 为 True 时，应用 Claude Code 兼容性转换：
    系统提示词前缀、工具名称前缀和提示词清理。

    当 *preserve_dots* 为 True 时，模型名称中的点不转换为连字符
    （用于 Alibaba/DashScope 的 anthropic 兼容端点：qwen3.5-plus）。

    当 *base_url* 指向第三方 Anthropic 兼容端点时，
    思考块签名会被去除（它们是 Anthropic 专有的）。
    """
    system, anthropic_messages = convert_messages_to_anthropic(messages, base_url=base_url)
    anthropic_tools = convert_tools_to_anthropic(tools) if tools else []

    model = normalize_model_name(model, preserve_dots=preserve_dots)
    effective_max_tokens = max_tokens or _get_anthropic_max_output(model)

    # 钳制到上下文窗口（如果用户设置了较低的 context_length）
    # （例如容量有限的自定义端点）。
    if context_length and effective_max_tokens > context_length:
        effective_max_tokens = max(context_length - 1, 1)

    # ── OAuth: KClaw 身份 ──────────────────────────────────
    if is_oauth:
        # 1. 在系统提示词前添加 KClaw 身份
        cc_block = {"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX}
        if isinstance(system, list):
            system = [cc_block] + system
        elif isinstance(system, str) and system:
            system = [cc_block, {"type": "text", "text": system}]
        else:
            system = [cc_block]

        # 2. 保持 KClaw 身份 — 无需品牌替换。
        #    （之前将 KClaw→Claude Code 替换以通过 Anthropic 服务端
        #     过滤器；现在 KClaw 始终以自身身份标识。）

        # 3. 为工具名称添加 mcp_ 前缀（Claude Code 约定）
        if anthropic_tools:
            for tool in anthropic_tools:
                if "name" in tool:
                    tool["name"] = _MCP_TOOL_PREFIX + tool["name"]

        # 4. 在消息历史中添加工具名称前缀（tool_use 和 tool_result 块）
        for msg in anthropic_messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use" and "name" in block:
                            if not block["name"].startswith(_MCP_TOOL_PREFIX):
                                block["name"] = _MCP_TOOL_PREFIX + block["name"]
                        elif block.get("type") == "tool_result" and "tool_use_id" in block:
                            pass  # tool_result 使用 ID，而非名称

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": effective_max_tokens,
    }

    if system:
        kwargs["system"] = system

    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
        # 将 OpenAI tool_choice 映射到 Anthropic 格式
        if tool_choice == "auto" or tool_choice is None:
            kwargs["tool_choice"] = {"type": "auto"}
        elif tool_choice == "required":
            kwargs["tool_choice"] = {"type": "any"}
        elif tool_choice == "none":
            # Anthropic 没有 tool_choice "none" — 完全移除工具以防止使用
            kwargs.pop("tools", None)
        elif isinstance(tool_choice, str):
            # 特定工具名称
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}

    # 将 reasoning_config 映射到 Anthropic 的 thinking 参数。
    # Claude 4.6 模型使用自适应思考 + output_config.effort。
    # 旧模型使用手动思考与 budget_tokens。
    # Haiku 和 MiniMax 模型不支持扩展思考 — 完全跳过。
    if reasoning_config and isinstance(reasoning_config, dict):
        if reasoning_config.get("enabled") is not False and "haiku" not in model.lower() and "minimax" not in model.lower():
            effort = str(reasoning_config.get("effort", "medium")).lower()
            budget = THINKING_BUDGET.get(effort, 8000)
            if _supports_adaptive_thinking(model):
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {
                    "effort": ADAPTIVE_EFFORT_MAP.get(effort, "medium")
                }
            else:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
                # Anthropic 在旧模型上启用思考时要求 temperature=1
                kwargs["temperature"] = 1
                kwargs["max_tokens"] = max(effective_max_tokens, budget + 4096)

    return kwargs


def normalize_anthropic_response(
    response,
    strip_tool_prefix: bool = False,
) -> Tuple[SimpleNamespace, str]:
    """将 Anthropic 响应规范化为 AIAgent 期望的形状。

    返回 (assistant_message, finish_reason)，其中 assistant_message 有
    .content, .tool_calls 和 .reasoning 属性。

    当 *strip_tool_prefix* 为 True 时，移除为 OAuth Claude Code 兼容性
    添加的 ``mcp_`` 前缀。
    """
    text_parts = []
    reasoning_parts = []
    reasoning_details = []
    tool_calls = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking":
            reasoning_parts.append(block.thinking)
            block_dict = _to_plain_data(block)
            if isinstance(block_dict, dict):
                reasoning_details.append(block_dict)
        elif block.type == "tool_use":
            name = block.name
            if strip_tool_prefix and name.startswith(_MCP_TOOL_PREFIX):
                name = name[len(_MCP_TOOL_PREFIX):]
            tool_calls.append(
                SimpleNamespace(
                    id=block.id,
                    type="function",
                    function=SimpleNamespace(
                        name=name,
                        arguments=json.dumps(block.input),
                    ),
                )
            )

    # 将 Anthropic stop_reason 映射到 OpenAI finish_reason
    stop_reason_map = {
        "end_turn": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "stop_sequence": "stop",
    }
    finish_reason = stop_reason_map.get(response.stop_reason, "stop")

    return (
        SimpleNamespace(
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls or None,
            reasoning="\n\n".join(reasoning_parts) if reasoning_parts else None,
            reasoning_content=None,
            reasoning_details=reasoning_details or None,
        ),
        finish_reason,
    )