from __future__ import annotations

import asyncio

from app.core.config import configure_logging
from app.services.agent.graph import (
    start_agent_graph_runtime,
    stop_agent_graph_runtime,
)
from app.services.reminder_service import ReminderService


async def _run() -> None:
    # Scheduler-triggered reminder replies use the same process-shared ReAct
    # topology even though this worker does not run the inbound EngineGraph.
    start_agent_graph_runtime()
    service = ReminderService()
    await service.start()
    try:
        await asyncio.Event().wait()
    finally:
        await service.stop()
        stop_agent_graph_runtime()


def main() -> None:
    configure_logging()
    asyncio.run(_run())
