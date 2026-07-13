from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.message import Message
from app.models.service_case import ServiceCase
from app.models.user_state_profile import UserStateProfile


STATE_DOMAIN_LABELS: dict[str, str] = {
    "basic": "基础信息",
    "goals": "改善目标与关注点",
    "need_analysis": "健康需求识别",
    "personal_preferences": "个人喜好与偏好",
    "lifestyle_habits": "稳定习惯与生活约束",
    "action_focus": "当前行动重点",
    "diet_structure": "饮食结构",
    "rhythm_structure": "节律结构",
    "product_usage": "产品使用",
    "body_feedback": "体感反馈",
    "risk_profile": "风险信息",
    "outcome_changes": "成效变化",
    "staff_guidance": "班主任确认的个性化安排",
}

PROFILE_SIGNAL_DOMAIN_LABELS = {
    "personal_preferences": "个人喜好与偏好",
    "lifestyle_habits": "稳定习惯与生活约束",
}


OUTCOME_FIELD_DOMAIN_MAP: dict[str, str] = {
    "profile_completed": "goals",
    "plan_understood": "goals",
    "documents_received": "goals",
    "started_execution": "product_usage",
    "product_schedule": "product_usage",
    "discomfort_feedback": "body_feedback",
    "rhythm_understood": "rhythm_structure",
    "weight_change": "outcome_changes",
    "waist_or_body_change": "outcome_changes",
    "energy_change": "body_feedback",
    "sleep_change": "body_feedback",
    "bowel_change": "body_feedback",
    "plateau_signal": "outcome_changes",
    "execution_rate_signal": "product_usage",
    "adjustment_need": "goals",
    "positive_body_signal": "body_feedback",
    "challenge_goal_result": "outcome_changes",
    "weight_result": "outcome_changes",
    "waist_result": "outcome_changes",
    "habit_stability": "rhythm_structure",
    "next_stage_intent": "goals",
    "lifestyle_habit": "rhythm_structure",
    "long_term_weight_stability": "outcome_changes",
    "self_management": "goals",
    "long_term_membership": "goals",
    "referral_signal": "goals",
    "brand_advocacy": "goals",
}


async def initialize_user_state_from_intake(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    profile_json: dict[str, Any],
    intake_facts: dict[str, Any],
) -> UserStateProfile:
    """Create or refresh the current-state projection from intake facts.

    Intake facts are the stable baseline. Later chat/check-in signals should not
    mutate these deterministic facts directly; they add evidence to dynamic
    domains such as body feedback, product usage and outcome changes.
    """

    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = _base_state()
    now = _now_iso()
    overview = profile_json.get("overview") if isinstance(profile_json.get("overview"), dict) else {}

    _set_domain_current(
        state,
        "basic",
        {
            "name": intake_facts.get("legal_name") or intake_facts.get("name"),
            "preferred_name": intake_facts.get("preferred_name"),
            "gender": intake_facts.get("gender"),
            "age": intake_facts.get("age"),
            "height_cm": intake_facts.get("height_cm"),
            "weight_kg": intake_facts.get("weight_kg"),
            "bmi": intake_facts.get("bmi"),
            "waist": intake_facts.get("waist"),
            "bodyfat": intake_facts.get("bodyfat"),
            "weight_loss_band": intake_facts.get("weight_loss_band") or overview.get("weight_loss_band"),
        },
        source="intake",
        evidence="入营问卷基础信息",
        occurred_at=now,
        confidence=1.0,
    )
    _set_domain_current(
        state,
        "goals",
        {
            "main_needs": intake_facts.get("main_needs"),
            "day28_expectation": intake_facts.get("day28_expectation"),
            "day28_goal": profile_json.get("day28_goal"),
            "main_problem": profile_json.get("main_problem"),
        },
        source="intake",
        evidence="入营问卷中的服务需求与28天期待",
        occurred_at=now,
        confidence=1.0,
    )
    need_analysis = profile_json.get("need_analysis") if isinstance(profile_json.get("need_analysis"), dict) else {}
    _set_domain_current(
        state,
        "need_analysis",
        {
            "surface_needs": need_analysis.get("surface_needs") or intake_facts.get("main_needs"),
            "deep_needs": need_analysis.get("deep_needs"),
            "priority_need": need_analysis.get("priority_need") or profile_json.get("main_problem"),
            "system_outputs": need_analysis.get("system_outputs"),
            "next_actions": need_analysis.get("next_actions"),
            "evidence_notes": need_analysis.get("evidence_notes"),
        },
        source="intake",
        evidence="入营问卷健康需求识别结果",
        occurred_at=now,
        confidence=0.9,
    )
    _set_domain_current(
        state,
        "diet_structure",
        {
            "diet_pattern": intake_facts.get("diet_pattern"),
            "profile_state": profile_json.get("state"),
        },
        source="intake",
        evidence="入营问卷饮食结构与状态判断",
        occurred_at=now,
        confidence=0.95,
    )
    diet_pattern = intake_facts.get("diet_pattern")
    if diet_pattern and str(diet_pattern) not in {"未提供", "暂无数据"}:
        _set_domain_current(
            state,
            "lifestyle_habits",
            {
                "meal_pattern": diet_pattern,
            },
            source="intake",
            evidence="入营问卷长期饮食模式",
            occurred_at=now,
            confidence=0.9,
        )
    _set_domain_current(
        state,
        "risk_profile",
        {
            "health_note": intake_facts.get("health_note"),
            "risks": profile_json.get("risks"),
            "adherence_worry": intake_facts.get("adherence_worry"),
            "pressure_reaction": intake_facts.get("pressure_reaction"),
        },
        source="intake",
        evidence="入营问卷健康补充、坚持风险与压力反应",
        occurred_at=now,
        confidence=0.95,
    )

    state_profile.state_json = state
    flag_modified(state_profile, "state_json")
    state_profile.summary = str(profile_json.get("summary") or "")[:2000]
    state_profile.source = "intake"
    await session.flush()
    return state_profile


