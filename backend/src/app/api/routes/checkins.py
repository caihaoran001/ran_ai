from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.service_case import ServiceCase
from app.schemas.checkin import CheckinCreate, CheckinRead
from app.services.checkin_service import normalize_checkin_json
from app.services.intervention_service import analyze_intervention, merge_case_insight
from app.services.service_case_queries import active_service_case_condition, get_active_service_case, get_service_case

router = APIRouter()


@router.get("/service-cases/{service_case_id}/checkins", response_model=list[CheckinRead])
async def list_case_checkins(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Checkin]:
    service_case = await get_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="service case not found")

    result = await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id == service_case_id)
        .order_by(Checkin.checkin_date.desc(), Checkin.created_at.desc())
    )
    return list(result)


@router.get("/checkins", response_model=list[CheckinRead])
async def list_checkins(
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> list[Checkin]:
    result = await session.scalars(
        select(Checkin)
        .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
        .where(active_service_case_condition())
        .order_by(Checkin.created_at.desc())
        .limit(min(max(limit, 1), 100))
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
    service_case = await get_active_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    checkin = Checkin(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        user_id=service_case.user_id,
        checkin_date=payload.checkin_date or date.today(),
        content=payload.content,
        raw_message_id=payload.raw_message_id,
        parsed_json=normalize_checkin_json(payload.parsed_json, payload.content),
    )
    insight = await analyze_intervention(
        source="checkin",
        content=payload.content,
        context={"service_case_id": service_case.id, "parsed_json": checkin.parsed_json},
    )
    merge_case_insight(service_case, insight)
    session.add(checkin)
    await session.commit()
    await session.refresh(checkin)
    return checkin
