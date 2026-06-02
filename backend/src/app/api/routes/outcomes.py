from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.outcome import OutcomeEvent, OutcomeSnapshot, OutcomeStageState
from app.schemas.outcome import OutcomeBundleRead
from app.services.service_case_queries import get_active_service_case

router = APIRouter()


@router.get("/service-cases/{service_case_id}/outcomes", response_model=OutcomeBundleRead)
async def list_case_outcomes(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> OutcomeBundleRead:
    service_case = await get_active_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")

    events = list(
        await session.scalars(
            select(OutcomeEvent)
            .where(OutcomeEvent.service_case_id == service_case_id)
            .order_by(OutcomeEvent.created_at.desc())
        )
    )
    states = list(
        await session.scalars(
            select(OutcomeStageState)
            .where(OutcomeStageState.service_case_id == service_case_id)
            .order_by(OutcomeStageState.created_at.asc())
        )
    )
    snapshots = list(
        await session.scalars(
            select(OutcomeSnapshot)
            .where(OutcomeSnapshot.service_case_id == service_case_id)
            .order_by(OutcomeSnapshot.created_at.asc())
        )
    )
    return OutcomeBundleRead(events=events, states=states, snapshots=snapshots)
