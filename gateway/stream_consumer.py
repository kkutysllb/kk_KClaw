"""Gateway 流式消费者 — 将同步代理回调桥接到异步平台传递。

代理从其工作线程同步触发 stream_delta_callback(text)。
GatewayStreamConsumer：
  1. 通过 on_delta() 接收增量（线程安全，同步）
  2. 通过 queue.Queue 将它们排队到 asyncio 任务
  3. 异步 run() 任务缓冲、限速，并逐步编辑
     目标平台上的单条消息

设计：使用编辑传输（发送初始消息，然后 editMessageText）。
这在 Telegram、Discord 和 Slack 上得到普遍支持。

致谢：jobless0x (#774, #1312)、OutThisLife (#798)、clicksingh (#697)。
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("gateway.stream_consumer")

# 哨兵，表示流已完成
_DONE = object()

# 哨兵，表示工具边界 — 完成当前消息并开始新消息，
# 以便后续文本出现在工具进度消息下方。
_NEW_SEGMENT = object()


@dataclass
class StreamConsumerConfig:
    """单个流消费者实例的运行时配置。"""
    edit_interval: float = 0.3
    buffer_threshold: int = 40
    cursor: str = " ▉"


class GatewayStreamConsumer:
    """异步消费者，使用流式标记逐步编辑平台消息。

    用法::

        consumer = GatewayStreamConsumer(adapter, chat_id, config, metadata=metadata)
        # 将 consumer.on_delta 作为 stream_delta_callback 传递给 AIAgent
        agent = AIAgent(..., stream_delta_callback=consumer.on_delta)
        # 将消费者作为 asyncio 任务启动
        task = asyncio.create_task(consumer.run())
        # ... 在线程池中运行代理 ...
        consumer.finish()  # 信号完成
        await task         # 等待最终编辑
    """

    def __init__(
        self,
        adapter: Any,
        chat_id: str,
        config: Optional[StreamConsumerConfig] = None,
        metadata: Optional[dict] = None,
    ):
        self.adapter = adapter
        self.chat_id = chat_id
        self.cfg = config or StreamConsumerConfig()
        self.metadata = metadata
        self._queue: queue.Queue = queue.Queue()
        self._accumulated = ""
        self._message_id: Optional[str] = None
        self._already_sent = False
        self._edit_supported = True  # 在首次编辑失败时禁用（Signal/Email/HA）
        self._last_edit_time = 0.0
        self._last_sent_text = ""   # 跟踪最后发送的文本以跳过冗余编辑
        self._fallback_final_send = False
        self._fallback_prefix = ""

    @property
    def already_sent(self) -> bool:
        """如果至少发送/编辑了一条消息则返回 True — 向基础适配器发出信号
        跳过重新发送最终响应。"""
        return self._already_sent

    def on_delta(self, text: str) -> None:
        """线程安全回调 — 从代理的工作线程调用。

        当 *text* 是 ``None`` 时，表示工具边界：当前消息
        被最终确定，后续文本将作为新消息发送，
        以便出现在网关在中间发送的任何工具进度消息下方。
        """
        if text:
            self._queue.put(text)
        elif text is None:
            self._queue.put(_NEW_SEGMENT)

    def finish(self) -> None:
        """信号流已完成。"""
        self._queue.put(_DONE)

    async def run(self) -> None:
        """耗尽队列并编辑平台消息的异步任务。"""
        # 平台消息长度限制 — 为光标和格式化留出空间
        _raw_limit = getattr(self.adapter, "MAX_MESSAGE_LENGTH", 4096)
        _safe_limit = max(500, _raw_limit - len(self.cfg.cursor) - 100)

        try:
            while True:
                # 耗尽队列中所有可用项
                got_done = False
                got_segment_break = False
                while True:
                    try:
                        item = self._queue.get_nowait()
                        if item is _DONE:
                            got_done = True
                            break
                        if item is _NEW_SEGMENT:
                            got_segment_break = True
                            break
                        self._accumulated += item
                    except queue.Empty:
                        break

                # 决定是否刷新编辑
                now = time.monotonic()
                elapsed = now - self._last_edit_time
                should_edit = (
                    got_done
                    or got_segment_break
                    or (elapsed >= self.cfg.edit_interval
                        and self._accumulated)
                    or len(self._accumulated) >= self.cfg.buffer_threshold
                )

                if should_edit and self._accumulated:
                    # 分割溢出：如果累积文本超过平台
                    # 限制，最终确定当前消息并开始新消息。
                    while (
                        len(self._accumulated) > _safe_limit
                        and self._message_id is not None
                        and self._edit_supported
                    ):
                        split_at = self._accumulated.rfind("\n", 0, _safe_limit)
                        if split_at < _safe_limit // 2:
                            split_at = _safe_limit
                        chunk = self._accumulated[:split_at]
                        await self._send_or_edit(chunk)
                        if self._fallback_final_send:
                            # 在尝试分割过大消息时编辑失败。
                            # 保持完整的累积文本完整，
                            # 以便回退最终发送路径可以投递
                            # 剩余的继续内容而不丢失内容。
                            break
                        self._accumulated = self._accumulated[split_at:].lstrip("\n")
                        self._message_id = None
                        self._last_sent_text = ""

                    display_text = self._accumulated
                    if not got_done and not got_segment_break:
                        display_text += self.cfg.cursor

                    await self._send_or_edit(display_text)
                    self._last_edit_time = time.monotonic()

                if got_done:
                    # 最终编辑（无光标）。如果渐进编辑
                    # 在中途失败，发送一个单独的继续/回退消息
                    # 这里，而不是让基础网关路径再次发送
                    # 完整响应。
                    if self._accumulated:
                        if self._fallback_final_send:
                            await self._send_fallback_final(self._accumulated)
                        elif self._message_id:
                            await self._send_or_edit(self._accumulated)
                        elif not self._already_sent:
                            await self._send_or_edit(self._accumulated)
                    return

                # 工具边界：should_edit 块已经在没有光标的情况下刷新了
                # 累积文本。重置状态，以便下一个
                # 文本块在网关在中间发送的工具进度
                # 消息下方创建新消息。
                if got_segment_break:
                    self._message_id = None
                    self._accumulated = ""
                    self._last_sent_text = ""
                    self._fallback_final_send = False
                    self._fallback_prefix = ""

                await asyncio.sleep(0.05)  # 小让步以避免忙循环

        except asyncio.CancelledError:
            # 取消时最佳努力最终编辑
            if self._accumulated and self._message_id:
                try:
                    await self._send_or_edit(self._accumulated)
                except Exception:
                    pass
        except Exception as e:
            logger.error("流消费者错误: %s", e)

    # 用于剥离 MEDIA:<path> 标签（包括可选的周围引号）的模式。
    # 与 gateway/platforms/base.py 中非流式路径使用的简单清理正则表达式匹配。
    _MEDIA_RE = re.compile(r'''[`"']?MEDIA:\s*\S+[`"']?''')

    @staticmethod
    def _clean_for_display(text: str) -> str:
        """在显示前从文本中剥离 MEDIA: 指令和内部标记。

        流式路径传递可能包含
        ``MEDIA:<path>`` 标签和 ``[[audio_as_voice]]`` 指令的原始文本块，
        这些是为平台适配器的后处理准备的。
        实际的媒体文件在流结束后通过 ``_deliver_media_from_response()``
        单独传递 — 我们只需要从用户那里隐藏原始指令。
        """
        if "MEDIA:" not in text and "[[audio_as_voice]]" not in text:
            return text
        cleaned = text.replace("[[audio_as_voice]]", "")
        cleaned = GatewayStreamConsumer._MEDIA_RE.sub("", cleaned)
        # 折叠移除标签后遗留的过多空行
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        # 剥离尾随空白/换行但保留前导内容
        return cleaned.rstrip()

    def _visible_prefix(self) -> str:
        """返回流式消息中已显示的可见文本。"""
        prefix = self._last_sent_text or ""
        if self.cfg.cursor and prefix.endswith(self.cfg.cursor):
            prefix = prefix[:-len(self.cfg.cursor)]
        return self._clean_for_display(prefix)

    def _continuation_text(self, final_text: str) -> str:
        """返回用户尚未看到的 final_text 部分。"""
        prefix = self._fallback_prefix or self._visible_prefix()
        if prefix and final_text.startswith(prefix):
            return final_text[len(prefix):].lstrip()
        return final_text

    @staticmethod
    def _split_text_chunks(text: str, limit: int) -> list[str]:
        """将文本分割成合理大小的块以供回退发送使用。"""
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at < limit // 2:
                split_at = limit
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        return chunks

    async def _send_fallback_final(self, text: str) -> None:
        """在流式编辑停止工作后发送最终继续。"""
        final_text = self._clean_for_display(text)
        continuation = self._continuation_text(final_text)
        self._fallback_final_send = False
        if not continuation.strip():
            # 没有新内容可发送 — 可见的部分已经匹配最终文本。
            self._already_sent = True
            return

        raw_limit = getattr(self.adapter, "MAX_MESSAGE_LENGTH", 4096)
        safe_limit = max(500, raw_limit - 100)
        chunks = self._split_text_chunks(continuation, safe_limit)

        last_message_id: Optional[str] = None
        last_successful_chunk = ""
        sent_any_chunk = False
        for chunk in chunks:
            result = await self.adapter.send(
                chat_id=self.chat_id,
                content=chunk,
                metadata=self.metadata,
            )
            if not result.success:
                if sent_any_chunk:
                    # 一些继续内容已经到达用户。抑制
                    # 基础网关最终发送路径，这样我们就不会重新发送
                    # 完整响应并创建另一个重复。
                    self._already_sent = True
                    self._message_id = last_message_id
                    self._last_sent_text = last_successful_chunk
                    self._fallback_prefix = ""
                    return
                # 没有回退块到达用户 — 允许正常的网关
                # 最终发送路径再试一次。
                self._already_sent = False
                self._message_id = None
                self._last_sent_text = ""
                self._fallback_prefix = ""
                return
            sent_any_chunk = True
            last_successful_chunk = chunk
            last_message_id = result.message_id or last_message_id

        self._message_id = last_message_id
        self._already_sent = True
        self._last_sent_text = chunks[-1]
        self._fallback_prefix = ""

    async def _send_or_edit(self, text: str) -> None:
        """发送或编辑流式消息。"""
        # 剥离 MEDIA: 指令，以免它们显示为可见文本。
        # 媒体文件在流结束后作为原生附件传递
        #（通过 gateway/run.py 中的 _deliver_media_from_response）。
        text = self._clean_for_display(text)
        if not text.strip():
            return
        try:
            if self._message_id is not None:
                if self._edit_supported:
                    # 如果文本与我们上次发送的相同则跳过
                    if text == self._last_sent_text:
                        return
                    # 编辑现有消息
                    result = await self.adapter.edit_message(
                        chat_id=self.chat_id,
                        message_id=self._message_id,
                        content=text,
                    )
                    if result.success:
                        self._already_sent = True
                        self._last_sent_text = text
                    else:
                        # 如果在流中间编辑失败（尤其是 Telegram 限流），
                        # 停止渐进编辑，仅在
                        # 最终响应可用时发送缺失的尾部。
                        logger.debug("编辑失败，为此适配器禁用流式传输")
                        self._fallback_prefix = self._visible_prefix()
                        self._fallback_final_send = True
                        self._edit_supported = False
                        self._already_sent = True
                else:
                    # 不支持编辑 — 跳过中间更新。
                    # 最终响应将由回退路径发送。
                    pass
            else:
                # 第一条消息 — 发送新的
                result = await self.adapter.send(
                    chat_id=self.chat_id,
                    content=text,
                    metadata=self.metadata,
                )
                if result.success and result.message_id:
                    self._message_id = result.message_id
                    self._already_sent = True
                    self._last_sent_text = text
                elif result.success:
                    # 平台接受了消息但没有返回 message_id
                    #（例如 Signal）。没有 ID 无法编辑 — 切换到
                    # 回退模式：抑制中间增量，仅在
                    # 最终响应准备好后发送缺失的尾部。
                    self._already_sent = True
                    self._edit_supported = False
                    self._fallback_prefix = self._clean_for_display(text)
                    self._fallback_final_send = True
                    # 哨兵防止在此分支上重新进入每个增量
                    self._message_id = "__no_edit__"
                else:
                    # 初始发送失败 — 为此会话禁用流式传输
                    self._edit_supported = False
        except Exception as e:
            logger.error("流发送/编辑错误: %s", e)
