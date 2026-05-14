from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection
from app.models.service_case import ServiceCase
from app.models.user import User
from app.schemas.service_case import (
    ServiceCaseCreate,
    ServiceCaseRead,
    ServiceCaseStats,
    ServiceCaseUpdate,
)

router = APIRouter()


@router.get("", response_model=list[ServiceCaseRead])
async def list_service_cases(
    session: AsyncSession = Depends(get_session),
) -> list[ServiceCase]:
    result = await session.scalars(
        select(ServiceCase)
        .where(ServiceCase.deleted_at.is_(None))
        .options(selectinload(ServiceCase.user))
        .order_by(ServiceCase.created_at.desc())
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
    result = await session.scalar(
        select(ServiceCase)
        .where(ServiceCase.id == service_case.id)
        .options(selectinload(ServiceCase.user))
    )
    assert result is not None
    return result


@router.get("/{service_case_id}", response_model=ServiceCaseRead)
async def get_service_case(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await session.scalar(
        select(ServiceCase)
        .where(ServiceCase.id == service_case_id)
        .options(selectinload(ServiceCase.user))
    )
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    return service_case


@router.get("/{service_case_id}/stats", response_model=ServiceCaseStats)
async def get_service_case_stats(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCaseStats:
    service_case = await session.scalar(
        select(ServiceCase).where(ServiceCase.id == service_case_id)
    )
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
    service_case = await session.scalar(
        select(ServiceCase).where(ServiceCase.id == service_case_id, ServiceCase.deleted_at.is_(None))
    )
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(service_case, key, value)
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
