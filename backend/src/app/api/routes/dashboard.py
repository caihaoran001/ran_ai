from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.models.ai_usage import AIUsageLog
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.outcome import OutcomeEvent, OutcomeSnapshot
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.schemas.dashboard import AICostCaseItem, AICostSummary, DashboardMetric, DashboardSummary, OutcomeDashboardSummary
from app.services.service_case_queries import active_service_case_condition

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    total = await session.scalar(
        select(func.count(ServiceCase.id)).where(active_service_case_condition())
    )
    in_service = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
        )
    )
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    today_start = datetime.combine(today, time.min, tzinfo=china_tz).astimezone(timezone.utc)
    today_end = today_start + timedelta(days=1)
    checkins_today = await session.scalar(
        select(func.count(Checkin.id))
        .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            Checkin.checkin_date == today,
        )
    )
    messages_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            Message.created_at >= today_start,
            Message.created_at < today_end,
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == MessageSenderType.USER,
        )
    ) or 0
    messages_outbound_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            Message.created_at >= today_start,
            Message.created_at < today_end,
            Message.direction == MessageDirection.OUTBOUND,
        )
    ) or 0
    # High-performance DISTINCT ON DB-level count of the latest documents to prevent loading all documents in memory
    from sqlalchemy import text
    doc_subq = (
        select(UserDocument.status)
        .distinct(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'")
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(active_service_case_condition())
        .order_by(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
            UserDocument.created_at.desc()
        )
        .subquery()
    )
    pending_documents = await session.scalar(
        select(func.count())
        .select_from(doc_subq)
        .where(doc_subq.c.status.in_([DocumentStatus.DRAFT, DocumentStatus.REVIEWING]))
    ) or 0
    new_users_today = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.created_at >= today_start,
            ServiceCase.created_at < today_end,
        )
    )
    risk_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.risk_level == "high",
        )
    )
    human_intervention_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.needs_human_intervention.is_(True),
        )
    )
    now = datetime.now(timezone.utc)
    last_message_subq = (
        select(Message.service_case_id, func.max(Message.created_at).label("last_at"))
        .where(Message.direction == MessageDirection.INBOUND)
        .group_by(Message.service_case_id)
        .subquery()
    )
    last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
    unfollowed_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=2),
            last_activity_at >= now - timedelta(days=7),
        )
    )
    lost_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=7),
        )
    )
    renewal_followup_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at <= now - timedelta(days=18),
            ServiceCase.started_at >= now - timedelta(days=28),
        )
    )
    renewal_intent_users = await _count_flagged_cases(
        session,
        ServiceCase.renewal_intent_flags.is_not(None),
    )
    refund_intent_users = await _count_flagged_cases(
        session,
        ServiceCase.refund_intent_flags.is_not(None),
    )

    today_user_messages = list(
        await session.scalars(
            select(Message)
            .join(ServiceCase, Message.service_case_id == ServiceCase.id)
            .where(
                active_service_case_condition(),
                Message.created_at >= today_start,
                Message.created_at < today_end,
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
            )
        )
    )
    emotional_users_today = len(
        {
            message.service_case_id
            for message in today_user_messages
            if message.service_case_id and _has_emotion_signal(message)
        }
    )
    outcome_summary = await _build_outcome_summary(
        session,
        total_cases=total or 0,
        renewal_intent_users=renewal_intent_users,
        refund_intent_users=refund_intent_users,
        now=now,
    )
    ai_cost_summary = await _build_ai_cost_summary(session)

    return DashboardSummary(
        service_cases_total=total or 0,
        service_cases_in_service=in_service or 0,
        checkins_today=checkins_today or 0,
        messages_today=messages_today or 0,
        messages_outbound_today=messages_outbound_today or 0,
        pending_documents=pending_documents or 0,
        new_users_today=new_users_today or 0,
        risk_users=risk_users or 0,
        human_intervention_users=human_intervention_users or 0,
        unfollowed_users=unfollowed_users or 0,
        renewal_followup_users=renewal_followup_users or 0,
        repurchase_soon_users=renewal_followup_users or 0,
        renewal_intent_users=renewal_intent_users,
        refund_intent_users=refund_intent_users,
        lost_users=lost_users or 0,
        emotional_users_today=emotional_users_today or 0,
        outcome_summary=outcome_summary,
        ai_cost_summary=ai_cost_summary,
    )


