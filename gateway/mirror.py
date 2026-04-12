"""
用于跨平台消息传递的会话镜像。

当通过 send_message 或 cron 传递向平台发送消息时，
此模块会向目标会话的记录中追加 "delivery-mirror" 条目，
以便接收方代理了解发送的内容。

独立运行 — 可从 CLI、cron 和网关上下文使用，
无需完整的 SessionStore 机制。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from kclaw_cli.config import get_kclaw_home

logger = logging.getLogger(__name__)

_SESSIONS_DIR = get_kclaw_home() / "sessions"
_SESSIONS_INDEX = _SESSIONS_DIR / "sessions.json"


def mirror_to_session(
    platform: str,
    chat_id: str,
    message_text: str,
    source_label: str = "cli",
    thread_id: Optional[str] = None,
) -> bool:
    """
    向目标会话的记录追加传递镜像消息。

    找到与给定 platform + chat_id 匹配的网关会话，
    然后将镜像条目写入 JSONL 记录和 SQLite DB。

    如果镜像成功则返回 True，如果没有匹配的会话或出错则返回 False。
    所有错误都被捕获 — 这永远不会致命。
    """
    try:
        session_id = _find_session_id(platform, str(chat_id), thread_id=thread_id)
        if not session_id:
            logger.debug("Mirror: no session found for %s:%s:%s", platform, chat_id, thread_id)
            return False

        mirror_msg = {
            "role": "assistant",
            "content": message_text,
            "timestamp": datetime.now().isoformat(),
            "mirror": True,
            "mirror_source": source_label,
        }

        _append_to_jsonl(session_id, mirror_msg)
        _append_to_sqlite(session_id, mirror_msg)

        logger.debug("Mirror: wrote to session %s (from %s)", session_id, source_label)
        return True

    except Exception as e:
        logger.debug("Mirror failed for %s:%s:%s: %s", platform, chat_id, thread_id, e)
        return False


def _find_session_id(platform: str, chat_id: str, thread_id: Optional[str] = None) -> Optional[str]:
    """
    查找 platform + chat_id 对的活跃 session_id。

    扫描 sessions.json 条目并匹配 origin.chat_id == chat_id
    在正确的平台上。私信会话键不嵌入 chat_id
    （例如 "agent:main:telegram:dm"），因此我们检查 origin 字典。
    """
    if not _SESSIONS_INDEX.exists():
        return None

    try:
        with open(_SESSIONS_INDEX, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    platform_lower = platform.lower()
    best_match = None
    best_updated = ""

    for _key, entry in data.items():
        origin = entry.get("origin") or {}
        entry_platform = (origin.get("platform") or entry.get("platform", "")).lower()

        if entry_platform != platform_lower:
            continue

        origin_chat_id = str(origin.get("chat_id", ""))
        if origin_chat_id == str(chat_id):
            origin_thread_id = origin.get("thread_id")
            if thread_id is not None and str(origin_thread_id or "") != str(thread_id):
                continue
            updated = entry.get("updated_at", "")
            if updated > best_updated:
                best_updated = updated
                best_match = entry.get("session_id")

    return best_match


def _append_to_jsonl(session_id: str, message: dict) -> None:
    """将消息追加到 JSONL 记录文件。"""
    transcript_path = _SESSIONS_DIR / f"{session_id}.jsonl"
    try:
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Mirror JSONL write failed: %s", e)


def _append_to_sqlite(session_id: str, message: dict) -> None:
    """将消息追加到 SQLite 会话数据库。"""
    db = None
    try:
        from kclaw_state import SessionDB
        db = SessionDB()
        db.append_message(
            session_id=session_id,
            role=message.get("role", "assistant"),
            content=message.get("content"),
        )
    except Exception as e:
        logger.debug("Mirror SQLite write failed: %s", e)
    finally:
        if db is not None:
            db.close()
