from __future__ import annotations

from wecom_agent_bridge import SendResult, WeComBotBridge, WeComMessage

from app.services.channels.base import ChannelMessage, ChannelSendResult


class WeComChannel:
    name = "wecom"

    def __init__(self, bridge: WeComBotBridge):
        self.bridge = bridge

    @property
    def connected(self) -> bool:
        return bool(self.bridge.connected)

    async def send_text(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: str | None = None,
        reply_context: object | None = None,
    ) -> ChannelSendResult:
        return _send_result(
            await self.bridge.send(chat_id=chat_id, content=content, reply_to=reply_to)
        )

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: str | None = None,
        reply_to: str | None = None,
        reply_context: object | None = None,
    ) -> ChannelSendResult:
        return _send_result(
            await self.bridge.send_document(
                chat_id=chat_id,
                file_path=file_path,
                caption=caption,
                reply_to=reply_to,
            )
        )

    async def send_hypertext(
        self,
        chat_id: str,
        content: list[dict[str, Any]],
        *,
        reply_to: str | None = None,
        reply_context: object | None = None,
    ) -> ChannelSendResult:
        # Fallback to plain text by joining the parts
        text_parts = []
        for item in content:
            if item.get("subtype") == 0:
                text_parts.append(item.get("text") or "")
            elif item.get("subtype") == 1:
                # Subtype 1 represents an @-mention in WeCom hypertext. 
                # Since WeComBotBridge only supports standard text, fallback to a textual mention.
                text_parts.append(f" @班主任 ")
        plain_text = "".join(text_parts)
        return await self.send_text(
            chat_id=chat_id,
            content=plain_text,
            reply_to=reply_to,
            reply_context=reply_context,
        )



def from_wecom_message(message: WeComMessage) -> ChannelMessage:
    return ChannelMessage(
        channel=WeComChannel.name,
        chat_id=message.source.chat_id,
        chat_type=message.source.chat_type,
        text=message.text or "",
        message_id=message.message_id,
        user_id=message.source.user_id,
        user_name=message.source.user_name,
        message_type=str(message.message_type.value),
        raw_payload=message.raw_payload,
        timestamp=message.timestamp,
    )


def _send_result(result: SendResult) -> ChannelSendResult:
    return ChannelSendResult(
        success=result.success,
        message_id=result.message_id,
        error=result.error,
        raw_response=result.raw_response,
        retryable=result.retryable,
    )
