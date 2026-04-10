#!/usr/bin/env python3
"""
MCP OAuth 2.1 客户端支持

为需要 OAuth 认证（而非静态 bearer token）的 MCP 服务器实现
基于浏览器的 OAuth 2.1 授权码流程和 PKCE。

使用 MCP Python SDK 的 ``OAuthClientProvider``（``httpx.Auth`` 子类），
自动处理发现、动态客户端注册、PKCE、token 交换、刷新和升级授权。

本模块提供粘合层：
    - ``KClawTokenStorage``：将 tokens/client-info 持久化到磁盘，
      使其在进程重启后仍然保留。
    - 回调服务器：临时的 localhost HTTP 服务器，用于捕获带有授权码的 OAuth 重定向。
    - ``build_oauth_auth()``：由 ``mcp_tool.py`` 调用的入口点，
      将所有内容连接起来并返回 ``httpx.Auth`` 对象。

config.yaml 中的配置::

    mcp_servers:
      my_server:
        url: "https://mcp.example.com/mcp"
        auth: oauth
        oauth:                                  # 所有字段可选
          client_id: "pre-registered-id"        # 跳过动态注册
          client_secret: "secret"               # 仅限机密客户端
          scope: "read write"                   # 默认：由服务器提供
          redirect_port: 0                      # 0 = 自动选择空闲端口
          client_name: "My Custom Client"       # 默认："KClaw Agent"
"""

import asyncio
import json
import logging
import os
import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports -- MCP SDK with OAuth support is optional
# ---------------------------------------------------------------------------

_OAUTH_AVAILABLE = False
try:
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthClientMetadata,
        OAuthToken,
    )
    from pydantic import AnyUrl

    _OAUTH_AVAILABLE = True
except ImportError:
    logger.debug("MCP OAuth types not available -- OAuth MCP auth disabled")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OAuthNonInteractiveError(RuntimeError):
    """当 OAuth 在非交互环境中需要浏览器交互时引发。"""


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Port used by the most recent build_oauth_auth() call.  Exposed so that
# tests can verify the callback server and the redirect_uri share a port.
_oauth_port: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_token_dir() -> Path:
    """返回 MCP OAuth token 文件的目录。

    使用 KCLAW_HOME 以便每个配置获得自己的 OAuth tokens。
    布局：``KCLAW_HOME/mcp-tokens/``
    """
    try:
        from kclaw_constants import get_kclaw_home
        base = Path(get_kclaw_home())
    except ImportError:
        base = Path(os.environ.get("KCLAW_HOME", str(Path.home() / ".kclaw")))
    return base / "mcp-tokens"


def _safe_filename(name: str) -> str:
    """清理服务器名称以用作文件名（不含路径分隔符）。"""
    return re.sub(r"[^\w\-]", "_", name).strip("_")[:128] or "default"


