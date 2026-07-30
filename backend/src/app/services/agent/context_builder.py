from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.checkin_checklists import (
    TRACKING_PRODUCT_SCHEDULE,
    get_effective_daily_checklist,
)
from app.services.profile_memory_service import load_profile_memory_context


async def build_agent_memory_context(
    session: AsyncSession,
    *,
    service_case_id: int,
    memory_scopes: Sequence[str] | None = None,
) -> str:
    """Build short implicit user background for the agent.

    各来源域已各自限量、历程小结靠上游字数上限兜底,因此不再按总条数截断
    (旧的 10 条位置截断会把排末位的风险边界静默砍掉)。scoped 路径只是挑选
    相关的域,同样不再叠加条数上限。
    """

    memory = await load_profile_memory_context(session, service_case_id=service_case_id)
    if memory_scopes is None:
        return memory.render_for_agent()
    return memory.render_for_agent(sections=memory_scopes)


async def build_agent_checkin_context(
    session: AsyncSession,
    *,
    service_case_id: int,
) -> str:
    """Render the active five-item checklist as a compact, stable-ID prompt block."""

    checklist = await get_effective_daily_checklist(
        session, service_case_id=service_case_id
    )
    items = checklist.resolved_items()
    if len(items) != 5:
        return "当前没有可用的五项打卡清单。"
    lines = [f"来源：{checklist.source_ref}"]
    for index, item in enumerate(items, start=1):
        tracking = (
            " | 跟踪 product_schedule.morning_c/noon_b/night_a"
            if item.get("tracking_kind") == TRACKING_PRODUCT_SCHEDULE
            else ""
        )
        lines.append(f"{index}. [{item['id']}] {item['label']}{tracking}")
    return "\n".join(lines)
