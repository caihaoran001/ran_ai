from __future__ import annotations

import asyncio

from app.services.reminder_service import ReminderService


async def _run() -> None:
    service = ReminderService()
    await service.start()
    try:
        await asyncio.Event().wait()
    finally:
        await service.stop()


def main() -> None:
    asyncio.run(_run())
