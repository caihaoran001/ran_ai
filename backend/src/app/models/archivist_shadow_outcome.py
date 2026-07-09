from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistShadowOutcome(TimestampMixin, Base):
    """档案官成效/画像收编的**影子对账**记录（P0.5，只观察不切换）。

    影子期档案官在后台批量抽一次成效/画像，但**不写** outcome_events/user_state；
    把它抽到的结果与热路径（engine._run_outcome_analysis 逐条写入）已落的 outcome_events
    做事件级 diff，落这张审计表，供 SQL 统计一致率。达标后下一轮才做真正切换。

    追加型审计表：不软删除（不承载业务事实，只留观察证据）。
    """

    __tablename__ = "archivist_shadow_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 本次对账覆盖的水位线区间（(watermark_from, watermark_to] 内的用户消息）。
    watermark_from: Mapped[int | None] = mapped_column(Integer)
    watermark_to: Mapped[int | None] = mapped_column(Integer)
    # 档案官批量抽出的成效事件（[{field_key,event_type,value,evidence_text,confidence}]）。
    shadow_events: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # 档案官抽出的画像信号（[{domain,signal_type,value,evidence_text,confidence}]）。
    shadow_signals: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # 同一批 message_id 下热路径已写 outcome_events 的快照（对账基准）。
    hot_events: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # 对账结果：{counts:{matched,hot_only,shadow_only}, matched:[], hot_only:[], shadow_only:[]}。
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSON)
