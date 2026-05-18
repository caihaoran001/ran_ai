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
from app.models.user import User
from app.services.channels import send_channel_text
from app.services.checkin_questionnaires import get_checkin_questionnaire_schedule
from app.services.document_service import generate_stage_report


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
                await self.check_questionnaire_sends_once()
                await self.check_stage_reports_once()
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
                    rule_id="daily_checkin",
                    event_date=today,
                    event_type=ReminderEventType.SEEN,
                )
                reminded = await _has_event(
                    session,
                    service_case.id,
                    rule_id="daily_checkin",
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

    async def check_questionnaire_sends_once(self) -> None:
        async with SessionLocal() as session:
            schedule = await get_checkin_questionnaire_schedule(session)
            if not schedule.get("enabled", True):
                return
            timezone = ZoneInfo(str(schedule.get("timezone") or self.settings.reminder_timezone))
            now = datetime.now(timezone)
            send_at_val = str(schedule.get("send_at") or "09:00")
            if send_at_val not in ("随时", "anytime", "immediate"):
                if now.time() < _parse_time(send_at_val):
                    return

            service_cases = list(
                await session.scalars(
                    select(ServiceCase).where(
                        ServiceCase.deleted_at.is_(None),
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            for service_case in service_cases:
                day = _service_day(service_case.started_at, now, timezone)
                rule = _questionnaire_rule_for_day(schedule, day)
                if not rule:
                    continue
                template_key = str(rule.get("template_key") or "")
                if not template_key:
                    continue
                rule_id = f"questionnaire:{template_key}:day{day}"
                if await _has_event(
                    session,
                    service_case.id,
                    rule_id=rule_id,
                    event_date=now.date(),
                    event_type=ReminderEventType.REMINDED,
                ):
                    continue

                user = await session.get(User, service_case.user_id) if service_case.user_id else None
                questionnaire_link = (
                    f"{self.settings.frontend_public_base_url.rstrip('/')}"
                    f"/q/{service_case.questionnaire_token}?template={template_key}"
                )
                personalized_link = (
                    f"{self.settings.frontend_public_base_url.rstrip('/')}"
                    f"/q/{service_case.questionnaire_token}?template=personalized_checkin"
                )
                content = _format_template(
                    str(rule.get("message") or "请完成今日问卷：\n{questionnaire_link}"),
                    {
                        "questionnaire_link": questionnaire_link,
                        "personalized_link": personalized_link,
                        "day": day,
                        "case_no": service_case.case_no,
                        "group_id": service_case.group_id,
                        "user_name": user.name if user else "",
                        "template_key": template_key,
                    },
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
                            rule_id=rule_id,
                            event_date=now.date(),
                            event_type=ReminderEventType.REMINDED,
                        )
                    )
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error
            await session.commit()

    async def check_stage_reports_once(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.reminder_timezone))
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select(ServiceCase).where(
                        ServiceCase.deleted_at.is_(None),
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            for service_case in service_cases:
                started_at = service_case.started_at
                if not started_at:
                    continue
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=ZoneInfo("UTC"))
                days = int((now - started_at.astimezone(ZoneInfo(self.settings.reminder_timezone))).total_seconds() // 86400)
                for threshold, stage in ((3, "day3"), (7, "day7"), (14, "day14"), (21, "day21"), (28, "day28")):
                    if days >= threshold:
                        await generate_stage_report(session, service_case, stage=stage)
                if days >= 28:
                    await generate_stage_report(session, service_case, stage="graduation")


async def mark_seen(service_case_id: int, event_date: date | None = None) -> None:
    async with SessionLocal() as session:
        if await _has_event(
            session,
            service_case_id,
            rule_id="daily_checkin",
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
    rule_id: str,
    event_date: date,
    event_type: ReminderEventType,
) -> bool:
    value = await session.scalar(
        select(ReminderEvent.id).where(
            ReminderEvent.service_case_id == service_case_id,
            ReminderEvent.rule_id == rule_id,
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


def _service_day(started_at: datetime | None, now: datetime, timezone: ZoneInfo) -> int:
    if started_at is None:
        return 1
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=ZoneInfo("UTC"))
    started_local = started_at.astimezone(timezone)
    return max((now.date() - started_local.date()).days + 1, 1)


def _questionnaire_rule_for_day(schedule: dict, day: int) -> dict | None:
    for rule in schedule.get("rules") or []:
        if isinstance(rule, dict) and int(rule.get("day") or -1) == day:
            return rule
    daily_rule = schedule.get("daily_rule")
    if not isinstance(daily_rule, dict) or not daily_rule.get("enabled", True):
        return None
    skip_days = {
        int(value)
        for value in daily_rule.get("skip_days", [])
        if isinstance(value, int) or str(value).isdigit()
    }
    return None if day in skip_days else daily_rule


def _format_template(template: str, values: dict[str, object]) -> str:
    try:
        return template.format(**values)
    except Exception:
        return template + "\n" + str(values.get("questionnaire_link") or "")
