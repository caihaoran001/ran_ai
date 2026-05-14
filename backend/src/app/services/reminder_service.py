from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.reminder import ReminderEvent, ReminderEventType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.channels import send_channel_text


logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if not self.settings.reminder_enabled:
            logger.info("Reminder service disabled")
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run_loop(), name="reminder-service")
        logger.info("Reminder service started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stopped.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Reminder check failed")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self.settings.reminder_check_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def check_once(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.reminder_timezone))
        if now.isoweekday() not in _parse_weekdays(self.settings.reminder_weekdays):
            return
        if now.time() < _parse_time(self.settings.reminder_at):
            return

        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select(ServiceCase).where(
                        ServiceCase.deleted_at.is_(None),
                        ServiceCase.status.in_(
                            [
                                ServiceCaseStatus.APPROVED,
                                ServiceCaseStatus.IN_SERVICE,
                            ]
                        )
                    )
                )
            )
            for service_case in service_cases:
                today = now.date()
                seen = await _has_event(
                    session,
                    service_case.id,
                    event_date=today,
                    event_type=ReminderEventType.SEEN,
                )
                reminded = await _has_event(
                    session,
                    service_case.id,
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if seen or reminded:
                    continue

                content = self.settings.reminder_message.format(
                    time=_parse_time(self.settings.reminder_at).strftime("%H:%M"),
                    date=today.isoformat(),
                    group_id=service_case.group_id,
                )
                message = Message(
                    service_case_id=service_case.id,
                    group_id=service_case.group_id,
                    direction=MessageDirection.OUTBOUND,
                    sender_type=MessageSenderType.SYSTEM,
                    message_type="markdown",
                    content=content,
                    purpose=MessagePurpose.REMINDER,
                    status=MessageStatus.PENDING,
                )
                session.add(message)
                await session.flush()
                result = await send_channel_text(service_case.group_id, content)
                if result.success:
                    message.status = MessageStatus.SENT
                    message.wecom_message_id = result.message_id
                    session.add(
                        ReminderEvent(
                            service_case_id=service_case.id,
                            rule_id="daily_checkin",
                            event_date=today,
                            event_type=ReminderEventType.REMINDED,
                        )
                    )
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error
            await session.commit()


async def mark_seen(service_case_id: int, event_date: date | None = None) -> None:
    async with SessionLocal() as session:
        if await _has_event(
            session,
            service_case_id,
            event_date=event_date or date.today(),
            event_type=ReminderEventType.SEEN,
        ):
            return
        session.add(
            ReminderEvent(
                service_case_id=service_case_id,
                rule_id="daily_checkin",
                event_date=event_date or date.today(),
                event_type=ReminderEventType.SEEN,
            )
        )
        await session.commit()


async def _has_event(
    session,
    service_case_id: int,
    *,
    event_date: date,
    event_type: ReminderEventType,
) -> bool:
    value = await session.scalar(
        select(ReminderEvent.id).where(
            ReminderEvent.service_case_id == service_case_id,
            ReminderEvent.rule_id == "daily_checkin",
            ReminderEvent.event_date == event_date,
            ReminderEvent.event_type == event_type,
        )
    )
    return value is not None


def _parse_time(value: str) -> time:
    hour, minute, *rest = value.split(":")
    return time(int(hour), int(minute), int(rest[0]) if rest else 0)


def _parse_weekdays(value: str) -> set[int]:
    return {int(item.strip()) for item in value.split(",") if item.strip()}
