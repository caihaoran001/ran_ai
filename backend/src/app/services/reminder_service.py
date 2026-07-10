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
from app.services.stage_report_settings import get_stage_report_schedule
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
        # 阶段报告生成时间配置缓存（5 分钟 TTL，避免白天每 30s 都查一次 DB）
        self._stage_report_schedule: tuple[int, int] | None = None
        self._stage_report_schedule_at: datetime | None = None

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
                            # check_once（12点泛化催打卡）已退役，并入晚间智能追问 check_checkin_followup_once。
                            await self.check_stage_reports_once()
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
                        if self.settings.archivist_enabled:
                            await self.check_archivist_once()
                        if self.settings.checkin_followup_enabled:
                            await self.check_checkin_followup_once()
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

    async def check_archivist_once(self) -> None:
        """档案官周期扫描：DB 派生找到点的服务单，跑聊天转打卡。触发/幂等/防双跑见 archivist.trigger。"""
        from app.services.archivist.trigger import run_archivist_scan

        stats = await run_archivist_scan()
        if stats.get("ran"):
            logger.info("Archivist scan stats=%s", stats)

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
        """【已退役】12点泛化文字催打卡（"还没收到打卡请补一下"）。

        H5 下线后，只会泛泛催、不问具体字段，且与晚间智能追问 check_checkin_followup_once 重叠且更笨，
        故已从 _run_loop 摘除调用。方法体暂留以防关联引用，不再被调度。
        """
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

    async def check_checkin_followup_once(self) -> None:
        """打卡单发线：只负责需"保证送达"的两件事——里程碑复盘 + 矛盾核对，晚间群内发一次。

        会向真实群发消息，受 CHECKIN_FOLLOWUP_ENABLED 门控，每单每天最多一条
        (rule_id=checkin_followup 去重)，不发链接。里程碑复盘在服务第 6/13/20/27 天由 LLM
        生成(失败自回退模板)，赶在里程碑当晚阶段报告前采集腰围/复盘；矛盾核对含标 asked、
        降级来源的状态机。日常缺口(早C/午B/晚A、体重)已改由聊天机器人「今日待补」搭便车承担，
        不再由本单发线冷推(见 archivist.followup.compute_gap_hint)。
        """
        if not self.settings.checkin_followup_enabled:
            return
        from app.services.archivist.checkin_projection import (
            mark_contradictions_pending_confirmation,
        )
        from app.services.archivist.followup import (
            compose_followup_message,
            compose_milestone_followup,
            detect_followup,
        )

        tz = ZoneInfo(self.settings.reminder_timezone)
        now = datetime.now(tz)
        if now.isoweekday() not in _parse_weekdays(self.settings.reminder_weekdays):
            return
        if now.time() < _parse_time(self.settings.checkin_followup_at):
            return
        today = now.date()

        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE
                    )
                )
            )
            events = list(
                await session.scalars(
                    select(ReminderEvent).where(
                        ReminderEvent.rule_id == "checkin_followup",
                        ReminderEvent.event_date == today,
                        ReminderEvent.event_type == ReminderEventType.REMINDED,
                    )
                )
            )
            reminded = {e.service_case_id for e in events}

            for service_case in service_cases:
                if service_case.id in reminded:
                    continue
                day = checkin_day(service_case, now, tz)
                if day is None or day > SERVICE_CASE_WINDOW_DAYS:
                    continue
                detect = await detect_followup(
                    session, service_case=service_case, now=now, tz=tz
                )
                milestone = detect.get("milestone")
                confirmations = detect.get("confirmations") or []
                # 单发线只负责两件"需保证送达"的事：里程碑复盘（LLM，失败自回退模板）+ 矛盾核对
                # （需标 asked、降级来源的状态机）。日常缺口（早C/午B/晚A、体重）已改由聊天机器人
                # 「今日待补」搭便车在自然对话里承担，此处不再冷推。
                parts: list[str] = []
                if milestone:
                    parts.append(
                        await compose_milestone_followup(
                            service_case=service_case,
                            milestone_day=milestone["milestone_day"],
                            need_waist=milestone["need_waist"],
                        )
                    )
                conf_msg = compose_followup_message(confirmations=confirmations)
                if conf_msg:
                    parts.append(conf_msg)
                content = " ".join(p for p in parts if p)
                if not content:
                    continue
                reminder_event = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id="checkin_followup",
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reminder_event:
                    continue
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
                    # 追问已发出的矛盾：把这些字段降级为 user_confirmed 并标 asked，
                    # 使用户随后报的新值覆盖旧表单值、且不再重复追问。
                    if confirmations:
                        today_checkin = await session.scalar(
                            select(Checkin).where(
                                Checkin.service_case_id == service_case.id,
                                Checkin.checkin_date == today,
                                Checkin.deleted_at.is_(None),
                            )
                        )
                        if today_checkin is not None:
                            mark_contradictions_pending_confirmation(
                                today_checkin, [c["field"] for c in confirmations]
                            )
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
                    current_channel = get_current_channel()
                    agent_result = await generate_scheduled_reminder_reply(
                        service_case_id=task.service_case_id,
                        trigger_id=task.trigger_message_id,
                        reminder_instruction=task.content,
                        recent_messages=recent_messages,
                        channel=getattr(current_channel, "name", None),
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
                    # 循环提醒：本次成功发出后，若下一次仍落在服务窗口内，排下一条待发任务。
                    # 每条循环序列任一时刻只有 1 条 PENDING，用户取消这条即终止整个序列。
                    await _schedule_next_recurrence(session, task, service_case, now, tz)
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error if result else "channel send failed"
                    task.status = AgentReminderTaskStatus.FAILED
                    task.sent_message_id = message.id
                    task.error = message.error
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
        # 日打卡 H5 已下线：群内反复发外链触发风控，改由档案官从聊天抽取打卡。
        # 默认不再推送；入营问卷走独立命令路径，不受影响。
        if not self.settings.daily_checkin_link_enabled:
            return
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

    async def _resolve_stage_report_time(self, now: datetime) -> tuple[int, int]:
        """阶段报告生成的 (hour, minute)，来自运营规则配置；带 5 分钟 TTL 缓存。

        缓存是为了保留原设计「白天不碰 DB」的意图——调度每 30s 跑一次，不必每次都查配置。
        读不到配置时由 get_stage_report_schedule 兜底默认 21:00。
        """
        cached_at = self._stage_report_schedule_at
        if self._stage_report_schedule is not None and cached_at is not None:
            if (now - cached_at).total_seconds() < 300:
                return self._stage_report_schedule
        async with SessionLocal() as session:
            schedule = await get_stage_report_schedule(session, settings=self.settings)
        resolved = (schedule.hour, schedule.minute)
        self._stage_report_schedule = resolved
        self._stage_report_schedule_at = now
        return resolved

    async def check_stage_reports_once(self, now: datetime | None = None) -> None:
        tz = ZoneInfo(self.settings.reminder_timezone)
        now = now or datetime.now(tz)
        # 阶段报告在运营规则配置的生成时间（默认北京时间 21:00）集中生成：运营当晚集中审核、用户次日早上看到。
        # 调度每 30s 跑一次，到点这一小时会扫到多次，靠下方 existing_reports 去重保证每个 stage 当晚只生成一次。
        # 未到点直接返回，白天不产出（配置读取带缓存，不会每 30s 打一次 DB）。
        gen_hour, gen_minute = await self._resolve_stage_report_time(now)
        if now.hour != gen_hour or now.minute < gen_minute:
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
            for stage in _due_stage_reports(
                service_case, now, tz, existing_reports, generate_hour=gen_hour, generate_minute=gen_minute
            ):
                tasks.append(self._generate_report_for_case_worker(service_case.id, stage))

        if tasks:
            logger.info(f"Concurrently generating {len(tasks)} stage reports via scheduler")
            # 分批并发：21:00 集中生成时，在服务的用户可能有几十个，一次性 gather 会瞬时打爆 LLM
            # 并发/限流。阶段报告是多段落生成、比成效快照更重，批量取更小的 3。
            batch_size = 3
            for i in range(0, len(tasks), batch_size):
                await asyncio.gather(*tasks[i:i + batch_size], return_exceptions=True)

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
            # 每日 FAQ 扫描已收编进档案官(delta 抽取时顺手产出 FAQ 候选)，此处不再独立扫。
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


def _next_recurrence_at(remind_at: datetime, recurrence: str, now: datetime, tz: ZoneInfo) -> datetime | None:
    """按循环规则算出严格晚于 now 的下一次触发时间（本地时区推进，跳过已错过的时段）。

    daily=+1天，weekly=+7天；带迭代上限防呆（调度器长时间停机也不会死循环）。
    """
    step = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}.get(recurrence)
    if step is None:
        return None
    base = remind_at.astimezone(tz) if remind_at.tzinfo else remind_at.replace(tzinfo=timezone.utc).astimezone(tz)
    now_local = now.astimezone(tz)
    nxt = base + step
    for _ in range(400):  # 上限覆盖 >1 年的 daily，正常只推进 1 次
        if nxt > now_local:
            return nxt
        nxt += step
    return None


