from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class PopulationInsightSnapshot(TimestampMixin, Base):
    """群体共同洞察的生成快照。

    生成过程也是一条快照记录：status=generating/failed/ready。
    ready 记录永不覆盖，因此一次更新失败时页面仍能读上一版成功结果。
    """

    __tablename__ = "population_insight_snapshots"
    __table_args__ = (
        Index(
            "ux_population_insight_snapshots_scope_generating",
            "scope_key",
            unique=True,
            postgresql_where=text("status = 'generating'"),
            sqlite_where=text("status = 'generating'"),
        ),
        Index(
            "ix_population_insight_snapshots_scope_status_generated",
            "scope_key",
            "status",
            "generated_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_case_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    included_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    profiled_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_input_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    generated_by_model: Mapped[str | None] = mapped_column(String(128))
    refresh_reason: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
