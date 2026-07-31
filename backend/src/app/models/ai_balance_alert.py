from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AIBalanceAlert(TimestampMixin, Base):
    """低余额告警的天闸：每天最多一条。

    账户级（非 per-case），故不复用 case 级的 ReminderEvent。靠 alert_date 唯一约束做
    "先插入抢占、冲突即当天已发" 的乐观节流；跨自然日自动重置。
    """

    __tablename__ = "ai_balance_alerts"
    __table_args__ = (
        UniqueConstraint("alert_date", name="uq_ai_balance_alert_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    balance_yuan: Mapped[float | None] = mapped_column(Float)
