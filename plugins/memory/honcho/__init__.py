"""Honcho 记忆插件 — Honcho AI 原生记忆的 MemoryProvider。

通过 Honcho SDK 提供跨会话用户建模，包括辩证问答、语义搜索、
peer cards 和持久化结论。Honcho 提供 AI 原生跨会话用户
建模，包括辩证问答、语义搜索、peer cards 和结论。

4 个工具（profile、search、context、conclude）通过
MemoryProvider 接口暴露。

配置：使用现有 Honcho 配置链：
  1. $KCLAW_HOME/honcho.json (profile-scoped)
  2. ~/.honcho/config.json (legacy global)
  3. 环境变量
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具 schema 定义（从 tools/honcho_tools.py 移入）
# ---------------------------------------------------------------------------

PROFILE_SCHEMA = {
    "name": "honcho_profile",
    "description": (
        "Retrieve the user's peer card from Honcho — a curated list of key facts "
        "about them (name, role, preferences, communication style, patterns). "
        "Fast, no LLM reasoning, minimal cost. "
        "Use this at conversation start or when you need a quick factual snapshot."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SEARCH_SCHEMA = {
    "name": "honcho_search",
    "description": (
        "Semantic search over Honcho's stored context about the user. "
        "Returns raw excerpts ranked by relevance — no LLM synthesis. "
        "Cheaper and faster than honcho_context. "
        "Good when you want to find specific past facts and reason over them yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in Honcho's memory.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Token budget for returned context (default 800, max 2000).",
            },
        },
        "required": ["query"],
    },
}

CONTEXT_SCHEMA = {
    "name": "honcho_context",
    "description": (
        "Ask Honcho a natural language question and get a synthesized answer. "
        "Uses Honcho's LLM (dialectic reasoning) — higher cost than honcho_profile or honcho_search. "
        "Can query about any peer: the user (default) or the AI assistant."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A natural language question.",
            },
            "peer": {
                "type": "string",
                "description": "Which peer to query about: 'user' (default) or 'ai'.",
            },
        },
        "required": ["query"],
    },
}

CONCLUDE_SCHEMA = {
    "name": "honcho_conclude",
    "description": (
        "Write a conclusion about the user back to Honcho's memory. "
        "Conclusions are persistent facts that build the user's profile. "
        "Use when the user states a preference, corrects you, or shares "
        "something to remember across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {
                "type": "string",
                "description": "A factual statement about the user to persist.",
            }
        },
        "required": ["conclusion"],
    },
}


ALL_TOOL_SCHEMAS = [PROFILE_SCHEMA, SEARCH_SCHEMA, CONTEXT_SCHEMA, CONCLUDE_SCHEMA]


# ---------------------------------------------------------------------------
# MemoryProvider 实现
# ---------------------------------------------------------------------------

class HonchoMemoryProvider(MemoryProvider):
    """Honcho AI 原生记忆，支持辩证问答和持久化用户建模。"""

    def __init__(self):
        self._manager = None   # HonchoSessionManager
        self._config = None    # HonchoClientConfig
        self._session_key = ""
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

        # B1: recall_mode — set during initialize from config
        self._recall_mode = "hybrid"  # "context", "tools", or "hybrid"

        # B4: First-turn context baking
        self._first_turn_context: Optional[str] = None
        self._first_turn_lock = threading.Lock()

        # B5: Cost-awareness turn counting and cadence
        self._turn_count = 0
        self._injection_frequency = "every-turn"  # or "first-turn"
        self._context_cadence = 1   # minimum turns between context API calls
        self._dialectic_cadence = 1  # minimum turns between dialectic API calls
        self._reasoning_level_cap: Optional[str] = None  # "minimal", "low", "mid", "high"
        self._last_context_turn = -999
        self._last_dialectic_turn = -999

        # Port #1957: lazy session init for tools-only mode
        self._session_initialized = False
        self._lazy_init_kwargs: Optional[dict] = None
        self._lazy_init_session_id: Optional[str] = None

        # Port #4053: cron guard — when True, plugin is fully inactive
        self._cron_skipped = False

    @property
    def name(self) -> str:
        return "honcho"

    def is_available(self) -> bool:
        """检查 Honcho 是否已配置。无网络调用。"""
        try:
            from plugins.memory.honcho.client import HonchoClientConfig
            cfg = HonchoClientConfig.from_global_config()
            # Port #2645: baseUrl-only verification — api_key OR base_url suffices
            return cfg.enabled and bool(cfg.api_key or cfg.base_url)
        except Exception:
            return False

    def save_config(self, values, kclaw_home):
        """将配置写入 $KCLAW_HOME/honcho.json（Honcho SDK 原生格式）。"""
        import json
        from pathlib import Path
        config_path = Path(kclaw_home) / "honcho.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    def get_config_schema(self):
        return [
            {"key": "api_key", "description": "Honcho API key", "secret": True, "env_var": "HONCHO_API_KEY", "url": "https://app.honcho.dev"},
            {"key": "baseUrl", "description": "Honcho base URL (for self-hosted)"},
        ]

    def post_setup(self, kclaw_home: str, config: dict) -> None:
        """在选择 provider 后运行完整的 Honcho 设置向导。"""
        import types
        from plugins.memory.honcho.cli import cmd_setup
        cmd_setup(types.SimpleNamespace())

    def initialize(self, session_id: str, **kwargs) -> None:
        """初始化 Honcho 会话管理器。

        处理：cron guard、recall_mode、session name 解析、
        peer memory mode、SOUL.md ai_peer 同步、memory file 迁移，
        以及初始化时的上下文预热。
        """
        try:
            # ----- Port #4053: cron guard -----
            agent_context = kwargs.get("agent_context", "")
            platform = kwargs.get("platform", "cli")
            if agent_context in ("cron", "flush") or platform == "cron":
                logger.debug("Honcho skipped: cron/flush context (agent_context=%s, platform=%s)",
                             agent_context, platform)
                self._cron_skipped = True
                return

            from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client
            from plugins.memory.honcho.session import HonchoSessionManager

            cfg = HonchoClientConfig.from_global_config()
            if not cfg.enabled or not (cfg.api_key or cfg.base_url):
                logger.debug("Honcho not configured — plugin inactive")
                return

            # Override peer_name with gateway user_id for per-user memory scoping.
            # CLI sessions won't have user_id, so the config default is preserved.
            _gw_user_id = kwargs.get("user_id")
            if _gw_user_id:
                cfg.peer_name = _gw_user_id

            self._config = cfg

            # ----- B1: recall_mode from config -----
            self._recall_mode = cfg.recall_mode  # "context", "tools", or "hybrid"
            logger.debug("Honcho recall_mode: %s", self._recall_mode)

            # ----- B5: cost-awareness config -----
            try:
                raw = cfg.raw or {}
                self._injection_frequency = raw.get("injectionFrequency", "every-turn")
                self._context_cadence = int(raw.get("contextCadence", 1))
                self._dialectic_cadence = int(raw.get("dialecticCadence", 1))
                cap = raw.get("reasoningLevelCap")
                if cap and cap in ("minimal", "low", "mid", "high"):
                    self._reasoning_level_cap = cap
            except Exception as e:
                logger.debug("Honcho cost-awareness config parse error: %s", e)

            # ----- Port #1969: aiPeer sync from SOUL.md — REMOVED -----
            # SOUL.md is persona content, not identity config. aiPeer should
            # only come from honcho.json (host block or root) or the default.
            # See scratch/memory-plugin-ux-specs.md #10 for rationale.

            # ----- Port #1957: lazy session init for tools-only mode -----
            if self._recall_mode == "tools":
                # Defer actual session creation until first tool call
                self._lazy_init_kwargs = kwargs
                self._lazy_init_session_id = session_id
                # Still need a client reference for _ensure_session
                self._config = cfg
                logger.debug("Honcho tools-only mode — deferring session init until first tool call")
                return

            # ----- Eager init (context or hybrid mode) -----
            self._do_session_init(cfg, session_id, **kwargs)

        except ImportError:
            logger.debug("honcho-ai package not installed — plugin inactive")
        except Exception as e:
            logger.warning("Honcho init failed: %s", e)
            self._manager = None

    def _do_session_init(self, cfg, session_id: str, **kwargs) -> None:
        """急切和惰性路径共用的会话初始化逻辑。"""
        from plugins.memory.honcho.client import get_honcho_client
        from plugins.memory.honcho.session import HonchoSessionManager

        client = get_honcho_client(cfg)
        self._manager = HonchoSessionManager(
            honcho=client,
            config=cfg,
            context_tokens=cfg.context_tokens,
        )

        # ----- B3: resolve_session_name -----
        session_title = kwargs.get("session_title")
        self._session_key = (
            cfg.resolve_session_name(session_title=session_title, session_id=session_id)
            or session_id
            or "kclaw-default"
        )
        logger.debug("Honcho session key resolved: %s", self._session_key)

        # Create session eagerly
        session = self._manager.get_or_create(self._session_key)
        self._session_initialized = True

        # ----- B6: Memory file migration (one-time, for new sessions) -----
        try:
            if not session.messages:
                from kclaw_constants import get_kclaw_home
                mem_dir = str(get_kclaw_home() / "memories")
                self._manager.migrate_memory_files(self._session_key, mem_dir)
                logger.debug("Honcho memory file migration attempted for new session: %s", self._session_key)
        except Exception as e:
            logger.debug("Honcho memory file migration skipped: %s", e)

        # ----- B7: Pre-warming context at init -----
        if self._recall_mode in ("context", "hybrid"):
            try:
                self._manager.prefetch_context(self._session_key)
                self._manager.prefetch_dialectic(self._session_key, "What should I know about this user?")
                logger.debug("Honcho pre-warm threads started for session: %s", self._session_key)
            except Exception as e:
                logger.debug("Honcho pre-warm failed: %s", e)

    def _ensure_session(self) -> bool:
        """惰性初始化 Honcho 会话（用于仅工具模式）。

        如果 manager 就绪则返回 True，否则返回 False。
        """
        if self._manager and self._session_initialized:
            return True
        if self._cron_skipped:
            return False
        if not self._config or not self._lazy_init_kwargs:
            return False

        try:
            self._do_session_init(
                self._config,
                self._lazy_init_session_id or "kclaw-default",
                **self._lazy_init_kwargs,
            )
            # Clear lazy refs
            self._lazy_init_kwargs = None
            self._lazy_init_session_id = None
            return self._manager is not None
        except Exception as e:
            logger.warning("Honcho lazy session init failed: %s", e)
            return False

    def _format_first_turn_context(self, ctx: dict) -> str:
        """将预取上下文字典格式化为可读的系统提示块。"""
        parts = []

        rep = ctx.get("representation", "")
        if rep:
            parts.append(f"## User Representation\n{rep}")

        card = ctx.get("card", "")
        if card:
            parts.append(f"## User Peer Card\n{card}")

        ai_rep = ctx.get("ai_representation", "")
        if ai_rep:
            parts.append(f"## AI Self-Representation\n{ai_rep}")

        ai_card = ctx.get("ai_card", "")
        if ai_card:
            parts.append(f"## AI Identity Card\n{ai_card}")

        if not parts:
            return ""
        return "\n\n".join(parts)

    def system_prompt_block(self) -> str:
        """返回系统提示文本，根据 recall_mode 调整。

        B4：在第一次调用时，获取并烘焙完整的 Honcho 上下文
        （用户表示、peer card、AI 表示、连续性综合）。
        后续调用返回缓存的块以保持提示缓存稳定性。
        """
        if self._cron_skipped:
            return ""
        if not self._manager or not self._session_key:
            # tools-only mode without session yet still returns a minimal block
            if self._recall_mode == "tools" and self._config:
                return (
                    "# Honcho Memory\n"
                    "Active (tools-only mode). Use honcho_profile, honcho_search, "
                    "honcho_context, and honcho_conclude tools to access user memory."
                )
            return ""

        # ----- B4: First-turn context baking -----
        first_turn_block = ""
        if self._recall_mode in ("context", "hybrid"):
            with self._first_turn_lock:
                if self._first_turn_context is None:
                    # First call — fetch and cache
                    try:
                        ctx = self._manager.get_prefetch_context(self._session_key)
                        self._first_turn_context = self._format_first_turn_context(ctx) if ctx else ""
                    except Exception as e:
                        logger.debug("Honcho first-turn context fetch failed: %s", e)
                        self._first_turn_context = ""
                first_turn_block = self._first_turn_context

        # ----- B1: adapt text based on recall_mode -----
        if self._recall_mode == "context":
            header = (
                "# Honcho Memory\n"
                "Active (context-injection mode). Relevant user context is automatically "
                "injected before each turn. No memory tools are available — context is "
                "managed automatically."
            )
        elif self._recall_mode == "tools":
            header = (
                "# Honcho Memory\n"
                "Active (tools-only mode). Use honcho_profile for a quick factual snapshot, "
                "honcho_search for raw excerpts, honcho_context for synthesized answers, "
                "honcho_conclude to save facts about the user. "
                "No automatic context injection — you must use tools to access memory."
            )
        else:  # hybrid
            header = (
                "# Honcho Memory\n"
                "Active (hybrid mode). Relevant context is auto-injected AND memory tools are available. "
                "Use honcho_profile for a quick factual snapshot, "
                "honcho_search for raw excerpts, honcho_context for synthesized answers, "
                "honcho_conclude to save facts about the user."
            )

        if first_turn_block:
            return f"{header}\n\n{first_turn_block}"
        return header

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """从后台线程返回预取的 dialectic 上下文。

        B1：当 recall_mode 为 "tools" 时返回空（无注入）。
        B5：遵循 injection_frequency — "first-turn" 在 turn 0 后返回缓存/空。
        Port #3265：截断到 context_tokens 预算。
        """
        if self._cron_skipped:
            return ""

        # B1: tools-only mode — no auto-injection
        if self._recall_mode == "tools":
            return ""

        # B5: injection_frequency — if "first-turn" and past first turn, return empty
        if self._injection_frequency == "first-turn" and self._turn_count > 0:
            return ""

        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""

        # ----- Port #3265: token budget enforcement -----
        result = self._truncate_to_budget(result)

        return f"## Honcho Context\n{result}"

    def _truncate_to_budget(self, text: str) -> str:
        """如果设置了 context_tokens 预算，则截断文本以适应。"""
        if not self._config or not self._config.context_tokens:
            return text
        budget_chars = self._config.context_tokens * 4  # conservative char estimate
        if len(text) <= budget_chars:
            return text
        # Truncate at word boundary
        truncated = text[:budget_chars]
        last_space = truncated.rfind(" ")
        if last_space > budget_chars * 0.8:
            truncated = truncated[:last_space]
        return truncated + " …"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """为即将到来的 turn 触发后台 dialectic 查询。

        B5：在触发后台线程前检查节奏。
        """
        if self._cron_skipped:
            return
        if not self._manager or not self._session_key or not query:
            return

        # B1: tools-only mode — no prefetch
        if self._recall_mode == "tools":
            return

        # B5: cadence check — skip if too soon since last dialectic call
        if self._dialectic_cadence > 1:
            if (self._turn_count - self._last_dialectic_turn) < self._dialectic_cadence:
                logger.debug("Honcho dialectic prefetch skipped: cadence %d, turns since last: %d",
                             self._dialectic_cadence, self._turn_count - self._last_dialectic_turn)
                return

        self._last_dialectic_turn = self._turn_count

        def _run():
            try:
                result = self._manager.dialectic_query(
                    self._session_key, query, peer="user"
                )
                if result and result.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = result
            except Exception as e:
                logger.debug("Honcho prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="honcho-prefetch"
        )
        self._prefetch_thread.start()

        # Also fire context prefetch if cadence allows
        if self._context_cadence <= 1 or (self._turn_count - self._last_context_turn) >= self._context_cadence:
            self._last_context_turn = self._turn_count
            try:
                self._manager.prefetch_context(self._session_key, query)
            except Exception as e:
                logger.debug("Honcho context prefetch failed: %s", e)

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """跟踪 turn 计数用于节奏和 injection_frequency 逻辑。"""
        self._turn_count = turn_number

    @staticmethod
    def _chunk_message(content: str, limit: int) -> list[str]:
        """将内容分割成适合 Honcho 消息限制的块。

        尽可能在段落边界分割，退回到句子边界，
        然后是单词边界。每个续接块以 "[continued] " 为前缀，
        以便 Honcho 的表示引擎可以重建完整消息。
        """
        if len(content) <= limit:
            return [content]

        prefix = "[continued] "
        prefix_len = len(prefix)
        chunks = []
        remaining = content
        first = True
        while remaining:
            effective = limit if first else limit - prefix_len
            if len(remaining) <= effective:
                chunks.append(remaining if first else prefix + remaining)
                break

            segment = remaining[:effective]

            # Try paragraph break, then sentence, then word
            cut = segment.rfind("\n\n")
            if cut < effective * 0.3:
                cut = segment.rfind(". ")
                if cut >= 0:
                    cut += 2  # include the period and space
            if cut < effective * 0.3:
                cut = segment.rfind(" ")
            if cut < effective * 0.3:
                cut = effective  # hard cut

            chunk = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip()
            if not first:
                chunk = prefix + chunk
            chunks.append(chunk)
            first = False

        return chunks

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """在 Honcho 中记录对话 turn（非阻塞）。

        超过 Honcho API 限制（默认 25k 字符）的消息
        被分割成带有续接标记的多个消息。
        """
        if self._cron_skipped:
            return
        if not self._manager or not self._session_key:
            return

        msg_limit = self._config.message_max_chars if self._config else 25000

        def _sync():
            try:
                session = self._manager.get_or_create(self._session_key)
                for chunk in self._chunk_message(user_content, msg_limit):
                    session.add_message("user", chunk)
                for chunk in self._chunk_message(assistant_content, msg_limit):
                    session.add_message("assistant", chunk)
                self._manager._flush_session(session)
            except Exception as e:
                logger.debug("Honcho sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="honcho-sync"
        )
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """将内置用户 profile 写入镜像为 Honcho 结论。"""
        if action != "add" or target != "user" or not content:
            return
        if self._cron_skipped:
            return
        if not self._manager or not self._session_key:
            return

        def _write():
            try:
                self._manager.create_conclusion(self._session_key, content)
            except Exception as e:
                logger.debug("Honcho memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="honcho-memwrite")
        t.start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """在会话结束时刷新所有待处理消息到 Honcho。"""
        if self._cron_skipped:
            return
        if not self._manager:
            return
        # Wait for pending sync
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)
        try:
            self._manager.flush_all()
        except Exception as e:
            logger.debug("Honcho session-end flush failed: %s", e)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回工具 schema，遵循 recall_mode。

        B1：仅上下文模式隐藏所有工具。
        """
        if self._cron_skipped:
            return []
        if self._recall_mode == "context":
            return []
        return list(ALL_TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        """处理 Honcho 工具调用，对于仅工具模式使用惰性会话初始化。"""
        if self._cron_skipped:
            return tool_error("Honcho is not active (cron context).")

        # Port #1957: ensure session is initialized for tools-only mode
        if not self._session_initialized:
            if not self._ensure_session():
                return tool_error("Honcho session could not be initialized.")

        if not self._manager or not self._session_key:
            return tool_error("Honcho is not active for this session.")

        try:
            if tool_name == "honcho_profile":
                card = self._manager.get_peer_card(self._session_key)
                if not card:
                    return json.dumps({"result": "No profile facts available yet."})
                return json.dumps({"result": card})

            elif tool_name == "honcho_search":
                query = args.get("query", "")
                if not query:
                    return tool_error("Missing required parameter: query")
                max_tokens = min(int(args.get("max_tokens", 800)), 2000)
                result = self._manager.search_context(
                    self._session_key, query, max_tokens=max_tokens
                )
                if not result:
                    return json.dumps({"result": "No relevant context found."})
                return json.dumps({"result": result})

            elif tool_name == "honcho_context":
                query = args.get("query", "")
                if not query:
                    return tool_error("Missing required parameter: query")
                peer = args.get("peer", "user")
                result = self._manager.dialectic_query(
                    self._session_key, query, peer=peer
                )
                return json.dumps({"result": result or "No result from Honcho."})

            elif tool_name == "honcho_conclude":
                conclusion = args.get("conclusion", "")
                if not conclusion:
                    return tool_error("Missing required parameter: conclusion")
                ok = self._manager.create_conclusion(self._session_key, conclusion)
                if ok:
                    return json.dumps({"result": f"Conclusion saved: {conclusion}"})
                return tool_error("Failed to save conclusion.")

            return tool_error(f"Unknown tool: {tool_name}")

        except Exception as e:
            logger.error("Honcho tool %s failed: %s", tool_name, e)
            return tool_error(f"Honcho {tool_name} failed: {e}")

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        # Flush any remaining messages
        if self._manager:
            try:
                self._manager.flush_all()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 插件入口点
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """将 Honcho 注册为记忆提供程序插件。"""
    ctx.register_memory_provider(HonchoMemoryProvider())
