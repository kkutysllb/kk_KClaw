"""
Gateway 运行器 - 消息平台集成的入口点。

本模块提供：
- start_gateway()：启动所有已配置的平台适配器
- GatewayRunner：管理 gateway 生命周期的主类

用法：
    # 启动 gateway
    python -m gateway.run
    
    # 或从 CLI
    python cli.py --gateway
"""

import asyncio
import json
import logging
import os
import re
import shlex
import sys
import signal
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any, List

# ---------------------------------------------------------------------------
# SSL 证书自动检测，用于 NixOS 和其他非标准系统。
# 必须在任何 HTTP 库（discord、aiohttp 等）导入之前运行。
# ---------------------------------------------------------------------------
def _ensure_ssl_certs() -> None:
    """如果系统未向 Python 暴露 CA 证书，则设置 SSL_CERT_FILE。"""
    if "SSL_CERT_FILE" in os.environ:
        return  # 用户已配置

    import ssl

    # 1. Python 编译时默认设置
    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate and os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return

    # 2. certifi（附带自己的 Mozilla 证书包）
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        return
    except ImportError:
        pass

    # 3. 常见发行版 / macOS 位置
    for candidate in (
        "/etc/ssl/certs/ca-certificates.crt",               # Debian/Ubuntu/Gentoo
        "/etc/pki/tls/certs/ca-bundle.crt",                 # RHEL/CentOS 7
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", # RHEL/CentOS 8+
        "/etc/ssl/ca-bundle.pem",                            # SUSE/OpenSUSE
        "/etc/ssl/cert.pem",                                 # Alpine / macOS
        "/etc/pki/tls/cert.pem",                             # Fedora
        "/usr/local/etc/openssl@1.1/cert.pem",               # macOS Homebrew Intel
        "/opt/homebrew/etc/openssl@1.1/cert.pem",            # macOS Homebrew ARM
    ):
        if os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return

_ensure_ssl_certs()

# 将父目录添加到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 解析 KClaw 主目录（尊重 KCLAW_HOME 覆盖）
from kclaw_constants import get_kclaw_home
from utils import atomic_yaml_write
_kclaw_home = get_kclaw_home()

# 加载环境变量 - 优先从 ~/.kclaw/.env 读取。
# 用户管理的环境文件应该在重启时覆盖过时的 shell 导出。
from dotenv import load_dotenv  # 向后兼容用于测试中猴子补丁此符号
from kclaw_cli.env_loader import load_kclaw_dotenv
_env_path = _kclaw_home / '.env'
load_kclaw_dotenv(kclaw_home=_kclaw_home, project_env=Path(__file__).resolve().parents[1] / '.env')

# 将 config.yaml 值桥接到环境变量，以便 os.getenv() 获取。
# config.yaml 是终端设置的权威来源 — 覆盖 .env。
_config_path = _kclaw_home / 'config.yaml'
if _config_path.exists():
    try:
        import yaml as _yaml
        with open(_config_path, encoding="utf-8") as _f:
            _cfg = _yaml.safe_load(_f) or {}
        # 在桥接到环境变量之前展开 ${ENV_VAR} 引用。
        from kclaw_cli.config import _expand_env_vars
        _cfg = _expand_env_vars(_cfg)
        # 顶层简单值（仅回退 — 不要覆盖 .env）
        for _key, _val in _cfg.items():
            if isinstance(_val, (str, int, float, bool)) and _key not in os.environ:
                os.environ[_key] = str(_val)
        # 终端配置是嵌套的 — 桥接到 TERMINAL_* 环境变量。
        # config.yaml 覆盖 .env 因为这是文档化的配置路径。
        _terminal_cfg = _cfg.get("terminal", {})
        if _terminal_cfg and isinstance(_terminal_cfg, dict):
            _terminal_env_map = {
                "backend": "TERMINAL_ENV",
                "cwd": "TERMINAL_CWD",
                "timeout": "TERMINAL_TIMEOUT",
                "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",
                "docker_image": "TERMINAL_DOCKER_IMAGE",
                "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",
                "singularity_image": "TERMINAL_SINGULARITY_IMAGE",
                "modal_image": "TERMINAL_MODAL_IMAGE",
                "daytona_image": "TERMINAL_DAYTONA_IMAGE",
                "ssh_host": "TERMINAL_SSH_HOST",
                "ssh_user": "TERMINAL_SSH_USER",
                "ssh_port": "TERMINAL_SSH_PORT",
                "ssh_key": "TERMINAL_SSH_KEY",
                "container_cpu": "TERMINAL_CONTAINER_CPU",
                "container_memory": "TERMINAL_CONTAINER_MEMORY",
                "container_disk": "TERMINAL_CONTAINER_DISK",
                "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
                "docker_volumes": "TERMINAL_DOCKER_VOLUMES",
                "sandbox_dir": "TERMINAL_SANDBOX_DIR",
                "persistent_shell": "TERMINAL_PERSISTENT_SHELL",
            }
            for _cfg_key, _env_var in _terminal_env_map.items():
                if _cfg_key in _terminal_cfg:
                    _val = _terminal_cfg[_cfg_key]
                    if isinstance(_val, list):
                        os.environ[_env_var] = json.dumps(_val)
                    else:
                        os.environ[_env_var] = str(_val)
        # 压缩配置由 run_agent.py 直接从 config.yaml 读取
        # 和 auxiliary_client.py — 无需环境变量桥接。
        # 辅助模型/直接端点覆盖 (vision, web_extract)。
        # 每个任务有 provider/model/base_url/api_key；将非默认值桥接到环境变量。
        _auxiliary_cfg = _cfg.get("auxiliary", {})
        if _auxiliary_cfg and isinstance(_auxiliary_cfg, dict):
            _aux_task_env = {
                "vision": {
                    "provider": "AUXILIARY_VISION_PROVIDER",
                    "model": "AUXILIARY_VISION_MODEL",
                    "base_url": "AUXILIARY_VISION_BASE_URL",
                    "api_key": "AUXILIARY_VISION_API_KEY",
                },
                "web_extract": {
                    "provider": "AUXILIARY_WEB_EXTRACT_PROVIDER",
                    "model": "AUXILIARY_WEB_EXTRACT_MODEL",
                    "base_url": "AUXILIARY_WEB_EXTRACT_BASE_URL",
                    "api_key": "AUXILIARY_WEB_EXTRACT_API_KEY",
                },
                "approval": {
                    "provider": "AUXILIARY_APPROVAL_PROVIDER",
                    "model": "AUXILIARY_APPROVAL_MODEL",
                    "base_url": "AUXILIARY_APPROVAL_BASE_URL",
                    "api_key": "AUXILIARY_APPROVAL_API_KEY",
                },
            }
            for _task_key, _env_map in _aux_task_env.items():
                _task_cfg = _auxiliary_cfg.get(_task_key, {})
                if not isinstance(_task_cfg, dict):
                    continue
                _prov = str(_task_cfg.get("provider", "")).strip()
                _model = str(_task_cfg.get("model", "")).strip()
                _base_url = str(_task_cfg.get("base_url", "")).strip()
                _api_key = str(_task_cfg.get("api_key", "")).strip()
                if _prov and _prov != "auto":
                    os.environ[_env_map["provider"]] = _prov
                if _model:
                    os.environ[_env_map["model"]] = _model
                if _base_url:
                    os.environ[_env_map["base_url"]] = _base_url
                if _api_key:
                    os.environ[_env_map["api_key"]] = _api_key
        _agent_cfg = _cfg.get("agent", {})
        if _agent_cfg and isinstance(_agent_cfg, dict):
            if "max_turns" in _agent_cfg:
                os.environ["KCLAW_MAX_ITERATIONS"] = str(_agent_cfg["max_turns"])
            # 桥接 agent.gateway_timeout → KCLAW_AGENT_TIMEOUT 环境变量。
            # .env 中的环境变量优先（已在 os.environ 中）。
            if "gateway_timeout" in _agent_cfg and "KCLAW_AGENT_TIMEOUT" not in os.environ:
                os.environ["KCLAW_AGENT_TIMEOUT"] = str(_agent_cfg["gateway_timeout"])
            if "gateway_timeout_warning" in _agent_cfg and "KCLAW_AGENT_TIMEOUT_WARNING" not in os.environ:
                os.environ["KCLAW_AGENT_TIMEOUT_WARNING"] = str(_agent_cfg["gateway_timeout_warning"])
        # 时区：桥接 config.yaml → KCLAW_TIMEZONE 环境变量。
        # .env 中的 KCLAW_TIMEZONE 优先（已在 os.environ 中）。
        _tz_cfg = _cfg.get("timezone", "")
        if _tz_cfg and isinstance(_tz_cfg, str) and "KCLAW_TIMEZONE" not in os.environ:
            os.environ["KCLAW_TIMEZONE"] = _tz_cfg.strip()
        # 安全设置
        _security_cfg = _cfg.get("security", {})
        if isinstance(_security_cfg, dict):
            _redact = _security_cfg.get("redact_secrets")
            if _redact is not None:
                os.environ["KCLAW_REDACT_SECRETS"] = str(_redact).lower()
    except Exception:
        pass  # 非致命；gateway 仍可以使用 .env 值运行

# 尽早验证配置结构 — 记录警告以便 gateway 运营商看到问题
try:
    from kclaw_cli.config import print_config_warnings
    print_config_warnings()
except Exception:
    pass

# Gateway 以安静模式运行 - 抑制调试输出并直接使用 cwd（无临时目录）
os.environ["KCLAW_QUIET"] = "1"

# 在消息平台上启用危险命令的交互式执行审批
os.environ["KCLAW_EXEC_ASK"] = "1"

# 为消息平台设置终端工作目录。
# 如果用户在 config.yaml 中设置了显式路径（非 "." 或 "auto"），
# 尊重它。否则使用 MESSAGING_CWD 或默认为主目录。
_configured_cwd = os.environ.get("TERMINAL_CWD", "")
if not _configured_cwd or _configured_cwd in (".", "auto", "cwd"):
    messaging_cwd = os.getenv("MESSAGING_CWD") or str(Path.home())
    os.environ["TERMINAL_CWD"] = messaging_cwd

from gateway.config import (
    Platform,
    GatewayConfig,
    load_gateway_config,
)
from gateway.session import (
    SessionStore,
    SessionSource,
    SessionContext,
    build_session_context,
    build_session_context_prompt,
    build_session_key,
)
from gateway.delivery import DeliveryRouter
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType


def _normalize_whatsapp_identifier(value: str) -> str:
    """将 WhatsApp JID/LID 语法剥离为其稳定的数字标识符。"""
    return (
        str(value or "")
        .strip()
        .replace("+", "", 1)
        .split(":", 1)[0]
        .split("@", 1)[0]
    )


