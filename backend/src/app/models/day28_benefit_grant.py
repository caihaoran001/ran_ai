from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Day28BenefitGrant(TimestampMixin, Base):
    """One day-28 benefit whose claim entry was handed to one service case.

    唯一约束把"一个服务单一类券只发一次"钉在数据库层，不靠代码判断——重复发券
    是直接的钱损失，而调度器会因为漏扫补偿、重启重放等原因把同一批候选跑多次。

    `status` 目前只有 `link_sent` 一个终态：商城未打通，系统能确认的只有"领取入口
    已经发到群里"，看不到客户领没领、用没用。V2.0 明令"没有回执不得写已到账"，
    所以这里绝不出现 claimed / used 这类状态，将来打通了再加。
    """

    __tablename__ = "day28_benefit_grants"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "benefit_code",
            name="ux_day28_benefit_grant_case_code",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    benefit_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="link_sent")
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
