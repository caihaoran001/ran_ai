from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_state_profile import UserStateProfile
from app.schemas.user import UserOperationsFact, UserOperationsSnapshot
from app.services.health_taxonomy import health_profile_dimension_labels


_DYNAMIC_DOMAIN_KEYS = (
    "service_state",
    "goals",
    "personal_preferences",
    "lifestyle_habits",
    "rhythm_structure",
    "product_usage",
    "body_feedback",
    "risk_profile",
    "outcome_changes",
)


async def build_user_operations_snapshot(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    state_profile: UserStateProfile | None,
) -> UserOperationsSnapshot:
    checkin_result = await session.execute(
        select(
            func.count(distinct(Checkin.checkin_date)),
            func.max(Checkin.created_at),
        ).where(
            Checkin.service_case_id == service_case.id,
            Checkin.deleted_at.is_(None),
        )
    )
    checkin_days, last_checkin_at = checkin_result.one()
    state_json = state_profile.state_json if state_profile else {}
    confirmed_guidance = latest_staff_guidance(state_json)
    latest_change = latest_dynamic_fact(state_json)
    attention_code, attention_label, attention_reason = attention_summary(service_case)
    updated_at = _latest_datetime(
        (
            confirmed_guidance.occurred_at if confirmed_guidance else None,
            latest_change.occurred_at if latest_change else None,
            last_checkin_at,
            state_profile.updated_at if state_profile else None,
            service_case.updated_at,
        )
    )
    return UserOperationsSnapshot(
        attention_code=attention_code,
        attention_label=attention_label,
        attention_reason=attention_reason,
        confirmed_guidance=confirmed_guidance,
        latest_change=latest_change,
        checkin_days=int(checkin_days or 0),
        last_checkin_at=last_checkin_at,
        updated_at=updated_at,
    )


def latest_staff_guidance(state_json: Any) -> UserOperationsFact | None:
    return _latest_fact(state_json, ("staff_guidance",), include_intake=True)


def latest_dynamic_fact(state_json: Any) -> UserOperationsFact | None:
    return _latest_fact(state_json, _DYNAMIC_DOMAIN_KEYS, include_intake=False)


def attention_summary(service_case: ServiceCase) -> tuple[str, str, str]:
    status = (
        service_case.status.value
        if isinstance(service_case.status, ServiceCaseStatus)
        else str(service_case.status)
    )
    if status in {ServiceCaseStatus.COMPLETED.value, ServiceCaseStatus.REFUNDED.value}:
        return "closed", "服务已结束", "当前没有进行中的服务任务。"
    if service_case.refund_pending_confirmation:
        return "action_required", "退款待确认", "已识别退款完成信号，等待运营确认。"
    pre_service_states = {
        ServiceCaseStatus.CREATED.value: (
            "watch",
            "等待入营问卷",
            "入营问卷尚未提交，服务还未开始。",
        ),
        ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED.value: (
            "watch",
            "画像生成中",
            "入营问卷已提交，正在生成画像与交付文件。",
        ),
        ServiceCaseStatus.GENERATING.value: (
            "watch",
            "画像生成中",
            "画像与交付文件正在生成，暂未进入正式服务。",
        ),
        ServiceCaseStatus.DOCUMENTS_GENERATED.value: (
            "watch",
            "文件待审核",
            "画像与交付文件已经生成，等待运营审核。",
        ),
        ServiceCaseStatus.REVIEWING.value: (
            "watch",
            "文件审核中",
            "交付文件正在审核，暂未进入正式服务。",
        ),
        ServiceCaseStatus.APPROVED.value: (
            "watch",
            "等待开始服务",
            "交付文件已审核通过，等待进入正式服务。",
        ),
    }
    if status in pre_service_states:
        return pre_service_states[status]
    if not service_case.ai_auto_reply_enabled:
        return (
            "manual",
            "人工接管中",
            _latest_reason(service_case.intervention_reasons) or "AI 自动回复已关闭。",
        )
    if service_case.risk_level == "high":
        return (
            "guarded",
            "需持续把关",
            _health_summary(service_case)
            or _latest_reason(service_case.risk_flags)
            or "当前服务单保留高风险标记。",
        )
    if service_case.risk_level == "medium":
        return (
            "watch",
            "需要留意",
            _latest_reason(service_case.risk_flags)
            or _latest_reason(service_case.intervention_reasons)
            or "当前服务单存在需要留意的记录。",
        )
    return "automatic", "自动服务中", "当前未发现需要持续人工接管的状态。"


