from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase


def is_checkin_message(text: str, pattern: str = r"^#打卡") -> bool:
    return re.search(pattern, text or "") is not None


async def create_checkin_from_message(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    content: str,
    raw_message_id: str | None,
) -> Checkin:
    existing = await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case.id,
            Checkin.checkin_date == date.today(),
        )
    )
    if existing:
        existing.content = content
        existing.raw_message_id = raw_message_id
        return existing

    checkin = Checkin(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        user_id=service_case.user_id,
        checkin_date=date.today(),
        content=content,
        raw_message_id=raw_message_id,
    )
    session.add(checkin)
    return checkin
