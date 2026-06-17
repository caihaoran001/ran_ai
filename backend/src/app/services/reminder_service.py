from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.checkin import Checkin
from app.models.outcome import OutcomeSnapshot, OutcomeStageState
from app.models.reminder import ReminderEvent, ReminderEventType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.channels import get_current_channel, send_channel_text
from app.services.checkin_questionnaires import get_checkin_questionnaire_schedule
from app.services.document_service import (
    generate_profile_and_documents,
    generate_stage_report,
    refresh_profile_for_case,
    retry_degraded_stage_report,
)
from app.services.knowledge_evolution import KnowledgeEvolutionService
from app.services.knowledge_evolution_settings import get_knowledge_evolution_config, parse_case_days
from app.services.outcome_engine import generate_outcome_snapshot
from app.services.service_case_queries import active_service_case_condition, checkin_day, effective_service_day, effective_start_at, select_active_service_cases


logger = logging.getLogger(__name__)
SCHEDULER_ADVISORY_LOCK_KEY = 2026051901
CHANNEL_SEND_MAX_ATTEMPTS = 3
CHANNEL_SEND_RETRY_DELAY_SECONDS = 1.0


class ReminderService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._last_knowledge_scan: datetime | None = None

    async def start(self) -> None:
        knowledge_enabled = self.settings.knowledge_evolution_enabled
        if not knowledge_enabled:
            try:
                async with SessionLocal() as session:
                    knowledge_enabled = (await get_knowledge_evolution_config(session, settings=self.settings)).enabled
            except Exception:
                logger.exception("Failed to load knowledge evolution config")
        questionnaire_schedule_enabled = False
        try:
            async with SessionLocal() as session:
                schedule = await get_checkin_questionnaire_schedule(session)
                questionnaire_schedule_enabled = schedule.get("enabled", False)
        except Exception:
            logger.exception("Failed to load questionnaire schedule config")
        if not self.settings.reminder_enabled and not knowledge_enabled and not self.settings.outcome_snapshots_enabled and not questionnaire_schedule_enabled:
            logger.info("Reminder, outcome snapshot, knowledge evolution and questionnaire schedule services disabled")
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
                async with _scheduler_advisory_lock() as acquired:
                    if not acquired:
                        logger.debug("Reminder scheduler skipped: advisory lock is held by another worker")
                    else:
                        if self.settings.reminder_enabled:
                            await self.check_once()
                            await self.check_inactive_followups_once()
                            await self.check_stage_reports_once()
                        await self.check_questionnaire_sends_once()
                        if self.settings.outcome_snapshots_enabled:
                            await self.check_outcome_snapshots_once()
                        await self.check_knowledge_evolution_once()
                        await self.check_profile_refresh_once()
                        await self.check_stuck_document_generation_once()
                        await self.check_retry_degraded_reports_once()
                        await self.check_unsent_channel_messages_once()
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
            schedule = await get_checkin_questionnaire_schedule(session)
            timezone = ZoneInfo(self.settings.reminder_timezone)
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status.in_(
                            [
                                ServiceCaseStatus.APPROVED,
                                ServiceCaseStatus.IN_SERVICE,
                            ]
                        )
                    )
                )
            )
            # Pre-fetch today's daily_checkin reminder events to perform in-memory checks
            today = now.date()
            reminder_events = list(
                await session.scalars(
                    select(ReminderEvent).where(
                        ReminderEvent.rule_id == "daily_checkin",
                        ReminderEvent.event_date == today,
                    )
                )
            )
            seen_set = {e.service_case_id for e in reminder_events if e.event_type == ReminderEventType.SEEN}
            reminded_set = {e.service_case_id for e in reminder_events if e.event_type == ReminderEventType.REMINDED}

            for service_case in service_cases:
                seen = service_case.id in seen_set
                reminded = service_case.id in reminded_set
                if seen or reminded:
                    continue
                day = checkin_day(service_case, now, timezone)
                # 第1天日期未到(存量沿用次日语义)则不催打卡
                if day is None:
                    continue
                if schedule.get("enabled", True):
                    rule = _questionnaire_rule_for_day(schedule, day)
                    if rule:
                        continue
                reminder_event = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id="daily_checkin",
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reminder_event:
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
                result = await _send_channel_text_with_retries(service_case.group_id, content)
                if result.success:
                    message.status = MessageStatus.SENT
                    message.wecom_message_id = result.message_id
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error
            await session.commit()

    async def check_inactive_followups_once(self) -> None:
        if not self.settings.inactive_followup_enabled:
            return
        now = datetime.now(ZoneInfo(self.settings.reminder_timezone))
        async with SessionLocal() as session:
            schedule = await get_checkin_questionnaire_schedule(session)
            inactive_config = schedule.get("inactive_followup") if isinstance(schedule, dict) else None
            if isinstance(inactive_config, dict):
                if inactive_config.get("enabled", True) is False:
                    return
                inactive_hours = _int_or_default(inactive_config.get("hours"), self.settings.inactive_followup_hours)
                inactive_message = str(inactive_config.get("message") or self.settings.inactive_followup_message)
            else:
                inactive_hours = self.settings.inactive_followup_hours
                inactive_message = self.settings.inactive_followup_message

        if now.isoweekday() not in _parse_weekdays(self.settings.reminder_weekdays):
            return
        if now.time() < _parse_time(self.settings.reminder_at):
            return

        today = now.date()
        stale_before = now - timedelta(hours=max(1, inactive_hours))
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                        or_(ServiceCase.risk_level.is_(None), ServiceCase.risk_level != "high"),
                    )
                )
            )
            if not service_cases:
                return
            case_ids = [item.id for item in service_cases]
            latest_inbound_rows = list(
                await session.execute(
                    select(Message.service_case_id, Message.created_at)
                    .where(
                        Message.service_case_id.in_(case_ids),
                        Message.direction == MessageDirection.INBOUND,
                        Message.sender_type == MessageSenderType.USER,
                        Message.purpose == MessagePurpose.CHAT,
                        Message.status == MessageStatus.RECEIVED,
                    )
                    .order_by(Message.service_case_id, Message.created_at.desc())
                )
            )
            latest_by_case: dict[int, datetime] = {}
            for case_id, created_at in latest_inbound_rows:
                latest_by_case.setdefault(case_id, created_at)

            reminders = list(
                await session.scalars(
                    select(ReminderEvent).where(
                        ReminderEvent.rule_id == "inactive_followup",
                        ReminderEvent.event_date == today,
                        ReminderEvent.event_type == ReminderEventType.REMINDED,
                    )
                )
            )
            reminded_ids = {item.service_case_id for item in reminders}

            for service_case in service_cases:
                if service_case.id in reminded_ids:
                    continue
                latest_inbound_at = latest_by_case.get(service_case.id) or service_case.started_at
                if not latest_inbound_at:
                    continue
                if latest_inbound_at.tzinfo is None:
                    latest_inbound_at = latest_inbound_at.replace(tzinfo=ZoneInfo("UTC"))
                if latest_inbound_at.astimezone(ZoneInfo(self.settings.reminder_timezone)) > stale_before:
                    continue
                reminder_event = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id="inactive_followup",
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reminder_event:
                    continue
                user = await session.get(User, service_case.user_id) if service_case.user_id else None
                content = _format_template(
                    inactive_message,
                    {
                        "case_no": service_case.case_no,
                        "group_id": service_case.group_id,
                        "user_name": user.name if user else "",
                        "hours": inactive_hours,
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
                result = await _send_channel_text_with_retries(service_case.group_id, content)
                if result.success:
                    message.status = MessageStatus.SENT
                    message.wecom_message_id = result.message_id
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error
            await session.commit()

    async def check_stuck_document_generation_once(self) -> None:
        """补跑卡在 generating 的文档生成（多为部署/重启时后台任务被中断）。"""
        if not self.settings.ai_api_key:
            return
        threshold = datetime.now(timezone.utc) - timedelta(minutes=10)
        async with SessionLocal() as session:
            cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.GENERATING,
                        ServiceCase.updated_at < threshold,
                    )
                )
            )
            for service_case in cases:
                logger.warning(
                    "Recovering stuck document generation service_case_id=%s case_no=%s",
                    service_case.id,
                    service_case.case_no,
                )
                try:
                    await generate_profile_and_documents(
                        session, service_case, auto_send=self.settings.auto_send_documents
                    )
                except Exception:
                    logger.exception(
                        "Retry stuck document generation failed service_case_id=%s", service_case.id
                    )
                    await session.rollback()

    async def check_retry_degraded_reports_once(self) -> None:
        """LLM 恢复后自动重试降级(degraded)的阶段报告，用真内容替换套话（带重试上限）。"""
        if not self.settings.ai_api_key:
            return
        threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        async with SessionLocal() as session:
            documents = list(
                await session.scalars(
                    select(UserDocument)
                    .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
                    .where(
                        active_service_case_condition(),
                        UserDocument.document_type.in_(
                            [DocumentType.STAGE_REPORT, DocumentType.GRADUATION_REPORT]
                        ),
                        UserDocument.updated_at < threshold,
                    )
                )
            )
            for document in documents:
                cj = document.content_json if isinstance(document.content_json, dict) else {}
                if not cj.get("degraded"):
                    continue
                if int(cj.get("degraded_retry_count", 0)) >= 5:
                    continue
                try:
                    await retry_degraded_stage_report(session, document)
                except Exception:
                    logger.exception("Retry degraded stage report failed doc_id=%s", document.id)
                    await session.rollback()

    async def check_unsent_channel_messages_once(self) -> None:
        """通道恢复后，自动补发此前因『通道未连』失败的主动消息，兜住部署重启窗口丢消息。

        只补发 error 含 message channel 的失败消息（不碰人工驳回等其它失败），且限近 2 小时内。
        """
        if get_current_channel() is None:
            return
        threshold = datetime.now(timezone.utc) - timedelta(hours=2)
        async with SessionLocal() as session:
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(
                        Message.direction == MessageDirection.OUTBOUND,
                        Message.sender_type == MessageSenderType.BOT,
                        Message.status == MessageStatus.FAILED,
                        Message.error.ilike("%message channel%"),
                        Message.created_at > threshold,
                    )
                    .order_by(Message.created_at)
                )
            )
            changed = False
            for message in messages:
                try:
                    result = await send_channel_text(message.group_id, message.content)
                except Exception:
                    logger.exception("Resend unsent channel message failed id=%s", message.id)
                    continue
                if result.success:
                    message.status = MessageStatus.SENT
                    message.error = None
                    if result.message_id:
                        message.wecom_message_id = result.message_id
                    changed = True
            if changed:
                await session.commit()

    async def check_profile_refresh_once(self) -> None:
        """每天刷新『自上次画像更新后有新聊天/打卡』的在服务用户主画像（无新活动跳过）。"""
        if not self.settings.ai_api_key:
            return
        timezone = ZoneInfo(self.settings.reminder_timezone)
        now = datetime.now(timezone)
        if now.time() < _parse_time(self.settings.reminder_at):
            return
        today = now.date()
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            for service_case in service_cases:
                existing = await session.scalar(
                    select(ReminderEvent).where(
                        ReminderEvent.service_case_id == service_case.id,
                        ReminderEvent.rule_id == "profile_refresh",
                        ReminderEvent.event_date == today,
                        ReminderEvent.event_type == ReminderEventType.REMINDED,
                    )
                )
                if existing:
                    continue
                latest_profile = await session.scalar(
                    select(UserProfile)
                    .where(UserProfile.service_case_id == service_case.id)
                    .order_by(UserProfile.created_at.desc())
                    .limit(1)
                )
                if not latest_profile:
                    continue
                since = latest_profile.created_at
                new_message = await session.scalar(
                    select(Message.id)
                    .where(
                        Message.service_case_id == service_case.id,
                        Message.direction == MessageDirection.INBOUND,
                        Message.created_at > since,
                    )
                    .limit(1)
                )
                new_checkin = await session.scalar(
                    select(Checkin.id)
                    .where(
                        Checkin.service_case_id == service_case.id,
                        Checkin.created_at > since,
                    )
                    .limit(1)
                )
                if not new_message and not new_checkin:
                    continue
                reserved = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id="profile_refresh",
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reserved:
                    continue
                day = effective_service_day(service_case, now, timezone)
                if day < 1:  # 计划开始日未到，不刷新画像
                    continue
                await refresh_profile_for_case(session, service_case, stage=f"day{day}")
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
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            # Pre-fetch today's questionnaire reminder events to check in memory
            today = now.date()
            reminder_events = list(
                await session.scalars(
                    select(ReminderEvent).where(
                        ReminderEvent.rule_id.like("questionnaire:%"),
                        ReminderEvent.event_date == today,
                        ReminderEvent.event_type == ReminderEventType.REMINDED,
                    )
                )
            )
            reminded_questionnaires = {(e.service_case_id, e.rule_id) for e in reminder_events}

            for service_case in service_cases:
                day = checkin_day(service_case, now, timezone)
                # 第1天日期当天发；存量未填计划日→沿用旧"次日=第1天"，天数号不变、不会重发；未到则不发
                if day is None:
                    continue
                rule = _questionnaire_rule_for_day(schedule, day)
                if not rule:
                    continue
                template_key = str(rule.get("template_key") or "")
                if not template_key:
                    continue
                rule_id = f"questionnaire:{template_key}:day{day}"
                if (service_case.id, rule_id) in reminded_questionnaires:
                    continue
                reminder_event = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id=rule_id,
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reminder_event:
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
                result = await _send_channel_text_with_retries(service_case.group_id, content)
                if result.success:
                    message.status = MessageStatus.SENT
                    message.wecom_message_id = result.message_id
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error
            await session.commit()

    async def _generate_report_for_case_worker(self, case_id: int, stage: str) -> None:
        async with SessionLocal() as session:
            service_case = await session.get(ServiceCase, case_id)
            if not service_case:
                logger.error(f"Service case {case_id} not found in worker")
                return
            try:
                await generate_stage_report(session, service_case, stage=stage)
                logger.info(f"Concurrently generated stage report {stage} for case {case_id}")
            except Exception:
                logger.exception(f"Failed to generate stage report {stage} for case {case_id}")

    async def check_stage_reports_once(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.reminder_timezone))
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            case_ids = [c.id for c in service_cases]
            if not case_ids:
                return

            # Pre-fetch all stage/graduation reports for active cases in one query
            documents = list(
                await session.scalars(
                    select(UserDocument).where(
                        UserDocument.service_case_id.in_(case_ids),
                        UserDocument.document_type.in_([DocumentType.STAGE_REPORT, DocumentType.GRADUATION_REPORT]),
                    )
                )
            )
            existing_reports = set()
            for doc in documents:
                if isinstance(doc.content_json, dict) and doc.content_json.get("stage"):
                    existing_reports.add((doc.service_case_id, doc.content_json.get("stage")))

        # Define the task list for parallel execution using distinct sessions
        tasks = []
        for service_case in service_cases:
            started_at = effective_start_at(service_case)  # 计划开始日优先，否则文件发送时刻
            if not started_at:
                continue
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=ZoneInfo("UTC"))
            days = int((now - started_at.astimezone(ZoneInfo(self.settings.reminder_timezone))).total_seconds() // 86400)
            for threshold, stage in ((3, "day3"), (7, "day7"), (14, "day14"), (21, "day21"), (28, "day28")):
                if days >= threshold:
                    if (service_case.id, stage) not in existing_reports:
                        tasks.append(self._generate_report_for_case_worker(service_case.id, stage))
            if days >= 28:
                if (service_case.id, "graduation") not in existing_reports:
                    tasks.append(self._generate_report_for_case_worker(service_case.id, "graduation"))

        if tasks:
            logger.info(f"Concurrently generating {len(tasks)} stage reports via scheduler")
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _generate_outcome_snapshot_for_case_worker(self, case_id: int, milestone: str) -> None:
        async with SessionLocal() as session:
            service_case = await session.get(ServiceCase, case_id)
            if not service_case:
                logger.error("Service case %s not found in outcome snapshot worker", case_id)
                return
            try:
                await generate_outcome_snapshot(session, service_case, milestone=milestone)
                await session.commit()
                logger.info("Generated outcome snapshot %s for case %s", milestone, case_id)
            except Exception:
                logger.exception("Failed to generate outcome snapshot %s for case %s", milestone, case_id)

    async def check_outcome_snapshots_once(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.reminder_timezone))
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            case_ids = [item.id for item in service_cases]
            if not case_ids:
                return
            snapshots = list(
                await session.scalars(
                    select(OutcomeSnapshot).where(OutcomeSnapshot.service_case_id.in_(case_ids))
                )
            )
            snapshot_map = {(item.service_case_id, item.milestone): item for item in snapshots}
            states = list(
                await session.scalars(
                    select(OutcomeStageState).where(OutcomeStageState.service_case_id.in_(case_ids))
                )
            )
            state_map = {(item.service_case_id, item.milestone): item for item in states}

        tasks = []
        for service_case in service_cases:
            started_at = service_case.started_at
            if not started_at:
                continue
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=ZoneInfo("UTC"))
            days = max((now.date() - started_at.astimezone(ZoneInfo(self.settings.reminder_timezone)).date()).days + 1, 1)
            for threshold, milestone in (
                (1, "day1"),
                (3, "day3"),
                (7, "day7"),
                (14, "day14"),
                (28, "day28"),
                (90, "day90"),
                (365, "day365"),
            ):
                snapshot = snapshot_map.get((service_case.id, milestone))
                state = state_map.get((service_case.id, milestone))
                should_refresh = (
                    snapshot is None
                    or (
                        state is not None
                        and (
                            snapshot.generated_at is None
                            or state.updated_at > snapshot.generated_at
                        )
                    )
                )
                if days >= threshold and should_refresh:
                    tasks.append(self._generate_outcome_snapshot_for_case_worker(service_case.id, milestone))
        if tasks:
            logger.info("Generating %s outcome snapshots via scheduler", len(tasks))
            batch_size = 5
            for i in range(0, len(tasks), batch_size):
                await asyncio.gather(*tasks[i:i + batch_size], return_exceptions=True)

    async def check_knowledge_evolution_once(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.reminder_timezone))
        async with SessionLocal() as session:
            config = await get_knowledge_evolution_config(session, settings=self.settings)
            if not config.enabled:
                return
            now = datetime.now(ZoneInfo(config.timezone))
            if self._last_knowledge_scan:
                elapsed = (now - self._last_knowledge_scan).total_seconds()
                if elapsed < config.interval_seconds:
                    return
            service = KnowledgeEvolutionService()
            created, skipped = 0, 0
            qa_at = _parse_time(config.qa_at)
            if now.time() >= qa_at:
                qa_created, qa_skipped = await service.scan_daily_qa(
                    session,
                    timezone_name=config.timezone,
                    limit=config.daily_qa_limit,
                )
                created += qa_created
                skipped += qa_skipped
            case_created, case_skipped = await service.scan_stage_cases(
                session,
                stage_days=parse_case_days(config.case_days),
                timezone_name=config.timezone,
                limit_per_case=config.case_limit_per_case,
            )
            created += case_created
            skipped += case_skipped
            if created or skipped:
                logger.info("Knowledge evolution scan finished: created=%s skipped=%s", created, skipped)
        self._last_knowledge_scan = now


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


