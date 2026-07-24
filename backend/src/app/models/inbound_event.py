from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class InboundEvent(TimestampMixin, Base):
    """入站消息的持久化「收件箱」。

    稳定 qiwe-ingress 在返回 2xx 前先写本表；可重启 backend 用状态租约持续消费，
    再进入约 8 秒聚合窗口。事件与业务 Message 在同一事务绑定，之后的中断交给
    Message/LangGraph 恢复，不会把 transport event 当成一条新消息重复回复。

    新事件用 dedupe_key 抵御重复回调；聚合器的内存 TTL 去重只作同进程快速保护。
    """

    __tablename__ = "inbound_events"

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_PROCESSED = "processed"
    STATUS_EXPIRED = "expired"
    EVENT_MESSAGE = "message"
    EVENT_QIWE_SPAM_BLOCK = "qiwe_spam_block"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), default=EVENT_MESSAGE, nullable=False, index=True)
    # 新写入事件的稳定幂等键。存量记录保持 NULL，避免上线迁移被历史重复回调阻塞。
    dedupe_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
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
    # Once this is set, the event has crossed the durable business boundary.
    # Any later interruption is recovered from Message/LangGraph checkpoints,
    # never by replaying the transport event as a new user turn.
    business_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"),
        index=True,
    )
