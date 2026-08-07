from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.service_case import ServiceCase


class ReminderEventType(str, enum.Enum):
    SEEN = "seen"
    REMINDED = "reminded"


# 流失召回的台账规则名。召回的次数与间隔全部从这条规则的历史事件推导，不另设状态字段，
# 所以规则名本身是数据契约的一部分——写入方(调度器)和读取方(看板)必须用同一个常量。
CHURN_RECALL_RULE_ID = "churn_recall"


class ReminderEvent(TimestampMixin, Base):
    __tablename__ = "reminder_events"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "rule_id",
            "event_date",
            "event_type",
            name="uq_reminder_case_rule_date_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[ReminderEventType] = mapped_column(
        Enum(ReminderEventType, name="reminder_event_type"),
        nullable=False,
    )

    service_case: Mapped[ServiceCase] = relationship(back_populates="reminder_events")
