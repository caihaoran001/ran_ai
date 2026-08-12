from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CuratedVideoSourceCursor(TimestampMixin, Base):
    """每个选品群的 durable 高水位；首次启用从当前收件箱尾部开始。"""

    __tablename__ = "curated_video_source_cursors"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_group_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    last_inbound_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CuratedVideoItem(TimestampMixin, Base):
    """运营在选品群投入的视频号素材队列。"""

    __tablename__ = "curated_video_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_inbound_event_id: Mapped[int] = mapped_column(
        ForeignKey("inbound_events.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    feed_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)


class CuratedVideoDailyRun(TimestampMixin, Base):
    """北京时间自然日天闸：即使 12 点无素材，当天也不会晚些时候补取。"""

    __tablename__ = "curated_video_daily_runs"
    __table_args__ = (
        UniqueConstraint("run_date", name="uq_curated_video_daily_run_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("curated_video_items.id", ondelete="RESTRICT"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CuratedVideoDelivery(TimestampMixin, Base):
    """一条每日素材对一个目标群的 record-first 发送状态。"""

    __tablename__ = "curated_video_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "daily_run_id",
            "group_id",
            name="uq_curated_video_delivery_run_group",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    daily_run_id: Mapped[int] = mapped_column(
        ForeignKey("curated_video_daily_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outbound_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        index=True,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_message_id: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
