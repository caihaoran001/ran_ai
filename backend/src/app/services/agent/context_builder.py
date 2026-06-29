from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.profile_memory_service import load_profile_memory_context


MAX_MEMORY_LINES = 8


async def build_agent_memory_context(
    session: AsyncSession,
    *,
    service_case_id: int,
    max_lines: int = MAX_MEMORY_LINES,
) -> str:
    """Build short implicit user background for the agent."""

    memory = await load_profile_memory_context(session, service_case_id=service_case_id)
    return memory.render_for_agent(max_lines=max_lines)
