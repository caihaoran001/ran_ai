from __future__ import annotations

import logging
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.inbound_event import InboundEvent
from app.services.channels.base import ChannelMessage


logger = logging.getLogger("uvicorn.error")

_SPAM_BLOCK_PREFIX = "spam_block_"


def _inbound_event_dedupe_key(
    *,
    event_type: str,
    channel: str,
    message_id: str | None,
) -> str | None:
    message_id = (message_id or "").strip()
    if not message_id:
        return None
    identity = "\x1f".join((event_type, channel, message_id))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def _existing_event_id(dedupe_key: str) -> int | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(InboundEvent.id).where(InboundEvent.dedupe_key == dedupe_key)
        )


async def persist_inbound_event(message: ChannelMessage) -> int | None:
    """把一条入站消息落到持久化收件箱，返回事件 id。

    失败记日志并返回 None；HTTP 回调入口会据此返回 503，聚合器内部调用则保留原有兜底。
    """
    dedupe_key = _inbound_event_dedupe_key(
        event_type=InboundEvent.EVENT_MESSAGE,
        channel=message.channel,
        message_id=message.message_id,
    )
    if dedupe_key:
        existing_id = await _existing_event_id(dedupe_key)
        if existing_id is not None:
            return existing_id
    try:
        async with SessionLocal() as session:
            event = InboundEvent(
                event_type=InboundEvent.EVENT_MESSAGE,
                dedupe_key=dedupe_key,
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
    except IntegrityError:
        if dedupe_key:
            existing_id = await _existing_event_id(dedupe_key)
            if existing_id is not None:
                logger.info(
                    "persist_inbound_event reused event_id=%s chat_id=%s message_id=%s",
                    existing_id,
                    message.chat_id,
                    message.message_id,
                )
                return existing_id
        logger.exception(
            "persist_inbound_event idempotency conflict without existing row chat_id=%s message_id=%s",
            message.chat_id,
            message.message_id,
        )
        return None
    except Exception:
        logger.exception(
            "persist_inbound_event failed chat_id=%s message_id=%s",
            message.chat_id,
            message.message_id,
        )
        return None


def qiwe_spam_block_notice(item: dict[str, Any]) -> tuple[str, str] | None:
    """识别企微风控拦截通知(msgType=1011)，返回(被拦截消息渠道 id, 原因文案)。"""
    if item.get("cmd") != 15000 or item.get("msgType") != 1011:
        return None
    identifier = str(item.get("msgUniqueIdentifier") or "")
    if not identifier.startswith(_SPAM_BLOCK_PREFIX):
        return None
    channel_message_id = identifier[len(_SPAM_BLOCK_PREFIX):].strip()
    if not channel_message_id:
        return None
    msg_data = item.get("msgData") if isinstance(item.get("msgData"), dict) else {}
    raw = msg_data.get("base64RawData") or item.get("base64RawData")
    detail = ""
    if raw:
        try:
            detail = base64.b64decode(raw).decode("utf-8", errors="ignore").strip()
        except Exception:
            detail = ""
    reason = f"企业微信官方风控拦截：{detail}" if detail else "企业微信官方风控拦截：消息未送达（账号功能被限制）"
    return channel_message_id, reason


def _timestamp_from_payload(item: dict[str, Any]) -> datetime:
    try:
        return datetime.fromtimestamp(int(item.get("timestamp")), timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


async def persist_qiwe_spam_block_event(
    *,
    channel_message_id: str,
    reason: str,
    raw_payload: dict[str, Any],
) -> int | None:
    """持久化企微风控通知，避免 background task 因重启丢失后留下假 SENT。"""
    channel_message_id = (channel_message_id or "").strip()
    if not channel_message_id:
        return None
    message_id = str(
        raw_payload.get("msgUniqueIdentifier")
        or f"{_SPAM_BLOCK_PREFIX}{channel_message_id}"
    )
    dedupe_key = _inbound_event_dedupe_key(
        event_type=InboundEvent.EVENT_QIWE_SPAM_BLOCK,
        channel="qiwe",
        message_id=message_id,
    )
    if dedupe_key:
        existing_id = await _existing_event_id(dedupe_key)
        if existing_id is not None:
            return existing_id
    try:
        async with SessionLocal() as session:
            event = InboundEvent(
                event_type=InboundEvent.EVENT_QIWE_SPAM_BLOCK,
                dedupe_key=dedupe_key,
                channel="qiwe",
                chat_id=channel_message_id,
                user_id=None,
                message_id=message_id,
                text=reason or "企业微信官方风控拦截：消息未送达",
                message_type=InboundEvent.EVENT_QIWE_SPAM_BLOCK,
                raw_payload=raw_payload,
                message_timestamp=_timestamp_from_payload(raw_payload),
                status=InboundEvent.STATUS_PENDING,
            )
            session.add(event)
            await session.flush()
            event_id = event.id
            await session.commit()
            return event_id
    except IntegrityError:
        if dedupe_key:
            return await _existing_event_id(dedupe_key)
        return None
    except Exception:
        logger.exception(
            "persist_qiwe_spam_block_event failed channel_message_id=%s",
            channel_message_id,
        )
        return None


async def process_qiwe_spam_block_event(
    event_id: int | None,
    channel_message_id: str,
    reason: str,
) -> bool:
    """执行风控对账；命中出站记录后才把 durable event 标 processed。"""
    from app.services.ranren_ai.engine import mark_outbound_blocked_by_channel_id

    # The notice itself is authoritative evidence that the account is blocked.
    # Disable new automatic replies even when the notice races the outbound row
    # update and cannot be matched on this first attempt.
    await _auto_disable_ai_reply_on_risk(reason)
    handled = await mark_outbound_blocked_by_channel_id(channel_message_id, reason)
    if handled and event_id is not None:
        await mark_inbound_events_processed([event_id])
    return handled


async def _auto_disable_ai_reply_on_risk(reason: str) -> None:
    """企微账号被风控（消息被拦截未送达）→ 自动关闭全局 AI 回复总开关。

    幂等由 disable_ai_reply_due_to_risk 保证（同一次风控期间重复通知不重复改写）。
    仅记日志，绝不因关开关失败而影响风控对账主流程（防御性编程）。
    """
    from app.services.content_policy import disable_ai_reply_due_to_risk

    try:
        async with SessionLocal() as session:
            disabled = await disable_ai_reply_due_to_risk(reason, session)
        if disabled:
            logger.warning(
                "AI reply globally auto-disabled due to QiWe risk-control: %s", reason
            )
    except Exception:
        logger.exception("auto-disable AI reply on QiWe risk-control failed")


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


def channel_message_from_inbound_event(event: InboundEvent) -> ChannelMessage:
    if isinstance(event.raw_payload, dict):
        # Reuse the callback parser so sender name, group/private classification,
        # and future QiWe payload changes stay identical across ingress/replay.
        from app.api.routes.qiwe import _channel_message_from_qiwe

        parsed = _channel_message_from_qiwe(event.raw_payload)
        if parsed is not None:
            parsed.inbound_event_ids = [event.id]
            return parsed
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
        inbound_event_ids=[event.id],
    )


async def replay_pending_spam_block_events(startup_time: datetime) -> int:
    """Process durable 1011 notices before any startup path can send again."""
    min_time = startup_time - timedelta(hours=2)
    async with SessionLocal() as session:
        events = list(
            await session.scalars(
                select(InboundEvent)
                .where(
                    InboundEvent.status == InboundEvent.STATUS_PENDING,
                    InboundEvent.event_type == InboundEvent.EVENT_QIWE_SPAM_BLOCK,
                    InboundEvent.created_at <= startup_time,
                    InboundEvent.created_at >= min_time,
                )
                .order_by(InboundEvent.id.asc())
            )
        )
    processed = 0
    for event in events:
        try:
            spam_block = qiwe_spam_block_notice(event.raw_payload or {})
            if spam_block is None:
                logger.warning(
                    "replay_pending_spam_block_events: malformed event_id=%s",
                    event.id,
                )
                await mark_inbound_events_processed([event.id])
                continue
            channel_message_id, reason = spam_block
            if await process_qiwe_spam_block_event(
                event.id,
                channel_message_id,
                event.text or reason,
            ):
                processed += 1
        except Exception:
            logger.exception(
                "replay_pending_spam_block_events failed event_id=%s",
                event.id,
            )
    if events:
        logger.info(
            "replay_pending_spam_block_events checked=%d processed=%d",
            len(events),
            processed,
        )
    return processed
