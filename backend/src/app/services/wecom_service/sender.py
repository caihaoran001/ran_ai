from __future__ import annotations

from app.services.channels import send_channel_document, send_channel_text
from app.services.channels.base import ChannelSendResult


async def send_wecom_message(chat_id: str, content: str) -> ChannelSendResult:
    return await send_channel_text(chat_id, content)


async def send_wecom_document(
    chat_id: str, file_path: str, caption: str | None = None
) -> ChannelSendResult:
    return await send_channel_document(chat_id, file_path, caption=caption)