@router.get("/ai-costs", response_model=list[AICostCaseItem])
async def dashboard_ai_costs(
    session: AsyncSession = Depends(get_session),
) -> list[AICostCaseItem]:
    ai_cost_expr = _ai_cost_expr()
    rows = await session.execute(
        select(
            ServiceCase.id,
            ServiceCase.case_no,
            ServiceCase.user_id,
            User.name,
            ServiceCase.group_id,
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
            ai_cost_expr,
            func.max(AIUsageLog.created_at),
        )
        .join(ServiceCase, AIUsageLog.service_case_id == ServiceCase.id)
        .outerjoin(User, ServiceCase.user_id == User.id)
        .where(active_service_case_condition())
        .group_by(ServiceCase.id, ServiceCase.case_no, ServiceCase.user_id, User.name, ServiceCase.group_id)
        .order_by(ai_cost_expr.desc(), func.max(AIUsageLog.created_at).desc())
    )
    return [
        AICostCaseItem(
            service_case_id=service_case_id,
            case_no=case_no,
            user_id=user_id,
            user_name=user_name,
            group_id=group_id,
            total_calls=total_calls or 0,
            total_tokens=total_tokens or 0,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            estimated_cost_yuan=round(float(cost or 0), 6),
            last_call_at=last_call_at,
        )
        for (
            service_case_id,
            case_no,
            user_id,
            user_name,
            group_id,
            total_calls,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            cost,
            last_call_at,
        ) in rows
    ]


def _has_emotion_signal(message: Message) -> bool:
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
    if analysis is not None:
        emotion_signal = analysis.get("emotion_signal")
        if isinstance(emotion_signal, dict):
            return bool(emotion_signal.get("detected"))
        return _analysis_has_emotion_code(analysis)
    return False


async def _build_ai_cost_summary(session: AsyncSession) -> AICostSummary:
    ai_cost_expr = _ai_cost_expr()
    row = await session.execute(
        select(
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
            ai_cost_expr,
        )
        .outerjoin(ServiceCase, AIUsageLog.service_case_id == ServiceCase.id)
        .where((AIUsageLog.service_case_id.is_(None)) | active_service_case_condition())
    )
    total_calls, total_tokens, prompt_tokens, completion_tokens, cost = row.one()
    return AICostSummary(
        total_calls=total_calls or 0,
        total_tokens=total_tokens or 0,
        prompt_tokens=prompt_tokens or 0,
        completion_tokens=completion_tokens or 0,
        estimated_cost_yuan=round(float(cost or 0), 6),
    )


def _ai_cost_expr():
    settings = get_settings()
    prompt_cost_per_1m = max(float(settings.ai_prompt_cost_per_1m_tokens or 0), 0.0)
    completion_cost_per_1m = max(float(settings.ai_completion_cost_per_1m_tokens or 0), 0.0)
    return (
        func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0) * prompt_cost_per_1m / 1_000_000.0
        + func.coalesce(func.sum(AIUsageLog.completion_tokens), 0)
        * completion_cost_per_1m
        / 1_000_000.0
    )


def _analysis_has_emotion_code(analysis: dict) -> bool:
    emotion_codes = {"emotional_distress", "depression", "self_harm"}
    for key in ("intervention_reasons", "risk_flags"):
        values = analysis.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("code") in emotion_codes:
                return True
    return False

async def _count_flagged_cases(session: AsyncSession, condition) -> int:
    count = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            condition,
        )
    )
    return count or 0


