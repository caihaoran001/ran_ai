from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(slots=True)
class ChannelMessage:
    channel: str
    chat_id: str
    chat_type: str
    text: str
    message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    message_type: str = "text"
    raw_payload: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # 聚合前的消息分段：(正文, [(被引用消息ID, 被引用原文), ...])，保持入站顺序；
    # qiwe 入站总是填充，聚合窗口合并后逐段拼接；非 qiwe 消息为空时退化为 text。
    segments: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    # Durable transport events that were merged into this business turn.
    # The engine links them to the persisted inbound Message in the same DB
    # transaction, closing the inbox -> LangGraph crash window.
    inbound_event_ids: list[int] = field(default_factory=list)
    # 本轮正文里含有由语音转写来的内容。转写文本按用户原话处理，但语音识别对数字最
    # 容易出错，故一路带到落库与模型上下文，让回复方在数字对不上时主动确认。
    voice_transcript: bool = False


@dataclass(slots=True)
class ChannelSendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None
    raw_response: Any = None
    retryable: bool = False
    # True means the request may have reached the channel, but no trustworthy
    # response was received (timeout/connection reset/invalid response).
    delivery_uncertain: bool = False


class MessageChannel(Protocol):
    name: str

    async def send_text(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: str | None = None,
        reply_context: Any = None,
    ) -> ChannelSendResult:
        ...

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: str | None = None,
        reply_to: str | None = None,
        reply_context: Any = None,
    ) -> ChannelSendResult:
        ...

    async def send_hypertext(
        self,
        chat_id: str,
        content: list[dict[str, Any]],
        *,
        reply_to: str | None = None,
        reply_context: Any = None,
    ) -> ChannelSendResult:
        ...
