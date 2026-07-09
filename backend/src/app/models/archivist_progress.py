from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistProgress(TimestampMixin, Base):
    """档案官（Archivist）逐服务单的归档进度/水位线。

    独立进度表而非给宽表 service_cases 加列：将来可能有多个后台 agent 各自持有水位，
    独立表更干净、可扩展。触发判定完全从 DB 派生（本表 + messages），不依赖内存 timer，
    因此进程重启后下一轮扫描仍能正确恢复。
    """

    __tablename__ = "archivist_progress"
    __table_args__ = (
        UniqueConstraint("service_case_id", name="ux_archivist_progress_service_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=False,
        index=True,
    )
    # 水位线：已归档到的最大 message.id；只处理其后的新消息为增量。
    last_archived_message_id: Mapped[int | None] = mapped_column(Integer)
    # 上次成功归档的时间；用于触发条件 B（距上次归档满 24h）。
    last_archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 认领时间：扫描器用 SELECT ... FOR UPDATE SKIP LOCKED 认领本行防双跑，
    # 跑完清空。残留的 claimed_at 由超时兜底回收。
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
