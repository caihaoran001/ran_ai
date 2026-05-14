from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.checkin import Checkin
    from app.models.message import Message
    from app.models.questionnaire import QuestionnaireSubmission
    from app.models.reminder import ReminderEvent
    from app.models.user import User
    from app.models.user_document import UserDocument
    from app.models.user_profile import UserProfile


class ServiceCaseStatus(str, enum.Enum):
    CREATED = "created"
    QUESTIONNAIRE_SUBMITTED = "questionnaire_submitted"
    DOCUMENTS_GENERATED = "documents_generated"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    IN_SERVICE = "in_service"
    COMPLETED = "completed"


class ServiceCase(TimestampMixin, Base):
    __tablename__ = "service_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    questionnaire_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[ServiceCaseStatus] = mapped_column(
        Enum(ServiceCaseStatus, name="service_case_status"),
        default=ServiceCaseStatus.CREATED,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(2000))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="service_cases")
    questionnaire_submissions: Mapped[list[QuestionnaireSubmission]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    user_profiles: Mapped[list[UserProfile]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    user_documents: Mapped[list[UserDocument]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    checkins: Mapped[list[Checkin]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    reminder_events: Mapped[list[ReminderEvent]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
