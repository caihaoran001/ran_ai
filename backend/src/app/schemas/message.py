from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.message import MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.schemas.common import Timestamped


class MessageSend(BaseModel):
    content: str = Field(min_length=1)
    purpose: MessagePurpose = MessagePurpose.MANUAL


class MessageRead(Timestamped):
    id: int
    service_case_id: int | None
    group_id: str
    wecom_message_id: str | None
    direction: MessageDirection
    sender_type: MessageSenderType
    message_type: str
    content: str
    purpose: MessagePurpose
    status: MessageStatus
    error: str | None
