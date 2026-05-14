from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class DocumentType(str, enum.Enum):
    ENERGY_RESTART_PRESCRIPTION = "energy_restart_prescription"
    DAY28_EXECUTION_STANDARD = "day28_execution_standard"


class DocumentStatus(str, enum.Enum):
    DRAFT = "draft"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    SENT = "sent"


class UserDocument(TimestampMixin, Base):
    __tablename__ = "user_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.DRAFT,
        nullable=False,
    )
    generated_by_model: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service_case: Mapped[ServiceCase] = relationship(back_populates="user_documents")
