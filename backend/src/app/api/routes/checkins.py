from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.service_case import ServiceCase
from app.schemas.checkin import CheckinCreate, CheckinRead

router = APIRouter()


@router.get("/service-cases/{service_case_id}/checkins", response_model=list[CheckinRead])
async def list_case_checkins(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Checkin]:
    result = await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id == service_case_id)
        .order_by(Checkin.checkin_date.desc(), Checkin.created_at.desc())
    )
    return list(result)


@router.post(
    "/service-cases/{service_case_id}/checkins",
    response_model=CheckinRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_checkin(
    service_case_id: int,
    payload: CheckinCreate,
    session: AsyncSession = Depends(get_session),
) -> Checkin:
    service_case = await session.get(ServiceCase, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    checkin = Checkin(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        user_id=service_case.user_id,
        checkin_date=payload.checkin_date or date.today(),
        content=payload.content,
        raw_message_id=payload.raw_message_id,
        parsed_json=payload.parsed_json,
    )
    session.add(checkin)
    await session.commit()
    await session.refresh(checkin)
    return checkin
