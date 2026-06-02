from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.service_case import ServiceCase


class OutcomeEvent(TimestampMixin, Base):
    __tablename__ = "outcome_events"
    __table_args__ = (
        Index("ix_outcome_events_case_milestone", "service_case_id", "milestone"),
        UniqueConstraint("service_case_id", "message_id", "field_key", name="ux_outcome_events_dedup"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(ForeignKey("service_cases.id"), nullable=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), index=True)
    milestone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="observation")
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="chat", nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    service_case: Mapped[ServiceCase] = relationship(back_populates="outcome_events")
    message: Mapped[Message | None] = relationship()


class OutcomeStageState(TimestampMixin, Base):
    __tablename__ = "outcome_stage_states"
    __table_args__ = (
        UniqueConstraint("service_case_id", "milestone", name="ux_outcome_stage_state_case_milestone"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(ForeignKey("service_cases.id"), nullable=False)
    milestone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    collected_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSON)
    last_followup_field: Mapped[str | None] = mapped_column(String(64))
    last_followup_question: Mapped[str | None] = mapped_column(Text)
    last_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    followup_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    service_case: Mapped[ServiceCase] = relationship(back_populates="outcome_stage_states")


class OutcomeSnapshot(TimestampMixin, Base):
    __tablename__ = "outcome_snapshots"
    __table_args__ = (
        UniqueConstraint("service_case_id", "milestone", name="ux_outcome_snapshot_case_milestone"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(ForeignKey("service_cases.id"), nullable=False)
    milestone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    action_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service_case: Mapped[ServiceCase] = relationship(back_populates="outcome_snapshots")
