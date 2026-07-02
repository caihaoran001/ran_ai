from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.inbound_event import InboundEvent
from app.models.message import Message, MessageDirection
from app.services.channels.base import ChannelMessage, MessageChannel


logger = logging.getLogger("uvicorn.error")


async def persist_inbound_event(message: ChannelMessage) -> int | None:
    """把一条入站消息落到持久化收件箱，返回事件 id。

    失败只记日志、返回 None——durability 层绝不阻断实时消息处理（防御性编程）。
    """
    try:
        async with SessionLocal() as session:
            event = InboundEvent(
                channel=message.channel,
                chat_id=message.chat_id,
                user_id=message.user_id,
                message_id=message.message_id,
                text=message.text or "",
                message_type=message.message_type or "text",
                raw_payload=message.raw_payload,
                message_timestamp=message.timestamp,
                status=InboundEvent.STATUS_PENDING,
            )
            session.add(event)
            await session.flush()
            event_id = event.id
            await session.commit()
            return event_id
    except Exception:
        logger.exception(
            "persist_inbound_event failed chat_id=%s message_id=%s",
            message.chat_id,
            message.message_id,
        )
        return None


async def mark_inbound_events_processed(event_ids: list[int]) -> None:
    ids = [e for e in event_ids if e]
    if not ids:
        return
    try:
        async with SessionLocal() as session:
            rows = list(await session.scalars(select(InboundEvent).where(InboundEvent.id.in_(ids))))
            now = datetime.now(timezone.utc)
            for row in rows:
                if row.status != InboundEvent.STATUS_PROCESSED:
                    row.status = InboundEvent.STATUS_PROCESSED
                    row.processed_at = now
            await session.commit()
    except Exception:
        logger.exception("mark_inbound_events_processed failed ids=%s", ids)


def _channel_message_from_event(event: InboundEvent) -> ChannelMessage:
    return ChannelMessage(
        channel=event.channel,
        chat_id=event.chat_id,
        chat_type="group",
        text=event.text,
        message_id=event.message_id,
        user_id=event.user_id,
        message_type=event.message_type or "text",
        raw_payload=event.raw_payload,
        timestamp=event.message_timestamp or event.created_at,
    )


async def replay_pending_inbound_events(channel: MessageChannel, startup_time: datetime) -> None:
    """开机重放：补处理重启前卡在聚合窗口、从未进入引擎的入站消息。

    保守判定(宁可少发)：若该会话在此消息时间点之后、且在本次重启之前已存在任意入站 Message，
    说明它(或其所在聚合批)已被引擎正常处理过，只标 processed、不再重放，把可能缺失的回复交给
    recover_stuck；否则视为窗口内真丢失，重建 ChannelMessage 交引擎补处理。

    「重启之前」这个上界很关键：它让重放自己写入的 Message 不会把同批的其他事件误判成已处理。
    """
    from app.services.ranren_ai.engine import RanrenAIEngine  # 延迟导入避免循环依赖

    cutoff = startup_time - timedelta(seconds=15)
    min_time = startup_time - timedelta(hours=2)
    async with SessionLocal() as session:
        events = list(
            await session.scalars(
                select(InboundEvent)
                .where(
                    InboundEvent.status == InboundEvent.STATUS_PENDING,
                    InboundEvent.created_at <= cutoff,
                    InboundEvent.created_at >= min_time,
                )
                .order_by(InboundEvent.id.asc())
            )
        )
    if not events:
        return

    logger.info(
        "replay_pending_inbound_events: found %d pending inbound events to check: %s",
        len(events),
        [e.id for e in events],
    )

    engine = RanrenAIEngine(channel)
    for event in events:
        try:
            ref_time = event.message_timestamp or event.created_at
            async with SessionLocal() as session:
                already = await session.scalar(
                    select(Message.id)
                    .where(
                        Message.group_id == event.chat_id,
                        Message.direction == MessageDirection.INBOUND,
                        Message.created_at >= ref_time - timedelta(seconds=30),
                        Message.created_at <= startup_time,
                    )
                    .limit(1)
                )
            if already:
                await mark_inbound_events_processed([event.id])
                continue
            await engine.handle_message(_channel_message_from_event(event))
            await mark_inbound_events_processed([event.id])
            logger.info(
                "replay_pending_inbound_events reprocessed event_id=%s chat_id=%s message_id=%s",
                event.id,
                event.chat_id,
                event.message_id,
            )
        except Exception:
            # 不标记 processed，留待下次开机重试
            logger.exception("replay_pending_inbound_events failed event_id=%s", event.id)
