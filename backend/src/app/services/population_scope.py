from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.service_case import ServiceCase
from app.services.service_case_queries import stats_scope_condition


async def select_population_service_cases(session: AsyncSession) -> list[ServiceCase]:
    """群体画像/共同洞察的统计范围：与数据看板完全同一口径。

    直接复用 `stats_scope_condition()`——未删除的新群服务单，按「单」计数，
    已退款照常计入。画像这边刻意不再自己去重到人、也不剔除已退款：两个页面的
    分母必须能对上，否则运营看到看板 179、画像 157 会以为哪边算错了。
    """

    return list(
        await session.scalars(
            select(ServiceCase)
            .where(stats_scope_condition())
            .options(selectinload(ServiceCase.user))
            .order_by(ServiceCase.created_at.desc(), ServiceCase.id.desc())
        )
    )
