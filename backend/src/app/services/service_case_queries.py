from __future__ import annotations

from datetime import date as _date, datetime, time as _time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus

SERVICE_CASE_WINDOW_DAYS = 28
# 第 28 天结束后保留次日的补问和归档/奖励结算。
# 服务单仍按原规则完结；仅补问/归档/奖励结算留窗口，且奖励只统计第 1—28 天。
SERVICE_CASE_SETTLEMENT_GRACE = timedelta(days=1)


def effective_start_at(service_case: ServiceCase) -> datetime | None:
    """服务计时起点：计划开始日期（当地时区当天0点）优先；存量数据回退 started_at。"""
    plan = getattr(service_case, "service_plan_start_date", None)
    if plan is not None:
        tz = ZoneInfo(get_settings().reminder_timezone)
        return datetime.combine(plan, _time.min, tzinfo=tz)
    return service_case.started_at


def effective_service_day(service_case: ServiceCase, now: datetime, tz: ZoneInfo) -> int:
    """服务第几天：计划开始当天=第1天；计划日未到或无任何起点返回 <=0(调用方据此判断『未开始』)。

    无起点(未设计划开始日、也未发开服文件)时返回 0，与"计划日未到"同走『服务尚未正式开始』
    分支，避免把仅自动建单、尚未开服的案例误报成"第1天"。需要保底≥1天号的调用方自行 max(...,1)。
    """
    start = effective_start_at(service_case)
    if start is None:
        return 0
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


def service_phase_end_date(service_case: ServiceCase, tz: ZoneInfo) -> _date | None:
    """28 天服务阶段的结束日期：第 28 天当天仍在服务内，次日停止周期内任务。"""
    first = checkin_first_day(service_case, tz)
    if first is None:
        return None
    return first + timedelta(days=SERVICE_CASE_WINDOW_DAYS - 1)


def service_continuation_active(service_case: ServiceCase) -> bool:
    """原服务单是否正处于续服中。

    `service_continued_at` 是曾续服的审计标记，实时是否续服仍以当前
    `IN_SERVICE` 状态为准；这样明确结束后无需擦除历史续服时间。
    """
    return (
        getattr(service_case, "service_continued_at", None) is not None
        and getattr(service_case, "status", None) == ServiceCaseStatus.IN_SERVICE
    )


def service_window_ended(service_case: ServiceCase, now: datetime, tz: ZoneInfo) -> bool:
    if service_continuation_active(service_case):
        return False
    phase_end = service_phase_end_date(service_case, tz)
    return phase_end is not None and now.date() > phase_end


def service_settlement_ended(service_case: ServiceCase, now: datetime, tz: ZoneInfo) -> bool:
    """服务完结结算线：允许第 28 天次早补答完成归档与奖励结算。"""
    if service_continuation_active(service_case):
        return False
    phase_end = service_phase_end_at(service_case, tz)
    if phase_end is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    return now.astimezone(tz) > phase_end + SERVICE_CASE_SETTLEMENT_GRACE


def service_phase_end_at(service_case: ServiceCase, tz: ZoneInfo) -> datetime | None:
    phase_end = service_phase_end_date(service_case, tz)
    if phase_end is None:
        return None
    return datetime.combine(phase_end, _time.max, tzinfo=tz)


def last_user_inbound_subquery():
    """每个服务单最近一次「用户本人」发言的时间。

    只统计 sender_type=USER：群里班主任(staff)的发言同样以 INBOUND 落库，
    若不加这个过滤，班主任跟进一句就会把已经沉默多日的用户从
    未跟进/流失名单里带走，导致越跟进流失数越少。

    调用方通常用 `func.coalesce(subq.c.last_at, ServiceCase.created_at)`
    兜底「从建单起就没说过话」的服务单。
    """
    return (
        select(Message.service_case_id, func.max(Message.created_at).label("last_at"))
        .where(
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == MessageSenderType.USER,
        )
        .group_by(Message.service_case_id)
        .subquery()
    )


def active_service_case_condition():
    return ServiceCase.deleted_at.is_(None)


def member_present_condition():
    """客户还在群里。

    客户本人退群后这张单只是留档：群位让给下一位客户，后台照常查得到，
    但一切会打扰群里的自动动作（打卡提醒、沉默跟进、阶段报告、视频推送、
    打卡追问）都必须停下来——人都走了，再发就是对着空群喊，还会把新客户
    的群刷屏。凡是「会往群里发东西」的周期任务都要带上这个条件。
    """
    return ServiceCase.member_left_at.is_(None)


def _intake_submission_exists(*extra: Any):
    return (
        select(QuestionnaireSubmission.id)
        .where(
            QuestionnaireSubmission.service_case_id == ServiceCase.id,
            QuestionnaireSubmission.template_key == "intake",
            *extra,
        )
        .exists()
    )


def intake_submitted_condition():
    """这张单交过入营问卷 —— 客户真的走过本系统流程的凭据。"""
    return _intake_submission_exists()


def intake_submitted_between_condition(start: datetime, end: datetime):
    """这张单的入营问卷是在 [start, end) 之间交的 —— 看板「今日新增用户」的口径。

    不能用「今天建了单」来数新客户：建单只代表群里有人说了话。一个群可以先后服务
    多位客户，班主任在空出来的老群里说一句「这个群先留着」就会开一张新单，
    那时候一个新客户都还没进来。客户真正开始服务的凭据是交了入营问卷
    （交完才生成画像和交付文件），所以按问卷提交时间数。

    也不能用「建单在今天且交过问卷」：班主任昨天先把群铺好、客户今天才填问卷的话，
    这个客户在哪一天都数不到。
    """
    return _intake_submission_exists(
        QuestionnaireSubmission.created_at >= start,
        QuestionnaireSubmission.created_at < end,
    )


def select_active_service_cases(*conditions: Any):
    return select(ServiceCase).where(active_service_case_condition(), *conditions)


def select_present_service_cases(*conditions: Any):
    """未删除、且客户还在群里的服务单。"""
    return select(ServiceCase).where(
        active_service_case_condition(), member_present_condition(), *conditions
    )


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
