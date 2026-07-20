from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.service_case_queries import active_service_case_condition


async def select_population_service_cases(session: AsyncSession) -> list[ServiceCase]:
    """返回群体画像/共同洞察共用的用户范围。

    每位未删除用户先取最新未删除服务单，再判断是否已退款；
    最新服务单已退款时不回退到更旧的服务单。
    """

    all_cases = list(
        await session.scalars(
            select(ServiceCase)
            .join(User, ServiceCase.user_id == User.id)
            .where(active_service_case_condition(), User.deleted_at.is_(None))
            .options(selectinload(ServiceCase.user))
            .order_by(
                ServiceCase.user_id,
                ServiceCase.created_at.desc(),
                ServiceCase.id.desc(),
            )
        )
    )
    selected: list[ServiceCase] = []
    seen_user_ids: set[int] = set()
    for service_case in all_cases:
        if service_case.user_id in seen_user_ids:
            continue
        seen_user_ids.add(service_case.user_id)
        if service_case.status == ServiceCaseStatus.REFUNDED:
            continue
        selected.append(service_case)
    return selected
