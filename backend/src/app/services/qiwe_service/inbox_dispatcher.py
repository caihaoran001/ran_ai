from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import case, func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.inbound_event import InboundEvent
from app.services.qiwe_service.aggregator import QiWeMessageAggregator
from app.services.qiwe_service.inbox import (
    channel_message_from_inbound_event,
    mark_inbound_events_processed,
    process_qiwe_spam_block_event,
    qiwe_spam_block_notice,
)
from app.services.qiwe_service.voice import transcribe_and_dispatch


logger = logging.getLogger("uvicorn.error")


class QiWeInboxDispatcher:
    """Continuously hand durable QiWe callbacks to the business engine.

    The database status transition is the cross-process lease. Only PENDING
    rows are claimable, and the engine atomically links claimed rows to the
    durable inbound Message before it starts the reply graph.
    """

    def __init__(
        self,
        aggregator: QiWeMessageAggregator,
        *,
        poll_interval_seconds: float = 1.0,
        batch_size: int = 100,
        max_message_age_seconds: float = 300.0,
        max_spam_event_age_seconds: float = 172800.0,
        stale_claim_seconds: float = 900.0,
    ) -> None:
        self.aggregator = aggregator
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.max_message_age_seconds = max_message_age_seconds
        self.max_spam_event_age_seconds = max_spam_event_age_seconds
        self.stale_claim_seconds = stale_claim_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._next_stale_recycle_at = datetime.min.replace(tzinfo=timezone.utc)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        await self._reset_interrupted_claims()
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="qiwe-inbox-dispatcher",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if not self._task:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def wait_until_handed_off(
        self,
        *,
        before: datetime,
        timeout_seconds: float = 30.0,
    ) -> bool:
        """Wait until pre-start message events crossed into durable Messages."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            async with SessionLocal() as session:
                remaining = await session.scalar(
                    select(func.count(InboundEvent.id)).where(
                        InboundEvent.event_type == InboundEvent.EVENT_MESSAGE,
                        InboundEvent.created_at <= before,
                        InboundEvent.status.in_(
                            (
                                InboundEvent.STATUS_PENDING,
                                InboundEvent.STATUS_PROCESSING,
                            )
                        ),
                        InboundEvent.business_message_id.is_(None),
                    )
                )
            if not remaining:
                return True
            await asyncio.sleep(min(self.poll_interval_seconds, 0.5))
        logger.warning(
            "QiWe inbox startup handoff timed out before=%s timeout_seconds=%.1f",
            before.isoformat(),
            timeout_seconds,
        )
        return False

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                if now >= self._next_stale_recycle_at:
                    await self._recycle_stale_claims(now)
                    self._next_stale_recycle_at = now + timedelta(seconds=60)
                events = await self._claim_pending(now)
                if events:
                    for event in events:
                        if self._stop_event.is_set():
                            break
                        await self._dispatch(event)
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("QiWe inbox dispatcher loop failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

    async def _claim_pending(self, now: datetime) -> list[InboundEvent]:
        claimed: list[InboundEvent] = []
        async with SessionLocal() as session:
            rows = list(
                await session.scalars(
                    select(InboundEvent)
                    .where(InboundEvent.status == InboundEvent.STATUS_PENDING)
                    .order_by(
                        case(
                            (
                                InboundEvent.event_type
                                == InboundEvent.EVENT_QIWE_SPAM_BLOCK,
                                0,
                            ),
                            else_=1,
                        ),
                        InboundEvent.id.asc(),
                    )
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            expiry = now - timedelta(seconds=self.max_message_age_seconds)
            spam_expiry = now - timedelta(
                seconds=self.max_spam_event_age_seconds
            )
            for row in rows:
                if row.business_message_id is not None:
                    row.status = InboundEvent.STATUS_PROCESSED
                    row.processed_at = row.processed_at or now
                    continue
                if (
                    row.event_type == InboundEvent.EVENT_MESSAGE
                    and row.created_at < expiry
                ):
                    row.status = InboundEvent.STATUS_EXPIRED
                    row.processed_at = now
                    logger.warning(
                        "QiWe inbox expired without auto-reply event_id=%s chat_id=%s age_seconds=%.1f",
                        row.id,
                        row.chat_id,
                        (now - row.created_at).total_seconds(),
                    )
                    continue
                if (
                    row.event_type == InboundEvent.EVENT_QIWE_SPAM_BLOCK
                    and row.created_at < spam_expiry
                ):
                    row.status = InboundEvent.STATUS_EXPIRED
                    row.processed_at = now
                    logger.warning(
                        "QiWe inbox expired stale spam-block event_id=%s age_seconds=%.1f",
                        row.id,
                        (now - row.created_at).total_seconds(),
                    )
                    continue
                row.status = InboundEvent.STATUS_PROCESSING
                claimed.append(row)
            await session.commit()
        return claimed

    async def _dispatch(self, event: InboundEvent) -> None:
        try:
            if event.event_type == InboundEvent.EVENT_QIWE_SPAM_BLOCK:
                await self._dispatch_spam_block(event)
                return

            settings = get_settings()
            if (
                settings.curated_video_enabled
                and event.chat_id == settings.curated_video_source_group_id.strip()
            ):
                # 视频选品源群只静默收走视频号卡片(msgType=141)作为选品素材：
                # 不落客服消息、不触发回复，由分发 worker 独立从收件箱采集。
                # 其余普通消息（文本/图片/语音等）仍走正常客服链路（落库 + 回复），
                # 选品源群与服务群可以共存于同一个群。
                raw = event.raw_payload if isinstance(event.raw_payload, dict) else {}
                if raw.get("cmd") == 15000 and raw.get("msgType") == 141:
                    await mark_inbound_events_processed([event.id])
                    return

            message = channel_message_from_inbound_event(event)
            # 语音先转文字再判定路由：转成功后它就是一条普通文本消息，应当进聚合窗口
            # 参与正常回合，而不是按非文本消息绕过窗口只留痕。
            await transcribe_and_dispatch(
                self.aggregator,
                message,
                inbound_event_id=event.id,
            )
        except Exception:
            logger.exception(
                "QiWe inbox dispatch failed event_id=%s event_type=%s",
                event.id,
                event.event_type,
            )
            await self._release_claim(event.id)

    async def _dispatch_spam_block(self, event: InboundEvent) -> None:
        notice = qiwe_spam_block_notice(event.raw_payload or {})
        if notice is None:
            logger.error(
                "QiWe inbox malformed spam-block event_id=%s",
                event.id,
            )
            await mark_inbound_events_processed([event.id])
            return
        channel_message_id, reason = notice
        handled = await process_qiwe_spam_block_event(
            event.id,
            channel_message_id,
            event.text or reason,
        )
        if not handled:
            # The 1011 callback can race the transaction that stores the
            # outbound channel id. Keep PROCESSING and let the stale-claim
            # recycler retry after 15 minutes instead of hammering RDS each
            # second. A backend restart also safely releases the old claim.
            logger.warning(
                "QiWe inbox spam-block unmatched; deferred retry event_id=%s",
                event.id,
            )

    async def _release_claim(self, event_id: int) -> None:
        async with SessionLocal() as session:
            event = await session.get(InboundEvent, event_id, with_for_update=True)
            if (
                event is not None
                and event.status == InboundEvent.STATUS_PROCESSING
                and event.business_message_id is None
            ):
                event.status = InboundEvent.STATUS_PENDING
                await session.commit()

    async def _reset_interrupted_claims(self) -> None:
        """Single backend instance: startup owns all unfinished old claims."""
        async with SessionLocal() as session:
            rows = list(
                await session.scalars(
                    select(InboundEvent)
                    .where(
                        InboundEvent.status == InboundEvent.STATUS_PROCESSING,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            now = datetime.now(timezone.utc)
            for row in rows:
                if row.business_message_id is not None:
                    row.status = InboundEvent.STATUS_PROCESSED
                    row.processed_at = row.processed_at or now
                else:
                    row.status = InboundEvent.STATUS_PENDING
            await session.commit()
        if rows:
            logger.info(
                "QiWe inbox reset interrupted claims count=%d",
                len(rows),
            )

    async def _recycle_stale_claims(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.stale_claim_seconds)
        async with SessionLocal() as session:
            rows = list(
                await session.scalars(
                    select(InboundEvent)
                    .where(
                        InboundEvent.status == InboundEvent.STATUS_PROCESSING,
                        InboundEvent.business_message_id.is_(None),
                        InboundEvent.updated_at <= cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = InboundEvent.STATUS_PENDING
            await session.commit()
        if rows:
            logger.warning(
                "QiWe inbox recycled stale claims count=%d",
                len(rows),
            )
