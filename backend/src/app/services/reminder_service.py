from __future__ import annotations

import asyncio
import hashlib
import logging
import time as clock
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
from app.models.checkin_followup_cycle import CheckinFollowupCycle
from app.models.outcome import OutcomeSnapshot, OutcomeStageState
from app.models.reminder import ReminderEvent, ReminderEventType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentType, UserDocument
from app.services.agent import generate_scheduled_reminder_reply
from app.services.agent_observability import (
    COMPLETED as AGENT_RUN_COMPLETED,
    FAILED as AGENT_RUN_FAILED,
    complete_agent_run,
    ensure_agent_run,
)
from app.services.channels import get_current_channel, send_channel_text
from app.services.document_service import (
    generate_profile_and_documents,
    generate_stage_report,
    retry_degraded_stage_report,
)
from app.services.outcome_engine import generate_outcome_snapshot
from app.services.stage_report_settings import get_stage_report_schedule
from app.services.service_case_queries import (
    active_service_case_condition,
    checkin_day,
    effective_service_day,
    get_active_service_case,
    select_active_service_cases,
    service_continuation_active,
    service_phase_end_at,
    service_window_ended,
)
from app.services.service_case_activation import (
    PRE_SERVICE_STATUSES,
    activate_service_case_if_due,
    align_active_service_start_with_plan,
)


logger = logging.getLogger(__name__)
SCHEDULER_ADVISORY_LOCK_KEY = 2026051901
CHANNEL_SEND_MAX_ATTEMPTS = 3
CHANNEL_SEND_RETRY_DELAY_SECONDS = 1.0
_CHECKIN_FOLLOWUP_WINDOWS = {
    "daily_checklist": (time(9, 30), time(10, 30)),
    "contradiction": (time(16, 0), time(17, 0)),
    "milestone": (time(19, 30), time(20, 30)),
}


