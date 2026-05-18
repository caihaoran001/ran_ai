from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class QuestionnaireSubmission(TimestampMixin, Base):
    __tablename__ = "questionnaire_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    template_key: Mapped[str] = mapped_column(String(80), default="intake", nullable=False, index=True)
    answers_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    service_case: Mapped[ServiceCase] = relationship(back_populates="questionnaire_submissions")