async def _build_outcome_summary(
    session: AsyncSession,
    *,
    total_cases: int,
    renewal_intent_users: int,
    refund_intent_users: int,
    now: datetime,
) -> OutcomeDashboardSummary:
    active_cases = total_cases
    day7_eligible = await _eligible_case_count(session, now=now, day=7)
    day28_eligible = await _eligible_case_count(session, now=now, day=28)

    profile_cases = await _distinct_active_case_count(session, UserProfile.service_case_id)
    core_document_cases = await _core_document_case_count(session)
    day7_snapshots = await _snapshot_case_count(session, milestone="day7", now=now, day=7)
    day28_complete_checkins = await _checkin_completion_case_count(session, now=now, day=28, minimum_days=20)
    result_signal_cases = await _outcome_event_case_count(
        session,
        {
            "weight_change",
            "waist_or_body_change",
            "weight_result",
            "waist_result",
            "challenge_goal_result",
        },
    )
    body_feedback_cases = await _outcome_event_case_count(
        session,
        {
            "energy_change",
            "sleep_change",
            "bowel_change",
            "positive_body_signal",
        },
    )
    discomfort_cases = await _outcome_event_case_count(session, {"discomfort_feedback"})
    referral_cases = await _outcome_event_case_count(session, {"referral_signal"})
    day28_weight_avg, day28_weight_count, day28_waist_avg, day28_waist_count = await _day28_change_averages(
        session,
        now=now,
    )

    return OutcomeDashboardSummary(
        active_cases=active_cases,
        eligible_day7_cases=day7_eligible,
        eligible_day28_cases=day28_eligible,
        metrics=[
            _rate_metric(
                key="profile_completion_rate",
                label="建档完成率",
                numerator=profile_cases,
                denominator=active_cases,
                description="已有用户画像的服务单占比，用来判断获客数据与建档质量。",
            ),
            _rate_metric(
                key="core_document_generation_rate",
                label="核心交付生成率",
                numerator=core_document_cases,
                denominator=active_cases,
                description="已生成两份核心交付文件的服务单占比，用来判断系统承接效率。",
            ),
            _rate_metric(
                key="day7_snapshot_rate",
                label="7日成效快照覆盖率",
                numerator=day7_snapshots,
                denominator=day7_eligible,
                description="已到第7天的服务单中，完成阶段成效沉淀的占比。",
            ),
            _rate_metric(
                key="day28_checkin_completion_rate",
                label="28天打卡完成率",
                numerator=day28_complete_checkins,
                denominator=day28_eligible,
                description="已到第28天的服务单中，至少沉淀20天打卡记录的占比。",
            ),
            _rate_metric(
                key="result_signal_rate",
                label="体重/围度/目标结果记录率",
                numerator=result_signal_cases,
                denominator=active_cases,
                description="聊天或阶段快照中已沉淀体重、围度或目标结果证据的用户占比。",
            ),
            _rate_metric(
                key="body_feedback_rate",
                label="睡眠/排便/能量反馈率",
                numerator=body_feedback_cases,
                denominator=active_cases,
                description="已收集到睡眠、排便、精神能量等体感反馈证据的用户占比。",
            ),
            _rate_metric(
                key="discomfort_feedback_rate",
                label="不适反馈率",
                numerator=discomfort_cases,
                denominator=active_cases,
                description="用户明确反馈心慌、失眠、不舒服等不适线索的占比，越高越需要人工关注。",
            ),
            _rate_metric(
                key="renewal_intent_rate",
                label="明确复购意向率",
                numerator=renewal_intent_users,
                denominator=active_cases,
                description="用户聊天中明确表达继续购买、续期或推荐购买的占比。",
            ),
            _rate_metric(
                key="refund_intent_rate",
                label="退款/退出意向率",
                numerator=refund_intent_users,
                denominator=active_cases,
                description="用户聊天中明确表达退款、退群、不继续等意向的占比。",
            ),
            _rate_metric(
                key="referral_signal_rate",
                label="推荐线索率",
                numerator=referral_cases,
                denominator=active_cases,
                description="聊天中出现推荐朋友、同事或家人参与的真实线索占比。",
            ),
            _value_metric(
                key="avg_day28_weight_change",
                label="28天平均体重变化",
                value=day28_weight_avg,
                unit="kg",
                sample_count=day28_weight_count,
                description="来自第28天阶段快照的体重变化均值；样本不足时不展示结论。",
            ),
            _value_metric(
                key="avg_day28_waist_change",
                label="28天平均腰围变化",
                value=day28_waist_avg,
                unit="cm",
                sample_count=day28_waist_count,
                description="来自第28天阶段快照的腰围变化均值；样本不足时不展示结论。",
            ),
        ],
    )


async def _eligible_case_count(session: AsyncSession, *, now: datetime, day: int) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    count = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
        )
    )
    return count or 0


async def _distinct_active_case_count(session: AsyncSession, service_case_column) -> int:
    count = await session.scalar(
        select(func.count(distinct(service_case_column)))
        .join(ServiceCase, service_case_column == ServiceCase.id)
        .where(active_service_case_condition())
    )
    return count or 0


