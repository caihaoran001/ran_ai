from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.profile_memory_service import load_profile_memory_context


MAX_MEMORY_LINES = 10
MAX_SCOPED_MEMORY_LINES = 6


async def build_agent_memory_context(
    session: AsyncSession,
    *,
    service_case_id: int,
    max_lines: int = MAX_MEMORY_LINES,
    memory_scopes: Sequence[str] | None = None,
) -> str:
    """Build short implicit user background for the agent."""

    memory = await load_profile_memory_context(session, service_case_id=service_case_id)
    if memory_scopes is None:
        return memory.render_for_agent(max_lines=max_lines)
    return memory.render_for_agent(
        max_lines=min(max_lines, MAX_SCOPED_MEMORY_LINES),
        sections=memory_scopes,
    )
