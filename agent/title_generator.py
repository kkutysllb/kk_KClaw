"""从第一次用户/助手交互自动生成简短的会话标题。

在第一次响应交付后异步运行,因此不会给面向用户的
回复增加延迟。
"""

import logging
import threading
from typing import Optional

from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
    "following exchange. The title should capture the main topic or intent. "
    "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
)


def generate_title(user_message: str, assistant_response: str, timeout: float = 30.0) -> Optional[str]:
    """从第一次交互生成会话标题。

    使用辅助 LLM 客户端(最便宜/最快的可用模型)。
    返回标题字符串,失败则返回 None。
    """
    # 截断长消息以保持请求较小
    user_snippet = user_message[:500] if user_message else ""
    assistant_snippet = assistant_response[:500] if assistant_response else ""

    messages = [
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"},
    ]

    try:
        response = call_llm(
            task="compression",  # 复用压缩任务配置(便宜/快速模型)
            messages=messages,
            max_tokens=30,
            temperature=0.3,
            timeout=timeout,
        )
        title = (response.choices[0].message.content or "").strip()
        # 清理: 移除引号、末尾标点、前缀如 "Title: "
        title = title.strip('"\'')
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        # 强制合理长度
        if len(title) > 80:
            title = title[:77] + "..."
        return title if title else None
    except Exception as e:
        logger.debug("标题生成失败: %s", e)
        return None


def auto_title_session(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
) -> None:
    """如果不存在会话标题,则生成并设置一个。

    在第一次交互完成后在后台线程中调用。
    静默跳过如果:
    - session_db 为 None
    - 会话已有标题(用户设置或之前自动生成)
    - 标题生成失败
    """
    if not session_db or not session_id:
        return

    # 检查标题是否已存在(用户可能在第一次响应前通过 /title 设置了)
    try:
        existing = session_db.get_session_title(session_id)
        if existing:
            return
    except Exception:
        return

    title = generate_title(user_message, assistant_response)
    if not title:
        return

    try:
        session_db.set_session_title(session_id, title)
        logger.debug("自动生成会话标题: %s", title)
    except Exception as e:
        logger.debug("设置自动生成标题失败: %s", e)


def maybe_auto_title(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list,
) -> None:
    """即发即弃的标题生成,在第一次交互后触发。

    仅在以下条件下生成标题:
    - 这似乎是第一次用户→助手交互
    - 尚未设置标题
    """
    if not session_db or not session_id or not user_message or not assistant_response:
        return

    # 计算历史中的用户消息数以检测第一次交互。
    # conversation_history 包含刚发生的交互,
    # 因此对于第一次交互,我们期望恰好 1 条用户消息
    # (或 2 条包括系统消息)。宽松起见:前 2 次交互都生成。
    user_msg_count = sum(1 for m in (conversation_history or []) if m.get("role") == "user")
    if user_msg_count > 2:
        return

    thread = threading.Thread(
        target=auto_title_session,
        args=(session_db, session_id, user_message, assistant_response),
        daemon=True,
        name="auto-title",
    )
    thread.start()
