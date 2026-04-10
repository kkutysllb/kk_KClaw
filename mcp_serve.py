"""
KClaw MCP Server — 将消息对话暴露为 MCP 工具。

启动一个 stdio MCP 服务器，让任何 MCP 客户端（Claude Code、Cursor、Codex 等）
可以列出对话、读取消息历史、发送消息、轮询实时事件，
以及管理所有已连接平台的审批请求。

匹配 OpenClaw 的 9 工具 MCP 通道桥接面：
  conversations_list, conversation_get, messages_read, attachments_fetch,
  events_poll, events_wait, messages_send, permissions_list_open,
  permissions_respond

额外提供：channels_list（KClaw 特有扩展）

使用方法：
    kclaw mcp serve
    kclaw mcp serve --verbose

MCP 客户端配置（例如 claude_desktop_config.json）：
    {
        "mcpServers": {
            "kclaw": {
                "command": "kclaw",
                "args": ["mcp", "serve"]
            }
        }
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("kclaw.mcp_serve")

# ---------------------------------------------------------------------------
# 延迟 MCP SDK 导入
# ---------------------------------------------------------------------------

_MCP_SERVER_AVAILABLE = False
try:
    from mcp.server.fastmcp import FastMCP

    _MCP_SERVER_AVAILABLE = True
except ImportError:
    FastMCP = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_sessions_dir() -> Path:
    """使用 KCLAW_HOME 返回 sessions 目录。"""
    try:
        from kclaw_constants import get_kclaw_home
        return get_kclaw_home() / "sessions"
    except ImportError:
        return Path(os.environ.get("KCLAW_HOME", Path.home() / ".kclaw")) / "sessions"


def _get_session_db():
    """获取 SessionDB 实例用于读取消息记录。"""
    try:
        from kclaw_state import SessionDB
        return SessionDB()
    except Exception as e:
        logger.debug("SessionDB 不可用: %s", e)
        return None


def _load_sessions_index() -> dict:
    """直接加载 gateway 的 sessions.json 索引。

    返回 session_key -> entry_dict 的字典，包含平台路由信息。
    这样可以避免导入需要 GatewayConfig 的完整 SessionStore。
    """
    sessions_file = _get_sessions_dir() / "sessions.json"
    if not sessions_file.exists():
        return {}
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("加载 sessions.json 失败: %s", e)
        return {}


def _load_channel_directory() -> dict:
    """加载缓存的通道目录以获取可用目标。"""
    try:
        from kclaw_constants import get_kclaw_home
        directory_file = get_kclaw_home() / "channel_directory.json"
    except ImportError:
        directory_file = Path(
            os.environ.get("KCLAW_HOME", Path.home() / ".kclaw")
        ) / "channel_directory.json"

    if not directory_file.exists():
        return {}
    try:
        with open(directory_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("加载 channel_directory.json 失败: %s", e)
        return {}


def _extract_message_content(msg: dict) -> str:
    """从消息中提取文本内容，处理多部分内容。"""
    content = msg.get("content", "")
    if isinstance(content, list):
        text_parts = [
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "\n".join(text_parts)
    return str(content) if content else ""


def _extract_attachments(msg: dict) -> List[dict]:
    """从消息中提取非文本附件。

    查找：多部分图片/文件内容块、文本中的 MEDIA: 标签、
    图片 URL 和文件引用。
    """
    attachments = []
    content = msg.get("content", "")

    # 多部分内容块（image_url、file 等）
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "")
            if ptype == "image_url":
                url = part.get("image_url", {}).get("url", "") if isinstance(part.get("image_url"), dict) else ""
                if url:
                    attachments.append({"type": "image", "url": url})
            elif ptype == "image":
                url = part.get("url", part.get("source", {}).get("url", ""))
                if url:
                    attachments.append({"type": "image", "url": url})
            elif ptype not in ("text",):
                # 未知的非文本内容类型
                attachments.append({"type": ptype, "data": part})

    # 文本内容中的 MEDIA: 标签
    text = _extract_message_content(msg)
    if text:
        media_pattern = re.compile(r'MEDIA:\s*(\S+)')
        for match in media_pattern.finditer(text):
            path = match.group(1)
            attachments.append({"type": "media", "path": path})

    return attachments


# ---------------------------------------------------------------------------
# 事件桥接 — 轮询 SessionDB 获取新消息，维护事件队列
# ---------------------------------------------------------------------------

QUEUE_LIMIT = 1000
POLL_INTERVAL = 0.2  # 数据库轮询间隔秒数（200ms）


@dataclass
class QueueEvent:
    """桥接器内存队列中的事件。"""
    cursor: int
    type: str  # "message", "approval_requested", "approval_resolved"
    session_key: str = ""
    data: dict = field(default_factory=dict)


class EventBridge:
    """后台轮询器，监视 SessionDB 的新消息并维护带等待器支持的内存事件队列。

    这是 KClaw 版的 OpenClaw WebSocket 网关桥接器。
    我们轮询 SQLite 数据库而非 WebSocket 事件来检测变化。
    """

    def __init__(self):
        self._queue: List[QueueEvent] = []
        self._cursor = 0
        self._lock = threading.Lock()
        self._new_event = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_poll_timestamps: Dict[str, float] = {}  # session_key -> unix 时间戳
        # 内存中的审批跟踪（从事件填充）
        self._pending_approvals: Dict[str, dict] = {}
        # mtime 缓存 — 文件未变化时跳过昂贵操作
        self._sessions_json_mtime: float = 0.0
        self._state_db_mtime: float = 0.0
        self._cached_sessions_index: dict = {}

    def start(self):
        """启动后台轮询线程。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.debug("EventBridge 已启动")

    def stop(self):
        """停止后台轮询线程。"""
        self._running = False
        self._new_event.set()  # 唤醒所有等待者
        if self._thread:
            self._thread.join(timeout=5)
        logger.debug("EventBridge 已停止")

    def poll_events(
        self,
        after_cursor: int = 0,
        session_key: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """返回 after_cursor 之后的事件，可按 session_key 过滤。"""
        with self._lock:
            events = [
                e for e in self._queue
                if e.cursor > after_cursor
                and (not session_key or e.session_key == session_key)
            ][:limit]

        next_cursor = events[-1].cursor if events else after_cursor
        return {
            "events": [
                {"cursor": e.cursor, "type": e.type,
                 "session_key": e.session_key, **e.data}
                for e in events
            ],
            "next_cursor": next_cursor,
        }

    def wait_for_event(
        self,
        after_cursor: int = 0,
        session_key: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> Optional[dict]:
        """阻塞直到匹配的事件到达或超时过期。"""
        deadline = time.monotonic() + (timeout_ms / 1000.0)

        while time.monotonic() < deadline:
            with self._lock:
                for e in self._queue:
                    if e.cursor > after_cursor and (
                        not session_key or e.session_key == session_key
                    ):
                        return {
                            "cursor": e.cursor, "type": e.type,
                            "session_key": e.session_key, **e.data,
                        }

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._new_event.clear()
            self._new_event.wait(timeout=min(remaining, POLL_INTERVAL))

        return None

    def list_pending_approvals(self) -> List[dict]:
        """列出此桥接会话期间观察到的审批请求。"""
        with self._lock:
            return sorted(
                self._pending_approvals.values(),
                key=lambda a: a.get("created_at", ""),
            )

    def respond_to_approval(self, approval_id: str, decision: str) -> dict:
        """解决待处理的审批（尽力而为，无网关 IPC）。"""
        with self._lock:
            approval = self._pending_approvals.pop(approval_id, None)

        if not approval:
            return {"error": f"审批未找到: {approval_id}"}

        self._enqueue(QueueEvent(
            cursor=0,  # 将由 _enqueue 设置
            type="approval_resolved",
            session_key=approval.get("session_key", ""),
            data={"approval_id": approval_id, "decision": decision},
        ))

        return {"resolved": True, "approval_id": approval_id, "decision": decision}

    def _enqueue(self, event: QueueEvent) -> None:
        """将事件添加到队列并唤醒所有等待者。"""
        with self._lock:
            self._cursor += 1
            event.cursor = self._cursor
            self._queue.append(event)
            # 修剪队列到限制大小
            while len(self._queue) > QUEUE_LIMIT:
                self._queue.pop(0)
        self._new_event.set()

    def _poll_loop(self):
        """后台循环：轮询 SessionDB 获取新消息。"""
        db = _get_session_db()
        if not db:
            logger.warning("EventBridge: SessionDB 不可用，事件轮询已禁用")
            return

        while self._running:
            try:
                self._poll_once(db)
            except Exception as e:
                logger.debug("EventBridge 轮询错误: %s", e)
            time.sleep(POLL_INTERVAL)

    def _poll_once(self, db):
        """检查所有会话的新消息。

        使用 sessions.json 和 state.db 的 mtime 检查来跳过
        无变化时的操作 — 使 200ms 轮询基本上无开销。
        """
        # 检查 sessions.json 是否已更改（mtime 检查约 1μs）
        sessions_file = _get_sessions_dir() / "sessions.json"
        try:
            sj_mtime = sessions_file.stat().st_mtime if sessions_file.exists() else 0.0
        except OSError:
            sj_mtime = 0.0

        if sj_mtime != self._sessions_json_mtime:
            self._sessions_json_mtime = sj_mtime
            self._cached_sessions_index = _load_sessions_index()

        # 检查 state.db 是否已更改
        try:
            from kclaw_constants import get_kclaw_home
            db_file = get_kclaw_home() / "state.db"
        except ImportError:
            db_file = Path(os.environ.get("KCLAW_HOME", Path.home() / ".kclaw")) / "state.db"

        try:
            db_mtime = db_file.stat().st_mtime if db_file.exists() else 0.0
        except OSError:
            db_mtime = 0.0

        if db_mtime == self._state_db_mtime and sj_mtime == self._sessions_json_mtime:
            return  # 自上次轮询以来无变化 — 完全跳过

        self._state_db_mtime = db_mtime
        entries = self._cached_sessions_index

        for session_key, entry in entries.items():
            session_id = entry.get("session_id", "")
            if not session_id:
                continue

            last_seen = self._last_poll_timestamps.get(session_key, 0.0)

            try:
                messages = db.get_messages(session_id)
            except Exception:
                continue

            if not messages:
                continue

            # 将时间戳规范化为浮点数以便比较
            def _ts_float(ts) -> float:
                if isinstance(ts, (int, float)):
                    return float(ts)
                if isinstance(ts, str) and ts:
                    try:
                        return float(ts)
                    except ValueError:
                        # ISO 字符串 — 解析为 epoch
                        try:
                            from datetime import datetime
                            return datetime.fromisoformat(ts).timestamp()
                        except Exception:
                            return 0.0
                return 0.0

            # 查找比上次看到的时间戳更新的消息
            new_messages = []
            for msg in messages:
                ts = _ts_float(msg.get("timestamp", 0))
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                if ts > last_seen:
                    new_messages.append(msg)

            for msg in new_messages:
                content = _extract_message_content(msg)
                if not content:
                    continue
                self._enqueue(QueueEvent(
                    cursor=0,
                    type="message",
                    session_key=session_key,
                    data={
                        "role": msg.get("role", ""),
                        "content": content[:500],
                        "timestamp": str(msg.get("timestamp", "")),
                        "message_id": str(msg.get("id", "")),
                    },
                ))

            # 更新到最后一条消息的时间戳
            all_ts = [_ts_float(m.get("timestamp", 0)) for m in messages]
            if all_ts:
                latest = max(all_ts)
                if latest > last_seen:
                    self._last_poll_timestamps[session_key] = latest


# ---------------------------------------------------------------------------
# MCP 服务器
# ---------------------------------------------------------------------------

def create_mcp_server(event_bridge: Optional[EventBridge] = None) -> "FastMCP":
    """创建并返回已注册所有工具的 KClaw MCP 服务器。"""
    if not _MCP_SERVER_AVAILABLE:
        raise ImportError(
            "MCP 服务器需要 'mcp' 包。"
            "安装方式：pip install 'kclaw[mcp]'"
        )

    mcp = FastMCP(
        "kclaw",
        instructions=(
            "KClaw Agent 消息桥接。使用这些工具与 Telegram、Discord、Slack、"
            "WhatsApp、Signal、Matrix 等已连接平台上的对话进行交互。"
        ),
    )

    bridge = event_bridge or EventBridge()

    # -- conversations_list ------------------------------------------------

    @mcp.tool()
    def conversations_list(
        platform: Optional[str] = None,
        limit: int = 50,
        search: Optional[str] = None,
    ) -> str:
        """列出已连接平台上的活跃消息对话。

        返回带有 session key（用于 messages_read）的对话，
        以及平台、聊天类型、显示名称和最后活动时间。

        参数：
            platform: 按平台名称过滤（telegram、discord、slack 等）
            limit: 返回的最大对话数量（默认 50）
            search: 按名称过滤对话的可选文本
        """
        entries = _load_sessions_index()
        conversations = []

        for key, entry in entries.items():
            origin = entry.get("origin", {})
            entry_platform = entry.get("platform") or origin.get("platform", "")

            if platform and entry_platform.lower() != platform.lower():
                continue

            display_name = entry.get("display_name", "")
            chat_name = origin.get("chat_name", "")
            if search:
                search_lower = search.lower()
                if (search_lower not in display_name.lower()
                        and search_lower not in chat_name.lower()
                        and search_lower not in key.lower()):
                    continue

            conversations.append({
                "session_key": key,
                "session_id": entry.get("session_id", ""),
                "platform": entry_platform,
                "chat_type": entry.get("chat_type", origin.get("chat_type", "")),
                "display_name": display_name,
                "chat_name": chat_name,
                "user_name": origin.get("user_name", ""),
                "updated_at": entry.get("updated_at", ""),
            })

        conversations.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        conversations = conversations[:limit]

        return json.dumps({
            "count": len(conversations),
            "conversations": conversations,
        }, indent=2)

    # -- conversation_get --------------------------------------------------

    @mcp.tool()
    def conversation_get(session_key: str) -> str:
        """通过 session key 获取一个对话的详细信息。

        参数：
            session_key: conversations_list 返回的 session key
        """
        entries = _load_sessions_index()
        entry = entries.get(session_key)

        if not entry:
            return json.dumps({"error": f"对话未找到: {session_key}"})

        origin = entry.get("origin", {})
        return json.dumps({
            "session_key": session_key,
            "session_id": entry.get("session_id", ""),
            "platform": entry.get("platform") or origin.get("platform", ""),
            "chat_type": entry.get("chat_type", origin.get("chat_type", "")),
            "display_name": entry.get("display_name", ""),
            "user_name": origin.get("user_name", ""),
            "chat_name": origin.get("chat_name", ""),
            "chat_id": origin.get("chat_id", ""),
            "thread_id": origin.get("thread_id"),
            "updated_at": entry.get("updated_at", ""),
            "created_at": entry.get("created_at", ""),
            "input_tokens": entry.get("input_tokens", 0),
            "output_tokens": entry.get("output_tokens", 0),
            "total_tokens": entry.get("total_tokens", 0),
        }, indent=2)

    # -- messages_read -----------------------------------------------------

    @mcp.tool()
    def messages_read(
        session_key: str,
        limit: int = 50,
    ) -> str:
        """从对话中读取最近的消息。

        按时间顺序返回消息历史，包含每条消息的 role、content 和 timestamp。

        参数：
            session_key: conversations_list 返回的 session key
            limit: 返回的最大消息数量（默认 50，最新优先）
        """
        entries = _load_sessions_index()
        entry = entries.get(session_key)
        if not entry:
            return json.dumps({"error": f"对话未找到: {session_key}"})

        session_id = entry.get("session_id", "")
        if not session_id:
            return json.dumps({"error": "此对话没有 session ID"})

        db = _get_session_db()
        if not db:
            return json.dumps({"error": "会话数据库不可用"})

        try:
            all_messages = db.get_messages(session_id)
        except Exception as e:
            return json.dumps({"error": f"读取消息失败: {e}"})

        filtered = []
        for msg in all_messages:
            role = msg.get("role", "")
            if role in ("user", "assistant"):
                content = _extract_message_content(msg)
                if content:
                    filtered.append({
                        "id": str(msg.get("id", "")),
                        "role": role,
                        "content": content[:2000],
                        "timestamp": msg.get("timestamp", ""),
                    })

        messages = filtered[-limit:]

        return json.dumps({
            "session_key": session_key,
            "count": len(messages),
            "total_in_session": len(filtered),
            "messages": messages,
        }, indent=2)

    # -- attachments_fetch -------------------------------------------------

    @mcp.tool()
    def attachments_fetch(
        session_key: str,
        message_id: str,
    ) -> str:
        """列出对话中一条消息的非文本附件。

        从指定消息中提取图片、媒体文件和其他非文本内容块。

        参数：
            session_key: conversations_list 返回的 session key
            message_id: messages_read 返回的消息 ID
        """
        entries = _load_sessions_index()
        entry = entries.get(session_key)
        if not entry:
            return json.dumps({"error": f"对话未找到: {session_key}"})

        session_id = entry.get("session_id", "")
        if not session_id:
            return json.dumps({"error": "此对话没有 session ID"})

        db = _get_session_db()
        if not db:
            return json.dumps({"error": "会话数据库不可用"})

        try:
            all_messages = db.get_messages(session_id)
        except Exception as e:
            return json.dumps({"error": f"读取消息失败: {e}"})

        # 查找目标消息
        target_msg = None
        for msg in all_messages:
            if str(msg.get("id", "")) == message_id:
                target_msg = msg
                break

        if not target_msg:
            return json.dumps({"error": f"消息未找到: {message_id}"})

        attachments = _extract_attachments(target_msg)

        return json.dumps({
            "message_id": message_id,
            "count": len(attachments),
            "attachments": attachments,
        }, indent=2)

    # -- events_poll -------------------------------------------------------

    @mcp.tool()
    def events_poll(
        after_cursor: int = 0,
        session_key: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """轮询自光标位置以来的新对话事件。

        返回自给定光标以来发生的事件。使用返回的 next_cursor 值
        进行后续轮询。

        事件类型：message、approval_requested、approval_resolved

        参数：
            after_cursor: 返回此光标之后的事件（0 表示全部）
            session_key: 可选过滤到单个对话
            limit: 返回的最大事件数量（默认 20）
        """
        result = bridge.poll_events(
            after_cursor=after_cursor,
            session_key=session_key,
            limit=limit,
        )
        return json.dumps(result, indent=2)

    # -- events_wait -------------------------------------------------------

    @mcp.tool()
    def events_wait(
        after_cursor: int = 0,
        session_key: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> str:
        """等待下一个对话事件（长轮询）。

        阻塞直到匹配的事件到达或超时过期。
        使用此方法可实现近实时事件传递而无需轮询。

        参数：
            after_cursor: 等待此光标之后的事件
            session_key: 可选过滤到单个对话
            timeout_ms: 最大等待时间（毫秒，默认 30000）
        """
        event = bridge.wait_for_event(
            after_cursor=after_cursor,
            session_key=session_key,
            timeout_ms=min(timeout_ms, 300000),  # 上限 5 分钟
        )
        if event:
            return json.dumps({"event": event}, indent=2)
        return json.dumps({"event": None, "reason": "timeout"}, indent=2)

    # -- messages_send -----------------------------------------------------

    @mcp.tool()
    def messages_send(
        target: str,
        message: str,
    ) -> str:
        """向平台对话发送消息。

        目标格式为 "platform:chat_id" — 与 channels_list 工具
        使用的格式相同。也可以使用会自动解析的友好通道名称。

        示例：
            target="telegram:6308981865"
            target="discord:#general"
            target="slack:#engineering"

        参数：
            target: "platform:identifier" 格式的平台目标
            message: 要发送的消息文本
        """
        if not target or not message:
            return json.dumps({"error": "target 和 message 都是必需的"})

        try:
            from tools.send_message_tool import send_message_tool
            result_str = send_message_tool(
                {"action": "send", "target": target, "message": message}
            )
            return result_str
        except ImportError:
            return json.dumps({"error": "发送消息工具不可用"})
        except Exception as e:
            return json.dumps({"error": f"发送失败: {e}"})

    # -- channels_list -----------------------------------------------------

    @mcp.tool()
    def channels_list(platform: Optional[str] = None) -> str:
        """列出跨平台可用的消息通道和目标。

        返回可以发送消息的通道。这里返回的目标字符串
        可直接用于 messages_send 工具。

        参数：
            platform: 按平台名称过滤（telegram、discord、slack 等）
        """
        directory = _load_channel_directory()
        if not directory:
            entries = _load_sessions_index()
            targets = []
            seen = set()
            for key, entry in entries.items():
                origin = entry.get("origin", {})
                p = entry.get("platform") or origin.get("platform", "")
                chat_id = origin.get("chat_id", "")
                if not p or not chat_id:
                    continue
                if platform and p.lower() != platform.lower():
                    continue
                target_str = f"{p}:{chat_id}"
                if target_str in seen:
                    continue
                seen.add(target_str)
                targets.append({
                    "target": target_str,
                    "platform": p,
                    "name": entry.get("display_name") or origin.get("chat_name", ""),
                    "chat_type": entry.get("chat_type", origin.get("chat_type", "")),
                })
            return json.dumps({"count": len(targets), "channels": targets}, indent=2)

        channels = []
        for plat, entries_list in directory.items():
            if platform and plat.lower() != platform.lower():
                continue
            if isinstance(entries_list, list):
                for ch in entries_list:
                    if isinstance(ch, dict):
                        chat_id = ch.get("id", ch.get("chat_id", ""))
                        channels.append({
                            "target": f"{plat}:{chat_id}" if chat_id else plat,
                            "platform": plat,
                            "name": ch.get("name", ch.get("display_name", "")),
                            "chat_type": ch.get("type", ""),
                        })

        return json.dumps({"count": len(channels), "channels": channels}, indent=2)

    # -- permissions_list_open ---------------------------------------------

    @mcp.tool()
    def permissions_list_open() -> str:
        """列出此桥接会话期间观察到的待处理审批请求。

        返回自桥接启动以来看到的 exec 和插件审批请求。
        审批仅在会话期间有效 — 不包含桥接连接之前的旧审批。
        """
        approvals = bridge.list_pending_approvals()
        return json.dumps({
            "count": len(approvals),
            "approvals": approvals,
        }, indent=2)

    # -- permissions_respond -----------------------------------------------

    @mcp.tool()
    def permissions_respond(
        id: str,
        decision: str,
    ) -> str:
        """响应待处理的审批请求。

        参数：
            id: permissions_list_open 返回的审批 ID
            decision: 之一 "allow-once"、"allow-always" 或 "deny"
        """
        if decision not in ("allow-once", "allow-always", "deny"):
            return json.dumps({
                "error": f"无效的决定: {decision}。"
                         f"必须是 allow-once、allow-always 或 deny"
            })

        result = bridge.respond_to_approval(id, decision)
        return json.dumps(result, indent=2)

    return mcp


# ---------------------------------------------------------------------------
# 入口点
# ---------------------------------------------------------------------------

def run_mcp_server(verbose: bool = False) -> None:
    """在 stdio 上启动 KClaw MCP 服务器。"""
    if not _MCP_SERVER_AVAILABLE:
        print(
            "错误：MCP 服务器需要 'mcp' 包。\n"
            "安装方式：pip install 'kclaw[mcp]'",
            file=sys.stderr,
        )
        sys.exit(1)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    bridge = EventBridge()
    bridge.start()

    server = create_mcp_server(event_bridge=bridge)

    import asyncio

    async def _run():
        try:
            await server.run_stdio_async()
        finally:
            bridge.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        bridge.stop()
