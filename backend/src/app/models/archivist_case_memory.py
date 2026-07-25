from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistCaseMemory(TimestampMixin, Base):
    """档案官为每个服务单维护的两类自由文本滚动小结。

    与 user_state_profiles（成效事件的结构化投影）分开：这里存的是**记忆产物**——
    LLM 浓缩的用户历程小结，以及只供档案官判断优秀案例的素材小结。两者都由同一轮
    档案官输出并与归档水位线同事务写入。
    """

    __tablename__ = "archivist_case_memory"
    __table_args__ = (
        UniqueConstraint("service_case_id", name="ux_archivist_case_memory_service_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    # 滚动小结：LLM 浓缩的这位用户重要经历/阶段转折，仅在本轮有新沉淀时刷新。
    summary: Mapped[str | None] = mapped_column(Text)
    # 优秀案例素材滚动小结：保留起点、行动、转折、持续结果与尚缺证据，不给回复 Agent。
    case_material_summary: Mapped[str | None] = mapped_column(Text)
    # 两类小结反映到的水位线（最大已纳入消息 id），供下一轮增量更新。
    summary_watermark_message_id: Mapped[int | None] = mapped_column(Integer)
