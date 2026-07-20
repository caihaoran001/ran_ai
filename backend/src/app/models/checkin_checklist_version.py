from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CheckinChecklistVersion(TimestampMixin, Base):
    """Append-only snapshot of the five daily questions selected for one case."""

    __tablename__ = "checkin_checklist_versions"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "version_no",
            name="ux_checkin_checklist_version_case_no",
        ),
        UniqueConstraint(
            "source_message_id",
            name="ux_checkin_checklist_version_source_message",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("user_documents.id"))
    changed_by_id: Mapped[str | None] = mapped_column(String(128))
    changed_by_name: Mapped[str | None] = mapped_column(String(128))
