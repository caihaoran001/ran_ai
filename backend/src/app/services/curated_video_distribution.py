from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.curated_video import (
    CuratedVideoDailyRun,
    CuratedVideoDelivery,
    CuratedVideoItem,
    CuratedVideoSourceCursor,
)
from app.models.inbound_event import InboundEvent
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.channels.base import ChannelSendResult, MessageChannel
from app.services.channels.send_context import bind_send_request_started
from app.services.content_policy import get_ai_reply_enabled
from app.services.curated_video_settings import get_curated_video_push_settings


logger = logging.getLogger(__name__)
_FEED_FIELDS = (
    "channelName",
    "channelUrl",
    "coverUrl",
    "encodeData",
    "headImgUrl",
    "feedId",
    "feedNo",
    "username",
)
_ACTIVE_DELIVERY_STATUSES = ("pending", "retry", "queued", "sending")
_MAX_DELIVERY_ATTEMPTS = 3
_RETRY_DELAY = timedelta(seconds=10)
_QUEUED_LEASE = timedelta(minutes=2)
_SENT_RECONCILIATION_LOOKBACK = timedelta(days=2)


class _StopDistribution(RuntimeError):
    """The account-wide send guard closed while a batch was queued."""


class _SkipDelivery(RuntimeError):
    """The target stopped being eligible while waiting for the send lock."""


def _schedule_time(value: str) -> time:
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        return time(int(hour_text), int(minute_text))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("CURATED_VIDEO_SEND_AT must use HH:MM") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _feed_expiry(payload: dict[str, Any]) -> datetime | None:
    cover_url = str(payload.get("coverUrl") or "").strip()
    if not cover_url:
        return None
    try:
        raw = parse_qs(urlparse(cover_url).query).get("finder_expire_time", [None])[0]
        return datetime.fromtimestamp(int(raw), timezone.utc) if raw else None
    except (TypeError, ValueError, OSError):
        return None


def _feed_payload(event: InboundEvent) -> dict[str, Any] | None:
    raw = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    if raw.get("cmd") != 15000 or raw.get("msgType") != 141:
        return None
    data = raw.get("msgData") if isinstance(raw.get("msgData"), dict) else {}
    if any(not data.get(key) for key in _FEED_FIELDS):
        logger.warning(
            "Curated video ignored incomplete payload event_id=%s missing=%s",
            event.id,
            [key for key in _FEED_FIELDS if not data.get(key)],
        )
        return None
    return {key: data[key] for key in _FEED_FIELDS}


