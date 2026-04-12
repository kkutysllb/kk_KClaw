"""
基础平台适配器接口。

所有平台适配器（Telegram、Discord、WhatsApp）都继承自此
并实现所需的方法。
"""

import asyncio
import logging
import os
import random
import re
import uuid
from abc import ABC, abstractmethod
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Awaitable, Tuple
from enum import Enum

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource, build_session_key
from kclaw_constants import get_kclaw_dir


GATEWAY_SECRET_CAPTURE_UNSUPPORTED_MESSAGE = (
    "安全密钥输入在消息平台上不支持。"
    "请在本地 CLI 中加载此技能以获取提示，或手动将密钥添加到 ~/.kclaw/.env。"
)


def _safe_url_for_log(url: str, max_len: int = 80) -> str:
    """返回对日志安全的 URL 字符串（无查询/片段/用户信息）。"""
    if max_len <= 0:
        return ""

    if url is None:
        return ""

    raw = str(url)
    if not raw:
        return ""

    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw[:max_len]

    if parsed.scheme and parsed.netloc:
        # Strip potential embedded credentials (user:pass@host).
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        base = f"{parsed.scheme}://{netloc}"
        path = parsed.path or ""
        if path and path != "/":
            basename = path.rsplit("/", 1)[-1]
            safe = f"{base}/.../{basename}" if basename else f"{base}/..."
        else:
            safe = base
    else:
        safe = raw

    if len(safe) <= max_len:
        return safe
    if max_len <= 3:
        return "." * max_len
    return f"{safe[:max_len - 3]}..."


# ---------------------------------------------------------------------------
# 图片缓存工具
#
# 当用户在消息平台上发送图片时，我们将其下载到本地
# 缓存目录，以便视觉工具可以分析（它接受
# 本地文件路径）。这避免了临时平台 URL
#（例如 Telegram 文件 URL 在约 1 小时后过期）的问题。
# ---------------------------------------------------------------------------

# 默认位置：{KCLAW_HOME}/cache/images/（传统：image_cache/）
IMAGE_CACHE_DIR = get_kclaw_dir("cache/images", "image_cache")


def get_image_cache_dir() -> Path:
    """返回图片缓存目录，在不存在时创建。"""
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return IMAGE_CACHE_DIR


def cache_image_from_bytes(data: bytes, ext: str = ".jpg") -> str:
    """
    将原始图片字节保存到缓存并返回绝对文件路径。

    参数：
        data: 原始图片字节。
        ext:  文件扩展名，包括点（例如 ".jpg"、".png"）。

    返回：
        缓存图片文件的绝对路径字符串。
    """
    cache_dir = get_image_cache_dir()
    filename = f"img_{uuid.uuid4().hex[:12]}{ext}"
    filepath = cache_dir / filename
    filepath.write_bytes(data)
    return str(filepath)


