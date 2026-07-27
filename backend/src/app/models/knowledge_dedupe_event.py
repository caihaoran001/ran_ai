from __future__ import annotations

from sqlalchemy import Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class KnowledgeDedupeEvent(TimestampMixin, Base):
    """Append-only audit record for one knowledge draft dedupe decision."""

    __tablename__ = "knowledge_dedupe_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_drafts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL"),
        index=True,
    )
    relation: Mapped[str] = mapped_column(String(40), nullable=False)
    answer_relation: Mapped[str | None] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resolution: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    resolved_by: Mapped[str] = mapped_column(String(24), nullable=False)
    distance: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict | None] = mapped_column(JSON)
