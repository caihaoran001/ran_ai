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
    "diet_structure": "饮食结构",
    "rhythm_structure": "节律结构",
    "product_usage": "产品使用",
    "body_feedback": "体感反馈",
    "service_state": "当前服务状态",
    "risk_profile": "风险信息",
    "outcome_changes": "成效变化",
    "staff_guidance": "班主任确认的个性化安排",
}

PROFILE_SIGNAL_DOMAIN_LABELS = {
    "personal_preferences": "个人喜好与偏好",
    "lifestyle_habits": "稳定习惯与生活约束",
}

PROFILE_SIGNAL_TYPE_DOMAIN: dict[str, str] = {
    "hobby": "personal_preferences",
    "activity_preference": "personal_preferences",
    "food_preference": "personal_preferences",
    "communication_preference": "personal_preferences",
    "motivation_preference": "personal_preferences",
    "meal_pattern": "lifestyle_habits",
    "hydration_habit": "lifestyle_habits",
    "supplement_habit": "lifestyle_habits",
    "schedule_constraint": "lifestyle_habits",
    "work_routine": "lifestyle_habits",
    "exercise_habit": "lifestyle_habits",
    "sleep_habit": "lifestyle_habits",
    "social_context": "lifestyle_habits",
}

STATE_SIGNAL_TYPES = frozenset(
    {
        "current_obstacle",
        "execution_status",
        "negative_change",
        "discomfort",
        "mood_state",
        "willingness",
        "unresolved_question",
        "user_commitment",
        "health_concern",
        "user_belief",
    }
)

# profile 分类的最后一道安全护栏：主判断仍由档案官的结构化工具输出完成。
_HEALTH_CONTEXT_MARKERS = (
    "月经",
    "经期",
    "手术",
    "术后",
    "恢复期",
    "用药",
    "怀孕",
    "哺乳",
    "疼痛",
    "头疼",
    "头痛",
    "胃疼",
    "胃痛",
    "腹痛",
    "腰痛",
    "痛经",
    "不适",
)
_PERSONAL_BELIEF_MARKERS = ("掉秤", "能瘦", "会瘦", "有效", "神器", "一定会")
_BELIEF_CONTEXT_MARKERS = ("吃", "喝", "空腹", "体重", "瘦", "秤")
_HYDRATION_MARKERS = ("喝水", "饮水")

# 成效表本身收全方向（好坏都要能统计），但**投影进 AI 用户背景小抄的只能是正向**——
# 这是本文件这道过滤的全部意义，不是可有可无的兜底，删掉会让助教天天看见"这人睡得差、
# 便秘、没瘦"。负向的当前状态由独立 service_state 域承接。与 outcome_engine 的
# POSITIVE_EVENT_TYPES 语义一致（此处独立定义，避免与 outcome_engine 形成循环导入）。
_POSITIVE_OUTCOME_EVENT_TYPES = frozenset({"improved", "completed"})