async def _core_document_case_count(session: AsyncSession) -> int:
    doc_counts = (
        select(
            UserDocument.service_case_id.label("service_case_id"),
            func.count(distinct(UserDocument.document_type)).label("document_count"),
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            UserDocument.document_type.in_(
                [
                    DocumentType.ENERGY_RESTART_PRESCRIPTION,
                    DocumentType.DAY28_EXECUTION_STANDARD,
                ]
            ),
        )
        .group_by(UserDocument.service_case_id)
        .subquery()
    )
    count = await session.scalar(
        select(func.count()).select_from(doc_counts).where(doc_counts.c.document_count >= 2)
    )
    return count or 0


async def _snapshot_case_count(session: AsyncSession, *, milestone: str, now: datetime, day: int) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    count = await session.scalar(
        select(func.count(distinct(ServiceCase.id)))
        .join(OutcomeSnapshot, OutcomeSnapshot.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
            OutcomeSnapshot.milestone == milestone,
        )
    )
    return count or 0


async def _checkin_completion_case_count(
    session: AsyncSession,
    *,
    now: datetime,
    day: int,
    minimum_days: int,
) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    completed_cases = (
        select(
            Checkin.service_case_id.label("service_case_id"),
            func.count(distinct(Checkin.checkin_date)).label("checkin_days"),
        )
        .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
        )
        .group_by(Checkin.service_case_id)
        .subquery()
    )
    count = await session.scalar(
        select(func.count()).select_from(completed_cases).where(completed_cases.c.checkin_days >= minimum_days)
    )
    return count or 0


async def _outcome_event_case_count(session: AsyncSession, field_keys: set[str]) -> int:
    count = await session.scalar(
        select(func.count(distinct(OutcomeEvent.service_case_id)))
        .join(ServiceCase, OutcomeEvent.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            OutcomeEvent.field_key.in_(sorted(field_keys)),
        )
    )
    return count or 0


async def _day28_change_averages(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[float | None, int, float | None, int]:
    eligible_before = _service_day_eligible_before(now, 28)
    snapshots = list(
        await session.scalars(
            select(OutcomeSnapshot)
            .join(ServiceCase, OutcomeSnapshot.service_case_id == ServiceCase.id)
            .where(
                active_service_case_condition(),
                ServiceCase.started_at.is_not(None),
                ServiceCase.started_at < eligible_before,
                OutcomeSnapshot.milestone == "day28",
            )
        )
    )
    weight_changes = [_float_metric((item.metrics_json or {}).get("weight_change")) for item in snapshots]
    waist_changes = [_float_metric((item.metrics_json or {}).get("waist_change")) for item in snapshots]
    weight_values = [item for item in weight_changes if item is not None]
    waist_values = [item for item in waist_changes if item is not None]
    return (
        round(mean(weight_values), 2) if weight_values else None,
        len(weight_values),
        round(mean(waist_values), 2) if waist_values else None,
        len(waist_values),
    )


def _rate_metric(
    *,
    key: str,
    label: str,
    numerator: int,
    denominator: int,
    description: str,
) -> DashboardMetric:
    if denominator <= 0:
        return DashboardMetric(
            key=key,
            label=label,
            value=None,
            unit="%",
            numerator=numerator,
            denominator=denominator,
            description=description,
            status="no_sample",
        )
    return DashboardMetric(
        key=key,
        label=label,
        value=round(numerator * 100 / denominator, 1),
        unit="%",
        numerator=numerator,
        denominator=denominator,
        description=description,
    )


def _value_metric(
    *,
    key: str,
    label: str,
    value: float | None,
    unit: str,
    sample_count: int,
    description: str,
) -> DashboardMetric:
    return DashboardMetric(
        key=key,
        label=label,
        value=value,
        unit=unit,
        denominator=sample_count,
        description=description,
        status="ok" if value is not None else "no_sample",
    )


def _service_day_eligible_before(now: datetime, day: int) -> datetime:
    """Return the UTC boundary for natural China-date service-day eligibility."""
    china_tz = ZoneInfo("Asia/Shanghai")
    latest_start_date = now.astimezone(china_tz).date() - timedelta(days=day - 1)
    next_day_start = datetime.combine(
        latest_start_date + timedelta(days=1),
        time.min,
        tzinfo=china_tz,
    )
    return next_day_start.astimezone(timezone.utc)


def _float_metric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