async def _send_channel_text_with_retries(chat_id: str, content: str):
    last_result = None
    for attempt in range(1, CHANNEL_SEND_MAX_ATTEMPTS + 1):
        result = await send_channel_text(chat_id, content)
        if result.success:
            return result
        last_result = result
        if not result.retryable or attempt >= CHANNEL_SEND_MAX_ATTEMPTS:
            break
        logger.warning(
            "Reminder channel send failed, will retry: chat_id=%s attempt=%s/%s error=%s",
            chat_id,
            attempt,
            CHANNEL_SEND_MAX_ATTEMPTS,
            result.error,
        )
        await asyncio.sleep(CHANNEL_SEND_RETRY_DELAY_SECONDS)
    if last_result is not None:
        last_result.error = f"{last_result.error or 'channel send failed'} (attempts={attempt})"
        logger.warning(
            "Reminder channel send failed after retries: chat_id=%s attempts=%s error=%s",
            chat_id,
            attempt,
            last_result.error,
        )
    return last_result


@asynccontextmanager
async def _scheduler_advisory_lock():
    async with SessionLocal() as session:
        if session.in_transaction():
            acquired = bool(
                await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(key=SCHEDULER_ADVISORY_LOCK_KEY)
                )
            )
            yield acquired
        else:
            async with session.begin():
                acquired = bool(
                    await session.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(key=SCHEDULER_ADVISORY_LOCK_KEY)
                    )
                )
                yield acquired


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


async def _reserve_reminder_event(
    session,
    service_case_id: int,
    *,
    rule_id: str,
    event_date: date,
    event_type: ReminderEventType,
) -> ReminderEvent | None:
    event = ReminderEvent(
        service_case_id=service_case_id,
        rule_id=rule_id,
        event_date=event_date,
        event_type=event_type,
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        return None
    return event


def _parse_time(value: str) -> time:
    hour, minute, *rest = value.split(":")
    return time(int(hour), int(minute), int(rest[0]) if rest else 0)


def _parse_weekdays(value: str) -> set[int]:
    return {int(item.strip()) for item in value.split(",") if item.strip()}


def _parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip().isdigit()]


def _service_day(started_at: datetime | None, now: datetime, timezone: ZoneInfo) -> int:
    if started_at is None:
        return 1
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=ZoneInfo("UTC"))
    started_local = started_at.astimezone(timezone)
    return max((now.date() - started_local.date()).days + 1, 1)


def _questionnaire_rule_for_day(schedule: dict, day: int) -> dict | None:
    if schedule.get("rules_enabled", True) is not False:
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


def _int_or_default(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