OUTCOME_FIELD_DOMAIN_MAP: dict[str, str] = {
    "profile_completed": "goals",
    "plan_understood": "goals",
    "documents_received": "goals",
    "started_execution": "product_usage",
    "product_schedule": "product_usage",
    "rhythm_understood": "rhythm_structure",
    "weight_change": "outcome_changes",
    "waist_or_body_change": "outcome_changes",
    "energy_change": "body_feedback",
    "sleep_change": "body_feedback",
    "bowel_change": "body_feedback",
    "execution_rate_signal": "product_usage",
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
    state = _ensure_state_shape(state_profile.state_json)
    now = _now_iso()
    overview = profile_json.get("overview") if isinstance(profile_json.get("overview"), dict) else {}

    _set_intake_domain_current(
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
    _set_intake_domain_current(
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
    _set_intake_domain_current(
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
    _set_intake_domain_current(
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
        _set_intake_domain_current(
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
    _set_intake_domain_current(
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
    if _has_dynamic_state(state):
        state_profile.summary = _state_summary(state)
        state_profile.source = "dynamic"
    else:
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
        event_type = str(event.get("event_type") or "")
        # 成效表收全方向，这里只放正向进入 AI 看的状态背景；负向留在账本里供统计。
        if event_type not in _POSITIVE_OUTCOME_EVENT_TYPES:
            continue
        field_key = str(event.get("field_key") or "")
        domain = OUTCOME_FIELD_DOMAIN_MAP.get(field_key, "outcome_changes")
        _update_domain_from_event(
            state,
            domain,
            field_key=field_key,
            value=str(event.get("value") or ""),
            event_type=event_type,
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
        signal_type = str(signal.get("signal_type") or "").strip()
        value = str(signal.get("value") or "").strip()
        evidence = str(signal.get("evidence_text") or "").strip()
        confidence = _float(signal.get("confidence"))
        if not value or not evidence or confidence < 0.65:
            continue

        supplement_name = str(signal.get("supplement_name") or "").strip()
        habit_status = str(signal.get("habit_status") or "").strip()
        supplement_category = str(signal.get("supplement_category") or "").strip()
        if signal_type == "supplement_habit":
            if not supplement_name or habit_status not in {"using", "considering", "stopped"}:
                continue
            if supplement_category not in {
                "micronutrient",
                "macronutrient",
                "health_supplement",
                "herbal",
                "other",
            }:
                supplement_category = "other"
            value = _supplement_fact_value(supplement_name, habit_status)

        # 补剂使用与咨询可与用药/健康咨询同时出现；工具 schema 已要求模型
        # 把处方药排除在 supplement_habit 之外，不再因原话带健康上下文而丢掉补剂事实。
        guarded_state_type = (
            None
            if signal_type == "supplement_habit"
            else _profile_guardrail_state_type(value=value, evidence=evidence)
        )
        if guarded_state_type:
            _update_domain_from_event(
                state,
                "service_state",
                field_key=guarded_state_type,
                value=_grounded_fact_value(value, evidence),
                event_type="profile_guardrail_reroute",
                evidence=evidence,
                source=("chat_profile_guardrail" if source == "chat" else f"{source}_profile_guardrail"),
                occurred_at=occurred_at,
                confidence=confidence,
                message_id=message.id,
                milestone="current_state",
                status="pending_confirmation",
            )
            continue

        if any(marker in f"{value}{evidence}" for marker in _HYDRATION_MARKERS):
            signal_type = "hydration_habit"
        expected_domain = PROFILE_SIGNAL_TYPE_DOMAIN.get(signal_type)
        if expected_domain is None or domain not in PROFILE_SIGNAL_DOMAIN_LABELS:
            continue
        # 类型已能唯一决定域，不让模型的 domain 误选把事实放错抽屉。
        domain = expected_domain
        _upsert_profile_fact(
            state,
            domain,
            field_key=signal_type,
            value=_grounded_fact_value(value, evidence),
            evidence=evidence,
            source=("chat_profile_signal" if source == "chat" else f"{source}_profile_signal"),
            occurred_at=occurred_at,
            confidence=confidence,
            message_id=message.id,
            fact_metadata=(
                {
                    "supplement_name": supplement_name,
                    "habit_status": habit_status,
                    "supplement_category": supplement_category,
                }
                if signal_type == "supplement_habit"
                else None
            ),
            identity_value=supplement_name if signal_type == "supplement_habit" else None,
        )

    state_profile.state_json = state
    flag_modified(state_profile, "state_json")
    state_profile.summary = _state_summary(state)
    state_profile.source = "dynamic"
    return state_profile


async def apply_state_signals_to_user_state(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    message: Message,
    state_signals: list[dict[str, Any]],
    source: str = "chat",
) -> UserStateProfile | None:
    """写入影响当前服务的阻塞、负向变化、执行状态和待解决事项。

    它们与稳定偏好不同：同一 state_type 的新事实代表当前状态更新，
    因此 current 保留最新一条，history 保留过程。
    """

    if not state_signals:
        return None
    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = _ensure_state_shape(state_profile.state_json)
    occurred_at = _datetime_iso(message.created_at)
    written = False
    for signal in state_signals:
        state_type = str(signal.get("state_type") or "").strip()
        value = str(signal.get("value") or "").strip()
        evidence = str(signal.get("evidence_text") or "").strip()
        confidence = _float(signal.get("confidence"))
        status = str(signal.get("status") or "active").strip()
        if state_type not in STATE_SIGNAL_TYPES or not value or not evidence or confidence < 0.6:
            continue
        if status not in {"active", "resolved"}:
            status = "active"
        _update_domain_from_event(
            state,
            "service_state",
            field_key=state_type,
            value=_grounded_fact_value(value, evidence),
            event_type="state_signal",
            evidence=evidence,
            source=("chat_state_signal" if source == "chat" else f"{source}_state_signal"),
            occurred_at=occurred_at,
            confidence=confidence,
            message_id=message.id,
            milestone="current_state",
            status=status,
        )
        written = True

    if not written:
        return None
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


def _set_intake_domain_current(
    state: dict[str, Any],
    domain_key: str,
    value: dict[str, Any],
    *,
    source: str,
    evidence: str,
    occurred_at: str,
    confidence: float,
) -> None:
    """刷新入营底稿，但不覆盖聊天后已经形成的当前事实和历史。"""

    domain = state["domains"][domain_key]
    clean_value = _clean_dict(value)
    current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
    current_value = current.get("value") if isinstance(current.get("value"), dict) else {}
    if current and str(current.get("source") or "") not in {"", "intake"}:
        # 动态事实优先；入营重建只补齐它没有占用的底稿字段。
        merged_value = {**clean_value, **current_value}
        domain["current"] = {**current, "value": merged_value}
    else:
        domain["current"] = {
            "value": clean_value,
            "source": source,
            "evidence": evidence,
            "updated_at": occurred_at,
            "confidence": confidence,
        }
    history = domain.get("history") if isinstance(domain.get("history"), list) else []
    domain["history"] = [
        item
        for item in history
        if not isinstance(item, dict) or str(item.get("source") or "") != "intake"
    ]
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
    status: str = "confirmed",
) -> None:
    if not value or not evidence:
        return
    domain = state["domains"][domain_key]
    current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
    current_value = current.get("value") if isinstance(current.get("value"), dict) else {}
    previous = current_value.get(field_key) if isinstance(current_value.get(field_key), dict) else {}
    first_seen_at = (
        str(previous.get("first_seen_at") or "")
        if _same_fact_value(previous.get("value"), value)
        else ""
    ) or occurred_at
    fact = {
        "value": value,
        "event_type": event_type,
        "milestone": milestone,
        "source": source,
        "evidence": evidence,
        "occurred_at": occurred_at,
        "confidence": confidence,
        "message_id": message_id,
        "status": status,
        "first_seen_at": first_seen_at,
        "last_confirmed_at": occurred_at,
    }
    current_value[field_key] = fact
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
            "status": status,
            "first_seen_at": first_seen_at,
            "last_confirmed_at": occurred_at,
        },
    )


def _upsert_profile_fact(
    state: dict[str, Any],
    domain_key: str,
    *,
    field_key: str,
    value: str,
    evidence: str,
    source: str,
    occurred_at: str,
    confidence: float,
    message_id: int | None,
    fact_metadata: dict[str, Any] | None = None,
    identity_value: str | None = None,
) -> None:
    """稳定偏好/习惯按事实多值保留；重复事实只刷新最后确认时间。"""

    if not value or not evidence:
        return
    domain = state["domains"][domain_key]
    current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
    current_value = current.get("value") if isinstance(current.get("value"), dict) else {}
    raw_facts = current_value.get(field_key)
    facts = raw_facts if isinstance(raw_facts, list) else ([raw_facts] if isinstance(raw_facts, dict) else [])
    normalized_facts: list[dict[str, Any]] = []
    matched = False
    fact_metadata = fact_metadata or {}
    for raw in facts:
        if not isinstance(raw, dict):
            continue
        existing = _enrich_legacy_fact(raw, current)
        same_identity = (
            _same_fact_value(existing.get("supplement_name"), identity_value)
            if identity_value
            else _same_fact_value(existing.get("value"), value)
        )
        if same_identity:
            existing.update(
                {
                    "value": value,
                    "evidence": evidence,
                    "source": source,
                    "confidence": confidence,
                    "occurred_at": occurred_at,
                    "message_id": message_id,
                    "status": "confirmed",
                    "last_confirmed_at": occurred_at,
                    **fact_metadata,
                }
            )
            existing.setdefault("first_seen_at", occurred_at)
            matched = True
        normalized_facts.append(existing)
    if not matched:
        normalized_facts.append(
            {
                "value": value,
                "event_type": "profile_signal",
                "milestone": "profile",
                "source": source,
                "evidence": evidence,
                "occurred_at": occurred_at,
                "confidence": confidence,
                "message_id": message_id,
                "status": "confirmed",
                "first_seen_at": occurred_at,
                "last_confirmed_at": occurred_at,
                **fact_metadata,
            }
        )
    current_limit = 10 if field_key == "supplement_habit" else 5
    current_value[field_key] = normalized_facts[-current_limit:]
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
            "event_type": "profile_signal",
            "milestone": "profile",
            "source": source,
            "evidence": evidence,
            "occurred_at": occurred_at,
            "confidence": confidence,
            "message_id": message_id,
            "status": "confirmed",
            "first_seen_at": occurred_at,
            "last_confirmed_at": occurred_at,
            **fact_metadata,
        },
    )


def _supplement_fact_value(supplement_name: str, habit_status: str) -> str:
    prefix = {
        "using": "正在使用",
        "considering": "咨询/考虑使用",
        "stopped": "已停用",
    }[habit_status]
    return f"{prefix}{supplement_name}"[:160]


def _enrich_legacy_fact(fact: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(fact)
    occurred_at = str(
        enriched.get("occurred_at")
        or enriched.get("last_confirmed_at")
        or current.get("updated_at")
        or _now_iso()
    )
    enriched.setdefault("source", current.get("source") or "legacy")
    enriched.setdefault("evidence", current.get("evidence") or "历史画像事实")
    enriched.setdefault("confidence", current.get("confidence") or 0.0)
    enriched.setdefault("occurred_at", occurred_at)
    enriched.setdefault("message_id", current.get("message_id"))
    enriched.setdefault("status", "confirmed")
    enriched.setdefault("first_seen_at", occurred_at)
    enriched.setdefault("last_confirmed_at", occurred_at)
    return enriched


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
        "service_state",
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
        current_value = current.get("value") if isinstance(current.get("value"), dict) else {}
        facts: list[str] = []
        for raw in current_value.values():
            values = raw if isinstance(raw, list) else [raw]
            for item in values:
                if isinstance(item, dict) and str(item.get("status") or "") == "resolved":
                    continue
                fact = str(item.get("value") or "").strip() if isinstance(item, dict) else str(item or "").strip()
                if fact and fact not in facts:
                    facts.append(fact)
        if facts:
            snippets.append(f"{domain.get('label') or key}：{'、'.join(facts[-3:])}")
    return "；".join(snippets)[:1000]


def _profile_guardrail_state_type(*, value: str, evidence: str) -> str | None:
    text = f"{value}{evidence}"
    if any(marker in text for marker in _HEALTH_CONTEXT_MARKERS):
        return "health_concern"
    if any(marker in text for marker in _PERSONAL_BELIEF_MARKERS) and any(
        marker in text for marker in _BELIEF_CONTEXT_MARKERS
    ):
        return "user_belief"
    return None


def _grounded_fact_value(value: str, evidence: str) -> str:
    """保留档案官的简短事实；原话证据在事实记录内独立保存。"""

    cleaned_value = " ".join(value.split()).strip()
    cleaned_evidence = " ".join(evidence.split()).strip()
    return cleaned_value[:160] or cleaned_evidence[:160]


def _same_fact_value(left: Any, right: Any) -> bool:
    def normalize(item: Any) -> str:
        return "".join(str(item or "").split()).strip("，。！？；;,.!?")

    return bool(normalize(left)) and normalize(left) == normalize(right)


def _has_dynamic_state(state: dict[str, Any]) -> bool:
    domains = state.get("domains") if isinstance(state.get("domains"), dict) else {}
    for domain in domains.values():
        if not isinstance(domain, dict):
            continue
        current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
        if current and str(current.get("source") or "") not in {"", "intake"}:
            return True
        history = domain.get("history") if isinstance(domain.get("history"), list) else []
        if any(
            isinstance(item, dict) and str(item.get("source") or "") not in {"", "intake"}
            for item in history
        ):
            return True
    return False


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