async def ingest_curated_video_events(settings: Settings | None = None) -> int:
    """Move new source-group msgType=141 events into the durable FIFO.

    The first run establishes an activation cursor at the current inbox tail,
    deliberately avoiding an accidental historical blast. Later restarts reuse
    that cursor, so events accepted while the business backend was down remain
    discoverable even if the normal reply inbox expires them.
    """

    settings = settings or get_settings()
    source_group_id = settings.curated_video_source_group_id.strip()
    if not settings.curated_video_enabled or not source_group_id:
        return 0

    async with SessionLocal() as session:
        cursor = await session.scalar(
            select(CuratedVideoSourceCursor)
            .where(CuratedVideoSourceCursor.source_group_id == source_group_id)
            .with_for_update()
        )
        high_watermark = int(await session.scalar(select(func.max(InboundEvent.id))) or 0)
        if cursor is None:
            session.add(
                CuratedVideoSourceCursor(
                    source_group_id=source_group_id,
                    last_inbound_event_id=high_watermark,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            logger.info(
                "Curated video source activated group_id=%s after_event_id=%s",
                source_group_id,
                high_watermark,
            )
            return 0

        if high_watermark <= cursor.last_inbound_event_id:
            return 0
        events = list(
            await session.scalars(
                select(InboundEvent)
                .where(
                    InboundEvent.id > cursor.last_inbound_event_id,
                    InboundEvent.id <= high_watermark,
                    InboundEvent.channel == "qiwe",
                    InboundEvent.chat_id == source_group_id,
                )
                .order_by(InboundEvent.id.asc())
            )
        )
        inserted = 0
        for event in events:
            feed = _feed_payload(event)
            source_message_id = str(event.message_id or "").strip()
            if feed is None or not source_message_id:
                continue
            session.add(
                CuratedVideoItem(
                    source_group_id=source_group_id,
                    source_inbound_event_id=event.id,
                    source_message_id=source_message_id,
                    title=str(feed["channelName"]).strip(),
                    feed_payload=feed,
                    received_at=event.message_timestamp or event.created_at,
                    expires_at=_feed_expiry(feed),
                    status="queued",
                )
            )
            inserted += 1
        cursor.last_inbound_event_id = high_watermark
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent worker may have inserted the same source event. Its
            # unique key is authoritative; the next pass advances this cursor.
            await session.rollback()
            return 0
        if inserted:
            logger.info(
                "Curated video queued count=%s group_id=%s through_event_id=%s",
                inserted,
                source_group_id,
                high_watermark,
            )
        return inserted


class CuratedVideoDistributionService:
    def __init__(
        self,
        channel: MessageChannel,
        settings: Settings | None = None,
        *,
        poll_interval_seconds: float = 10.0,
    ) -> None:
        self.channel = channel
        self.settings = settings or get_settings()
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if not self.settings.curated_video_enabled:
            return
        if not self.settings.curated_video_source_group_id.strip():
            logger.warning("Curated video distribution disabled: source group is empty")
            return
        _schedule_time(self.settings.curated_video_send_at)
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="curated-video-distribution")
        logger.info(
            "Curated video distribution started source_group_id=%s send_at=%s",
            self.settings.curated_video_source_group_id,
            self.settings.curated_video_send_at,
        )

    async def stop(self) -> None:
        self._stopped.set()
        if not self._task:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Curated video distribution check failed")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def check_once(self, now: datetime | None = None) -> None:
        now = _utc(now or datetime.now(timezone.utc))
        await ingest_curated_video_events(self.settings)
        await self._sync_delivery_states(now)

        async with SessionLocal() as session:
            push_config = await get_curated_video_push_settings(session)
        if not push_config["enabled"]:
            logger.info("Curated video push skipped: switch is off")
            return

        local_now = now.astimezone(ZoneInfo(self.settings.reminder_timezone))
        if local_now.time().replace(tzinfo=None) >= _schedule_time(push_config["send_at"]):
            await self._ensure_daily_run(local_now.date(), now, push_config["scope"])

        run_ids = list(
            await self._load_distributing_run_ids()
        )
        for run_id in run_ids:
            try:
                await self._process_run(run_id, now)
            except _StopDistribution:
                logger.warning("Curated video distribution paused by account send guard")
                break

    async def _ensure_daily_run(self, run_date, now: datetime, scope: str = "all") -> int | None:
        async with SessionLocal() as session:
            existing = await session.scalar(
                select(CuratedVideoDailyRun).where(CuratedVideoDailyRun.run_date == run_date)
            )
            if existing is not None:
                return existing.id

            base = select(ServiceCase).where(
                ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                ServiceCase.deleted_at.is_(None),
                # 客户已经退群的服务单不推视频：群位在等下一位客户，别刷屏。
                ServiceCase.member_left_at.is_(None),
                ServiceCase.user_id.is_not(None),
                ServiceCase.group_id != "",
                ServiceCase.group_id != self.settings.curated_video_source_group_id.strip(),
            )
            if scope in ("active_24h", "active_7d"):
                hours = 24 if scope == "active_24h" else 24 * 7
                active_subq = (
                    select(Message.service_case_id)
                    .where(
                        Message.direction == MessageDirection.INBOUND,
                        Message.sender_type == MessageSenderType.USER,
                        Message.created_at >= now - timedelta(hours=hours),
                    )
                    .distinct()
                    .subquery()
                )
                base = base.join(active_subq, active_subq.c.service_case_id == ServiceCase.id)

            cases = list(
                await session.scalars(base.order_by(ServiceCase.id.asc()))
            )
            targets: dict[str, ServiceCase] = {}
            for service_case in cases:
                targets.setdefault(service_case.group_id, service_case)

            item: CuratedVideoItem | None = None
            if targets:
                candidates = list(
                    await session.scalars(
                        select(CuratedVideoItem)
                        .where(
                            CuratedVideoItem.source_group_id
                            == self.settings.curated_video_source_group_id.strip(),
                            CuratedVideoItem.status == "queued",
                            ~exists(
                                select(CuratedVideoDailyRun.id).where(
                                    CuratedVideoDailyRun.item_id == CuratedVideoItem.id
                                )
                            ),
                        )
                        .order_by(CuratedVideoItem.received_at.asc(), CuratedVideoItem.id.asc())
                        .with_for_update(skip_locked=True)
                    )
                )
                for candidate in candidates:
                    if candidate.expires_at is not None and _utc(candidate.expires_at) <= now:
                        candidate.status = "expired"
                        continue
                    item = candidate
                    break

            if not targets:
                status = "no_targets"
            elif item is None:
                status = "no_content"
            else:
                status = "distributing"
                item.status = "assigned"
            run = CuratedVideoDailyRun(
                run_date=run_date,
                item_id=item.id if item else None,
                status=status,
                target_count=len(targets),
                sent_count=0,
                failed_count=0,
                started_at=now,
                completed_at=now if item is None else None,
            )
            session.add(run)
            try:
                await session.flush()
                if item is not None:
                    for group_id, service_case in targets.items():
                        session.add(
                            CuratedVideoDelivery(
                                daily_run_id=run.id,
                                service_case_id=service_case.id,
                                group_id=group_id,
                                status="pending",
                                attempt_count=0,
                            )
                        )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return await session.scalar(
                    select(CuratedVideoDailyRun.id).where(
                        CuratedVideoDailyRun.run_date == run_date
                    )
                )
            logger.info(
                "Curated video daily run reserved date=%s status=%s item_id=%s targets=%s",
                run_date,
                status,
                item.id if item else None,
                len(targets) if item else 0,
            )
            return run.id

    async def _load_distributing_run_ids(self) -> list[int]:
        async with SessionLocal() as session:
            return list(
                await session.scalars(
                    select(CuratedVideoDailyRun.id)
                    .where(CuratedVideoDailyRun.status == "distributing")
                    .order_by(CuratedVideoDailyRun.run_date.asc())
                )
            )

    async def _process_run(self, run_id: int, now: datetime) -> None:
        async with SessionLocal() as session:
            run = await session.get(CuratedVideoDailyRun, run_id)
            item = await session.get(CuratedVideoItem, run.item_id) if run and run.item_id else None
            if run is None or item is None:
                return
            deliveries = list(
                await session.scalars(
                    select(CuratedVideoDelivery)
                    .where(
                        CuratedVideoDelivery.daily_run_id == run_id,
                        CuratedVideoDelivery.status.in_(("pending", "retry")),
                        CuratedVideoDelivery.attempt_count < _MAX_DELIVERY_ATTEMPTS,
                        or_(
                            CuratedVideoDelivery.next_attempt_at.is_(None),
                            CuratedVideoDelivery.next_attempt_at <= now,
                        ),
                    )
                    .order_by(CuratedVideoDelivery.id.asc())
                )
            )
        for delivery in deliveries:
            outcome = await self._attempt_delivery(delivery.id, item)
            if outcome == "paused":
                raise _StopDistribution()
        await self._refresh_run(run_id)

    async def _attempt_delivery(self, delivery_id: int, item: CuratedVideoItem) -> str:
        async with SessionLocal() as session:
            delivery = await session.get(CuratedVideoDelivery, delivery_id, with_for_update=True)
            if delivery is None or delivery.status not in ("pending", "retry"):
                return "skipped"
            service_case = await session.get(ServiceCase, delivery.service_case_id)
            if (
                service_case is None
                or service_case.deleted_at is not None
                or service_case.status != ServiceCaseStatus.IN_SERVICE
                or service_case.group_id != delivery.group_id
            ):
                delivery.status = "skipped"
                delivery.last_error = "service case is no longer active in this group"
                await session.commit()
                return "skipped"

            outbound = None
            if delivery.outbound_message_id is not None:
                candidate = await session.get(Message, delivery.outbound_message_id)
                if candidate is not None and candidate.status == MessageStatus.PENDING:
                    outbound = candidate
            if outbound is None:
                outbound = Message(
                    service_case_id=delivery.service_case_id,
                    group_id=delivery.group_id,
                    direction=MessageDirection.OUTBOUND,
                    sender_type=MessageSenderType.BOT,
                    message_type="video_channel",
                    content=item.title,
                    purpose=MessagePurpose.REMINDER,
                    status=MessageStatus.PENDING,
                    analysis_json={
                        "_system_event": "curated_video_delivery",
                        "curated_video": {
                            "item_id": item.id,
                            "source_message_id": item.source_message_id,
                            "feed_id": item.feed_payload.get("feedId"),
                        },
                        "_delivery": {},
                    },
                )
                session.add(outbound)
                await session.flush()
                delivery.outbound_message_id = outbound.id
                delivery.status = "pending"
            outbound_id = outbound.id
            group_id = delivery.group_id
            # Claim before joining the shared QiWe send lock. A second backend
            # instance then skips this delivery instead of enqueueing it twice.
            delivery.status = "queued"
            await session.commit()

        request_started = False

        async def mark_request_started() -> None:
            nonlocal request_started
            async with SessionLocal() as session:
                delivery = await session.get(CuratedVideoDelivery, delivery_id, with_for_update=True)
                outbound = await session.get(Message, outbound_id, with_for_update=True)
                service_case = await session.get(ServiceCase, delivery.service_case_id) if delivery else None
                if delivery is None or outbound is None:
                    raise RuntimeError("curated video delivery disappeared before send")
                if not await get_ai_reply_enabled(session):
                    raise _StopDistribution("account send guard is disabled")
                if (
                    service_case is None
                    or service_case.deleted_at is not None
                    or service_case.status != ServiceCaseStatus.IN_SERVICE
                    or service_case.group_id != delivery.group_id
                ):
                    raise _SkipDelivery("service case became inactive before channel request")
                if outbound.status != MessageStatus.PENDING:
                    raise RuntimeError(f"outbound message cannot start from {outbound.status.value}")
                started_at = datetime.now(timezone.utc)
                analysis = dict(outbound.analysis_json or {})
                metadata = dict(analysis.get("_delivery") or {})
                metadata["request_started_at"] = started_at.isoformat()
                analysis["_delivery"] = metadata
                outbound.analysis_json = analysis
                outbound.status = MessageStatus.SENDING
                outbound.error = None
                delivery.status = "sending"
                delivery.attempt_count += 1
                delivery.next_attempt_at = None
                await session.commit()
                request_started = True

        try:
            with bind_send_request_started(mark_request_started):
                result = await self.channel.send_feed_video(group_id, item.feed_payload)
        except _StopDistribution as exc:
            await self._finalize_attempt(
                delivery_id,
                outbound_id,
                status="retry",
                message_status=MessageStatus.FAILED,
                error=str(exc),
                retry_after=timedelta(minutes=5),
            )
            return "paused"
        except _SkipDelivery as exc:
            await self._finalize_attempt(
                delivery_id,
                outbound_id,
                status="skipped",
                message_status=MessageStatus.FAILED,
                error=str(exc),
            )
            return "skipped"
        except Exception as exc:
            uncertain = request_started
            await self._finalize_attempt(
                delivery_id,
                outbound_id,
                status="unknown" if uncertain else "retry",
                message_status=MessageStatus.UNKNOWN if uncertain else MessageStatus.FAILED,
                error=f"send raised: {type(exc).__name__}: {exc}",
                retry_after=None if uncertain else _RETRY_DELAY,
                delivery_uncertain=uncertain,
                increment_attempt=not request_started,
            )
            return "unknown" if uncertain else "retry"

        if result is None:
            uncertain = request_started
            await self._finalize_attempt(
                delivery_id,
                outbound_id,
                status="unknown" if uncertain else "retry",
                message_status=MessageStatus.UNKNOWN if uncertain else MessageStatus.FAILED,
                error="channel returned no delivery result",
                retry_after=None if uncertain else _RETRY_DELAY,
                delivery_uncertain=uncertain,
                increment_attempt=not request_started,
            )
            return "unknown" if uncertain else "retry"
        if result.success:
            await self._finalize_attempt(
                delivery_id,
                outbound_id,
                status="sent",
                message_status=MessageStatus.SENT,
                result=result,
            )
            return "sent"
        if result.delivery_uncertain:
            await self._finalize_attempt(
                delivery_id,
                outbound_id,
                status="unknown",
                message_status=MessageStatus.UNKNOWN,
                error=result.error or "delivery result is uncertain",
                result=result,
                delivery_uncertain=True,
            )
            return "unknown"
        await self._finalize_attempt(
            delivery_id,
            outbound_id,
            status="retry",
            message_status=MessageStatus.FAILED,
            error=result.error or "channel send failed",
            result=result,
            retry_after=_RETRY_DELAY,
            increment_attempt=not request_started,
        )
        return "retry"

    async def _finalize_attempt(
        self,
        delivery_id: int,
        outbound_id: int,
        *,
        status: str,
        message_status: MessageStatus,
        error: str | None = None,
        result: ChannelSendResult | None = None,
        retry_after: timedelta | None = None,
        delivery_uncertain: bool = False,
        increment_attempt: bool = False,
    ) -> None:
        async with SessionLocal() as session:
            delivery = await session.get(CuratedVideoDelivery, delivery_id, with_for_update=True)
            outbound = await session.get(Message, outbound_id, with_for_update=True)
            if delivery is None or outbound is None:
                return
            now = datetime.now(timezone.utc)
            if increment_attempt:
                delivery.attempt_count += 1
            if status == "retry" and delivery.attempt_count >= _MAX_DELIVERY_ATTEMPTS:
                status = "failed"
                retry_after = None
            delivery.status = status
            delivery.last_error = None if status == "sent" else error
            delivery.next_attempt_at = now + retry_after if retry_after else None
            delivery.channel_message_id = result.message_id if result else None
            delivery.sent_at = now if status == "sent" else None

            analysis = dict(outbound.analysis_json or {})
            metadata = dict(analysis.get("_delivery") or {})
            metadata["completed_at"] = now.isoformat()
            metadata["delivery_uncertain"] = delivery_uncertain
            raw = result.raw_response if result and isinstance(result.raw_response, dict) else {}
            raw_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            receipt = {
                key: raw_data[key]
                for key in ("msgUniqueIdentifier", "msgServerId", "seq", "timestamp")
                if raw_data.get(key) is not None
            }
            if receipt:
                metadata["provider_receipt"] = receipt
            analysis["_delivery"] = metadata
            outbound.analysis_json = analysis
            outbound.status = message_status
            outbound.wecom_message_id = result.message_id if result else None
            outbound.error = None if message_status == MessageStatus.SENT else error
            outbound.sent_at = now if message_status == MessageStatus.SENT else None
            await session.commit()

    async def _sync_delivery_states(self, now: datetime) -> None:
        affected_runs: set[int] = set()
        recent_sent_cutoff = now - _SENT_RECONCILIATION_LOOKBACK
        async with SessionLocal() as session:
            deliveries = list(
                await session.scalars(
                    select(CuratedVideoDelivery)
                    .where(
                        CuratedVideoDelivery.outbound_message_id.is_not(None),
                        or_(
                            CuratedVideoDelivery.status.in_(
                                (*_ACTIVE_DELIVERY_STATUSES, "unknown")
                            ),
                            and_(
                                CuratedVideoDelivery.status == "sent",
                                CuratedVideoDelivery.sent_at >= recent_sent_cutoff,
                            ),
                        ),
                    )
                    .order_by(CuratedVideoDelivery.id.asc())
                )
            )
            for delivery in deliveries:
                outbound = await session.get(Message, delivery.outbound_message_id)
                if outbound is None:
                    continue
                new_status = delivery.status
                if outbound.status == MessageStatus.SENT:
                    new_status = "sent"
                    delivery.channel_message_id = outbound.wecom_message_id
                    delivery.sent_at = outbound.sent_at or now
                    delivery.last_error = None
                    delivery.next_attempt_at = None
                elif outbound.status == MessageStatus.UNKNOWN:
                    new_status = "unknown"
                    delivery.last_error = outbound.error
                    delivery.next_attempt_at = None
                elif outbound.status == MessageStatus.FAILED:
                    new_status = "retry" if delivery.attempt_count < _MAX_DELIVERY_ATTEMPTS else "failed"
                    delivery.last_error = outbound.error
                    if new_status == "retry":
                        if delivery.status != "retry" or delivery.next_attempt_at is None:
                            delivery.next_attempt_at = now
                    else:
                        delivery.next_attempt_at = None
                elif outbound.status == MessageStatus.PENDING:
                    if delivery.status == "queued" and _utc(delivery.updated_at) > (
                        now - _QUEUED_LEASE
                    ):
                        # Another live worker has claimed the row and may still
                        # be waiting for the shared QiWe send lock.
                        new_status = "queued"
                    else:
                        # A stale pre-request claim is safe to retry: the durable
                        # outbound row never crossed PENDING -> SENDING.
                        new_status = "pending"
                # SENDING may belong to this live worker. Startup recovery turns
                # interrupted SENDING into UNKNOWN before this service starts.
                if new_status != delivery.status:
                    delivery.status = new_status
                    affected_runs.add(delivery.daily_run_id)
            await session.commit()
        for run_id in affected_runs:
            await self._refresh_run(run_id)

    async def _refresh_run(self, run_id: int) -> None:
        async with SessionLocal() as session:
            run = await session.get(CuratedVideoDailyRun, run_id, with_for_update=True)
            if run is None:
                return
            deliveries = list(
                await session.scalars(
                    select(CuratedVideoDelivery).where(
                        CuratedVideoDelivery.daily_run_id == run_id
                    )
                )
            )
            sent = sum(row.status == "sent" for row in deliveries)
            active = sum(row.status in _ACTIVE_DELIVERY_STATUSES for row in deliveries)
            failed = len(deliveries) - sent - active
            run.sent_count = sent
            run.failed_count = failed
            item = await session.get(CuratedVideoItem, run.item_id) if run.item_id else None
            if active:
                run.status = "distributing"
                run.completed_at = None
                if item is not None:
                    item.status = "assigned"
            else:
                run.status = "completed" if failed == 0 else "completed_with_failures"
                run.completed_at = datetime.now(timezone.utc)
                if item is not None:
                    item.status = run.status
            await session.commit()
