from __future__ import annotations

from app.services.channels.base import ChannelSendResult, MessageChannel


_current_channel: MessageChannel | None = None


def set_current_channel(channel: MessageChannel | None) -> None:
    global _current_channel
    _current_channel = channel


def get_current_channel() -> MessageChannel | None:
    return _current_channel


async def send_channel_text(chat_id: str, content: str) -> ChannelSendResult:
    if not _current_channel:
        return ChannelSendResult(success=False, error="No message channel is connected")
    return await _current_channel.send_text(chat_id, content)


async def send_channel_document(
    chat_id: str, file_path: str, caption: str | None = None
) -> ChannelSendResult:
    if not _current_channel:
        return ChannelSendResult(success=False, error="No message channel is connected")
    return await _current_channel.send_document(chat_id, file_path, caption=caption)
