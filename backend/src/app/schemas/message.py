from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from pydantic import computed_field

from app.models.message import MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.schemas.common import Timestamped


class MessageSend(BaseModel):
    content: str = Field(min_length=1)
    purpose: MessagePurpose = MessagePurpose.MANUAL


class MessageUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)


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
    analysis_json: dict | None = None
    trigger: MessageRead | None = None

    @computed_field
    @property
    def display_at(self) -> datetime:
        meta = self.analysis_json.get("_qiwe_meta") if isinstance(self.analysis_json, dict) else None
        timestamp = meta.get("timestamp") if isinstance(meta, dict) else None
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp, timezone.utc)
        return self.created_at

    @computed_field
    @property
    def display_sender_name(self) -> str | None:
        meta = self.analysis_json.get("_qiwe_meta") if isinstance(self.analysis_json, dict) else None
        sender_name = meta.get("sender_name") if isinstance(meta, dict) else None
        if isinstance(sender_name, str) and sender_name.strip():
            return sender_name.strip()
        if self.sender_type == MessageSenderType.BOT:
            return "燃人.AI"
        if self.sender_type == MessageSenderType.STAFF:
            return "运营"
        if self.sender_type == MessageSenderType.SYSTEM:
            return "系统"
        return None
