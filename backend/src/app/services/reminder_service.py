from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.agent_reminder_task import AgentReminderTask, AgentReminderTaskStatus
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
from app.models.user_state_profile import UserStateProfile
from app.services.agent import generate_scheduled_reminder_reply
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
from app.services.service_case_queries import (
    SERVICE_CASE_WINDOW_DAYS,
    active_service_case_condition,
    checkin_day,
    effective_service_day,
    select_active_service_cases,
    service_graduation_at,
    service_window_ended,
)
from app.services.short_link import get_or_create_short_link


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
        self._last_room_name_full_refresh: datetime | None = None
        self._last_image_cache_cleanup: datetime | None = None

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
        if (
            not self.settings.reminder_enabled
            and not self.settings.agent_reminders_enabled
            and not knowledge_enabled
            and not self.settings.outcome_snapshots_enabled
            and not questionnaire_schedule_enabled
        ):
            logger.info("Reminder, agent reminder, outcome snapshot, knowledge evolution and questionnaire schedule services disabled")
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
                        await self.check_service_graduations_once()
                        if self.settings.agent_reminders_enabled:
                            await self.check_agent_reminder_tasks_once()
                        if self.settings.reminder_enabled:
                            await self.check_once()
                            await self.check_inactive_followups_once()
                            await self.check_stage_reports_once()
                            await self.check_action_focus_push_once()
                        await self.check_questionnaire_sends_once()
                        if self.settings.outcome_snapshots_enabled:
                            await self.check_outcome_snapshots_once()
                        await self.check_knowledge_evolution_once()
                        await self.check_profile_refresh_once()
                        await self.check_stuck_document_generation_once()
                        await self.check_retry_degraded_reports_once()
                        await self.check_unsent_channel_messages_once()
                        await self.check_room_names_once()
                        await self.check_image_cache_cleanup_once()
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

    async def check_service_graduations_once(self, now: datetime | None = None) -> None:
        timezone = ZoneInfo(self.settings.reminder_timezone)
        now = now or datetime.now(timezone)
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.started_at.is_not(None),
                    )
                )
            )
            changed = False
            for service_case in service_cases:
                if not service_window_ended(service_case, now, timezone):
                    continue
                service_case.status = ServiceCaseStatus.COMPLETED
                service_case.ended_at = service_case.ended_at or service_graduation_at(service_case, timezone)
                changed = True
            if changed:
                await session.commit()

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
                if day > SERVICE_CASE_WINDOW_DAYS:
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

    async def check_agent_reminder_tasks_once(self, now: datetime | None = None) -> None:
        if not self.settings.agent_reminders_enabled:
            return
        tz = ZoneInfo(self.settings.reminder_timezone)
        now = now or datetime.now(tz)
        now_utc = now.astimezone(timezone.utc)
        async with SessionLocal() as session:
            tasks = list(
                await session.scalars(
                    select(AgentReminderTask)
                    .where(
                        AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
                        AgentReminderTask.remind_at <= now_utc,
                    )
                    .order_by(AgentReminderTask.remind_at, AgentReminderTask.id)
                    .limit(20)
                )
            )
            for task in tasks:
                service_case = await session.scalar(
                    select_active_service_cases(ServiceCase.id == task.service_case_id)
                )
                if service_case is None:
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = "service case not found or deleted"
                    continue
                if service_case.status in {ServiceCaseStatus.COMPLETED, ServiceCaseStatus.REFUNDED}:
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = f"service case is {service_case.status.value}"
                    continue
                if service_window_ended(service_case, now, tz):
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = "service window ended"
                    continue

                task.status = AgentReminderTaskStatus.SENDING
                task.error = None
                await session.flush()

                recent_messages = await _recent_chat_lines(session, task.service_case_id)
                try:
                    agent_result = await generate_scheduled_reminder_reply(
                        service_case_id=task.service_case_id,
                        trigger_id=task.trigger_message_id,
                        reminder_instruction=task.content,
                        recent_messages=recent_messages,
                        settings=self.settings,
                    )
                    outbound_content = agent_result.reply.strip()
                    outbound_analysis = {
                        "agent_reminder_task_id": task.id,
                        "agent": {
                            "enabled": True,
                            "scheduled_reminder": True,
                            "tool_trace": agent_result.tool_trace,
                            "data_tool_failed": agent_result.data_tool_failed,
                        },
                        "reminder_instruction": task.content,
                    }
                except Exception as exc:
                    logger.exception("Scheduled agent reminder generation failed task_id=%s", task.id)
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = f"agent generation failed: {type(exc).__name__}: {exc}"
                    continue
                if not outbound_content:
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = "agent generation returned empty reply"
                    continue

                # 提醒默认原生 @ 请求者（当初说“提醒我…”的那个用户）：从 trigger 消息解析其企微 userId。
                at_target = await _resolve_reminder_at_target(session, task.trigger_message_id)

                message = Message(
                    service_case_id=task.service_case_id,
                    group_id=task.group_id,
                    direction=MessageDirection.OUTBOUND,
                    sender_type=MessageSenderType.SYSTEM,
                    message_type="markdown",
                    content=outbound_content,
                    purpose=MessagePurpose.REMINDER,
                    status=MessageStatus.PENDING,
                    trigger_id=task.trigger_message_id,
                    analysis_json=outbound_analysis,
                )
                session.add(message)
                await session.flush()

                result, sent_content = await _send_reminder_with_at(task.group_id, outbound_content, at_target)
                message.content = sent_content
                if result and result.success:
                    message.status = MessageStatus.SENT
                    message.wecom_message_id = result.message_id
                    message.sent_at = datetime.now(timezone.utc)
                    task.status = AgentReminderTaskStatus.SENT
                    task.sent_message_id = message.id
                    task.sent_at = message.sent_at
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error if result else "channel send failed"
                    task.status = AgentReminderTaskStatus.FAILED
                    task.sent_message_id = message.id
                    task.error = message.error
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
                if service_window_ended(service_case, now, ZoneInfo(self.settings.reminder_timezone)):
                    continue
                if not service_case.inactive_wake_enabled:
                    continue  # 单服务单关闭了无互动自动唤醒
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
                if not service_case.checkin_link_enabled:
                    continue  # 单服务单关闭了打卡链接推送
                day = checkin_day(service_case, now, timezone)
                # 第1天日期当天发；存量未填计划日→沿用旧"次日=第1天"，天数号不变、不会重发；未到则不发
                if day is None:
                    continue
                if day > SERVICE_CASE_WINDOW_DAYS:
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
                base = self.settings.frontend_public_base_url.rstrip("/")
                q_code = await get_or_create_short_link(
                    session,
                    service_case_id=service_case.id,
                    template_key=template_key,
                    target_path=f"/q/{service_case.questionnaire_token}?template={template_key}",
                )
                questionnaire_link = f"{base}/s/{q_code}"
                p_code = await get_or_create_short_link(
                    session,
                    service_case_id=service_case.id,
                    template_key="personalized_checkin",
                    target_path=f"/q/{service_case.questionnaire_token}?template=personalized_checkin",
                )
                personalized_link = f"{base}/s/{p_code}"
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

    async def check_image_cache_cleanup_once(self, now: datetime | None = None) -> None:
        """清理过期的图片缓存(.wecom_cache/images)：保留最近 60 天，每天清理一次更老的文件。

        聊天记录显示的就是这些文件，保留期(覆盖 28 天服务周期 + 回看余量)内的图都正常显示，
        只清掉很久以前的；前端对已清理的图做"图片已过期"兜底。要调整保留期改 retention_days 即可。
        """
        now = now or datetime.now(ZoneInfo(self.settings.reminder_timezone))
        if self._last_image_cache_cleanup and (now - self._last_image_cache_cleanup).total_seconds() < 24 * 3600:
            return
        self._last_image_cache_cleanup = now
        retention_days = 60
        cache_dir = Path(".wecom_cache/images")
        if not cache_dir.exists():
            return
        cutoff = now.timestamp() - retention_days * 86400
        removed = 0
        for f in cache_dir.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                logger.exception("Failed to remove cached image %s", f)
        if removed:
            logger.info("Image cache cleanup removed %d expired files (>%d days)", removed, retention_days)

    async def check_action_focus_push_once(self, now: datetime | None = None) -> None:
        """早 9 点给在服务用户推送一句白话「今日执行重点」(基于动态 action_focus.current_focus)。

        取 current_focus.key → _ACTION_FOCUS_STUDENT_MESSAGES 白话文案发到群里。
        高风险(risk_level=high)跳过、交人工；无当前重点/未映射的不发；每人每天一次(ReminderEvent 去重)。
        """
        now = now or datetime.now(ZoneInfo(self.settings.reminder_timezone))
        if now.hour != 9:
            return
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
                )
            )
            if not service_cases:
                return
            today = now.date()
            case_ids = [c.id for c in service_cases]
            sent_events = list(
                await session.scalars(
                    select(ReminderEvent).where(
                        ReminderEvent.rule_id == "action_focus_push",
                        ReminderEvent.event_date == today,
                        ReminderEvent.event_type == ReminderEventType.REMINDED,
                    )
                )
            )
            sent_set = {e.service_case_id for e in sent_events}
            profiles = {
                p.service_case_id: p
                for p in await session.scalars(
                    select(UserStateProfile).where(UserStateProfile.service_case_id.in_(case_ids))
                )
            }
            for service_case in service_cases:
                if service_case.id in sent_set:
                    continue
                if service_window_ended(service_case, now, ZoneInfo(self.settings.reminder_timezone)):
                    continue
                if not service_case.morning_push_enabled:
                    continue  # 单服务单关闭了早间个性化提醒
                if (service_case.risk_level or "").lower() == "high":
                    continue  # 高风险交班主任人工跟，不自动推送
                profile = profiles.get(service_case.id)
                key = _current_focus_key(profile.state_json if profile else None)
                message_text = _ACTION_FOCUS_STUDENT_MESSAGES.get(key or "")
                if not message_text:
                    continue  # 无当前重点 / key 未映射 → 不发
                reminder_event = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id="action_focus_push",
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reminder_event:
                    continue
                db_message = Message(
                    service_case_id=service_case.id,
                    group_id=service_case.group_id,
                    direction=MessageDirection.OUTBOUND,
                    sender_type=MessageSenderType.SYSTEM,
                    message_type="text",
                    content=message_text,
                    purpose=MessagePurpose.REMINDER,
                    status=MessageStatus.PENDING,
                )
                session.add(db_message)
                await session.flush()
                result = await _send_channel_text_with_retries(service_case.group_id, message_text)
                if result.success:
                    db_message.status = MessageStatus.SENT
                    db_message.wecom_message_id = result.message_id
                else:
                    db_message.status = MessageStatus.FAILED
                    db_message.error = result.error
            await session.commit()

    async def check_room_names_once(self, now: datetime | None = None) -> None:
        """回填/刷新服务单的 QiWe 群名(group_name)，用于按群名搜索服务单。

        - 距上次全量刷新 >=24h：刷新所有 active 案例的群名(同步运营改名)；
        - 否则只补 group_name 为空(IS NULL)的案例(新案例回填，每轮都跑，几十秒内补上)；
        - 取得到名字才写真名；取不到(机器人已退/群解散)把 None 标记成 ""，避免补空分支每轮重复重试(全量刷新仍会重试)。
        """
        channel = get_current_channel()
        if channel is None or not hasattr(channel, "batch_get_room_names"):
            return
        now = now or datetime.now(ZoneInfo(self.settings.reminder_timezone))
        full_refresh = (
            self._last_room_name_full_refresh is None
            or (now - self._last_room_name_full_refresh).total_seconds() >= 24 * 3600
        )
        async with SessionLocal() as session:
            statement = select_active_service_cases()
            if not full_refresh:
                statement = statement.where(ServiceCase.group_name.is_(None))
            cases = list(await session.scalars(statement))
            if not cases:
                if full_refresh:
                    self._last_room_name_full_refresh = now
                return
            room_ids = [c.group_id for c in cases if c.group_id]
            names = await channel.batch_get_room_names(room_ids)
            updated = 0
            for case in cases:
                name = names.get(str(case.group_id)) if case.group_id else None
                if name:
                    if name != case.group_name:
                        case.group_name = name
                        updated += 1
                elif case.group_name is None:
                    # 取不到群名：标记空串，补空分支(IS NULL)以后不再每轮重试它
                    case.group_name = ""
                    updated += 1
            if updated:
                await session.commit()
            if full_refresh:
                self._last_room_name_full_refresh = now
        if updated:
            logger.info("Refreshed %d service-case group names (full_refresh=%s)", updated, full_refresh)

    async def check_stage_reports_once(self, now: datetime | None = None) -> None:
        tz = ZoneInfo(self.settings.reminder_timezone)
        now = now or datetime.now(tz)
        # 阶段报告固定在每天 23:00（北京时间）集中生成：运营当晚集中审核、用户次日早上看到。
        # 调度每 30s 跑一次，这一小时会扫到约 120 次，靠下方 existing_reports 去重保证每个 stage 当晚只生成一次。
        # 非 23 点直接返回（连 DB 都不查），白天不产出。
        if now.hour != 23:
            return
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
            for stage in _due_stage_reports(service_case, now, tz, existing_reports):
                tasks.append(self._generate_report_for_case_worker(service_case.id, stage))

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