async def _schedule_next_recurrence(session, task, service_case, now: datetime, tz: ZoneInfo) -> None:
    """循环提醒成功发出后，若下一次仍在服务窗口内，新建一条 PENDING 续排；否则序列自然结束。"""
    if task.recurrence not in {"daily", "weekly"}:
        return
    next_at = _next_recurrence_at(task.remind_at, task.recurrence, now, tz)
    if next_at is None:
        return
    if service_window_ended(service_case, next_at, tz):
        return  # 下一次已越过服务窗口，停止循环
    session.add(
        AgentReminderTask(
            service_case_id=task.service_case_id,
            group_id=task.group_id,
            trigger_message_id=task.trigger_message_id,
            remind_at=next_at.astimezone(timezone.utc),
            content=task.content,
            recurrence=task.recurrence,
            status=AgentReminderTaskStatus.PENDING,
        )
    )


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


_STAGE_MILESTONES = ((3, "day3"), (7, "day7"), (14, "day14"), (21, "day21"), (28, "day28"))


def _due_stage_reports(
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
    existing_reports: set[tuple[int, str]],
    *,
    generate_hour: int = 23,
    generate_minute: int = 0,
) -> list[str]:
    """该 case 在 now 时刻应生成、且尚未生成的阶段/毕业报告 stage 列表。

    规则：仅在配置的生成时间集中产出（运营当晚集中审、用户次日早上看到）；「服务第几天」用
    effective_service_day（计划开始日当天=第1天，与看板/AI 口径一致）；service_day >= 里程碑
    且该 (case_id, stage) 未生成过才产出（用 >= 而非 == 提供漏扫/晚激活补偿，existing_reports 去重保证不重复）。

    generate_hour/minute 由 check_stage_reports_once 从运营规则配置注入（生产默认 21:00）；
    默认值保留 23 供纯函数单测直接调用（勿改，测试依赖）。
    """
    if now.hour != generate_hour or now.minute < generate_minute:
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