def _find_free_port() -> int:
    """在 localhost 上查找可用的 TCP 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_interactive() -> bool:
    """如果我们可以合理地期望与用户交互，则返回 True。"""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _can_open_browser() -> bool:
    """如果打开浏览器可能有效，则返回 True。"""
    # 明确的 SSH 会话 → 无本地显示
    if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
        return False
    # macOS and Windows usually have a display
    if os.name == "nt":
        return True
    try:
        if os.uname().sysname == "Darwin":
            return True
    except AttributeError:
        pass
    # Linux/other posix: need DISPLAY or WAYLAND_DISPLAY
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def _read_json(path: Path) -> dict | None:
    """读取 JSON 文件，如果不存在或无效则返回 None。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _write_json(path: Path, data: dict) -> None:
    """将字典写入 JSON 文件，权限受限（0o600）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.rename(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# KClawTokenStorage -- persistent token/client-info on disk
# ---------------------------------------------------------------------------


class KClawTokenStorage:
    """将 OAuth tokens 和客户端注册持久化到 JSON 文件。

    文件布局::

        KCLAW_HOME/mcp-tokens/<server_name>.json         -- tokens
        KCLAW_HOME/mcp-tokens/<server_name>.client.json   -- 客户端信息
    """

    def __init__(self, server_name: str):
        self._server_name = _safe_filename(server_name)

    def _tokens_path(self) -> Path:
        return _get_token_dir() / f"{self._server_name}.json"

    def _client_info_path(self) -> Path:
        return _get_token_dir() / f"{self._server_name}.client.json"

    # -- tokens ------------------------------------------------------------

    async def get_tokens(self) -> "OAuthToken | None":
        data = _read_json(self._tokens_path())
        if data is None:
            return None
        try:
            return OAuthToken.model_validate(data)
        except Exception:
            logger.warning("Corrupt tokens at %s -- ignoring", self._tokens_path())
            return None

    async def set_tokens(self, tokens: "OAuthToken") -> None:
        _write_json(self._tokens_path(), tokens.model_dump(exclude_none=True))
        logger.debug("OAuth tokens saved for %s", self._server_name)

    # -- client info -------------------------------------------------------

    async def get_client_info(self) -> "OAuthClientInformationFull | None":
        data = _read_json(self._client_info_path())
        if data is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(data)
        except Exception:
            logger.warning("Corrupt client info at %s -- ignoring", self._client_info_path())
            return None

    async def set_client_info(self, client_info: "OAuthClientInformationFull") -> None:
        _write_json(self._client_info_path(), client_info.model_dump(exclude_none=True))
        logger.debug("OAuth client info saved for %s", self._server_name)

    # -- cleanup -----------------------------------------------------------

    def remove(self) -> None:
        """删除此服务器的所有已存储 OAuth 状态。"""
        for p in (self._tokens_path(), self._client_info_path()):
            p.unlink(missing_ok=True)

    def has_cached_tokens(self) -> bool:
        """如果磁盘上有 tokens（可能已过期），则返回 True。"""
        return self._tokens_path().exists()


# ---------------------------------------------------------------------------
# Callback handler factory -- each invocation gets its own result dict
# ---------------------------------------------------------------------------


def _make_callback_handler() -> tuple[type, dict]:
    """创建每个流程独立的回调 HTTP 处理器类及其自己的结果字典。

    返回 ``(HandlerClass, result_dict)``，其中 *result_dict* 是一个可变字典，
    当 OAuth 重定向到达时，处理器将 ``auth_code`` 和 ``state`` 写入其中。
    每次调用返回一对新的组合，这样并发流程不会互相覆盖。
    """
    result: dict[str, Any] = {"auth_code": None, "state": None, "error": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            params = parse_qs(urlparse(self.path).query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            error = params.get("error", [None])[0]

            result["auth_code"] = code
            result["state"] = state
            result["error"] = error

            body = (
                "<html><body><h2>Authorization Successful</h2>"
                "<p>You can close this tab and return to KClaw.</p></body></html>"
            ) if code else (
                "<html><body><h2>Authorization Failed</h2>"
                f"<p>Error: {error or 'unknown'}</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("OAuth callback: %s", fmt % args)

    return _Handler, result


# ---------------------------------------------------------------------------
# Async redirect + callback handlers for OAuthClientProvider
# ---------------------------------------------------------------------------


async def _redirect_handler(authorization_url: str) -> None:
    """向用户显示授权 URL。

    尽可能自动打开浏览器；始终打印 URL 作为
    无界面/SSH/网关环境的备选方案。
    """
    msg = (
        f"\n  MCP OAuth: authorization required.\n"
        f"  Open this URL in your browser:\n\n"
        f"    {authorization_url}\n"
    )
    print(msg, file=sys.stderr)

    if _can_open_browser():
        try:
            opened = webbrowser.open(authorization_url)
            if opened:
                print("  (Browser opened automatically.)\n", file=sys.stderr)
            else:
                print("  (Could not open browser — please open the URL manually.)\n", file=sys.stderr)
        except Exception:
            print("  (Could not open browser — please open the URL manually.)\n", file=sys.stderr)
    else:
        print("  (Headless environment detected — open the URL manually.)\n", file=sys.stderr)


async def _wait_for_callback() -> tuple[str, str | None]:
    """等待本地回调服务器上的 OAuth 回调到达。

    使用模块级 ``_oauth_port``，该值由 ``build_oauth_auth``
    在调用此函数之前设置。轮询结果而不阻塞事件循环。

    引发：
        OAuthNonInteractiveError：如果回调超时（没有用户在场
            完成浏览器认证）。
    """
    assert _oauth_port is not None, "OAuth callback port not set"

    # The callback server is already running (started in build_oauth_auth).
    # We just need to poll for the result.
    handler_cls, result = _make_callback_handler()

    # Start a temporary server on the known port
    try:
        server = HTTPServer(("127.0.0.1", _oauth_port), handler_cls)
    except OSError:
        # Port already in use — the server from build_oauth_auth is running.
        # Fall back to polling the server started by build_oauth_auth.
        raise OAuthNonInteractiveError(
            "OAuth callback timed out — could not bind callback port. "
            "Complete the authorization in a browser first, then retry."
        )

    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    timeout = 300.0
    poll_interval = 0.5
    elapsed = 0.0
    while elapsed < timeout:
        if result["auth_code"] is not None or result["error"] is not None:
            break
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    server.server_close()

    if result["error"]:
        raise RuntimeError(f"OAuth authorization failed: {result['error']}")
    if result["auth_code"] is None:
        raise OAuthNonInteractiveError(
            "OAuth callback timed out — no authorization code received. "
            "Ensure you completed the browser authorization flow."
        )

    return result["auth_code"], result["state"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def remove_oauth_tokens(server_name: str) -> None:
    """删除服务器已存储的 OAuth tokens 和客户端信息。"""
    storage = KClawTokenStorage(server_name)
    storage.remove()
    logger.info("OAuth tokens removed for '%s'", server_name)


def build_oauth_auth(
    server_name: str,
    server_url: str,
    oauth_config: dict | None = None,
) -> "OAuthClientProvider | None":
    """为 MCP 服务器构建兼容 ``httpx.Auth`` 的 OAuth 处理器。

    当服务器在配置中有 ``auth: oauth`` 时由 ``mcp_tool.py`` 调用。

    参数：
        server_name：mcp_servers 配置中的服务器键（用于存储）。
        server_url：MCP 服务器端点 URL。
        oauth_config：config.yaml 中 ``oauth:`` 块的可选字典。

    返回：
        ``OAuthClientProvider`` 实例，如果 MCP SDK 缺少 OAuth 支持则返回 None。
    """
    if not _OAUTH_AVAILABLE:
        logger.warning(
            "MCP OAuth requested for '%s' but SDK auth types are not available. "
            "Install with: pip install 'mcp>=1.10.0'",
            server_name,
        )
        return None

    global _oauth_port

    cfg = oauth_config or {}

    # --- Storage ---
    storage = KClawTokenStorage(server_name)

    # --- Non-interactive warning ---
    if not _is_interactive() and not storage.has_cached_tokens():
        logger.warning(
            "MCP OAuth for '%s': non-interactive environment and no cached tokens found. "
            "The OAuth flow requires browser authorization. Run interactively first "
            "to complete the initial authorization, then cached tokens will be reused.",
            server_name,
        )

    # --- Pick callback port ---
    redirect_port = int(cfg.get("redirect_port", 0))
    if redirect_port == 0:
        redirect_port = _find_free_port()
    _oauth_port = redirect_port

    # --- Client metadata ---
    client_name = cfg.get("client_name", "KClaw Agent")
    scope = cfg.get("scope")
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"

    metadata_kwargs: dict[str, Any] = {
        "client_name": client_name,
        "redirect_uris": [AnyUrl(redirect_uri)],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        metadata_kwargs["scope"] = scope

    client_secret = cfg.get("client_secret")
    if client_secret:
        metadata_kwargs["token_endpoint_auth_method"] = "client_secret_post"

    client_metadata = OAuthClientMetadata.model_validate(metadata_kwargs)

    # --- Pre-registered client ---
    client_id = cfg.get("client_id")
    if client_id:
        info_dict: dict[str, Any] = {
            "client_id": client_id,
            "redirect_uris": [redirect_uri],
            "grant_types": client_metadata.grant_types,
            "response_types": client_metadata.response_types,
            "token_endpoint_auth_method": client_metadata.token_endpoint_auth_method,
        }
        if client_secret:
            info_dict["client_secret"] = client_secret
        if client_name:
            info_dict["client_name"] = client_name
        if scope:
            info_dict["scope"] = scope

        client_info = OAuthClientInformationFull.model_validate(info_dict)
        _write_json(storage._client_info_path(), client_info.model_dump(exclude_none=True))
        logger.debug("Pre-registered client_id=%s for '%s'", client_id, server_name)

    # --- Base URL for discovery ---
    parsed = urlparse(server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # --- Build provider ---
    provider = OAuthClientProvider(
        server_url=base_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=_redirect_handler,
        callback_handler=_wait_for_callback,
        timeout=float(cfg.get("timeout", 300)),
    )

    return provider
