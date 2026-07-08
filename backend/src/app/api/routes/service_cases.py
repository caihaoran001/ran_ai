from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.ai_usage import AIUsageLog
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.schemas.service_case import (
    ServiceCaseAIContextPreview,
    ServiceCaseRead,
    ServiceCaseStats,
    ServiceCaseUpdate,
)
from app.services.ranren_ai.engine import build_ai_user_context
from app.services.service_case_queries import (
    active_service_case_condition,
    get_active_service_case,
    get_service_case as get_service_case_any,
    pending_review_case_ids_subquery,
    select_active_service_cases,
)

router = APIRouter()


async def _annotate_pending_review(session: AsyncSession, cases: list[ServiceCase]) -> None:
    """把 needs_human_intervention 字段动态置为『是否有待审核 AI 回复』(事件级『需关注』)。

    内存赋值仅用于序列化输出;只读端点不 commit，不会污染 DB(列已停止写入)。
    """
    if not cases:
        return
    ids = [case.id for case in cases]
    pending = set(
        (
            await session.scalars(
                pending_review_case_ids_subquery().where(Message.service_case_id.in_(ids))
            )
        ).all()
    )
    for case in cases:
        case.needs_human_intervention = case.id in pending


@router.get("", response_model=list[ServiceCaseRead])
async def list_service_cases(
    filter: str | None = None,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ServiceCase]:
    statement = (
        select_active_service_cases()
        .options(selectinload(ServiceCase.user))
        .order_by(ServiceCase.created_at.desc())
    )
    statement = _apply_service_case_filter(statement, filter)
    if q and q.strip():
        # 按 用户名 / 手机号 / 群名 模糊搜索。outerjoin 保留未绑定用户但有群名的服务单。
        like = f"%{q.strip()}%"
        statement = statement.outerjoin(User, ServiceCase.user_id == User.id).where(
            or_(
                User.name.ilike(like),
                User.phone.ilike(like),
                ServiceCase.group_name.ilike(like),
            )
        )
    cases = list(await session.scalars(statement))
    await _annotate_pending_review(session, cases)
    return cases


@router.get("/{service_case_id}", response_model=ServiceCaseRead)
async def get_service_case(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_service_case_any(session, service_case_id, options=(selectinload(ServiceCase.user),))
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    await _annotate_pending_review(session, [service_case])
    return service_case


@router.get("/{service_case_id}/stats", response_model=ServiceCaseStats)
async def get_service_case_stats(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCaseStats:
    service_case = await get_service_case_any(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")

    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.service_case_id == service_case_id)
            .order_by(Message.created_at.asc())
        )
    )
    checkins = list(
        await session.scalars(
            select(Checkin)
            .where(Checkin.service_case_id == service_case_id)
            .order_by(Checkin.created_at.asc())
        )
    )
    inbound = [
        message
        for message in messages
        if message.direction == MessageDirection.INBOUND
    ]
    outbound = [
        message
        for message in messages
        if message.direction == MessageDirection.OUTBOUND
    ]
    first_inbound_at = inbound[0].created_at if inbound else None
    now = datetime.now(timezone.utc)
    days = 1.0
    if first_inbound_at:
        started_at = first_inbound_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        days = max((now - started_at).total_seconds() / 86400, 1.0)
    ai_usage = await session.execute(
        select(
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0),
            func.coalesce(
                func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario == "qwen_vl_translate"),
                0.0,
            ),
            func.coalesce(
                func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario != "qwen_vl_translate"),
                0.0,
            ),
        ).where(AIUsageLog.service_case_id == service_case_id)
    )
    ai_calls, ai_tokens, ai_cost, ai_vision_cost, ai_deepseek_cost = ai_usage.one()

    return ServiceCaseStats(
        message_count=len(messages),
        inbound_message_count=len(inbound),
        outbound_message_count=len(outbound),
        avg_daily_inbound_messages=round(len(inbound) / days, 2),
        last_message_at=messages[-1].created_at if messages else None,
        last_user_message_at=inbound[-1].created_at if inbound else None,
        checkin_count=len(checkins),
        last_checkin_at=checkins[-1].created_at if checkins else None,
        ai_total_calls=ai_calls or 0,
        ai_total_tokens=ai_tokens or 0,
        ai_estimated_cost_yuan=round(float(ai_cost or 0), 6),
        ai_deepseek_cost_yuan=round(float(ai_deepseek_cost or 0), 6),
        ai_vision_cost_yuan=round(float(ai_vision_cost or 0), 6),
    )


