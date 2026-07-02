from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class InboundEvent(TimestampMixin, Base):
    """入站消息的持久化「收件箱」。

    QiWe 群消息进聚合器时会先在内存缓冲 ~8s 再合并处理，这段窗口内若进程被重启，
    尚未落库的消息就会丢失（现有 recover_stuck 只能捞已入库的 Message）。本表在消息
    进窗口时就先落一条记录，开机 replay 扫描未处理(pending)的事件补处理，堵住这个黑洞。

    注意：去重仍由聚合器内存层负责(带 TTL)，本表不做唯一约束，只作持久化日志与重放来源。
    """

    __tablename__ = "inbound_events"

    STATUS_PENDING = "pending"
    STATUS_PROCESSED = "processed"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(128))
    message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    raw_payload: Mapped[dict | list | None] = mapped_column(JSON)
    # 消息自身携带的时间戳（非入库时间）；用于重放时判定是否已被正常处理。
    message_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