async def apply_outcome_events_to_user_state(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    message: Message,
    milestone: str,
    created_events: list[dict[str, Any]],
    source: str = "chat",
) -> UserStateProfile | None:
    """Update current-state projection from validated outcome events.

    Each created outcome event is already deduped by `outcome_engine`. This
    function uses the same evidence as the state evidence chain, so profile and
    outcome statistics can explain themselves from one user sentence.
    """

    if not created_events:
        return None
    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = _ensure_state_shape(state_profile.state_json)
    occurred_at = _datetime_iso(message.created_at)

    for event in created_events:
        field_key = str(event.get("field_key") or "")
        domain = OUTCOME_FIELD_DOMAIN_MAP.get(field_key, "outcome_changes")
        _update_domain_from_event(
            state,
            domain,
            field_key=field_key,
            value=str(event.get("value") or ""),
            event_type=str(event.get("event_type") or "observation"),
            evidence=str(event.get("evidence_text") or ""),
            source=source,
            occurred_at=occurred_at,
            confidence=_float(event.get("confidence")),
            message_id=message.id,
            milestone=milestone,
        )

    state_profile.state_json = state
    flag_modified(state_profile, "state_json")
    state_profile.summary = _state_summary(state)
    state_profile.source = "dynamic"
    return state_profile


async def apply_profile_signals_to_user_state(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    message: Message,
    profile_signals: list[dict[str, Any]],
    source: str = "chat",
) -> UserStateProfile | None:
    """Persist long-lived personal profile signals from chat evidence.

    Outcome events describe service milestones. Profile signals describe stable
    personal context such as hobbies, meal patterns, work constraints or food
    preferences. Keeping them in separate domains prevents casual facts like
    "喜欢漂流" from being lost inside generic outcome metrics.
    """

    if not profile_signals:
        return None
    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = _ensure_state_shape(state_profile.state_json)
    occurred_at = _datetime_iso(message.created_at)

    for signal in profile_signals:
        domain = str(signal.get("domain") or "")
        signal_type = str(signal.get("signal_type") or "other").strip() or "other"
        value = str(signal.get("value") or "").strip()
        evidence = str(signal.get("evidence_text") or "").strip()
        confidence = _float(signal.get("confidence"))
        if domain not in PROFILE_SIGNAL_DOMAIN_LABELS or not value or not evidence or confidence < 0.65:
            continue
        _update_domain_from_event(
            state,
            domain,
            field_key=signal_type,
            value=value,
            event_type="profile_signal",
            evidence=evidence,
            source=("chat_profile_signal" if source == "chat" else f"{source}_profile_signal"),
            occurred_at=occurred_at,
            confidence=confidence,
            message_id=message.id,
            milestone="profile",
        )

    state_profile.state_json = state
    flag_modified(state_profile, "state_json")
    state_profile.summary = _state_summary(state)
    state_profile.source = "dynamic"
    return state_profile


async def apply_staff_correction_to_user_state(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    staff_message: Message,
    summary: str,
    evidence: str,
    confidence: float,
) -> UserStateProfile | None:
    """把班主任确认的个人安排写进当前用户背景，只影响本服务单。"""

    summary = summary.strip()
    evidence = evidence.strip()
    confidence = _float(confidence)
    if not summary or not evidence or confidence < 0.7:
        return None
    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = _ensure_state_shape(state_profile.state_json)
    _update_domain_from_event(
        state,
        "staff_guidance",
        field_key=f"staff_correction_{staff_message.id}",
        value=summary,
        event_type="staff_correction",
        evidence=evidence,
        source="archivist_staff_correction",
        occurred_at=_datetime_iso(staff_message.created_at),
        confidence=confidence,
        message_id=staff_message.id,
        milestone="correction",
    )
    state_profile.state_json = state
    flag_modified(state_profile, "state_json")
    state_profile.summary = _state_summary(state)
    state_profile.source = "dynamic"
    return state_profile


