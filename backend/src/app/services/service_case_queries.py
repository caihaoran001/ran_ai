from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_case import ServiceCase


def active_service_case_condition():
    return ServiceCase.deleted_at.is_(None)


def select_active_service_cases(*conditions: Any):
    return select(ServiceCase).where(active_service_case_condition(), *conditions)


def select_service_cases(*conditions: Any):
    return select(ServiceCase).where(*conditions)


async def get_active_service_case(
    session: AsyncSession,
    service_case_id: int,
    *,
    options: tuple[Any, ...] = (),
) -> ServiceCase | None:
    statement = select_active_service_cases(ServiceCase.id == service_case_id)
    if options:
        statement = statement.options(*options)
    return await session.scalar(statement)


async def get_service_case(
    session: AsyncSession,
    service_case_id: int,
    *,
    options: tuple[Any, ...] = (),
) -> ServiceCase | None:
    statement = select_service_cases(ServiceCase.id == service_case_id)
    if options:
        statement = statement.options(*options)
    return await session.scalar(statement)
