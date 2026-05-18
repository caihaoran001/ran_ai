from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class Checkin(TimestampMixin, Base):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    raw_message_id: Mapped[str | None] = mapped_column(String(128))
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    service_case: Mapped[ServiceCase] = relationship(back_populates="checkins")
