from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.message import Message
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, UserDocument
from app.schemas.dashboard import DashboardSummary

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    total = await session.scalar(select(func.count(ServiceCase.id)))
    in_service = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE
        )
    )
    today = date.today()
    checkins_today = await session.scalar(
        select(func.count(Checkin.id)).where(Checkin.checkin_date == today)
    )
    messages_today = await session.scalar(
        select(func.count(Message.id)).where(func.date(Message.created_at) == today)
    )
    pending_documents = await session.scalar(
        select(func.count(UserDocument.id)).where(
            UserDocument.status.in_([DocumentStatus.DRAFT, DocumentStatus.REVIEWING])
        )
    )
    return DashboardSummary(
        service_cases_total=total or 0,
        service_cases_in_service=in_service or 0,
        checkins_today=checkins_today or 0,
        messages_today=messages_today or 0,
        pending_documents=pending_documents or 0,
    )
