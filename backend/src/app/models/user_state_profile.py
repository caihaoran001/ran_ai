from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class UserStateProfile(TimestampMixin, Base):
    """Current user-state projection for one service case.

    `UserProfile` is the intake baseline generated from the questionnaire.
    This table is intentionally separate: it stores the user's latest operating
    state during service while preserving per-domain evidence history inside
    `state_json`. Outcome events remain the auditable event stream; this model
    is the fast-read projection for operators and chat context.
    """

    __tablename__ = "user_state_profiles"
    __table_args__ = (
        UniqueConstraint("service_case_id", name="ux_user_state_profiles_service_case_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(ForeignKey("service_cases.id"), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="intake", nullable=False)

    service_case: Mapped[ServiceCase] = relationship(back_populates="user_state_profile")
