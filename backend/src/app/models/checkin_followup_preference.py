from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CheckinFollowupPreference(TimestampMixin, Base):
    """A service user's durable opt-in/out choice for a proactive follow-up topic."""

    __tablename__ = "checkin_followup_preferences"
    __table_args__ = (
        UniqueConstraint("service_case_id", "topic", name="ux_checkin_followup_preference_case_topic"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The inbound user message that explicitly changed this setting.  The group
    # transcript remains the immutable full evidence; this is the fast audit link.
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
