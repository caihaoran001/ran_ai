from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CheckinMessageReceipt(TimestampMixin, Base):
    """One durable check-in write receipt for an inbound turn and target date.

    **已退役，运行时零写入零读取**：这张表只服务过"回复 Agent 的 record_checkin
    工具按来源消息幂等写打卡"那条链路（`b4dffce`），该设计已在 `fa11dc5` 回退——
    打卡抽取交回档案官，后者靠 `archivist_progress` 水位线幂等，不需要来源回执。
    模型与迁移 `20260730_0063` 一并保留：迁移可能已在库上 upgrade（按铁律不动已跑
    迁移），保留映射也避免 autogenerate 生成 DROP。要恢复按来源消息幂等的即时打卡
    写入时可直接复用；在那之前别把它当作在用的表。
    """

    __tablename__ = "checkin_message_receipts"
    __table_args__ = (
        UniqueConstraint(
            "service_case_id",
            "source_message_id",
            "target_date",
            name="ux_checkin_message_receipt_source_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    checkin_id: Mapped[int] = mapped_column(
        ForeignKey("checkins.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel: Mapped[str | None] = mapped_column(String(32))
    checklist_source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
