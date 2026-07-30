from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CheckinMessageReceipt(TimestampMixin, Base):
    """One durable check-in write receipt for an inbound turn and target date."""

    __tablename__ = "checkin_message_receipts"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "source_message_id",
            "target_date",
            name="ux_checkin_message_receipt_source_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    checkin_id: Mapped[int] = mapped_column(
        ForeignKey("checkins.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel: Mapped[str | None] = mapped_column(String(32))
    checklist_source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
