from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistPostCommitTask(TimestampMixin, Base):
    """档案官主事务提交后的最终一致任务。

    任务意图与归档水位线在同一事务写入；执行器允许至少一次执行，因此各处理器必须幂等。
    """

    __tablename__ = "archivist_post_commit_tasks"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "watermark_to",
            "task_type",
            name="ux_archivist_post_task_case_watermark_type",
        ),
        Index(
            "ix_archivist_post_tasks_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
    )

    STATUS_PENDING = "pending"
    STATUS_FAILED = "failed"
    STATUS_SUCCEEDED = "succeeded"

    TYPE_CASE_MEMORY = "case_memory"
    TYPE_FAQ_DRAFTS = "faq_drafts"
    TYPE_CASE_SYNTHESIS = "case_synthesis"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    watermark_to: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=STATUS_PENDING,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
