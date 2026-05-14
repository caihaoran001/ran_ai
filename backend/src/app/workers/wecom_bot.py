from __future__ import annotations

import asyncio

from app.services.wecom_service.worker import run_forever


def main() -> None:
    asyncio.run(run_forever())