class ReminderService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._last_room_name_full_refresh: datetime | None = None
        self._last_image_cache_cleanup: datetime | None = None
        self._last_population_insight_check_date: date | None = None
        # 阶段报告生成时间配置缓存（5 分钟 TTL，避免白天每 30s 都查一次 DB）
        self._stage_report_schedule: tuple[int, int] | None = None
        self._stage_report_schedule_at: datetime | None = None

    async def start(self) -> None:
        optional_services_disabled = (
            not self.settings.reminder_enabled
            and not self.settings.agent_reminders_enabled
            and not self.settings.outcome_snapshots_enabled
            and not self.settings.checkin_followup_enabled
            and not self.settings.population_insight_enabled
        )
        if optional_services_disabled:
            logger.info(
                "Optional scheduled services are disabled; lifecycle maintenance remains enabled"
            )
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
                        logger.debug(
                            "Reminder scheduler skipped: advisory lock is held by another worker"
                        )
                    else:
                        await self.check_service_starts_once()
                        await self.check_service_phase_ends_once()
                        if self.settings.agent_reminders_enabled:
                            await self.check_agent_reminder_tasks_once()
                        if self.settings.reminder_enabled:
                            await self.check_stage_reports_once()
                        if self.settings.outcome_snapshots_enabled:
                            await self.check_outcome_snapshots_once()
                        await self.check_pending_intake_generation_once()
                        await self.check_stuck_document_generation_once()
                        await self.check_retry_degraded_reports_once()
                        await self.check_unsent_channel_messages_once()
                        await self.check_room_names_once()
                        await self.check_image_cache_cleanup_once()
                        if self.settings.archivist_enabled:
                            await self.check_archivist_once()
                        if self.settings.checkin_followup_enabled:
                            await self.check_checkin_followup_v2_once()
                        # 群体洞察可能有一次较长的 LLM 调用，放到循环末尾，
                        # 避免延迟阶段完结、提醒、补发等时效性任务。
                        if self.settings.population_insight_enabled:
                            await self.check_population_insight_once()
                        if (
                            self.settings.ai_balance_alert_enabled
                            and self.settings.ai_balance_alert_group_id
                        ):
                            await self.check_low_balance_once()
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

    async def check_low_balance_once(self) -> None:
        """查 DeepSeek 账户余额，跌破阈值/账户不可用时向指定群告警，每天最多一条(天闸)。

        天闸用 AIBalanceAlert(alert_date 唯一) 先插入抢占：发送成功才 commit 占住当天名额，
        发送失败则 rollback 释放，下一轮重试；跨自然日自动重置。查不到余额不误报。
        """
        from app.models.ai_balance_alert import AIBalanceAlert
        from app.services.ai_service.balance import fetch_deepseek_balance

        group_id = (self.settings.ai_balance_alert_group_id or "").strip()
        if not group_id:
            return
        snap = await fetch_deepseek_balance(self.settings, force=True)
        if snap is None or not snap.available or snap.balance_yuan is None:
            return  # 外部查询失败/不适用：不误报
        threshold = float(self.settings.ai_balance_alert_threshold_yuan or 0)
        if snap.balance_yuan >= threshold and snap.is_available:
            return  # 余额充足
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        alert_text = (
            "⚠️ DeepSeek 账户余额偏低\n"
            f"当前余额：¥{snap.balance_yuan:.2f}（阈值 ¥{threshold:.2f}）\n"
            "请尽快充值，以免影响 AI 回复、风控与文档生成。"
        )
        async with SessionLocal() as session:
            alert = AIBalanceAlert(alert_date=today, balance_yuan=snap.balance_yuan)
            try:
                async with session.begin_nested():
                    session.add(alert)
                    await session.flush()
            except IntegrityError:
                return  # 今天已发过
            result = await _send_channel_text_with_retries(group_id, alert_text)
            if result is not None and result.success:
                await session.commit()
                logger.info(
                    "low-balance alert sent balance=%.2f threshold=%.2f",
                    snap.balance_yuan,
                    threshold,
                )
            else:
                await session.rollback()  # 释放天闸，下一轮重试
                logger.warning(
                    "low-balance alert send failed, will retry next tick: %s",
                    getattr(result, "error", None),
                )

    async def check_population_insight_once(self, now: datetime | None = None) -> None:
        """每天只检查一次群体洞察刷新条件；普通画像变动不直接触发模型。"""

        if not self.settings.population_insight_enabled:
            return
        from app.services.population_insight import refresh_population_insight

        tz = ZoneInfo(self.settings.reminder_timezone)
        current = now or datetime.now(tz)
        if current.tzinfo is None:
            current = current.replace(tzinfo=tz)
        else:
            current = current.astimezone(tz)
        if current.hour < self.settings.population_insight_auto_hour:
            return
        if self._last_population_insight_check_date == current.date():
            return
        self._last_population_insight_check_date = current.date()
        async with SessionLocal() as session:
            result = await refresh_population_insight(
                session, now=current.astimezone(timezone.utc)
            )
        if result.ran:
            logger.info(
                "Population insight auto-refresh completed reason=%s snapshot_id=%s",
                result.reason,
                result.snapshot_id,
            )
        else:
            logger.debug(
                "Population insight auto-refresh skipped reason=%s", result.reason
            )

    async def check_service_phase_ends_once(self, now: datetime | None = None) -> None:
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
                service_case.ended_at = service_case.ended_at or service_phase_end_at(
                    service_case, timezone
                )
                changed = True
            if changed:
                await session.commit()

    async def check_service_starts_once(self, now: datetime | None = None) -> None:
        """Activate every submitted case whose local plan date has arrived."""
        tz = ZoneInfo(self.settings.reminder_timezone)
        current = now or datetime.now(tz)
        if current.tzinfo is None:
            current = current.replace(tzinfo=tz)
        else:
            current = current.astimezone(tz)
        async with SessionLocal() as session:
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status.in_(
                            (*PRE_SERVICE_STATUSES, ServiceCaseStatus.IN_SERVICE)
                        ),
                        ServiceCase.service_plan_start_date.is_not(None),
                        ServiceCase.service_plan_start_date <= current.date(),
                    )
                )
            )
            activated = 0
            aligned = 0
            for service_case in service_cases:
                if activate_service_case_if_due(service_case, now=current, tz=tz):
                    activated += 1
                elif align_active_service_start_with_plan(
                    service_case, now=current, tz=tz
                ):
                    aligned += 1
            if activated or aligned:
                await session.commit()
                logger.info(
                    "Reconciled service plan dates activated=%d aligned=%d",
                    activated,
                    aligned,
                )

    async def check_checkin_followup_v2_once(
        self,
        *,
        now: datetime | None = None,
    ) -> None:
        """Five-item daily review plus low-frequency measurements, capped at three sends per cycle."""
        if not self.settings.checkin_followup_enabled:
            return
        from app.services.archivist.checkin_projection import (
            mark_contradictions_pending_confirmation,
        )
        from app.services.archivist.followup import (
            finalize_checkin_followup_cycles,
            is_followup_paused,
            plan_scheduled_followups,
            render_scheduled_followup,
        )

        tz = ZoneInfo(self.settings.reminder_timezone)
        now = now or datetime.now(tz)
        today = now.date()
        async with SessionLocal() as session:
            await finalize_checkin_followup_cycles(session, today=today)
            service_cases = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                        ServiceCase.ai_auto_reply_enabled.is_(True),
                    )
                )
            )
            for service_case in service_cases:
                if checkin_day(service_case, now, tz) is None:
                    continue
                if await is_followup_paused(session, service_case_id=service_case.id):
                    continue
                cycle = await session.scalar(
                    select(CheckinFollowupCycle).where(
                        CheckinFollowupCycle.service_case_id == service_case.id,
                        CheckinFollowupCycle.cycle_date == today,
                    )
                )
                sent_kinds = list(cycle.sent_kinds or []) if cycle is not None else []
                plans = await plan_scheduled_followups(
                    session, service_case=service_case, now=now, tz=tz
                )
                for plan in plans:
                    if len(sent_kinds) >= 3 or plan.kind in sent_kinds:
                        continue
                    start, end = _CHECKIN_FOLLOWUP_WINDOWS[plan.kind]
                    target = _deterministic_followup_time(
                        service_case.id, today, plan.kind, start, end
                    )
                    if not (start <= now.time() < end) or now.time() < target:
                        continue
                    event = await _reserve_reminder_event(
                        session,
                        service_case.id,
                        rule_id=f"checkin_followup:{plan.kind}",
                        event_date=today,
                        event_type=ReminderEventType.REMINDED,
                    )
                    if event is None:
                        continue
                    followup = render_scheduled_followup(plan)
                    if cycle is None:
                        cycle = CheckinFollowupCycle(
                            service_case_id=service_case.id, cycle_date=today
                        )
                        session.add(cycle)
                        await session.flush()
                    context = {
                        "cycle_date": today.isoformat(),
                        "target_date": followup.target_date.isoformat(),
                        "kind": followup.kind,
                        "items": [
                            {
                                "item_id": item["id"],
                                "label": item["label"],
                                "cycle_date": today.isoformat(),
                                "target_date": followup.target_date.isoformat(),
                            }
                            for item in followup.expected_items
                        ],
                    }
                    message = Message(
                        service_case_id=service_case.id,
                        group_id=service_case.group_id,
                        direction=MessageDirection.OUTBOUND,
                        sender_type=MessageSenderType.SYSTEM,
                        message_type="markdown",
                        content=followup.text,
                        purpose=MessagePurpose.REMINDER,
                        status=MessageStatus.PENDING,
                        analysis_json={"checkin_followup": context},
                    )
                    session.add(message)
                    await session.flush()
                    result = await _send_channel_text_with_retries(
                        service_case.group_id, followup.text
                    )
                    if not result.success:
                        message.status, message.error = (
                            MessageStatus.FAILED,
                            result.error,
                        )
                        continue
                    message.status, message.wecom_message_id = (
                        MessageStatus.SENT,
                        result.message_id,
                    )
                    sent_kinds.append(followup.kind)
                    cycle.sent_kinds = sent_kinds
                    cycle.message_ids = list(cycle.message_ids or []) + [message.id]
                    if (
                        followup.kind == "contradiction"
                        and followup.confirmation_fields
                    ):
                        today_checkin = await session.scalar(
                            select(Checkin).where(
                                Checkin.service_case_id == service_case.id,
                                Checkin.checkin_date == today,
                                Checkin.deleted_at.is_(None),
                            )
                        )
                        if today_checkin is not None:
                            mark_contradictions_pending_confirmation(
                                today_checkin, followup.confirmation_fields
                            )
            await session.commit()

    async def check_agent_reminder_tasks_once(
        self, now: datetime | None = None
    ) -> None:
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
                # 「过点即弃」：提醒是时点绑定的，过点太久（退下期间到点 / 重启补跑积压）迟发反而
                # 内容过期、突然炸群打断当前对话，不如不发。丢弃这一次，但把循环序列顺延到下一次，
                # 避免 daily/weekly 因跳过而断链（单次 none 则序列自然结束）。
                stale_minutes = self.settings.reminder_stale_after_minutes
                if now_utc - task.remind_at > timedelta(minutes=stale_minutes):
                    task.status = AgentReminderTaskStatus.SKIPPED
                    task.error = (
                        f"stale: remind_at past by >{int(stale_minutes)}min, skipped"
                    )
                    await _schedule_next_recurrence(session, task, now, tz)
                    continue
                if not service_case.ai_auto_reply_enabled:
                    # 「退下」期间不主动发提醒；阈值内保持 PENDING 跳过本轮，用户「回来」后仍可准点补发；
                    # 若退下持续到过点太久，则下一轮由上面的「过点即弃」丢弃并顺延。
                    continue
                task.status = AgentReminderTaskStatus.SENDING
                task.error = None
                await session.flush()

                recent_messages = await _recent_chat_lines(
                    session, task.service_case_id
                )
                run_started_at = clock.monotonic()
                agent_run_id = await ensure_agent_run(
                    run_key=f"reminder:{task.id}",
                    agent_type="reply",
                    run_kind="scheduled_reminder",
                    service_case_id=task.service_case_id,
                    trigger_message_id=task.trigger_message_id,
                    trigger_reason="scheduled_reminder",
                    input_count=1,
                    metadata={"reminder_task_id": task.id},
                )
                try:
                    current_channel = get_current_channel()
                    agent_result = await generate_scheduled_reminder_reply(
                        service_case_id=task.service_case_id,
                        trigger_id=task.trigger_message_id,
                        reminder_instruction=task.content,
                        recent_messages=recent_messages,
                        channel=getattr(current_channel, "name", None),
                        settings=self.settings,
                        agent_run_id=agent_run_id,
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
                    logger.exception(
                        "Scheduled agent reminder generation failed task_id=%s", task.id
                    )
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = f"agent generation failed: {type(exc).__name__}: {exc}"
                    await complete_agent_run(
                        agent_run_id=agent_run_id,
                        status=AGENT_RUN_FAILED,
                        outcome="reminder_generation_failed",
                        latency_ms=int((clock.monotonic() - run_started_at) * 1000),
                        error_type=type(exc).__name__,
                    )
                    continue
                if not outbound_content:
                    task.status = AgentReminderTaskStatus.FAILED
                    task.error = "agent generation returned empty reply"
                    await complete_agent_run(
                        agent_run_id=agent_run_id,
                        status=AGENT_RUN_FAILED,
                        outcome="reminder_empty",
                        latency_ms=int((clock.monotonic() - run_started_at) * 1000),
                        error_type="EmptyReply",
                    )
                    continue

                # 提醒默认原生 @ 请求者（当初说“提醒我…”的那个用户）：从 trigger 消息解析其企微 userId。
                at_target = await _resolve_reminder_at_target(
                    session, task.trigger_message_id
                )

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

                result, sent_content = await _send_reminder_with_at(
                    task.group_id, outbound_content, at_target
                )
                message.content = sent_content
                if result and result.success:
                    message.status = MessageStatus.SENT
                    message.wecom_message_id = result.message_id
                    message.sent_at = datetime.now(timezone.utc)
                    task.status = AgentReminderTaskStatus.SENT
                    task.sent_message_id = message.id
                    task.sent_at = message.sent_at
                    # 循环提醒：本次成功发出后排下一条待发任务。
                    # 每条循环序列任一时刻只有 1 条 PENDING，用户取消这条即终止整个序列。
                    await _schedule_next_recurrence(session, task, now, tz)
                    await complete_agent_run(
                        agent_run_id=agent_run_id,
                        status=AGENT_RUN_COMPLETED,
                        outcome="reminder_sent",
                        latency_ms=int((clock.monotonic() - run_started_at) * 1000),
                        output_count=1,
                    )
                else:
                    message.status = MessageStatus.FAILED
                    message.error = result.error if result else "channel send failed"
                    task.status = AgentReminderTaskStatus.FAILED
                    task.sent_message_id = message.id
                    task.error = message.error
                    await complete_agent_run(
                        agent_run_id=agent_run_id,
                        status=AGENT_RUN_FAILED,
                        outcome="reminder_send_failed",
                        latency_ms=int((clock.monotonic() - run_started_at) * 1000),
                        error_type="ChannelSendFailed",
                    )
            await session.commit()

    async def check_pending_intake_generation_once(self) -> None:
        """消费入营问卷提交留下的"待生成"信号。

        提交入口(问卷 POST，可能跑在常驻 ingress 上)只落库并置
        intake_pending_generation_at，把重活解耦到这里，由 backend 进程内的本循环
        按 风险分析→画像/文档→欢迎语 的顺序生成。单实例 advisory lock 保证不并发重入。

        与运营 PATCH 重生成互不干扰：PATCH 走自带的 backend 后台任务、不置此信号，
        故不会被本循环重复消费或误发欢迎语。
        """
        if not self.settings.ai_api_key:
            return
        from app.api.routes.questionnaires import (
            _append_generation_error_note,
            _latest_submission,
            _send_intake_submit_reply,
        )
        from app.services.message_insight_service import (
            analyze_message_insight,
            merge_case_insight,
            questionnaire_content,
        )
        from app.services.questionnaire_template import get_active_questionnaire_fields

        async with SessionLocal() as session:
            pending = list(
                await session.scalars(
                    select_active_service_cases(
                        ServiceCase.intake_pending_generation_at.isnot(None)
                    )
                )
            )
        for pending_case in pending:
            service_case_id = pending_case.id
            reply_ready = False
            async with SessionLocal() as session:
                service_case = await get_active_service_case(session, service_case_id)
                if service_case is None or service_case.intake_pending_generation_at is None:
                    continue
                # 先清信号(一次性消费)：即便随后生成失败也不无限重试，失败会回落
                # QUESTIONNAIRE_SUBMITTED 交运营 PATCH 重触发；进程硬崩溃(信号已清、状态
                # 仍 GENERATING)由 check_stuck_document_generation_once 兜底补跑。
                service_case.intake_pending_generation_at = None
                await session.commit()
                try:
                    submission = await _latest_submission(session, service_case.id, "intake")
                    if submission is not None:
                        fields = await get_active_questionnaire_fields(session, "intake")
                        analysis = await analyze_message_insight(
                            source="questionnaire",
                            content=questionnaire_content(fields, submission.answers_json or {}),
                            context={
                                "service_case_id": service_case.id,
                                "case_no": service_case.case_no,
                                "template_key": "intake",
                            },
                        )
                        merge_case_insight(service_case, analysis)
                        await session.commit()
                    await generate_profile_and_documents(
                        session,
                        service_case,
                        auto_send=self.settings.auto_send_documents,
                    )
                    reply_ready = True
                except Exception as exc:
                    logger.exception(
                        "Pending intake generation failed service_case_id=%s",
                        service_case_id,
                    )
                    await session.rollback()
                    failed_case = await get_active_service_case(session, service_case_id)
                    if failed_case is not None:
                        _append_generation_error_note(failed_case, exc)
                        await session.commit()
            if reply_ready:
                # 欢迎语独立事务、失败不回滚已生成文档(与旧的 background task 语义一致)。
                try:
                    await _send_intake_submit_reply(service_case_id)
                except Exception:
                    logger.exception(
                        "Intake submit reply failed service_case_id=%s",
                        service_case_id,
                    )

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
                        session,
                        service_case,
                        auto_send=self.settings.auto_send_documents,
                    )
                except Exception:
                    logger.exception(
                        "Retry stuck document generation failed service_case_id=%s",
                        service_case.id,
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
                        UserDocument.document_type == DocumentType.STAGE_REPORT,
                        UserDocument.updated_at < threshold,
                    )
                )
            )
            for document in documents:
                cj = (
                    document.content_json
                    if isinstance(document.content_json, dict)
                    else {}
                )
                if not cj.get("degraded"):
                    continue
                if int(cj.get("degraded_retry_count", 0)) >= 5:
                    continue
                try:
                    await retry_degraded_stage_report(session, document)
                except Exception:
                    logger.exception(
                        "Retry degraded stage report failed doc_id=%s", document.id
                    )
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
                    logger.exception(
                        "Resend unsent channel message failed id=%s", message.id
                    )
                    continue
                if result.success:
                    message.status = MessageStatus.SENT
                    message.error = None
                    if result.message_id:
                        message.wecom_message_id = result.message_id
                    changed = True
            if changed:
                await session.commit()

    async def _generate_report_for_case_worker(self, case_id: int, stage: str) -> None:
        async with SessionLocal() as session:
            service_case = await session.get(ServiceCase, case_id)
            if not service_case:
                logger.error(f"Service case {case_id} not found in worker")
                return
            try:
                await generate_stage_report(session, service_case, stage=stage)
                logger.info(
                    f"Concurrently generated stage report {stage} for case {case_id}"
                )
            except Exception:
                logger.exception(
                    f"Failed to generate stage report {stage} for case {case_id}"
                )

    async def check_image_cache_cleanup_once(self, now: datetime | None = None) -> None:
        """清理过期的图片缓存(.wecom_cache/images)：保留最近 60 天，每天清理一次更老的文件。

        聊天记录显示的就是这些文件，保留期(覆盖 28 天服务周期 + 回看余量)内的图都正常显示，
        只清掉很久以前的；前端对已清理的图做"图片已过期"兜底。要调整保留期改 retention_days 即可。
        """
        now = now or datetime.now(ZoneInfo(self.settings.reminder_timezone))
        if (
            self._last_image_cache_cleanup
            and (now - self._last_image_cache_cleanup).total_seconds() < 24 * 3600
        ):
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
            logger.info(
                "Image cache cleanup removed %d expired files (>%d days)",
                removed,
                retention_days,
            )

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
            logger.info(
                "Refreshed %d service-case group names (full_refresh=%s)",
                updated,
                full_refresh,
            )

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

            # Pre-fetch all stage reports for active cases in one query.
            documents = list(
                await session.scalars(
                    select(UserDocument).where(
                        UserDocument.service_case_id.in_(case_ids),
                        UserDocument.document_type == DocumentType.STAGE_REPORT,
                    )
                )
            )
            existing_reports = set()
            for doc in documents:
                if isinstance(doc.content_json, dict) and doc.content_json.get("stage"):
                    existing_reports.add(
                        (doc.service_case_id, doc.content_json.get("stage"))
                    )

        # Define the task list for parallel execution using distinct sessions
        tasks = []
        for service_case in service_cases:
            for stage in _due_stage_reports(
                service_case,
                now,
                tz,
                existing_reports,
                generate_hour=gen_hour,
                generate_minute=gen_minute,
            ):
                tasks.append(
                    self._generate_report_for_case_worker(service_case.id, stage)
                )

        if tasks:
            logger.info(
                f"Concurrently generating {len(tasks)} stage reports via scheduler"
            )
            # 分批并发：21:00 集中生成时，在服务的用户可能有几十个，一次性 gather 会瞬时打爆 LLM
            # 并发/限流。阶段报告是多段落生成、比成效快照更重，批量取更小的 3。
            batch_size = 3
            for i in range(0, len(tasks), batch_size):
                await asyncio.gather(*tasks[i : i + batch_size], return_exceptions=True)

    async def _generate_outcome_snapshot_for_case_worker(
        self, case_id: int, milestone: str
    ) -> None:
        async with SessionLocal() as session:
            service_case = await session.get(ServiceCase, case_id)
            if not service_case:
                logger.error(
                    "Service case %s not found in outcome snapshot worker", case_id
                )
                return
            try:
                await generate_outcome_snapshot(
                    session, service_case, milestone=milestone
                )
                await session.commit()
                logger.info(
                    "Generated outcome snapshot %s for case %s", milestone, case_id
                )
            except Exception:
                logger.exception(
                    "Failed to generate outcome snapshot %s for case %s",
                    milestone,
                    case_id,
                )

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
                    select(OutcomeSnapshot).where(
                        OutcomeSnapshot.service_case_id.in_(case_ids)
                    )
                )
            )
            snapshot_map = {
                (item.service_case_id, item.milestone): item for item in snapshots
            }
            states = list(
                await session.scalars(
                    select(OutcomeStageState).where(
                        OutcomeStageState.service_case_id.in_(case_ids)
                    )
                )
            )
            state_map = {
                (item.service_case_id, item.milestone): item for item in states
            }

        tasks = []
        for service_case in service_cases:
            days = max(
                effective_service_day(
                    service_case,
                    now,
                    ZoneInfo(self.settings.reminder_timezone),
                ),
                1,
            )
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
                should_refresh = snapshot is None or (
                    state is not None
                    and (
                        snapshot.generated_at is None
                        or state.updated_at > snapshot.generated_at
                    )
                )
                if days >= threshold and should_refresh:
                    tasks.append(
                        self._generate_outcome_snapshot_for_case_worker(
                            service_case.id, milestone
                        )
                    )
        if tasks:
            logger.info("Generating %s outcome snapshots via scheduler", len(tasks))
            batch_size = 5
            for i in range(0, len(tasks), batch_size):
                await asyncio.gather(*tasks[i : i + batch_size], return_exceptions=True)


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
        last_result.error = (
            f"{last_result.error or 'channel send failed'} (attempts={attempt})"
        )
        logger.warning(
            "Reminder channel send failed after retries: chat_id=%s attempts=%s error=%s",
            chat_id,
            attempt,
            last_result.error,
        )
    return last_result