async def cache_image_from_url(url: str, ext: str = ".jpg", retries: int = 2) -> str:
    """
    从 URL 下载图片并保存到本地缓存。

    在瞬时失败（超时、429、5xx）时使用指数退避重试，
    以便单个慢速 CDN 响应不会丢失媒体。

    参数：
        url: 要下载的 HTTP/HTTPS URL。
        ext: 文件扩展名，包括点（例如 ".jpg"、".png"）。
        retries: 瞬时失败的重试次数。

    返回：
        缓存图片文件的绝对路径字符串。

    抛出：
        ValueError: 如果 URL 指向私有/内部网络（SSRF 保护）。
    """
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        raise ValueError(f"Blocked unsafe URL (SSRF protection): {_safe_url_for_log(url)}")

    import asyncio
    import httpx
    import logging as _logging
    _log = _logging.getLogger(__name__)

    last_exc = None
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; KClawAgent/1.0)",
                        "Accept": "image/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()
                return cache_image_from_bytes(response.content, ext)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise
                if attempt < retries:
                    wait = 1.5 * (attempt + 1)
                    _log.debug(
                        "Media cache retry %d/%d for %s (%.1fs): %s",
                        attempt + 1,
                        retries,
                        _safe_url_for_log(url),
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
    raise last_exc


def cleanup_image_cache(max_age_hours: int = 24) -> int:
    """
    删除超过 *max_age_hours* 的缓存图片。

    返回移除的文件数。
    """
    import time

    cache_dir = get_image_cache_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in cache_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# 音频缓存工具
#
# 与图片缓存相同的模式 — 来自平台的语音消息被下载到这里，
# 以便 STT 工具（OpenAI Whisper）可以从本地文件转录。
# ---------------------------------------------------------------------------

AUDIO_CACHE_DIR = get_kclaw_dir("cache/audio", "audio_cache")

def get_audio_cache_dir() -> Path:
    """返回音频缓存目录，在不存在时创建。"""
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_CACHE_DIR

def cache_audio_from_bytes(data: bytes, ext: str = ".ogg") -> str:
    """
    将原始音频字节保存到缓存并返回绝对文件路径。

    参数：
        data: 原始音频字节。
        ext:  文件扩展名，包括点（例如 ".ogg"、".mp3"）。

    返回：
        缓存音频文件的绝对路径字符串。
    """
    cache_dir = get_audio_cache_dir()
    filename = f"audio_{uuid.uuid4().hex[:12]}{ext}"
    filepath = cache_dir / filename
    filepath.write_bytes(data)
    return str(filepath)

async def cache_audio_from_url(url: str, ext: str = ".ogg", retries: int = 2) -> str:
    """
    从 URL 下载音频文件并保存到本地缓存。

    在瞬时失败（超时、429、5xx）时使用指数退避重试，
    以便单个慢速 CDN 响应不会丢失媒体。

    参数：
        url: 要下载的 HTTP/HTTPS URL。
        ext: 文件扩展名，包括点（例如 ".ogg"、".mp3"）。
        retries: 重试次数。

    返回：
        缓存音频文件的绝对路径字符串。

    抛出：
        ValueError: 如果 URL 指向私有/内部网络（SSRF 保护）。
    """
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        raise ValueError(f"Blocked unsafe URL (SSRF protection): {_safe_url_for_log(url)}")

    import asyncio
    import httpx
    import logging as _logging
    _log = _logging.getLogger(__name__)

    last_exc = None
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; KClawAgent/1.0)",
                        "Accept": "audio/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()
                return cache_audio_from_bytes(response.content, ext)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise
                if attempt < retries:
                    wait = 1.5 * (attempt + 1)
                    _log.debug(
                        "Audio cache retry %d/%d for %s (%.1fs): %s",
                        attempt + 1,
                        retries,
                        _safe_url_for_log(url),
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
    raise last_exc


# ---------------------------------------------------------------------------
# 文档缓存工具
#
# 与图片/音频缓存相同的模式 — 来自平台的文档被下载到这里，
# 以便代理可以通过本地文件路径引用它们。
# ---------------------------------------------------------------------------

DOCUMENT_CACHE_DIR = get_kclaw_dir("cache/documents", "document_cache")

SUPPORTED_DOCUMENT_TYPES = {
    # 文件扩展名到 MIME 类型的映射
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def get_document_cache_dir() -> Path:
    """返回文档缓存目录，在不存在时创建。"""
    DOCUMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return DOCUMENT_CACHE_DIR


def cache_document_from_bytes(data: bytes, filename: str) -> str:
    """
    将原始文档字节保存到缓存并返回绝对文件路径。

    缓存的文件名保留原始可读名称，并添加唯一前缀：``doc_{uuid12}_{original_filename}``。

    参数：
        data: 原始文档字节。
        filename: 原始文件名（例如 "report.pdf"）。

    返回：
        缓存文档文件的绝对路径字符串。

    抛出：
        ValueError: 如果清理后的路径逃逸出缓存目录（路径遍历攻击）。
    """
    cache_dir = get_document_cache_dir()
    # 清理：剥离目录组件、空字节和控制字符
    safe_name = Path(filename).name if filename else "document"
    safe_name = safe_name.replace("\x00", "").strip()
    if not safe_name or safe_name in (".", ".."):
        safe_name = "document"
    cached_name = f"doc_{uuid.uuid4().hex[:12]}_{safe_name}"
    filepath = cache_dir / cached_name
    # 最终安全检查：确保路径保持在缓存目录内
    if not filepath.resolve().is_relative_to(cache_dir.resolve()):
        raise ValueError(f"路径遍历攻击被拒绝: {filename!r}")
    filepath.write_bytes(data)
    return str(filepath)


def cleanup_document_cache(max_age_hours: int = 24) -> int:
    """
    删除超过 *max_age_hours* 的缓存文档。

    返回移除的文件数。
    """
    import time

    cache_dir = get_document_cache_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in cache_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


class MessageType(Enum):
    """消息类型枚举。"""
    TEXT = "text"
    LOCATION = "location"
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    STICKER = "sticker"
    COMMAND = "command"  # /command 样式


@dataclass
class MessageEvent:
    """
    来自平台的消息事件。
    
    所有适配器产生的规范化表示。
    """
    # 消息内容
    text: str
    message_type: MessageType = MessageType.TEXT
    
    # 来源信息
    source: SessionSource = None
    
    # 原始平台数据
    raw_message: Any = None
    message_id: Optional[str] = None
    
    # 媒体附件
    # media_urls: 本地文件路径（供视觉工具访问）
    media_urls: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)
    
    # 回复上下文
    reply_to_message_id: Optional[str] = None
    reply_to_text: Optional[str] = None  # 被回复消息的文本（用于上下文注入）
    
    # 自动加载的技能（主题/频道绑定，例如 Telegram DM Topics）
    auto_skill: Optional[str] = None
    
    # 内部标志 — 为合成事件设置（例如后台进程完成通知），
    # 必须绕过用户授权检查。
    internal: bool = False

    # 时间戳
    timestamp: datetime = field(default_factory=datetime.now)
    
    def is_command(self) -> bool:
        """检查这是否是一条命令消息（例如 /new, /reset）。"""
        return self.text.startswith("/")
    
    def get_command(self) -> Optional[str]:
        """如果是命令消息，提取命令名称。"""
        if not self.is_command():
            return None
        # 按空格分割并获取第一个词，去掉 /
        parts = self.text.split(maxsplit=1)
        raw = parts[0][1:].lower() if parts else None
        if raw and "@" in raw:
            raw = raw.split("@", 1)[0]
        return raw
    
    def get_command_args(self) -> str:
        """获取命令后的参数。"""
        if not self.is_command():
            return self.text
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""


@dataclass 
class SendResult:
    """发送消息的结果。"""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: Any = None
    retryable: bool = False  # 瞬时连接错误为 True — 基础类将自动重试


# 表示瞬时*连接*失败的可重试错误子字符串。
# "timeout" / "timed out" / "readtimeout" / "writetimeout" 被故意排除：
# 在非幂等调用（如 send_message）上读取/写入超时意味着请求可能已到达服务器
# — 重试可能导致重复投递。"connecttimeout" 是安全的，因为连接从未建立。
# 知道超时可以安全重试的平台应该显式设置 SendResult.retryable = True。
_RETRYABLE_ERROR_PATTERNS = (
    "connecterror",
    "connectionerror",
    "connectionreset",
    "connectionrefused",
    "connecttimeout",
    "network",
    "broken pipe",
    "remotedisconnected",
    "eoferror",
)


# 消息处理器类型
MessageHandler = Callable[[MessageEvent], Awaitable[Optional[str]]]


class BasePlatformAdapter(ABC):
    """
    平台适配器基类。
    
    子类实现特定平台的逻辑：
    - 连接和认证
    - 接收消息
    - 发送消息/响应
    - 处理媒体
    """
    
    def __init__(self, config: PlatformConfig, platform: Platform):
        self.config = config
        self.platform = platform
        self._message_handler: Optional[MessageHandler] = None
        self._running = False
        self._fatal_error_code: Optional[str] = None
        self._fatal_error_message: Optional[str] = None
        self._fatal_error_retryable = True
        self._fatal_error_handler: Optional[Callable[["BasePlatformAdapter"], Awaitable[None] | None]] = None
        
        # 跟踪每个会话的活动消息处理器以支持中断
        # 键：session_key（例如 chat_id），值：(event, asyncio.Event 用于中断)
        self._active_sessions: Dict[str, asyncio.Event] = {}
        self._pending_messages: Dict[str, MessageEvent] = {}
        # handle_message() 生成的后台消息处理任务。
        # Gateway 关闭时会取消这些任务，以便旧的 gateway 实例
        # 在 --replace 或手动重启后不会继续处理任务。
        self._background_tasks: set[asyncio.Task] = set()
        # 已禁用自动 TTS 的聊天（由 /voice off 设置）
        self._auto_tts_disabled_chats: set = set()
        # 暂停打字指示器的聊天（例如在等待审批期间）。
        # _keep_typing 在 chat_id 在此集合中时跳过 send_typing。
        self._typing_paused: set = set()

    @property
    def has_fatal_error(self) -> bool:
        return self._fatal_error_message is not None

    @property
    def fatal_error_message(self) -> Optional[str]:
        return self._fatal_error_message

    @property
    def fatal_error_code(self) -> Optional[str]:
        return self._fatal_error_code

    @property
    def fatal_error_retryable(self) -> bool:
        return self._fatal_error_retryable

    def set_fatal_error_handler(self, handler: Callable[["BasePlatformAdapter"], Awaitable[None] | None]) -> None:
        self._fatal_error_handler = handler

    def _mark_connected(self) -> None:
        self._running = True
        self._fatal_error_code = None
        self._fatal_error_message = None
        self._fatal_error_retryable = True
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(platform=self.platform.value, platform_state="connected", error_code=None, error_message=None)
        except Exception:
            pass

    def _mark_disconnected(self) -> None:
        self._running = False
        if self.has_fatal_error:
            return
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(platform=self.platform.value, platform_state="disconnected", error_code=None, error_message=None)
        except Exception:
            pass

    def _set_fatal_error(self, code: str, message: str, *, retryable: bool) -> None:
        self._running = False
        self._fatal_error_code = code
        self._fatal_error_message = message
        self._fatal_error_retryable = retryable
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                platform=self.platform.value,
                platform_state="fatal",
                error_code=code,
                error_message=message,
            )
        except Exception:
            pass

    async def _notify_fatal_error(self) -> None:
        handler = self._fatal_error_handler
        if not handler:
            return
        result = handler(self)
        if asyncio.iscoroutine(result):
            await result
    
    @property
    def name(self) -> str:
        """此适配器的人类可读名称。"""
        return self.platform.value.title()
    
    @property
    def is_connected(self) -> bool:
        """检查适配器当前是否已连接。"""
        return self._running
    
    def set_message_handler(self, handler: MessageHandler) -> None:
        """
        设置传入消息的处理程序。
        
        处理程序接收 MessageEvent 并应返回可选的响应字符串。
        """
        self._message_handler = handler
    
    def set_session_store(self, session_store: Any) -> None:
        """
        设置会话存储以检查活动会话。
        
        被需要在处理消息前检查线程/对话是否有活动会话的适配器使用
        （例如，没有明确提及的 Slack 线程回复）。
        """
        self._session_store = session_store
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        连接到平台并开始接收消息。
        
        如果连接成功则返回 True。
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开与平台的连接。"""
        pass
    
    @abstractmethod
    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """
        发送消息到聊天。
        
        参数：
            chat_id: 要发送到的聊天/频道 ID
            content: 消息内容（可能是 markdown）
            reply_to: 可选的回复消息 ID
            metadata: 额外的平台特定选项
        
        返回：
            包含成功状态和消息 ID 的 SendResult
        """
        pass

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """
        编辑之前发送的消息。可选 — 不支持编辑的平台返回 success=False，
        调用方会回退到发送新消息。
        """
        return SendResult(success=False, error="不支持")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """
        发送打字指示器。
        
        如果平台支持，子类应覆盖。
        metadata: 可选的平台特定上下文字典（例如 Slack 的 thread_id）。
        """
        pass

    async def stop_typing(self, chat_id: str) -> None:
        """停止持续打字指示器（如果平台使用）。

        覆盖启动后台打字循环的子类。
        默认对于一次性打字指示器的平台是空操作。
        """
        pass
    
    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        通过平台 API 原生发送图片。
        
        子类应覆盖以将图片作为适当的附件发送，
        而不是纯文本 URL。默认回退到将 URL 作为文本消息发送。
        """
        # 回退：将 URL 作为文本发送（子类覆盖以支持原生图片）
        text = f"{caption}\n{image_url}" if caption else image_url
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)
    
    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        通过平台 API 原生发送动画 GIF。
        
        子类应覆盖以将 GIF 作为适当的动画发送
        （例如 Telegram send_animation），以便它们可以内联自动播放。
        默认回退到 send_image。
        """
        return await self.send_image(chat_id=chat_id, image_url=animation_url, caption=caption, reply_to=reply_to, metadata=metadata)
    
    @staticmethod
    def _is_animation_url(url: str) -> bool:
        """检查 URL 是否指向动画 GIF（而非静态图片）。"""
        lower = url.lower().split('?')[0]  # 剥离查询参数
        return lower.endswith('.gif')

    @staticmethod
    def extract_images(content: str) -> Tuple[List[Tuple[str, str]], str]:
        """
        从响应的 markdown 和 HTML 图片标签中提取图片 URL。
        
        匹配的模式：
        - ![alt text](https://example.com/image.png)
        - <img src="https://example.com/image.png">
        - <img src="https://example.com/image.png"></img>
        
        参数：
            content: 要扫描的响应文本。
        
        返回：
            (图片 URL 和 alt 文本对列表, 移除了图片标签的清理后内容) 元组。
        """
        images = []
        cleaned = content
        
        # 匹配 markdown 图片：![alt](url)
        md_pattern = r'!\[([^\]]*)\]\((https?://[^\s\)]+)\)'
        for match in re.finditer(md_pattern, content):
            alt_text = match.group(1)
            url = match.group(2)
            # 只提取看起来像实际图片的 URL
            if any(url.lower().endswith(ext) or ext in url.lower() for ext in
                   ['.png', '.jpg', '.jpeg', '.gif', '.webp', 'fal.media', 'fal-cdn', 'replicate.delivery']):
                images.append((url, alt_text))
        
        # 匹配 HTML img 标签：<img src="url"> 或 <img src="url"></img> 或 <img src="url"/>
        html_pattern = r'<img\s+src=["\']?(https?://[^\s"\'<>]+)["\']?\s*/?>\s*(?:</img>)?'
        for match in re.finditer(html_pattern, content):
            url = match.group(1)
            images.append((url, ""))
        
        # 只从内容中移除匹配的图片标签（不是所有 markdown 图片）
        if images:
            extracted_urls = {url for url, _ in images}
            def _remove_if_extracted(match):
                url = match.group(2) if match.lastindex >= 2 else match.group(1)
                return '' if url in extracted_urls else match.group(0)
            cleaned = re.sub(md_pattern, _remove_if_extracted, cleaned)
            cleaned = re.sub(html_pattern, _remove_if_extracted, cleaned)
            # 清理遗留的空行
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        
        return images, cleaned
    
    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 将音频文件作为原生语音消息发送。
        
        子类应覆盖以将音频作为语音气泡（Telegram）
        或文件附件（Discord）发送。默认回退到将文件路径作为文本发送。
        """
        text = f"🔊 音频: {audio_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def play_tts(
        self,
        chat_id: str,
        audio_path: str,
        **kwargs,
    ) -> SendResult:
        """
        为语音回复播放自动 TTS 音频。

        子类应覆盖以进行不可见的播放（例如 Web UI）。
        默认回退到 send_voice（显示音频播放器）。
        """
        return await self.send_voice(chat_id=chat_id, audio_path=audio_path, **kwargs)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送视频。

        子类应覆盖以将视频作为内联可播放媒体发送。
        默认回退到将文件路径作为文本发送。
        """
        text = f"🎬 视频: {video_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送文档/文件。

        子类应覆盖以将文件作为可下载附件发送。
        默认回退到将文件路径作为文本发送。
        """
        text = f"📎 文件: {file_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送本地图片文件。

        与接受 URL 的 send_image() 不同，这接受本地文件路径。
        子类应覆盖以支持原生照片附件。
        默认回退到将文件路径作为文本发送。
        """
        text = f"🖼️ 图片: {image_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    @staticmethod
    def extract_media(content: str) -> Tuple[List[Tuple[str, bool]], str]:
        """
        从响应文本中提取 MEDIA:<path> 标签和 [[audio_as_voice]] 指令。
        
        TTS 工具返回类似这样的响应：
            [[audio_as_voice]]
            MEDIA:/path/to/audio.ogg
        
        参数：
            content: 要扫描的响应文本。
        
        返回：
            (路径和是否为语音的对列表, 移除了标签的清理后内容) 元组。
        """
        media = []
        cleaned = content
        
        # 检查 [[audio_as_voice]] 指令
        has_voice_tag = "[[audio_as_voice]]" in content
        cleaned = cleaned.replace("[[audio_as_voice]]", "")
        
        # 提取 MEDIA:<path> 标签，允许冒号后有可选空格
        # 以及带引号/反引号的路径（用于 LLM 格式化输出）。
        media_pattern = re.compile(
            r'''[`"']?MEDIA:\s*(?P<path>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|(?:~/|/)\S+(?:[^\S\n]+\S+)*?\.(?:png|jpe?g|gif|webp|mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a)(?=[\s`"',;:)\]}]|$)|\S+)[`"']?'''
        )
        for match in media_pattern.finditer(content):
            path = match.group("path").strip()
            if len(path) >= 2 and path[0] == path[-1] and path[0] in "`\"'":
                path = path[1:-1].strip()
            path = path.lstrip("`\"'").rstrip("`\"',.;:)}]")
            if path:
                media.append((path, has_voice_tag))

        # 从内容中移除 MEDIA 标签（包括周围的引号/反引号包装）
        if media:
            cleaned = media_pattern.sub('', cleaned)
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        
        return media, cleaned

    @staticmethod
    def extract_local_files(content: str) -> Tuple[List[str], str]:
        """
        检测响应文本中的裸本地文件路径，以便原生媒体投递。
    
        匹配以常见图片或视频扩展名结尾的绝对路径（/...）和波浪号路径（~/）。
        使用 ``os.path.isfile()`` 验证每个候选路径，以避免 URL 或
        不存在路径的误报。
    
        代码块（``` ... ```）和内联代码（`...`）内的路径会被忽略，
        以便代码示例不会被破坏。
    
        返回：
            (展开文件路径列表, 移除了原始路径字符串的清理后文本) 元组。
        """
        _LOCAL_MEDIA_EXTS = (
            '.png', '.jpg', '.jpeg', '.gif', '.webp',
            '.mp4', '.mov', '.avi', '.mkv', '.webm',
        )
        ext_part = '|'.join(e.lstrip('.') for e in _LOCAL_MEDIA_EXTS)
    
        # (?<![/:\w.]) 防止匹配 URL 内（如 https://…/img.png）
        # 和相对路径（./foo.png）
        # (?:~/|/) 将路径锚定到绝对或主目录相对路径
        path_re = re.compile(
            r'(?<![/:\w.])(?:~/|/)(?:[\w.\-]+/)*[\w.\-]+\.(?:' + ext_part + r')\b',
            re.IGNORECASE,
        )
    
        # 构建代码块和内联代码覆盖的跨度
        code_spans: list = []
        for m in re.finditer(r'```[^\n]*\n.*?```', content, re.DOTALL):
            code_spans.append((m.start(), m.end()))
        for m in re.finditer(r'`[^`\n]+`', content):
            code_spans.append((m.start(), m.end()))
    
        def _in_code(pos: int) -> bool:
            return any(s <= pos < e for s, e in code_spans)
    
        found: list = []  # (原始匹配文本, 展开路径)
        for match in path_re.finditer(content):
            if _in_code(match.start()):
                continue
            raw = match.group(0)
            expanded = os.path.expanduser(raw)
            if os.path.isfile(expanded):
                found.append((raw, expanded))
    
        # 通过展开路径去重，保持发现顺序
        seen: set = set()
        unique: list = []
        for raw, expanded in found:
            if expanded not in seen:
                seen.add(expanded)
                unique.append((raw, expanded))
    
        paths = [expanded for _, expanded in unique]
    
        cleaned = content
        if unique:
            for raw, _exp in unique:
                cleaned = cleaned.replace(raw, '')
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    
        return paths, cleaned

    async def _keep_typing(self, chat_id: str, interval: float = 2.0, metadata=None) -> None:
        """
        持续发送打字指示器直到取消。
        
        Telegram/Discord 打字状态约 5 秒后过期，所以我们每 2 秒刷新一次，
        以便在进度消息中断后快速恢复。
        
        当聊天在 ``_typing_paused`` 中时跳过 send_typing（例如
        代理等待危险命令审批时）。这对于 Slack 的 Assistant API 至关重要，
        其中 ``assistant_threads_setStatus`` 禁用了输入框
        — 暂停让用户可以输入 ``/approve`` 或 ``/deny``。
        """
        try:
            while True:
                if chat_id not in self._typing_paused:
                    await self.send_typing(chat_id, metadata=metadata)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass  # 正常取消，处理完成后
        finally:
            # 确保底层平台打字循环已停止。
            # _keep_typing 可能在最后一次 stop_typing() 后调用 send_typing()
            # 重新创建了循环。
            # 只取消 _keep_typing 不会清理它。
            if hasattr(self, "stop_typing"):
                try:
                    await self.stop_typing(chat_id)
                except Exception:
                    pass
            self._typing_paused.discard(chat_id)

    def pause_typing_for_chat(self, chat_id: str) -> None:
        """暂停聊天的打字指示器（例如在审批等待期间）。

        线程安全（CPython GIL）— 可以在 ``_keep_typing``
        运行在异步事件循环上的同时从同步代理线程调用。
        """
        self._typing_paused.add(chat_id)

    def resume_typing_for_chat(self, chat_id: str) -> None:
        """审批解决后恢复聊天的打字指示器。"""
        self._typing_paused.discard(chat_id)

    # ── 处理生命周期钩子 ────────────────────────────────────────────────
    # 子类覆盖这些以响应消息处理事件
    #（例如 Discord 添加 👀/✅/❌ 反应）。

    async def on_processing_start(self, event: MessageEvent) -> None:
        """后台处理开始时调用的钩子。"""

    async def on_processing_complete(self, event: MessageEvent, success: bool) -> None:
        """后台处理完成时调用的钩子。"""

    async def _run_processing_hook(self, hook_name: str, *args: Any, **kwargs: Any) -> None:
        """运行生命周期钩子，不让失败破坏消息流。"""
        hook = getattr(self, hook_name, None)
        if not callable(hook):
            return
        try:
            await hook(*args, **kwargs)
        except Exception as e:
            logger.warning("[%s] %s 钩子失败: %s", self.name, hook_name, e)

    @staticmethod
    def _is_retryable_error(error: Optional[str]) -> bool:
        """如果错误字符串看起来像瞬时网络故障则返回 True。"""
        if not error:
            return False
        lowered = error.lower()
        return any(pat in lowered for pat in _RETRYABLE_ERROR_PATTERNS)

    @staticmethod
    def _is_timeout_error(error: Optional[str]) -> bool:
        """如果错误字符串表示读取/写入超时则返回 True。

        超时错误不可重试且不应触发纯文本回退 —
        请求可能已经投递。
        """
        if not error:
            return False
        lowered = error.lower()
        return "timed out" in lowered or "readtimeout" in lowered or "writetimeout" in lowered

    async def _send_with_retry(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Any = None,
        max_retries: int = 2,
        base_delay: float = 2.0,
    ) -> "SendResult":
        """
        发送消息，对瞬时网络错误进行自动重试。

        对于永久性故障（例如格式化/权限错误），在放弃之前
        回退到纯文本版本。如果所有尝试都因网络错误失败，
        向用户发送简短的投递失败通知，让他们知道重试
        （而不是无限等待）。
        """

        result = await self.send(
            chat_id=chat_id,
            content=content,
            reply_to=reply_to,
            metadata=metadata,
        )

        if result.success:
            return result

        error_str = result.error or ""
        is_network = result.retryable or self._is_retryable_error(error_str)

        # Timeout errors are not safe to retry (message may have been
        # delivered) and not formatting errors — return the failure as-is.
        if not is_network and self._is_timeout_error(error_str):
            return result

        if is_network:
            # Retry with exponential backoff for transient errors
            for attempt in range(1, max_retries + 1):
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "[%s] Send failed (attempt %d/%d, retrying in %.1fs): %s",
                    self.name, attempt, max_retries, delay, error_str,
                )
                await asyncio.sleep(delay)
                result = await self.send(
                    chat_id=chat_id,
                    content=content,
                    reply_to=reply_to,
                    metadata=metadata,
                )
                if result.success:
                    logger.info("[%s] Send succeeded on retry %d", self.name, attempt)
                    return result
                error_str = result.error or ""
                if not (result.retryable or self._is_retryable_error(error_str)):
                    break  # error switched to non-transient — fall through to plain-text fallback
            else:
                # All retries exhausted (loop completed without break) — notify user
                logger.error("[%s] Failed to deliver response after %d retries: %s", self.name, max_retries, error_str)
                notice = (
                    "\u26a0\ufe0f Message delivery failed after multiple attempts. "
                    "Please try again \u2014 your request was processed but the response could not be sent."
                )
                try:
                    await self.send(chat_id=chat_id, content=notice, reply_to=reply_to, metadata=metadata)
                except Exception as notify_err:
                    logger.debug("[%s] Could not send delivery-failure notice: %s", self.name, notify_err)
                return result

        # Non-network / post-retry formatting failure: try plain text as fallback
        logger.warning("[%s] Send failed: %s — trying plain-text fallback", self.name, error_str)
        fallback_result = await self.send(
            chat_id=chat_id,
            content=f"(Response formatting failed, plain text:)\n\n{content[:3500]}",
            reply_to=reply_to,
            metadata=metadata,
        )
        if not fallback_result.success:
            logger.error("[%s] Fallback send also failed: %s", self.name, fallback_result.error)
        return fallback_result

    @staticmethod
    def _merge_caption(existing_text: Optional[str], new_text: str) -> str:
        """将新标题合并到现有文本中，避免重复。

        使用逐行精确匹配（不是子字符串）以防止误报
        （例如，较短的标题因为是较长标题的子字符串而被静默丢弃，
        如 "Meeting" 在 "Meeting agenda" 中）。
        比较时规范化空白。
        """
        if not existing_text:
            return new_text
        existing_captions = [c.strip() for c in existing_text.split("\n\n")]
        if new_text.strip() not in existing_captions:
            return f"{existing_text}\n\n{new_text}".strip()
        return existing_text

    async def handle_message(self, event: MessageEvent) -> None:
        """
        处理传入消息。
        
        此方法通过生成后台任务快速返回。
        这允许在代理运行时处理新消息，
        从而支持中断。
        """
        if not self._message_handler:
            return
        
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        
        # 检查此会话是否已有活动处理器
        if session_key in self._active_sessions:
            # 某些命令必须绕过活动会话保护，直接分发给 gateway 运行器。
            # 没有这个，它们要么：
            #   - 泄漏到对话中作为用户文本（/stop, /new），要么
            #   - 死锁（/approve, /deny — 代理在 Event.wait 上阻塞）
            #
            # 内联分发：直接调用消息处理器并发送响应。
            # 不要使用 _process_message_background — 它管理会话生命周期，
            # 其清理与运行中的任务竞争（见 PR #4926）。
            cmd = event.get_command()
            if cmd in ("approve", "deny", "status", "stop", "new", "reset"):
                logger.debug(
                    "[%s] 命令 '/%s' 绕过活动会话保护 for %s",
                    self.name, cmd, session_key,
                )
                try:
                    _thread_meta = {"thread_id": event.source.thread_id} if event.source.thread_id else None
                    response = await self._message_handler(event)
                    if response:
                        await self._send_with_retry(
                            chat_id=event.source.chat_id,
                            content=response,
                            reply_to=event.message_id,
                            metadata=_thread_meta,
                        )
                except Exception as e:
                    logger.error("[%s] 命令 '/%s' 分发失败: %s", self.name, cmd, e, exc_info=True)
                return

            # 特殊情况：照片爆发/相册经常作为多条几乎同时的消息到达。
            # 将它们排队而不中断活动运行，然后在当前任务完成后立即处理。
            if event.message_type == MessageType.PHOTO:
                logger.debug("[%s] 为会话 %s 排队照片后续而不中断", self.name, session_key)
                existing = self._pending_messages.get(session_key)
                if existing and existing.message_type == MessageType.PHOTO:
                    existing.media_urls.extend(event.media_urls)
                    existing.media_types.extend(event.media_types)
                    if event.text:
                        existing.text = self._merge_caption(existing.text, event.text)
                else:
                    self._pending_messages[session_key] = event
                return  # 现在不中断 - 当前任务完成后会运行

            # 非照片后续的默认行为：中断运行中的代理
            logger.debug("[%s] 会话 %s 活动时收到新消息 — 触发中断", self.name, session_key)
            self._pending_messages[session_key] = event
            # 发出中断信号（处理任务检查这个）
            self._active_sessions[session_key].set()
            return  # 现在不处理 - 当前任务完成后会处理
        
        # 在生成后台任务之前将会话标记为活动，以关闭
        # 第二个消息在任务开始前到达也会通过 _active_sessions 检查
        # 并生成重复任务的竞争窗口。
        # （grammY sequentialize / aiogram EventIsolation
        # 模式 — 同步设置保护，而不是在任务内部。）
        self._active_sessions[session_key] = asyncio.Event()

        # 生成后台任务来处理此消息
        task = asyncio.create_task(self._process_message_background(event, session_key))
        try:
            self._background_tasks.add(task)
        except TypeError:
            # 一些测试用轻量级哨兵替换 create_task()，它们不可哈希
            # 且不支持生命周期回调。
            return
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._background_tasks.discard)
    
    @staticmethod
    def _get_human_delay() -> float:
        """
        返回用于类人响应节奏的随机延迟（秒）。

        从环境变量读取：
          KCLAW_HUMAN_DELAY_MODE: "off"（默认）| "natural" | "custom"
          KCLAW_HUMAN_DELAY_MIN_MS: 最小延迟（毫秒）（默认 800，custom 模式）
          KCLAW_HUMAN_DELAY_MAX_MS: 最大延迟（毫秒）（默认 2500，custom 模式）
        """
        import random

        mode = os.getenv("KCLAW_HUMAN_DELAY_MODE", "off").lower()
        if mode == "off":
            return 0.0
        min_ms = int(os.getenv("KCLAW_HUMAN_DELAY_MIN_MS", "800"))
        max_ms = int(os.getenv("KCLAW_HUMAN_DELAY_MAX_MS", "2500"))
        if mode == "natural":
            min_ms, max_ms = 800, 2500
        return random.uniform(min_ms / 1000.0, max_ms / 1000.0)

    async def _process_message_background(self, event: MessageEvent, session_key: str) -> None:
        """实际处理消息的后台任务。"""
        # 跟踪处理完成钩子的投递结果
        delivery_attempted = False
        delivery_succeeded = False

        def _record_delivery(result):
            nonlocal delivery_attempted, delivery_succeeded
            if result is None:
                return
            delivery_attempted = True
            if getattr(result, "success", False):
                delivery_succeeded = True

        # 重用 handle_message() 设置的中断事件（它在生成此任务之前
        # 将会话标记为活动以防止竞争）。
        # 仅在条目被外部移除时才回退到新的 Event。
        interrupt_event = self._active_sessions.get(session_key) or asyncio.Event()
        self._active_sessions[session_key] = interrupt_event
        
        # 启动持续打字指示器（每 2 秒刷新）
        _thread_metadata = {"thread_id": event.source.thread_id} if event.source.thread_id else None
        typing_task = asyncio.create_task(self._keep_typing(event.source.chat_id, metadata=_thread_metadata))
        
        try:
            await self._run_processing_hook("on_processing_start", event)

            # Call the handler (this can take a while with tool calls)
            response = await self._message_handler(event)
            
            # 发送响应（如果有的话）。当流式传输已投递文本时（already_sent=True）
            # 或消息排在活动代理后面时，None/空响应是正常的。
            # 在 DEBUG 级别记录以避免对预期行为的噪音警告。
            if not response:
                logger.debug("[%s] 处理器返回空/无响应 for %s", self.name, event.source.chat_id)
            if response:
                # 在其他处理之前提取 MEDIA:<path> 标签（来自 TTS 工具）
                media_files, response = self.extract_media(response)
                
                # 提取图片 URL 并将它们作为原生平台附件发送
                images, text_content = self.extract_images(response)
                # 从消息体中剥离任何剩余的内部指令（修复 #1561）
                text_content = text_content.replace("[[audio_as_voice]]", "").strip()
                text_content = re.sub(r"MEDIA:\s*\S+", "", text_content).strip()
                if images:
                    logger.info("[%s] extract_images 在响应中找到 %d 张图片 (%d 字符)", self.name, len(images), len(response))

                # 自动检测裸本地文件路径用于原生媒体投递
                #（帮助不使用 MEDIA: 语法的小模型）
                local_files, text_content = self.extract_local_files(text_content)
                if local_files:
                    logger.info("[%s] extract_local_files 在响应中找到 %d 个文件", self.name, len(local_files))
                
                # 自动 TTS：如果语音消息，先生成音频（在发送文本之前）
                # 当聊天已禁用语音模式时跳过（/voice off）
                _tts_path = None
                if (event.message_type == MessageType.VOICE
                        and text_content
                        and not media_files
                        and event.source.chat_id not in self._auto_tts_disabled_chats):
                    try:
                        from tools.tts_tool import text_to_speech_tool, check_tts_requirements
                        if check_tts_requirements():
                            import json as _json
                            speech_text = re.sub(r'[*_`#\[\]()]', '', text_content)[:4000].strip()
                            if not speech_text:
                                raise ValueError("清理 markdown 后文本为空")
                            tts_result_str = await asyncio.to_thread(
                                text_to_speech_tool, text=speech_text
                            )
                            tts_data = _json.loads(tts_result_str)
                            _tts_path = tts_data.get("file_path")
                    except Exception as tts_err:
                        logger.warning("[%s] 自动 TTS 失败: %s", self.name, tts_err)

                # 在文本之前播放 TTS 音频（语音优先体验）
                if _tts_path and Path(_tts_path).exists():
                    try:
                        await self.play_tts(
                            chat_id=event.source.chat_id,
                            audio_path=_tts_path,
                            metadata=_thread_metadata,
                        )
                    finally:
                        try:
                            os.remove(_tts_path)
                        except OSError:
                            pass

                # 发送文本部分
                if text_content:
                    logger.info("[%s] 发送响应 (%d 字符) 到 %s", self.name, len(text_content), event.source.chat_id)
                    result = await self._send_with_retry(
                        chat_id=event.source.chat_id,
                        content=text_content,
                        reply_to=event.message_id,
                        metadata=_thread_metadata,
                    )
                    _record_delivery(result)

                # 文本和媒体之间的类人节奏延迟
                human_delay = self._get_human_delay()

                # 将提取的图片作为原生附件发送
                if images:
                    logger.info("[%s] 提取 %d 张图片作为附件发送", self.name, len(images))
                for image_url, alt_text in images:
                    if human_delay > 0:
                        await asyncio.sleep(human_delay)
                    try:
                        logger.info(
                            "[%s] 发送图片: %s (alt=%s)",
                            self.name,
                            _safe_url_for_log(image_url),
                            alt_text[:30] if alt_text else "",
                        )
                        # 通过 send_animation 路由动画 GIF 以正确播放
                        if self._is_animation_url(image_url):
                            img_result = await self.send_animation(
                                chat_id=event.source.chat_id,
                                animation_url=image_url,
                                caption=alt_text if alt_text else None,
                                metadata=_thread_metadata,
                            )
                        else:
                            img_result = await self.send_image(
                                chat_id=event.source.chat_id,
                                image_url=image_url,
                                caption=alt_text if alt_text else None,
                                metadata=_thread_metadata,
                            )
                        if not img_result.success:
                            logger.error("[%s] 发送图片失败: %s", self.name, img_result.error)
                    except Exception as img_err:
                        logger.error("[%s] 发送图片错误: %s", self.name, img_err, exc_info=True)

                # 发送提取的媒体文件 — 按文件类型路由
                _AUDIO_EXTS = {'.ogg', '.opus', '.mp3', '.wav', '.m4a'}
                _VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}
                _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

                for media_path, is_voice in media_files:
                    if human_delay > 0:
                        await asyncio.sleep(human_delay)
                    try:
                        ext = Path(media_path).suffix.lower()
                        if ext in _AUDIO_EXTS:
                            media_result = await self.send_voice(
                                chat_id=event.source.chat_id,
                                audio_path=media_path,
                                metadata=_thread_metadata,
                            )
                        elif ext in _VIDEO_EXTS:
                            media_result = await self.send_video(
                                chat_id=event.source.chat_id,
                                video_path=media_path,
                                metadata=_thread_metadata,
                            )
                        elif ext in _IMAGE_EXTS:
                            media_result = await self.send_image_file(
                                chat_id=event.source.chat_id,
                                image_path=media_path,
                                metadata=_thread_metadata,
                            )
                        else:
                            media_result = await self.send_document(
                                chat_id=event.source.chat_id,
                                file_path=media_path,
                                metadata=_thread_metadata,
                            )

                        if not media_result.success:
                            logger.warning("[%s] 发送媒体失败 (%s): %s", self.name, ext, media_result.error)
                    except Exception as media_err:
                        logger.warning("[%s] 发送媒体错误: %s", self.name, media_err)

                # 将自动检测的本地文件作为原生附件发送
                for file_path in local_files:
                    if human_delay > 0:
                        await asyncio.sleep(human_delay)
                    try:
                        ext = Path(file_path).suffix.lower()
                        if ext in _IMAGE_EXTS:
                            await self.send_image_file(
                                chat_id=event.source.chat_id,
                                image_path=file_path,
                                metadata=_thread_metadata,
                            )
                        elif ext in _VIDEO_EXTS:
                            await self.send_video(
                                chat_id=event.source.chat_id,
                                video_path=file_path,
                                metadata=_thread_metadata,
                            )
                        else:
                            await self.send_document(
                                chat_id=event.source.chat_id,
                                file_path=file_path,
                                metadata=_thread_metadata,
                            )
                    except Exception as file_err:
                        logger.error("[%s] 发送本地文件 %s 错误: %s", self.name, file_path, file_err)

            # 为处理钩子确定整体成功状态
            processing_ok = delivery_succeeded if delivery_attempted else not bool(response)
            await self._run_processing_hook("on_processing_complete", event, processing_ok)

            # 检查是否有在处理过程中排队的待处理消息
            if session_key in self._pending_messages:
                pending_event = self._pending_messages.pop(session_key)
                logger.debug("[%s] 处理中断中的排队消息", self.name)
                # 在处理待处理消息之前清理当前会话
                if session_key in self._active_sessions:
                    del self._active_sessions[session_key]
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
                # 在新的后台任务中处理待处理消息
                await self._process_message_background(pending_event, session_key)
                return  # 已经清理完毕
                
        except asyncio.CancelledError:
            await self._run_processing_hook("on_processing_complete", event, False)
            raise
        except Exception as e:
            await self._run_processing_hook("on_processing_complete", event, False)
            logger.error("[%s] 处理消息错误: %s", self.name, e, exc_info=True)
            # 向用户发送错误消息，以免他们陷入沉默
            try:
                error_type = type(e).__name__
                error_detail = str(e)[:300] if str(e) else "无可用详情"
                _thread_metadata = {"thread_id": event.source.thread_id} if event.source.thread_id else None
                await self.send(
                    chat_id=event.source.chat_id,
                    content=(
                        f"抱歉，遇到了错误 ({error_type})。\n"
                        f"{error_detail}\n"
                        "请重试或使用 /reset 开始新的会话。"
                    ),
                    metadata=_thread_metadata,
                )
            except Exception:
                pass  # 最后一招 — 不要让错误报告崩溃处理器
        finally:
            # 停止打字指示器
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            # 同时取消任何平台级别的持续打字任务（例如 Discord）
            # 这些任务可能在最后一次 stop_typing() 后被 _keep_typing 重新创建
            try:
                if hasattr(self, "stop_typing"):
                    await self.stop_typing(event.source.chat_id)
            except Exception:
                pass
            # 清理会话跟踪
            if session_key in self._active_sessions:
                del self._active_sessions[session_key]
    
    async def cancel_background_tasks(self) -> None:
        """取消任何进行中的后台消息处理任务。

        在 gateway 关闭/替换期间使用，以便旧进程的活动会话
        在适配器被拆除后不会继续运行。
        """
        tasks = [task for task in self._background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._pending_messages.clear()
        self._active_sessions.clear()

    def has_pending_interrupt(self, session_key: str) -> bool:
        """检查会话是否有待处理的中断。"""
        return session_key in self._active_sessions and self._active_sessions[session_key].is_set()
    
    def get_pending_message(self, session_key: str) -> Optional[MessageEvent]:
        """获取并清除会话的任何待处理消息。"""
        return self._pending_messages.pop(session_key, None)
    
    def build_source(
        self,
        chat_id: str,
        chat_name: Optional[str] = None,
        chat_type: str = "dm",
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        thread_id: Optional[str] = None,
        chat_topic: Optional[str] = None,
        user_id_alt: Optional[str] = None,
        chat_id_alt: Optional[str] = None,
    ) -> SessionSource:
        """为此平台构建 SessionSource 的辅助方法。"""
        # 将空主题规范化为 None
        if chat_topic is not None and not chat_topic.strip():
            chat_topic = None
        return SessionSource(
            platform=self.platform,
            chat_id=str(chat_id),
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=str(user_id) if user_id else None,
            user_name=user_name,
            thread_id=str(thread_id) if thread_id else None,
            chat_topic=chat_topic.strip() if chat_topic else None,
            user_id_alt=user_id_alt,
            chat_id_alt=chat_id_alt,
        )
    
    @abstractmethod
    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """
        获取聊天/频道的信息。
        
        返回至少包含以下内容的字典：
        - name: 聊天名称
        - type: "dm", "group", "channel"
        """
        pass
    
    def format_message(self, content: str) -> str:
        """
        为此平台格式化消息。
        
        子类应覆盖以处理平台特定的格式化
        （例如 Telegram MarkdownV2、Discord markdown）。
        
        默认实现原样返回内容。
        """
        return content
    
    @staticmethod
    def truncate_message(content: str, max_length: int = 4096) -> List[str]:
        """
        将长消息分割成块，保留代码块边界。

        当分割落在三反引号代码块内部时，围栏在当前块的末尾关闭，
        并在下一个块的开头重新打开（使用原始语言标签）。
        多块响应会收到类似 ``(1/3)`` 的指示器。

        参数：
            content: 完整的消息内容
            max_length: 每块的最大长度（平台特定）

        返回：
            消息块列表
        """
        if len(content) <= max_length:
            return [content]

        INDICATOR_RESERVE = 10   # 为 " (XX/XX)" 预留空间
        FENCE_CLOSE = "\n```"

        chunks: List[str] = []
        remaining = content
        # 当上一个块在代码块中间结束时，这保存语言标签（可能是 ""），
        # 以便我们可以重新打开围栏。
        carry_lang: Optional[str] = None

        while remaining:
            # 如果从前一个块继续代码块，
            # 使用相同的语言标签预先添加新的打开围栏。
            prefix = f"```{carry_lang}\n" if carry_lang is not None else ""

            # 在考虑前缀、可能的关闭围栏和块指示器后，
            # 我们可以容纳多少正文文本。
            headroom = max_length - INDICATOR_RESERVE - len(prefix) - len(FENCE_CLOSE)
            if headroom < 1:
                headroom = max_length // 2

            # 剩余的所有内容适合一个最终块
            if len(prefix) + len(remaining) <= max_length - INDICATOR_RESERVE:
                chunks.append(prefix + remaining)
                break

            # 找到自然的分割点（优先换行，然后空格）
            region = remaining[:headroom]
            split_at = region.rfind("\n")
            if split_at < headroom // 2:
                split_at = region.rfind(" ")
            if split_at < 1:
                split_at = headroom

            # 避免在行内代码跨度内分割（`...`）。
            # 如果 split_at 之前的文本有奇数个未转义的反引号，
            # 分割会落在行内代码内部 — 生成的块会有不成对的反引号，
            # 内部任何特殊字符（如括号）会被转义，
            # 导致 Telegram 上的 MarkdownV2 解析错误。
            candidate = remaining[:split_at]
            backtick_count = candidate.count("`") - candidate.count("\\`")
            if backtick_count % 2 == 1:
                # 找到最后一个未转义的反引号并在其前面分割
                last_bt = candidate.rfind("`")
                while last_bt > 0 and candidate[last_bt - 1] == "\\":
                    last_bt = candidate.rfind("`", 0, last_bt)
                if last_bt > 0:
                    # 尝试在反引号之前找到一个空格或换行
                    safe_split = candidate.rfind(" ", 0, last_bt)
                    nl_split = candidate.rfind("\n", 0, last_bt)
                    safe_split = max(safe_split, nl_split)
                    if safe_split > headroom // 4:
                        split_at = safe_split

            chunk_body = remaining[:split_at]
            remaining = remaining[split_at:].lstrip()

            full_chunk = prefix + chunk_body

            # 只遍历 chunk_body（而不是我们预先添加的前缀）
            # 以确定我们是否在打开的代码块内结束。
            in_code = carry_lang is not None
            lang = carry_lang or ""
            for line in chunk_body.split("\n"):
                stripped = line.strip()
                if stripped.startswith("```"):
                    if in_code:
                        in_code = False
                        lang = ""
                    else:
                        in_code = True
                        tag = stripped[3:].strip()
                        lang = tag.split()[0] if tag else ""

            if in_code:
                # 关闭孤立的围栏，使块自身有效
                full_chunk += FENCE_CLOSE
                carry_lang = lang
            else:
                carry_lang = None

            chunks.append(full_chunk)

        # 当响应跨越多条消息时附加块指示器
        if len(chunks) > 1:
            total = len(chunks)
            chunks = [
                f"{chunk} ({i + 1}/{total})" for i, chunk in enumerate(chunks)
            ]

        return chunks
