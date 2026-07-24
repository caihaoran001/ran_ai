from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageSenderType(str, enum.Enum):
    USER = "user"
    STAFF = "staff"
    BOT = "bot"
    SYSTEM = "system"


class MessagePurpose(str, enum.Enum):
    CHAT = "chat"
    CHECKIN = "checkin"
    REMINDER = "reminder"
    DOCUMENT_DELIVERY = "document_delivery"
    MANUAL = "manual"


class MessageStatus(str, enum.Enum):
    RECEIVED = "received"
    PENDING = "pending"
    # SENDING：已经开始调用渠道 API、但尚未拿到可信结果的「在途」态。
    # 等发送锁/限流时保持 PENDING；仅在真正 HTTP 请求前切换到 SENDING。
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    # 请求可能已经被渠道接收，但进程没有拿到可信结果。UNKNOWN 绝不能自动重发，
    # 否则可能在群里产生重复消息；后续只能通过渠道历史记录或人工确认来对账。
    UNKNOWN = "unknown"


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_cases.id"),
        index=True,
    )
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    wecom_message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, name="message_direction"),
        nullable=False,
    )
    sender_type: Mapped[MessageSenderType] = mapped_column(
        Enum(MessageSenderType, name="message_sender_type"),
        nullable=False,
    )
    message_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[MessagePurpose] = mapped_column(
        Enum(MessagePurpose, name="message_purpose"),
        default=MessagePurpose.CHAT,
        nullable=False,
    )
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, name="message_status"),
        default=MessageStatus.RECEIVED,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text)
    # 出站消息确认发送成功的时间(UTC)；未发送/发送失败为空。
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_json: Mapped[dict | None] = mapped_column(JSON)
    trigger_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), index=True)
    
    trigger: Mapped[Message | None] = relationship(remote_side=[id])
    service_case: Mapped[ServiceCase | None] = relationship(back_populates="messages")