def _latest_fact(
    state_json: Any,
    domain_keys: Iterable[str],
    *,
    include_intake: bool,
) -> UserOperationsFact | None:
    state = state_json if isinstance(state_json, dict) else {}
    domains = state.get("domains") if isinstance(state.get("domains"), dict) else {}
    candidates: list[tuple[datetime, UserOperationsFact]] = []
    for domain_key in domain_keys:
        domain = domains.get(domain_key) if isinstance(domains.get(domain_key), dict) else {}
        domain_label = str(domain.get("label") or domain_key)
        history = domain.get("history") if isinstance(domain.get("history"), list) else []
        for item in history:
            fact = _fact_from_item(
                item,
                domain=domain_key,
                domain_label=domain_label,
                include_intake=include_intake,
            )
            if fact and fact.occurred_at:
                candidates.append((_aware(fact.occurred_at), fact))

        if history:
            continue
        current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
        fact = _fact_from_current(
            current,
            domain=domain_key,
            domain_label=domain_label,
            include_intake=include_intake,
        )
        if fact and fact.occurred_at:
            candidates.append((_aware(fact.occurred_at), fact))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _fact_from_item(
    value: Any,
    *,
    domain: str,
    domain_label: str,
    include_intake: bool,
) -> UserOperationsFact | None:
    item = value if isinstance(value, dict) else {}
    source = str(item.get("source") or "")
    if not include_intake and source == "intake":
        return None
    fact_value = _fact_value(item.get("value"))
    occurred_at = _parse_datetime(item.get("occurred_at") or item.get("updated_at"))
    if not fact_value or not occurred_at:
        return None
    return UserOperationsFact(
        domain=domain,
        domain_label=domain_label,
        field_key=str(item.get("field_key") or "") or None,
        value=fact_value,
        evidence=str(item.get("evidence") or "").strip() or None,
        source=source or None,
        occurred_at=occurred_at,
    )


def _fact_from_current(
    current: dict[str, Any],
    *,
    domain: str,
    domain_label: str,
    include_intake: bool,
) -> UserOperationsFact | None:
    source = str(current.get("source") or "")
    if not include_intake and source == "intake":
        return None
    occurred_at = _parse_datetime(current.get("updated_at"))
    value = current.get("value")
    if not isinstance(value, dict) or not occurred_at:
        return None
    for field_key, field_value in reversed(value.items()):
        fact_value = _fact_value(field_value)
        if fact_value:
            return UserOperationsFact(
                domain=domain,
                domain_label=domain_label,
                field_key=str(field_key),
                value=fact_value,
                evidence=str(current.get("evidence") or "").strip() or None,
                source=source or None,
                occurred_at=occurred_at,
            )
    return None


def _fact_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        nested = value.get("value")
        if nested is not None:
            return _fact_value(nested)
    if isinstance(value, list):
        for item in reversed(value):
            nested = _fact_value(item)
            if nested:
                return nested
    return ""


def _latest_reason(items: Any) -> str:
    values = items if isinstance(items, list) else []
    for item in reversed(values):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or item.get("code") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if reason and evidence:
            return f"{reason}（{evidence}）"
        if reason or evidence:
            return reason or evidence
    return ""


def _health_summary(service_case: ServiceCase) -> str:
    dimensions = health_profile_dimension_labels(service_case.health_profile)
    for key in ("health_condition", "special_stage", "medication_category", "allergy_category"):
        labels = dimensions.get(key) or []
        if labels:
            return "、".join(labels[:2])
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_datetime(values: Iterable[datetime | None]) -> datetime | None:
    candidates = [_aware(item) for item in values if item is not None]
    return max(candidates) if candidates else None
