from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CheckinRewardProgram(TimestampMixin, Base):
    """一个服务单只有一轮累计奖励，同时保存当前寄送对话状态。"""

    __tablename__ = "checkin_reward_programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    intro_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    intro_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_choice: Mapped[str | None] = mapped_column(String(24))
    choice_tier_days: Mapped[int | None] = mapped_column(Integer)
    recipient_name: Mapped[str | None] = mapped_column(String(100))
    recipient_phone: Mapped[str | None] = mapped_column(String(32))
    shipping_address: Mapped[str | None] = mapped_column(String(1000))
    choice_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckinRewardShipment(TimestampMixin, Base):
    """寄送批次由用户选择立即寄出时创建，只能由运营标记已发货。"""

    __tablename__ = "checkin_reward_shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(100))
    recipient_phone: Mapped[str | None] = mapped_column(String(32))
    shipping_address: Mapped[str | None] = mapped_column(String(1000))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marked_by: Mapped[str | None] = mapped_column(String(100))
    notification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="not_applicable",
    )
    notification_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )


class CheckinRewardQualification(TimestampMixin, Base):
    """达标资格是不可逆的发奖事实；同一服务单的每档只能有一条。"""

    __tablename__ = "checkin_reward_qualifications"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "tier_days",
            name="uq_checkin_reward_qualification_case_tier",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tier_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_code: Mapped[str] = mapped_column(String(40), nullable=False)
    reward_name: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    qualified_on: Mapped[date] = mapped_column(Date, nullable=False)
    notification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending",
    )
    notification_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    shipment_id: Mapped[int | None] = mapped_column(
        ForeignKey("checkin_reward_shipments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class CheckinRewardProgressNotice(TimestampMixin, Base):
    """2/6/13/27 天进度提醒去重账本。"""

    __tablename__ = "checkin_reward_progress_notices"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "threshold_days",
            name="uq_checkin_reward_progress_case_threshold",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    threshold_days: Mapped[int] = mapped_column(Integer, nullable=False)
    next_tier_days: Mapped[int] = mapped_column(Integer, nullable=False)
    notification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending",
    )
    notification_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
