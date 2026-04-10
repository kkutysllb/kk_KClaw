"""基于 Honcho 的会话管理，用于对话历史。"""

from __future__ import annotations

import queue
import re
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from plugins.memory.honcho.client import get_honcho_client

if TYPE_CHECKING:
    from honcho import Honcho

logger = logging.getLogger(__name__)

# Sentinel to signal the async writer thread to shut down
_ASYNC_SHUTDOWN = object()


@dataclass
class HonchoSession:
    """
    由 Honcho 支持的对话会话。

    提供本地消息缓存，同步到 Honcho 的
    AI 原生记忆系统用于用户建模。
    """

    key: str  # channel:chat_id
    user_peer_id: str  # Honcho peer ID for the user
    assistant_peer_id: str  # Honcho peer ID for the assistant
    honcho_session_id: str  # Honcho session ID
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """将消息添加到本地缓存。"""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """获取用于 LLM 上下文的消息历史。"""
        recent = (
            self.messages[-max_messages:]
            if len(self.messages) > max_messages
            else self.messages
        )
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear(self) -> None:
        """清除会话中的所有消息。"""
        self.messages = []
        self.updated_at = datetime.now()


class HonchoSessionManager:
    """
    使用 Honcho 管理对话会话。

    与 kclaw 现有的 SQLite 状态和基于文件的记忆并行运行，
    通过 Honcho 的 AI 原生记忆添加持久化跨会话用户建模。
    """

    def __init__(
        self,
        honcho: Honcho | None = None,
        context_tokens: int | None = None,
        config: Any | None = None,
    ):
        """
        初始化会话管理器。

        Args:
            honcho: 可选的 Honcho 客户端。如果未提供，则使用单例。
            context_tokens: context() 调用的最大 token 数（None = Honcho 默认值）。
            config: 来自全局配置的 HonchoClientConfig（提供 peer_name、ai_peer、
                    write_frequency、observation 等）。
        """
        self._honcho = honcho
        self._context_tokens = context_tokens
        self._config = config
        self._cache: dict[str, HonchoSession] = {}
        self._peers_cache: dict[str, Any] = {}
        self._sessions_cache: dict[str, Any] = {}

        # Write frequency state
        write_frequency = (config.write_frequency if config else "async")
        self._write_frequency = write_frequency
        self._turn_counter: int = 0

        # Prefetch caches: session_key → last result (consumed once per turn)
        self._context_cache: dict[str, dict] = {}
        self._dialectic_cache: dict[str, str] = {}
        self._prefetch_cache_lock = threading.Lock()
        self._dialectic_reasoning_level: str = (
            config.dialectic_reasoning_level if config else "low"
        )
        self._dialectic_dynamic: bool = (
            config.dialectic_dynamic if config else True
        )
        self._dialectic_max_chars: int = (
            config.dialectic_max_chars if config else 600
        )
        self._observation_mode: str = (
            config.observation_mode if config else "directional"
        )
        # Per-peer observation booleans (granular, from config)
        self._user_observe_me: bool = config.user_observe_me if config else True
        self._user_observe_others: bool = config.user_observe_others if config else True
        self._ai_observe_me: bool = config.ai_observe_me if config else True
        self._ai_observe_others: bool = config.ai_observe_others if config else True
        self._message_max_chars: int = (
            config.message_max_chars if config else 25000
        )
        self._dialectic_max_input_chars: int = (
            config.dialectic_max_input_chars if config else 10000
        )

        # Async write queue — started lazily on first enqueue
        self._async_queue: queue.Queue | None = None
        self._async_thread: threading.Thread | None = None
        if write_frequency == "async":
            self._async_queue = queue.Queue()
            self._async_thread = threading.Thread(
                target=self._async_writer_loop,
                name="honcho-async-writer",
                daemon=True,
            )
            self._async_thread.start()

    @property
    def honcho(self) -> Honcho:
        """获取 Honcho 客户端，必要时初始化。"""
        if self._honcho is None:
            self._honcho = get_honcho_client()
        return self._honcho

    def _get_or_create_peer(self, peer_id: str) -> Any:
        """
        获取或创建 Honcho peer。

        Peers 是惰性的 — 在首次使用之前不会进行 API 调用。
        观察设置通过 SessionPeerConfig 每会话控制。
        """
        if peer_id in self._peers_cache:
            return self._peers_cache[peer_id]

        peer = self.honcho.peer(peer_id)
        self._peers_cache[peer_id] = peer
        return peer

    def _get_or_create_honcho_session(
        self, session_id: str, user_peer: Any, assistant_peer: Any
    ) -> tuple[Any, list]:
        """
        获取或创建配置了 peers 的 Honcho 会话。

        Returns:
            (honcho_session, existing_messages) 元组。
        """
        if session_id in self._sessions_cache:
            logger.debug("Honcho session '%s' retrieved from cache", session_id)
            return self._sessions_cache[session_id], []

        session = self.honcho.session(session_id)

        # Configure per-peer observation from granular booleans.
        # These map 1:1 to Honcho's SessionPeerConfig toggles.
        try:
            from honcho.session import SessionPeerConfig
            user_config = SessionPeerConfig(
                observe_me=self._user_observe_me,
                observe_others=self._user_observe_others,
            )
            ai_config = SessionPeerConfig(
                observe_me=self._ai_observe_me,
                observe_others=self._ai_observe_others,
            )

            session.add_peers([(user_peer, user_config), (assistant_peer, ai_config)])

            # Sync back: server-side config (set via Honcho UI) wins over
            # local defaults. Read the effective config after add_peers.
            # Note: observation booleans are manager-scoped, not per-session.
            # Last session init wins. Fine for CLI; gateway should scope per-session.
            try:
                server_user = session.get_peer_configuration(user_peer)
                server_ai = session.get_peer_configuration(assistant_peer)
                if server_user.observe_me is not None:
                    self._user_observe_me = server_user.observe_me
                if server_user.observe_others is not None:
                    self._user_observe_others = server_user.observe_others
                if server_ai.observe_me is not None:
                    self._ai_observe_me = server_ai.observe_me
                if server_ai.observe_others is not None:
                    self._ai_observe_others = server_ai.observe_others
                logger.debug(
                    "Honcho observation synced from server: user(me=%s,others=%s) ai(me=%s,others=%s)",
                    self._user_observe_me, self._user_observe_others,
                    self._ai_observe_me, self._ai_observe_others,
                )
            except Exception as e:
                logger.debug("Honcho get_peer_configuration failed (using local config): %s", e)
        except Exception as e:
            logger.warning(
                "Honcho session '%s' add_peers failed (non-fatal): %s",
                session_id, e,
            )

        # Load existing messages via context() - single call for messages + metadata
        existing_messages = []
        try:
            ctx = session.context(summary=True, tokens=self._context_tokens)
            existing_messages = ctx.messages or []

            # Verify chronological ordering
            if existing_messages and len(existing_messages) > 1:
                timestamps = [m.created_at for m in existing_messages if m.created_at]
                if timestamps and timestamps != sorted(timestamps):
                    logger.warning(
                        "Honcho messages not chronologically ordered for session '%s', sorting",
                        session_id,
                    )
                    existing_messages = sorted(
                        existing_messages,
                        key=lambda m: m.created_at or datetime.min,
                    )

            if existing_messages:
                logger.info(
                    "Honcho session '%s' retrieved (%d existing messages)",
                    session_id, len(existing_messages),
                )
            else:
                logger.info("Honcho session '%s' created (new)", session_id)
        except Exception as e:
            logger.warning(
                "Honcho session '%s' loaded (failed to fetch context: %s)",
                session_id, e,
            )

        self._sessions_cache[session_id] = session
        return session, existing_messages

    def _sanitize_id(self, id_str: str) -> str:
        """清理 ID 以匹配 Honcho 的模式：^[a-zA-Z0-9_-]+"""
        return re.sub(r'[^a-zA-Z0-9_-]', '-', id_str)

    def get_or_create(self, key: str) -> HonchoSession:
        """
        获取现有会话或创建新会话。

        Args:
            key: Session key（通常为 channel:chat_id）。

        Returns:
            会话。
        """
        if key in self._cache:
            logger.debug("Local session cache hit: %s", key)
            return self._cache[key]

        # Use peer names from global config when available
        if self._config and self._config.peer_name:
            user_peer_id = self._sanitize_id(self._config.peer_name)
        else:
            # Fallback: derive from session key
            parts = key.split(":", 1)
            channel = parts[0] if len(parts) > 1 else "default"
            chat_id = parts[1] if len(parts) > 1 else key
            user_peer_id = self._sanitize_id(f"user-{channel}-{chat_id}")

        assistant_peer_id = self._sanitize_id(
            self._config.ai_peer if self._config else "kclaw-assistant"
        )

        # Sanitize session ID for Honcho
        honcho_session_id = self._sanitize_id(key)

        # Get or create peers
        user_peer = self._get_or_create_peer(user_peer_id)
        assistant_peer = self._get_or_create_peer(assistant_peer_id)

        # Get or create Honcho session
        honcho_session, existing_messages = self._get_or_create_honcho_session(
            honcho_session_id, user_peer, assistant_peer
        )

        # Convert Honcho messages to local format
        local_messages = []
        for msg in existing_messages:
            role = "assistant" if msg.peer_id == assistant_peer_id else "user"
            local_messages.append({
                "role": role,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat() if msg.created_at else "",
                "_synced": True,  # Already in Honcho
            })

        # Create local session wrapper with existing messages
        session = HonchoSession(
            key=key,
            user_peer_id=user_peer_id,
            assistant_peer_id=assistant_peer_id,
            honcho_session_id=honcho_session_id,
            messages=local_messages,
        )

        self._cache[key] = session
        return session

    def _flush_session(self, session: HonchoSession) -> bool:
        """内部：同步将未同步的消息写入 Honcho。"""
        if not session.messages:
            return True

        user_peer = self._get_or_create_peer(session.user_peer_id)
        assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
        honcho_session = self._sessions_cache.get(session.honcho_session_id)

        if not honcho_session:
            honcho_session, _ = self._get_or_create_honcho_session(
                session.honcho_session_id, user_peer, assistant_peer
            )

        new_messages = [m for m in session.messages if not m.get("_synced")]
        if not new_messages:
            return True

        honcho_messages = []
        for msg in new_messages:
            peer = user_peer if msg["role"] == "user" else assistant_peer
            honcho_messages.append(peer.message(msg["content"]))

        try:
            honcho_session.add_messages(honcho_messages)
            for msg in new_messages:
                msg["_synced"] = True
            logger.debug("Synced %d messages to Honcho for %s", len(honcho_messages), session.key)
            self._cache[session.key] = session
            return True
        except Exception as e:
            for msg in new_messages:
                msg["_synced"] = False
            logger.error("Failed to sync messages to Honcho: %s", e)
            self._cache[session.key] = session
            return False

    def _async_writer_loop(self) -> None:
        """后台守护线程：消耗异步写入队列。"""
        while True:
            try:
                item = self._async_queue.get(timeout=5)
                if item is _ASYNC_SHUTDOWN:
                    break

                first_error: Exception | None = None
                try:
                    success = self._flush_session(item)
                except Exception as e:
                    success = False
                    first_error = e

                if success:
                    continue

                if first_error is not None:
                    logger.warning("Honcho async write failed, retrying once: %s", first_error)
                else:
                    logger.warning("Honcho async write failed, retrying once")

                import time as _time
                _time.sleep(2)

                try:
                    retry_success = self._flush_session(item)
                except Exception as e2:
                    logger.error("Honcho async write retry failed, dropping batch: %s", e2)
                    continue

                if not retry_success:
                    logger.error("Honcho async write retry failed, dropping batch")
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Honcho async writer error: %s", e)

    def save(self, session: HonchoSession) -> None:
        """保存消息到 Honcho，遵循 write_frequency。

        write_frequency 模式：
          "async"   — 入队到后台线程（零阻塞，零 token 成本）
          "turn"    — 每个 turn 同步刷新
          "session" — 延迟直到显式调用 flush_session()
          N (int)   — 每 N 个 turn 刷新一次
        """
        self._turn_counter += 1
        wf = self._write_frequency

        if wf == "async":
            if self._async_queue is not None:
                self._async_queue.put(session)
        elif wf == "turn":
            self._flush_session(session)
        elif wf == "session":
            # Accumulate; caller must call flush_all() at session end
            pass
        elif isinstance(wf, int) and wf > 0:
            if self._turn_counter % wf == 0:
                self._flush_session(session)

    def flush_all(self) -> None:
        """刷新所有缓存会话的所有待处理未同步消息。

        在"session" write_frequency 的会话结束时调用，或在
        进程退出前强制同步（无论模式如何）。
        """
        for session in list(self._cache.values()):
            try:
                self._flush_session(session)
            except Exception as e:
                logger.error("Honcho flush_all error for %s: %s", session.key, e)

        # Drain async queue synchronously if it exists
        if self._async_queue is not None:
            while not self._async_queue.empty():
                try:
                    item = self._async_queue.get_nowait()
                    if item is not _ASYNC_SHUTDOWN:
                        self._flush_session(item)
                except queue.Empty:
                    break

    def shutdown(self) -> None:
        """优雅地关闭异步写入器线程。"""
        if self._async_queue is not None and self._async_thread is not None:
            self.flush_all()
            self._async_queue.put(_ASYNC_SHUTDOWN)
            self._async_thread.join(timeout=10)

    def delete(self, key: str) -> bool:
        """从本地缓存中删除会话。"""
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def new_session(self, key: str) -> HonchoSession:
        """
        创建新会话，保留旧会话用于用户建模。

        创建带有新 ID 的全新会话，同时将旧会话的数据保留在 Honcho 中
        以继续进行用户建模。
        """
        import time

        # Remove old session from caches (but don't delete from Honcho)
        old_session = self._cache.pop(key, None)
        if old_session:
            self._sessions_cache.pop(old_session.honcho_session_id, None)

        # Create new session with timestamp suffix
        timestamp = int(time.time())
        new_key = f"{key}:{timestamp}"

        # get_or_create will create a fresh session
        session = self.get_or_create(new_key)

        # Cache under the original key so callers find it by the expected name
        self._cache[key] = session

        logger.info("Created new session for %s (honcho: %s)", key, session.honcho_session_id)
        return session

    _REASONING_LEVELS = ("minimal", "low", "medium", "high", "max")

    def _dynamic_reasoning_level(self, query: str) -> str:
        """
        为 dialectic 查询选择推理级别。

        当 dialecticDynamic 为 true（默认）时，根据查询
        长度自动提升，以便 Honcho 在重要的地方应用更多推理：

          < 120 chars  -> 配置的默认值（通常为 "low"）
          120-400 chars -> 比默认值高一级（上限为 "high"）
          > 400 chars  -> 比默认值高两级（上限为 "high"）

        "max" 永远不会自动选择 — 为显式配置保留。

        当 dialecticDynamic 为 false 时，始终返回配置的级别。
        """
        if not self._dialectic_dynamic:
            return self._dialectic_reasoning_level

        levels = self._REASONING_LEVELS
        default_idx = levels.index(self._dialectic_reasoning_level) if self._dialectic_reasoning_level in levels else 1
        n = len(query)
        if n < 120:
            bump = 0
        elif n < 400:
            bump = 1
        else:
            bump = 2
        # Cap at "high" (index 3) for auto-selection
        idx = min(default_idx + bump, 3)
        return levels[idx]

    def dialectic_query(
        self, session_key: str, query: str,
        reasoning_level: str | None = None,
        peer: str = "user",
    ) -> str:
        """
        查询 Honcho 关于某个 peer 的 dialectic 端点。

        在 Honcho 后端针对目标 peer 的完整表示运行 LLM。
        延迟高于 context() — 通过 prefetch_dialectic() 异步调用
        以避免阻塞响应。

        Args:
            session_key: 要查询的会话键。
            query: 自然语言问题。
            reasoning_level: 覆盖配置默认值。如果为 None，则使用
                             _dynamic_reasoning_level(query)。
            peer: 查询哪个 peer — "user"（默认）或 "ai"。

        Returns:
            Honcho 的合成答案，失败时返回空字符串。
        """
        session = self._cache.get(session_key)
        if not session:
            return ""

        # Guard: truncate query to Honcho's dialectic input limit
        if len(query) > self._dialectic_max_input_chars:
            query = query[:self._dialectic_max_input_chars].rsplit(" ", 1)[0]

        level = reasoning_level or self._dynamic_reasoning_level(query)

        try:
            if self._ai_observe_others:
                # AI peer can observe user — use cross-observation routing
                if peer == "ai":
                    ai_peer_obj = self._get_or_create_peer(session.assistant_peer_id)
                    result = ai_peer_obj.chat(query, reasoning_level=level) or ""
                else:
                    ai_peer_obj = self._get_or_create_peer(session.assistant_peer_id)
                    result = ai_peer_obj.chat(
                        query,
                        target=session.user_peer_id,
                        reasoning_level=level,
                    ) or ""
            else:
                # AI can't observe others — each peer queries self
                peer_id = session.assistant_peer_id if peer == "ai" else session.user_peer_id
                target_peer = self._get_or_create_peer(peer_id)
                result = target_peer.chat(query, reasoning_level=level) or ""

            # Apply KClaw-side char cap before caching
            if result and self._dialectic_max_chars and len(result) > self._dialectic_max_chars:
                result = result[:self._dialectic_max_chars].rsplit(" ", 1)[0] + " …"
            return result
        except Exception as e:
            logger.warning("Honcho dialectic query failed: %s", e)
            return ""

    def prefetch_dialectic(self, session_key: str, query: str) -> None:
        """
        在后台线程中触发 dialectic_query，缓存结果。

        非阻塞。结果可通过下次调用时的 pop_dialectic_result() 获取
        （通常是下一个 turn）。推理级别根据查询复杂性动态选择。

        Args:
            session_key: 要查询的会话键。
            query: 用户当前消息，用作查询。
        """
        def _run():
            result = self.dialectic_query(session_key, query)
            if result:
                self.set_dialectic_result(session_key, result)

        t = threading.Thread(target=_run, name="honcho-dialectic-prefetch", daemon=True)
        t.start()

    def set_dialectic_result(self, session_key: str, result: str) -> None:
        """以线程安全的方式存储预取的 dialectic 结果。"""
        if not result:
            return
        with self._prefetch_cache_lock:
            self._dialectic_cache[session_key] = result

    def pop_dialectic_result(self, session_key: str) -> str:
        """
        返回并清除此会话的缓存 dialectic 结果。

        如果还没有结果准备就绪，则返回空字符串。
        """
        with self._prefetch_cache_lock:
            return self._dialectic_cache.pop(session_key, "")

    def prefetch_context(self, session_key: str, user_message: str | None = None) -> None:
        """
        在后台线程中触发 get_prefetch_context，缓存结果。

        非阻塞。通过 pop_context_result() 在下一个 turn 消费。
        这样可以避免同步 HTTP 往返阻塞每个响应。
        """
        def _run():
            result = self.get_prefetch_context(session_key, user_message)
            if result:
                self.set_context_result(session_key, result)

        t = threading.Thread(target=_run, name="honcho-context-prefetch", daemon=True)
        t.start()

    def set_context_result(self, session_key: str, result: dict[str, str]) -> None:
        """以线程安全的方式存储预取的上下文结果。"""
        if not result:
            return
        with self._prefetch_cache_lock:
            self._context_cache[session_key] = result

    def pop_context_result(self, session_key: str) -> dict[str, str]:
        """
        返回并清除此会话的缓存上下文结果。

        如果还没有结果准备就绪（第一个 turn），则返回空字典。
        """
        with self._prefetch_cache_lock:
            return self._context_cache.pop(session_key, {})

    def get_prefetch_context(self, session_key: str, user_message: str | None = None) -> dict[str, str]:
        """
        从 Honcho 预取用户和 AI peer 上下文。

        获取两个 peer 的 peer_representation 和 peer_card。
        search_query 被故意省略 — 它只会影响此代码不使用的额外摘录，
        而传递原始消息会在服务器访问日志中暴露对话内容。

        Args:
            session_key: 要获取上下文的会话键。
            user_message: 未使用；为调用点兼容性保留。

        Returns:
            带有 'representation'、'card'、'ai_representation' 和 'ai_card' 键的字典。
        """
        session = self._cache.get(session_key)
        if not session:
            return {}

        result: dict[str, str] = {}
        try:
            user_ctx = self._fetch_peer_context(session.user_peer_id)
            result["representation"] = user_ctx["representation"]
            result["card"] = "\n".join(user_ctx["card"])
        except Exception as e:
            logger.warning("Failed to fetch user context from Honcho: %s", e)

        # Also fetch AI peer's own representation so KClaw knows itself.
        try:
            ai_ctx = self._fetch_peer_context(session.assistant_peer_id)
            result["ai_representation"] = ai_ctx["representation"]
            result["ai_card"] = "\n".join(ai_ctx["card"])
        except Exception as e:
            logger.debug("Failed to fetch AI peer context from Honcho: %s", e)

        return result

    def migrate_local_history(self, session_key: str, messages: list[dict[str, Any]]) -> bool:
        """
        将本地会话历史作为文件上传到 Honcho。

        当 Honcho 在对话中途激活以保留先前上下文时使用。

        Args:
            session_key: 会话键（例如 "telegram:123456"）。
            messages: 本地消息（带有 role、content、timestamp 的字典）。

        Returns:
            如果上传成功则为 True，否则为 False。
        """
        session = self._cache.get(session_key)
        if not session:
            logger.warning("No local session cached for '%s', skipping migration", session_key)
            return False

        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            logger.warning("No Honcho session cached for '%s', skipping migration", session_key)
            return False

        user_peer = self._get_or_create_peer(session.user_peer_id)

        content_bytes = self._format_migration_transcript(session_key, messages)
        first_ts = messages[0].get("timestamp") if messages else None

        try:
            honcho_session.upload_file(
                file=("prior_history.txt", content_bytes, "text/plain"),
                peer=user_peer,
                metadata={"source": "local_jsonl", "count": len(messages)},
                created_at=first_ts,
            )
            logger.info("Migrated %d local messages to Honcho for %s", len(messages), session_key)
            return True
        except Exception as e:
            logger.error("Failed to upload local history to Honcho for %s: %s", session_key, e)
            return False

    @staticmethod
    def _format_migration_transcript(session_key: str, messages: list[dict[str, Any]]) -> bytes:
        """将本地消息格式化为 XML 记录，用于 Honcho 文件上传。"""
        timestamps = [m.get("timestamp", "") for m in messages]
        time_range = f"{timestamps[0]} to {timestamps[-1]}" if timestamps else "unknown"

        lines = [
            "<prior_conversation_history>",
            "<context>",
            "This conversation history occurred BEFORE the Honcho memory system was activated.",
            "These messages are the preceding elements of this conversation session and should",
            "be treated as foundational context for all subsequent interactions. The user and",
            "assistant have already established rapport through these exchanges.",
            "</context>",
            "",
            f'<transcript session_key="{session_key}" message_count="{len(messages)}"',
            f'           time_range="{time_range}">',
            "",
        ]
        for msg in messages:
            ts = msg.get("timestamp", "?")
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            lines.append(f"[{ts}] {role}: {content}")

        lines.append("")
        lines.append("</transcript>")
        lines.append("</prior_conversation_history>")

        return "\n".join(lines).encode("utf-8")

    def migrate_memory_files(self, session_key: str, memory_dir: str) -> bool:
        """
        将 MEMORY.md 和 USER.md 作为文件上传到 Honcho。

        当 Honcho 在已有本地整合记忆的实例上激活时使用。
        向后兼容 — 如果文件不存在则跳过。

        Args:
            session_key: 要关联文件的会话键。
            memory_dir: memories 目录的路径（~/.kclaw/memories/）。

        Returns:
            如果至少上传了一个文件则为 True，否则为 False。
        """
        from pathlib import Path
        memory_path = Path(memory_dir)

        if not memory_path.exists():
            return False

        session = self._cache.get(session_key)
        if not session:
            logger.warning("No local session cached for '%s', skipping memory migration", session_key)
            return False

        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            logger.warning("No Honcho session cached for '%s', skipping memory migration", session_key)
            return False

        user_peer = self._get_or_create_peer(session.user_peer_id)
        assistant_peer = self._get_or_create_peer(session.assistant_peer_id)

        uploaded = False
        files = [
            (
                "MEMORY.md",
                "consolidated_memory.md",
                "Long-term agent notes and preferences",
                user_peer,
                "user",
            ),
            (
                "USER.md",
                "user_profile.md",
                "User profile and preferences",
                user_peer,
                "user",
            ),
            (
                "SOUL.md",
                "agent_soul.md",
                "Agent persona and identity configuration",
                assistant_peer,
                "ai",
            ),
        ]

        for filename, upload_name, description, target_peer, target_kind in files:
            filepath = memory_path / filename
            if not filepath.exists():
                continue
            content = filepath.read_text(encoding="utf-8").strip()
            if not content:
                continue

            wrapped = (
                f"<prior_memory_file>\n"
                f"<context>\n"
                f"This file was consolidated from local conversations BEFORE Honcho was activated.\n"
                f"{description}. Treat as foundational context for this user.\n"
                f"</context>\n"
                f"\n"
                f"{content}\n"
                f"</prior_memory_file>\n"
            )

            try:
                honcho_session.upload_file(
                    file=(upload_name, wrapped.encode("utf-8"), "text/plain"),
                    peer=target_peer,
                    metadata={
                        "source": "local_memory",
                        "original_file": filename,
                        "target_peer": target_kind,
                    },
                )
                logger.info(
                    "Uploaded %s to Honcho for %s (%s peer)",
                    filename,
                    session_key,
                    target_kind,
                )
                uploaded = True
            except Exception as e:
                logger.error("Failed to upload %s to Honcho: %s", filename, e)

        return uploaded

    @staticmethod
    def _normalize_card(card: Any) -> list[str]:
        """将 Honcho card 负载规范化为普通字符串列表。"""
        if not card:
            return []
        if isinstance(card, list):
            return [str(item) for item in card if item]
        return [str(card)]

    def _fetch_peer_card(self, peer_id: str) -> list[str]:
        """直接从 peer 对象获取 peer card。

        这样可以避免依赖 session.context()，后者可能返回空的
        peer_card，即使 peer 本身有已填充的 card。
        """
        peer = self._get_or_create_peer(peer_id)
        getter = getattr(peer, "get_card", None)
        if callable(getter):
            return self._normalize_card(getter())

        legacy_getter = getattr(peer, "card", None)
        if callable(legacy_getter):
            return self._normalize_card(legacy_getter())

        return []

    def _fetch_peer_context(self, peer_id: str, search_query: str | None = None) -> dict[str, Any]:
        """直接从 peer 对象获取表示 + peer card。"""
        peer = self._get_or_create_peer(peer_id)
        representation = ""
        card: list[str] = []

        try:
            ctx = peer.context(search_query=search_query) if search_query else peer.context()
            representation = (
                getattr(ctx, "representation", None)
                or getattr(ctx, "peer_representation", None)
                or ""
            )
            card = self._normalize_card(getattr(ctx, "peer_card", None))
        except Exception as e:
            logger.debug("Direct peer.context() failed for '%s': %s", peer_id, e)

        if not representation:
            try:
                representation = peer.representation() or ""
            except Exception as e:
                logger.debug("Direct peer.representation() failed for '%s': %s", peer_id, e)

        if not card:
            try:
                card = self._fetch_peer_card(peer_id)
            except Exception as e:
                logger.debug("Direct peer card fetch failed for '%s': %s", peer_id, e)

        return {"representation": representation, "card": card}

    def get_peer_card(self, session_key: str) -> list[str]:
        """
        获取用户 peer 的 card — 关键事实的精选列表。

        快速，无需 LLM 推理。返回 Honcho 推断的
        关于用户的原始结构化事实（姓名、角色、偏好、模式）。
        如果不可用则返回空列表。
        """
        session = self._cache.get(session_key)
        if not session:
            return []

        try:
            return self._fetch_peer_card(session.user_peer_id)
        except Exception as e:
            logger.debug("Failed to fetch peer card from Honcho: %s", e)
            return []

    def search_context(self, session_key: str, query: str, max_tokens: int = 800) -> str:
        """
        对 Honcho 会话上下文进行语义搜索。

        返回按与查询相关性排序的原始摘录。无 LLM
        推理 — 比 dialectic_query 更便宜更快。适用于
        模型将进行自己综合的事实查找。

        Args:
            session_key: 要搜索的会话。
            query: 用于语义匹配的搜索查询。
            max_tokens: 返回内容的 token 预算。

        Returns:
            作为字符串的相关上下文摘录，如果没有则返回空字符串。
        """
        session = self._cache.get(session_key)
        if not session:
            return ""

        try:
            ctx = self._fetch_peer_context(session.user_peer_id, search_query=query)
            parts = []
            if ctx["representation"]:
                parts.append(ctx["representation"])
            card = ctx["card"] or []
            if card:
                parts.append("\n".join(f"- {f}" for f in card))
            return "\n\n".join(parts)
        except Exception as e:
            logger.debug("Honcho search_context failed: %s", e)
            return ""

    def create_conclusion(self, session_key: str, content: str) -> bool:
        """将关于用户的结论写回 Honcho。

        结论是 AI peer 观察到的关于用户的事实 —
        偏好、纠正、澄清、项目上下文。
        它们被输入到用户的 peer card 和表示中。

        Args:
            session_key: 要关联结论的会话。
            content: 结论文本（例如 "User prefers dark mode"）。

        Returns:
            成功时为 True，失败时为 False。
        """
        if not content or not content.strip():
            return False

        session = self._cache.get(session_key)
        if not session:
            logger.warning("No session cached for '%s', skipping conclusion", session_key)
            return False

        try:
            if self._ai_observe_others:
                # AI peer creates conclusion about user (cross-observation)
                assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
                conclusions_scope = assistant_peer.conclusions_of(session.user_peer_id)
            else:
                # AI can't observe others — user peer creates self-conclusion
                user_peer = self._get_or_create_peer(session.user_peer_id)
                conclusions_scope = user_peer.conclusions_of(session.user_peer_id)

            conclusions_scope.create([{
                "content": content.strip(),
                "session_id": session.honcho_session_id,
            }])
            logger.info("Created conclusion for %s: %s", session_key, content[:80])
            return True
        except Exception as e:
            logger.error("Failed to create conclusion: %s", e)
            return False

    def seed_ai_identity(self, session_key: str, content: str, source: str = "manual") -> bool:
        """
        从文本内容种入 AI peer 的 Honcho 表示。

        用于从 SOUL.md、导出的聊天或任何结构化描述中
        启动 AI 身份。内容作为 assistant peer 消息发送，
        以便 Honcho 的推理模型可以将其纳入。

        Args:
            session_key: 要关联的会话键。
            content: 要种入的身份/角色内容。
            source: 来源的元数据标签（例如 "soul_md"、"export"）。

        Returns:
            成功时为 True，失败时为 False。
        """
        if not content or not content.strip():
            return False

        session = self._cache.get(session_key)
        if not session:
            logger.warning("No session cached for '%s', skipping AI seed", session_key)
            return False

        assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            logger.warning("No Honcho session cached for '%s', skipping AI seed", session_key)
            return False

        try:
            wrapped = (
                f"<ai_identity_seed>\n"
                f"<source>{source}</source>\n"
                f"\n"
                f"{content.strip()}\n"
                f"</ai_identity_seed>"
            )
            honcho_session.add_messages([assistant_peer.message(wrapped)])
            logger.info("Seeded AI identity from '%s' into %s", source, session_key)
            return True
        except Exception as e:
            logger.error("Failed to seed AI identity: %s", e)
            return False

    def get_ai_representation(self, session_key: str) -> dict[str, str]:
        """
        获取 AI peer 当前的 Honcho 表示。

        Returns:
            带有 'representation' 和 'card' 键的字典，如果不可用则为空字符串。
        """
        session = self._cache.get(session_key)
        if not session:
            return {"representation": "", "card": ""}

        try:
            ctx = self._fetch_peer_context(session.assistant_peer_id)
            return {
                "representation": ctx["representation"] or "",
                "card": "\n".join(ctx["card"]),
            }
        except Exception as e:
            logger.debug("Failed to fetch AI representation: %s", e)
            return {"representation": "", "card": ""}

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有缓存的会话。"""
        return [
            {
                "key": s.key,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
                "message_count": len(s.messages),
            }
            for s in self._cache.values()
        ]
