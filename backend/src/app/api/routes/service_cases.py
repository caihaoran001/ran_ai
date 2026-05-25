from __future__ import annotations

import secrets
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.schemas.service_case import (
    ServiceCaseCreate,
    ServiceCaseRead,
    ServiceCaseStats,
    ServiceCaseUpdate,
)
from app.services.service_case_queries import (
    active_service_case_condition,
    get_active_service_case,
    get_service_case as get_service_case_any,
    select_active_service_cases,
)

router = APIRouter()


@router.get("", response_model=list[ServiceCaseRead])
async def list_service_cases(
    filter: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ServiceCase]:
    statement = (
        select_active_service_cases()
        .options(selectinload(ServiceCase.user))
        .order_by(ServiceCase.created_at.desc())
    )
    statement = _apply_service_case_filter(statement, filter)
    result = await session.scalars(
        statement
    )
    return list(result)


@router.post("", response_model=ServiceCaseRead, status_code=status.HTTP_201_CREATED)
async def create_service_case(
    payload: ServiceCaseCreate,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    user_data = payload.user.model_dump()
    user_data["name"] = user_data["name"].strip() or "待填写问卷用户"
    user = User(**user_data)
    session.add(user)
    await session.flush()

    service_case = ServiceCase(
        case_no=payload.case_no or _generate_case_no(),
        user_id=user.id,
        group_id=payload.group_id,
        questionnaire_token=secrets.token_urlsafe(24),
        notes=payload.notes,
    )
    session.add(service_case)
    await session.commit()
    result = await get_active_service_case(session, service_case.id, options=(selectinload(ServiceCase.user),))
    assert result is not None
    return result


@router.get("/{service_case_id}", response_model=ServiceCaseRead)
async def get_service_case(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_service_case_any(session, service_case_id, options=(selectinload(ServiceCase.user),))
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
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

    return ServiceCaseStats(
        message_count=len(messages),
        inbound_message_count=len(inbound),
        outbound_message_count=len(outbound),
        avg_daily_inbound_messages=round(len(inbound) / days, 2),
        last_message_at=messages[-1].created_at if messages else None,
        last_user_message_at=inbound[-1].created_at if inbound else None,
        checkin_count=len(checkins),
        last_checkin_at=checkins[-1].created_at if checkins else None,
    )


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
        service_case.risk_level = "normal"
        service_case.risk_flags = None
        service_case.needs_human_intervention = False
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


def _generate_case_no() -> str:
    return f"RAI-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


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
        return statement.where(ServiceCase.needs_human_intervention.is_(True))
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

    return statement
