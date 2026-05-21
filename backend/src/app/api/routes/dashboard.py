from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, UserDocument
from app.schemas.dashboard import DashboardSummary

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    total = await session.scalar(
        select(func.count(ServiceCase.id)).where(ServiceCase.deleted_at.is_(None))
    )
    in_service = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            ServiceCase.deleted_at.is_(None),
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
            ServiceCase.deleted_at.is_(None),
            Checkin.checkin_date == today,
        )
    )
    messages_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            ServiceCase.deleted_at.is_(None),
            Message.created_at >= today_start,
            Message.created_at < today_end,
        )
    )
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
        .where(ServiceCase.deleted_at.is_(None))
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
            ServiceCase.deleted_at.is_(None),
            ServiceCase.created_at >= today_start,
            ServiceCase.created_at < today_end,
        )
    )
    risk_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            ServiceCase.deleted_at.is_(None),
            ServiceCase.risk_level == "high",
        )
    )
    human_intervention_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            ServiceCase.deleted_at.is_(None),
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
            ServiceCase.deleted_at.is_(None),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=2),
            last_activity_at >= now - timedelta(days=7),
        )
    )
    lost_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            ServiceCase.deleted_at.is_(None),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=7),
        )
    )
    renewal_followup_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            ServiceCase.deleted_at.is_(None),
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
                ServiceCase.deleted_at.is_(None),
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

    return DashboardSummary(
        service_cases_total=total or 0,
        service_cases_in_service=in_service or 0,
        checkins_today=checkins_today or 0,
        messages_today=messages_today or 0,
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
    )


def _has_emotion_signal(message: Message) -> bool:
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
    if analysis is not None:
        emotion_signal = analysis.get("emotion_signal")
        if isinstance(emotion_signal, dict):
            return bool(emotion_signal.get("detected"))
        return _analysis_has_emotion_code(analysis)
    return False


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
            ServiceCase.deleted_at.is_(None),
            condition,
        )
    )
    return count or 0
