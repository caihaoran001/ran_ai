from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistQuarantine(TimestampMixin, Base):
    """档案官主动跳过的单条消息及其人工重放状态。"""

    __tablename__ = "archivist_quarantines"
    __table_args__ = (
        Index(
            "ix_archivist_quarantines_status_quarantined_at",
            "status",
            "quarantined_at",
        ),
    )

    STATUS_QUARANTINED = "quarantined"
    STATUS_REPLAYED = "replayed"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=STATUS_QUARANTINED,
        index=True,
    )
    failure_fingerprint: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replay_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replay_error: Mapped[str | None] = mapped_column(Text)
