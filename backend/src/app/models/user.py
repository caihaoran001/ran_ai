from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True)
    wecom_user_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    notes: Mapped[str | None] = mapped_column(String(1000))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service_cases: Mapped[list[ServiceCase]] = relationship(back_populates="user")
