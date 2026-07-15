from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistCaseMemory(TimestampMixin, Base):
    """档案官为每个服务单维护的自由文本滚动小结。

    与 user_state_profiles（成效事件的结构化投影）分开：这里存的是**记忆产物**——
    LLM 浓缩的滚动小结(重要经历/阶段转折),供 Chat Agent 了解背景。它可随源数据
    重新生成,非权威事实源,因此归档主流程对它的刷新是尽力而为(失败不影响归档水位线)。
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
    # summary 反映到的水位线（最大已纳入消息 id），供将来增量摘要参考。
    summary_watermark_message_id: Mapped[int | None] = mapped_column(Integer)
