from __future__ import annotations

"""打卡记录的共享查询。

engine 的 AI 上下文与 agent 工具此前各写一份"取最近 N 条打卡"的查询，排序/字段一变要改两处。
统一在此定义查询主体，两边各自决定展示顺序与格式化方式。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin


async def recent_checkins(
    session: AsyncSession,
    service_case_id: int,
    *,
    limit: int,
    ascending: bool = True,
) -> list[Checkin]:
    """取该服务单最近 ``limit`` 条打卡。

    底层始终按 ``checkin_date`` 降序取最新的若干条；``ascending=True`` 时再翻转为时间正序
    （旧→新，便于算趋势），``ascending=False`` 保持最新在前（用于画像上下文展示）。
    """
    result = await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id == service_case_id)
        .order_by(Checkin.checkin_date.desc(), Checkin.created_at.desc())
        .limit(max(1, int(limit)))
    )
    rows = list(result)
    return list(reversed(rows)) if ascending else rows