async def _resolve_reminder_at_target(session, trigger_message_id: int | None) -> tuple[str, str] | None:
    """取提醒请求者的企微 (userId, 昵称)，用于到点原生 @。

    仅当 trigger 是用户(USER)消息、且能从 analysis_json._qiwe_meta 拿到 sender_id 时返回；
    否则返回 None（如班主任代设、系统触发、非企微渠道），调用方回退纯文本发送。
    """
    if not trigger_message_id:
        return None
    trigger = await session.get(Message, trigger_message_id)
    if trigger is None or trigger.sender_type != MessageSenderType.USER:
        return None
    analysis = trigger.analysis_json if isinstance(trigger.analysis_json, dict) else {}
    meta = analysis.get("_qiwe_meta") if isinstance(analysis.get("_qiwe_meta"), dict) else {}
    user_id = str(meta.get("sender_id") or "").strip()
    if not user_id:
        return None
    name = str(meta.get("sender_name") or "").strip() or user_id
    return user_id, name


async def _send_reminder_with_at(chat_id: str, content: str, at_target: tuple[str, str] | None):
    """发送定时提醒：能拿到 @ 目标且渠道支持 hypertext 时，用原生 @（企微高亮），否则纯文本。

    返回 (send_result, db_content)：db_content 反映实际发出的内容（带 "@昵称 " 前缀或纯文本），
    用于落库展示与实际发送一致。原生 @ 发送失败则回退纯文本，保证提醒仍能送达。
    """
    channel = get_current_channel()
    if at_target and channel is not None and hasattr(channel, "send_hypertext"):
        user_id, name = at_target
        # subtype 1 = 原生 @ 成员(text 传 userId)；subtype 0 = 普通文本。与班主任 handoff 同一机制。
        content_list = [
            {"subtype": 1, "text": user_id},
            {"subtype": 0, "text": " " + content},
        ]
        db_content = f"@{name} {content}"
        last_result = None
        for attempt in range(1, CHANNEL_SEND_MAX_ATTEMPTS + 1):
            result = await channel.send_hypertext(chat_id, content_list)
            if result.success:
                return result, db_content
            last_result = result
            if not result.retryable or attempt >= CHANNEL_SEND_MAX_ATTEMPTS:
                break
            await asyncio.sleep(CHANNEL_SEND_RETRY_DELAY_SECONDS)
        logger.warning(
            "Reminder native @ send failed, falling back to plain text: chat_id=%s error=%s",
            chat_id,
            getattr(last_result, "error", None),
        )
    result = await _send_channel_text_with_retries(chat_id, content)
    return result, content


