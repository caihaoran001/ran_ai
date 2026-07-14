from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CheckinFollowupCycle(TimestampMixin, Base):
    """A durable daily envelope for all proactive check-in messages of one case.

    A cycle is deliberately date based rather than message based: two messages on the
    same day are still only one missed check-in opportunity.  This makes the silence
    brake stable across scheduler restarts and channel retries.
    """

    __tablename__ = "checkin_followup_cycles"
    __table_args__ = (
        UniqueConstraint("service_case_id", "cycle_date", name="ux_checkin_followup_cycle_case_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    cycle_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Which kinds were actually sent, and their message/expected-item audit data.
    sent_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    message_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    answered_item_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # open -> answered / unanswered.  Late valid answers can still flip unanswered to answered.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
