from __future__ import annotations

from datetime import date as _date, datetime, time as _time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase


def effective_start_at(service_case: ServiceCase) -> datetime | None:
    """服务计时起点：用户在问卷选的『计划开始日期』(当地时区当天0点)优先；未填则回退 started_at(文件发送时刻)。"""
    plan = getattr(service_case, "service_plan_start_date", None)
    if plan is not None:
        tz = ZoneInfo(get_settings().reminder_timezone)
        return datetime.combine(plan, _time.min, tzinfo=tz)
    return service_case.started_at


def effective_service_day(service_case: ServiceCase, now: datetime, tz: ZoneInfo) -> int:
    """服务第几天：计划开始当天=第1天；计划日未到返回 <=0(调用方据此判断『未开始』)；无起点返回 1。"""
    start = effective_start_at(service_case)
    if start is None:
        return 1
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo("UTC"))
    return (now.date() - start.astimezone(tz).date()).days + 1


def checkin_first_day(service_case: ServiceCase, tz: ZoneInfo) -> _date | None:
    """打卡『第1天』的日期：
    - 填了计划开始日 → 计划日当天即第1天(新语义)；
    - 未填(存量/旧案例) → 文件发送(started_at)次日为第1天(沿用旧『入群当天第0天、次日第1天』，存量天数号不变、不会重发)。
    """
    plan = getattr(service_case, "service_plan_start_date", None)
    if plan is not None:
        return plan
    started = service_case.started_at
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=ZoneInfo("UTC"))
    return started.astimezone(tz).date() + timedelta(days=1)


def checkin_day(service_case: ServiceCase, now: datetime, tz: ZoneInfo) -> int | None:
    """打卡第几天：第1天日期当天=1；还没到第1天返回 None(不发)。"""
    first = checkin_first_day(service_case, tz)
    if first is None:
        return None
    day = (now.date() - first).days + 1
    return day if day >= 1 else None


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