async def apply_staff_guidance_to_user_state(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    staff_message: Message,
    guidance: str,
    evidence: str,
    confidence: float,
) -> UserStateProfile | None:
    """把班主任确认的专属指导写进当前用户背景，只影响本服务单。"""

    guidance = guidance.strip()
    evidence = evidence.strip()
    confidence = _float(confidence)
    if not guidance or not evidence or confidence < 0.7:
        return None
    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = _ensure_state_shape(state_profile.state_json)
    _update_domain_from_event(
        state,
        "staff_guidance",
        field_key=f"staff_guidance_{staff_message.id}",
        value=guidance,
        event_type="staff_guidance",
        evidence=evidence,
        source="archivist_staff_guidance",
        occurred_at=_datetime_iso(staff_message.created_at),
        confidence=confidence,
        message_id=staff_message.id,
        milestone="guidance",
    )
    state_profile.state_json = state
    flag_modified(state_profile, "state_json")
    state_profile.summary = _state_summary(state)
    state_profile.source = "dynamic"
    return state_profile


async def get_or_create_user_state_profile(
    session: AsyncSession,
    service_case_id: int,
) -> UserStateProfile:
    state_profile = await session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == service_case_id)
    )
    if state_profile:
        state_profile.state_json = _ensure_state_shape(state_profile.state_json)
        return state_profile
    state_profile = UserStateProfile(
        service_case_id=service_case_id,
        state_json=_base_state(),
        summary=None,
        source="system",
    )
    session.add(state_profile)
    await session.flush()
    return state_profile


def _base_state() -> dict[str, Any]:
    return {
        "version": 1,
        "domains": {
            key: {
                "label": label,
                "current": {},
                "history": [],
            }
            for key, label in STATE_DOMAIN_LABELS.items()
        },
    }


def _ensure_state_shape(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    base = _base_state()
    domains = state.get("domains") if isinstance(state.get("domains"), dict) else {}
    for key, domain in base["domains"].items():
        existing = domains.get(key) if isinstance(domains.get(key), dict) else {}
        domain.update(existing)
        domain.setdefault("label", STATE_DOMAIN_LABELS[key])
        domain.setdefault("current", {})
        domain.setdefault("history", [])
        domains[key] = domain
    state["version"] = state.get("version") or 1
    state["domains"] = domains
    return state


def _set_domain_current(
    state: dict[str, Any],
    domain_key: str,
    value: dict[str, Any],
    *,
    source: str,
    evidence: str,
    occurred_at: str,
    confidence: float,
) -> None:
    domain = state["domains"][domain_key]
    clean_value = _clean_dict(value)
    domain["current"] = {
        "value": clean_value,
        "source": source,
        "evidence": evidence,
        "updated_at": occurred_at,
        "confidence": confidence,
    }
    _append_history(
        domain,
        {
            "value": clean_value,
            "source": source,
            "evidence": evidence,
            "occurred_at": occurred_at,
            "confidence": confidence,
        },
    )


def _update_domain_from_event(
    state: dict[str, Any],
    domain_key: str,
    *,
    field_key: str,
    value: str,
    event_type: str,
    evidence: str,
    source: str,
    occurred_at: str,
    confidence: float,
    message_id: int | None,
    milestone: str,
) -> None:
    if not value or not evidence:
        return
    domain = state["domains"][domain_key]
    current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
    current_value = current.get("value") if isinstance(current.get("value"), dict) else {}
    current_value[field_key] = {
        "value": value,
        "event_type": event_type,
        "milestone": milestone,
    }
    domain["current"] = {
        "value": current_value,
        "source": source,
        "evidence": evidence,
        "updated_at": occurred_at,
        "confidence": confidence,
        "message_id": message_id,
    }
    _append_history(
        domain,
        {
            "field_key": field_key,
            "value": value,
            "event_type": event_type,
            "milestone": milestone,
            "source": source,
            "evidence": evidence,
            "occurred_at": occurred_at,
            "confidence": confidence,
            "message_id": message_id,
        },
    )


def _append_history(domain: dict[str, Any], item: dict[str, Any]) -> None:
    history = domain.get("history")
    if not isinstance(history, list):
        history = []
    marker = (item.get("field_key"), item.get("evidence"), item.get("message_id"))
    if marker[1] and any((old.get("field_key"), old.get("evidence"), old.get("message_id")) == marker for old in history if isinstance(old, dict)):
        domain["history"] = history
        return
    history.append(item)
    domain["history"] = history[-30:]


def _state_summary(state: dict[str, Any]) -> str:
    domains = state.get("domains") if isinstance(state.get("domains"), dict) else {}
    snippets: list[str] = []
    for key in (
        "goals",
        "personal_preferences",
        "lifestyle_habits",
        "product_usage",
        "body_feedback",
        "outcome_changes",
        "staff_guidance",
        "risk_profile",
    ):
        domain = domains.get(key) if isinstance(domains.get(key), dict) else {}
        current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
        evidence = current.get("evidence")
        if evidence:
            snippets.append(f"{domain.get('label') or key}：{evidence}")
    return "；".join(snippets)[:1000]


def _clean_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", "未提供", "暂无数据")
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _datetime_iso(value: datetime | None) -> str:
    if value is None:
        return _now_iso()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _float(value: Any) -> float:
    try:
        return max(min(float(value), 1.0), 0.0)
    except (TypeError, ValueError):
        return 0.0
