from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.models.service_case import ServiceCase, ServiceCaseStatus


PRE_SERVICE_STATUSES: frozenset[ServiceCaseStatus] = frozenset(
    {
        ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED,
        ServiceCaseStatus.GENERATING,
        ServiceCaseStatus.DOCUMENTS_GENERATED,
        ServiceCaseStatus.REVIEWING,
        ServiceCaseStatus.APPROVED,
    }
)


def planned_service_start_at(service_case: ServiceCase, tz: ZoneInfo) -> datetime | None:
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