@router.get("/{service_case_id}/ai-context-preview", response_model=ServiceCaseAIContextPreview)
async def get_service_case_ai_context_preview(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCaseAIContextPreview:
    service_case = await get_service_case_any(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    return ServiceCaseAIContextPreview(context=await build_ai_user_context(session, service_case))


@router.patch("/{service_case_id}", response_model=ServiceCaseRead)
async def update_service_case(
    service_case_id: int,
    payload: ServiceCaseUpdate,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_active_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    data = payload.model_dump(exclude_unset=True)
    if data.pop("reset_risk", False):
        # 事件化后不再有 needs_human_intervention 持久标记可清；此操作语义=重置风险等级。
        service_case.risk_level = "normal"
        service_case.risk_flags = None
        service_case.intervention_reasons = None
    for key, value in data.items():
        setattr(service_case, key, value)
    await session.commit()
    await session.refresh(service_case)
    result = await get_active_service_case(session, service_case.id, options=(selectinload(ServiceCase.user),))
    assert result is not None
    return result


@router.delete("/{service_case_id}", response_model=ServiceCaseRead)
async def delete_service_case(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_active_service_case(session, service_case_id, options=(selectinload(ServiceCase.user),))
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    if not service_case.deleted_at:
        deleted_at = datetime.now(timezone.utc)
        service_case.deleted_at = deleted_at
        if service_case.user_id:
            active_sibling = await session.scalar(
                select(ServiceCase.id)
                .where(
                    ServiceCase.user_id == service_case.user_id,
                    ServiceCase.id != service_case.id,
                    active_service_case_condition(),
                )
                .limit(1)
            )
            if not active_sibling and service_case.user and not service_case.user.deleted_at:
                service_case.user.deleted_at = deleted_at
        await session.commit()
        await session.refresh(service_case)
    result = await session.scalar(
        select(ServiceCase)
        .where(ServiceCase.id == service_case.id)
        .options(selectinload(ServiceCase.user))
    )
    assert result is not None
    return result


def _apply_service_case_filter(statement, metric: str | None):
    if not metric:
        return statement

    now = datetime.now(timezone.utc)
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    today_start = datetime.combine(today, time.min, tzinfo=china_tz).astimezone(timezone.utc)
    today_end = today_start + timedelta(days=1)

    if metric == "new_today":
        return statement.where(
            ServiceCase.created_at >= today_start,
            ServiceCase.created_at < today_end,
        )
    if metric == "risk":
        return statement.where(ServiceCase.risk_level == "high")
    if metric == "human_intervention":
        return statement.where(ServiceCase.id.in_(pending_review_case_ids_subquery()))
    if metric == "in_service":
        return statement.where(ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
    if metric == "renewal_followup":
        return statement.where(
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at <= now - timedelta(days=18),
            ServiceCase.started_at >= now - timedelta(days=28),
        )
    if metric == "renewal_intent":
        return statement.where(ServiceCase.renewal_intent_flags.is_not(None))
    if metric == "refund_intent":
        return statement.where(ServiceCase.refund_intent_flags.is_not(None))
    if metric == "refund_pending":
        # 模型识别到"疑似已退款"、等待运营确认（尚未改成 REFUNDED、仍在正常服务）
        return statement.where(
            ServiceCase.refund_pending_confirmation.is_(True),
            ServiceCase.status != ServiceCaseStatus.REFUNDED,
        )
    if metric == "refunded":
        return statement.where(ServiceCase.status == ServiceCaseStatus.REFUNDED)
    if metric in {"unfollowed", "lost"}:
        last_message_subq = (
            select(Message.service_case_id, func.max(Message.created_at).label("last_at"))
            .where(Message.direction == MessageDirection.INBOUND)
            .group_by(Message.service_case_id)
            .subquery()
        )
        last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
        statement = statement.outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        if metric == "unfollowed":
            return statement.where(
                ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                last_activity_at < now - timedelta(days=2),
                last_activity_at >= now - timedelta(days=7),
            )
        return statement.where(
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=7),
        )
    if metric == "messaged_today":
        message_subq = (
            select(Message.service_case_id)
            .where(
                Message.created_at >= today_start,
                Message.created_at < today_end,
            )
            .distinct()
            .subquery()
        )
        return statement.join(message_subq, message_subq.c.service_case_id == ServiceCase.id)

    if metric == "active_24h":
        # 活跃用户：近 24 小时内有「用户发言」(inbound) 的服务单（不含机器人/班主任发出的 outbound）。
        active_subq = (
            select(Message.service_case_id)
            .where(
                Message.direction == MessageDirection.INBOUND,
                Message.created_at >= now - timedelta(hours=24),
            )
            .distinct()
            .subquery()
        )
        return statement.join(active_subq, active_subq.c.service_case_id == ServiceCase.id)

    return statement