async def _resolve_reminder_at_target(
    session, trigger_message_id: int | None
) -> tuple[str, str] | None:
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
    meta = (
        analysis.get("_qiwe_meta")
        if isinstance(analysis.get("_qiwe_meta"), dict)
        else {}
    )
    user_id = str(meta.get("sender_id") or "").strip()
    if not user_id:
        return None
    name = str(meta.get("sender_name") or "").strip() or user_id
    return user_id, name


async def _send_reminder_with_at(
    chat_id: str, content: str, at_target: tuple[str, str] | None
):
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


def _next_recurrence_at(
    remind_at: datetime, recurrence: str, now: datetime, tz: ZoneInfo
) -> datetime | None:
    """按循环规则算出严格晚于 now 的下一次触发时间（本地时区推进，跳过已错过的时段）。

    daily=+1天，weekly=+7天；带迭代上限防呆（调度器长时间停机也不会死循环）。
    """
    step = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}.get(recurrence)
    if step is None:
        return None
    base = (
        remind_at.astimezone(tz)
        if remind_at.tzinfo
        else remind_at.replace(tzinfo=timezone.utc).astimezone(tz)
    )
    now_local = now.astimezone(tz)
    nxt = base + step
    for _ in range(400):  # 上限覆盖 >1 年的 daily，正常只推进 1 次
        if nxt > now_local:
            return nxt
        nxt += step
    return None


