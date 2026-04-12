"""
网关的会话管理。

处理：
- 会话上下文跟踪（消息来自哪里）
- 会话存储（对话持久化到磁盘）
- 重置策略评估（何时开始新的）
- 动态系统提示词注入（代理知道其上下文）
"""

import hashlib
import logging
import os
import json
import re
import threading
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """返回当前本地时间。"""
    return datetime.now()


# ---------------------------------------------------------------------------
# PII redaction helpers
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"^\+?\d[\d\-\s]{6,}$")


def _hash_id(value: str) -> str:
    """标识符的确定性 12 字符十六进制哈希。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _hash_sender_id(value: str) -> str:
    """将发送者 ID 哈希为 ``user_<12hex>``。"""
    return f"user_{_hash_id(value)}"


def _hash_chat_id(value: str) -> str:
    """哈希聊天 ID 的数字部分，保留平台前缀。

    ``telegram:12345`` → ``telegram:<hash>``
    ``12345``          → ``<hash>``
    """
    colon = value.find(":")
    if colon > 0:
        prefix = value[:colon]
        return f"{prefix}:{_hash_id(value[colon + 1:])}"
    return _hash_id(value)


def _looks_like_phone(value: str) -> bool:
    """如果 *value* 看起来像电话号码（E.164 或类似），则返回 True。"""
    return bool(_PHONE_RE.match(value.strip()))

from .config import (
    Platform,
    GatewayConfig,
    SessionResetPolicy,  # noqa: F401 — re-exported via gateway/__init__.py
    HomeChannel,
)


@dataclass
class SessionSource:
    """
    描述消息来自哪里。
    
    此信息用于：
    1. 将响应路由回正确的地方
    2. 注入系统提示词的上下文
    3. 跟踪 cron 作业传递的来源
    """
    platform: Platform
    chat_id: str
    chat_name: Optional[str] = None
    chat_type: str = "dm"  # "dm", "group", "channel", "thread"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    thread_id: Optional[str] = None  # 对于论坛主题、Discord 线程等
    chat_topic: Optional[str] = None  # 频道主题/描述（Discord、Slack）
    user_id_alt: Optional[str] = None  # Signal UUID（电话号码的替代）
    chat_id_alt: Optional[str] = None  # Signal 群组内部 ID
    
    @property
    def description(self) -> str:
        """来源的人类可读描述。"""
        if self.platform == Platform.LOCAL:
            return "CLI terminal"
        
        parts = []
        if self.chat_type == "dm":
            parts.append(f"DM with {self.user_name or self.user_id or 'user'}")
        elif self.chat_type == "group":
            parts.append(f"group: {self.chat_name or self.chat_id}")
        elif self.chat_type == "channel":
            parts.append(f"channel: {self.chat_name or self.chat_id}")
        else:
            parts.append(self.chat_name or self.chat_id)
        
        if self.thread_id:
            parts.append(f"thread: {self.thread_id}")
        
        return ", ".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        d = {
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "chat_type": self.chat_type,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "thread_id": self.thread_id,
            "chat_topic": self.chat_topic,
        }
        if self.user_id_alt:
            d["user_id_alt"] = self.user_id_alt
        if self.chat_id_alt:
            d["chat_id_alt"] = self.chat_id_alt
        return d
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionSource":
        return cls(
            platform=Platform(data["platform"]),
            chat_id=str(data["chat_id"]),
            chat_name=data.get("chat_name"),
            chat_type=data.get("chat_type", "dm"),
            user_id=data.get("user_id"),
            user_name=data.get("user_name"),
            thread_id=data.get("thread_id"),
            chat_topic=data.get("chat_topic"),
            user_id_alt=data.get("user_id_alt"),
            chat_id_alt=data.get("chat_id_alt"),
        )
    
    @classmethod
    def local_cli(cls) -> "SessionSource":
        """创建代表本地 CLI 的来源。"""
        return cls(
            platform=Platform.LOCAL,
            chat_id="cli",
            chat_name="CLI terminal",
            chat_type="dm",
        )


@dataclass
class SessionContext:
    """
    会话的完整上下文，用于动态系统提示词注入。
    
    代理接收此信息以了解：
    - 消息来自哪里
    - 哪些平台已连接
    - 可以在哪里传递计划任务输出
    """
    source: SessionSource
    connected_platforms: List[Platform]
    home_channels: Dict[Platform, HomeChannel]
    
    # Session metadata
    session_key: str = ""
    session_id: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "connected_platforms": [p.value for p in self.connected_platforms],
            "home_channels": {
                p.value: hc.to_dict() for p, hc in self.home_channels.items()
            },
            "session_key": self.session_key,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


_PII_SAFE_PLATFORMS = frozenset({
    Platform.WHATSAPP,
    Platform.SIGNAL,
    Platform.TELEGRAM,
    Platform.BLUEBUBBLES,
})
"""用户 ID 可以安全地匿名化的平台（没有消息中提及系统
需要原始 ID）。Discord 被排除，因为提及使用 ``<@user_id>``
且 LLM 需要真实 ID 来标记用户。"""


def build_session_context_prompt(
    context: SessionContext,
    *,
    redact_pii: bool = False,
) -> str:
    """
    构建告诉代理其上下文（包括来源、已连接平台和传递选项）的动态系统提示词部分。
    
    当 *redact_pii* 为 True **且** 来源平台在
    ``_PII_SAFE_PLATFORMS`` 中时，电话号码会被剥离，用户/聊天 ID
    会在发送到 LLM 之前被替换为确定性哈希。
    Discord 等平台被排除，因为提及需要真实 ID。
    路由仍然使用原始值（它们留在 SessionSource 中）。
    """
    # Only apply redaction on platforms where IDs aren't needed for mentions
    redact_pii = redact_pii and context.source.platform in _PII_SAFE_PLATFORMS
    lines = [
        "## Current Session Context",
        "",
    ]
    
    # Source info
    platform_name = context.source.platform.value.title()
    if context.source.platform == Platform.LOCAL:
        lines.append(f"**Source:** {platform_name} (the machine running this agent)")
    else:
        # Build a description that respects PII redaction
        src = context.source
        if redact_pii:
            # Build a safe description without raw IDs
            _uname = src.user_name or (
                _hash_sender_id(src.user_id) if src.user_id else "user"
            )
            _cname = src.chat_name or _hash_chat_id(src.chat_id)
            if src.chat_type == "dm":
                desc = f"DM with {_uname}"
            elif src.chat_type == "group":
                desc = f"group: {_cname}"
            elif src.chat_type == "channel":
                desc = f"channel: {_cname}"
            else:
                desc = _cname
        else:
            desc = src.description
        lines.append(f"**Source:** {platform_name} ({desc})")
    
    # Channel topic (if available - provides context about the channel's purpose)
    if context.source.chat_topic:
        lines.append(f"**Channel Topic:** {context.source.chat_topic}")

    # User identity.
    # In shared thread sessions (non-DM with thread_id), multiple users
    # contribute to the same conversation.  Don't pin a single user name
    # in the system prompt — it changes per-turn and would bust the prompt
    # cache.  Instead, note that this is a multi-user thread; individual
    # sender names are prefixed on each user message by the gateway.
    _is_shared_thread = (
        context.source.chat_type != "dm"
        and context.source.thread_id
    )
    if _is_shared_thread:
        lines.append(
            "**Session type:** Multi-user thread — messages are prefixed "
            "with [sender name]. Multiple users may participate."
        )
    elif context.source.user_name:
        lines.append(f"**User:** {context.source.user_name}")
    elif context.source.user_id:
        uid = context.source.user_id
        if redact_pii:
            uid = _hash_sender_id(uid)
        lines.append(f"**User ID:** {uid}")
    
    # Platform-specific behavioral notes
    if context.source.platform == Platform.SLACK:
        lines.append("")
        lines.append(
            "**Platform notes:** You are running inside Slack. "
            "You do NOT have access to Slack-specific APIs — you cannot search "
            "channel history, pin/unpin messages, manage channels, or list users. "
            "Do not promise to perform these actions. If the user asks, explain "
            "that you can only read messages sent directly to you and respond."
        )
    elif context.source.platform == Platform.DISCORD:
        lines.append("")
        lines.append(
            "**Platform notes:** You are running inside Discord. "
            "You do NOT have access to Discord-specific APIs — you cannot search "
            "channel history, pin messages, manage roles, or list server members. "
            "Do not promise to perform these actions. If the user asks, explain "
            "that you can only read messages sent directly to you and respond."
        )

    # Connected platforms
    platforms_list = ["local (files on this machine)"]
    for p in context.connected_platforms:
        if p != Platform.LOCAL:
            platforms_list.append(f"{p.value}: Connected ✓")
    
    lines.append(f"**Connected Platforms:** {', '.join(platforms_list)}")
    
    # Home channels
    if context.home_channels:
        lines.append("")
        lines.append("**Home Channels (default destinations):**")
        for platform, home in context.home_channels.items():
            hc_id = _hash_chat_id(home.chat_id) if redact_pii else home.chat_id
            lines.append(f"  - {platform.value}: {home.name} (ID: {hc_id})")
    
    # Delivery options for scheduled tasks
    lines.append("")
    lines.append("**Delivery options for scheduled tasks:**")
    
    # Origin delivery
    if context.source.platform == Platform.LOCAL:
        lines.append("- `\"origin\"` → Local output (saved to files)")
    else:
        _origin_label = context.source.chat_name or (
            _hash_chat_id(context.source.chat_id) if redact_pii else context.source.chat_id
        )
        lines.append(f"- `\"origin\"` → Back to this chat ({_origin_label})")
    
    # Local always available
    lines.append("- `\"local\"` → Save to local files only (~/.kclaw/cron/output/)")
    
    # Platform home channels
    for platform, home in context.home_channels.items():
        lines.append(f"- `\"{platform.value}\"` → Home channel ({home.name})")
    
    # Note about explicit targeting
    lines.append("")
    lines.append("*For explicit targeting, use `\"platform:chat_id\"` format if the user provides a specific chat ID.*")
    
    return "\n".join(lines)


@dataclass
class SessionEntry:
    """
    会话存储中的条目。
    
    将会话键映射到其当前会话 ID 和元数据。
    """
    session_key: str
    session_id: str
    created_at: datetime
    updated_at: datetime
    
    # Origin metadata for delivery routing
    origin: Optional[SessionSource] = None
    
    # 显示元数据
    display_name: Optional[str] = None
    platform: Optional[Platform] = None
    chat_type: str = "dm"
    
    # Token 跟踪
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_status: str = "unknown"
    
    # 最后 API 报告的提示词 token 数（用于准确的压缩预检查）
    last_prompt_tokens: int = 0
    
    # 当会话创建是因为前一个已过期时设置；
    # 被消息处理程序消费一次以注入上下文通知
    was_auto_reset: bool = False
    auto_reset_reason: Optional[str] = None  # "idle" 或 "daily"
    reset_had_activity: bool = False  # 过期会话是否有任何消息
    
    # 由后台过期监视器在成功刷新
    # 此会话的内存后设置。持久化到 sessions.json 以便标志
    # 在网关重启后仍然存在（旧的内存 _pre_flushed_sessions
    # 集在重启时丢失，导致冗余重新刷新）。
    memory_flushed: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "session_key": self.session_key,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "display_name": self.display_name,
            "platform": self.platform.value if self.platform else None,
            "chat_type": self.chat_type,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "last_prompt_tokens": self.last_prompt_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cost_status": self.cost_status,
            "memory_flushed": self.memory_flushed,
        }
        if self.origin:
            result["origin"] = self.origin.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionEntry":
        origin = None
        if "origin" in data and data["origin"]:
            origin = SessionSource.from_dict(data["origin"])
        
        platform = None
        if data.get("platform"):
            try:
                platform = Platform(data["platform"])
            except ValueError as e:
                logger.debug("Unknown platform value %r: %s", data["platform"], e)
        
        return cls(
            session_key=data["session_key"],
            session_id=data["session_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            origin=origin,
            display_name=data.get("display_name"),
            platform=platform,
            chat_type=data.get("chat_type", "dm"),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            cache_write_tokens=data.get("cache_write_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            last_prompt_tokens=data.get("last_prompt_tokens", 0),
            estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
            cost_status=data.get("cost_status", "unknown"),
            memory_flushed=data.get("memory_flushed", False),
        )


def build_session_key(
    source: SessionSource,
    group_sessions_per_user: bool = True,
    thread_sessions_per_user: bool = False,
) -> str:
    """从消息来源构建确定性会话键。

    这是会话键构建的单一真实来源。

    私信规则：
      - 私信在有 chat_id 时包含 chat_id，因此每个私人对话都是隔离的。
      - thread_id 进一步区分同一私信聊天中的线程私信。
      - 没有 chat_id 时，thread_id 作为最佳回退使用。
      - 没有 thread_id 或 chat_id 时，私信共享一个会话。

    群组/频道规则：
      - chat_id 标识父群组/频道。
      - 当 user_id/user_id_alt 可用时，user_id/user_id_alt 在该父聊天中隔离参与者，
        当 ``group_sessions_per_user`` 启用时。
      - thread_id 在该父聊天中区分线程。当
        ``thread_sessions_per_user`` 为 False（默认）时，线程在所有
        参与者之间 *共享* — user_id 未附加，因此线程中的每个用户
        共享一个会话。这是线程化
        对话（Telegram 论坛主题、Discord 线程、Slack 线程）的预期 UX。
      - 没有参与者标识符，或隔离被禁用时，消息回退到每个聊天的
        一个共享会话。
      - 没有标识符时，消息回退到每个平台/chat_type 一个会话。
    """
    platform = source.platform.value
    if source.chat_type == "dm":
        if source.chat_id:
            if source.thread_id:
                return f"agent:main:{platform}:dm:{source.chat_id}:{source.thread_id}"
            return f"agent:main:{platform}:dm:{source.chat_id}"
        if source.thread_id:
            return f"agent:main:{platform}:dm:{source.thread_id}"
        return f"agent:main:{platform}:dm"

    participant_id = source.user_id_alt or source.user_id
    key_parts = ["agent:main", platform, source.chat_type]

    if source.chat_id:
        key_parts.append(source.chat_id)
    if source.thread_id:
        key_parts.append(source.thread_id)

    # In threads, default to shared sessions (all participants see the same
    # conversation).  Per-user isolation only applies when explicitly enabled
    # via thread_sessions_per_user, or when there is no thread (regular group).
    isolate_user = group_sessions_per_user
    if source.thread_id and not thread_sessions_per_user:
        isolate_user = False

    if isolate_user and participant_id:
        key_parts.append(str(participant_id))

    return ":".join(key_parts)


class SessionStore:
    """
    管理会话存储和检索。
    
    使用 SQLite（通过 SessionDB）存储会话元数据和消息记录。
    如果 SQLite 不可用，则回退到传统 JSONL 文件。
    """
    
    def __init__(self, sessions_dir: Path, config: GatewayConfig,
                 has_active_processes_fn=None,
                 on_auto_reset=None):
        self.sessions_dir = sessions_dir
        self.config = config
        self._entries: Dict[str, SessionEntry] = {}
        self._loaded = False
        self._lock = threading.Lock()
        self._has_active_processes_fn = has_active_processes_fn
        
        # 初始化 SQLite 会话数据库
        self._db = None
        try:
            from kclaw_state import SessionDB
            self._db = SessionDB()
        except Exception as e:
            print(f"[网关] 警告：SQLite 会话存储不可用，回退到 JSONL: {e}")
    
    def _ensure_loaded(self) -> None:
        """如果尚未加载，则从磁盘加载会话索引。"""
        with self._lock:
            self._ensure_loaded_locked()

    def _ensure_loaded_locked(self) -> None:
        """从磁盘加载会话索引。必须在持有 self._lock 的情况下调用。"""
        if self._loaded:
            return

        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions_file = self.sessions_dir / "sessions.json"

        if sessions_file.exists():
            try:
                with open(sessions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key, entry_data in data.items():
                        try:
                            self._entries[key] = SessionEntry.from_dict(entry_data)
                        except (ValueError, KeyError):
                            # Skip entries with unknown/removed platform values
                            continue
            except Exception as e:
                print(f"[gateway] Warning: Failed to load sessions: {e}")

        self._loaded = True
    
    def _save(self) -> None:
        """将会话索引保存到磁盘（保留用于会话键 → ID 映射）。"""
        import tempfile
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions_file = self.sessions_dir / "sessions.json"

        data = {key: entry.to_dict() for key, entry in self._entries.items()}
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.sessions_dir), suffix=".tmp", prefix=".sessions_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, sessions_file)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.debug("Could not remove temp file %s: %s", tmp_path, e)
            raise
    
    def _generate_session_key(self, source: SessionSource) -> str:
        """从来源生成会话键。"""
        return build_session_key(
            source,
            group_sessions_per_user=getattr(self.config, "group_sessions_per_user", True),
            thread_sessions_per_user=getattr(self.config, "thread_sessions_per_user", False),
        )
    
    def _is_session_expired(self, entry: SessionEntry) -> bool:
        """根据其重置策略检查会话是否已过期。
        
        仅从条目本身工作 — 无需 SessionSource。
        被后台过期监视器用于主动刷新内存。
        有活跃后台进程的会话永远不会被视为过期。
        """
        if self._has_active_processes_fn:
            if self._has_active_processes_fn(entry.session_key):
                return False

        policy = self.config.get_reset_policy(
            platform=entry.platform,
            session_type=entry.chat_type,
        )

        if policy.mode == "none":
            return False

        now = _now()

        if policy.mode in ("idle", "both"):
            idle_deadline = entry.updated_at + timedelta(minutes=policy.idle_minutes)
            if now > idle_deadline:
                return True

        if policy.mode in ("daily", "both"):
            today_reset = now.replace(
                hour=policy.at_hour,
                minute=0, second=0, microsecond=0,
            )
            if now.hour < policy.at_hour:
                today_reset -= timedelta(days=1)
            if entry.updated_at < today_reset:
                return True

        return False

    def _should_reset(self, entry: SessionEntry, source: SessionSource) -> Optional[str]:
        """
        根据策略检查会话是否应该重置。
        
        如果需要重置则返回重置原因（"idle" 或 "daily"），
        如果会话仍然有效则返回 None。
        
        有活跃后台进程的会话永远不会被重置。
        """
        if self._has_active_processes_fn:
            session_key = self._generate_session_key(source)
            if self._has_active_processes_fn(session_key):
                return None

        policy = self.config.get_reset_policy(
            platform=source.platform,
            session_type=source.chat_type
        )
        
        if policy.mode == "none":
            return None
        
        now = _now()
        
        if policy.mode in ("idle", "both"):
            idle_deadline = entry.updated_at + timedelta(minutes=policy.idle_minutes)
            if now > idle_deadline:
                return "idle"
        
        if policy.mode in ("daily", "both"):
            today_reset = now.replace(
                hour=policy.at_hour, 
                minute=0, 
                second=0, 
                microsecond=0
            )
            if now.hour < policy.at_hour:
                today_reset -= timedelta(days=1)
            
            if entry.updated_at < today_reset:
                return "daily"
        
        return None
    
    def has_any_sessions(self) -> bool:
        """检查是否创建了任何会话（跨所有平台）。

        使用 SQLite 数据库作为真实来源，因为它保留
        历史会话记录（已结束的会话仍然计数）。内存中的
        ``_entries`` 字典在重置时替换条目，因此 ``len(_entries)`` 将
        保持在 1（对于单平台用户）— 这是此修复的 bug。

        调用此方法时，当前会话已在 DB 中
        （get_or_create_session 先运行），因此我们检查 ``> 1``。
        """
        if self._db:
            try:
                return self._db.session_count() > 1
            except Exception:
                pass  # fall through to heuristic
        # Fallback: check if sessions.json was loaded with existing data.
        # This covers the rare case where the DB is unavailable.
        with self._lock:
            self._ensure_loaded_locked()
            return len(self._entries) > 1

    def get_or_create_session(
        self,
        source: SessionSource,
        force_new: bool = False
    ) -> SessionEntry:
        """
        获取现有会话或创建新会话。

        评估重置策略以确定现有会话是否已过期。
        在新会话开始时在 SQLite 中创建会话记录。
        """
        session_key = self._generate_session_key(source)
        now = _now()

        # SQLite calls are made outside the lock to avoid holding it during I/O.
        # All _entries / _loaded mutations are protected by self._lock.
        db_end_session_id = None
        db_create_kwargs = None

        with self._lock:
            self._ensure_loaded_locked()

            if session_key in self._entries and not force_new:
                entry = self._entries[session_key]

                reset_reason = self._should_reset(entry, source)
                if not reset_reason:
                    entry.updated_at = now
                    self._save()
                    return entry
                else:
                    # Session is being auto-reset.
                    was_auto_reset = True
                    auto_reset_reason = reset_reason
                    # Track whether the expired session had any real conversation
                    reset_had_activity = entry.total_tokens > 0
                    db_end_session_id = entry.session_id
            else:
                was_auto_reset = False
                auto_reset_reason = None
                reset_had_activity = False

            # Create new session
            session_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

            entry = SessionEntry(
                session_key=session_key,
                session_id=session_id,
                created_at=now,
                updated_at=now,
                origin=source,
                display_name=source.chat_name,
                platform=source.platform,
                chat_type=source.chat_type,
                was_auto_reset=was_auto_reset,
                auto_reset_reason=auto_reset_reason,
                reset_had_activity=reset_had_activity,
            )

            self._entries[session_key] = entry
            self._save()
            db_create_kwargs = {
                "session_id": session_id,
                "source": source.platform.value,
                "user_id": source.user_id,
            }

        # SQLite operations outside the lock
        if self._db and db_end_session_id:
            try:
                self._db.end_session(db_end_session_id, "session_reset")
            except Exception as e:
                logger.debug("Session DB operation failed: %s", e)

        if self._db and db_create_kwargs:
            try:
                self._db.create_session(**db_create_kwargs)
            except Exception as e:
                print(f"[gateway] Warning: Failed to create SQLite session: {e}")

        # Seed new DM thread sessions with parent DM session history.
        # When a bot reply creates a Slack thread and the user responds in it,
        # the thread gets a new session (keyed by thread_ts).  Without seeding,
        # the thread session starts with zero context — the user's original
        # question and the bot's answer are invisible.  Fix: copy the parent
        # DM session's transcript into the new thread session so context carries
        # over while still keeping threads isolated from each other.
        if (
            source.chat_type == "dm"
            and source.thread_id
            and entry.created_at == entry.updated_at  # brand-new session
            and not was_auto_reset
        ):
            parent_source = SessionSource(
                platform=source.platform,
                chat_id=source.chat_id,
                chat_type="dm",
                user_id=source.user_id,
                # no thread_id — this is the parent DM session
            )
            parent_key = self._generate_session_key(parent_source)
            with self._lock:
                parent_entry = self._entries.get(parent_key)
            if parent_entry and parent_entry.session_id != entry.session_id:
                try:
                    parent_history = self.load_transcript(parent_entry.session_id)
                    if parent_history:
                        self.rewrite_transcript(entry.session_id, parent_history)
                        logger.info(
                            "[Session] Seeded DM thread session %s with %d messages from parent %s",
                            entry.session_id, len(parent_history), parent_entry.session_id,
                        )
                except Exception as e:
                    logger.warning("[Session] Failed to seed thread session: %s", e)

        return entry

    def update_session(
        self,
        session_key: str,
        last_prompt_tokens: int = None,
    ) -> None:
        """在交互后更新轻量级会话元数据。"""
        with self._lock:
            self._ensure_loaded_locked()

            if session_key in self._entries:
                entry = self._entries[session_key]
                entry.updated_at = _now()
                if last_prompt_tokens is not None:
                    entry.last_prompt_tokens = last_prompt_tokens
                self._save()

    def reset_session(self, session_key: str) -> Optional[SessionEntry]:
        """强制重置会话，创建新的会话 ID。"""
        db_end_session_id = None
        db_create_kwargs = None
        new_entry = None

        with self._lock:
            self._ensure_loaded_locked()

            if session_key not in self._entries:
                return None

            old_entry = self._entries[session_key]
            db_end_session_id = old_entry.session_id

            now = _now()
            session_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

            new_entry = SessionEntry(
                session_key=session_key,
                session_id=session_id,
                created_at=now,
                updated_at=now,
                origin=old_entry.origin,
                display_name=old_entry.display_name,
                platform=old_entry.platform,
                chat_type=old_entry.chat_type,
            )

            self._entries[session_key] = new_entry
            self._save()
            db_create_kwargs = {
                "session_id": session_id,
                "source": old_entry.platform.value if old_entry.platform else "unknown",
                "user_id": old_entry.origin.user_id if old_entry.origin else None,
            }

        if self._db and db_end_session_id:
            try:
                self._db.end_session(db_end_session_id, "session_reset")
            except Exception as e:
                logger.debug("Session DB operation failed: %s", e)

        if self._db and db_create_kwargs:
            try:
                self._db.create_session(**db_create_kwargs)
            except Exception as e:
                logger.debug("Session DB operation failed: %s", e)

        return new_entry

    def switch_session(self, session_key: str, target_session_id: str) -> Optional[SessionEntry]:
        """将会话键切换为指向现有会话 ID。

        用于 ``/resume`` 恢复先前命名的会话。
        在 SQLite 中结束当前会话（如重置），但不是
        生成新的会话 ID，而是重用 ``target_session_id`` 以便
        下条消息时加载旧的记录。
        """
        db_end_session_id = None
        new_entry = None

        with self._lock:
            self._ensure_loaded_locked()

            if session_key not in self._entries:
                return None

            old_entry = self._entries[session_key]

            # Don't switch if already on that session
            if old_entry.session_id == target_session_id:
                return old_entry

            db_end_session_id = old_entry.session_id

            now = _now()
            new_entry = SessionEntry(
                session_key=session_key,
                session_id=target_session_id,
                created_at=now,
                updated_at=now,
                origin=old_entry.origin,
                display_name=old_entry.display_name,
                platform=old_entry.platform,
                chat_type=old_entry.chat_type,
            )

            self._entries[session_key] = new_entry
            self._save()

        if self._db and db_end_session_id:
            try:
                self._db.end_session(db_end_session_id, "session_switch")
            except Exception as e:
                logger.debug("Session DB end_session failed: %s", e)

        return new_entry

    def list_sessions(self, active_minutes: Optional[int] = None) -> List[SessionEntry]:
        """列出所有会话，可选择按活动过滤。"""
        with self._lock:
            self._ensure_loaded_locked()
            entries = list(self._entries.values())

        if active_minutes is not None:
            cutoff = _now() - timedelta(minutes=active_minutes)
            entries = [e for e in entries if e.updated_at >= cutoff]

        entries.sort(key=lambda e: e.updated_at, reverse=True)

        return entries
    
    def get_transcript_path(self, session_id: str) -> Path:
        """获取会话传统记录文件的路径。"""
        return self.sessions_dir / f"{session_id}.jsonl"
    
    def append_to_transcript(self, session_id: str, message: Dict[str, Any], skip_db: bool = False) -> None:
        """将消息追加到会话的记录（SQLite + 传统 JSONL）。

        参数：
            skip_db: 为 True 时，仅写入 JSONL 并跳过 SQLite 写入。
                     用于代理已经通过自己的 _flush_messages_to_session_db() 持久化消息时，
                     防止重复写入 bug (#860)。
        """
        # Write to SQLite (unless the agent already handled it)
        if self._db and not skip_db:
            try:
                self._db.append_message(
                    session_id=session_id,
                    role=message.get("role", "unknown"),
                    content=message.get("content"),
                    tool_name=message.get("tool_name"),
                    tool_calls=message.get("tool_calls"),
                    tool_call_id=message.get("tool_call_id"),
                )
            except Exception as e:
                logger.debug("Session DB operation failed: %s", e)
        
        # Also write legacy JSONL (keeps existing tooling working during transition)
        transcript_path = self.get_transcript_path(session_id)
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
    
    def rewrite_transcript(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """用新消息替换会话的整个记录。
        
        由 /retry、/undo 和 /compress 使用以持久化修改的对话历史。
        重写 SQLite 和传统 JSONL 存储。
        """
        # SQLite: clear old messages and re-insert
        if self._db:
            try:
                self._db.clear_messages(session_id)
                for msg in messages:
                    role = msg.get("role", "unknown")
                    self._db.append_message(
                        session_id=session_id,
                        role=role,
                        content=msg.get("content"),
                        tool_name=msg.get("tool_name"),
                        tool_calls=msg.get("tool_calls"),
                        tool_call_id=msg.get("tool_call_id"),
                        reasoning=msg.get("reasoning") if role == "assistant" else None,
                        reasoning_details=msg.get("reasoning_details") if role == "assistant" else None,
                        codex_reasoning_items=msg.get("codex_reasoning_items") if role == "assistant" else None,
                    )
            except Exception as e:
                logger.debug("Failed to rewrite transcript in DB: %s", e)
        
        # JSONL: overwrite the file
        transcript_path = self.get_transcript_path(session_id)
        with open(transcript_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def load_transcript(self, session_id: str) -> List[Dict[str, Any]]:
        """从会话的记录加载所有消息。"""
        db_messages = []
        # Try SQLite first
        if self._db:
            try:
                db_messages = self._db.get_messages_as_conversation(session_id)
            except Exception as e:
                logger.debug("Could not load messages from DB: %s", e)

        # Load legacy JSONL transcript (may contain more history than SQLite
        # for sessions created before the DB layer was introduced).
        transcript_path = self.get_transcript_path(session_id)
        jsonl_messages = []
        if transcript_path.exists():
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            jsonl_messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning(
                                "Skipping corrupt line in transcript %s: %s",
                                session_id, line[:120],
                            )

        # Prefer whichever source has more messages.
        #
        # Background: when a session pre-dates SQLite storage (or when the DB
        # layer was added while a long-lived session was already active), the
        # first post-migration turn writes only the *new* messages to SQLite
        # (because _flush_messages_to_session_db skips messages already in
        # conversation_history, assuming they're persisted).  On the *next*
        # turn load_transcript returns those few SQLite rows and ignores the
        # full JSONL history — the model sees a context of 1-4 messages instead
        # of hundreds.  Using the longer source prevents this silent truncation.
        if len(jsonl_messages) > len(db_messages):
            if db_messages:
                logger.debug(
                    "Session %s: JSONL has %d messages vs SQLite %d — "
                    "using JSONL (legacy session not yet fully migrated)",
                    session_id, len(jsonl_messages), len(db_messages),
                )
            return jsonl_messages

        return db_messages


def build_session_context(
    source: SessionSource,
    config: GatewayConfig,
    session_entry: Optional[SessionEntry] = None
) -> SessionContext:
    """
    从来源和配置构建完整的会话上下文。
    
    这用于将上下文注入代理的系统提示词。
    """
    connected = config.get_connected_platforms()
    
    home_channels = {}
    for platform in connected:
        home = config.get_home_channel(platform)
        if home:
            home_channels[platform] = home
    
    context = SessionContext(
        source=source,
        connected_platforms=connected,
        home_channels=home_channels,
    )
    
    if session_entry:
        context.session_key = session_entry.session_key
        context.session_id = session_entry.session_id
        context.created_at = session_entry.created_at
        context.updated_at = session_entry.updated_at
    
    return context
