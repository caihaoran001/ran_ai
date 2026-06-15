from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase


def active_service_case_condition():
    return ServiceCase.deleted_at.is_(None)


def pending_review_case_ids_subquery():
    """『需关注』的事件级定义：有待审核 AI 回复(OUTBOUND+BOT+CHAT+PENDING)的 service_case_id 子查询。

    运营在复核台处理(确认发送/不发送)后该消息离开 PENDING，case 自动不再『需关注』，无需手动清除。
    """
    return (
        select(Message.service_case_id)
        .where(
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == MessageSenderType.BOT,
            Message.purpose == MessagePurpose.CHAT,
            Message.status == MessageStatus.PENDING,
        )
        .distinct()
    )


def select_active_service_cases(*conditions: Any):
    return select(ServiceCase).where(active_service_case_condition(), *conditions)


def select_service_cases(*conditions: Any):
    return select(ServiceCase).where(*conditions)


async def get_active_service_case(
    session: AsyncSession,
    service_case_id: int,
    *,
    options: tuple[Any, ...] = (),
) -> ServiceCase | None:
    statement = select_active_service_cases(ServiceCase.id == service_case_id)
    if options:
        statement = statement.options(*options)
    return await session.scalar(statement)


async def get_service_case(
    session: AsyncSession,
    service_case_id: int,
    *,
    options: tuple[Any, ...] = (),
) -> ServiceCase | None:
    statement = select_service_cases(ServiceCase.id == service_case_id)
    if options:
        statement = statement.options(*options)
    return await session.scalar(statement)
