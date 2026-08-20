from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.service_case_queries import stats_scope_condition


async def select_population_service_cases(
    session: AsyncSession, *, exclude_refunded: bool = False
) -> list[ServiceCase]:
    """画像/下钻/共同洞察的服务单范围。

    默认与数据看板完全同一口径：直接复用 `stats_scope_condition()`——未删除的新群
    服务单，按「单」计数，已退款照常计入。画像与下钻保持默认，这样两个页面的分母
    必须能对上，否则运营看到看板 179、画像 157 会以为哪边算错了。

    共同洞察语义不同：它分析的是「当前该服务谁」，已退款的人已不是服务对象，所以
    调用方传 `exclude_refunded=True`，在统一口径之上再剔除已退款单。
    """

    conditions = [stats_scope_condition()]
    if exclude_refunded:
        conditions.append(ServiceCase.status != ServiceCaseStatus.REFUNDED)
    return list(
        await session.scalars(
            select(ServiceCase)
            .where(*conditions)
            .options(selectinload(ServiceCase.user))
            .order_by(ServiceCase.created_at.desc(), ServiceCase.id.desc())
        )
    )