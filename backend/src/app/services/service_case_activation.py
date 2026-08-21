from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.service_case_queries import effective_service_day, service_window_ended


PRE_SERVICE_STATUSES: frozenset[ServiceCaseStatus] = frozenset(
    {
        # 问卷不是服务启动的前置条件：群聊自动建单后，工作人员也可以
        # 直接设置计划开始日期，由调度器在到期时激活 CREATED 服务单。
        ServiceCaseStatus.CREATED,
        ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED,
        ServiceCaseStatus.GENERATING,
        ServiceCaseStatus.DOCUMENTS_GENERATED,
        ServiceCaseStatus.REVIEWING,
        ServiceCaseStatus.APPROVED,
    }
)


def planned_service_start_at(
    service_case: ServiceCase, tz: ZoneInfo
) -> datetime | None:
    """Return the plan-date boundary as an aware UTC timestamp."""
    plan = service_case.service_plan_start_date
    if plan is None:
        return None
    return datetime.combine(plan, time.min, tzinfo=tz).astimezone(timezone.utc)


def activate_service_case_if_due(
    service_case: ServiceCase,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> bool:
    """Promote a pre-service case when its configured local plan date has arrived."""
    if service_case.status not in PRE_SERVICE_STATUSES:
        return False
    planned_start = planned_service_start_at(service_case, tz)
    if planned_start is None:
        return False
    local_now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    if service_case.service_plan_start_date > local_now.date():
        return False
    service_case.status = ServiceCaseStatus.IN_SERVICE
    service_case.started_at = planned_start
    service_case.ended_at = None
    return True


def align_active_service_start_with_plan(
    service_case: ServiceCase,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> bool:
    """Repair active legacy cases whose started_at still reflects file delivery."""
    if service_case.status != ServiceCaseStatus.IN_SERVICE:
        return False
    planned_start = planned_service_start_at(service_case, tz)
    if planned_start is None:
        return False
    local_now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    if service_case.service_plan_start_date > local_now.date():
        return False
    current_start = service_case.started_at
    if current_start is not None:
        if current_start.tzinfo is None:
            current_start = current_start.replace(tzinfo=timezone.utc)
        else:
            current_start = current_start.astimezone(timezone.utc)
    if current_start == planned_start:
        return False
    service_case.started_at = planned_start
    return True


def set_pre_service_status_or_activate(
    service_case: ServiceCase,
    pending_status: ServiceCaseStatus,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> bool:
    """Apply a generation/review status without regressing an active or closed case."""
    if service_case.status in {
        ServiceCaseStatus.IN_SERVICE,
        ServiceCaseStatus.COMPLETED,
        ServiceCaseStatus.REFUNDED,
    }:
        return False
    service_case.status = pending_status
    return activate_service_case_if_due(service_case, now=now, tz=tz)


# 客户退群自动收口 / 接回恢复的生命周期审计动作名。
# 「因退群自动结束」是接回恢复的判据：只有被这条路径完结的单子才允许自动复活，
# 正常毕业（28 天走完）的单子绝不因为人回群就复活。
AUTO_CLOSED_MEMBER_LEFT_ACTION = "auto_closed_member_left"
AUTO_REOPENED_MEMBER_RETURN_ACTION = "auto_reopened_member_return"


def _append_auto_lifecycle_event(
    service_case: ServiceCase,
    *,
    action: str,
    occurred_at: datetime,
    details: dict[str, Any] | None = None,
) -> None:
    """往轻量审计轨迹追加一条系统自动动作。

    occurred_at 取应用时钟的 ISO 字符串，只作人工排查展示；member_left_at/ended_at
    这类参与「本期聊天区间」切界的时间一律由调用方用数据库时钟写列。审计事件从不
    参与区间计算，两个时钟不同源无妨。
    """
    events = list(service_case.service_lifecycle_events or [])
    event: dict[str, Any] = {"action": action, "occurred_at": occurred_at.isoformat()}
    if details:
        event.update(details)
    events.append(event)
    # 整体重新赋值：SQLAlchemy 不检测 JSON 列的原地变异。
    service_case.service_lifecycle_events = events


def _last_lifecycle_action(service_case: ServiceCase) -> str | None:
    events = service_case.service_lifecycle_events or []
    if not events:
        return None
    action = events[-1].get("action")
    return str(action) if action else None


def close_case_on_member_left(
    service_case: ServiceCase,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> bool:
    """客户退群时立即收口：服务中的单子当场置为已完结，不再等运营逐个判断。

    - 只动 IN_SERVICE：前置状态（刚建单、还没有客户的空群）不收口——那批单没有
      发生过服务，批量完结只会虚增看板完成率；已完结/已退款的不覆盖人工结论。
    - ended_at 与 member_left_at 由调用方在同一个事务里用数据库时钟 func.now()
      赋值，PostgreSQL 的 now() 取事务起始时间，两列值完全一致；聊天记录的
      「本期」区间只认 member_left_at，不受影响。
    - 服务天数照旧从原起点连续计算，这里只在审计里记下「走时到第几天」供运营回看。
    """
    if service_case.status != ServiceCaseStatus.IN_SERVICE:
        return False
    service_case.status = ServiceCaseStatus.COMPLETED
    service_case.ended_at = func.now()
    _append_auto_lifecycle_event(
        service_case,
        action=AUTO_CLOSED_MEMBER_LEFT_ACTION,
        occurred_at=now,
        details={"service_day": max(effective_service_day(service_case, now, tz), 0)},
    )
    return True


def restore_case_on_member_return(
    service_case: ServiceCase,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> bool:
    """原客户回群接回时，把「因退群自动收口」的单子恢复成服务中。

    恢复要同时满足三条：
    - 当前是已完结——已退款的单子绝不自动复活；
    - 最后一条生命周期审计是「因退群自动结束」——正常毕业的单子不复活，
      要续服走群内 @机器人 的续服通道；
    - 28 天窗口还没走完（含续服中）——人回来时服务期已经过了就保持完结。
    """
    if service_case.status != ServiceCaseStatus.COMPLETED:
        return False
    if _last_lifecycle_action(service_case) != AUTO_CLOSED_MEMBER_LEFT_ACTION:
        return False
    if service_window_ended(service_case, now, tz):
        return False
    service_case.status = ServiceCaseStatus.IN_SERVICE
    service_case.ended_at = None
    _append_auto_lifecycle_event(
        service_case,
        action=AUTO_REOPENED_MEMBER_RETURN_ACTION,
        occurred_at=now,
    )
    return True