def _expand_whatsapp_auth_aliases(identifier: str) -> set:
    """使用桥接会话映射文件解析 WhatsApp phone/LID 别名。"""
    normalized = _normalize_whatsapp_identifier(identifier)
    if not normalized:
        return set()

    session_dir = _kclaw_home / "whatsapp" / "session"
    resolved = set()
    queue = [normalized]

    while queue:
        current = queue.pop(0)
        if not current or current in resolved:
            continue

        resolved.add(current)
        for suffix in ("", "_reverse"):
            mapping_path = session_dir / f"lid-mapping-{current}{suffix}.json"
            if not mapping_path.exists():
                continue
            try:
                mapped = _normalize_whatsapp_identifier(
                    json.loads(mapping_path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
            if mapped and mapped not in resolved:
                queue.append(mapped)

    return resolved

logger = logging.getLogger(__name__)

# 哨兵，在会话开始处理时立即放入 _running_agents，
# *在任何 await 之前*。防止第二个相同会话的消息
# 在保护检查和实际代理创建之间的异步间隙期间
# 绕过 "already running" 保护。
_AGENT_PENDING_SENTINEL = object()


def _resolve_runtime_agent_kwargs() -> dict:
    """为 gateway 创建的 AIAgent 实例解析提供商凭据。"""
    from kclaw_cli.runtime_provider import (
        resolve_runtime_provider,
        format_runtime_provider_error,
    )

    try:
        runtime = resolve_runtime_provider(
            requested=os.getenv("KCLAW_INFERENCE_PROVIDER"),
        )
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc

    return {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "api_mode": runtime.get("api_mode"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
        "credential_pool": runtime.get("credential_pool"),
    }


def _build_media_placeholder(event) -> str:
    """为纯媒体事件构建文本占位符，以免它们被丢弃。

    当照片/文档在活动处理期间排队，稍后出队时，只提取 .text。
    如果事件没有标题，媒体将静默丢失。这构建一个占位符，
    视觉富化管道将用真实描述替换它。
    """
    parts = []
    media_urls = getattr(event, "media_urls", None) or []
    media_types = getattr(event, "media_types", None) or []
    for i, url in enumerate(media_urls):
        mtype = media_types[i] if i < len(media_types) else ""
        if mtype.startswith("image/") or getattr(event, "message_type", None) == MessageType.PHOTO:
            parts.append(f"[用户发送了一张图片: {url}]")
        elif mtype.startswith("audio/"):
            parts.append(f"[用户发送了音频: {url}]")
        else:
            parts.append(f"[用户发送了一个文件: {url}]")
    return "\n".join(parts)


def _dequeue_pending_text(adapter, session_key: str) -> str | None:
    """消费并返回排队消息的文本。

    通过构建占位符保留无标题照片/文档事件的媒体上下文，
    以便消息不会被静默丢弃。
    """
    event = adapter.get_pending_message(session_key)
    if not event:
        return None
    text = event.text
    if not text and getattr(event, "media_urls", None):
        text = _build_media_placeholder(event)
    return text


def _check_unavailable_skill(command_name: str) -> str | None:
    """检查命令是否匹配已知但未激活的技能。

    如果技能存在但被禁用或仅作为可选安装可用，返回有帮助的消息。
    如果没有找到匹配则返回 None。
    """
    # 规范化：命令使用连字符，技能名称可能使用连字符或下划线
    normalized = command_name.lower().replace("_", "-")
    try:
        from tools.skills_tool import _get_disabled_skill_names
        from agent.skill_utils import get_all_skills_dirs
        disabled = _get_disabled_skill_names()

        # 检查所有目录的禁用技能（本地 + 外部）
        for skills_dir in get_all_skills_dirs():
            if not skills_dir.exists():
                continue
            for skill_md in skills_dir.rglob("SKILL.md"):
                if any(part in ('.git', '.github', '.hub') for part in skill_md.parts):
                    continue
                name = skill_md.parent.name.lower().replace("_", "-")
                if name == normalized and name in disabled:
                    return (
                        f"**{command_name}** 技能已安装但被禁用。\n"
                        f"使用以下命令启用：``kclaw skills config``"
                    )

        # 检查可选技能（随仓库附带但未安装）
        from kclaw_constants import get_optional_skills_dir
        repo_root = Path(__file__).resolve().parent.parent
        optional_dir = get_optional_skills_dir(repo_root / "optional-skills")
        if optional_dir.exists():
            for skill_md in optional_dir.rglob("SKILL.md"):
                name = skill_md.parent.name.lower().replace("_", "-")
                if name == normalized:
                    # 构建安装路径：official/<category>/<name>
                    rel = skill_md.parent.relative_to(optional_dir)
                    parts = list(rel.parts)
                    install_path = f"official/{'/'.join(parts)}"
                    return (
                        f"**{command_name}** 技能可用但未安装。\n"
                        f"使用以下命令安装：``kclaw skills install {install_path}``"
                    )
    except Exception:
        pass
    return None


def _platform_config_key(platform: "Platform") -> str:
    """将 Platform 枚举映射到其 config.yaml 键（LOCAL→"cli"，其余→枚举值）。"""
    return "cli" if platform == Platform.LOCAL else platform.value


def _load_gateway_config() -> dict:
    """加载并解析 ~/.kclaw/config.yaml，任意错误时返回 {}。"""
    try:
        config_path = _kclaw_home / 'config.yaml'
        if config_path.exists():
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
    except Exception:
        logger.debug("无法从 %s 加载 gateway 配置", _kclaw_home / 'config.yaml')
    return {}


def _resolve_gateway_model(config: dict | None = None) -> str:
    """从 config.yaml 读取模型 — 单一真相来源。

    没有这个，临时 AIAgent 实例（内存刷新、/compress）会回退到
    当活动提供商是 openai-codex 时会失败的硬编码默认值。
    """
    cfg = config if config is not None else _load_gateway_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        return model_cfg
    elif isinstance(model_cfg, dict):
        return model_cfg.get("default") or model_cfg.get("model") or ""
    return ""


def _resolve_kclaw_bin() -> Optional[list[str]]:
    """将 KClaw 更新命令解析为 argv 部分。

    按顺序尝试：
    1. ``shutil.which("kclaw")`` — 标准 PATH 查找
    2. ``sys.executable -m kclaw_cli.main`` — 当 KClaw 从
       venv/module 调用运行且 ``kclaw`` shim 不在 PATH 上时的回退

    返回可供引用/连接的准备好的 argv 部分，如果都不行则返回 ``None``。
    """
    import shutil

    kclaw_bin = shutil.which("kclaw")
    if kclaw_bin:
        return [kclaw_bin]

    try:
        import importlib.util

        if importlib.util.find_spec("kclaw_cli") is not None:
            return [sys.executable, "-m", "kclaw_cli.main"]
    except Exception:
        pass

    return None


class GatewayRunner:
    """
    主 gateway 控制器。

    管理所有平台适配器的生命周期以及
    与代理之间的消息路由。
    """

    # 类级别默认值，以便测试中的部分构造不会
    # 在属性访问时崩溃。
    _running_agents_ts: Dict[str, float] = {}
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or load_gateway_config()
        self.adapters: Dict[Platform, BasePlatformAdapter] = {}

        # 从 config.yaml / 环境变量加载临时配置。
        # 两者都在 API 调用时注入，从不持久化。
        self._prefill_messages = self._load_prefill_messages()
        self._ephemeral_system_prompt = self._load_ephemeral_system_prompt()
        self._reasoning_config = self._load_reasoning_config()
        self._show_reasoning = self._load_show_reasoning()
        self._provider_routing = self._load_provider_routing()
        self._fallback_model = self._load_fallback_model()
        self._smart_model_routing = self._load_smart_model_routing()

        # 将进程注册表连接到会话存储以进行重置保护
        from tools.process_registry import process_registry
        self.session_store = SessionStore(
            self.config.sessions_dir, self.config,
            has_active_processes_fn=lambda key: process_registry.has_active_for_session(key),
        )
        self.delivery_router = DeliveryRouter(self.config)
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._exit_cleanly = False
        self._exit_with_failure = False
        self._exit_reason: Optional[str] = None
        
        # 跟踪每个会话的运行代理以支持中断
        # 键：session_key，值：AIAgent 实例
        self._running_agents: Dict[str, Any] = {}
        self._running_agents_ts: Dict[str, float] = {}  # 每个会话的开始时间戳
        self._pending_messages: Dict[str, str] = {}  # 中断期间排队的消息

        # 缓存每个会话的 AIAgent 实例以保留提示词缓存。
        # 没有这个，每个消息都会创建新的 AIAgent，每次都会重建
        # 系统提示词（包括内存），破坏前缀缓存
        # 并在有提示词缓存的提供商（Anthropic）上花费约 10 倍成本。
        # 键：session_key，值：(AIAgent, config_signature_str)
        import threading as _threading
        self._agent_cache: Dict[str, tuple] = {}
        self._agent_cache_lock = _threading.Lock()

        # 跟踪当主提供商被速率限制时的有效回退模型/提供商。
        # 在激活了回退的代理运行后设置；
        # 当主模型再次成功或用户通过 /model 切换时清除。
        self._effective_model: Optional[str] = None
        self._effective_provider: Optional[str] = None

        # 每个会话的模型覆盖来自 /model 命令。
        # 键：session_key，值：带有 model/provider/api_key/base_url/api_mode 的字典
        self._session_model_overrides: Dict[str, Dict[str, str]] = {}
        # 跟踪每个会话的待处理执行审批
        # 键：session_key，值：{"command": str, "pattern_key": str, ...}
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}

        # 跟踪连接失败的平台以便后台重连。
        # 键：Platform 枚举，值：{"config": platform_config, "attempts": int, "next_retry": float}
        self._failed_platforms: Dict[Platform, Dict[str, Any]] = {}

        # 跟踪每个会话的待处理 /update 提示响应。
        # 键：session_key，当提示等待用户输入时为 True
        self._update_prompt_pending: Dict[str, bool] = {}

        # 按 gateway 会话键持久化的 Honcho 管理器。
        # 这在短命的每消息 AIAgent 实例中保留 write_frequency="session" 语义。



        # 确保 tirith 安全扫描器可用（按需下载）
        try:
            from tools.tirith_security import ensure_installed
            ensure_installed(log_failures=False)
        except Exception:
            pass  # 非致命 — 在扫描时失败打开如果不可用
        
        # 初始化会话数据库以支持 session_search 工具
        self._session_db = None
        try:
            from kclaw_state import SessionDB
            self._session_db = SessionDB()
        except Exception as e:
            logger.debug("SQLite 会话存储不可用：%s", e)
        
        # DM 配对存储用于基于代码的用户授权
        from gateway.pairing import PairingStore
        self.pairing_store = PairingStore()
        
        # 事件钩子系统
        from gateway.hooks import HookRegistry
        self.hooks = HookRegistry()

        # 每个聊天室的语音回复模式："off" | "voice_only" | "all"
        self._voice_mode: Dict[str, str] = self._load_voice_modes()

        # 跟踪后台任务以防止在执行中间被垃圾回收
        self._background_tasks: set = set()




    # -- 设置技能可用性 ----------------------------------------

    def _has_setup_skill(self) -> bool:
        """检查 kclaw-setup 技能是否已安装。"""
        try:
            from tools.skill_manager_tool import _find_skill
            return _find_skill("kclaw-setup") is not None
        except Exception:
            return False

    # -- 语音模式持久化 ------------------------------------------

    _VOICE_MODE_PATH = _kclaw_home / "gateway_voice_mode.json"

    def _load_voice_modes(self) -> Dict[str, str]:
        try:
            data = json.loads(self._VOICE_MODE_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

        if not isinstance(data, dict):
            return {}

        valid_modes = {"off", "voice_only", "all"}
        return {
            str(chat_id): mode
            for chat_id, mode in data.items()
            if mode in valid_modes
        }

    def _save_voice_modes(self) -> None:
        try:
            self._VOICE_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._VOICE_MODE_PATH.write_text(
                json.dumps(self._voice_mode, indent=2)
            )
        except OSError as e:
            logger.warning("保存语音模式失败: %s", e)

    def _set_adapter_auto_tts_disabled(self, adapter, chat_id: str, disabled: bool) -> None:
        """如果存在则更新适配器的内存中自动 TTS 抑制集。"""
        disabled_chats = getattr(adapter, "_auto_tts_disabled_chats", None)
        if not isinstance(disabled_chats, set):
            return
        if disabled:
            disabled_chats.add(chat_id)
        else:
            disabled_chats.discard(chat_id)

    def _sync_voice_mode_state_to_adapter(self, adapter) -> None:
        """将持续的 /voice off 状态恢复到实时平台适配器。"""
        disabled_chats = getattr(adapter, "_auto_tts_disabled_chats", None)
        if not isinstance(disabled_chats, set):
            return
        disabled_chats.clear()
        disabled_chats.update(
            chat_id for chat_id, mode in self._voice_mode.items() if mode == "off"
        )

    # -----------------------------------------------------------------

    def _flush_memories_for_session(
        self,
        old_session_id: str,
    ):
        """提示代理在上下文丢失前保存内存/技能。

        同步工作器 — 旨在从异步上下文中通过 run_in_executor 调用，
        以便它不会阻塞事件循环。
        """
        # 跳过 cron 会话 — 它们无头运行，没有有意义的用户
        # 对话来提取记忆。
        if old_session_id and old_session_id.startswith("cron_"):
            logger.debug("跳过 cron 会话的内存刷新: %s", old_session_id)
            return

        try:
            history = self.session_store.load_transcript(old_session_id)
            if not history or len(history) < 4:
                return

            from run_agent import AIAgent
            runtime_kwargs = _resolve_runtime_agent_kwargs()
            if not runtime_kwargs.get("api_key"):
                return

            # 从配置解析模型 — AIAgent 的默认值是 OpenRouter-
            # 格式的 ("anthropic/claude-opus-4.6")，当
            # 活动提供商是 openai-codex 时会失败。
            model = _resolve_gateway_model()

            tmp_agent = AIAgent(
                **runtime_kwargs,
                model=model,
                max_iterations=8,
                quiet_mode=True,
                skip_memory=True,  # 刷新代理 — 无内存提供者
                enabled_toolsets=["memory", "skills"],
                session_id=old_session_id,
            )
            # 完全静默刷新代理 — quiet_mode 仅抑制初始化
            # 消息；工具调用输出仍通过
            # _safe_print → _print_fn 泄漏到终端。设置为无操作以防止。
            tmp_agent._print_fn = lambda *a, **kw: None

            # 从 transcript 构建对话历史
            msgs = [
                {"role": m.get("role"), "content": m.get("content")}
                for m in history
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]

            # 从磁盘读取实时内存状态，以便刷新代理可以查看
            # 已保存的内容并避免覆盖较新的条目。
            _current_memory = ""
            try:
                from tools.memory_tool import get_memory_dir
                _mem_dir = get_memory_dir()
                for fname, label in [
                    ("MEMORY.md", "MEMORY (your personal notes)"),
                    ("USER.md", "USER PROFILE (who the user is)"),
                ]:
                    fpath = _mem_dir / fname
                    if fpath.exists():
                        content = fpath.read_text(encoding="utf-8").strip()
                        if content:
                            _current_memory += f"\n\n## 当前 {label}:\n{content}"
            except Exception:
                pass  # 非致命 — 刷新仍然有效，只是没有防护

            # 给代理一个真正的轮次来思考要保存什么
            flush_prompt = (
                "[System: This session is about to be automatically reset due to "
                "inactivity or a scheduled daily reset. The conversation context "
                "will be cleared after this turn.\n\n"
                "Review the conversation above and:\n"
                "1. Save any important facts, preferences, or decisions to memory "
                "(user profile or your notes) that would be useful in future sessions.\n"
                "2. If you discovered a reusable workflow or solved a non-trivial "
                "problem, consider saving it as a skill.\n"
                "3. If nothing is worth saving, that's fine — just skip.\n\n"
            )

            if _current_memory:
                flush_prompt += (
                    "IMPORTANT — here is the current live state of memory. Other "
                    "sessions, cron jobs, or the user may have updated it since this "
                    "conversation ended. Do NOT overwrite or remove entries unless "
                    "the conversation above reveals something that genuinely "
                    "supersedes them. Only add new information that is not already "
                    "captured below."
                    f"{_current_memory}\n\n"
                )

            flush_prompt += (
                "Do NOT respond to the user. Just use the memory and skill_manage "
                "tools if needed, then stop.]"
            )

            tmp_agent.run_conversation(
                user_message=flush_prompt,
                conversation_history=msgs,
            )
            logger.info("会话 %s 的重置前内存刷新已完成", old_session_id)
        except Exception as e:
            logger.debug("会话 %s 的重置前内存刷新失败：%s", old_session_id, e)

    async def _async_flush_memories(
        self,
        old_session_id: str,
    ):
        """在线程池中运行同步内存刷新，以便不会阻塞事件循环。"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._flush_memories_for_session,
            old_session_id,
        )

    @property
    def should_exit_cleanly(self) -> bool:
        return self._exit_cleanly

    @property
    def should_exit_with_failure(self) -> bool:
        return self._exit_with_failure

    @property
    def exit_reason(self) -> Optional[str]:
        return self._exit_reason

    def _session_key_for_source(self, source: SessionSource) -> str:
        """解析源的当前会话键，在可用时尊重 gateway 配置。"""
        if hasattr(self, "session_store") and self.session_store is not None:
            try:
                session_key = self.session_store._generate_session_key(source)
                if isinstance(session_key, str) and session_key:
                    return session_key
            except Exception:
                pass
        config = getattr(self, "config", None)
        return build_session_key(
            source,
            group_sessions_per_user=getattr(config, "group_sessions_per_user", True),
            thread_sessions_per_user=getattr(config, "thread_sessions_per_user", False),
        )

    def _resolve_turn_agent_config(self, user_message: str, model: str, runtime_kwargs: dict) -> dict:
        from agent.smart_model_routing import resolve_turn_route

        primary = {
            "model": model,
            "api_key": runtime_kwargs.get("api_key"),
            "base_url": runtime_kwargs.get("base_url"),
            "provider": runtime_kwargs.get("provider"),
            "api_mode": runtime_kwargs.get("api_mode"),
            "command": runtime_kwargs.get("command"),
            "args": list(runtime_kwargs.get("args") or []),
            "credential_pool": runtime_kwargs.get("credential_pool"),
        }
        return resolve_turn_route(user_message, getattr(self, "_smart_model_routing", {}), primary)

    async def _handle_adapter_fatal_error(self, adapter: BasePlatformAdapter) -> None:
        """在启动后对适配器失败做出反应。

        如果错误可重试（如网络故障、DNS 失败），则将
        平台排队等待后台重连，而不是永久放弃。
        """
        logger.error(
            "%s 适配器致命错误（%s）：%s",
            adapter.platform.value,
            adapter.fatal_error_code or "未知",
            adapter.fatal_error_message or "未知错误",
        )

        existing = self.adapters.get(adapter.platform)
        if existing is adapter:
            try:
                await adapter.disconnect()
            finally:
                self.adapters.pop(adapter.platform, None)
                self.delivery_router.adapters = self.adapters

        # 如果错误可重试，则排队等待后台重连
        if adapter.fatal_error_retryable:
            platform_config = self.config.platforms.get(adapter.platform)
            if platform_config and adapter.platform not in self._failed_platforms:
                self._failed_platforms[adapter.platform] = {
                    "config": platform_config,
                    "attempts": 0,
                    "next_retry": time.monotonic() + 30,
                }
                logger.info(
                    "%s 已排队等待后台重连",
                    adapter.platform.value,
                )

        if not self.adapters and not self._failed_platforms:
            self._exit_reason = adapter.fatal_error_message or "所有消息适配器已断开连接"
            if adapter.fatal_error_retryable:
                self._exit_with_failure = True
                logger.error("没有剩余的已连接消息平台。关闭 gateway 以便服务重启。")
            else:
                logger.error("没有剩余的已连接消息平台。干净地关闭 gateway。")
            await self.stop()
        elif not self.adapters and self._failed_platforms:
            # 所有平台都已离线并排队等待后台重连。
            # 如果错误可重试，则以失败状态退出，以便 systemd Restart=on-failure
            # 可以重启进程。否则保持运行并在后台持续重试。
            if adapter.fatal_error_retryable:
                self._exit_reason = adapter.fatal_error_message or "所有消息平台因可重试错误失败"
                self._exit_with_failure = True
                logger.error(
                    "所有消息平台因可重试错误失败。"
                    "关闭 gateway 以便服务重启（systemd 将重试）。"
                )
                await self.stop()
            else:
                logger.warning(
                    "没有剩余的已连接消息平台，但有 %d 个平台排队等待重连",
                    len(self._failed_platforms),
                )

    def _request_clean_exit(self, reason: str) -> None:
        self._exit_cleanly = True
        self._exit_reason = reason
        self._shutdown_event.set()
    
    @staticmethod
    def _load_prefill_messages() -> List[Dict[str, Any]]:
        """从配置或环境变量加载临时填充消息。
        
        首先检查 KCLAW_PREFILL_MESSAGES_FILE 环境变量，然后回退到
        ~/.kclaw/config.yaml 中的 prefill_messages_file 键。
        相对路径从 ~/.kclaw/ 解析。
        """
        import json as _json
        file_path = os.getenv("KCLAW_PREFILL_MESSAGES_FILE", "")
        if not file_path:
            try:
                import yaml as _y
                cfg_path = _kclaw_home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as _f:
                        cfg = _y.safe_load(_f) or {}
                    file_path = cfg.get("prefill_messages_file", "")
            except Exception:
                pass
        if not file_path:
            return []
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = _kclaw_home / path
        if not path.exists():
            logger.warning("未找到预填充消息文件: %s", path)
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if not isinstance(data, list):
                logger.warning("预填充消息文件必须包含 JSON 数组: %s", path)
                return []
            return data
        except Exception as e:
            logger.warning("从 %s 加载预填充消息失败: %s", path, e)
            return []

    @staticmethod
    def _load_ephemeral_system_prompt() -> str:
        """从配置或环境变量加载临时系统提示词。
        
        首先检查 KCLAW_EPHEMERAL_SYSTEM_PROMPT 环境变量，然后回退到
        agent.system_prompt in ~/.kclaw/config.yaml。
        """
        prompt = os.getenv("KCLAW_EPHEMERAL_SYSTEM_PROMPT", "")
        if prompt:
            return prompt
        try:
            import yaml as _y
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                return (cfg.get("agent", {}).get("system_prompt", "") or "").strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _load_reasoning_config() -> dict | None:
        """从 config.yaml 加载推理强度配置。

        从 config.yaml 读取 agent.reasoning_effort。有效值: "xhigh"、
        "high"、"medium"、"low"、"minimal"、"none"。返回 None 则使用默认值 (medium)。
        """
        from kclaw_constants import parse_reasoning_effort
        effort = ""
        try:
            import yaml as _y
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                effort = str(cfg.get("agent", {}).get("reasoning_effort", "") or "").strip()
        except Exception:
            pass
        result = parse_reasoning_effort(effort)
        if effort and effort.strip() and result is None:
            logger.warning("未知的 reasoning_effort '%s'，使用默认值 (medium)", effort)
        return result

    @staticmethod
    def _load_show_reasoning() -> bool:
        """从 config.yaml 的 display 部分加载 show_reasoning 开关。"""
        try:
            import yaml as _y
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                return bool(cfg.get("display", {}).get("show_reasoning", False))
        except Exception:
            pass
        return False

    @staticmethod
    def _load_background_notifications_mode() -> str:
        """从配置或环境变量加载后台进程通知模式。

        模式:
          - ``all``    — 推送运行输出更新 *以及* 最终消息 (默认)
          - ``result`` — 仅最终完成消息 (无论退出码如何)
          - ``error``  — 仅在退出码非零时的最终消息
          - ``off``    — 完全不显示观察者消息
        """
        mode = os.getenv("KCLAW_BACKGROUND_NOTIFICATIONS", "")
        if not mode:
            try:
                import yaml as _y
                cfg_path = _kclaw_home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as _f:
                        cfg = _y.safe_load(_f) or {}
                    raw = cfg.get("display", {}).get("background_process_notifications")
                    if raw is False:
                        mode = "off"
                    elif raw not in (None, ""):
                        mode = str(raw)
            except Exception:
                pass
        mode = (mode or "all").strip().lower()
        valid = {"all", "result", "error", "off"}
        if mode not in valid:
            logger.warning(
                "未知的 background_process_notifications '%s'，默认为 'all'",
                mode,
            )
            return "all"
        return mode

    @staticmethod
    def _load_provider_routing() -> dict:
        """从 config.yaml 加载 OpenRouter 提供商路由偏好。"""
        try:
            import yaml as _y
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                return cfg.get("provider_routing", {}) or {}
        except Exception:
            pass
        return {}

    @staticmethod
    def _load_fallback_model() -> list | dict | None:
        """从 config.yaml 加载备用提供商链。

        返回提供商字典列表 (``fallback_providers``)、单个字典 (旧版 ``fallback_model``)，
        或 None 如果未配置。AIAgent.__init__ 会将两种格式规范化为链式结构。
        """
        try:
            import yaml as _y
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                fb = cfg.get("fallback_providers") or cfg.get("fallback_model") or None
                if fb:
                    return fb
        except Exception:
            pass
        return None

    @staticmethod
    def _load_smart_model_routing() -> dict:
        """加载可选的智能廉价/强模型路由配置。"""
        try:
            import yaml as _y
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                return cfg.get("smart_model_routing", {}) or {}
        except Exception:
            pass
        return {}

    async def start(self) -> bool:
        """
        启动网关并连接所有已配置的平台适配器。
        
        如果至少一个适配器成功连接则返回 True。
        """
        logger.info("正在启动 KClaw 网关...")
        logger.info("会话存储: %s", self.config.sessions_dir)
        try:
            from kclaw_cli.profiles import get_active_profile_name
            _profile = get_active_profile_name()
            if _profile and _profile != "default":
                logger.info("活动配置: %s", _profile)
        except Exception:
            pass
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(gateway_state="starting", exit_reason=None)
        except Exception:
            pass
        
        # 如果没有配置用户白名单且未选择开放访问则发出警告
        _any_allowlist = any(
            os.getenv(v)
            for v in ("TELEGRAM_ALLOWED_USERS", "DISCORD_ALLOWED_USERS",
                       "WHATSAPP_ALLOWED_USERS", "SLACK_ALLOWED_USERS",
                       "SIGNAL_ALLOWED_USERS", "SIGNAL_GROUP_ALLOWED_USERS",
                       "EMAIL_ALLOWED_USERS",
                       "SMS_ALLOWED_USERS", "MATTERMOST_ALLOWED_USERS",
                       "MATRIX_ALLOWED_USERS", "DINGTALK_ALLOWED_USERS",
                       "FEISHU_ALLOWED_USERS",
                       "WECOM_ALLOWED_USERS",
                       "BLUEBUBBLES_ALLOWED_USERS",
                       "GATEWAY_ALLOWED_USERS")
        )
        _allow_all = os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in ("true", "1", "yes") or any(
            os.getenv(v, "").lower() in ("true", "1", "yes")
            for v in ("TELEGRAM_ALLOW_ALL_USERS", "DISCORD_ALLOW_ALL_USERS",
                       "WHATSAPP_ALLOW_ALL_USERS", "SLACK_ALLOW_ALL_USERS",
                       "SIGNAL_ALLOW_ALL_USERS", "EMAIL_ALLOW_ALL_USERS",
                       "SMS_ALLOW_ALL_USERS", "MATTERMOST_ALLOW_ALL_USERS",
                       "MATRIX_ALLOW_ALL_USERS", "DINGTALK_ALLOW_ALL_USERS",
                       "FEISHU_ALLOW_ALL_USERS",
                       "WECOM_ALLOW_ALL_USERS",
                       "BLUEBUBBLES_ALLOW_ALL_USERS")
        )
        if not _any_allowlist and not _allow_all:
            logger.warning(
                "未配置用户白名单。所有未授权用户将被拒绝。"
                "在 ~/.kclaw/.env 中设置 GATEWAY_ALLOW_ALL_USERS=true 以允许开放访问，"
                "或配置平台白名单（例如，TELEGRAM_ALLOWED_USERS=your_id）。"
            )
                
        # 发现并加载事件钩子
        self.hooks.discover_and_load()
        
        # 从检查点恢复后台进程 (崩溃恢复)
        try:
            from tools.process_registry import process_registry
            recovered = process_registry.recover_from_checkpoint()
            if recovered:
                logger.info("从上次运行中恢复了 %s 个后台进程", recovered)
        except Exception as e:
            logger.warning("进程检查点恢复: %s", e)
        
        connected_count = 0
        enabled_platform_count = 0
        startup_nonretryable_errors: list[str] = []
        startup_retryable_errors: list[str] = []
        
        # 初始化并连接每个已配置的平台
        for platform, platform_config in self.config.platforms.items():
            if not platform_config.enabled:
                continue
            enabled_platform_count += 1
            
            adapter = self._create_adapter(platform, platform_config)
            if not adapter:
                logger.warning("%s 没有可用的适配器", platform.value)
                continue
            
            # 设置消息和致命错误处理器
            adapter.set_message_handler(self._handle_message)
            adapter.set_fatal_error_handler(self._handle_adapter_fatal_error)
            adapter.set_session_store(self.session_store)
            
            # 尝试连接
            logger.info("正在连接到 %s...", platform.value)
            try:
                success = await adapter.connect()
                if success:
                    self.adapters[platform] = adapter
                    self._sync_voice_mode_state_to_adapter(adapter)
                    connected_count += 1
                    logger.info("✓ %s 已连接", platform.value)
                else:
                    logger.warning("✗ %s 连接失败", platform.value)
                    if adapter.has_fatal_error:
                        target = (
                            startup_retryable_errors
                            if adapter.fatal_error_retryable
                            else startup_nonretryable_errors
                        )
                        target.append(
                            f"{platform.value}: {adapter.fatal_error_message}"
                        )
                        # 如果错误可重试，则排队等待重连
                        if adapter.fatal_error_retryable:
                            self._failed_platforms[platform] = {
                                "config": platform_config,
                                "attempts": 1,
                                "next_retry": time.monotonic() + 30,
                            }
                    else:
                        startup_retryable_errors.append(
                            f"{platform.value}: 连接失败"
                        )
                        # 没有致命错误信息意味着可能是临时问题 — 排队等待重试
                        self._failed_platforms[platform] = {
                            "config": platform_config,
                            "attempts": 1,
                            "next_retry": time.monotonic() + 30,
                        }
            except Exception as e:
                logger.error("✗ %s 错误: %s", platform.value, e)
                startup_retryable_errors.append(f"{platform.value}: {e}")
                # 意外异常通常是临时的 — 排队等待重试
                self._failed_platforms[platform] = {
                    "config": platform_config,
                    "attempts": 1,
                    "next_retry": time.monotonic() + 30,
                }
        
        if connected_count == 0:
            if startup_nonretryable_errors:
                reason = "; ".join(startup_nonretryable_errors)
                logger.error("网关遇到不可重试的启动冲突: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                self._request_clean_exit(reason)
                return True
            if enabled_platform_count > 0:
                reason = "; ".join(startup_retryable_errors) or "所有已配置的消息平台都连接失败"
                logger.error("网关未能连接任何已配置的消息平台: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                return False
            logger.warning("未启用任何消息平台。")
            logger.info("网关将继续运行以执行定时任务。")
        
        # 更新传递路由器的适配器
        self.delivery_router.adapters = self.adapters
        
        self._running = True
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(gateway_state="running", exit_reason=None)
        except Exception:
            pass
        
        # 触发 gateway:startup 钩子
        hook_count = len(self.hooks.loaded_hooks)
        if hook_count:
            logger.info("已加载 %s 个钩子", hook_count)
        await self.hooks.emit("gateway:startup", {
            "platforms": [p.value for p in self.adapters.keys()],
        })
        
        if connected_count > 0:
            logger.info("网关运行中，使用 %s 个平台", connected_count)
        
        # 构建初始渠道目录以解析 send_message 名称
        try:
            from gateway.channel_directory import build_channel_directory
            directory = build_channel_directory(self.adapters)
            ch_count = sum(len(chs) for chs in directory.get("platforms", {}).values())
            logger.info("渠道目录已构建: %d 个目标", ch_count)
        except Exception as e:
            logger.warning("渠道目录构建失败: %s", e)
        
        # 检查我们是否正在重启 /update 命令后的状态。如果更新仍在运行，
        # 继续监视以便在更新实际完成后通知。
        notified = await self._send_update_notification()
        if not notified and any(
            path.exists()
            for path in (
                _kclaw_home / ".update_pending.json",
                _kclaw_home / ".update_pending.claimed.json",
            )
        ):
            self._schedule_update_notification_watch()

        # 排出所有已恢复的进程观察者 (来自崩溃恢复检查点)
        try:
            from tools.process_registry import process_registry
            while process_registry.pending_watchers:
                watcher = process_registry.pending_watchers.pop(0)
                asyncio.create_task(self._run_process_watcher(watcher))
                logger.info("恢复了对进程 %s 的观察", watcher.get("session_id"))
        except Exception as e:
            logger.error("恢复的观察者设置错误: %s", e)

        # 启动后台会话过期观察者以主动刷新内存
        asyncio.create_task(self._session_expiry_watcher())

        # 启动后台重连观察者以重连启动时失败的平台
        if self._failed_platforms:
            logger.info(
                "为 %d 个失败平台启动重连观察者: %s",
                len(self._failed_platforms),
                ", ".join(p.value for p in self._failed_platforms),
            )
        asyncio.create_task(self._platform_reconnect_watcher())

        logger.info("按 Ctrl+C 停止")
        
        return True
    
    async def _session_expiry_watcher(self, interval: int = 300):
        """后台任务，主动刷新已过期会话的内存。
        
        每隔 `interval` 秒运行一次 (默认 5 分钟)。对于每个根据其重置策略
        已过期的会话，在线程池中刷新内存并标记会话以防止再次被刷新。

        这意味着在用户发送下一条消息时内存已经保存，
        因此没有阻塞延迟。
        """
        await asyncio.sleep(60)  # 初始延迟 — 让网关完全启动
        _flush_failures: dict[str, int] = {}  # session_id -> 连续失败次数
        _MAX_FLUSH_RETRIES = 3
        while self._running:
            try:
                self.session_store._ensure_loaded()
                # 首先收集已过期的会话，然后记录单个摘要。
                _expired_entries = []
                for key, entry in list(self.session_store._entries.items()):
                    if entry.memory_flushed:
                        continue
                    if not self.session_store._is_session_expired(entry):
                        continue
                    _expired_entries.append((key, entry))

                if _expired_entries:
                    # 从会话键中提取平台名称以生成紧凑摘要。
                    # 键格式如 "agent:main:telegram:dm:12345" — 平台是字段 [2]。
                    _platforms: dict[str, int] = {}
                    for _k, _e in _expired_entries:
                        _parts = _k.split(":")
                        _plat = _parts[2] if len(_parts) > 2 else "unknown"
                        _platforms[_plat] = _platforms.get(_plat, 0) + 1
                    _plat_summary = ", ".join(
                        f"{p}:{c}" for p, c in sorted(_platforms.items())
                    )
                    logger.info(
                        "会话过期: %d 个会话待刷新 (%s)",
                        len(_expired_entries), _plat_summary,
                    )

                for key, entry in _expired_entries:
                    try:
                        await self._async_flush_memories(entry.session_id)
                        # 关闭缓存代理上的内存提供者
                        cached_agent = self._running_agents.get(key)
                        if cached_agent and cached_agent is not _AGENT_PENDING_SENTINEL:
                            try:
                                if hasattr(cached_agent, 'shutdown_memory_provider'):
                                    cached_agent.shutdown_memory_provider()
                            except Exception:
                                pass
                        # 标记为已刷新并持久化到磁盘，以便标志
                        # 在网关重启后仍然有效。
                        with self.session_store._lock:
                            entry.memory_flushed = True
                            self.session_store._save()
                        logger.debug(
                            "会话 %s 的内存刷新已完成",
                            entry.session_id,
                        )
                        _flush_failures.pop(entry.session_id, None)
                    except Exception as e:
                        failures = _flush_failures.get(entry.session_id, 0) + 1
                        _flush_failures[entry.session_id] = failures
                        if failures >= _MAX_FLUSH_RETRIES:
                            logger.warning(
                                "%d 次尝试后放弃刷新 %s 的内存: %s。"
                                "标记为已刷新以防止无限重试循环。",
                                failures, entry.session_id, e,
                            )
                            with self.session_store._lock:
                                entry.memory_flushed = True
                                self.session_store._save()
                            _flush_failures.pop(entry.session_id, None)
                        else:
                            logger.debug(
                                "%s 的内存刷新失败 (%d/%d): %s",
                                failures, _MAX_FLUSH_RETRIES, entry.session_id, e,
                            )

                if _expired_entries:
                    _flushed = sum(
                        1 for _, e in _expired_entries if e.memory_flushed
                    )
                    _failed = len(_expired_entries) - _flushed
                    if _failed:
                        logger.info(
                            "会话过期完成: %d 个已刷新, %d 个待重试",
                            _flushed, _failed,
                        )
                    else:
                        logger.info(
                            "会话过期完成: %d 个已刷新", _flushed,
                        )
            except Exception as e:
                logger.debug("会话过期观察者错误: %s", e)
            # 睡眠递增以便快速停止
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _platform_reconnect_watcher(self) -> None:
        """后台任务，定期重试连接失败的平台。

        使用指数退避: 30秒 → 60秒 → 120秒 → 240秒 → 300秒 (上限)。
        在 20 次失败尝试后或错误不可重试 (如认证令牌错误) 时停止重试该平台。
        """
        _MAX_ATTEMPTS = 20
        _BACKOFF_CAP = 300  # 5 minutes max between retries

        await asyncio.sleep(10)  # 初始延迟 — 让启动完成
        while self._running:
            if not self._failed_platforms:
                # 没有需要重连的任务 — 睡眠并再次检查
                for _ in range(30):
                    if not self._running:
                        return
                    await asyncio.sleep(1)
                continue

            now = time.monotonic()
            for platform in list(self._failed_platforms.keys()):
                if not self._running:
                    return
                info = self._failed_platforms[platform]
                if now < info["next_retry"]:
                    continue  # not time yet

                if info["attempts"] >= _MAX_ATTEMPTS:
                    logger.warning(
                        "%d 次尝试后放弃重连 %s",
                        platform.value, info["attempts"],
                    )
                    del self._failed_platforms[platform]
                    continue

                platform_config = info["config"]
                attempt = info["attempts"] + 1
                logger.info(
                    "正在重连 %s (尝试 %d/%d)...",
                    platform.value, attempt, _MAX_ATTEMPTS,
                )

                try:
                    adapter = self._create_adapter(platform, platform_config)
                    if not adapter:
                        logger.warning(
                            "重连 %s: 适配器创建返回 None，从重试队列中移除",
                            platform.value,
                        )
                        del self._failed_platforms[platform]
                        continue

                    adapter.set_message_handler(self._handle_message)
                    adapter.set_fatal_error_handler(self._handle_adapter_fatal_error)
                    adapter.set_session_store(self.session_store)

                    success = await adapter.connect()
                    if success:
                        self.adapters[platform] = adapter
                        self._sync_voice_mode_state_to_adapter(adapter)
                        self.delivery_router.adapters = self.adapters
                        del self._failed_platforms[platform]
                        logger.info("✓ %s 重连成功", platform.value)

                        # 使用新适配器重建渠道目录
                        try:
                            from gateway.channel_directory import build_channel_directory
                            build_channel_directory(self.adapters)
                        except Exception:
                            pass
                    else:
                        # 检查失败是否不可重试
                        if adapter.has_fatal_error and not adapter.fatal_error_retryable:
                            logger.warning(
                                "重连 %s: 不可重试错误 (%s)，从重试队列中移除",
                                platform.value, adapter.fatal_error_message,
                            )
                            del self._failed_platforms[platform]
                        else:
                            backoff = min(30 * (2 ** (attempt - 1)), _BACKOFF_CAP)
                            info["attempts"] = attempt
                            info["next_retry"] = time.monotonic() + backoff
                            logger.info(
                                "重连 %s 失败，%d 秒后重试",
                                platform.value, backoff,
                            )
                except Exception as e:
                    backoff = min(30 * (2 ** (attempt - 1)), _BACKOFF_CAP)
                    info["attempts"] = attempt
                    info["next_retry"] = time.monotonic() + backoff
                    logger.warning(
                        "重连 %s 错误: %s，%d 秒后重试",
                        platform.value, e, backoff,
                    )

            # 每 10 秒检查一次需要重连的平台
            for _ in range(10):
                if not self._running:
                    return
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止网关并断开所有适配器。"""
        logger.info("正在停止网关...")
        self._running = False

        for session_key, agent in list(self._running_agents.items()):
            if agent is _AGENT_PENDING_SENTINEL:
                continue
            try:
                agent.interrupt("网关正在关闭")
                logger.debug("关闭期间中断会话 %s 的运行代理", session_key[:20])
            except Exception as e:
                logger.debug("关闭期间中断代理失败: %s", e)
            # 在内存关闭前触发插件 on_session_finalize 钩子
            try:
                from kclaw_cli.plugins import invoke_hook as _invoke_hook
                _invoke_hook("on_session_finalize",
                             session_id=getattr(agent, 'session_id', None),
                             platform="gateway")
            except Exception:
                pass
            # 在实际会话边界关闭内存提供者
            try:
                if hasattr(agent, 'shutdown_memory_provider'):
                    agent.shutdown_memory_provider()
            except Exception:
                pass

        for platform, adapter in list(self.adapters.items()):
            try:
                await adapter.cancel_background_tasks()
            except Exception as e:
                logger.debug("✗ %s 后台任务取消错误: %s", platform.value, e)
            try:
                await adapter.disconnect()
                logger.info("✓ %s 已断开", platform.value)
            except Exception as e:
                logger.error("✗ %s 断开错误: %s", platform.value, e)

        # 取消所有待处理的后台任务
        for _task in list(self._background_tasks):
            _task.cancel()
        self._background_tasks.clear()

        self.adapters.clear()
        self._running_agents.clear()
        self._pending_messages.clear()
        self._pending_approvals.clear()
        self._shutdown_event.set()
        
        from gateway.status import remove_pid_file, write_runtime_status
        remove_pid_file()
        try:
            write_runtime_status(gateway_state="stopped", exit_reason=self._exit_reason)
        except Exception:
            pass
        
        logger.info("网关已停止")
    
    async def wait_for_shutdown(self) -> None:
        """等待关闭信号。"""
        await self._shutdown_event.wait()
    
    def _create_adapter(
        self, 
        platform: Platform, 
        config: Any
    ) -> Optional[BasePlatformAdapter]:
        """为平台创建相应的适配器。"""
        if hasattr(config, "extra") and isinstance(config.extra, dict):
            config.extra.setdefault(
                "group_sessions_per_user",
                self.config.group_sessions_per_user,
            )
            config.extra.setdefault(
                "thread_sessions_per_user",
                getattr(self.config, "thread_sessions_per_user", False),
            )

        if platform == Platform.TELEGRAM:
            from gateway.platforms.telegram import TelegramAdapter, check_telegram_requirements
            if not check_telegram_requirements():
                logger.warning("Telegram: python-telegram-bot 未安装")
                return None
            return TelegramAdapter(config)
        
        elif platform == Platform.DISCORD:
            from gateway.platforms.discord import DiscordAdapter, check_discord_requirements
            if not check_discord_requirements():
                logger.warning("Discord: discord.py 未安装")
                return None
            return DiscordAdapter(config)
        
        elif platform == Platform.WHATSAPP:
            from gateway.platforms.whatsapp import WhatsAppAdapter, check_whatsapp_requirements
            if not check_whatsapp_requirements():
                logger.warning("WhatsApp: Node.js 未安装或桥接未配置")
                return None
            return WhatsAppAdapter(config)
        
        elif platform == Platform.SLACK:
            from gateway.platforms.slack import SlackAdapter, check_slack_requirements
            if not check_slack_requirements():
                logger.warning("Slack: slack-bolt 未安装。运行: pip install 'kclaw[slack]'")
                return None
            return SlackAdapter(config)

        elif platform == Platform.SIGNAL:
            from gateway.platforms.signal import SignalAdapter, check_signal_requirements
            if not check_signal_requirements():
                logger.warning("Signal: SIGNAL_HTTP_URL 或 SIGNAL_ACCOUNT 未配置")
                return None
            return SignalAdapter(config)

        elif platform == Platform.HOMEASSISTANT:
            from gateway.platforms.homeassistant import HomeAssistantAdapter, check_ha_requirements
            if not check_ha_requirements():
                logger.warning("HomeAssistant: aiohttp 未安装或 HASS_TOKEN 未设置")
                return None
            return HomeAssistantAdapter(config)

        elif platform == Platform.EMAIL:
            from gateway.platforms.email import EmailAdapter, check_email_requirements
            if not check_email_requirements():
                logger.warning("Email: EMAIL_ADDRESS、EMAIL_PASSWORD、EMAIL_IMAP_HOST 或 EMAIL_SMTP_HOST 未设置")
                return None
            return EmailAdapter(config)

        elif platform == Platform.SMS:
            from gateway.platforms.sms import SmsAdapter, check_sms_requirements
            if not check_sms_requirements():
                logger.warning("SMS: aiohttp 未安装或 TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN 未设置")
                return None
            return SmsAdapter(config)

        elif platform == Platform.DINGTALK:
            from gateway.platforms.dingtalk import DingTalkAdapter, check_dingtalk_requirements
            if not check_dingtalk_requirements():
                logger.warning("DingTalk: dingtalk-stream 未安装或 DINGTALK_CLIENT_ID/SECRET 未设置")
                return None
            return DingTalkAdapter(config)

        elif platform == Platform.FEISHU:
            from gateway.platforms.feishu import FeishuAdapter, check_feishu_requirements
            if not check_feishu_requirements():
                logger.warning("Feishu: lark-oapi 未安装或 FEISHU_APP_ID/SECRET 未设置")
                return None
            return FeishuAdapter(config)

        elif platform == Platform.WECOM:
            from gateway.platforms.wecom import WeComAdapter, check_wecom_requirements
            if not check_wecom_requirements():
                logger.warning("WeCom: aiohttp 未安装或 WECOM_BOT_ID/SECRET 未设置")
                return None
            return WeComAdapter(config)

        elif platform == Platform.MATTERMOST:
            from gateway.platforms.mattermost import MattermostAdapter, check_mattermost_requirements
            if not check_mattermost_requirements():
                logger.warning("Mattermost: MATTERMOST_TOKEN 或 MATTERMOST_URL 未设置，或缺少 aiohttp")
                return None
            return MattermostAdapter(config)

        elif platform == Platform.MATRIX:
            from gateway.platforms.matrix import MatrixAdapter, check_matrix_requirements
            if not check_matrix_requirements():
                logger.warning("Matrix: matrix-nio 未安装或凭据未设置。运行: pip install 'matrix-nio[e2e]'")
                return None
            return MatrixAdapter(config)

        elif platform == Platform.API_SERVER:
            from gateway.platforms.api_server import APIServerAdapter, check_api_server_requirements
            if not check_api_server_requirements():
                logger.warning("API Server: aiohttp 未安装")
                return None
            return APIServerAdapter(config)

        elif platform == Platform.WEBHOOK:
            from gateway.platforms.webhook import WebhookAdapter, check_webhook_requirements
            if not check_webhook_requirements():
                logger.warning("Webhook: aiohttp 未安装")
                return None
            adapter = WebhookAdapter(config)
            adapter.gateway_runner = self  # 用于跨平台传递
            return adapter

        elif platform == Platform.BLUEBUBBLES:
            from gateway.platforms.bluebubbles import BlueBubblesAdapter, check_bluebubbles_requirements
            if not check_bluebubbles_requirements():
                logger.warning("BlueBubbles: 缺少 aiohttp/httpx 或 BLUEBUBBLES_SERVER_URL/BLUEBUBBLES_PASSWORD 未配置")
                return None
            return BlueBubblesAdapter(config)

        return None
    
    def _is_user_authorized(self, source: SessionSource) -> bool:
        """
        检查用户是否有权使用机器人。
        
        按顺序检查:
        1. 每个平台的全部允许标志 (例如 DISCORD_ALLOW_ALL_USERS=true)
        2. 环境变量白名单 (TELEGRAM_ALLOWED_USERS 等)
        3. 私信配对批准列表
        4. 全局全部允许 (GATEWAY_ALLOW_ALL_USERS=true)
        5. 默认: 拒绝
        """
        # Home Assistant 事件是由系统生成 (状态变更)，不是
        # 用户发起的消息。HASS_TOKEN 已经验证了连接，
        # 所以 HA 事件始终已授权。
        # Webhook 事件通过适配器中的 HMAC 签名验证进行认证 — 
        # 不适用于用户白名单。
        if source.platform in (Platform.HOMEASSISTANT, Platform.WEBHOOK):
            return True

        user_id = source.user_id
        if not user_id:
            return False

        platform_env_map = {
            Platform.TELEGRAM: "TELEGRAM_ALLOWED_USERS",
            Platform.DISCORD: "DISCORD_ALLOWED_USERS",
            Platform.WHATSAPP: "WHATSAPP_ALLOWED_USERS",
            Platform.SLACK: "SLACK_ALLOWED_USERS",
            Platform.SIGNAL: "SIGNAL_ALLOWED_USERS",
            Platform.EMAIL: "EMAIL_ALLOWED_USERS",
            Platform.SMS: "SMS_ALLOWED_USERS",
            Platform.MATTERMOST: "MATTERMOST_ALLOWED_USERS",
            Platform.MATRIX: "MATRIX_ALLOWED_USERS",
            Platform.DINGTALK: "DINGTALK_ALLOWED_USERS",
            Platform.FEISHU: "FEISHU_ALLOWED_USERS",
            Platform.WECOM: "WECOM_ALLOWED_USERS",
            Platform.BLUEBUBBLES: "BLUEBUBBLES_ALLOWED_USERS",
        }
        platform_allow_all_map = {
            Platform.TELEGRAM: "TELEGRAM_ALLOW_ALL_USERS",
            Platform.DISCORD: "DISCORD_ALLOW_ALL_USERS",
            Platform.WHATSAPP: "WHATSAPP_ALLOW_ALL_USERS",
            Platform.SLACK: "SLACK_ALLOW_ALL_USERS",
            Platform.SIGNAL: "SIGNAL_ALLOW_ALL_USERS",
            Platform.EMAIL: "EMAIL_ALLOW_ALL_USERS",
            Platform.SMS: "SMS_ALLOW_ALL_USERS",
            Platform.MATTERMOST: "MATTERMOST_ALLOW_ALL_USERS",
            Platform.MATRIX: "MATRIX_ALLOW_ALL_USERS",
            Platform.DINGTALK: "DINGTALK_ALLOW_ALL_USERS",
            Platform.FEISHU: "FEISHU_ALLOW_ALL_USERS",
            Platform.WECOM: "WECOM_ALLOW_ALL_USERS",
            Platform.BLUEBUBBLES: "BLUEBUBBLES_ALLOW_ALL_USERS",
        }

        # 每个平台的 allow-all 标志（例如 DISCORD_ALLOW_ALL_USERS=true）
        platform_allow_all_var = platform_allow_all_map.get(source.platform, "")
        if platform_allow_all_var and os.getenv(platform_allow_all_var, "").lower() in ("true", "1", "yes"):
            return True

        # 检查配对存储 (始终检查，无论白名单如何)
        platform_name = source.platform.value if source.platform else ""
        if self.pairing_store.is_approved(platform_name, user_id):
            return True

        # 检查平台特定和全局允许列表
        platform_allowlist = os.getenv(platform_env_map.get(source.platform, ""), "").strip()
        global_allowlist = os.getenv("GATEWAY_ALLOWED_USERS", "").strip()

        if not platform_allowlist and not global_allowlist:
            # 没有配置允许列表 -- 检查全局允许所有标志
            return os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in ("true", "1", "yes")

        # 检查用户是否在任何允许列表中
        allowed_ids = set()
        if platform_allowlist:
            allowed_ids.update(uid.strip() for uid in platform_allowlist.split(",") if uid.strip())
        if global_allowlist:
            allowed_ids.update(uid.strip() for uid in global_allowlist.split(",") if uid.strip())

        # "*" 在任何白名单中表示允许所有人 (与
        # SIGNAL_GROUP_ALLOWED_USERS 惯例一致)
        if "*" in allowed_ids:
            return True

        check_ids = {user_id}
        if "@" in user_id:
            check_ids.add(user_id.split("@")[0])

        # WhatsApp: 从桥接会话映射文件中解析电话↔LID 别名
        if source.platform == Platform.WHATSAPP:
            normalized_allowed_ids = set()
            for allowed_id in allowed_ids:
                normalized_allowed_ids.update(_expand_whatsapp_auth_aliases(allowed_id))
            if normalized_allowed_ids:
                allowed_ids = normalized_allowed_ids

            check_ids.update(_expand_whatsapp_auth_aliases(user_id))
            normalized_user_id = _normalize_whatsapp_identifier(user_id)
            if normalized_user_id:
                check_ids.add(normalized_user_id)

        return bool(check_ids & allowed_ids)

    def _get_unauthorized_dm_behavior(self, platform: Optional[Platform]) -> str:
        """返回平台对未授权私信的处理方式。"""
        config = getattr(self, "config", None)
        if config and hasattr(config, "get_unauthorized_dm_behavior"):
            return config.get_unauthorized_dm_behavior(platform)
        return "pair"
    
    async def _handle_message(self, event: MessageEvent) -> Optional[str]:
        """
        处理来自任何平台的消息。
        
        这是核心消息处理管道:
        1. 检查用户授权
        2. 检查命令 (/new、/reset 等)
        3. 检查正在运行的代理并在需要时中断
        4. 获取或创建会话
        5. 为代理构建上下文
        6. 运行代理会话
        7. 返回响应
        """
        source = event.source

        # 内部事件 (如后台进程完成通知) 是系统生成的，
        # 必须跳过用户授权检查。
        if getattr(event, "internal", False):
            pass
        elif not self._is_user_authorized(source):
            logger.warning("未授权用户: %s (%s) 在 %s 上", source.user_id, source.user_name, source.platform.value)
            # 在私信中: 提供配对码。在群组中: 静默忽略。
            if source.chat_type == "dm" and self._get_unauthorized_dm_behavior(source.platform) == "pair":
                platform_name = source.platform.value if source.platform else "unknown"
                # 对所有配对响应 (码或拒绝) 进行速率限制，
                # 以防止在多个私信快速到达时向用户重复发送消息。
                if self.pairing_store._is_rate_limited(platform_name, source.user_id):
                    return None
                code = self.pairing_store.generate_code(
                    platform_name, source.user_id, source.user_name or ""
                )
                if code:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            f"嗨~ 我还不认识你！\n\n"
                            f"这是你的配对码: `{code}`\n\n"
                            f"请让机器人所有者运行:\n"
                            f"`kclaw pairing approve {platform_name} {code}`"
                        )
                else:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            "现在请求过多~ "
                            "请稍后重试！"
                        )
                    # 记录速率限制，以便后续消息被静默忽略
                    self.pairing_store._record_rate_limit(platform_name, source.user_id)
            return None
        
        # 拦截对待处理 /update 提示的响应消息。
        # 更新进程 (分离) 写了 .update_prompt.json; 观察者
        # 将其转发给用户; 现在用户的回复通过
        # .update_response 返回，以便更新进程继续。
        _quick_key = self._session_key_for_source(source)
        _update_prompts = getattr(self, "_update_prompt_pending", {})
        if _update_prompts.get(_quick_key):
            raw = (event.text or "").strip()
            # 接受 /approve 和 /deny 作为是/否的简写
            cmd = event.get_command()
            if cmd in ("approve", "yes"):
                response_text = "y"
            elif cmd in ("deny", "no"):
                response_text = "n"
            else:
                response_text = raw
            if response_text:
                response_path = _kclaw_home / ".update_response"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text(response_text)
                    tmp.replace(response_path)
                except OSError as e:
                    logger.warning("写入更新响应失败: %s", e)
                    return f"✗ 向更新进程发送响应失败: {e}"
                _update_prompts.pop(_quick_key, None)
                label = response_text if len(response_text) <= 20 else response_text[:20] + "…"
                return f"✓ 已发送 `{label}` 到更新进程。"

        # 当此会话已有代理运行时进行优先级处理。
        # 默认行为是立即中断，以便用户文本/停止消息
        # 以最小延迟处理。
        #
        # 特殊情况: Telegram/照片突发通常作为多个几乎
        # 同时的更新到达。不要在这里中断纯照片的后续;
        # 让适配器级别的批处理/队列逻辑吸收它们。

        # 陈旧性驱逐: 检测挂起/崩溃处理器的泄漏锁。
        # 由于基于不活动的超时，活动任务可以运行数小时，
        # 因此仅靠墙上时钟年龄是不够的。仅当代理
        # 在不活动阈值之外处于*空闲*时才驱逐 (或者当代理
        # 对象没有活动跟踪器且墙上时钟年龄极端时)。
        _raw_stale_timeout = float(os.getenv("KCLAW_AGENT_TIMEOUT", 1800))
        _stale_ts = self._running_agents_ts.get(_quick_key, 0)
        if _quick_key in self._running_agents and _stale_ts:
            _stale_age = time.time() - _stale_ts
            _stale_agent = self._running_agents.get(_quick_key)
            # 永不驱逐待处理哨兵 — 它是在真实代理
            # 创建之前的异步设置阶段刚刚放置的。哨兵没有
            # get_activity_summary()，因此下面的空闲检查
            # 总是评估为 inf >= timeout 并立即驱逐它们，
            # 与设置路径竞争。
            _stale_idle = float("inf")  # assume idle if we can't check
            _stale_detail = ""
            if _stale_agent and hasattr(_stale_agent, "get_activity_summary"):
                try:
                    _sa = _stale_agent.get_activity_summary()
                    _stale_idle = _sa.get("seconds_since_activity", float("inf"))
                    _stale_detail = (
                        f" | last_activity={_sa.get('last_activity_desc', 'unknown')} "
                        f"({_stale_idle:.0f}s ago) "
                        f"| iteration={_sa.get('api_call_count', 0)}/{_sa.get('max_iterations', 0)}"
                    )
                except Exception:
                    pass
            # 如果代理空闲超过超时，或者墙上时钟年龄极端
            # (10倍超时或2小时，以较大者为准 — 捕获
            # 代理对象被垃圾回收的情况)，则驱逐。
            _wall_ttl = max(_raw_stale_timeout * 10, 7200) if _raw_stale_timeout > 0 else float("inf")
            _should_evict = (
                _stale_agent is not _AGENT_PENDING_SENTINEL
                and (
                    (_raw_stale_timeout > 0 and _stale_idle >= _raw_stale_timeout)
                    or _stale_age > _wall_ttl
                )
            )
            if _should_evict:
                logger.warning(
                    "驱逐会话 %s 的过时 _running_agents 条目 "
                    "(年龄: %.0f秒, 空闲: %.0f秒, 超时: %.0f秒)%s",
                    _quick_key[:30], _stale_age, _stale_idle,
                    _raw_stale_timeout, _stale_detail,
                )
                del self._running_agents[_quick_key]
                self._running_agents_ts.pop(_quick_key, None)

        if _quick_key in self._running_agents:
            if event.get_command() == "status":
                return await self._handle_status_command(event)

            # 解析命令一次以供下面的所有早期拦截检查使用。
            from kclaw_cli.commands import resolve_command as _resolve_cmd_inner
            _evt_cmd = event.get_command()
            _cmd_def_inner = _resolve_cmd_inner(_evt_cmd) if _evt_cmd else None

            # /stop 必须在代理运行时强制终止会话。
            # 当代理真正挂起时，软中断 (agent.interrupt()) 没有帮助 — 
            # 执行器线程被阻塞，从不检查 _interrupt_requested。
            # 强制清理 _running_agents 以便会话
            # 解锁，后续消息可以正常处理。
            if _cmd_def_inner and _cmd_def_inner.name == "stop":
                running_agent = self._running_agents.get(_quick_key)
                if running_agent and running_agent is not _AGENT_PENDING_SENTINEL:
                    running_agent.interrupt("停止请求")
                # 强制清理: 无论代理状态如何都移除会话锁
                adapter = self.adapters.get(source.platform)
                if adapter and hasattr(adapter, 'get_pending_message'):
                    adapter.get_pending_message(_quick_key)  # 消费并丢弃
                self._pending_messages.pop(_quick_key, None)
                if _quick_key in self._running_agents:
                    del self._running_agents[_quick_key]
                logger.info("会话 %s 强制停止 — 会话锁已释放", _quick_key[:20])
                return "⚡ 已强制停止。会话已解锁 — 你可以发送新消息。"

            # /reset 和 /new 必须绕过运行中代理守卫，以便它们
            # 实际作为命令分派，而不是被队列化为用户
            # 文本 (这会导致相同的损坏历史被反馈给代理 — #2170)。
            # 首先中断代理，然后清除适配器的待处理
            # 队列，以便陈旧的 "/reset" 文本
            # 在中断完成后不会作为用户消息重新处理。
            if _cmd_def_inner and _cmd_def_inner.name == "new":
                running_agent = self._running_agents.get(_quick_key)
                if running_agent and running_agent is not _AGENT_PENDING_SENTINEL:
                    running_agent.interrupt("会话重置请求")
                # 清除任何待处理的消息，以便旧文本不会重放
                adapter = self.adapters.get(source.platform)
                if adapter and hasattr(adapter, 'get_pending_message'):
                    adapter.get_pending_message(_quick_key)  # 消费并丢弃
                self._pending_messages.pop(_quick_key, None)
                # 清理运行中的代理条目，以便重置处理器
                # 不认为代理仍然处于活动状态。
                if _quick_key in self._running_agents:
                    del self._running_agents[_quick_key]
                return await self._handle_reset_command(event)

            # /queue <prompt> — 不中断地排队
            if event.get_command() in ("queue", "q"):
                queued_text = event.get_command_args().strip()
                if not queued_text:
                    return "用法: /queue <提示词>"
                adapter = self.adapters.get(source.platform)
                if adapter:
                    from gateway.platforms.base import MessageEvent as _ME, MessageType as _MT
                    queued_event = _ME(
                        text=queued_text,
                        message_type=_MT.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                    )
                    adapter._pending_messages[_quick_key] = queued_event
                return "已排队等待下一轮。"

            # /model 在代理运行时不能使用。
            if _cmd_def_inner and _cmd_def_inner.name == "model":
                return "代理正在运行 — 请等待或先 /stop，然后再切换模型。"

            # /approve 和 /deny 必须绕过运行中代理的中断路径。
            # 代理线程在 tools/approval.py 中的 threading.Event 内阻塞 — 
            # 发送中断无法解除阻塞。
            # 直接路由到审批处理器，以便事件被通知。
            if _cmd_def_inner and _cmd_def_inner.name in ("approve", "deny"):
                if _cmd_def_inner.name == "approve":
                    return await self._handle_approve_command(event)
                return await self._handle_deny_command(event)

            if event.message_type == MessageType.PHOTO:
                logger.debug("会话 %s 的优先级照片后续 — 无需中断地排队", _quick_key[:20])
                adapter = self.adapters.get(source.platform)
                if adapter:
                    # 重用适配器队列语义，以便照片突发能干净地合并。
                    if _quick_key in adapter._pending_messages:
                        existing = adapter._pending_messages[_quick_key]
                        if getattr(existing, "message_type", None) == MessageType.PHOTO:
                            existing.media_urls.extend(event.media_urls)
                            existing.media_types.extend(event.media_types)
                            if event.text:
                                existing.text = BasePlatformAdapter._merge_caption(existing.text, event.text)
                        else:
                            adapter._pending_messages[_quick_key] = event
                    else:
                        adapter._pending_messages[_quick_key] = event
                return None

            running_agent = self._running_agents.get(_quick_key)
            if running_agent is _AGENT_PENDING_SENTINEL:
                # 代理正在设置但尚未准备就绪。
                if event.get_command() == "stop":
                    # 强制清理哨兵，以便会话被解锁。
                    if _quick_key in self._running_agents:
                        del self._running_agents[_quick_key]
                    logger.info("会话 %s 强制停止 (待处理) — 哨兵已清除", _quick_key[:20])
                    return "⚡ 已强制停止。代理仍在启动中 — 会话已解锁。"
                # 将消息排队，以便在代理启动后被拾取。
                adapter = self.adapters.get(source.platform)
                if adapter:
                    adapter._pending_messages[_quick_key] = event
                return None
            logger.debug("会话 %s 的优先级中断", _quick_key[:20])
            running_agent.interrupt(event.text)
            if _quick_key in self._pending_messages:
                self._pending_messages[_quick_key] += "\n" + event.text
            else:
                self._pending_messages[_quick_key] = event.text
            return None

        # 检查命令
        command = event.get_command()
        
        # 为任何已识别的斜杠命令触发 command:* 钩子。
        # GATEWAY_KNOWN_COMMANDS 来自 kclaw_cli/commands.py 中的
        # 中心 COMMAND_REGISTRY — 此处没有硬编码集合需要维护。
        from kclaw_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command as _resolve_cmd
        if command and command in GATEWAY_KNOWN_COMMANDS:
            await self.hooks.emit(f"command:{command}", {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "command": command,
                "args": event.get_command_args().strip(),
            })

        # 解析别名到规范名称，以便分派仅检查规范名称。
        _cmd_def = _resolve_cmd(command) if command else None
        canonical = _cmd_def.name if _cmd_def else command

        if canonical == "new":
            return await self._handle_reset_command(event)
        
        if canonical == "help":
            return await self._handle_help_command(event)

        if canonical == "commands":
            return await self._handle_commands_command(event)
        
        if canonical == "profile":
            return await self._handle_profile_command(event)

        if canonical == "status":
            return await self._handle_status_command(event)
        
        if canonical == "stop":
            return await self._handle_stop_command(event)
        
        if canonical == "reasoning":
            return await self._handle_reasoning_command(event)

        if canonical == "verbose":
            return await self._handle_verbose_command(event)

        if canonical == "yolo":
            return await self._handle_yolo_command(event)

        if canonical == "model":
            return await self._handle_model_command(event)

        if canonical == "provider":
            return await self._handle_provider_command(event)
        
        if canonical == "personality":
            return await self._handle_personality_command(event)

        if canonical == "plan":
            try:
                from agent.skill_commands import build_plan_path, build_skill_invocation_message

                user_instruction = event.get_command_args().strip()
                plan_path = build_plan_path(user_instruction)
                event.text = build_skill_invocation_message(
                    "/plan",
                    user_instruction,
                    task_id=_quick_key,
                    runtime_note=(
                        "Save the markdown plan with write_file to this exact relative path "
                        f"inside the active workspace/backend cwd: {plan_path}"
                    ),
                )
                if not event.text:
                    return "无法加载绑定的 /plan 技能。"
                canonical = None
            except Exception as e:
                logger.exception("准备 /plan 命令失败")
                return f"进入计划模式失败: {e}"
        
        if canonical == "retry":
            return await self._handle_retry_command(event)
        
        if canonical == "undo":
            return await self._handle_undo_command(event)
        
        if canonical == "sethome":
            return await self._handle_set_home_command(event)

        if canonical == "compress":
            return await self._handle_compress_command(event)

        if canonical == "usage":
            return await self._handle_usage_command(event)

        if canonical == "insights":
            return await self._handle_insights_command(event)

        if canonical == "reload-mcp":
            return await self._handle_reload_mcp_command(event)

        if canonical == "approve":
            return await self._handle_approve_command(event)

        if canonical == "deny":
            return await self._handle_deny_command(event)

        if canonical == "update":
            return await self._handle_update_command(event)

        if canonical == "title":
            return await self._handle_title_command(event)

        if canonical == "resume":
            return await self._handle_resume_command(event)

        if canonical == "branch":
            return await self._handle_branch_command(event)

        if canonical == "rollback":
            return await self._handle_rollback_command(event)

        if canonical == "background":
            return await self._handle_background_command(event)

        if canonical == "btw":
            return await self._handle_btw_command(event)

        if canonical == "voice":
            return await self._handle_voice_command(event)

        # 用户定义的快速命令 (绕过代理循环，无 LLM 调用)
        if command:
            if isinstance(self.config, dict):
                quick_commands = self.config.get("quick_commands", {}) or {}
            else:
                quick_commands = getattr(self.config, "quick_commands", {}) or {}
            if not isinstance(quick_commands, dict):
                quick_commands = {}
            if command in quick_commands:
                qcmd = quick_commands[command]
                if qcmd.get("type") == "exec":
                    exec_cmd = qcmd.get("command", "")
                    if exec_cmd:
                        try:
                            proc = await asyncio.create_subprocess_shell(
                                exec_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                            output = (stdout or stderr).decode().strip()
                            return output if output else "命令无输出。"
                        except asyncio.TimeoutError:
                            return "快速命令超时 (30秒)。"
                        except Exception as e:
                            return f"快速命令错误: {e}"
                    else:
                        return f"快速命令 '/{command}' 未定义命令。"
                elif qcmd.get("type") == "alias":
                    target = qcmd.get("target", "").strip()
                    if target:
                        target = target if target.startswith("/") else f"/{target}"
                        target_command = target.lstrip("/")
                        user_args = event.get_command_args().strip()
                        event.text = f"{target} {user_args}".strip()
                        command = target_command
                        # 继续下面的正常命令分派
                    else:
                        return f"快速命令 '/{command}' 未定义目标。"
                else:
                    return f"快速命令 '/{command}' 不支持的类型 (支持: 'exec', 'alias')。"

        # 插件注册的斜杠命令
        if command:
            try:
                from kclaw_cli.plugins import get_plugin_command_handler
                # 将下划线规范化为连字符，以便 Telegram 带下划线的
                # 自动补全表单与用连字符注册的插件命令匹配。
                # 参见 kclaw_cli/commands.py:_build_telegram_menu。
                plugin_handler = get_plugin_command_handler(command.replace("_", "-"))
                if plugin_handler:
                    user_args = event.get_command_args().strip()
                    import asyncio as _aio
                    result = plugin_handler(user_args)
                    if _aio.iscoroutine(result):
                        result = await result
                    return str(result) if result else None
            except Exception as e:
                logger.debug("插件命令分派失败 (非致命): %s", e)

        # 技能斜杠命令: /skill-name 加载技能并发送给代理。
        # resolve_skill_command_key() 处理 Telegram 下划线/连字符
        # 往返，以便 Telegram 自动补全的 /claude_code 仍能解析
        # 到 claude-code 技能。
        if command:
            try:
                from agent.skill_commands import (
                    get_skill_commands,
                    build_skill_invocation_message,
                    resolve_skill_command_key,
                )
                skill_cmds = get_skill_commands()
                cmd_key = resolve_skill_command_key(command)
                if cmd_key is not None:
                    # 在执行前检查每个平台的禁用状态。
                    # get_skill_commands() 只在扫描时应用*全局*禁用
                    # 列表; 需要在此处检查每个平台的覆盖，因为缓存
                    # 在平台间是进程全局的。
                    _skill_name = skill_cmds[cmd_key].get("name", "")
                    _plat = source.platform.value if source.platform else None
                    if _plat and _skill_name:
                        from agent.skill_utils import get_disabled_skill_names as _get_plat_disabled
                        if _skill_name in _get_plat_disabled(platform=_plat):
                            return (
                                f"**{_skill_name}** 技能在 {_plat} 上已禁用。\n"
                                f"使用以下命令启用: `kclaw skills config`"
                            )
                    user_instruction = event.get_command_args().strip()
                    msg = build_skill_invocation_message(
                        cmd_key, user_instruction, task_id=_quick_key
                    )
                    if msg:
                        event.text = msg
                        # 继续使用技能内容进行正常消息处理
                else:
                    # 不是活动技能 — 检查是否是已知但禁用的或
                    # 未安装的技能，并提供可操作的指导。
                    _unavail_msg = _check_unavailable_skill(command)
                    if _unavail_msg:
                        return _unavail_msg
                    # 真正无法识别的 /command: 不是内置的，不是
                    # 插件的，不是技能的，不是已知非活动技能的。
                    # 警告用户而不是静默转发给 LLM
                    # 作为自由文本 (这会导致静默失败行为，
                    # 如模型编造 delegate_task 调用)。
                    # 在检查已知内置命令之前规范化
                    # 为连字符形式 (命令可能是上面
                    # 快速命令块中设置的别名目标，因此 _cmd_def 可能已过时)。
                    if command.replace("_", "-") not in GATEWAY_KNOWN_COMMANDS:
                        logger.warning(
                            "来自 %s 的无法识别的斜杠命令 /%s — "
                            "回复未知命令通知",
                            command,
                            source.platform.value if source.platform else "?",
                        )
                        return (
                            f"未知命令 `/{command}`。"
                            f"输入 /commands 查看可用命令，"
                            f"或重新发送时不带前导斜杠作为普通消息发送。"
                        )
            except Exception as e:
                logger.debug("技能命令检查失败 (非致命): %s", e)
        
        # 待处理的执行审批由上面的 /approve 和 /deny 命令处理。
        # 不进行裸文本匹配 — 普通会话中的 "yes" 不得触发
        # 危险命令的执行。

        # ── 在任何 await 之前声明此会话 ───────────────────────
        # 在这里和 _run_agent 注册真实 AIAgent 之间，
        # 有很多 await 点 (钩子、视觉增强、STT、
        # 会话卫生压缩)。没有这个哨兵，第二个在任何一个
        # 这些 yield 期间到达的消息会通过
        # "已在运行" 守卫并为同一会话启动重复代理 — 
        # 损坏会话记录。
        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL
        self._running_agents_ts[_quick_key] = time.time()

        try:
            return await self._handle_message_with_agent(event, source, _quick_key)
        finally:
            # 如果 _run_agent 用真实代理替换了哨兵并
            # 随后清理了它，这是一个空操作。如果提前退出
            # (异常、命令贯穿等) 哨兵必须
            # 不能停留，否则会话将被永久锁定。
            if self._running_agents.get(_quick_key) is _AGENT_PENDING_SENTINEL:
                del self._running_agents[_quick_key]
            self._running_agents_ts.pop(_quick_key, None)

    async def _handle_message_with_agent(self, event, source, _quick_key: str):
        """在 _running_agents 哨兵守卫下运行的内部处理器。"""
        _msg_start_time = time.time()
        _platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        _msg_preview = (event.text or "")[:80].replace("\n", " ")
        logger.info(
            "入站消息: platform=%s user=%s chat=%s msg=%r",
            _platform_name, source.user_name or source.user_id or "unknown",
            source.chat_id or "unknown", _msg_preview,
        )

        # 获取或创建会话
        session_entry = self.session_store.get_or_create_session(source)
        session_key = session_entry.session_key
        
        # 为新会话或自动重置会话触发 session:start
        _is_new_session = (
            session_entry.created_at == session_entry.updated_at
            or getattr(session_entry, "was_auto_reset", False)
        )
        if _is_new_session:
            await self.hooks.emit("session:start", {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "session_id": session_entry.session_id,
                "session_key": session_key,
            })
        
        # 构建会话上下文
        context = build_session_context(source, self.config, session_entry)
        
        # 为工具设置环境变量
        self._set_session_env(context)
        
        # 从配置读取 privacy.redact_pii (每条消息重新读取)
        _redact_pii = False
        try:
            import yaml as _pii_yaml
            with open(_config_path, encoding="utf-8") as _pf:
                _pcfg = _pii_yaml.safe_load(_pf) or {}
            _redact_pii = bool((_pcfg.get("privacy") or {}).get("redact_pii", False))
        except Exception:
            pass

        # 构建要注入的上下文提示词
        context_prompt = build_session_context_prompt(context, redact_pii=_redact_pii)
        
        # 如果上一个会话过期并被自动重置，在前面添加一条通知，
        # 以便代理知道这是一个新的对话 (而不是故意的 /reset)。
        if getattr(session_entry, 'was_auto_reset', False):
            reset_reason = getattr(session_entry, 'auto_reset_reason', None) or 'idle'
            if reset_reason == "daily":
                context_note = "[系统提示: 用户的会话已被每日计划自动重置。这是一次新的对话，没有先前的上下文。]"
            else:
                context_note = "[系统提示: 用户的先前会话因不活动而过期。这是一次新的对话，没有先前的上下文。]"
            context_prompt = context_note + "\n\n" + context_prompt

            # 发送面向用户的消息解释重置，除非:
            # - 通知在配置中被禁用
            # - 平台被排除 (例如 api_server, webhook)
            # - 过期会话没有活动 (没有内容被清除)
            try:
                policy = self.session_store.config.get_reset_policy(
                    platform=source.platform,
                    session_type=getattr(source, 'chat_type', 'dm'),
                )
                platform_name = source.platform.value if source.platform else ""
                had_activity = getattr(session_entry, 'reset_had_activity', False)
                should_notify = (
                    policy.notify
                    and had_activity
                    and platform_name not in policy.notify_exclude_platforms
                )
                if should_notify:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        if reset_reason == "daily":
                            reason_text = f"每日计划在 {policy.at_hour}:00"
                        else:
                            hours = policy.idle_minutes // 60
                            mins = policy.idle_minutes % 60
                            duration = f"{hours}小时" if not mins else f"{hours}小时 {mins}分钟" if hours else f"{mins}分钟"
                            reason_text = f"不活动 {duration}"
                        notice = (
                            f"◐ 会话已自动重置 ({reason_text})。"
                            f"对话历史已清除。\n"
                            f"使用 /resume 浏览并恢复先前的会话。\n"
                            f"在 config.yaml 的 session_reset 下调整重置时间。"
                        )
                        try:
                            session_info = self._format_session_info()
                            if session_info:
                                notice = f"{notice}\n\n{session_info}"
                        except Exception:
                            pass
                        await adapter.send(
                            source.chat_id, notice,
                            metadata=getattr(event, 'metadata', None),
                        )
            except Exception as e:
                logger.debug("自动重置通知失败 (非致命): %s", e)

            session_entry.was_auto_reset = False
            session_entry.auto_reset_reason = None

        # 为私信话题绑定自动加载技能 (例如 Telegram 私信话题)
        # 仅在新会话时注入 — 对于正在进行的对话，技能内容
        # 已从第一条消息包含在对话历史中。
        if _is_new_session and getattr(event, "auto_skill", None):
            try:
                from agent.skill_commands import _load_skill_payload, _build_skill_message
                _skill_name = event.auto_skill
                _loaded = _load_skill_payload(_skill_name, task_id=_quick_key)
                if _loaded:
                    _loaded_skill, _skill_dir, _display_name = _loaded
                    _activation_note = (
                        f'[系统: 此对话在具有 "{_display_name}" 技能的话题中，'
                        f"已自动加载。在本次会话期间请遵循其说明。]"
                    )
                    _skill_msg = _build_skill_message(
                        _loaded_skill, _skill_dir, _activation_note,
                        user_instruction=event.text,
                    )
                    if _skill_msg:
                        event.text = _skill_msg
                        logger.info(
                            "[网关] 为私信话题会话 %s 自动加载技能 '%s'",
                            _skill_name, session_key,
                        )
                else:
                    logger.warning(
                        "[网关] 在可用技能中未找到私信话题技能 '%s'",
                        _skill_name,
                    )
            except Exception as e:
                logger.warning("[网关] 自动加载话题技能 '%s' 失败: %s", event.auto_skill, e)

        # 从会话记录加载对话历史
        history = self.session_store.load_transcript(session_entry.session_id)
        
        # -----------------------------------------------------------------
        # 会话卫生：自动压缩病理级庞大的对话记录
        #
        # 长期运行的网关会话可能积累足够多的历史记录，导致每条新消息
        # 都会重新加载一个过大的对话记录，从而引发重复的截断/上下文
        # 失败。在代理启动之前及早检测并主动压缩。(#628)
        #
        # Token 来源优先级：
        # 1. 上轮 API 实际报告的 prompt_tokens
        #    (存储在 session_entry.last_prompt_tokens)
        # 2. 粗略的基于字符的估算 (str(msg)//4)。对于代码/JSON
        #    密集型会话会高估 30-50%，但这只是意味着卫生机制会
        #    提前触发 —— 安全且无害。
        # -----------------------------------------------------------------
        if history and len(history) >= 4:
            from agent.model_metadata import (
                estimate_messages_tokens_rough,
                get_model_context_length,
            )

            # 从 config.yaml 读取模型和压缩配置。
            # 注意：卫生阈值特意设置得比代理自身的压缩器更高
            # (0.85 对比 0.50)。卫生机制是为轮次之间增长过大的会话
            # 设置的安全网 —— 它在代理运行前触发以防止 API 失败。
            # 代理自身的压缩器在其工具循环中使用精确的实时 token 计数
            # 处理正常的上下文管理。将卫生阈值设为 0.50 导致在
            # 长期网关会话中每轮都过早触发压缩。
            _hyg_model = "anthropic/claude-sonnet-4.6"
            _hyg_threshold_pct = 0.85
            _hyg_compression_enabled = True
            _hyg_config_context_length = None
            _hyg_provider = None
            _hyg_base_url = None
            _hyg_api_key = None
            try:
                _hyg_cfg_path = _kclaw_home / "config.yaml"
                if _hyg_cfg_path.exists():
                    import yaml as _hyg_yaml
                    with open(_hyg_cfg_path, encoding="utf-8") as _hyg_f:
                        _hyg_data = _hyg_yaml.safe_load(_hyg_f) or {}

                    # 解析模型名称（与 run_sync 相同的逻辑）
                    _model_cfg = _hyg_data.get("model", {})
                    if isinstance(_model_cfg, str):
                        _hyg_model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        _hyg_model = _model_cfg.get("default") or _model_cfg.get("model") or _hyg_model
                        # 从模型配置读取显式的 context_length 覆盖
                        # (与 run_agent.py lines 995-1005 相同)
                        _raw_ctx = _model_cfg.get("context_length")
                        if _raw_ctx is not None:
                            try:
                                _hyg_config_context_length = int(_raw_ctx)
                            except (TypeError, ValueError):
                                pass
                        # 读取提供商以进行准确的上下文检测
                        _hyg_provider = _model_cfg.get("provider") or None
                        _hyg_base_url = _model_cfg.get("base_url") or None

                    # 读取压缩设置 — 仅使用 enabled 标志。
                    # 阈值有意与代理的 compression.threshold 分离
                    # (卫生运行时更高)。
                    _comp_cfg = _hyg_data.get("compression", {})
                    if isinstance(_comp_cfg, dict):
                        _hyg_compression_enabled = str(
                            _comp_cfg.get("enabled", True)
                        ).lower() in ("true", "1", "yes")

                # 如果配置中没有提供商/base_url，则从运行时解析
                if not _hyg_provider or not _hyg_base_url:
                    try:
                        _hyg_runtime = _resolve_runtime_agent_kwargs()
                        _hyg_provider = _hyg_provider or _hyg_runtime.get("provider")
                        _hyg_base_url = _hyg_base_url or _hyg_runtime.get("base_url")
                        _hyg_api_key = _hyg_runtime.get("api_key")
                    except Exception:
                        pass

                # 检查 custom_providers 每模型 context_length
                # (与 run_agent.py lines 1171-1189 相同的回退)。
                # 必须在运行时解析后运行，以便设置 _hyg_base_url。
                if _hyg_config_context_length is None and _hyg_base_url:
                    try:
                        _hyg_custom_providers = _hyg_data.get("custom_providers")
                        if isinstance(_hyg_custom_providers, list):
                            for _cp in _hyg_custom_providers:
                                if not isinstance(_cp, dict):
                                    continue
                                _cp_url = (_cp.get("base_url") or "").rstrip("/")
                                if _cp_url and _cp_url == _hyg_base_url.rstrip("/"):
                                    _cp_models = _cp.get("models", {})
                                    if isinstance(_cp_models, dict):
                                        _cp_model_cfg = _cp_models.get(_hyg_model, {})
                                        if isinstance(_cp_model_cfg, dict):
                                            _cp_ctx = _cp_model_cfg.get("context_length")
                                            if _cp_ctx is not None:
                                                _hyg_config_context_length = int(_cp_ctx)
                                    break
                    except (TypeError, ValueError):
                        pass
            except Exception:
                pass

            if _hyg_compression_enabled:
                _hyg_context_length = get_model_context_length(
                    _hyg_model,
                    base_url=_hyg_base_url or "",
                    api_key=_hyg_api_key or "",
                    config_context_length=_hyg_config_context_length,
                    provider=_hyg_provider or "",
                )
                _compress_token_threshold = int(
                    _hyg_context_length * _hyg_threshold_pct
                )
                _warn_token_threshold = int(_hyg_context_length * 0.95)

                _msg_count = len(history)

                # 优先使用上轮实际 API 报告的 tokens
                # (存储在会话条目中)，而非粗略的基于字符的估算。
                _stored_tokens = session_entry.last_prompt_tokens
                if _stored_tokens > 0:
                    _approx_tokens = _stored_tokens
                    _token_source = "actual"
                else:
                    _approx_tokens = estimate_messages_tokens_rough(history)
                    _token_source = "estimated"
                    # 注意：粗略估算对于代码/JSON 密集型会话会高估 30-50%，
                    # 但这只是意味着卫生会稍微提前触发 — 这是安全且无害的。
                    # 85% 阈值已经提供了充足的缓冲空间（代理自身的压缩器在 50% 运行）。
                    # 之前尝试的 1.4 倍系数试图通过膨胀阈值来补偿，
                    # 但 85% * 1.4 = 119% 的上下文 — 超过了模型的限制，
                    # 并阻止了卫生对 ~200K 模型（GLM-5）触发。

                # 硬安全阀：如果消息数量极端则强制压缩，
                # 无论 token 估算如何。这打破了死亡螺旋，
                # 其中 API 断开阻止 token 数据收集，
                # 这阻止了压缩，导致更多断开。400 条消息远超
                # 正常会话，但会在失控增长变得不可恢复之前捕获它。
                # (#2153)
                _HARD_MSG_LIMIT = 400
                _needs_compress = (
                    _approx_tokens >= _compress_token_threshold
                    or _msg_count >= _HARD_MSG_LIMIT
                )

                if _needs_compress:
                    logger.info(
                        "会话卫生: %s 条消息，~%s tokens (%s) — 自动压缩 "
                        "(阈值: %s%% of %s = %s tokens)",
                        _msg_count, f"{_approx_tokens:,}", _token_source,
                        int(_hyg_threshold_pct * 100),
                        f"{_hyg_context_length:,}",
                        f"{_compress_token_threshold:,}",
                    )

                    _hyg_meta = {"thread_id": source.thread_id} if source.thread_id else None

                    try:
                        from run_agent import AIAgent

                        _hyg_runtime = _resolve_runtime_agent_kwargs()
                        if _hyg_runtime.get("api_key"):
                            _hyg_msgs = [
                                {"role": m.get("role"), "content": m.get("content")}
                                for m in history
                                if m.get("role") in ("user", "assistant")
                                and m.get("content")
                            ]

                            if len(_hyg_msgs) >= 4:
                                _hyg_agent = AIAgent(
                                    **_hyg_runtime,
                                    model=_hyg_model,
                                    max_iterations=4,
                                    quiet_mode=True,
                                    enabled_toolsets=["memory"],
                                    session_id=session_entry.session_id,
                                )
                                _hyg_agent._print_fn = lambda *a, **kw: None

                                loop = asyncio.get_event_loop()
                                _compressed, _ = await loop.run_in_executor(
                                    None,
                                    lambda: _hyg_agent._compress_context(
                                        _hyg_msgs, "",
                                        approx_tokens=_approx_tokens,
                                    ),
                                )

                                # _compress_context 结束旧会话并创建
                                # 新的 session_id。将压缩后的消息写入
                                # 新会话，这样旧记录保持完整
                                # 且可通过 session_search 搜索。
                                _hyg_new_sid = _hyg_agent.session_id
                                if _hyg_new_sid != session_entry.session_id:
                                    session_entry.session_id = _hyg_new_sid
                                    self.session_store._save()

                                self.session_store.rewrite_transcript(
                                    session_entry.session_id, _compressed
                                )
                                # 重置存储的令牌计数 — 转录本已被重写
                                session_entry.last_prompt_tokens = 0
                                history = _compressed
                                _new_count = len(_compressed)
                                _new_tokens = estimate_messages_tokens_rough(
                                    _compressed
                                )

                                logger.info(
                                    "会话卫生: 压缩 %s → %s 条消息，"
                                    "~%s → ~%s tokens",
                                    _msg_count, _new_count,
                                    f"{_approx_tokens:,}", f"{_new_tokens:,}",
                                )

                                if _new_tokens >= _warn_token_threshold:
                                    logger.warning(
                                        "会话卫生: 压缩后仍有 ~%s tokens",
                                        f"{_new_tokens:,}",
                                    )

                    except Exception as e:
                        logger.warning(
                            "会话卫生自动压缩失败: %s", e
                        )

        # 首次消息引导 — 仅在第一次交互时
        if not history and not self.session_store.has_any_sessions():
            context_prompt += (
                "\n\n[系统提示: 这是用户的第一条消息。"
                "简要介绍一下自己，并提及 /help 可查看可用命令。"
                "介绍要简洁 — 最多一两句话。]"
            )
        
        # 如果没有为此平台设置主频道则一次性提示
        # 跳过 webhooks - 它们直接投递到配置的目标 (github_comment 等)
        if not history and source.platform and source.platform != Platform.LOCAL and source.platform != Platform.WEBHOOK:
            platform_name = source.platform.value
            env_key = f"{platform_name.upper()}_HOME_CHANNEL"
            if not os.getenv(env_key):
                adapter = self.adapters.get(source.platform)
                if adapter:
                    await adapter.send(
                        source.chat_id,
                        f"📬 尚未为 {platform_name.title()} 设置主频道。"
                        f"主频道是 KClaw 投递 cron 作业结果"
                        f"和跨平台消息的地方。\n\n"
                        f"输入 /sethome 将此聊天室设为主频道，"
                        f"或忽略跳过。"
                    )
        
        # -----------------------------------------------------------------
        # 语音频道感知 — 将当前语音频道状态注入上下文，
        # 以便代理知道谁在频道中谁在说话，无需单独的工具调用。
        # -----------------------------------------------------------------
        if source.platform == Platform.DISCORD:
            adapter = self.adapters.get(Platform.DISCORD)
            guild_id = self._get_guild_id(event)
            if guild_id and adapter and hasattr(adapter, "get_voice_channel_context"):
                vc_context = adapter.get_voice_channel_context(guild_id)
                if vc_context:
                    context_prompt += f"\n\n{vc_context}"

        # -----------------------------------------------------------------
        # 自动分析用户发送的图片
        #
        # 如果用户附加了图片，我们急切地运行视觉工具，
        # 以便对话模型始终接收文本描述。还包括本地文件路径，
        # 以便模型稍后可以通过 vision_analyze 更精准地重新检查图片。
        #
        # 我们仅过滤图片路径（按 media_type），
        # 以便非图片附件（文档、音频等）在同一条消息中出现时
        # 不会发送到视觉工具。
        # -----------------------------------------------------------------
        message_text = event.text or ""

        # -----------------------------------------------------------------
        # 共享线程会话的发件人归属。
        #
        # 当多个用户共享单个线程会话（线程的默认设置）时，
        # 在每条消息前添加 [发送者名称]，以便代理区分参与者。
        # 跳过私信（本质上是单一用户）和
        # 当明确启用每用户线程隔离时。
        # -----------------------------------------------------------------
        _is_shared_thread = (
            source.chat_type != "dm"
            and source.thread_id
            and not getattr(self.config, "thread_sessions_per_user", False)
        )
        if _is_shared_thread and source.user_name:
            message_text = f"[{source.user_name}] {message_text}"

        if event.media_urls:
            image_paths = []
            for i, path in enumerate(event.media_urls):
                # 检查 media_types 是否可用；否则从消息类型推断
                # 检查 media_types 是否可用；否则从消息类型推断
                mtype = event.media_types[i] if i < len(event.media_types) else ""
                is_image = (
                    mtype.startswith("image/")
                    or event.message_type == MessageType.PHOTO
                )
                if is_image:
                    image_paths.append(path)
            if image_paths:
                message_text = await self._enrich_message_with_vision(
                    message_text, image_paths
                )
        
        # -----------------------------------------------------------------
        # 自动转录用户发送的语音/音频消息
        # -----------------------------------------------------------------
        if event.media_urls:
            audio_paths = []
            for i, path in enumerate(event.media_urls):
                mtype = event.media_types[i] if i < len(event.media_types) else ""
                is_audio = (
                    mtype.startswith("audio/")
                    or event.message_type in (MessageType.VOICE, MessageType.AUDIO)
                )
                if is_audio:
                    audio_paths.append(path)
            if audio_paths:
                message_text = await self._enrich_message_with_transcription(
                    message_text, audio_paths
                )
                # 如果 STT 失败，直接发送消息给用户，
                # 以便他们知道语音未配置 — 不要依赖代理来
                # 清楚地转发错误。
                _stt_fail_markers = (
                    "没有 STT 提供商",
                    "STT 已禁用",
                    "无法收听",
                    "VOICE_TOOLS_OPENAI_KEY",
                )
                if any(m in message_text for m in _stt_fail_markers):
                    _stt_adapter = self.adapters.get(source.platform)
                    _stt_meta = {"thread_id": source.thread_id} if source.thread_id else None
                    if _stt_adapter:
                        try:
                            _stt_msg = (
                                "🎤 我收到了你的语音消息，但无法转录 — "
                                "没有配置语音转文本提供商。\n\n"
                                "要启用语音：安装 faster-whisper "
                                "(`pip install faster-whisper` 在 KClaw venv 中) "
                                "并在 config.yaml 中设置 `stt.enabled: true`，"
                                "然后 /restart 网关。"
                            )
                            # 如果安装了设置技能则指向设置说明
                            if self._has_setup_skill():
                                _stt_msg += "\n\n要查看完整设置说明，请输入: `/skill kclaw-setup`"
                            await _stt_adapter.send(
                                source.chat_id, _stt_msg,
                                metadata=_stt_meta,
                            )
                        except Exception:
                            pass

        # -----------------------------------------------------------------
        # 为代理富化文档消息的上下文注释
        # -----------------------------------------------------------------
        if event.media_urls and event.message_type == MessageType.DOCUMENT:
            import mimetypes as _mimetypes
            _TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
            for i, path in enumerate(event.media_urls):
                mtype = event.media_types[i] if i < len(event.media_types) else ""
                # 当 MIME 类型不可靠时回退到基于扩展名的检测。
                if mtype in ("", "application/octet-stream"):
                    import os as _os2
                    _ext = _os2.path.splitext(path)[1].lower()
                    if _ext in _TEXT_EXTENSIONS:
                        mtype = "text/plain"
                    else:
                        guessed, _ = _mimetypes.guess_type(path)
                        if guessed:
                            mtype = guessed
                if not mtype.startswith(("application/", "text/")):
                    continue
                # 通过剥离 doc_{uuid12}_ 前缀来提取显示文件名
                import os as _os
                basename = _os.path.basename(path)
                # 格式: doc_<12hex>_<原始文件名>
                parts = basename.split("_", 2)
                display_name = parts[2] if len(parts) >= 3 else basename
                # 清理以防止通过文件名进行提示词注入
                import re as _re
                display_name = _re.sub(r'[^\w.\- ]', '_', display_name)

                if mtype.startswith("text/"):
                    context_note = (
                        f"[用户发送了一个文本文档: '{display_name}'。"
                        f"其内容已包含在下方。"
                        f"文件也保存在: {path}]"
                    )
                else:
                    context_note = (
                        f"[用户发送了一个文档: '{display_name}'。"
                        f"文件保存在: {path}。"
                        f"询问用户他们想用此文件做什么。]"
                    )
                message_text = f"{context_note}\n\n{message_text}"

        # -----------------------------------------------------------------
        # 当用户回复不在历史记录中的消息时注入回复上下文。
        # Telegram（和其他平台）让用户回复特定消息，
        # 但如果引用消息来自之前的会话、cron 投递或后台任务，
        # 代理没有关于引用内容的上下文。
        # 预置引用文本以便代理理解。 (#1594)
        # -----------------------------------------------------------------
        if getattr(event, 'reply_to_text', None) and event.reply_to_message_id:
            reply_snippet = event.reply_to_text[:500]
            found_in_history = any(
                reply_snippet[:200] in (msg.get("content") or "")
                for msg in history
                if msg.get("role") in ("assistant", "user", "tool")
            )
            if not found_in_history:
                message_text = f'[回复: "{reply_snippet}"]\n\n{message_text}'

        try:
            # 触发 agent:start 钩子
            hook_ctx = {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "session_id": session_entry.session_id,
                "message": message_text[:500],
            }
            await self.hooks.emit("agent:start", hook_ctx)

            # 展开 @ 上下文引用（@file:、@folder:、@diff 等）
            if "@" in message_text:
                try:
                    from agent.context_references import preprocess_context_references_async
                    from agent.model_metadata import get_model_context_length
                    _msg_cwd = os.environ.get("MESSAGING_CWD", os.path.expanduser("~"))
                    _msg_ctx_len = get_model_context_length(
                        self._model, base_url=self._base_url or "")
                    _ctx_result = await preprocess_context_references_async(
                        message_text, cwd=_msg_cwd,
                        context_length=_msg_ctx_len, allowed_root=_msg_cwd)
                    if _ctx_result.blocked:
                        _adapter = self.adapters.get(source.platform)
                        if _adapter:
                            await _adapter.send(
                                source.chat_id,
                                "\n".join(_ctx_result.warnings) or "上下文注入被拒绝。"
                            )
                        return
                    if _ctx_result.expanded:
                        message_text = _ctx_result.message
                except Exception as exc:
                    logger.debug("@ 上下文引用展开失败: %s", exc)

            # 运行代理
            agent_result = await self._run_agent(
                message=message_text,
                context_prompt=context_prompt,
                history=history,
                source=source,
                session_id=session_entry.session_id,
                session_key=session_key,
                event_message_id=event.message_id,
            )

            # 停止持久打字指示器，因为代理已完成
            try:
                _typing_adapter = self.adapters.get(source.platform)
                if _typing_adapter and hasattr(_typing_adapter, "stop_typing"):
                    await _typing_adapter.stop_typing(source.chat_id)
            except Exception:
                pass

            response = agent_result.get("final_response") or ""
            agent_messages = agent_result.get("messages", [])
            _response_time, _api_calls, _resp_len = time.time() - _msg_start_time, agent_result.get("api_calls", 0), len(response)
            logger.info(
                "响应就绪: platform=%s chat=%s time=%.1fs api_calls=%d response=%d 字符",
                _platform_name, source.chat_id or "unknown",
                _response_time, _api_calls, _resp_len,
            )

            # 当代理静默失败时显示错误详情（final_response=None）
            if not response and agent_result.get("failed"):
                error_detail = agent_result.get("error", "未知错误")
                error_str = str(error_detail).lower()

                # 检测上下文溢出失败并给出具体指导。
                # 当会话较大时，来自 Anthropic 的通用 400 "Error" 是
                # 最常见的原因（#1630）。
                _is_ctx_fail = any(p in error_str for p in (
                    "context", "token", "too large", "too long",
                    "exceed", "payload",
                )) or (
                    "400" in error_str
                    and len(history) > 50
                )

                if _is_ctx_fail:
                    response = (
                        "⚠️ 会话上下文超出了模型的限制范围。\n"
                        "使用 /compact 压缩对话，或 "
                        "/reset 重新开始。"
                    )
                else:
                    response = (
                        f"请求失败: {str(error_detail)[:300]}\n"
                        "请重试或使用 /reset 重新开始会话。"
                    )

            # 如果代理的 session_id 在压缩期间发生变化，
            # 更新 session_entry 以便下面的转录写入正确的会话。
            if agent_result.get("session_id") and agent_result["session_id"] != session_entry.session_id:
                session_entry.session_id = agent_result["session_id"]

            # 在显示时添加 reasoning/thinking
            if getattr(self, "_show_reasoning", False) and response:
                last_reasoning = agent_result.get("last_reasoning")
                if last_reasoning:
                    # 折叠长推理以保持消息可读
                    lines = last_reasoning.strip().splitlines()
                    if len(lines) > 15:
                        display_reasoning = "\n".join(lines[:15])
                        display_reasoning += f"\n_... ({len(lines) - 15} more lines)_"
                    else:
                        display_reasoning = last_reasoning.strip()
                    response = f"💭 **Reasoning:**\n```\n{display_reasoning}\n```\n\n{response}"

            # 触发 agent:end 钩子
            await self.hooks.emit("agent:end", {
                **hook_ctx,
                "response": (response or "")[:500],
            })
            
            # 检查待处理的进程观察者（后台进程的 check_interval）
            try:
                from tools.process_registry import process_registry
                while process_registry.pending_watchers:
                    watcher = process_registry.pending_watchers.pop(0)
                    asyncio.create_task(self._run_process_watcher(watcher))
            except Exception as e:
                logger.error("进程观察者设置错误: %s", e)

            # 注意：危险命令审批现在由 tools/approval.py 中的
            # 阻塞式 gateway 审批机制内联处理。代理
            # 线程会阻塞，直到用户响应 /approve 或 /deny，
            # 因此当我们到达这里时审批已经解决。
            # 旧的循环后 pop_pending + approval_hint 代码被删除，
            # 转而使用镜像 CLI 同步 input() 的阻塞方法。
            
            # 保存完整的对话到记录中，包括工具调用。
            # 保存完整的对话到记录中，包括工具调用。
            # 这保留了完整的代理循环（工具调用、工具结果、
            # 中间推理），以便可以恢复具有完整上下文的会话，
            # 记录对调试和训练数据有用。
            #
            # 重要提示：当代理在产生任何响应之前失败时
            # （例如上下文溢出 400），不要保存用户的消息。
            # 保存它会使会话更大，导致下一次尝试出现
            # 相同的失败 — 形成无限循环。 (#1630)
            agent_failed_early = (
                agent_result.get("failed")
                and not agent_result.get("final_response")
            )
            if agent_failed_early:
                logger.info(
                    "跳过失败请求的记录持久化 "
                    "在会话 %s 中以防止会话增长循环。",
                    session_entry.session_id,
                )

            ts = datetime.now().isoformat()
            
            # 如果是新会话（无历史），将完整的工具定义
            # 写入第一条记录，以便记录是自描述的
            # — 与 API 请求中发送的 tools=[...] 相同的字典列表。
            if agent_failed_early:
                pass  # 跳过所有记录写入 — 不要增长损坏的会话
            elif not history:
                tool_defs = agent_result.get("tools", [])
                self.session_store.append_to_transcript(
                    session_entry.session_id,
                    {
                        "role": "session_meta",
                        "tools": tool_defs or [],
                        "model": _resolve_gateway_model(),
                        "platform": source.platform.value if source.platform else "",
                        "timestamp": ts,
                    }
                )
            
            # 只查找本轮的新消息（跳过我们加载的历史）。
            # 使用实际传递给代理的过滤后的历史长度（history_offset），
            # 而不是 len(history)，后者包括在代理看到之前被剥离的 session_meta 条目。
            if not agent_failed_early:
                history_len = agent_result.get("history_offset", len(history))
                new_messages = agent_messages[history_len:] if len(agent_messages) > history_len else []
                
                # 如果没有找到新消息（边缘情况），回退到简单的 user/assistant
                if not new_messages:
                    self.session_store.append_to_transcript(
                        session_entry.session_id,
                        {"role": "user", "content": message_text, "timestamp": ts}
                    )
                    if response:
                        self.session_store.append_to_transcript(
                            session_entry.session_id,
                            {"role": "assistant", "content": response, "timestamp": ts}
                        )
                else:
                    # 代理已经通过 _flush_messages_to_session_db() 将这些消息持久化到 SQLite，
                    # 所以在这里跳过数据库写入以防止重复写入 bug (#860)。
                    # 我们仍然写入 JSONL 以保持向后兼容性并作为备份。
                    agent_persisted = self._session_db is not None
                    for msg in new_messages:
                        # 跳过系统消息（每次运行时都会重建）
                        if msg.get("role") == "system":
                            continue
                        # 为每条消息添加时间戳以便调试
                        entry = {**msg, "timestamp": ts}
                        self.session_store.append_to_transcript(
                            session_entry.session_id, entry,
                            skip_db=agent_persisted,
                        )
            
            # 令牌计数和模型现在由代理直接持久化。
            # 只在这里保留 last_prompt_tokens 用于上下文窗口跟踪和
            # 压缩决策。
            self.session_store.update_session(
                session_entry.session_key,
                last_prompt_tokens=agent_result.get("last_prompt_tokens", 0),
            )

            # 自动语音回复：在文本响应之前发送 TTS 音频
            _already_sent = bool(agent_result.get("already_sent"))
            if self._should_send_voice_reply(event, response, agent_messages, already_sent=_already_sent):
                await self._send_voice_reply(event, response)

            # 如果流式传输已经传递了响应，在返回 None 之前提取并
            # 传递任何 MEDIA: 文件。流式传输发送包含 MEDIA: 标签的原始文本块 — 
            # 当 already_sent 为 True 时，会跳过 _process_message_background 中的正常
            # 后处理，因此没有这个媒体文件将永远不会被传递。
            if agent_result.get("already_sent"):
                if response:
                    _media_adapter = self.adapters.get(source.platform)
                    if _media_adapter:
                        await self._deliver_media_from_response(
                            response, event, _media_adapter,
                        )
                return None

            return response
            
        except Exception as e:
            # 错误时也停止打字指示器
            try:
                _err_adapter = self.adapters.get(source.platform)
                if _err_adapter and hasattr(_err_adapter, "stop_typing"):
                    await _err_adapter.stop_typing(source.chat_id)
            except Exception:
                pass
            logger.exception("会话 %s 中的代理错误", session_key)
            error_type = type(e).__name__
            error_detail = str(e)[:300] if str(e) else "无详细信息"
            status_hint = ""
            status_code = getattr(e, "status_code", None)
            _hist_len = len(history) if 'history' in locals() else 0
            if status_code == 401:
                status_hint = " 请检查您的 API 密钥或运行 `kclaw /login` 刷新 OAuth 凭据。"
            elif status_code == 429:
                # 检查这是计划使用限制（按计划重置）还是临时速率限制
                _err_body = getattr(e, "response", None)
                _err_json = {}
                try:
                    if _err_body is not None:
                        _err_json = _err_body.json().get("error", {})
                except Exception:
                    pass
                if _err_json.get("type") == "usage_limit_reached":
                    _resets_in = _err_json.get("resets_in_seconds")
                    if _resets_in and _resets_in > 0:
                        import math
                        _hours = math.ceil(_resets_in / 3600)
                        status_hint = f" 您的计划使用限制已达到。约 {_hours} 小时后重置。"
                    else:
                        status_hint = " 您的计划使用限制已达到。请等待重置。"
                else:
                    status_hint = " 您正在被限速。请稍等并重试。"
            elif status_code == 529:
                status_hint = " API 暂时过载。请稍后重试。"
            elif status_code in (400, 500):
                # 大会话的 400 是上下文溢出。
                # 大会话的 500 通常意味着负载对
                # API 处理来说太大 — 以相同方式处理。
                if _hist_len > 50:
                    return (
                        "⚠️ 会话上下文超出了模型的限制范围。\n"
                        "使用 /compact 压缩对话，或 "
                        "/reset 重新开始。"
                    )
                elif status_code == 400:
                    status_hint = " 请求被 API 拒绝。"
            return (
                f"抱歉，遇到错误 ({error_type})。\n"
                f"{error_detail}\n"
                f"{status_hint}"
                "请重试或使用 /reset 重新开始会话。"
            )
        finally:
            # 清除会话环境
            self._clear_session_env()
    
    def _format_session_info(self) -> str:
        """解析当前模型配置并返回格式化的信息块。

        显示模型、提供商、上下文长度和端点，以便网关用户可以立即看到
        上下文检测是否出错（例如，本地模型回退到 128K 默认值）。
        """
        from agent.model_metadata import get_model_context_length, DEFAULT_FALLBACK_CONTEXT

        model = _resolve_gateway_model()
        config_context_length = None
        provider = None
        base_url = None
        api_key = None

        try:
            cfg_path = _kclaw_home / "config.yaml"
            if cfg_path.exists():
                import yaml as _info_yaml
                with open(cfg_path, encoding="utf-8") as f:
                    data = _info_yaml.safe_load(f) or {}
                model_cfg = data.get("model", {})
                if isinstance(model_cfg, dict):
                    raw_ctx = model_cfg.get("context_length")
                    if raw_ctx is not None:
                        try:
                            config_context_length = int(raw_ctx)
                        except (TypeError, ValueError):
                            pass
                    provider = model_cfg.get("provider") or None
                    base_url = model_cfg.get("base_url") or None
        except Exception:
            pass

        # 解析运行时凭证以进行探测
        try:
            runtime = _resolve_runtime_agent_kwargs()
            provider = provider or runtime.get("provider")
            base_url = base_url or runtime.get("base_url")
            api_key = runtime.get("api_key")
        except Exception:
            pass

        context_length = get_model_context_length(
            model,
            base_url=base_url or "",
            api_key=api_key or "",
            config_context_length=config_context_length,
            provider=provider or "",
        )

        # 格式化上下文来源提示
        if config_context_length is not None:
            ctx_source = "配置"
        elif context_length == DEFAULT_FALLBACK_CONTEXT:
            ctx_source = "默认值 — 在 config 中设置 model.context_length 以覆盖"
        else:
            ctx_source = "已检测"

        # 格式化上下文长度以便显示
        if context_length >= 1_000_000:
            ctx_display = f"{context_length / 1_000_000:.1f}M"
        elif context_length >= 1_000:
            ctx_display = f"{context_length // 1_000}K"
        else:
            ctx_display = str(context_length)

        lines = [
            f"◆ 模型: `{model}`",
            f"◆ 提供商: {provider or 'openrouter'}",
            f"◆ 上下文: {ctx_display} tokens ({ctx_source})",
        ]

        # 为本地/自定义设置显示端点
        if base_url and ("localhost" in base_url or "127.0.0.1" in base_url or "0.0.0.0" in base_url):
            lines.append(f"◆ 端点: {base_url}")

        return "\n".join(lines)

    async def _handle_reset_command(self, event: MessageEvent) -> str:
        """处理 /new 或 /reset 命令。"""
        source = event.source

        # 获取现有会话键
        session_key = self._session_key_for_source(source)

        # 在后台刷新记忆（触发后忘记），以便用户
        # 立即收到 "会话已重置！" 响应。
        try:
            old_entry = self.session_store._entries.get(session_key)
            if old_entry:
                _flush_task = asyncio.create_task(
                    self._async_flush_memories(old_entry.session_id)
                )
                self._background_tasks.add(_flush_task)
                _flush_task.add_done_callback(self._background_tasks.discard)
        except Exception as e:
            logger.debug("网关重置时的记忆刷新失败: %s", e)
        self._evict_cached_agent(session_key)

        try:
            from tools.env_passthrough import clear_env_passthrough
            clear_env_passthrough()
        except Exception:
            pass

        try:
            from tools.credential_files import clear_credential_files
            clear_credential_files()
        except Exception:
            pass

        # 重置会话
        new_entry = self.session_store.reset_session(session_key)

        # 清除任何会话范围的模型覆盖，以便下一个代理
        # 拾取配置的默认值而不是之前切换的模型。
        self._session_model_overrides.pop(session_key, None)

        # 触发插件 on_session_finalize 钩子（会话边界）
        try:
            from kclaw_cli.plugins import invoke_hook as _invoke_hook
            _old_sid = old_entry.session_id if old_entry else None
            _invoke_hook("on_session_finalize", session_id=_old_sid,
                         platform=source.platform.value if source.platform else "")
        except Exception:
            pass

        # 触发 session:end 钩子（会话正在结束）
        await self.hooks.emit("session:end", {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "session_key": session_key,
        })

        # 触发 session:reset 钩子
        await self.hooks.emit("session:reset", {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "session_key": session_key,
        })

        # 解析会话配置信息以便向用户显示
        try:
            session_info = self._format_session_info()
        except Exception:
            session_info = ""

        if new_entry:
            header = "✨ 会话已重置！重新开始。"
        else:
            # 没有现有会话，只需创建一个
            new_entry = self.session_store.get_or_create_session(source, force_new=True)
            header = "✨ 新会话已开始！"

        # 触发插件 on_session_reset 钩子（保证新会话存在）
        try:
            from kclaw_cli.plugins import invoke_hook as _invoke_hook
            _new_sid = new_entry.session_id if new_entry else None
            _invoke_hook("on_session_reset", session_id=_new_sid,
                         platform=source.platform.value if source.platform else "")
        except Exception:
            pass

        if session_info:
            return f"{header}\n\n{session_info}"
        return header
    
    async def _handle_profile_command(self, event: MessageEvent) -> str:
        """处理 /profile — 显示活动配置名称和主目录。"""
        from kclaw_constants import get_kclaw_home, display_kclaw_home
        from pathlib import Path

        home = get_kclaw_home()
        display = display_kclaw_home()

        # 从 KCLAW_HOME 路径检测配置名称
        # 配置路径格式: ~/.kclaw/profiles/<name>
        profiles_parent = Path.home() / ".kclaw" / "profiles"
        try:
            rel = home.relative_to(profiles_parent)
            profile_name = str(rel).split("/")[0]
        except ValueError:
            profile_name = None

        if profile_name:
            lines = [
                f"👤 **配置:** `{profile_name}`",
                f"📂 **主目录:** `{display}`",
            ]
        else:
            lines = [
                "👤 **配置:** 默认",
                f"📂 **主目录:** `{display}`",
            ]

        return "\n".join(lines)

    async def _handle_status_command(self, event: MessageEvent) -> str:
        """处理 /status 命令。"""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)

        connected_platforms = [p.value for p in self.adapters.keys()]

        # 检查是否有活动代理
        session_key = session_entry.session_key
        is_running = session_key in self._running_agents

        title = None
        if self._session_db:
            try:
                title = self._session_db.get_session_title(session_entry.session_id)
            except Exception:
                title = None

        lines = [
            "📊 **KClaw 网关状态**",
            "",
            f"**会话 ID:** `{session_entry.session_id}`",
        ]
        if title:
            lines.append(f"**标题:** {title}")
        lines.extend([
            f"**创建时间:** {session_entry.created_at.strftime('%Y-%m-%d %H:%M')}",
            f"**最后活动:** {session_entry.updated_at.strftime('%Y-%m-%d %H:%M')}",
            f"**Tokens:** {session_entry.total_tokens:,}",
            f"**代理运行中:** {'是 ⚡' if is_running else '否'}",
            "",
            f"**已连接平台:** {', '.join(connected_platforms)}",
        ])

        return "\n".join(lines)
    
    async def _handle_stop_command(self, event: MessageEvent) -> str:
        """处理 /stop 命令 — 中断正在运行的代理。

        当代理真正挂起（阻塞线程从不检查
        _interrupt_requested）时，_handle_message() 中的早期拦截
        在此方法被调用之前处理 /stop。此处理程序仅通过
        正常命令分发（无运行中的代理）或作为
        后备触发。为安全起见，在所有情况下强制清理会话锁。
        """
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        session_key = session_entry.session_key

        agent = self._running_agents.get(session_key)
        if agent is _AGENT_PENDING_SENTINEL:
            # 强制清理哨兵以便会话被解锁。
            if session_key in self._running_agents:
                del self._running_agents[session_key]
            logger.info("强制停止（待处理）会话 %s — 哨兵已清除", session_key[:20])
            return "⚡ 已强制停止。代理仍在启动中 — 会话已解锁。"
        if agent:
            agent.interrupt("停止请求")
            # 强制清理会话锁，以便真正挂起的代理不会
            # 永远保持锁定。
            if session_key in self._running_agents:
                del self._running_agents[session_key]
            return "⚡ 已强制停止。会话已解锁 — 你可以发送新消息。"
        else:
            return "没有活动任务要停止。"
    
    async def _handle_help_command(self, event: MessageEvent) -> str:
        """处理 /help 命令 — 列出可用命令。"""
        from kclaw_cli.commands import gateway_help_lines
        lines = [
            "📖 **KClaw 命令**\n",
            *gateway_help_lines(),
        ]
        try:
            from agent.skill_commands import get_skill_commands
            skill_cmds = get_skill_commands()
            if skill_cmds:
                lines.append(f"\n⚡ **技能命令** ({len(skill_cmds)} 个活动):")
                # 显示前 10 个，其余指向 /commands
                sorted_cmds = sorted(skill_cmds)
                for cmd in sorted_cmds[:10]:
                    lines.append(f"`{cmd}` — {skill_cmds[cmd]['description']}")
                if len(sorted_cmds) > 10:
                    lines.append(f"\n... 还有 {len(sorted_cmds) - 10} 个。使用 `/commands` 查看完整的分页列表。")
        except Exception:
            pass
        return "\n".join(lines)

    async def _handle_commands_command(self, event: MessageEvent) -> str:
        """处理 /commands [page] — 所有命令和技能的分页列表。"""
        from kclaw_cli.commands import gateway_help_lines

        raw_args = event.get_command_args().strip()
        if raw_args:
            try:
                requested_page = int(raw_args)
            except ValueError:
                return "用法: `/commands [page]`"
        else:
            requested_page = 1

        # 构建组合条目列表：内置命令 + 技能命令
        entries = list(gateway_help_lines())
        try:
            from agent.skill_commands import get_skill_commands
            skill_cmds = get_skill_commands()
            if skill_cmds:
                entries.append("")
                entries.append("⚡ **技能命令**:")
                for cmd in sorted(skill_cmds):
                    desc = skill_cmds[cmd].get("description", "").strip() or "技能命令"
                    entries.append(f"`{cmd}` — {desc}")
        except Exception:
            pass

        if not entries:
            return "没有可用的命令。"

        from gateway.config import Platform
        page_size = 15 if event.source.platform == Platform.TELEGRAM else 20
        total_pages = max(1, (len(entries) + page_size - 1) // page_size)
        page = max(1, min(requested_page, total_pages))
        start = (page - 1) * page_size
        page_entries = entries[start:start + page_size]

        lines = [
            f"📚 **命令** (共 {len(entries)} 个，第 {page}/{total_pages} 页)",
            "",
            *page_entries,
        ]
        if total_pages > 1:
            nav_parts = []
            if page > 1:
                nav_parts.append(f"`/commands {page - 1}` ← 上一页")
            if page < total_pages:
                nav_parts.append(f"下一页 → `/commands {page + 1}`")
            lines.extend(["", " | ".join(nav_parts)])
        if page != requested_page:
            lines.append(f"_(请求的页面 {requested_page} 超出了范围，显示第 {page} 页)_")
        return "\n".join(lines)
    
    async def _handle_model_command(self, event: MessageEvent) -> Optional[str]:
        """处理 /model 命令 — 为此会话切换模型。

        支持:
          /model                              — 交互式选择器 (Telegram/Discord) 或文本列表
          /model <name>                       — 仅为此会话切换
          /model <name> --global              — 切换并持久化到 config.yaml
          /model <name> --provider <provider> — 切换提供商 + 模型
          /model --provider <provider>        — 切换到提供商，自动检测模型
        """
        import yaml
        from kclaw_cli.model_switch import (
            switch_model as _switch_model, parse_model_flags,
            list_authenticated_providers,
        )
        from kclaw_cli.providers import get_label

        raw_args = event.get_command_args().strip()

        # 解析 --provider 和 --global 标志
        model_input, explicit_provider, persist_global = parse_model_flags(raw_args)

        # 从配置读取当前模型/提供商
        current_model = ""
        current_provider = "openrouter"
        current_base_url = ""
        current_api_key = ""
        user_provs = None
        config_path = _kclaw_home / "config.yaml"
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                model_cfg = cfg.get("model", {})
                if isinstance(model_cfg, dict):
                    current_model = model_cfg.get("default", "")
                    current_provider = model_cfg.get("provider", current_provider)
                    current_base_url = model_cfg.get("base_url", "")
                user_provs = cfg.get("providers")
        except Exception:
            pass

        # 检查会话覆盖
        source = event.source
        session_key = self._session_key_for_source(source)
        override = getattr(self, "_session_model_overrides", {}).get(session_key, {})
        if override:
            current_model = override.get("model", current_model)
            current_provider = override.get("provider", current_provider)
            current_base_url = override.get("base_url", current_base_url)
            current_api_key = override.get("api_key", current_api_key)

        # 无参数：显示交互式选择器 (Telegram/Discord) 或文本列表
        if not model_input and not explicit_provider:
            # 如果平台支持，尝试交互式选择器
            adapter = self.adapters.get(source.platform)
            has_picker = (
                adapter is not None
                and getattr(type(adapter), "send_model_picker", None) is not None
            )

            if has_picker:
                try:
                    providers = list_authenticated_providers(
                        current_provider=current_provider,
                        user_providers=user_provs,
                        max_models=50,
                    )
                except Exception:
                    providers = []

                if providers:
                    # 构建当用户选择模型时的回调闭包。
                    # 捕获 self + 切换逻辑所需的局部变量。
                    _self = self
                    _session_key = session_key
                    _cur_model = current_model
                    _cur_provider = current_provider
                    _cur_base_url = current_base_url
                    _cur_api_key = current_api_key

                    async def _on_model_selected(
                        _chat_id: str, model_id: str, provider_slug: str
                    ) -> str:
                        """执行模型切换并返回确认文本。"""
                        result = _switch_model(
                            raw_input=model_id,
                            current_provider=_cur_provider,
                            current_model=_cur_model,
                            current_base_url=_cur_base_url,
                            current_api_key=_cur_api_key,
                            is_global=False,
                            explicit_provider=provider_slug,
                        )
                        if not result.success:
                            return f"错误: {result.error_message}"

                        # 就地更新缓存代理
                        cached_entry = None
                        _cache_lock = getattr(_self, "_agent_cache_lock", None)
                        _cache = getattr(_self, "_agent_cache", None)
                        if _cache_lock and _cache is not None:
                            with _cache_lock:
                                cached_entry = _cache.get(_session_key)
                        if cached_entry and cached_entry[0] is not None:
                            try:
                                cached_entry[0].switch_model(
                                    new_model=result.new_model,
                                    new_provider=result.target_provider,
                                    api_key=result.api_key,
                                    base_url=result.base_url,
                                    api_mode=result.api_mode,
                                )
                            except Exception as exc:
                                logger.warning("Picker model switch failed for cached agent: %s", exc)

                        # 存储模型注释 + 会话覆盖
                        if not hasattr(_self, "_pending_model_notes"):
                            _self._pending_model_notes = {}
                        _self._pending_model_notes[_session_key] = (
                            f"[注意: 模型刚从 {_cur_model} 切换到 {result.new_model} "
                            f"通过 {result.provider_label or result.target_provider}。 "
                            f"请相应调整你的自我认知。]"
                        )
                        if not hasattr(_self, "_session_model_overrides"):
                            _self._session_model_overrides = {}
                        _self._session_model_overrides[_session_key] = {
                            "model": result.new_model,
                            "provider": result.target_provider,
                            "api_key": result.api_key,
                            "base_url": result.base_url,
                            "api_mode": result.api_mode,
                        }

                        # 构建确认文本
                        plabel = result.provider_label or result.target_provider
                        lines = [f"模型已切换到 `{result.new_model}`"]
                        lines.append(f"提供商: {plabel}")
                        mi = result.model_info
                        if mi:
                            if mi.context_window:
                                lines.append(f"上下文: {mi.context_window:,} tokens")
                            if mi.max_output:
                                lines.append(f"最大输出: {mi.max_output:,} tokens")
                            if mi.has_cost_data():
                                lines.append(f"费用: {mi.format_cost()}")
                            lines.append(f"能力: {mi.format_capabilities()}")
                        lines.append("_(仅会话 — 使用 `/model <name> --global` 持久化)_")
                        return "\n".join(lines)

                    metadata = {"thread_id": source.thread_id} if source.thread_id else None
                    result = await adapter.send_model_picker(
                        chat_id=source.chat_id,
                        providers=providers,
                        current_model=current_model,
                        current_provider=current_provider,
                        session_key=session_key,
                        on_model_selected=_on_model_selected,
                        metadata=metadata,
                    )
                    if result.success:
                        return None  # 选择器已发送 — 适配器处理响应

            # 后备：文本列表（适用于不支持选择器的平台或选择器失败时）
            provider_label = get_label(current_provider)
            lines = [f"当前: `{current_model or '未知'}` 于 {provider_label}", ""]

            try:
                providers = list_authenticated_providers(
                    current_provider=current_provider,
                    user_providers=user_provs,
                    max_models=5,
                )
                for p in providers:
                    tag = " (当前)" if p["is_current"] else ""
                    lines.append(f"**{p['name']}** `--provider {p['slug']}`{tag}:")
                    if p["models"]:
                        model_strs = ", ".join(f"`{m}`" for m in p["models"])
                        extra = f" (+{p['total_models'] - len(p['models'])} 更多)" if p["total_models"] > len(p["models"]) else ""
                        lines.append(f"  {model_strs}{extra}")
                    elif p.get("api_url"):
                        lines.append(f"  `{p['api_url']}`")
                    lines.append("")
            except Exception:
                pass

            lines.append("`/model <name>` — 切换模型")
            lines.append("`/model <name> --provider <slug>` — 切换提供商")
            lines.append("`/model <name> --global` — 持久化")
            return "\n".join(lines)

        # 执行切换
        result = _switch_model(
            raw_input=model_input,
            current_provider=current_provider,
            current_model=current_model,
            current_base_url=current_base_url,
            current_api_key=current_api_key,
            is_global=persist_global,
            explicit_provider=explicit_provider,
        )

        if not result.success:
            return f"错误: {result.error_message}"

        # 如果有缓存代理，就地更新它
        cached_entry = None
        _cache_lock = getattr(self, "_agent_cache_lock", None)
        _cache = getattr(self, "_agent_cache", None)
        if _cache_lock and _cache is not None:
            with _cache_lock:
                cached_entry = _cache.get(session_key)

        if cached_entry and cached_entry[0] is not None:
            try:
                cached_entry[0].switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                    api_mode=result.api_mode,
                )
            except Exception as exc:
                logger.warning("为缓存代理就地切换模型失败: %s", exc)

        # 存储注释以便下一个用户消息能感知切换
        # (避免在历史中间出现系统消息)。
        if not hasattr(self, "_pending_model_notes"):
            self._pending_model_notes = {}
        self._pending_model_notes[session_key] = (
            f"[注意: 模型刚从 {current_model} 切换到 {result.new_model} "
            f"通过 {result.provider_label or result.target_provider}。 "
            f"请相应调整你的自我认知。]"
        )

        # 存储会话覆盖以便下一个代理创建使用新模型
        if not hasattr(self, "_session_model_overrides"):
            self._session_model_overrides = {}
        self._session_model_overrides[session_key] = {
            "model": result.new_model,
            "provider": result.target_provider,
            "api_key": result.api_key,
            "base_url": result.base_url,
            "api_mode": result.api_mode,
        }

        # 如果指定了 --global 则持久化到配置
        if persist_global:
            try:
                if config_path.exists():
                    with open(config_path, encoding="utf-8") as f:
                        cfg = yaml.safe_load(f) or {}
                else:
                    cfg = {}
                model_cfg = cfg.setdefault("model", {})
                model_cfg["default"] = result.new_model
                model_cfg["provider"] = result.target_provider
                if result.base_url:
                    model_cfg["base_url"] = result.base_url
                from kclaw_cli.config import save_config
                save_config(cfg)
            except Exception as e:
                logger.warning("持久化模型切换失败: %s", e)

        # 构建带有完整元数据的确认消息
        provider_label = result.provider_label or result.target_provider
        lines = [f"模型已切换到 `{result.new_model}`"]
        lines.append(f"提供商: {provider_label}")

        # 来自 models.dev 的丰富元数据
        mi = result.model_info
        if mi:
            if mi.context_window:
                lines.append(f"上下文: {mi.context_window:,} tokens")
            if mi.max_output:
                lines.append(f"最大输出: {mi.max_output:,} tokens")
            if mi.has_cost_data():
                lines.append(f"费用: {mi.format_cost()}")
            lines.append(f"能力: {mi.format_capabilities()}")
        else:
            try:
                from agent.model_metadata import get_model_context_length
                ctx = get_model_context_length(
                    result.new_model,
                    base_url=result.base_url or current_base_url,
                    api_key=result.api_key or current_api_key,
                    provider=result.target_provider,
                )
                lines.append(f"上下文: {ctx:,} tokens")
            except Exception:
                pass

        # 缓存通知
        cache_enabled = (
            ("openrouter" in (result.base_url or "").lower() and "claude" in result.new_model.lower())
            or result.api_mode == "anthropic_messages"
        )
        if cache_enabled:
            lines.append("提示词缓存: 已启用")

        if result.warning_message:
            lines.append(f"警告: {result.warning_message}")

        if persist_global:
            lines.append("已保存到 config.yaml (`--global`)")
        else:
            lines.append("_(仅会话 — 添加 `--global` 以持久化)_")

        return "\n".join(lines)

    async def _handle_provider_command(self, event: MessageEvent) -> str:
        """处理 /provider 命令 - 显示可用的提供商。"""
        import yaml
        from kclaw_cli.models import (
            list_available_providers,
            normalize_provider,
            _PROVIDER_LABELS,
        )

        # 从配置解析当前提供商
        current_provider = "openrouter"
        config_path = _kclaw_home / 'config.yaml'
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                model_cfg = cfg.get("model", {})
                if isinstance(model_cfg, dict):
                    current_provider = model_cfg.get("provider", current_provider)
        except Exception:
            pass

        current_provider = normalize_provider(current_provider)
        if current_provider == "auto":
            try:
                from kclaw_cli.auth import resolve_provider as _resolve_provider
                current_provider = _resolve_provider(current_provider)
            except Exception:
                current_provider = "openrouter"

        # 从配置 base_url 检测自定义端点
        if current_provider == "openrouter":
            _cfg_base = model_cfg.get("base_url", "") if isinstance(model_cfg, dict) else ""
            if _cfg_base and "openrouter.ai" not in _cfg_base:
                current_provider = "custom"

        current_label = _PROVIDER_LABELS.get(current_provider, current_provider)

        lines = [
            f"🔌 **当前提供商:** {current_label} (`{current_provider}`)",
            "",
            "**可用的提供商:**",
        ]

        providers = list_available_providers()
        for p in providers:
            marker = " ← 活动" if p["id"] == current_provider else ""
            auth = "✅" if p["authenticated"] else "❌"
            aliases = f"  _(别名: {', '.join(p['aliases'])})_" if p["aliases"] else ""
            lines.append(f"{auth} `{p['id']}` — {p['label']}{aliases}{marker}")

        lines.append("")
        lines.append("切换: `/model provider:模型名称`")
        lines.append("设置: `kclaw setup`")
        return "\n".join(lines)
    
    async def _handle_personality_command(self, event: MessageEvent) -> str:
        """处理 /personality 命令 - 列出或设置人格。"""
        import yaml

        args = event.get_command_args().strip().lower()
        config_path = _kclaw_home / 'config.yaml'

        try:
            if config_path.exists():
                with open(config_path, 'r', encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                personalities = config.get("agent", {}).get("personalities", {})
            else:
                config = {}
                personalities = {}
        except Exception:
            config = {}
            personalities = {}

        if not personalities:
            return "在 `~/.kclaw/config.yaml` 中没有配置人格"

        if not args:
            lines = ["🎭 **可用的人格**\n"]
            lines.append("• `none` — (无人格覆盖)")
            for name, prompt in personalities.items():
                if isinstance(prompt, dict):
                    preview = prompt.get("description") or prompt.get("system_prompt", "")[:50]
                else:
                    preview = prompt[:50] + "..." if len(prompt) > 50 else prompt
                lines.append(f"• `{name}` — {preview}")
            lines.append("\n用法: `/personality <名称>`")
            return "\n".join(lines)

        def _resolve_prompt(value):
            if isinstance(value, dict):
                parts = [value.get("system_prompt", "")]
                if value.get("tone"):
                    parts.append(f'语气: {value["tone"]}')
                if value.get("style"):
                    parts.append(f'风格: {value["style"]}')
                return "\n".join(p for p in parts if p)
            return str(value)

        if args in ("none", "default", "neutral"):
            try:
                if "agent" not in config or not isinstance(config.get("agent"), dict):
                    config["agent"] = {}
                config["agent"]["system_prompt"] = ""
                atomic_yaml_write(config_path, config)
            except Exception as e:
                return f"⚠️ 保存人格变更失败: {e}"
            self._ephemeral_system_prompt = ""
            return "🎭 人格已清除 — 使用基础代理行为。\n_(在下条消息生效)_"
        elif args in personalities:
            new_prompt = _resolve_prompt(personalities[args])

            # 写入 config.yaml，与 CLI save_config_value 相同的模式。
            try:
                if "agent" not in config or not isinstance(config.get("agent"), dict):
                    config["agent"] = {}
                config["agent"]["system_prompt"] = new_prompt
                atomic_yaml_write(config_path, config)
            except Exception as e:
                return f"⚠️ 保存人格变更失败: {e}"

            # 更新内存以便下一条消息立即生效。
            self._ephemeral_system_prompt = new_prompt

            return f"🎭 人格已设置为 **{args}**\n_(在下条消息生效)_"

        available = "`none`, " + ", ".join(f"`{n}`" for n in personalities)
        return f"未知人格: `{args}`\n\n可用: {available}"
    
    async def _handle_retry_command(self, event: MessageEvent) -> str:
        """处理 /retry 命令 - 重新发送上一条用户消息。"""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)

        # 查找上一条用户消息
        last_user_msg = None
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_msg = history[i].get("content", "")
                last_user_idx = i
                break

        if not last_user_msg:
            return "没有上一条消息可重试。"

        # 截断历史到上一条用户消息之前并持久化
        truncated = history[:last_user_idx]
        self.session_store.rewrite_transcript(session_entry.session_id, truncated)
        # 重置存储的 token 计数 — 历史已被截断
        session_entry.last_prompt_tokens = 0

        # 通过用旧消息创建假文本事件来重新发送
        retry_event = MessageEvent(
            text=last_user_msg,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=event.raw_message,
        )

        # 让正常的消息处理器处理它
        return await self._handle_message(retry_event)
    
    async def _handle_undo_command(self, event: MessageEvent) -> str:
        """处理 /undo 命令 - 移除最后一条用户/助手交换。"""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)

        # 查找最后一条用户消息并从它开始移除所有内容
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return "没有可撤销的内容。"

        removed_msg = history[last_user_idx].get("content", "")
        removed_count = len(history) - last_user_idx
        self.session_store.rewrite_transcript(session_entry.session_id, history[:last_user_idx])
        # 重置存储的 token 计数 — 历史已被截断
        session_entry.last_prompt_tokens = 0

        preview = removed_msg[:40] + "..." if len(removed_msg) > 40 else removed_msg
        return f"↩️ 已撤销 {removed_count} 条消息。\n已移除: \"{preview}\""
    
    async def _handle_set_home_command(self, event: MessageEvent) -> str:
        """处理 /sethome 命令 -- 将当前聊天设置为平台的主频道。"""
        source = event.source
        platform_name = source.platform.value if source.platform else "unknown"
        chat_id = source.chat_id
        chat_name = source.chat_name or chat_id

        env_key = f"{platform_name.upper()}_HOME_CHANNEL"

        # 保存到 config.yaml
        try:
            import yaml
            config_path = _kclaw_home / 'config.yaml'
            user_config = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
            user_config[env_key] = chat_id
            atomic_yaml_write(config_path, user_config)
            # 同时设置到当前环境以便立即生效
            os.environ[env_key] = str(chat_id)
        except Exception as e:
            return f"保存主频道失败: {e}"

        return (
            f"✅ 主频道已设置为 **{chat_name}** (ID: {chat_id})。\n"
            f"定时任务和跨平台消息将投递到这里。"
        )
    
    @staticmethod
    def _get_guild_id(event: MessageEvent) -> Optional[int]:
        """从原始消息对象中提取 Discord guild_id。"""
        raw = getattr(event, "raw_message", None)
        if raw is None:
            return None
        # 斜杠命令交互
        if hasattr(raw, "guild_id") and raw.guild_id:
            return int(raw.guild_id)
        # 普通消息
        if hasattr(raw, "guild") and raw.guild:
            return raw.guild.id
        return None

    async def _handle_voice_command(self, event: MessageEvent) -> str:
        """处理 /voice [on|off|tts|channel|leave|status] 命令。"""
        args = event.get_command_args().strip().lower()
        chat_id = event.source.chat_id

        adapter = self.adapters.get(event.source.platform)

        if args in ("on", "enable"):
            self._voice_mode[chat_id] = "voice_only"
            self._save_voice_modes()
            if adapter:
                self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=False)
            return (
                "语音模式已启用。\n"
                "当你发送语音消息时我会用语音回复。\n"
                "使用 /voice tts 获取所有消息的语音回复。"
            )
        elif args in ("off", "disable"):
            self._voice_mode[chat_id] = "off"
            self._save_voice_modes()
            if adapter:
                self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)
            return "语音模式已禁用。仅文字回复。"
        elif args == "tts":
            self._voice_mode[chat_id] = "all"
            self._save_voice_modes()
            if adapter:
                self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=False)
            return (
                "自动 TTS 已启用。\n"
                "所有回复将包含语音消息。"
            )
        elif args in ("channel", "join"):
            return await self._handle_voice_channel_join(event)
        elif args == "leave":
            return await self._handle_voice_channel_leave(event)
        elif args == "status":
            mode = self._voice_mode.get(chat_id, "off")
            labels = {
                "off": "关闭（仅文字）",
                "voice_only": "开启（语音消息语音回复）",
                "all": "TTS（所有消息语音回复）",
            }
            # 如果已连接则附加语音频道信息
            adapter = self.adapters.get(event.source.platform)
            guild_id = self._get_guild_id(event)
            if guild_id and hasattr(adapter, "get_voice_channel_info"):
                info = adapter.get_voice_channel_info(guild_id)
                if info:
                    lines = [
                        f"语音模式: {labels.get(mode, mode)}",
                        f"语音频道: #{info['channel_name']}",
                        f"参与者: {info['member_count']}",
                    ]
                    for m in info["members"]:
                        status = " (说话中)" if m.get("is_speaking") else ""
                        lines.append(f"  - {m['display_name']}{status}")
                    return "\n".join(lines)
            return f"语音模式: {labels.get(mode, mode)}"
        else:
            # 切换: off → on, on/all → off
            current = self._voice_mode.get(chat_id, "off")
            if current == "off":
                self._voice_mode[chat_id] = "voice_only"
                self._save_voice_modes()
                if adapter:
                    self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=False)
                return "语音模式已启用。"
            else:
                self._voice_mode[chat_id] = "off"
                self._save_voice_modes()
                if adapter:
                    self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)
                return "语音模式已禁用。"

    async def _handle_voice_channel_join(self, event: MessageEvent) -> str:
        """加入用户当前的 Discord 语音频道。"""
        adapter = self.adapters.get(event.source.platform)
        if not hasattr(adapter, "join_voice_channel"):
            return "此平台不支持语音频道。"

        guild_id = self._get_guild_id(event)
        if not guild_id:
            return "此命令仅在 Discord 服务器中有效。"

        voice_channel = await adapter.get_user_voice_channel(
            guild_id, event.source.user_id
        )
        if not voice_channel:
            return "你需要先进入语音频道。"

        # 在连接之前连接回调，以便连接后立即到达的语音输入不会丢失。
        if hasattr(adapter, "_voice_input_callback"):
            adapter._voice_input_callback = self._handle_voice_channel_input
        if hasattr(adapter, "_on_voice_disconnect"):
            adapter._on_voice_disconnect = self._handle_voice_timeout_cleanup

        try:
            success = await adapter.join_voice_channel(voice_channel)
        except Exception as e:
            logger.warning("加入语音频道失败: %s", e)
            adapter._voice_input_callback = None
            err_lower = str(e).lower()
            if "pynacl" in err_lower or "nacl" in err_lower or "davey" in err_lower:
                return (
                    "缺少语音依赖 (PyNaCl / davey)。 "
                    "安装或重新安装带有消息功能的 KClaw，例如 "
                    "`pip install kclaw[messaging]`。"
                )
            return f"加入语音频道失败: {e}"

        if success:
            adapter._voice_text_channels[guild_id] = int(event.source.chat_id)
            self._voice_mode[event.source.chat_id] = "all"
            self._save_voice_modes()
            self._set_adapter_auto_tts_disabled(adapter, event.source.chat_id, disabled=False)
            return (
                f"已加入语音频道 **{voice_channel.name}**。\n"
                f"我会用语音回复你并听你说话。使用 /voice leave 断开连接。"
            )
        # 加入失败 — 清除回调
        adapter._voice_input_callback = None
        return "加入语音频道失败。请检查机器人权限（连接 + 说话）。"

    async def _handle_voice_channel_leave(self, event: MessageEvent) -> str:
        """离开 Discord 语音频道。"""
        adapter = self.adapters.get(event.source.platform)
        guild_id = self._get_guild_id(event)

        if not guild_id or not hasattr(adapter, "leave_voice_channel"):
            return "不在语音频道中。"

        if not hasattr(adapter, "is_in_voice_channel") or not adapter.is_in_voice_channel(guild_id):
            return "不在语音频道中。"

        try:
            await adapter.leave_voice_channel(guild_id)
        except Exception as e:
            logger.warning("离开语音频道错误: %s", e)
        # 即使 leave 抛出异常也要始终清理状态
        self._voice_mode[event.source.chat_id] = "off"
        self._save_voice_modes()
        self._set_adapter_auto_tts_disabled(adapter, event.source.chat_id, disabled=True)
        if hasattr(adapter, "_voice_input_callback"):
            adapter._voice_input_callback = None
        return "已离开语音频道。"

    def _handle_voice_timeout_cleanup(self, chat_id: str) -> None:
        """当语音频道超时时由适配器调用。

        清理运行器端适配器无法触及的 voice_mode 状态。
        """
        self._voice_mode[chat_id] = "off"
        self._save_voice_modes()
        adapter = self.adapters.get(Platform.DISCORD)
        self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)

    async def _handle_voice_channel_input(
        self, guild_id: int, user_id: int, transcript: str
    ):
        """处理语音频道中用户的转录语音。

        创建一个合成 MessageEvent 并通过适配器的完整消息管道
        （会话、打字、代理、TTS 回复）处理。
        """
        adapter = self.adapters.get(Platform.DISCORD)
        if not adapter:
            return

        text_ch_id = adapter._voice_text_channels.get(guild_id)
        if not text_ch_id:
            return

        # 处理语音输入前检查授权
        source = SessionSource(
            platform=Platform.DISCORD,
            chat_id=str(text_ch_id),
            user_id=str(user_id),
            user_name=str(user_id),
            chat_type="channel",
        )
        if not self._is_user_authorized(source):
            logger.debug("未授权的用户 %d 语音输入，忽略", user_id)
            return

        # 在文本频道中显示转录文本（在认证后，清理提及）
        try:
            channel = adapter._client.get_channel(text_ch_id)
            if channel:
                safe_text = transcript[:2000].replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
                await channel.send(f"**[语音]** <@{user_id}>: {safe_text}")
        except Exception:
            pass

        # 构建合成 MessageEvent 并通过正常管道处理
        # 使用 SimpleNamespace 作为 raw_message，以便 _get_guild_id() 可以提取
        # guild_id，_send_voice_reply() 在语音频道中播放音频。
        from types import SimpleNamespace
        event = MessageEvent(
            source=source,
            text=transcript,
            message_type=MessageType.VOICE,
            raw_message=SimpleNamespace(guild_id=guild_id, guild=None),
        )

        await adapter.handle_message(event)

    def _should_send_voice_reply(
        self,
        event: MessageEvent,
        response: str,
        agent_messages: list,
        already_sent: bool = False,
    ) -> bool:
        """决定运行器是否应该发送 TTS 语音回复。

        在以下情况下返回 False:
        - 此聊天的 voice_mode 关闭
        - 响应为空或错误
        - 代理已调用 text_to_speech 工具（去重）
        - 语音输入和基础适配器自动 TTS 已处理（跳过双重）
          除非流式传输已消费响应 (already_sent=True)，
          在这种情况下基础适配器不会有自动 TTS 的文本，
          所以运行器必须处理。
        """
        if not response or response.startswith("Error:"):
            return False

        chat_id = event.source.chat_id
        voice_mode = self._voice_mode.get(chat_id, "off")
        is_voice_input = (event.message_type == MessageType.VOICE)

        should = (
            (voice_mode == "all")
            or (voice_mode == "voice_only" and is_voice_input)
        )
        if not should:
            return False

        # 去重：代理已调用 TTS 工具
        has_agent_tts = any(
            msg.get("role") == "assistant"
            and any(
                tc.get("function", {}).get("name") == "text_to_speech"
                for tc in (msg.get("tool_calls") or [])
            )
            for msg in agent_messages
        )
        if has_agent_tts:
            return False

        # 去重：基础适配器自动 TTS 已处理语音输入
        # (play_tts 在 VC 中播放时，运行器可以跳过)。
        # 当流式传输已传递文本时 (already_sent=True)，
        # 基础适配器将收到 None，无法运行自动 TTS，
        # 所以运行器必须接管。
        if is_voice_input and not already_sent:
            return False

        return True

    async def _send_voice_reply(self, event: MessageEvent, text: str) -> None:
        """在文本回复之前生成 TTS 音频并作为语音消息发送。"""
        import uuid as _uuid
        audio_path = None
        actual_path = None
        try:
            from tools.tts_tool import text_to_speech_tool, _strip_markdown_for_tts

            tts_text = _strip_markdown_for_tts(text[:4000])
            if not tts_text:
                return

            # 使用 .mp3 扩展名以便 edge-tts 正确转换为 opus。
            # TTS 工具可能转换为 .ogg — 使用结果中的 file_path。
            audio_path = os.path.join(
                tempfile.gettempdir(), "kclaw_voice",
                f"tts_reply_{_uuid.uuid4().hex[:12]}.mp3",
            )
            os.makedirs(os.path.dirname(audio_path), exist_ok=True)

            result_json = await asyncio.to_thread(
                text_to_speech_tool, text=tts_text, output_path=audio_path
            )
            result = json.loads(result_json)

            # 使用结果中的实际文件路径（opus 转换后可能不同）
            actual_path = result.get("file_path", audio_path)
            if not result.get("success") or not os.path.isfile(actual_path):
                logger.warning("自动语音回复 TTS 失败: %s", result.get("error"))
                return

            adapter = self.adapters.get(event.source.platform)

            # 如果连接到语音频道，在那里播放而不是发送文件
            guild_id = self._get_guild_id(event)
            if (guild_id
                    and hasattr(adapter, "play_in_voice_channel")
                    and hasattr(adapter, "is_in_voice_channel")
                    and adapter.is_in_voice_channel(guild_id)):
                await adapter.play_in_voice_channel(guild_id, actual_path)
            elif adapter and hasattr(adapter, "send_voice"):
                send_kwargs: Dict[str, Any] = {
                    "chat_id": event.source.chat_id,
                    "audio_path": actual_path,
                    "reply_to": event.message_id,
                }
                if event.source.thread_id:
                    send_kwargs["metadata"] = {"thread_id": event.source.thread_id}
                await adapter.send_voice(**send_kwargs)
        except Exception as e:
            logger.warning("自动语音回复失败: %s", e, exc_info=True)
        finally:
            for p in {audio_path, actual_path} - {None}:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def _deliver_media_from_response(
        self,
        response: str,
        event: MessageEvent,
        adapter,
    ) -> None:
        """Extract MEDIA: tags and local file paths from a response and deliver them.

        Called after streaming has already sent the text to the user, so the
        text itself is already delivered — this only handles file attachments
        that the normal _process_message_background path would have caught.
        """
        from pathlib import Path

        try:
            media_files, _ = adapter.extract_media(response)
            _, cleaned = adapter.extract_images(response)
            local_files, _ = adapter.extract_local_files(cleaned)

            _thread_meta = {"thread_id": event.source.thread_id} if event.source.thread_id else None

            _AUDIO_EXTS = {'.ogg', '.opus', '.mp3', '.wav', '.m4a'}
            _VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}
            _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

            for media_path, is_voice in media_files:
                try:
                    ext = Path(media_path).suffix.lower()
                    if ext in _AUDIO_EXTS:
                        await adapter.send_voice(
                            chat_id=event.source.chat_id,
                            audio_path=media_path,
                            metadata=_thread_meta,
                        )
                    elif ext in _VIDEO_EXTS:
                        await adapter.send_video(
                            chat_id=event.source.chat_id,
                            video_path=media_path,
                            metadata=_thread_meta,
                        )
                    elif ext in _IMAGE_EXTS:
                        await adapter.send_image_file(
                            chat_id=event.source.chat_id,
                            image_path=media_path,
                            metadata=_thread_meta,
                        )
                    else:
                        await adapter.send_document(
                            chat_id=event.source.chat_id,
                            file_path=media_path,
                            metadata=_thread_meta,
                        )
                except Exception as e:
                    logger.warning("[%s] 流后媒体传递失败：%s", adapter.name, e)

            for file_path in local_files:
                try:
                    ext = Path(file_path).suffix.lower()
                    if ext in _IMAGE_EXTS:
                        await adapter.send_image_file(
                            chat_id=event.source.chat_id,
                            image_path=file_path,
                            metadata=_thread_meta,
                        )
                    else:
                        await adapter.send_document(
                            chat_id=event.source.chat_id,
                            file_path=file_path,
                            metadata=_thread_meta,
                        )
                except Exception as e:
                    logger.warning("[%s] 流后文件传递失败：%s", adapter.name, e)

        except Exception as e:
            logger.warning("流后媒体提取失败：%s", e)

    async def _handle_rollback_command(self, event: MessageEvent) -> str:
        """Handle /rollback command — list or restore filesystem checkpoints."""
        from tools.checkpoint_manager import CheckpointManager, format_checkpoint_list

        # 从 config.yaml 读取检查点配置
        cp_cfg = {}
        try:
            import yaml as _y
            _cfg_path = _kclaw_home / "config.yaml"
            if _cfg_path.exists():
                with open(_cfg_path, encoding="utf-8") as _f:
                    _data = _y.safe_load(_f) or {}
                cp_cfg = _data.get("checkpoints", {})
                if isinstance(cp_cfg, bool):
                    cp_cfg = {"enabled": cp_cfg}
        except Exception:
            pass

        if not cp_cfg.get("enabled", False):
            return (
                "Checkpoints are not enabled.\n"
                "Enable in config.yaml:\n```\ncheckpoints:\n  enabled: true\n```"
            )

        mgr = CheckpointManager(
            enabled=True,
            max_snapshots=cp_cfg.get("max_snapshots", 50),
        )

        cwd = os.getenv("MESSAGING_CWD", str(Path.home()))
        arg = event.get_command_args().strip()

        if not arg:
            checkpoints = mgr.list_checkpoints(cwd)
            return format_checkpoint_list(checkpoints, cwd)

        # 通过编号或哈希恢复
        checkpoints = mgr.list_checkpoints(cwd)
        if not checkpoints:
            return f"未找到 {cwd} 的检查点"

        target_hash = None
        try:
            idx = int(arg) - 1
            if 0 <= idx < len(checkpoints):
                target_hash = checkpoints[idx]["hash"]
            else:
                return f"无效的检查点编号。使用 1-{len(checkpoints)}。"
        except ValueError:
            target_hash = arg

        result = mgr.restore(cwd, target_hash)
        if result["success"]:
            return (
                f"✅ 已恢复到检查点 {result['restored_to']}: {result['reason']}\n"
                f"自动保存了回滚前快照。"
            )
        return f"❌ {result['error']}"

    async def _handle_background_command(self, event: MessageEvent) -> str:
        """处理 /background <prompt> — 在单独的后台会话中运行提示词。

        在后台线程中生成一个新的 AIAgent，有自己的会话。
        当完成时，将结果发送回同一聊天而不修改
        活动会话的对话历史。
        """
        prompt = event.get_command_args().strip()
        if not prompt:
            return (
                "用法: /background <提示词>\n"
                "示例: /background 总结今天的热门 HN 新闻\n\n"
                "在单独的会话中运行提示词。 "
                "你可以继续聊天 — 结果完成后会出现在这里。"
            )

        source = event.source
        task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{os.urandom(3).hex()}"

        # 后台任务的触发即忘记模式
        _task = asyncio.create_task(
            self._run_background_task(prompt, source, task_id)
        )
        self._background_tasks.add(_task)
        _task.add_done_callback(self._background_tasks.discard)

        preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
        return f'🔄 后台任务已开始: "{preview}"\n任务 ID: {task_id}\n你可以继续聊天 — 结果完成后会出现在这里。'

    async def _run_background_task(
        self, prompt: str, source: "SessionSource", task_id: str
    ) -> None:
        """执行后台代理任务并将结果发送到聊天。"""
        from run_agent import AIAgent

        adapter = self.adapters.get(source.platform)
        if not adapter:
            logger.warning("后台任务 %s 没有平台 %s 的适配器", source.platform, task_id)
            return

        _thread_metadata = {"thread_id": source.thread_id} if source.thread_id else None

        try:
            runtime_kwargs = _resolve_runtime_agent_kwargs()
            if not runtime_kwargs.get("api_key"):
                await adapter.send(
                    source.chat_id,
                    f"❌ 后台任务 {task_id} 失败: 未配置提供商凭据。",
                    metadata=_thread_metadata,
                )
                return

            user_config = _load_gateway_config()
            model = _resolve_gateway_model(user_config)
            platform_key = _platform_config_key(source.platform)

            from kclaw_cli.tools_config import _get_platform_tools
            enabled_toolsets = sorted(_get_platform_tools(user_config, platform_key))

            pr = self._provider_routing
            max_iterations = int(os.getenv("KCLAW_MAX_ITERATIONS", "90"))
            reasoning_config = self._load_reasoning_config()
            self._reasoning_config = reasoning_config
            turn_route = self._resolve_turn_agent_config(prompt, model, runtime_kwargs)

            def run_sync():
                agent = AIAgent(
                    model=turn_route["model"],
                    **turn_route["runtime"],
                    max_iterations=max_iterations,
                    quiet_mode=True,
                    verbose_logging=False,
                    enabled_toolsets=enabled_toolsets,
                    reasoning_config=reasoning_config,
                    providers_allowed=pr.get("only"),
                    providers_ignored=pr.get("ignore"),
                    providers_order=pr.get("order"),
                    provider_sort=pr.get("sort"),
                    provider_require_parameters=pr.get("require_parameters", False),
                    provider_data_collection=pr.get("data_collection"),
                    session_id=task_id,
                    platform=platform_key,
                    user_id=source.user_id,
                    session_db=self._session_db,
                    fallback_model=self._fallback_model,
                )

                return agent.run_conversation(
                    user_message=prompt,
                    task_id=task_id,
                )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_sync)

            response = result.get("final_response", "") if result else ""
            if not response and result and result.get("error"):
                response = f"错误: {result['error']}"

            # 从响应中提取媒体文件
            if response:
                media_files, response = adapter.extract_media(response)
                images, text_content = adapter.extract_images(response)

                preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
                header = f'✅ 后台任务完成\n提示词: "{preview}"\n\n'

                if text_content:
                    await adapter.send(
                        chat_id=source.chat_id,
                        content=header + text_content,
                        metadata=_thread_metadata,
                    )
                elif not images and not media_files:
                    await adapter.send(
                        chat_id=source.chat_id,
                        content=header + "(未生成响应)",
                        metadata=_thread_metadata,
                    )

                # 发送提取的图片
                for image_url, alt_text in (images or []):
                    try:
                        await adapter.send_image(
                            chat_id=source.chat_id,
                            image_url=image_url,
                            caption=alt_text,
                        )
                    except Exception:
                        pass

                # 发送媒体文件
                for media_path in (media_files or []):
                    try:
                        await adapter.send_document(
                            chat_id=source.chat_id,
                            file_path=media_path,
                        )
                    except Exception:
                        pass
            else:
                preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
                await adapter.send(
                    chat_id=source.chat_id,
                    content=f'✅ 后台任务完成\n提示词: "{preview}"\n\n(未生成响应)',
                    metadata=_thread_metadata,
                )

        except Exception as e:
            logger.exception("后台任务 %s 失败", task_id)
            try:
                await adapter.send(
                    chat_id=source.chat_id,
                    content=f"❌ 后台任务 {task_id} 失败: {e}",
                    metadata=_thread_metadata,
                )
            except Exception:
                pass

    async def _handle_btw_command(self, event: MessageEvent) -> str:
        """Handle /btw <question> — ephemeral side question in the same chat."""
        question = event.get_command_args().strip()
        if not question:
            return (
                "用法: /btw <问题>\n"
                "示例: /btw 哪个模块负责会话标题清理？\n\n"
                "使用会话上下文回答。不使用工具，不会持久化。"
            )

        source = event.source
        session_key = self._session_key_for_source(source)

        # 保护：每个会话一次只能有一个 /btw
        existing = getattr(self, "_active_btw_tasks", {}).get(session_key)
        if existing and not existing.done():
            return "此聊天已有一个 /btw 任务在运行，请等待完成。"

        if not hasattr(self, "_active_btw_tasks"):
            self._active_btw_tasks: dict = {}

        import uuid as _uuid
        task_id = f"btw_{datetime.now().strftime('%H%M%S')}_{_uuid.uuid4().hex[:6]}"
        _task = asyncio.create_task(self._run_btw_task(question, source, session_key, task_id))
        self._background_tasks.add(_task)
        self._active_btw_tasks[session_key] = _task

        def _cleanup(task):
            self._background_tasks.discard(task)
            if self._active_btw_tasks.get(session_key) is task:
                self._active_btw_tasks.pop(session_key, None)

        _task.add_done_callback(_cleanup)

        preview = question[:60] + ("..." if len(question) > 60 else "")
        return f'💬 /btw: "{preview}"\nReply will appear here shortly.'

    async def _run_btw_task(
        self, question: str, source, session_key: str, task_id: str,
    ) -> None:
        """Execute an ephemeral /btw side question and deliver the answer."""
        from run_agent import AIAgent

        adapter = self.adapters.get(source.platform)
        if not adapter:
            logger.warning("/btw 任务 %s 没有平台 %s 的适配器", source.platform, task_id)
            return

        _thread_meta = {"thread_id": source.thread_id} if source.thread_id else None

        try:
            runtime_kwargs = _resolve_runtime_agent_kwargs()
            if not runtime_kwargs.get("api_key"):
                await adapter.send(
                    source.chat_id,
                    "❌ /btw failed: no provider credentials configured.",
                    metadata=_thread_meta,
                )
                return

            user_config = _load_gateway_config()
            model = _resolve_gateway_model(user_config)
            platform_key = _platform_config_key(source.platform)
            reasoning_config = self._load_reasoning_config()
            turn_route = self._resolve_turn_agent_config(question, model, runtime_kwargs)
            pr = self._provider_routing

            # 从运行中的代理或存储的转录本获取历史快照
            running_agent = self._running_agents.get(session_key)
            if running_agent and running_agent is not _AGENT_PENDING_SENTINEL:
                history_snapshot = list(getattr(running_agent, "_session_messages", []) or [])
            else:
                session_entry = self.session_store.get_or_create_session(source)
                history_snapshot = self.session_store.load_transcript(session_entry.session_id)

            btw_prompt = (
                "[Ephemeral /btw side question. Answer using the conversation "
                "context. No tools available. Be direct and concise.]\n\n"
                + question
            )

            def run_sync():
                agent = AIAgent(
                    model=turn_route["model"],
                    **turn_route["runtime"],
                    max_iterations=8,
                    quiet_mode=True,
                    verbose_logging=False,
                    enabled_toolsets=[],
                    reasoning_config=reasoning_config,
                    providers_allowed=pr.get("only"),
                    providers_ignored=pr.get("ignore"),
                    providers_order=pr.get("order"),
                    provider_sort=pr.get("sort"),
                    provider_require_parameters=pr.get("require_parameters", False),
                    provider_data_collection=pr.get("data_collection"),
                    session_id=task_id,
                    platform=platform_key,
                    session_db=None,
                    fallback_model=self._fallback_model,
                    skip_memory=True,
                    skip_context_files=True,
                    persist_session=False,
                )
                return agent.run_conversation(
                    user_message=btw_prompt,
                    conversation_history=history_snapshot,
                    task_id=task_id,
                )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_sync)

            response = (result.get("final_response") or "") if result else ""
            if not response and result and result.get("error"):
                response = f"Error: {result['error']}"
            if not response:
                response = "(No response generated)"

            media_files, response = adapter.extract_media(response)
            images, text_content = adapter.extract_images(response)
            preview = question[:60] + ("..." if len(question) > 60 else "")
            header = f'💬 /btw: "{preview}"\n\n'

            if text_content:
                await adapter.send(
                    chat_id=source.chat_id,
                    content=header + text_content,
                    metadata=_thread_meta,
                )
            elif not images and not media_files:
                await adapter.send(
                    chat_id=source.chat_id,
                    content=header + "(No response generated)",
                    metadata=_thread_meta,
                )

            for image_url, alt_text in (images or []):
                try:
                    await adapter.send_image(chat_id=source.chat_id, image_url=image_url, caption=alt_text)
                except Exception:
                    pass

            for media_path in (media_files or []):
                try:
                    await adapter.send_file(chat_id=source.chat_id, file_path=media_path)
                except Exception:
                    pass

        except Exception as e:
            logger.exception("/btw 任务 %s 失败", task_id)
            try:
                await adapter.send(
                    chat_id=source.chat_id,
                    content=f"❌ /btw failed: {e}",
                    metadata=_thread_meta,
                )
            except Exception:
                pass

    async def _handle_reasoning_command(self, event: MessageEvent) -> str:
        """Handle /reasoning command — manage reasoning effort and display toggle.

        用法：
            /reasoning              显示当前推理强度和显示状态
            /reasoning <level>      设置推理强度 (none, low, medium, high, xhigh)
            /reasoning show|on      在响应中显示模型推理
            /reasoning hide|off     在响应中隐藏模型推理
        """
        import yaml

        args = event.get_command_args().strip().lower()
        config_path = _kclaw_home / "config.yaml"
        self._reasoning_config = self._load_reasoning_config()
        self._show_reasoning = self._load_show_reasoning()

        def _save_config_key(key_path: str, value):
            """Save a dot-separated key to config.yaml."""
            try:
                user_config = {}
                if config_path.exists():
                    with open(config_path, encoding="utf-8") as f:
                        user_config = yaml.safe_load(f) or {}
                keys = key_path.split(".")
                current = user_config
                for k in keys[:-1]:
                    if k not in current or not isinstance(current[k], dict):
                        current[k] = {}
                    current = current[k]
                current[keys[-1]] = value
                atomic_yaml_write(config_path, user_config)
                return True
            except Exception as e:
                logger.error("保存配置键 %s 失败：%s", key_path, e)
                return False

        if not args:
            # 显示当前状态
            rc = self._reasoning_config
            if rc is None:
                level = "medium (default)"
            elif rc.get("enabled") is False:
                level = "none (disabled)"
            else:
                level = rc.get("effort", "medium")
            display_state = "on ✓" if self._show_reasoning else "off"
            return (
                "🧠 **Reasoning Settings**\n\n"
                f"**Effort:** `{level}`\n"
                f"**Display:** {display_state}\n\n"
                "_用法:_ `/reasoning <none|low|medium|high|xhigh|show|hide>`"
            )

        # 显示切换
        if args in ("show", "on"):
            self._show_reasoning = True
            _save_config_key("display.show_reasoning", True)
            return "🧠 ✓ 推理显示：**开启**\n模型思考过程将在每次响应前显示。"

        if args in ("hide", "off"):
            self._show_reasoning = False
            _save_config_key("display.show_reasoning", False)
            return "🧠 ✓ 推理显示：**关闭**"

        # 工作量级别变化
        effort = args.strip()
        if effort == "none":
            parsed = {"enabled": False}
        elif effort in ("xhigh", "high", "medium", "low", "minimal"):
            parsed = {"enabled": True, "effort": effort}
        else:
            return (
                f"⚠️ 未知参数：`{effort}`\n\n"
                "**有效级别：** none, low, minimal, medium, high, xhigh\n"
                "**显示选项：** show, hide"
            )

        self._reasoning_config = parsed
        if _save_config_key("agent.reasoning_effort", effort):
            return f"🧠 ✓ 推理强度设置为 `{effort}`（已保存到配置）\n_(下次消息生效)_"
        else:
            return f"🧠 ✓ 推理强度设置为 `{effort}`（仅限本次会话）"

    async def _handle_yolo_command(self, event: MessageEvent) -> str:
        """Handle /yolo — toggle dangerous command approval bypass."""
        current = bool(os.environ.get("KCLAW_YOLO_MODE"))
        if current:
            os.environ.pop("KCLAW_YOLO_MODE", None)
            return "⚠️ YOLO 模式 **关闭** — 危险命令将需要审批。"
        else:
            os.environ["KCLAW_YOLO_MODE"] = "1"
            return "⚡ YOLO 模式 **开启** — 所有命令自动批准。请谨慎使用。"

    async def _handle_verbose_command(self, event: MessageEvent) -> str:
        """Handle /verbose command — cycle tool progress display mode.

        由 config.yaml 中的 ``display.tool_progress_command`` 控制（默认关闭）。
        开启后，工具进度模式循环切换：off → new → all → verbose → off，
        与 CLI 相同。
        """
        import yaml

        config_path = _kclaw_home / "config.yaml"

        # --- check config gate ------------------------------------------------
        try:
            user_config = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
            gate_enabled = user_config.get("display", {}).get("tool_progress_command", False)
        except Exception:
            gate_enabled = False

        if not gate_enabled:
            return (
                "`/verbose` 命令未在消息平台启用。\n\n"
                "在 `config.yaml` 中开启：\n```yaml\n"
                "display:\n  tool_progress_command: true\n```"
            )

        # --- cycle mode -------------------------------------------------------
        cycle = ["off", "new", "all", "verbose"]
        descriptions = {
            "off": "⚙️ 工具进度：**关闭** — 不显示工具活动。",
            "new": "⚙️ 工具进度：**新工具** — 仅在工具切换时显示（预览长度：`display.tool_preview_length`，默认 40）。",
            "all": "⚙️ 工具进度：**全部** — 显示每个工具调用（预览长度：`display.tool_preview_length`，默认 40）。",
            "verbose": "⚙️ 工具进度：**详细** — 每个工具调用显示完整参数。",
        }

        raw_progress = user_config.get("display", {}).get("tool_progress", "all")
        # YAML 1.1 将裸 "off" 解析为布尔值 False — 规范化回来
        if raw_progress is False:
            current = "off"
        elif raw_progress is True:
            current = "all"
        else:
            current = str(raw_progress).lower()
        if current not in cycle:
            current = "all"
        idx = (cycle.index(current) + 1) % len(cycle)
        new_mode = cycle[idx]

        # 保存到 config.yaml
        try:
            if "display" not in user_config or not isinstance(user_config.get("display"), dict):
                user_config["display"] = {}
            user_config["display"]["tool_progress"] = new_mode
            atomic_yaml_write(config_path, user_config)
            return f"{descriptions[new_mode]}\n_(已保存到配置 — 下次消息生效)_"
        except Exception as e:
            logger.warning("保存 tool_progress 模式失败：%s", e)
            return f"{descriptions[new_mode]}\n_(无法保存到配置：{e})_"

    async def _handle_compress_command(self, event: MessageEvent) -> str:
        """Handle /compress command -- manually compress conversation context."""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)

        if not history or len(history) < 4:
            return "对话不足，无法压缩（需要至少 4 条消息）。"

        try:
            from run_agent import AIAgent
            from agent.model_metadata import estimate_messages_tokens_rough

            runtime_kwargs = _resolve_runtime_agent_kwargs()
            if not runtime_kwargs.get("api_key"):
                return "未配置提供商 — 无法压缩。"

            # 从配置解析模型（与上面的内存刷新相同原因）。
            model = _resolve_gateway_model()

            msgs = [
                {"role": m.get("role"), "content": m.get("content")}
                for m in history
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            original_count = len(msgs)
            approx_tokens = estimate_messages_tokens_rough(msgs)

            tmp_agent = AIAgent(
                **runtime_kwargs,
                model=model,
                max_iterations=4,
                quiet_mode=True,
                enabled_toolsets=["memory"],
                session_id=session_entry.session_id,
            )
            tmp_agent._print_fn = lambda *a, **kw: None

            loop = asyncio.get_event_loop()
            compressed, _ = await loop.run_in_executor(
                None,
                lambda: tmp_agent._compress_context(msgs, "", approx_tokens=approx_tokens)
            )

            # _compress_context 已经在旧会话上调用 end_session()
            # （在 SQLite 中保留完整记录）并创建新的
            # session_id 用于继续。将压缩后的消息写入
            # 新会话，这样原始历史保持可搜索。
            new_session_id = tmp_agent.session_id
            if new_session_id != session_entry.session_id:
                session_entry.session_id = new_session_id
                self.session_store._save()

            self.session_store.rewrite_transcript(new_session_id, compressed)
            # 重置存储的令牌计数 — 转录本已更改，旧值已过时
            self.session_store.update_session(
                session_entry.session_key, last_prompt_tokens=0
            )
            new_count = len(compressed)
            new_tokens = estimate_messages_tokens_rough(compressed)

            return (
                f"🗜️ Compressed: {original_count} → {new_count} messages\n"
                f"~{approx_tokens:,} → ~{new_tokens:,} tokens"
            )
        except Exception as e:
            logger.warning("手动压缩失败：%s", e)
            return f"压缩失败：{e}"

    async def _handle_title_command(self, event: MessageEvent) -> str:
        """Handle /title command — set or show the current session's title."""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        session_id = session_entry.session_id

        if not self._session_db:
            return "会话数据库不可用。"

        # 确保会话在 SQLite 数据库中存在（如果这是新会话中的第一个命令，
        # 它可能只存在于 session_store 中）
        existing_title = self._session_db.get_session_title(session_id)
        if existing_title is None:
            # 会话在数据库中尚不存在 — 创建它
            try:
                self._session_db.create_session(
                    session_id=session_id,
                    source=source.platform.value if source.platform else "unknown",
                    user_id=source.user_id,
                )
            except Exception:
                pass  # 会话可能已存在，忽略错误

        title_arg = event.get_command_args().strip()
        if title_arg:
            # 在设置前清理标题
            try:
                sanitized = self._session_db.sanitize_title(title_arg)
            except ValueError as e:
                return f"⚠️ {e}"
            if not sanitized:
                return "⚠️ 标题清理后为空。请使用可打印字符。"
            # 设置标题
            try:
                if self._session_db.set_session_title(session_id, sanitized):
                    return f"✏️ 会话标题已设置：**{sanitized}**"
                else:
                    return "数据库中未找到该会话。"
            except ValueError as e:
                return f"⚠️ {e}"
        else:
            # 显示当前标题和会话 ID
            title = self._session_db.get_session_title(session_id)
            if title:
                return f"📌 会话：`{session_id}`\n标题：**{title}**"
            else:
                return f"📌 会话：`{session_id}`\n未设置标题。用法：`/title 我的会话名称`"

    async def _handle_resume_command(self, event: MessageEvent) -> str:
        """Handle /resume command — switch to a previously-named session."""
        if not self._session_db:
            return "会话数据库不可用。"

        source = event.source
        session_key = self._session_key_for_source(source)
        name = event.get_command_args().strip()

        if not name:
            # 列出该用户/平台最近的已命名会话
            try:
                user_source = source.platform.value if source.platform else None
                sessions = self._session_db.list_sessions_rich(
                    source=user_source, limit=10
                )
                titled = [s for s in sessions if s.get("title")]
                if not titled:
                    return (
                        "未找到已命名的会话。\n"
                        "使用 `/title 我的会话` 为当前会话命名，"
                        "然后 `/resume 我的会话` 可稍后返回。"
                    )
                lines = ["📋 **Named Sessions**\n"]
                for s in titled[:10]:
                    title = s["title"]
                    preview = s.get("preview", "")[:40]
                    preview_part = f" — _{preview}_" if preview else ""
                    lines.append(f"• **{title}**{preview_part}")
                lines.append("\n用法：`/resume <会话名称>`")
                return "\n".join(lines)
            except Exception as e:
                logger.debug("列出会话失败：%s", e)
                return f"无法列出会话：{e}"

        # 将名称解析为会话 ID
        target_id = self._session_db.resolve_session_by_title(name)
        if not target_id:
            return (
                f"未找到匹配 '**{name}**' 的会话。\n"
                "使用不带参数的 `/resume` 可查看可用会话。"
            )

        # 检查是否已在该会话上
        current_entry = self.session_store.get_or_create_session(source)
        if current_entry.session_id == target_id:
            return f"📌 已在会话 **{name}** 中。"

        # 切换前刷新当前会话的内存
        try:
            _flush_task = asyncio.create_task(
                self._async_flush_memories(current_entry.session_id)
            )
            self._background_tasks.add(_flush_task)
            _flush_task.add_done_callback(self._background_tasks.discard)
        except Exception as e:
            logger.debug("恢复时内存刷新失败：%s", e)

        # 清除此会话密钥的任何运行中的代理
        if session_key in self._running_agents:
            del self._running_agents[session_key]

        # 将会话条目切换到旧会话
        new_entry = self.session_store.switch_session(session_key, target_id)
        if not new_entry:
            return "切换会话失败。"

        # 获取标题以进行确认
        title = self._session_db.get_session_title(target_id) or name

        # 统计消息数以了解上下文
        history = self.session_store.load_transcript(target_id)
        msg_count = len([m for m in history if m.get("role") == "user"]) if history else 0
        msg_part = f" ({msg_count} message{'s' if msg_count != 1 else ''})" if msg_count else ""

        return f"↻ 已恢复会话 **{title}**{msg_part}。对话已恢复。"

    async def _handle_branch_command(self, event: MessageEvent) -> str:
        """Handle /branch [name] — fork the current session into a new independent copy.

        Copies conversation history to a new session so the user can explore
        a different approach without losing the original.
        Inspired by Claude Code's /branch command.
        """
        import uuid as _uuid

        if not self._session_db:
            return "会话数据库不可用。"

        source = event.source
        session_key = self._session_key_for_source(source)

        # 加载当前会话及其转录本
        current_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(current_entry.session_id)
        if not history:
            return "没有对话可以分支 — 请先发送一条消息。"

        branch_name = event.get_command_args().strip()

        # 生成新的会话 ID
        from datetime import datetime as _dt
        now = _dt.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        short_uuid = _uuid.uuid4().hex[:6]
        new_session_id = f"{timestamp_str}_{short_uuid}"

        # 确定分支标题
        if branch_name:
            branch_title = branch_name
        else:
            current_title = self._session_db.get_session_title(current_entry.session_id)
            base = current_title or "branch"
            branch_title = self._session_db.get_next_title_in_lineage(base)

        parent_session_id = current_entry.session_id

        # 创建带有父链接的新会话
        try:
            self._session_db.create_session(
                session_id=new_session_id,
                source=source.platform.value if source.platform else "gateway",
                model=(self.config.get("model", {}) or {}).get("default") if isinstance(self.config, dict) else None,
                parent_session_id=parent_session_id,
            )
        except Exception as e:
            logger.error("创建分支会话失败：%s", e)
            return f"创建分支失败：{e}"

        # 将对话历史复制到新会话
        for msg in history:
            try:
                self._session_db.append_message(
                    session_id=new_session_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                    tool_name=msg.get("tool_name") or msg.get("name"),
                    tool_calls=msg.get("tool_calls"),
                    tool_call_id=msg.get("tool_call_id"),
                    reasoning=msg.get("reasoning"),
                )
            except Exception:
                pass  # Best-effort copy

        # 设置标题
        try:
            self._session_db.set_session_title(new_session_id, branch_title)
        except Exception:
            pass

        # 将会话存储条目切换到新会话
        new_entry = self.session_store.switch_session(session_key, new_session_id)
        if not new_entry:
            return "分支已创建但切换失败。"

        # 驱逐此会话的任何缓存代理
        self._evict_cached_agent(session_key)

        msg_count = len([m for m in history if m.get("role") == "user"])
        return (
            f"⑂ 已分支到 **{branch_title}**"
            f" ({msg_count} 条消息已复制)\n"
            f"原始会话：`{parent_session_id}`\n"
            f"分支会话：`{new_session_id}`\n"
            f"使用 `/resume` 可切换回原始会话。"
        )

    async def _handle_usage_command(self, event: MessageEvent) -> str:
        """Handle /usage command -- show token usage for the session's last agent run."""
        source = event.source
        session_key = self._session_key_for_source(source)

        agent = self._running_agents.get(session_key)
        if agent and hasattr(agent, "session_total_tokens") and agent.session_api_calls > 0:
            lines = []

            # 首先是速率限制（当从提供商头信息可用时）
            rl_state = agent.get_rate_limit_state()
            if rl_state and rl_state.has_data:
                from agent.rate_limit_tracker import format_rate_limit_compact
                lines.append(f"⏱️ **Rate Limits:** {format_rate_limit_compact(rl_state)}")
                lines.append("")

            # 会话 Token 使用
            lines.append("📊 **会话 Token 使用**")
            lines.append(f"提示词（输入）：{agent.session_prompt_tokens:,}")
            lines.append(f"补全（输出）：{agent.session_completion_tokens:,}")
            lines.append(f"总计：{agent.session_total_tokens:,}")
            lines.append(f"API 调用：{agent.session_api_calls}")
            ctx = agent.context_compressor
            if ctx.last_prompt_tokens:
                pct = min(100, ctx.last_prompt_tokens / ctx.context_length * 100) if ctx.context_length else 0
                lines.append(f"上下文：{ctx.last_prompt_tokens:,} / {ctx.context_length:,} ({pct:.0f}%)")
            if ctx.compression_count:
                lines.append(f"压缩次数：{ctx.compression_count}")

            return "\n".join(lines)

        # 无运行中的代理 — 检查会话历史以获取粗略计数
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)
        if history:
            from agent.model_metadata import estimate_messages_tokens_rough
            msgs = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")]
            approx = estimate_messages_tokens_rough(msgs)
            return (
                f"📊 **会话信息**\n"
                f"消息数：{len(msgs)}\n"
                f"预估上下文：~{approx:,} tokens\n"
                f"_(活动对话期间可查看详细使用信息)_"
            )
        return "此会话没有可用使用数据。"

    async def _handle_insights_command(self, event: MessageEvent) -> str:
        """Handle /insights command -- show usage insights and analytics."""
        import asyncio as _asyncio

        args = event.get_command_args().strip()
        days = 30
        source = None

        # 解析简单参数: /insights 7  或  /insights --days 7
        if args:
            parts = args.split()
            i = 0
            while i < len(parts):
                if parts[i] == "--days" and i + 1 < len(parts):
                    try:
                        days = int(parts[i + 1])
                    except ValueError:
                        return f"无效的 --days 值：{parts[i + 1]}"
                    i += 2
                elif parts[i] == "--source" and i + 1 < len(parts):
                    source = parts[i + 1]
                    i += 2
                elif parts[i].isdigit():
                    days = int(parts[i])
                    i += 1
                else:
                    i += 1

        try:
            from kclaw_state import SessionDB
            from agent.insights import InsightsEngine

            loop = _asyncio.get_event_loop()

            def _run_insights():
                db = SessionDB()
                engine = InsightsEngine(db)
                report = engine.generate(days=days, source=source)
                result = engine.format_gateway(report)
                db.close()
                return result

            return await loop.run_in_executor(None, _run_insights)
        except Exception as e:
            logger.error("Insights 命令错误：%s", e, exc_info=True)
            return f"生成洞察时出错：{e}"

    async def _handle_reload_mcp_command(self, event: MessageEvent) -> str:
        """Handle /reload-mcp command -- disconnect and reconnect all MCP servers."""
        loop = asyncio.get_event_loop()
        try:
            from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools, _load_mcp_config, _servers, _lock

            # 在关闭前捕获旧服务器名称
            with _lock:
                old_servers = set(_servers.keys())

            # 关闭前读取新配置，这样我们就知道将添加/删除什么
                        # 关闭现有连接
            await loop.run_in_executor(None, shutdown_mcp_servers)

            # 通过发现工具重新连接（重新读取 config.yaml）
            new_tools = await loop.run_in_executor(None, discover_mcp_tools)

            # 计算发生了什么变化
            with _lock:
                connected_servers = set(_servers.keys())

            added = connected_servers - old_servers
            removed = old_servers - connected_servers
            reconnected = connected_servers & old_servers

            lines = ["🔄 **MCP Servers Reloaded**\n"]
            if reconnected:
                lines.append(f"♻️ Reconnected: {', '.join(sorted(reconnected))}")
            if added:
                lines.append(f"➕ Added: {', '.join(sorted(added))}")
            if removed:
                lines.append(f"➖ Removed: {', '.join(sorted(removed))}")
            if not connected_servers:
                lines.append("No MCP servers connected.")
            else:
                lines.append(f"\n🔧 {len(new_tools)} tool(s) available from {len(connected_servers)} server(s)")

            # 在会话历史末尾注入一条消息，以便
            # 模型在下一轮知道工具已更改。在处理完所有内容后附加。
            # all existing messages to preserve prompt-cache for the prefix.
            change_parts = []
            if added:
                change_parts.append(f"Added servers: {', '.join(sorted(added))}")
            if removed:
                change_parts.append(f"Removed servers: {', '.join(sorted(removed))}")
            if reconnected:
                change_parts.append(f"Reconnected servers: {', '.join(sorted(reconnected))}")
            tool_summary = f"{len(new_tools)} MCP tool(s) now available" if new_tools else "No MCP tools available"
            change_detail = ". ".join(change_parts) + ". " if change_parts else ""
            reload_msg = {
                "role": "user",
                "content": f"[SYSTEM: MCP servers have been reloaded. {change_detail}{tool_summary}. The tool list for this conversation has been updated accordingly.]",
            }
            try:
                session_entry = self.session_store.get_or_create_session(event.source)
                self.session_store.append_to_transcript(
                    session_entry.session_id, reload_msg
                )
            except Exception:
                pass  # Best-effort; don't fail the reload over a transcript write

            return "\n".join(lines)

        except Exception as e:
            logger.warning("MCP 重新加载失败：%s", e)
            return f"❌ MCP reload failed: {e}"

    # ------------------------------------------------------------------
    # /approve & /deny — 显式危险命令审批
    # ------------------------------------------------------------------

    _APPROVAL_TIMEOUT_SECONDS = 300  # 5 minutes

    async def _handle_approve_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /approve command — unblock waiting agent thread(s).

        The agent thread(s) are blocked inside tools/approval.py waiting for
        the user to respond.  This handler signals the event so the agent
        resumes and the terminal_tool executes the command inline — the same
        flow as the CLI's synchronous input() approval.

        Supports multiple concurrent approvals (parallel subagents,
        execute_code).  ``/approve`` resolves the oldest pending command;
        ``/approve all`` resolves every pending command at once.

        用法：
            /approve              — 批准最早的待处理命令一次
            /approve all          — 一次批准所有待处理命令
            /approve session      — 批准最早 + 记住本次会话
            /approve all session  — 批准所有 + 记住本次会话
            /approve always       — 批准最早 + 永久记住
            /approve all always   — 批准所有 + 永久记住
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        from tools.approval import (
            resolve_gateway_approval, has_blocking_approval,
        )

        if not has_blocking_approval(session_key):
            if session_key in self._pending_approvals:
                self._pending_approvals.pop(session_key)
                return "⚠️ 审批已过期（代理不再等待）。请让代理重试。"
            return "没有待批准的命令。"

        # 解析参数: 支持 "all"、"all session"、"all always"、"session"、"always"
        args = event.get_command_args().strip().lower().split()
        resolve_all = "all" in args
        remaining = [a for a in args if a != "all"]

        if any(a in ("always", "permanent", "permanently") for a in remaining):
            choice = "always"
            scope_msg = "（永久批准此模式）"
        elif any(a in ("session", "ses") for a in remaining):
            choice = "session"
            scope_msg = "（批准此会话）"
        else:
            choice = "once"
            scope_msg = ""

        count = resolve_gateway_approval(session_key, choice, resolve_all=resolve_all)
        if not count:
            return "没有待批准的命令。"

        # 恢复打字指示器 — 代理即将继续处理。
        _adapter = self.adapters.get(source.platform)
        if _adapter:
            _adapter.resume_typing_for_chat(source.chat_id)

        count_msg = f" ({count} 个命令)" if count > 1 else ""
        logger.info("用户通过 /approve 批准了 %d 个危险命令%s", count, scope_msg)
        return f"✅ 命令{'s' if count > 1 else ''} 已批准{scope_msg}{count_msg}。代理正在恢复..."

    async def _handle_deny_command(self, event: MessageEvent) -> str:
        """Handle /deny command — reject pending dangerous command(s).

        Signals blocked agent thread(s) with a 'deny' result so they receive
        a definitive BLOCKED message, same as the CLI deny flow.

        ``/deny`` denies the oldest; ``/deny all`` denies everything.
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        from tools.approval import (
            resolve_gateway_approval, has_blocking_approval,
        )

        if not has_blocking_approval(session_key):
            if session_key in self._pending_approvals:
                self._pending_approvals.pop(session_key)
                return "❌ 命令已拒绝（审批已过期）。"
            return "没有待拒绝的命令。"

        args = event.get_command_args().strip().lower()
        resolve_all = "all" in args

        count = resolve_gateway_approval(session_key, "deny", resolve_all=resolve_all)
        if not count:
            return "没有待拒绝的命令。"

        # 恢复打字指示器 — 代理继续（带有 BLOCKED 结果）。
        _adapter = self.adapters.get(source.platform)
        if _adapter:
            _adapter.resume_typing_for_chat(source.chat_id)

        count_msg = f" ({count} 个命令)" if count > 1 else ""
        logger.info("用户通过 /deny 拒绝了 %d 个危险命令", count)
        return f"❌ 命令{'s' if count > 1 else ''} 已拒绝{count_msg}。"

    # 允许 /update 的平台。ACP、API 服务器和 webhooks 是
    # 程序化接口，不应触发系统更新。
    _UPDATE_ALLOWED_PLATFORMS = frozenset({
        Platform.TELEGRAM, Platform.DISCORD, Platform.SLACK, Platform.WHATSAPP,
        Platform.SIGNAL, Platform.MATTERMOST, Platform.MATRIX,
        Platform.HOMEASSISTANT, Platform.EMAIL, Platform.SMS, Platform.DINGTALK,
        Platform.FEISHU, Platform.WECOM, Platform.BLUEBUBBLES, Platform.LOCAL,
    })

    async def _handle_update_command(self, event: MessageEvent) -> str:
        """Handle /update command — update KClaw Agent to the latest version.

        Spawns ``kclaw update`` in a detached session (via ``setsid``) so it
        survives the gateway restart that ``kclaw update`` may trigger. Marker
        files are written so either the current gateway process or the next one
        can notify the user when the update finishes.
        """
        import json
        import shutil
        import subprocess
        from datetime import datetime
        from kclaw_cli.config import is_managed, format_managed_message

        # 阻止非消息平台（API 服务器、webhooks、ACP）
        platform = event.source.platform
        if platform not in self._UPDATE_ALLOWED_PLATFORMS:
            return "✗ /update 仅在消息平台可用。请在终端运行 `kclaw update`。"

        if is_managed():
            return f"✗ {format_managed_message('update KClaw Agent')}"

        project_root = Path(__file__).parent.parent.resolve()
        git_dir = project_root / '.git'

        if not git_dir.exists():
            return "✗ 不是 git 仓库 — 无法更新。"

        kclaw_cmd = _resolve_kclaw_bin()
        if not kclaw_cmd:
            return (
                "✗ 无法定位 `kclaw` 命令。"
                "KClaw 正在运行，但更新命令无法在 PATH "
                "或当前 Python 解释器上找到可执行文件。"
                "请尝试在终端中手动运行 `kclaw update`。"
            )

        pending_path = _kclaw_home / ".update_pending.json"
        output_path = _kclaw_home / ".update_output.txt"
        exit_code_path = _kclaw_home / ".update_exit_code"
        session_key = self._session_key_for_source(event.source)
        pending = {
            "platform": event.source.platform.value,
            "chat_id": event.source.chat_id,
            "user_id": event.source.user_id,
            "session_key": session_key,
            "timestamp": datetime.now().isoformat(),
        }
        _tmp_pending = pending_path.with_suffix(".tmp")
        _tmp_pending.write_text(json.dumps(pending))
        _tmp_pending.replace(pending_path)
        exit_code_path.unlink(missing_ok=True)

        # 生成 `kclaw update --gateway` 分离进程，以便在 gateway 重启后继续运行。
        # --gateway 启用基于文件的 IPC 用于交互式提示（stash
        # restore, config migration）以便 gateway 可以将它们转发给
        # 用户而不是默默跳过它们。
        # 使用 setsid 以实现可移植的会话分离（在系统服务下
        # systemd-run --user 因缺少 D-Bus 会话而失败的地方也能工作）。
        # PYTHONUNBUFFERED 确保输出逐行刷新，以便
        # gateway 可以近实时地将其流式传输到消息程序。
        kclaw_cmd_str = " ".join(shlex.quote(part) for part in kclaw_cmd)
        update_cmd = (
            f"PYTHONUNBUFFERED=1 {kclaw_cmd_str} update --gateway"
            f" > {shlex.quote(str(output_path))} 2>&1; "
            f"status=$?; printf '%s' \"$status\" > {shlex.quote(str(exit_code_path))}"
        )
        try:
            setsid_bin = shutil.which("setsid")
            if setsid_bin:
                # 首选：setsid 创建新会话，完全分离
                subprocess.Popen(
                    [setsid_bin, "bash", "-c", update_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                # 回退：start_new_session=True 在子进程中调用 os.setsid()
                subprocess.Popen(
                    ["bash", "-c", update_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception as e:
            pending_path.unlink(missing_ok=True)
            exit_code_path.unlink(missing_ok=True)
            return f"✗ 启动更新失败：{e}"

        self._schedule_update_notification_watch()
        return "⚕ 正在启动 KClaw 更新… 我将在这里实时推送进度。"

    def _schedule_update_notification_watch(self) -> None:
        """Ensure a background task is watching for update completion."""
        existing_task = getattr(self, "_update_notification_task", None)
        if existing_task and not existing_task.done():
            return

        try:
            self._update_notification_task = asyncio.create_task(
                self._watch_update_progress()
            )
        except RuntimeError:
            logger.debug("跳过更新通知观察者：无运行中的事件循环")

    async def _watch_update_progress(
        self,
        poll_interval: float = 2.0,
        stream_interval: float = 4.0,
        timeout: float = 1800.0,
    ) -> None:
        """Watch ``kclaw update --gateway``, streaming output + forwarding prompts.

        Polls ``.update_output.txt`` for new content and sends chunks to the
        user periodically.  Detects ``.update_prompt.json`` (written by the
        update process when it needs user input) and forwards the prompt to
        the messenger.  The user's next message is intercepted by
        ``_handle_message`` and written to ``.update_response``.
        """
        import json
        import re as _re

        pending_path = _kclaw_home / ".update_pending.json"
        claimed_path = _kclaw_home / ".update_pending.claimed.json"
        output_path = _kclaw_home / ".update_output.txt"
        exit_code_path = _kclaw_home / ".update_exit_code"
        prompt_path = _kclaw_home / ".update_prompt.json"

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        # 解析用于发送消息的适配器和 chat_id
        adapter = None
        chat_id = None
        session_key = None
        for path in (claimed_path, pending_path):
            if path.exists():
                try:
                    pending = json.loads(path.read_text())
                    platform_str = pending.get("platform")
                    chat_id = pending.get("chat_id")
                    session_key = pending.get("session_key")
                    if platform_str and chat_id:
                        platform = Platform(platform_str)
                        adapter = self.adapters.get(platform)
                        # 如果未存储则回退会话密钥（旧待处理文件）
                        if not session_key:
                            session_key = f"{platform_str}:{chat_id}"
                    break
                except Exception:
                    pass

        if not adapter or not chat_id:
            logger.warning("更新观察者：无法解析适配器/chat_id，回退到仅完成通知")
            # 回退到旧行为：等待退出码并发送最终通知
            while (pending_path.exists() or claimed_path.exists()) and loop.time() < deadline:
                if exit_code_path.exists():
                    await self._send_update_notification()
                    return
                await asyncio.sleep(poll_interval)
            if (pending_path.exists() or claimed_path.exists()) and not exit_code_path.exists():
                exit_code_path.write_text("124")
                await self._send_update_notification()
            return

        def _strip_ansi(text: str) -> str:
            return _re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)

        bytes_sent = 0
        last_stream_time = loop.time()
        buffer = ""

        async def _flush_buffer() -> None:
            """Send buffered output to the user."""
            nonlocal buffer, last_stream_time
            if not buffer.strip():
                buffer = ""
                return
            # 分块以适应消息限制（Telegram: 4096，其他：从宽）
            clean = _strip_ansi(buffer).strip()
            buffer = ""
            last_stream_time = loop.time()
            if not clean:
                return
            # 如果太长则分割成块
            max_chunk = 3500
            chunks = [clean[i:i + max_chunk] for i in range(0, len(clean), max_chunk)]
            for chunk in chunks:
                try:
                    await adapter.send(chat_id, f"```\n{chunk}\n```")
                except Exception as e:
                    logger.debug("更新流发送失败：%s", e)

        while loop.time() < deadline:
            # 检查是否完成
            if exit_code_path.exists():
                # 读取任何剩余输出
                if output_path.exists():
                    try:
                        content = output_path.read_text()
                        if len(content) > bytes_sent:
                            buffer += content[bytes_sent:]
                            bytes_sent = len(content)
                    except OSError:
                        pass
                await _flush_buffer()

                # 发送最终状态
                try:
                    exit_code_raw = exit_code_path.read_text().strip() or "1"
                    exit_code = int(exit_code_raw)
                    if exit_code == 0:
                        await adapter.send(chat_id, "✅ KClaw 更新完成。")
                    else:
                        await adapter.send(chat_id, "❌ KClaw 更新失败（退出码 {}）。".format(exit_code))
                    logger.info("更新完成（退出码=%s），已通知 %s", exit_code, session_key)
                except Exception as e:
                    logger.warning("更新最终通知失败：%s", e)

                # 清理
                for p in (pending_path, claimed_path, output_path,
                          exit_code_path, prompt_path):
                    p.unlink(missing_ok=True)
                (_kclaw_home / ".update_response").unlink(missing_ok=True)
                self._update_prompt_pending.pop(session_key, None)
                return

            # 检查新输出
            if output_path.exists():
                try:
                    content = output_path.read_text()
                    if len(content) > bytes_sent:
                        buffer += content[bytes_sent:]
                        bytes_sent = len(content)
                except OSError:
                    pass

            # 定期刷新缓冲区
            if buffer.strip() and (loop.time() - last_stream_time) >= stream_interval:
                await _flush_buffer()

            # 检查提示
            if prompt_path.exists() and session_key:
                try:
                    prompt_data = json.loads(prompt_path.read_text())
                    prompt_text = prompt_data.get("prompt", "")
                    default = prompt_data.get("default", "")
                    if prompt_text:
                        # 首先刷新任何缓冲输出，以便用户在
                        # 看到提示前先看到上下文
                        await _flush_buffer()
                        # 首先尝试平台原生按钮（Discord、Telegram）
                        sent_buttons = False
                        if getattr(type(adapter), "send_update_prompt", None) is not None:
                            try:
                                await adapter.send_update_prompt(
                                    chat_id=chat_id,
                                    prompt=prompt_text,
                                    default=default,
                                    session_key=session_key,
                                )
                                sent_buttons = True
                            except Exception as btn_err:
                                logger.debug("基于按钮的更新提示失败：%s", btn_err)
                        if not sent_buttons:
                            default_hint = f" (default: {default})" if default else ""
                            await adapter.send(
                                chat_id,
                                f"⚕ **Update needs your input:**\n\n"
                                f"{prompt_text}{default_hint}\n\n"
                                f"Reply `/approve` (yes) or `/deny` (no), "
                                f"or type your answer directly."
                            )
                        self._update_prompt_pending[session_key] = True
                        logger.info("转发更新提示到 %s: %s", session_key, prompt_text[:80])
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("读取更新提示失败：%s", e)

            await asyncio.sleep(poll_interval)

        # 超时
        if not exit_code_path.exists():
            logger.warning("更新观察者在 %.0fs 后超时", timeout)
            exit_code_path.write_text("124")
            await _flush_buffer()
            try:
                await adapter.send(chat_id, "❌ KClaw 更新在 30 分钟后超时。")
            except Exception:
                pass
            for p in (pending_path, claimed_path, output_path,
                      exit_code_path, prompt_path):
                p.unlink(missing_ok=True)
            (_kclaw_home / ".update_response").unlink(missing_ok=True)
            self._update_prompt_pending.pop(session_key, None)

    async def _send_update_notification(self) -> bool:
        """If an update finished, notify the user.

        Returns False when the update is still running so a caller can retry
        later. Returns True after a definitive send/skip decision.

        This is the legacy notification path used when the streaming watcher
        cannot resolve the adapter (e.g. after a gateway restart where the
        platform hasn't reconnected yet).
        """
        import json
        import re as _re

        pending_path = _kclaw_home / ".update_pending.json"
        claimed_path = _kclaw_home / ".update_pending.claimed.json"
        output_path = _kclaw_home / ".update_output.txt"
        exit_code_path = _kclaw_home / ".update_exit_code"

        if not pending_path.exists() and not claimed_path.exists():
            return False

        cleanup = True
        active_pending_path = claimed_path
        try:
            if pending_path.exists():
                try:
                    pending_path.replace(claimed_path)
                except FileNotFoundError:
                    if not claimed_path.exists():
                        return True
            elif not claimed_path.exists():
                return True

            pending = json.loads(claimed_path.read_text())
            platform_str = pending.get("platform")
            chat_id = pending.get("chat_id")

            if not exit_code_path.exists():
                logger.info("更新通知延迟：更新仍在运行")
                cleanup = False
                active_pending_path = pending_path
                claimed_path.replace(pending_path)
                return False

            exit_code_raw = exit_code_path.read_text().strip() or "1"
            exit_code = int(exit_code_raw)

            # 读取捕获的更新输出
            output = ""
            if output_path.exists():
                output = output_path.read_text()

            # 解析适配器
            platform = Platform(platform_str)
            adapter = self.adapters.get(platform)

            if adapter and chat_id:
                # 剥离 ANSI 转义码以进行干净显示
                output = _re.sub(r'\x1b\[[0-9;]*m', '', output).strip()
                if output:
                    if len(output) > 3500:
                        output = "…" + output[-3500:]
                    if exit_code == 0:
                        msg = f"✅ KClaw 更新完成。\n\n```\n{output}\n```"
                    else:
                        msg = f"❌ KClaw 更新失败。\n\n```\n{output}\n```"
                else:
                    if exit_code == 0:
                        msg = "✅ KClaw 更新成功完成。"
                    else:
                        msg = "❌ KClaw 更新失败。请查看 gateway 日志或手动运行 `kclaw update` 获取详情。"
                await adapter.send(chat_id, msg)
                logger.info(
                    "Sent post-update notification to %s:%s (exit=%s)",
                    platform_str,
                    chat_id,
                    exit_code,
                )
        except Exception as e:
            logger.warning("更新后通知失败：%s", e)
        finally:
            if cleanup:
                active_pending_path.unlink(missing_ok=True)
                claimed_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                exit_code_path.unlink(missing_ok=True)

        return True

    def _set_session_env(self, context: SessionContext) -> None:
        """Set environment variables for the current session."""
        os.environ["KCLAW_SESSION_PLATFORM"] = context.source.platform.value
        os.environ["KCLAW_SESSION_CHAT_ID"] = context.source.chat_id
        if context.source.chat_name:
            os.environ["KCLAW_SESSION_CHAT_NAME"] = context.source.chat_name
        if context.source.thread_id:
            os.environ["KCLAW_SESSION_THREAD_ID"] = str(context.source.thread_id)
    
    def _clear_session_env(self) -> None:
        """Clear session environment variables."""
        for var in ["KCLAW_SESSION_PLATFORM", "KCLAW_SESSION_CHAT_ID", "KCLAW_SESSION_CHAT_NAME", "KCLAW_SESSION_THREAD_ID"]:
            if var in os.environ:
                del os.environ[var]
    
    async def _enrich_message_with_vision(
        self,
        user_text: str,
        image_paths: List[str],
    ) -> str:
        """
        Auto-analyze user-attached images with the vision tool and prepend
        the descriptions to the message text.

        Each image is analyzed with a general-purpose prompt.  The resulting
        description *and* the local cache path are injected so the model can:
          1. Immediately understand what the user sent (no extra tool call).
          2. Re-examine the image with vision_analyze if it needs more detail.

        Args:
            user_text:   The user's original caption / message text.
            image_paths: List of local file paths to cached images.

        Returns:
            The enriched message string with vision descriptions prepended.
        """
        from tools.vision_tools import vision_analyze_tool
        import json as _json

        analysis_prompt = (
            "Describe everything visible in this image in thorough detail. "
            "Include any text, code, data, objects, people, layout, colors, "
            "and any other notable visual information."
        )

        enriched_parts = []
        for path in image_paths:
            try:
                logger.debug("自动分析用户图片：%s", path)
                result_json = await vision_analyze_tool(
                    image_url=path,
                    user_prompt=analysis_prompt,
                )
                result = _json.loads(result_json)
                if result.get("success"):
                    description = result.get("analysis", "")
                    enriched_parts.append(
                        f"[The user sent an image~ Here's what I can see:\n{description}]\n"
                        f"[If you need a closer look, use vision_analyze with "
                        f"image_url: {path} ~]"
                    )
                else:
                    enriched_parts.append(
                        "[The user sent an image but I couldn't quite see it "
                        "this time (>_<) You can try looking at it yourself "
                        f"with vision_analyze using image_url: {path}]"
                    )
            except Exception as e:
                logger.error("视觉自动分析错误：%s", e)
                enriched_parts.append(
                    f"[The user sent an image but something went wrong when I "
                    f"tried to look at it~ You can try examining it yourself "
                    f"with vision_analyze using image_url: {path}]"
                )

        # 合并：先视觉描述，然后是用户的原始文本
        if enriched_parts:
            prefix = "\n\n".join(enriched_parts)
            if user_text:
                return f"{prefix}\n\n{user_text}"
            return prefix
        return user_text

    async def _enrich_message_with_transcription(
        self,
        user_text: str,
        audio_paths: List[str],
    ) -> str:
        """
        Auto-transcribe user voice/audio messages using the configured STT provider
        and prepend the transcript to the message text.

        Args:
            user_text:   The user's original caption / message text.
            audio_paths: List of local file paths to cached audio files.

        Returns:
            The enriched message string with transcriptions prepended.
        """
        if not getattr(self.config, "stt_enabled", True):
            disabled_note = "[The user sent voice message(s), but transcription is disabled in config."
            if self._has_setup_skill():
                disabled_note += (
                    " You have a skill called kclaw-setup that can help "
                    "users configure KClaw features including voice, tools, and more."
                )
            disabled_note += "]"
            if user_text:
                return f"{disabled_note}\n\n{user_text}"
            return disabled_note

        from tools.transcription_tools import transcribe_audio, get_stt_model_from_config
        import asyncio

        stt_model = get_stt_model_from_config()

        enriched_parts = []
        for path in audio_paths:
            try:
                logger.debug("转录用户语音：%s", path)
                result = await asyncio.to_thread(transcribe_audio, path, model=stt_model)
                if result["success"]:
                    transcript = result["transcript"]
                    enriched_parts.append(
                        f'[The user sent a voice message~ '
                        f'Here\'s what they said: "{transcript}"]'
                    )
                else:
                    error = result.get("error", "unknown error")
                    if (
                        "No STT provider" in error
                        or error.startswith("Neither VOICE_TOOLS_OPENAI_KEY nor OPENAI_API_KEY is set")
                    ):
                        _no_stt_note = (
                            "[The user sent a voice message but I can't listen "
                            "to it right now — no STT provider is configured. "
                            "A direct message has already been sent to the user "
                            "with setup instructions."
                        )
                        if self._has_setup_skill():
                            _no_stt_note += (
                                " You have a skill called kclaw-setup "
                                "that can help users configure KClaw features "
                                "including voice, tools, and more."
                            )
                        _no_stt_note += "]"
                        enriched_parts.append(_no_stt_note)
                    else:
                        enriched_parts.append(
                            "[The user sent a voice message but I had trouble "
                            f"transcribing it~ ({error})]"
                        )
            except Exception as e:
                logger.error("转录错误：%s", e)
                enriched_parts.append(
                    "[The user sent a voice message but something went wrong "
                    "when I tried to listen to it~ Let them know!]"
                )

        if enriched_parts:
            prefix = "\n\n".join(enriched_parts)
            # 当我们成功转录音频时，从 Discord 适配器中剥离空的占位符
            # 这是多余的。
            _placeholder = "(The user sent a message with no text content)"
            if user_text and user_text.strip() == _placeholder:
                return prefix
            if user_text:
                return f"{prefix}\n\n{user_text}"
            return prefix
        return user_text

    async def _run_process_watcher(self, watcher: dict) -> None:
        """
        Periodically check a background process and push updates to the user.

        Runs as an asyncio task. Stays silent when nothing changed.
        Auto-removes when the process exits or is killed.

        Notification mode (from ``display.background_process_notifications``):
          - ``all``    — running-output updates + final message
          - ``result`` — final completion message only
          - ``error``  — final message only when exit code != 0
          - ``off``    — no messages at all
        """
        from tools.process_registry import process_registry

        session_id = watcher["session_id"]
        interval = watcher["check_interval"]
        session_key = watcher.get("session_key", "")
        platform_name = watcher.get("platform", "")
        chat_id = watcher.get("chat_id", "")
        thread_id = watcher.get("thread_id", "")
        agent_notify = watcher.get("notify_on_complete", False)
        notify_mode = self._load_background_notifications_mode()

        logger.debug("进程观察者已启动：%s (每 %ss, notify=%s, agent_notify=%s)",
                      session_id, interval, notify_mode, agent_notify)

        if notify_mode == "off" and not agent_notify:
            # 仍然等待进程退出以便我们记录它，但不
            # push any messages to the user.
            while True:
                await asyncio.sleep(interval)
                session = process_registry.get(session_id)
                if session is None or session.exited:
                    break
            logger.debug("进程观察者已结束（静默）：%s", session_id)
            return

        last_output_len = 0
        while True:
            await asyncio.sleep(interval)

            session = process_registry.get(session_id)
            if session is None:
                break

            current_output_len = len(session.output_buffer)
            has_new_output = current_output_len > last_output_len
            last_output_len = current_output_len

            if session.exited:
                # --- Agent-triggered completion: inject synthetic message ---
                if agent_notify:
                    from tools.ansi_strip import strip_ansi
                    _out = strip_ansi(session.output_buffer[-2000:]) if session.output_buffer else ""
                    synth_text = (
                        f"[SYSTEM: Background process {session_id} completed "
                        f"(exit code {session.exit_code}).\n"
                        f"Command: {session.command}\n"
                        f"Output:\n{_out}]"
                    )
                    adapter = None
                    for p, a in self.adapters.items():
                        if p.value == platform_name:
                            adapter = a
                            break
                    if adapter and chat_id:
                        try:
                            from gateway.platforms.base import MessageEvent, MessageType
                            from gateway.session import SessionSource
                            from gateway.config import Platform
                            _platform_enum = Platform(platform_name)
                            _source = SessionSource(
                                platform=_platform_enum,
                                chat_id=chat_id,
                                thread_id=thread_id or None,
                            )
                            synth_event = MessageEvent(
                                text=synth_text,
                                message_type=MessageType.TEXT,
                                source=_source,
                                internal=True,
                            )
                            logger.info(
                                "Process %s finished — injecting agent notification for session %s",
                                session_id, session_key,
                            )
                            await adapter.handle_message(synth_event)
                        except Exception as e:
                            logger.error("代理通知注入错误：%s", e)
                    break

                # --- 普通纯文本通知 ---
                # 根据模式决定是否通知
                should_notify = (
                    notify_mode in ("all", "result")
                    or (notify_mode == "error" and session.exit_code not in (0, None))
                )
                if should_notify:
                    new_output = session.output_buffer[-1000:] if session.output_buffer else ""
                    message_text = (
                        f"[Background process {session_id} finished with exit code {session.exit_code}~ "
                        f"Here's the final output:\n{new_output}]"
                    )
                    adapter = None
                    for p, a in self.adapters.items():
                        if p.value == platform_name:
                            adapter = a
                            break
                    if adapter and chat_id:
                        try:
                            send_meta = {"thread_id": thread_id} if thread_id else None
                            await adapter.send(chat_id, message_text, metadata=send_meta)
                        except Exception as e:
                            logger.error("观察者传递错误：%s", e)
                break

            elif has_new_output and notify_mode == "all" and not agent_notify:
                # 有新输出可用 — 传递状态更新（仅在 "all" 模式下）
                # 跳过 agent_notify 观察者的定期更新（它们只关心完成）
                new_output = session.output_buffer[-500:] if session.output_buffer else ""
                message_text = (
                    f"[Background process {session_id} is still running~ "
                    f"New output:\n{new_output}]"
                )
                adapter = None
                for p, a in self.adapters.items():
                    if p.value == platform_name:
                        adapter = a
                        break
                if adapter and chat_id:
                    try:
                        send_meta = {"thread_id": thread_id} if thread_id else None
                        await adapter.send(chat_id, message_text, metadata=send_meta)
                    except Exception as e:
                        logger.error("观察者传递错误：%s", e)

        logger.debug("进程观察者已结束：%s", session_id)

    _MAX_INTERRUPT_DEPTH = 3  # 限制递归中断处理（#816）

    @staticmethod
    def _agent_config_signature(
        model: str,
        runtime: dict,
        enabled_toolsets: list,
        ephemeral_prompt: str,
    ) -> str:
        """从代理配置值计算稳定的字符串键。

        当此签名在消息之间发生变化时，缓存的 AIAgent 将被
        丢弃并重新构建。当它保持不变时，缓存的代理将被
        复用 — 为提示词缓存命中保留冻结的系统提示和工具模式。
        """
        import hashlib, json as _j

        # 使用完整凭据字符串的指纹而不是短
        # 前缀。OAuth/JWT 风格的令牌经常共享相同的前缀
        # （例如 "eyJhbGci"），如果只考虑前几个字符，
        # 可能会在切换认证时导致误命中缓存。
        _api_key = str(runtime.get("api_key", "") or "")
        _api_key_fingerprint = hashlib.sha256(_api_key.encode()).hexdigest() if _api_key else ""

        blob = _j.dumps(
            [
                model,
                _api_key_fingerprint,
                runtime.get("base_url", ""),
                runtime.get("provider", ""),
                runtime.get("api_mode", ""),
                sorted(enabled_toolsets) if enabled_toolsets else [],
                # reasoning_config 已排除 — 它是每条消息在缓存的代理上设置的，
                # 不影响系统提示或工具。
                ephemeral_prompt or "",
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def _evict_cached_agent(self, session_key: str) -> None:
        """Remove a cached agent for a session (called on /new, /model, etc)."""
        _lock = getattr(self, "_agent_cache_lock", None)
        if _lock:
            with _lock:
                self._agent_cache.pop(session_key, None)

    async def _run_agent(
        self,
        message: str,
        context_prompt: str,
        history: List[Dict[str, Any]],
        source: SessionSource,
        session_id: str,
        session_key: str = None,
        _interrupt_depth: int = 0,
        event_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        使用给定的消息和上下文运行代理。
        
        返回 run_conversation 的完整结果字典，包括：
          - "final_response": str（要发回的文本）
          - "messages": list（包含工具调用的完整对话）
          - "api_calls": int
          - "completed": bool
        
        在线程池中运行以避免阻塞事件循环。
        支持通过新消息进行中断。
        """
        from run_agent import AIAgent
        import queue
        
        user_config = _load_gateway_config()
        platform_key = _platform_config_key(source.platform)

        from kclaw_cli.tools_config import _get_platform_tools
        enabled_toolsets = sorted(_get_platform_tools(user_config, platform_key))

        # 应用工具预览长度配置（0 = 无限制）
        try:
            from agent.display import set_tool_preview_max_len
            _tpl = user_config.get("display", {}).get("tool_preview_length", 0)
            set_tool_preview_max_len(int(_tpl) if _tpl else 0)
        except Exception:
            pass

        # 工具进度模式来自 config.yaml: "all"、"new"、"verbose"、"off"
                    # 为向后兼容回退到环境变量。
        # YAML 1.1 将裸 `off` 解析为布尔值 False — 在 `or` 链之前
        # 规范化，这样它不会静默回退到 "all"。
        #
        # 每个平台的覆盖（display.tool_progress_overrides）优先于
        # 全局设置 — 例如 Signal 用户可以设置
        # tool_progress 为 "off" 同时保持 Telegram 为 "all"。
        _display_cfg = user_config.get("display", {})
        _overrides = _display_cfg.get("tool_progress_overrides", {})
        _raw_tp = _overrides.get(platform_key)
        if _raw_tp is None:
            _raw_tp = _display_cfg.get("tool_progress")
        if _raw_tp is False:
            _raw_tp = "off"
        progress_mode = (
            _raw_tp
            or os.getenv("KCLAW_TOOL_PROGRESS_MODE")
            or "all"
        )
        # 为 webhooks 禁用工具进度 - 它们不支持消息编辑，
        # 因此每个进度行将作为单独的消息发送。
        from gateway.config import Platform
        tool_progress_enabled = progress_mode != "off" and source.platform != Platform.WEBHOOK
        
        # 进度消息队列（线程安全）
        progress_queue = queue.Queue() if tool_progress_enabled else None
        last_tool = [None]  # 用于在闭包中跟踪的可变容器
        last_progress_msg = [None]  # 跟踪最后一条消息用于去重
        repeat_count = [0]  # 同一消息重复次数
        
        def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):
            """Callback invoked by agent on tool lifecycle events."""
            if not progress_queue:
                return

            # 仅处理 tool.started 事件（忽略 tool.completed、reasoning.available 等）
            if event_type not in ("tool.started",):
                return

            # "new" mode: only report when tool changes
            if progress_mode == "new" and tool_name == last_tool[0]:
                return
            last_tool[0] = tool_name
            
            # 使用主参数预览构建进度消息
            from agent.display import get_tool_emoji
            emoji = get_tool_emoji(tool_name, default="⚙️")
            
            # 详细模式：显示详细参数，遵守 tool_preview_length
            if progress_mode == "verbose":
                if args:
                    from agent.display import get_tool_preview_max_len
                    _pl = get_tool_preview_max_len()
                    import json as _json
                    args_str = _json.dumps(args, ensure_ascii=False, default=str)
                    _cap = _pl if _pl > 0 else 200
                    if len(args_str) > _cap:
                        args_str = args_str[:_cap - 3] + "..."
                    msg = f"{emoji} {tool_name}({list(args.keys())})\n{args_str}"
                elif preview:
                    msg = f"{emoji} {tool_name}: \"{preview}\""
                else:
                    msg = f"{emoji} {tool_name}..."
                progress_queue.put(msg)
                return
            
            # "all" / "new" 模式：短预览，遵循 tool_preview_length
            # 配置（未设置时默认 40 个字符以保持 gateway 消息
            # 简洁 — 与 CLI spinner 不同，这些会作为永久消息保留）。
            if preview:
                from agent.display import get_tool_preview_max_len
                _pl = get_tool_preview_max_len()
                _cap = _pl if _pl > 0 else 40
                if len(preview) > _cap:
                    preview = preview[:_cap - 3] + "..."
                msg = f"{emoji} {tool_name}: \"{preview}\""
            else:
                msg = f"{emoji} {tool_name}..."
            
            # 去重：折叠连续相同的进度消息。
            # 这在使用 execute_code 时很常见，因为模型会使用相同的
            # 代码迭代（相同的样板导入 → 相同的预览）。
            if msg == last_progress_msg[0]:
                repeat_count[0] += 1
                # 通过特殊的 "dedup" 队列消息更新 progress_lines 的最后一行
                progress_queue.put(("__dedup__", msg, repeat_count[0]))
                return
            last_progress_msg[0] = msg
            repeat_count[0] = 0
            
            progress_queue.put(msg)
        
        # 后台任务发送进度消息
        # 将工具行累积到一条可编辑的消息中。
        #
        # 线程元数据是平台特定的：
        # - Slack DM 线程需要 event_message_id 回退（回复线程）
        # - Telegram uses message_thread_id only for forum topics; passing a
        #   normal DM/group message id as thread_id causes send failures
        # - Other platforms should use explicit source.thread_id only
        if source.platform == Platform.SLACK:
            _progress_thread_id = source.thread_id or event_message_id
        else:
            _progress_thread_id = source.thread_id
        _progress_metadata = {"thread_id": _progress_thread_id} if _progress_thread_id else None

        async def send_progress_messages():
            if not progress_queue:
                return

            adapter = self.adapters.get(source.platform)
            if not adapter:
                return

            # 跳过不支持消息
            # 编辑的平台（例如 iMessage/BlueBubbles）— 每次进度更新
            # 都会变成单独的消息气泡，这样会很吵。
            from gateway.platforms.base import BasePlatformAdapter as _BaseAdapter
            if type(adapter).edit_message is _BaseAdapter.edit_message:
                while not progress_queue.empty():
                    try:
                        progress_queue.get_nowait()
                    except Exception:
                        break
                return

            progress_lines = []      # 累积的工具行
            progress_msg_id = None   # ID of the progress message to edit
            can_edit = True          # 一旦编辑失败则为 False（平台不支持）
            _last_edit_ts = 0.0      # 节流编辑以避免 Telegram 限流
            _PROGRESS_EDIT_INTERVAL = 1.5  # 编辑之间的最小秒数

            while True:
                try:
                    raw = progress_queue.get_nowait()

                    # 处理去重消息：用重复计数器更新最后一行
                    if isinstance(raw, tuple) and len(raw) == 3 and raw[0] == "__dedup__":
                        _, base_msg, count = raw
                        if progress_lines:
                            progress_lines[-1] = f"{base_msg} (×{count + 1})"
                        msg = progress_lines[-1] if progress_lines else base_msg
                    else:
                        msg = raw
                        progress_lines.append(msg)

                    # 节流编辑：将快速工具更新批量到更少
                    # API calls to avoid hitting Telegram flood control.
                    # (grammY auto-retry pattern: proactively rate-limit
                    # instead of reacting to 429s.)
                    _now = time.monotonic()
                    _remaining = _PROGRESS_EDIT_INTERVAL - (_now - _last_edit_ts)
                    if _remaining > 0:
                        # 等待节流间隔，然后循环回来在发送前
                        # 排出任何其他排队的消息
                        # a single batched edit.
                        await asyncio.sleep(_remaining)
                        continue

                    if can_edit and progress_msg_id is not None:
                        # 尝试编辑现有进度消息
                        full_text = "\n".join(progress_lines)
                        result = await adapter.edit_message(
                            chat_id=source.chat_id,
                            message_id=progress_msg_id,
                            content=full_text,
                        )
                        if not result.success:
                            _err = (getattr(result, "error", "") or "").lower()
                            if "flood" in _err or "retry after" in _err:
                                # 遇到限流 — 禁用进一步编辑，
                                # 仅对重要更新发送新消息。
                                # 不要阻塞 23 秒。
                                logger.info(
                                    "[%s] Progress edits disabled due to flood control",
                                    adapter.name,
                                )
                            can_edit = False
                            await adapter.send(chat_id=source.chat_id, content=msg, metadata=_progress_metadata)
                    else:
                        if can_edit:
                            # 第一个工具：将所有累积文本作为新消息发送
                            full_text = "\n".join(progress_lines)
                            result = await adapter.send(chat_id=source.chat_id, content=full_text, metadata=_progress_metadata)
                        else:
                            # 不支持编辑：仅发送这一行
                            result = await adapter.send(chat_id=source.chat_id, content=msg, metadata=_progress_metadata)
                        if result.success and result.message_id:
                            progress_msg_id = result.message_id

                    _last_edit_ts = time.monotonic()

                    # 恢复打字指示器
                    await asyncio.sleep(0.3)
                    await adapter.send_typing(source.chat_id, metadata=_progress_metadata)

                except queue.Empty:
                    await asyncio.sleep(0.3)
                except asyncio.CancelledError:
                    # 排出剩余排队的消息
                    while not progress_queue.empty():
                        try:
                            raw = progress_queue.get_nowait()
                            if isinstance(raw, tuple) and len(raw) == 3 and raw[0] == "__dedup__":
                                _, base_msg, count = raw
                                if progress_lines:
                                    progress_lines[-1] = f"{base_msg} (×{count + 1})"
                            else:
                                progress_lines.append(raw)
                        except Exception:
                            break
                    # 最终编辑所有剩余工具（仅在编辑有效时）
                    if can_edit and progress_lines and progress_msg_id:
                        full_text = "\n".join(progress_lines)
                        try:
                            await adapter.edit_message(
                                chat_id=source.chat_id,
                                message_id=progress_msg_id,
                                content=full_text,
                            )
                        except Exception:
                            pass
                    return
                except Exception as e:
                    logger.error("进度消息错误：%s", e)
                    await asyncio.sleep(1)
        
        # 我们需要共享代理实例以支持中断
        agent_holder = [None]  # 代理实例的可变容器
        result_holder = [None]  # 结果的可变容器
        tools_holder = [None]   # 工具定义的可变容器
        stream_consumer_holder = [None]  # 流消费者的可变容器
        
        # 桥接 sync step_callback → async hooks.emit 用于 agent:step 事件
        _loop_for_step = asyncio.get_event_loop()
        _hooks_ref = self.hooks

        def _step_callback_sync(iteration: int, prev_tools: list) -> None:
            try:
                # prev_tools 可能是 list[str] 或带有 "name"/"result"
                # 键的 list[dict]。规范化以保持 "tool_names" 向后兼容，
                # 适用于执行 ', '.join(tool_names)' 的用户编写钩子。
                _names: list[str] = []
                for _t in (prev_tools or []):
                    if isinstance(_t, dict):
                        _names.append(_t.get("name") or "")
                    else:
                        _names.append(str(_t))
                asyncio.run_coroutine_threadsafe(
                    _hooks_ref.emit("agent:step", {
                        "platform": source.platform.value if source.platform else "",
                        "user_id": source.user_id,
                        "session_id": session_id,
                        "iteration": iteration,
                        "tool_names": _names,
                        "tools": prev_tools,
                    }),
                    _loop_for_step,
                )
            except Exception as _e:
                logger.debug("agent:step 钩子错误：%s", _e)

        # 桥接 sync status_callback → async adapter.send 用于上下文压力
        _status_adapter = self.adapters.get(source.platform)
        _status_chat_id = source.chat_id
        _status_thread_metadata = {"thread_id": _progress_thread_id} if _progress_thread_id else None

        def _status_callback_sync(event_type: str, message: str) -> None:
            if not _status_adapter:
                return
            try:
                asyncio.run_coroutine_threadsafe(
                    _status_adapter.send(
                        _status_chat_id,
                        message,
                        metadata=_status_thread_metadata,
                    ),
                    _loop_for_step,
                )
            except Exception as _e:
                logger.debug("status_callback 错误 (%s): %s", event_type, _e)

        def run_sync():
            # 下面的条件重新赋值 `message`
            # （前置模型切换说明）使 Python 将其视为整个函数中的
            # 局部变量。`nonlocal` 让我们可以读取*并重新赋值*
            # 外层的 `_run_agent` 参数，而不会在之前的读取时
            # 触发 UnboundLocalError（在 `_resolve_turn_agent_config(message, …)` 处）。
            nonlocal message

            # 通过环境变量传递 session_key 到进程注册表，以便后台
            # 进程可以映射回此 gateway 会话
            os.environ["KCLAW_SESSION_KEY"] = session_key or ""

            # 从环境变量读取或使用默认值（与 CLI 相同）
            max_iterations = int(os.getenv("KCLAW_MAX_ITERATIONS", "90"))
            
            # 将平台枚举映射到代理理解的平台提示键。
            # Platform.LOCAL ("local") 映射到 "cli"；其他按原样传递。
            platform_key = "cli" if source.platform == Platform.LOCAL else source.platform.value
            
            # 将平台上下文与用户配置的临时系统提示合并
            combined_ephemeral = context_prompt or ""
            if self._ephemeral_system_prompt:
                combined_ephemeral = (combined_ephemeral + "\n\n" + self._ephemeral_system_prompt).strip()

            # 重新读取 .env 和配置以获取新凭证（gateway 生命周期长，
            # 密钥可能在不重启的情况下更改）。
            try:
                load_dotenv(_env_path, override=True, encoding="utf-8")
            except UnicodeDecodeError:
                load_dotenv(_env_path, override=True, encoding="latin-1")
            except Exception:
                pass

            model = _resolve_gateway_model(user_config)

            try:
                runtime_kwargs = _resolve_runtime_agent_kwargs()
            except Exception as exc:
                return {
                    "final_response": f"⚠️ Provider authentication failed: {exc}",
                    "messages": [],
                    "api_calls": 0,
                    "tools": [],
                }

            pr = self._provider_routing
            reasoning_config = self._load_reasoning_config()
            self._reasoning_config = reasoning_config
            # 如果启用则设置流消费者
            _stream_consumer = None
            _stream_delta_cb = None
            _scfg = getattr(getattr(self, 'config', None), 'streaming', None)
            if _scfg is None:
                from gateway.config import StreamingConfig
                _scfg = StreamingConfig()

            if _scfg.enabled and _scfg.transport != "off":
                try:
                    from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
                    _adapter = self.adapters.get(source.platform)
                    if _adapter:
                        _consumer_cfg = StreamConsumerConfig(
                            edit_interval=_scfg.edit_interval,
                            buffer_threshold=_scfg.buffer_threshold,
                            cursor=_scfg.cursor,
                        )
                        _stream_consumer = GatewayStreamConsumer(
                            adapter=_adapter,
                            chat_id=source.chat_id,
                            config=_consumer_cfg,
                            metadata={"thread_id": _progress_thread_id} if _progress_thread_id else None,
                        )
                        _stream_delta_cb = _stream_consumer.on_delta
                        stream_consumer_holder[0] = _stream_consumer
                except Exception as _sc_err:
                    logger.debug("Could not set up stream consumer: %s", _sc_err)

            turn_route = self._resolve_turn_agent_config(message, model, runtime_kwargs)

            # 检查代理缓存 — 重用上一条消息中的 AIAgent
            # 以保留冻结的系统提示和工具。
            # schemas for prompt cache hits.
            _sig = self._agent_config_signature(
                turn_route["model"],
                turn_route["runtime"],
                enabled_toolsets,
                combined_ephemeral,
            )
            agent = None
            _cache_lock = getattr(self, "_agent_cache_lock", None)
            _cache = getattr(self, "_agent_cache", None)
            if _cache_lock and _cache is not None:
                with _cache_lock:
                    cached = _cache.get(session_key)
                    if cached and cached[1] == _sig:
                        agent = cached[0]
                        logger.debug("Reusing cached agent for session %s", session_key)

            if agent is None:
                # 配置已更改或第一条消息 — 创建新代理
                agent = AIAgent(
                    model=turn_route["model"],
                    **turn_route["runtime"],
                    max_iterations=max_iterations,
                    quiet_mode=True,
                    verbose_logging=False,
                    enabled_toolsets=enabled_toolsets,
                    ephemeral_system_prompt=combined_ephemeral or None,
                    prefill_messages=self._prefill_messages or None,
                    reasoning_config=reasoning_config,
                    providers_allowed=pr.get("only"),
                    providers_ignored=pr.get("ignore"),
                    providers_order=pr.get("order"),
                    provider_sort=pr.get("sort"),
                    provider_require_parameters=pr.get("require_parameters", False),
                    provider_data_collection=pr.get("data_collection"),
                    session_id=session_id,
                    platform=platform_key,
                    user_id=source.user_id,
                    session_db=self._session_db,
                    fallback_model=self._fallback_model,
                )
                if _cache_lock and _cache is not None:
                    with _cache_lock:
                        _cache[session_key] = (agent, _sig)
                logger.debug("Created new agent for session %s (sig=%s)", session_key, _sig)

            # 每条消息状态 — 回调和推理配置每次
            # turn and must not be baked into the cached agent constructor.
            agent.tool_progress_callback = progress_callback if tool_progress_enabled else None
            agent.step_callback = _step_callback_sync if _hooks_ref.loaded_hooks else None
            agent.stream_delta_callback = _stream_delta_cb
            agent.status_callback = _status_callback_sync
            agent.reasoning_config = reasoning_config

            # 后台审查交付 — 向用户发送 "💾 Memory updated" 等
            def _bg_review_send(message: str) -> None:
                if not _status_adapter:
                    return
                try:
                    asyncio.run_coroutine_threadsafe(
                        _status_adapter.send(
                            _status_chat_id,
                            message,
                            metadata=_status_thread_metadata,
                        ),
                        _loop_for_step,
                    )
                except Exception as _e:
                    logger.debug("background_review_callback error: %s", _e)

            agent.background_review_callback = _bg_review_send

            # 存储代理引用以支持中断
            agent_holder[0] = agent
            # 捕获完整工具定义以用于转录日志
            tools_holder[0] = agent.tools if hasattr(agent, 'tools') else None
            
            # 将历史转换为代理格式。
            # 两种情况：
            #   1. 正常路径（来自转录本）：简单的 {role, content, timestamp} 字典
            #      - Strip timestamps, keep role+content
            #   2. Interrupt path (from agent result["messages"]): full agent messages
            #      that may include tool_calls, tool_call_id, reasoning, etc.
            #      - These must be passed through intact so the API sees valid
            #        assistant→tool sequences (dropping tool_calls causes 500 errors)
            agent_history = []
            for msg in history:
                role = msg.get("role")
                if not role:
                    continue
                
                # 跳过元数据条目（工具定义、会话信息）
                # — 这些用于转录日志，不是给 LLM 的
                if role in ("session_meta",):
                    continue
                
                # 跳过系统消息 — 代理重建自己的系统提示
                if role == "system":
                    continue
                
                # 富代理消息（tool_calls、工具结果）必须完整传递
                # 这样 API 才能看到有效的 assistant→tool 序列
                has_tool_calls = "tool_calls" in msg
                has_tool_call_id = "tool_call_id" in msg
                is_tool_message = role == "tool"
                
                if has_tool_calls or has_tool_call_id or is_tool_message:
                    clean_msg = {k: v for k, v in msg.items() if k != "timestamp"}
                    agent_history.append(clean_msg)
                else:
                    # 简单文本消息 - 只需要 role 和 content
                    content = msg.get("content")
                    if content:
                        # 标记跨平台镜像消息，以便代理知道其来源
                        if msg.get("mirror"):
                            mirror_src = msg.get("mirror_source", "another session")
                            content = f"[Delivered from {mirror_src}] {content}"
                        entry = {"role": role, "content": content}
                        # 保留助手消息上的推理字段，以便
                        # multi-turn reasoning context survives session reload.
                        # 代理的 _build_api_kwargs 将这些转换为
                        # 提供商特定格式（reasoning_content 等）。
                        if role == "assistant":
                            for _rkey in ("reasoning", "reasoning_details",
                                          "codex_reasoning_items"):
                                _rval = msg.get(_rkey)
                                if _rval:
                                    entry[_rkey] = _rval
                        agent_history.append(entry)
            
            # 收集历史中已有的 MEDIA 路径，以便我们可以在当前轮次中排除它们。
            # 这对压缩是安全的：
            # even if the message list shrinks, we know which paths are old.
            _history_media_paths: set = set()
            for _hm in agent_history:
                if _hm.get("role") in ("tool", "function"):
                    _hc = _hm.get("content", "")
                    if "MEDIA:" in _hc:
                        for _match in re.finditer(r'MEDIA:(\S+)', _hc):
                            _p = _match.group(1).strip().rstrip('",}')
                            if _p:
                                _history_media_paths.add(_p)
            
            # 注册每个会话的 gateway 审批回调，以便危险
            # 命令审批阻塞代理线程（镜像 CLI input()）。
            # 回调桥接 sync→async 以立即向用户发送审批请求。
            from tools.approval import (
                register_gateway_notify,
                reset_current_session_key,
                set_current_session_key,
                unregister_gateway_notify,
            )

            def _approval_notify_sync(approval_data: dict) -> None:
                """Send the approval request to the user from the agent thread.

                If the adapter supports interactive button-based approvals
                (e.g. Discord's ``send_exec_approval``), use that for a richer
                UX.  Otherwise fall back to a plain text message with
                ``/approve`` instructions.
                """
                # 在代理等待时暂停打字指示器
                # 用户审批。对 Slack 的 Assistant API 至关重要，
                # assistant_threads_setStatus 禁用组合框 — 用户在 "thinking..."
                # 活跃时无法输入 /approve。审批消息发送会自动清除 Slack
                # 状态；暂停可防止 _keep_typing 重新设置它。
                # 打字在 _handle_approve_command/_handle_deny_command 中恢复。
                _status_adapter.pause_typing_for_chat(_status_chat_id)

                cmd = approval_data.get("command", "")
                desc = approval_data.get("description", "dangerous command")

                # 首选基于按钮的审批（当适配器支持时）。
                # 检查*类*的方法，而不是实例 — 避免
                # 测试中 MagicMock 自动属性创建导致的误报。
                if getattr(type(_status_adapter), "send_exec_approval", None) is not None:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            _status_adapter.send_exec_approval(
                                chat_id=_status_chat_id,
                                command=cmd,
                                session_key=_approval_session_key,
                                description=desc,
                                metadata=_status_thread_metadata,
                            ),
                            _loop_for_step,
                        ).result(timeout=15)
                        return
                    except Exception as _e:
                        logger.warning(
                            "基于按钮的审批失败，回退到文本：%s", _e
                        )

                # 回退：纯文本审批提示
                cmd_preview = cmd[:200] + "..." if len(cmd) > 200 else cmd
                msg = (
                    f"⚠️ **Dangerous command requires approval:**\n"
                    f"```\n{cmd_preview}\n```\n"
                    f"Reason: {desc}\n\n"
                    f"Reply `/approve` to execute, `/approve session` to approve this pattern "
                    f"for the session, `/approve always` to approve permanently, or `/deny` to cancel."
                )
                try:
                    asyncio.run_coroutine_threadsafe(
                        _status_adapter.send(
                            _status_chat_id,
                            msg,
                            metadata=_status_thread_metadata,
                        ),
                        _loop_for_step,
                    ).result(timeout=15)
                except Exception as _e:
                    logger.error("Failed to send approval request: %s", _e)

            # 预先追加待处理的模型切换说明，以便模型知道切换
            _pending_notes = getattr(self, '_pending_model_notes', {})
            _msn = _pending_notes.pop(session_key, None) if session_key else None
            if _msn:
                message = _msn + "\n\n" + message

            _approval_session_key = session_key or ""
            _approval_session_token = set_current_session_key(_approval_session_key)
            register_gateway_notify(_approval_session_key, _approval_notify_sync)
            try:
                result = agent.run_conversation(message, conversation_history=agent_history, task_id=session_id)
            finally:
                unregister_gateway_notify(_approval_session_key)
                reset_current_session_key(_approval_session_token)
            result_holder[0] = result

            # 向流消费者发出信号，表明代理已完成
            if _stream_consumer is not None:
                _stream_consumer.finish()
            
            # 返回最终响应，或在出错时返回消息
            final_response = result.get("final_response")

            # 从用于此运行的代理实例中提取实际令牌计数
            _last_prompt_toks = 0
            _input_toks = 0
            _output_toks = 0
            _agent = agent_holder[0]
            if _agent and hasattr(_agent, "context_compressor"):
                _last_prompt_toks = getattr(_agent.context_compressor, "last_prompt_tokens", 0)
                _input_toks = getattr(_agent, "session_prompt_tokens", 0)
                _output_toks = getattr(_agent, "session_completion_tokens", 0)
            _resolved_model = getattr(_agent, "model", None) if _agent else None

            if not final_response:
                error_msg = f"⚠️ {result['error']}" if result.get("error") else "(No response generated)"
                return {
                    "final_response": error_msg,
                    "messages": result.get("messages", []),
                    "api_calls": result.get("api_calls", 0),
                    "tools": tools_holder[0] or [],
                    "history_offset": len(agent_history),
                    "last_prompt_tokens": _last_prompt_toks,
                    "input_tokens": _input_toks,
                    "output_tokens": _output_toks,
                    "model": _resolved_model,
                }
            
            # 扫描工具结果中需要作为原生音频/文件附件传递的
            # MEDIA:<path> 标签。TTS 工具在其 JSON 响应中嵌入 MEDIA: 标签，
            # 但模型的最终文本回复通常不包含它们。我们从工具结果中收集唯一标签，
            # 并追加任何不在最终响应中的标签，以便
            # 适配器的 extract_media() 可以精确找到并传递文件一次。
            #
            # 使用基于路径的去重（针对运行前收集的 _history_media_paths）
            # 而不是索引切片。即使在上下文压缩缩小消息列表后这也是安全的。（修复 #160）
            if "MEDIA:" not in final_response:
                media_tags = []
                has_voice_directive = False
                for msg in result.get("messages", []):
                    if msg.get("role") in ("tool", "function"):
                        content = msg.get("content", "")
                        if "MEDIA:" in content:
                            for match in re.finditer(r'MEDIA:(\S+)', content):
                                path = match.group(1).strip().rstrip('",}')
                                if path and path not in _history_media_paths:
                                    media_tags.append(f"MEDIA:{path}")
                            if "[[audio_as_voice]]" in content:
                                has_voice_directive = True
                
                if media_tags:
                    seen = set()
                    unique_tags = []
                    for tag in media_tags:
                        if tag not in seen:
                            seen.add(tag)
                            unique_tags.append(tag)
                    if has_voice_directive:
                        unique_tags.insert(0, "[[audio_as_voice]]")
                    final_response = final_response + "\n" + "\n".join(unique_tags)
            
            # 同步 session_id：代理可能在运行期间创建了新会话
            # （mid-run context compression，即 _compress_context 分割会话）。
            # 如果是这样，更新会话存储条目，以便下一条消息加载
            # 压缩后的转录本，而不是过时的压缩前版本。
            agent = agent_holder[0]
            _session_was_split = False
            if agent and session_key and hasattr(agent, 'session_id') and agent.session_id != session_id:
                _session_was_split = True
                logger.info(
                    "Session split detected: %s → %s (compression)",
                    session_id, agent.session_id,
                )
                entry = self.session_store._entries.get(session_key)
                if entry:
                    entry.session_id = agent.session_id
                    self.session_store._save()

            effective_session_id = getattr(agent, 'session_id', session_id) if agent else session_id

            # 当压缩创建新会话时，消息列表已
            # 缩短。使用原始历史偏移量会产生空的 new_messages 切片，
            # 导致 gateway 只写入一对 user/assistant — 丢失压缩摘要和尾部。
            # 重置为 0，以便 gateway 写入所有压缩消息。
            _effective_history_offset = 0 if _session_was_split else len(agent_history)

            # 在第一次交换后自动生成会话标题（非阻塞）
            if final_response and self._session_db:
                try:
                    from agent.title_generator import maybe_auto_title
                    all_msgs = result_holder[0].get("messages", []) if result_holder[0] else []
                    maybe_auto_title(
                        self._session_db,
                        effective_session_id,
                        message,
                        final_response,
                        all_msgs,
                    )
                except Exception:
                    pass

            return {
                "final_response": final_response,
                "last_reasoning": result.get("last_reasoning"),
                "messages": result_holder[0].get("messages", []) if result_holder[0] else [],
                "api_calls": result_holder[0].get("api_calls", 0) if result_holder[0] else 0,
                "tools": tools_holder[0] or [],
                "history_offset": _effective_history_offset,
                "last_prompt_tokens": _last_prompt_toks,
                "input_tokens": _input_toks,
                "output_tokens": _output_toks,
                "model": _resolved_model,
                "session_id": effective_session_id,
            }
        
        # 如果启用则启动进度消息发送器
        progress_task = None
        if tool_progress_enabled:
            progress_task = asyncio.create_task(send_progress_messages())

        # 启动流消费者任务 — 轮询消费者创建，因为它
        # 在代理构造后在 run_sync（线程池）中发生。
        stream_task = None

        async def _start_stream_consumer():
            """Wait for the stream consumer to be created, then run it."""
            for _ in range(200):  # 最多等待 10s
                if stream_consumer_holder[0] is not None:
                    await stream_consumer_holder[0].run()
                    return
                await asyncio.sleep(0.05)

        stream_task = asyncio.create_task(_start_stream_consumer())
        
        # 跟踪此会话的运行代理（用于中断支持）
        # 我们在代理创建后的回调中执行此操作
        async def track_agent():
            # 等待代理创建
            while agent_holder[0] is None:
                await asyncio.sleep(0.05)
            if session_key:
                self._running_agents[session_key] = agent_holder[0]
        
        tracking_task = asyncio.create_task(track_agent())
        
        # 监控来自适配器的中断（新消息到达）
        async def monitor_for_interrupt():
            adapter = self.adapters.get(source.platform)
            if not adapter or not session_key:
                return
            
            while True:
                await asyncio.sleep(0.2)  # 每 200ms 检查一次
                # 检查适配器是否有此会话的待处理中断。
                # 必须使用 session_key（build_session_key 输出）— 而不是
                # source.chat_id — 因为适配器存储中断事件
                # under the full session key.
                if hasattr(adapter, 'has_pending_interrupt') and adapter.has_pending_interrupt(session_key):
                    agent = agent_holder[0]
                    if agent:
                        pending_event = adapter.get_pending_message(session_key)
                        pending_text = pending_event.text if pending_event else None
                        logger.debug("Interrupt detected from adapter, signaling agent...")
                        agent.interrupt(pending_text)
                        break
        
        interrupt_monitor = asyncio.create_task(monitor_for_interrupt())

        # 长时间运行任务的定期 "仍在工作" 通知。
        # 每 10 分钟触发一次，以便用户知道代理没有死亡。
        _NOTIFY_INTERVAL = 600  # 10 minutes
        _notify_start = time.time()

        async def _notify_long_running():
            _notify_adapter = self.adapters.get(source.platform)
            if not _notify_adapter:
                return
            while True:
                await asyncio.sleep(_NOTIFY_INTERVAL)
                _elapsed_mins = int((time.time() - _notify_start) // 60)
                # 如果可用则包含代理活动上下文。
                _agent_ref = agent_holder[0]
                _status_detail = ""
                if _agent_ref and hasattr(_agent_ref, "get_activity_summary"):
                    try:
                        _a = _agent_ref.get_activity_summary()
                        _parts = [f"iteration {_a['api_call_count']}/{_a['max_iterations']}"]
                        if _a.get("current_tool"):
                            _parts.append(f"running: {_a['current_tool']}")
                        else:
                            _parts.append(_a.get("last_activity_desc", ""))
                        _status_detail = " — " + ", ".join(_parts)
                    except Exception:
                        pass
                try:
                    await _notify_adapter.send(
                        source.chat_id,
                        f"⏳ Still working... ({_elapsed_mins} min elapsed{_status_detail})",
                        metadata=_status_thread_metadata,
                    )
                except Exception as _ne:
                    logger.debug("Long-running notification error: %s", _ne)

        _notify_task = asyncio.create_task(_notify_long_running())

        try:
            # 在线程池中运行以不阻塞。使用基于*不活动*的超时，
            # 而不是挂钟限制：如果代理在积极调用工具/接收流令牌，
            # 它可以运行数小时，但如果 API 调用挂起或工具卡住且在
            # 配置的时间内没有活动，则会被捕获并终止。（#4815）
            #
            # 配置：agent.gateway_timeout 在 config.yaml 中，或
            # KCLAW_AGENT_TIMEOUT 环境变量（环境变量优先）。
            # 默认 1800s（30 分钟不活动）。0 = 无限制。
            _agent_timeout_raw = float(os.getenv("KCLAW_AGENT_TIMEOUT", 1800))
            _agent_timeout = _agent_timeout_raw if _agent_timeout_raw > 0 else None
            _agent_warning_raw = float(os.getenv("KCLAW_AGENT_TIMEOUT_WARNING", 900))
            _agent_warning = _agent_warning_raw if _agent_warning_raw > 0 else None
            _warning_fired = False
            loop = asyncio.get_event_loop()
            _executor_task = asyncio.ensure_future(
                loop.run_in_executor(None, run_sync)
            )

            _inactivity_timeout = False
            _POLL_INTERVAL = 5.0

            if _agent_timeout is None:
                # 无限制 — 只需等待结果。
                response = await _executor_task
            else:
                # 轮询循环：检查代理的内置活动跟踪器
                # (updated by _touch_activity() on every tool call, API
                # call, and stream delta) every few seconds.
                response = None
                while True:
                    done, _ = await asyncio.wait(
                        {_executor_task}, timeout=_POLL_INTERVAL
                    )
                    if done:
                        response = _executor_task.result()
                        break
                    # 代理仍在运行 — 检查不活动。
                    _agent_ref = agent_holder[0]
                    _idle_secs = 0.0
                    if _agent_ref and hasattr(_agent_ref, "get_activity_summary"):
                        try:
                            _act = _agent_ref.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    # 分阶段警告：在升级到完整超时前触发一次。
                    if (not _warning_fired and _agent_warning is not None
                            and _idle_secs >= _agent_warning):
                        _warning_fired = True
                        _warn_adapter = self.adapters.get(source.platform)
                        if _warn_adapter:
                            _elapsed_warn = int(_agent_warning // 60) or 1
                            _remaining_mins = int((_agent_timeout - _agent_warning) // 60) or 1
                            try:
                                await _warn_adapter.send(
                                    source.chat_id,
                                    f"⚠️ No activity for {_elapsed_warn} min. "
                                    f"If the agent does not respond soon, it will "
                                    f"be timed out in {_remaining_mins} min. "
                                    f"You can continue waiting or use /reset.",
                                    metadata=_status_thread_metadata,
                                )
                            except Exception as _warn_err:
                                logger.debug("Inactivity warning send error: %s", _warn_err)
                    if _idle_secs >= _agent_timeout:
                        _inactivity_timeout = True
                        break

            if _inactivity_timeout:
                # 从代理的活动跟踪器构建诊断摘要。
                _timed_out_agent = agent_holder[0]
                _activity = {}
                if _timed_out_agent and hasattr(_timed_out_agent, "get_activity_summary"):
                    try:
                        _activity = _timed_out_agent.get_activity_summary()
                    except Exception:
                        pass

                _last_desc = _activity.get("last_activity_desc", "unknown")
                _secs_ago = _activity.get("seconds_since_activity", 0)
                _cur_tool = _activity.get("current_tool")
                _iter_n = _activity.get("api_call_count", 0)
                _iter_max = _activity.get("max_iterations", 0)

                logger.error(
                    "代理空闲 %.0fs（超时 %.0fs）会话 %s "
                    "| last_activity=%s | iteration=%s/%s | tool=%s",
                    _secs_ago, _agent_timeout, session_key,
                    _last_desc, _iter_n, _iter_max,
                    _cur_tool or "none",
                )

                # 如果代理仍在运行则中断它，
                # pool worker is freed.
                if _timed_out_agent and hasattr(_timed_out_agent, "interrupt"):
                    _timed_out_agent.interrupt("Execution timed out (inactivity)")

                _timeout_mins = int(_agent_timeout // 60) or 1

                # 构建带有诊断上下文面向用户的消息。
                _diag_lines = [
                    f"⏱️ 代理已无活动 {_timeout_mins} 分钟 — 没有工具调用 "
                    f"或 API 响应。"
                ]
                if _cur_tool:
                    _diag_lines.append(
                        f"代理似乎卡在工具 `{_cur_tool}` 上 "
                        f"（距离上次活动 {_secs_ago:.0f} 秒，"
                        f"迭代 {_iter_n}/{_iter_max}）。"
                    )
                else:
                    _diag_lines.append(
                        f"上次活动：{_last_desc}（{_secs_ago:.0f} 秒前，"
                        f"迭代 {_iter_n}/{_iter_max}）。"
                        "代理可能一直在等待 API 响应。"
                    )
                _diag_lines.append(
                    "如需增加限制，请在 config.yaml 中设置 agent.gateway_timeout "
                    "（值以秒为单位，0 = 无限制）然后重启 gateway。\n"
                    "重试，或使用 /reset 重新开始。"
                )

                response = {
                    "final_response": "\n".join(_diag_lines),
                    "messages": result_holder[0].get("messages", []) if result_holder[0] else [],
                    "api_calls": _iter_n,
                    "tools": tools_holder[0] or [],
                    "history_offset": 0,
                    "failed": True,
                }

            # 跟踪回退模型状态：如果代理在此运行期间切换到
            # 回退模型，持久化它以便 /model 显示
            # 实际激活的模型而不是配置默认值。
            _agent = agent_holder[0]
            if _agent is not None and hasattr(_agent, 'model'):
                _cfg_model = _resolve_gateway_model()
                if _agent.model != _cfg_model:
                    self._effective_model = _agent.model
                    self._effective_provider = getattr(_agent, 'provider', None)
                    # 回退已激活 — 驱逐缓存的代理，以便下一条
                    # message starts fresh and retries the primary model.
                    self._evict_cached_agent(session_key)
                else:
                    # 主模型工作 — 清除任何过时的回退状态
                    self._effective_model = None
                    self._effective_provider = None

            # 检查我们是否被中断或有排队的消息（/queue）。
            result = result_holder[0]
            adapter = self.adapters.get(source.platform)
            
            # 从适配器获取待处理消息。
            # 使用 session_key（而不是 source.chat_id）来匹配适配器的存储键。
            pending = None
            if result and adapter and session_key:
                if result.get("interrupted"):
                    pending = _dequeue_pending_text(adapter, session_key)
                    if not pending and result.get("interrupt_message"):
                        pending = result.get("interrupt_message")
                else:
                    pending = _dequeue_pending_text(adapter, session_key)
                    if pending:
                        logger.debug("Processing queued message after agent completion: '%s...'", pending[:40])
            
            # 安全网：如果待处理文本是斜杠命令（例如 "/stop"、
            # "/new"），丢弃它 — 命令永远不应作为用户输入
            # 传递给代理。主要修复在 base.py 中（命令绕过
            # 活动会话守卫），但这捕获了命令文本通过
            # interrupt_message 回退泄漏的边缘情况。
            if pending and pending.strip().startswith("/"):
                _pending_parts = pending.strip().split(None, 1)
                _pending_cmd_word = _pending_parts[0][1:].lower() if _pending_parts else ""
                if _pending_cmd_word:
                    try:
                        from kclaw_cli.commands import resolve_command as _rc_pending
                        if _rc_pending(_pending_cmd_word):
                            logger.info(
                                "Discarding command '/%s' from pending queue — "
                                "commands must not be passed as agent input",
                                _pending_cmd_word,
                            )
                            pending = None
                    except Exception:
                        pass

            if pending:
                logger.debug("Processing pending message: '%s...'", pending[:40])
                
                # 清除适配器的中断事件，
                # 以便下一个 _run_agent 调用不会在新代理准备好前立即重新触发中断
                # even makes its first API call (this was causing an infinite loop).
                if adapter and hasattr(adapter, '_active_sessions') and session_key and session_key in adapter._active_sessions:
                    adapter._active_sessions[session_key].clear()
                
                # 限制递归深度以防止当用户在代理持续失败时发送多条消息
                # 导致资源耗尽。（#816）
                if _interrupt_depth >= self._MAX_INTERRUPT_DEPTH:
                    logger.warning(
                        "Interrupt recursion depth %d reached for session %s — "
                        "queueing message instead of recursing.",
                        _interrupt_depth, session_key,
                    )
                    # 将待处理消息排队，以便在下一轮正常处理
                    adapter = self.adapters.get(source.platform)
                    if adapter and hasattr(adapter, 'queue_message'):
                        adapter.queue_message(session_key, pending)
                    return result_holder[0] or {"final_response": response, "messages": history}

                was_interrupted = result.get("interrupted")
                if not was_interrupted:
                    # 正常完成后排队消息 — 在处理排队后续之前
                    # response before processing the queued follow-up.
                    # 如果流式传输已传递则跳过。
                    _sc = stream_consumer_holder[0]
                    _already_streamed = _sc and getattr(_sc, "already_sent", False)
                    first_response = result.get("final_response", "")
                    if first_response and not _already_streamed:
                        try:
                            await adapter.send(source.chat_id, first_response,
                                               metadata=getattr(event, "metadata", None))
                        except Exception as e:
                            logger.warning("Failed to send first response before queued message: %s", e)
                # 否则：被中断 — 丢弃被中断的响应（"Operation
                # interrupted." 只是噪音；用户已经知道他们发送了
                # 新消息）。

                # 使用更新后的历史处理待处理消息
                updated_history = result.get("messages", history)
                return await self._run_agent(
                    message=pending,
                    context_prompt=context_prompt,
                    history=updated_history,
                    source=source,
                    session_id=session_id,
                    session_key=session_key,
                    _interrupt_depth=_interrupt_depth + 1,
                )
        finally:
            # 停止进度发送器、中断监视器和通知任务
            if progress_task:
                progress_task.cancel()
            interrupt_monitor.cancel()
            _notify_task.cancel()

            # 等待流消费者完成其最终编辑
            if stream_task:
                try:
                    await asyncio.wait_for(stream_task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
            
            # 清理跟踪
            tracking_task.cancel()
            if session_key and session_key in self._running_agents:
                del self._running_agents[session_key]
            if session_key:
                self._running_agents_ts.pop(session_key, None)
            
            # 等待已取消的任务
            for task in [progress_task, interrupt_monitor, tracking_task, _notify_task]:
                if task:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        # 如果流式传输已传递响应，则标记它，
        # caller's send() is skipped (avoiding duplicate messages).
        _sc = stream_consumer_holder[0]
        if _sc and _sc.already_sent and isinstance(response, dict):
            response["already_sent"] = True
        
        return response


def _start_cron_ticker(stop_event: threading.Event, adapters=None, loop=None, interval: int = 60):
    """
    定期触发 cron 调度器的心跳线程。

    在 gateway 进程内运行，因此 cron 作业可以自动触发，
    无需单独的 `kclaw cron daemon` 或系统 cron 条目。

    当提供了 ``adapters`` 和 ``loop`` 时，会将它们传递到
    cron 投递路径，以便可以为 E2EE 房间使用实时适配器。

    还会每 5 分钟刷新一次频道目录，
    每小时清理一次图片/音频/文档缓存。
    """
    from cron.scheduler import tick as cron_tick
    from gateway.platforms.base import cleanup_image_cache, cleanup_document_cache

    IMAGE_CACHE_EVERY = 60   # ticks — once per hour at default 60s interval
    CHANNEL_DIR_EVERY = 5    # ticks — every 5 minutes

    logger.info("Cron ticker started (interval=%ds)", interval)
    tick_count = 0
    while not stop_event.is_set():
        try:
            cron_tick(verbose=False, adapters=adapters, loop=loop)
        except Exception as e:
            logger.debug("Cron tick error: %s", e)

        tick_count += 1

        if tick_count % CHANNEL_DIR_EVERY == 0 and adapters:
            try:
                from gateway.channel_directory import build_channel_directory
                build_channel_directory(adapters)
            except Exception as e:
                logger.debug("渠道目录刷新错误：%s", e)

        if tick_count % IMAGE_CACHE_EVERY == 0:
            try:
                removed = cleanup_image_cache(max_age_hours=24)
                if removed:
                    logger.info("图片缓存清理：删除了 %d 个过期文件", removed)
            except Exception as e:
                logger.debug("图片缓存清理错误：%s", e)
            try:
                removed = cleanup_document_cache(max_age_hours=24)
                if removed:
                    logger.info("文档缓存清理：删除了 %d 个过期文件", removed)
            except Exception as e:
                logger.debug("文档缓存清理错误：%s", e)

        stop_event.wait(timeout=interval)
    logger.info("Cron ticker 已停止")


async def start_gateway(config: Optional[GatewayConfig] = None, replace: bool = False, verbosity: Optional[int] = 0) -> bool:
    """
    启动 gateway 并运行直到被中断。

    这是运行 gateway 的主入口点。
    如果 gateway 成功运行则返回 True，启动失败则返回 False。
    返回 False 会导致非零退出码，以便 systemd 可以自动重启。

    参数：
        config: 可选 gateway 配置覆盖。
        replace: 如果为 True，则在启动前终止任何现有的 gateway 实例。
                 对于 systemd 服务很有用，可以避免在前一个进程尚未完全退出时的重启循环死锁。
    """
    # ── 重复实例保护 ──────────────────────────────────────
    # 防止两个 gateway 在同一 KCLAW_HOME 下运行。
    # PID 文件的作用域是 KCLAW_HOME，因此未来的多 profile
    # 设置（每个 profile 使用不同的 KCLAW_HOME）将自然
    # 允许并发实例而不会触发此保护。
    import time as _time
    from gateway.status import get_running_pid, remove_pid_file
    existing_pid = get_running_pid()
    if existing_pid is not None and existing_pid != os.getpid():
        if replace:
            logger.info(
                "Replacing existing gateway instance (PID %d) with --replace.",
                existing_pid,
            )
            try:
                os.kill(existing_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # 进程已消失
            except PermissionError:
                logger.error(
                    "权限不足，无法终止 PID %d。无法替换。",
                    existing_pid,
                )
                return False
            # 等待最多 10 秒让旧进程退出
            for _ in range(20):
                try:
                    os.kill(existing_pid, 0)
                    _time.sleep(0.5)
                except (ProcessLookupError, PermissionError):
                    break  # 进程已消失
            else:
                # 10 秒后仍存活 — 强制终止
                logger.warning(
                    "旧 gateway（PID %d）在 SIGTERM 后仍未退出，发送 SIGKILL。",
                    existing_pid,
                )
                try:
                    os.kill(existing_pid, signal.SIGKILL)
                    _time.sleep(0.5)
                except (ProcessLookupError, PermissionError):
                    pass
            remove_pid_file()
            # 同时释放旧进程留下的所有作用域锁。
            # 停止的（Ctrl+Z）进程在退出时不释放锁，
            # 这会留下过时的锁文件，阻止新 gateway 启动。
            try:
                from gateway.status import release_all_scoped_locks
                _released = release_all_scoped_locks()
                if _released:
                    logger.info("Released %d stale scoped lock(s) from old gateway.", _released)
            except Exception:
                pass
        else:
            kclaw_home = str(get_kclaw_home())
            logger.error(
                "另一个 gateway 实例已在运行（PID %d, KCLAW_HOME=%s）。"
                "使用 'kclaw gateway restart' 替换它，或先 'kclaw gateway stop'。",
                existing_pid, kclaw_home,
            )
            print(
                f"\n❌ Gateway 已在运行（PID {existing_pid}）。\n"
                f"   使用 'kclaw gateway restart' 替换它，\n"
                f"   或先 'kclaw gateway stop' 停止它。\n"
                f"   或使用 'kclaw gateway run --replace' 自动替换。\n"
            )
            return False

    # 同步捆绑的技能在 gateway 启动时（快速 — 跳过未更改的）
    try:
        from tools.skills_sync import sync_skills
        sync_skills(quiet=True)
    except Exception:
        pass

    # 集中日志 — agent.log（INFO+）和 errors.log（WARNING+）。
    # 幂等的，所以从 AIAgent.__init__ 的重复调用不会重复。
    from kclaw_logging import setup_logging
    log_dir = setup_logging(kclaw_home=_kclaw_home, mode="gateway")

    # Gateway 专用轮转日志 — 捕获所有 gateway 级别的消息
    # （会话管理、平台适配器、斜杠命令等）。
    from agent.redact import RedactingFormatter
    from kclaw_logging import _add_rotating_handler
    _add_rotating_handler(
        logging.getLogger(),
        log_dir / 'gateway.log',
        level=logging.INFO,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
        formatter=RedactingFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'),
    )

    # 可选 stderr 处理器 — 级别由 CLI 上的 -v/-q 标志驱动。
    # verbosity=None (-q/--quiet): 无 stderr 输出
    # verbosity=0    (默认): WARNING 及以上
    # verbosity=1    (-v): INFO 及以上
    # verbosity=2+   (-vv/-vvv): DEBUG
    if verbosity is not None:
        _stderr_level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
        _stderr_handler = logging.StreamHandler()
        _stderr_handler.setLevel(_stderr_level)
        _stderr_handler.setFormatter(RedactingFormatter('%(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(_stderr_handler)
        # 降低根日志级别（如果需要）以便 DEBUG 记录可以到达处理器
        if _stderr_level < logging.getLogger().level:
            logging.getLogger().setLevel(_stderr_level)

    runner = GatewayRunner(config)
    
    # 设置信号处理器
    def signal_handler():
        asyncio.create_task(runner.stop())
    
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass
    
    # 启动 gateway
    success = await runner.start()
    if not success:
        return False
    if runner.should_exit_cleanly:
        if runner.exit_reason:
            logger.error("Gateway 正在干净退出：%s", runner.exit_reason)
        return True
    
    # 编写 PID 文件以便 CLI 可以检测 gateway 是否正在运行
    import atexit
    from gateway.status import write_pid_file, remove_pid_file
    write_pid_file()
    atexit.register(remove_pid_file)
    
    # 启动后台 cron 心跳，以便定时作业可以自动触发。
    # 传递事件循环，以便 cron 投递可以使用实时适配器（E2EE 支持）。
    cron_stop = threading.Event()
    cron_thread = threading.Thread(
        target=_start_cron_ticker,
        args=(cron_stop,),
        kwargs={"adapters": runner.adapters, "loop": asyncio.get_running_loop()},
        daemon=True,
        name="cron-ticker",
    )
    cron_thread.start()
    
    # 等待关闭
    await runner.wait_for_shutdown()

    if runner.should_exit_with_failure:
        if runner.exit_reason:
            logger.error("Gateway 因故障退出：%s", runner.exit_reason)
        return False
    
    # 干净地停止 cron 心跳
    cron_stop.set()
    cron_thread.join(timeout=5)

    # 关闭 MCP 服务器连接
    try:
        from tools.mcp_tool import shutdown_mcp_servers
        shutdown_mcp_servers()
    except Exception:
        pass

    return True


def main():
    """Gateway 的 CLI 入口点。"""
    import argparse
    
    parser = argparse.ArgumentParser(description="KClaw Gateway - Multi-platform messaging")
    parser.add_argument("--config", "-c", help="Path to gateway config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    config = None
    if args.config:
        import json
        with open(args.config, encoding="utf-8") as f:
            data = json.load(f)
            config = GatewayConfig.from_dict(data)
    
    # 运行 gateway — 如果没有平台连接则退出代码为 1，
    # 以便 systemd Restart=on-failure 可以重试临时错误（如 DNS）
    success = asyncio.run(start_gateway(config))
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
