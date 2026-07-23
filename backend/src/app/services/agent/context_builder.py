from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

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
