from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.service_case import ServiceCase


class AgentReminderTaskStatus(str, enum.Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentReminderTask(TimestampMixin, Base):
    __tablename__ = "agent_reminder_tasks"
    __table_args__ = (
        Index("ix_agent_reminder_tasks_status_remind_at", "status", "remind_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trigger_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AgentReminderTaskStatus] = mapped_column(
        Enum(AgentReminderTaskStatus, name="agent_reminder_task_status"),
        nullable=False,
        default=AgentReminderTaskStatus.PENDING,
    )
    sent_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    service_case: Mapped[ServiceCase] = relationship(back_populates="agent_reminder_tasks")
    trigger_message: Mapped[Message | None] = relationship(foreign_keys=[trigger_message_id])
    sent_message: Mapped[Message | None] = relationship(foreign_keys=[sent_message_id])