async def _recent_chat_lines(session, service_case_id: int, *, limit: int = 10) -> list[str]:
    result = await session.scalars(
        select(Message)
        .where(
            Message.service_case_id == service_case_id,
            or_(
                and_(
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
                    Message.purpose == MessagePurpose.CHAT,
                    Message.status == MessageStatus.RECEIVED,
                ),
                and_(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type.in_([MessageSenderType.BOT, MessageSenderType.STAFF]),
                    Message.purpose.in_([MessagePurpose.CHAT, MessagePurpose.DOCUMENT_DELIVERY]),
                    Message.status == MessageStatus.SENT,
                ),
            ),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return [_format_chat_line(item) for item in reversed(list(result))]


def _format_chat_line(message: Message) -> str:
    role = "用户"
    if message.direction == MessageDirection.OUTBOUND:
        role = "助教" if message.sender_type == MessageSenderType.BOT else "班主任"
    elif message.sender_type == MessageSenderType.STAFF:
        role = "班主任"
    content = " ".join((message.content or "").split())
    return f"{role}: {content[:300]}"


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


# action_focus 的内部策略词(label/action 是给 AI/助教看的教研用语)→ 发给学员的白话晨间提醒。
# 早 9 点按用户当前 current_focus.key 取对应这句发到群里。
_ACTION_FOCUS_STUDENT_MESSAGES: dict[str, str] = {
    "sleep_wind_down": "早上好呀！今天咱们把重点放在「睡前仪式」上，晚饭后让自己慢慢静下来，今晚试试 11 点前闭眼入睡，不额外加压力，加油！",
    "evening_control": "早上好！今天主攻晚间这一关：晚餐吃饱后就不再加餐，晚A 按你的方案放在晚上/睡前喝上。守住晚上的防线，今天就稳了！",
    "product_timing": "早上好！今天只抓产品时间：早C 跟早餐、午B 跟午餐；晚A 是晚间/睡前这杯，按你的既定方案喝，不要和午B混到一起。",
    "bowel_rhythm": "新的一天开始啦！今天给肠道减减负：多喝温水（目标 2L），午餐晚餐各加一拳绿叶菜，饭后散散步，保持肠道畅通！",
    "meal_structure": "早上好！今天吃饭抓个小重点：主食控制在拳头大小，搭配充足的蛋白质和蔬菜，两餐之间不吃零食。吃得营养又踏实！",
    "execution_simplify": "早上好！最近辛苦啦，今天给执行降降温：不用做太多复杂动作，挑最容易的一两个小动作做到就行。先放松心态，慢慢来！",
    "safety_review": "早上好！身体舒服是第一位的，今天先不追求高强度执行。如果有任何不适，随时在群里跟我说，我来帮你微调方案。",
    "motivation_support": "早上好呀！之前的每一步积累都很棒，今天放轻松，完成一个小小的打卡动作就是胜利，满分支持你！",
}


def _current_focus_key(state_json: object) -> str | None:
    """从画像 state_json 取当前行动重点的 key(domains.action_focus.current.value.current_focus.key)。"""
    if not isinstance(state_json, dict):
        return None
    domains = state_json.get("domains")
    if not isinstance(domains, dict):
        return None
    af = domains.get("action_focus")
    current = af.get("current") if isinstance(af, dict) else None
    value = current.get("value") if isinstance(current, dict) else None
    focus = value.get("current_focus") if isinstance(value, dict) else None
    if isinstance(focus, dict) and focus.get("key"):
        return str(focus.get("key"))
    return None


_STAGE_MILESTONES = ((3, "day3"), (7, "day7"), (14, "day14"), (21, "day21"), (28, "day28"))


def _due_stage_reports(
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
    existing_reports: set[tuple[int, str]],
) -> list[str]:
    """该 case 在 now 时刻应生成、且尚未生成的阶段/毕业报告 stage 列表。

    规则：仅在每天 23:00 集中产出（运营当晚集中审、用户次日早上看到）；「服务第几天」用
    effective_service_day（计划开始日当天=第1天，与看板/AI 口径一致）；service_day >= 里程碑
    且该 (case_id, stage) 未生成过才产出（用 >= 而非 == 提供漏扫/晚激活补偿，existing_reports 去重保证不重复）。
    """
    if now.hour != 23:
        return []
    service_day = effective_service_day(service_case, now, tz)
    due: list[str] = []
    for threshold, stage in _STAGE_MILESTONES:
        if service_day >= threshold and (service_case.id, stage) not in existing_reports:
            due.append(stage)
    if service_day >= 28 and (service_case.id, "graduation") not in existing_reports:
        due.append("graduation")
    return due


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