async def _schedule_next_recurrence(session, task, now: datetime, tz: ZoneInfo) -> None:
    """循环提醒成功发出或过点跳过后，新建一条 PENDING 续排。"""
    if task.recurrence not in {"daily", "weekly"}:
        return
    next_at = _next_recurrence_at(task.remind_at, task.recurrence, now, tz)
    if next_at is None:
        return
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


async def _recent_chat_lines(
    session, service_case_id: int, *, limit: int = 10
) -> list[str]:
    result = await session.scalars(
        select(Message)
        .where(
            Message.service_case_id == service_case_id,
            or_(
                and_(
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type.in_(
                        [MessageSenderType.USER, MessageSenderType.STAFF]
                    ),
                    Message.purpose == MessagePurpose.CHAT,
                    Message.status == MessageStatus.RECEIVED,
                ),
                and_(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type.in_(
                        [MessageSenderType.BOT, MessageSenderType.STAFF]
                    ),
                    Message.purpose.in_(
                        [MessagePurpose.CHAT, MessagePurpose.DOCUMENT_DELIVERY]
                    ),
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
                    text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(
                        key=SCHEDULER_ADVISORY_LOCK_KEY
                    )
                )
            )
            yield acquired
        else:
            async with session.begin():
                acquired = bool(
                    await session.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(
                            key=SCHEDULER_ADVISORY_LOCK_KEY
                        )
                    )
                )
                yield acquired


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


def _deterministic_followup_time(
    case_id: int, day: date, kind: str, start: time, end: time
) -> time:
    """Like the legacy slot function, but independent categories must not all land together."""
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    if end_min <= start_min:
        return start
    digest = hashlib.md5(f"{case_id}:{day.isoformat()}:{kind}".encode()).hexdigest()
    total = start_min + int(digest, 16) % (end_min - start_min)
    return time(total // 60, total % 60)


def _parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip().isdigit()]


_STAGE_MILESTONES = (
    (3, "day3"),
    (7, "day7"),
    (14, "day14"),
    (21, "day21"),
    (28, "day28"),
)


def _due_stage_reports(
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
    existing_reports: set[tuple[int, str]],
    *,
    generate_hour: int = 23,
    generate_minute: int = 0,
) -> list[str]:
    """该 case 在 now 时刻应生成、且尚未生成的阶段报告 stage 列表。

    规则：仅在配置的生成时间集中产出（运营当晚集中审、用户次日早上看到）；「服务第几天」用
    effective_service_day（计划开始日当天=第1天，与看板/AI 口径一致）；service_day >= 里程碑
    且该 (case_id, stage) 未生成过才产出（用 >= 而非 == 提供漏扫/晚激活补偿，existing_reports 去重保证不重复）。

    generate_hour/minute 由 check_stage_reports_once 从运营规则配置注入（生产默认 21:00）；
    默认值保留 23 供纯函数单测直接调用（勿改，测试依赖）。
    """
    if now.hour != generate_hour or now.minute < generate_minute:
        return []
    # 续服沿用原服务天数，不开启新一期，因此不回头补生成标准 28 天阶段报告。
    if service_continuation_active(service_case):
        return []
    service_day = effective_service_day(service_case, now, tz)
    due: list[str] = []
    for threshold, stage in _STAGE_MILESTONES:
        if (
            service_day >= threshold
            and (service_case.id, stage) not in existing_reports
        ):
            due.append(stage)
    return due


def _service_day(started_at: datetime | None, now: datetime, timezone: ZoneInfo) -> int:
    if started_at is None:
        return 1
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=ZoneInfo("UTC"))
    started_local = started_at.astimezone(timezone)
    return max((now.date() - started_local.date()).days + 1, 1)
