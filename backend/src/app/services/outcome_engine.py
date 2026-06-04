from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.checkin import Checkin
from app.models.message import Message
from app.models.outcome import OutcomeEvent, OutcomeSnapshot, OutcomeStageState
from app.models.service_case import ServiceCase
from app.models.user_state_profile import UserStateProfile
from app.services.ai_service import AIService
from app.services.user_state_profile import apply_outcome_events_to_user_state, get_or_create_user_state_profile

logger = logging.getLogger(__name__)

VALID_EVENT_TYPES = frozenset({
    "improved", "worsened", "unchanged", "completed", "not_completed",
    "feedback", "discomfort",
})

FOLLOWUP_COOLDOWN = timedelta(hours=12)
ACTION_FOCUS_THRESHOLD = 0.85
ACTION_FOCUS_LEAD_MARGIN = 0.2
ACTION_FOCUS_SOFT_LOCK = timedelta(days=3)
ACTION_FOCUS_DECAY_PER_DAY = 0.08
CHINA_TZ = ZoneInfo("Asia/Shanghai")


ACTION_FOCUS_DEFINITIONS: dict[str, dict[str, str]] = {
    "sleep_wind_down": {
        "label": "睡眠收口",
        "action": "接下来先抓睡前流程和晚间节律，不额外增加任务。",
    },
    "evening_control": {
        "label": "晚间收口",
        "action": "接下来先抓晚餐后不加餐、晚A时机和睡前边界。",
    },
    "product_timing": {
        "label": "产品时机调优",
        "action": "接下来先复核早C/午B/晚A的随餐、前移或减量安排。",
    },
    "bowel_rhythm": {
        "label": "排便与肠道节律",
        "action": "接下来先观察排便、水分、蔬菜和轻运动的连续性。",
    },
    "meal_structure": {
        "label": "饮食结构",
        "action": "接下来先抓主食、蛋白、蔬菜和加餐边界。",
    },
    "execution_simplify": {
        "label": "低摩擦执行",
        "action": "接下来先减少任务数量，只保留最容易完成的一两个动作。",
    },
    "safety_review": {
        "label": "安全与人工复核",
        "action": "接下来先安全优先，暂停加码并交由班主任复核。",
    },
    "motivation_support": {
        "label": "鼓励与陪跑",
        "action": "接下来先降低压力，多给正反馈和可完成的小动作。",
    },
}


FIELD_ACTION_RULES: dict[str, tuple[tuple[str, float], ...]] = {
    "sleep_change": (("sleep_wind_down", 0.35),),
    "bowel_change": (("bowel_rhythm", 0.35),),
    "discomfort_feedback": (("safety_review", 1.0), ("product_timing", 0.55)),
    "product_schedule": (("product_timing", 0.35), ("execution_simplify", 0.2)),
    "rhythm_understood": (("product_timing", 0.25),),
    "started_execution": (("execution_simplify", 0.2),),
    "plan_understood": (("execution_simplify", 0.2),),
    "execution_rate_signal": (("execution_simplify", 0.45),),
    "adjustment_need": (("evening_control", 0.35), ("product_timing", 0.25)),
    "plateau_signal": (("meal_structure", 0.35), ("execution_simplify", 0.2)),
    "energy_change": (("sleep_wind_down", 0.2), ("product_timing", 0.15)),
    "positive_body_signal": (("motivation_support", 0.2),),
    "habit_stability": (("execution_simplify", 0.2),),
    "next_stage_intent": (("motivation_support", 0.2),),
}


@dataclass(frozen=True)
class OutcomeGoal:
    key: str
    label: str
    evidence_examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutcomeMilestoneSpec:
    milestone: str
    day: int
    name: str
    goals: tuple[OutcomeGoal, ...]


@dataclass(frozen=True)
class OutcomeExtractionPlan:
    service_case_id: int
    message_id: int
    milestone: str
    content: str
    context: dict[str, Any]


MILESTONE_SPECS: tuple[OutcomeMilestoneSpec, ...] = (
    OutcomeMilestoneSpec(
        milestone="day1",
        day=1,
        name="第1天建档与理解",
        goals=(
            OutcomeGoal("profile_completed", "是否完成建档", ("问卷提交了", "方案收到了")),
            OutcomeGoal("plan_understood", "是否理解方案", ("知道接下来怎么做", "不太明白怎么喝")),
            OutcomeGoal("documents_received", "是否收到交付文件", ("文件收到了", "我看完方案了")),
        ),
    ),
    OutcomeMilestoneSpec(
        milestone="day3",
        day=3,
        name="第3天启动执行",
        goals=(
            OutcomeGoal("started_execution", "是否开始执行", ("今天开始喝了", "还没开始")),
            OutcomeGoal("product_schedule", "早C/午B/晚A是否能跟上", ("午B忘了", "三杯都完成")),
            OutcomeGoal("discomfort_feedback", "是否有不适反馈", ("有点心慌", "睡不着", "没有不舒服")),
            OutcomeGoal("rhythm_understood", "是否理解执行节奏", ("几点喝", "随餐怎么喝")),
        ),
    ),
    OutcomeMilestoneSpec(
        milestone="day7",
        day=7,
        name="第7天体感变化",
        goals=(
            OutcomeGoal("weight_change", "体重变化", ("轻了1斤", "体重没变", "涨了0.5斤")),
            OutcomeGoal("waist_or_body_change", "腰围/裤腰/体感变化", ("裤腰松了", "腰围小了", "肚子没变化")),
            OutcomeGoal("energy_change", "精神状态变化", ("下午没那么困", "精神稳一点", "还是很疲惫")),
            OutcomeGoal("sleep_change", "睡眠变化", ("睡得更好了", "还是熬夜", "失眠")),
            OutcomeGoal("bowel_change", "排便变化", ("排便顺了", "便秘", "肚子胀")),
        ),
    ),
    OutcomeMilestoneSpec(
        milestone="day14",
        day=14,
        name="第14天平台期与调方案",
        goals=(
            OutcomeGoal("plateau_signal", "是否疑似平台期", ("体重卡住", "一周没变化")),
            OutcomeGoal("execution_rate_signal", "执行率是否够", ("这周漏了几次", "基本都完成")),
            OutcomeGoal("adjustment_need", "是否需要调整方案", ("晚A喝不下", "晚餐总收不住")),
            OutcomeGoal("positive_body_signal", "是否有正向体感", ("腰腹松了", "精神变好")),
        ),
    ),
    OutcomeMilestoneSpec(
        milestone="day28",
        day=28,
        name="第28天目标达成",
        goals=(
            OutcomeGoal("challenge_goal_result", "是否达成挑战/稳健/保底目标", ("达标了", "差一点", "没达到")),
            OutcomeGoal("weight_result", "28天体重结果", ("瘦了3斤", "体重没变")),
            OutcomeGoal("waist_result", "28天腰围/体态结果", ("腰围小了", "裤子松了")),
            OutcomeGoal("habit_stability", "执行习惯稳定性", ("已经习惯了", "还是靠提醒")),
            OutcomeGoal("next_stage_intent", "下一阶段意向", ("还想继续", "先停一下")),
        ),
    ),
    OutcomeMilestoneSpec(
        milestone="day90",
        day=90,
        name="第90天生活方式习惯",
        goals=(
            OutcomeGoal("lifestyle_habit", "是否形成生活方式习惯", ("已经自然这么吃", "不需要提醒")),
            OutcomeGoal("long_term_weight_stability", "体重/围度是否稳定", ("稳定住了", "又反弹")),
            OutcomeGoal("self_management", "自我管理能力", ("知道怎么调整", "出差也能执行")),
        ),
    ),
    OutcomeMilestoneSpec(
        milestone="day365",
        day=365,
        name="第365天长期会员与推荐",
        goals=(
            OutcomeGoal("long_term_membership", "是否成为长期会员", ("一直跟着", "长期续")),
            OutcomeGoal("referral_signal", "是否推荐他人", ("推荐朋友", "拉同事来")),
            OutcomeGoal("brand_advocacy", "是否有品牌认同", ("这个方法适合我", "愿意分享")),
        ),
    ),
)


MILESTONE_BY_KEY = {spec.milestone: spec for spec in MILESTONE_SPECS}


async def analyze_and_update_outcomes(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    message: Message,
    intervention_analysis: dict[str, Any] | None,
    recent_messages: list[str] | None = None,
) -> dict[str, Any]:
    """Extract outcome evidence from one chat message and update stage state.

    This is intentionally a controlled sidecar: the LLM proposes structured facts,
    and this service validates/dedupes before writing state.
    """

    if not message.content.strip():
        return {}

    spec = current_milestone_spec(service_case)
    state = await get_or_create_stage_state(session, service_case.id, spec)
    followup_time_context = await _build_followup_time_context(
        session,
        service_case=service_case,
        spec=spec,
        state=state,
    )
    context = _analysis_context(
        service_case=service_case,
        spec=spec,
        state=state,
        intervention_analysis=intervention_analysis,
        recent_messages=recent_messages or [],
        followup_time_context=followup_time_context,
    )
    context["message_id"] = message.id

    raw = await _extract_outcome_safely(message.content, context)
    result = await apply_outcome_analysis(
        session,
        service_case=service_case,
        message=message,
        spec=spec,
        state=state,
        raw=raw,
        intervention_analysis=intervention_analysis,
        allowed_followup_fields=_allowed_followup_fields_from_context(context),
    )
    return result


async def prepare_outcome_extraction(
    session: AsyncSession,
    *,
    service_case_id: int,
    message_id: int,
    intervention_analysis: dict[str, Any] | None,
    recent_messages: list[str] | None = None,
) -> OutcomeExtractionPlan | None:
    service_case = await session.scalar(
        select(ServiceCase).where(
            ServiceCase.id == service_case_id,
            ServiceCase.deleted_at.is_(None),
        )
    )
    message = await session.get(Message, message_id)
    if not service_case or not message or not message.content.strip():
        return None
    spec = current_milestone_spec(service_case)
    state = await get_or_create_stage_state(session, service_case.id, spec)
    followup_time_context = await _build_followup_time_context(
        session,
        service_case=service_case,
        spec=spec,
        state=state,
    )
    context = _analysis_context(
        service_case=service_case,
        spec=spec,
        state=state,
        intervention_analysis=intervention_analysis,
        recent_messages=recent_messages or [],
        followup_time_context=followup_time_context,
    )
    context["message_id"] = message.id
    return OutcomeExtractionPlan(
        service_case_id=service_case.id,
        message_id=message.id,
        milestone=spec.milestone,
        content=message.content,
        context=context,
    )


async def extract_outcome_from_plan(plan: OutcomeExtractionPlan) -> dict[str, Any]:
    return await _extract_outcome_safely(plan.content, plan.context)


async def apply_outcome_analysis_plan(
    session: AsyncSession,
    *,
    plan: OutcomeExtractionPlan,
    raw: dict[str, Any],
    intervention_analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    service_case = await session.scalar(
        select(ServiceCase).where(
            ServiceCase.id == plan.service_case_id,
            ServiceCase.deleted_at.is_(None),
        )
    )
    message = await session.get(Message, plan.message_id)
    spec = MILESTONE_BY_KEY.get(plan.milestone)
    if not service_case or not message or spec is None:
        return {}
    state = await get_or_create_stage_state(session, service_case.id, spec)
    return await apply_outcome_analysis(
        session,
        service_case=service_case,
        message=message,
        spec=spec,
        state=state,
        raw=raw,
        intervention_analysis=intervention_analysis,
        allowed_followup_fields=_allowed_followup_fields_from_context(plan.context),
    )


async def get_or_create_stage_state(
    session: AsyncSession,
    service_case_id: int,
    spec: OutcomeMilestoneSpec,
) -> OutcomeStageState:
    state = await session.scalar(
        select(OutcomeStageState).where(
            OutcomeStageState.service_case_id == service_case_id,
            OutcomeStageState.milestone == spec.milestone,
        )
    )
    if state:
        return state
    state = OutcomeStageState(
        service_case_id=service_case_id,
        milestone=spec.milestone,
        status="active",
        collected_fields={},
        missing_fields=[goal.key for goal in spec.goals],
        followup_count=0,
    )
    session.add(state)
    await session.flush()
    return state


async def record_intake_profile_completed(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    evidence_text: str = "用户提交入营问卷",
) -> OutcomeEvent | None:
    """Record deterministic day1 evidence from the intake submission flow.

    This does not go through the chat/outcome LLM because questionnaire submit is
    a first-class system event. Without this bridge, day1 progress can miss
    `profile_completed` even though the intake HTTP API has already accepted the
    submission.
    """

    spec = MILESTONE_BY_KEY["day1"]
    item = {
        "field_key": "profile_completed",
        "event_type": "completed",
        "value": "已完成入营问卷提交",
        "evidence_text": evidence_text,
        "confidence": 1.0,
    }
    event: OutcomeEvent | None = None

    now = datetime.now(timezone.utc)
    if not await _event_exists(session, service_case.id, None, item):
        event = OutcomeEvent(
            service_case_id=service_case.id,
            message_id=None,
            milestone=spec.milestone,
            field_key=item["field_key"],
            event_type=item["event_type"],
            value_text=item["value"],
            evidence_text=item["evidence_text"],
            confidence=item["confidence"],
            source="questionnaire",
            occurred_at=now,
            metadata_json={"template_key": "intake"},
        )
        session.add(event)

    state = await get_or_create_stage_state(session, service_case.id, spec)
    collected = dict(state.collected_fields or {})
    collected[item["field_key"]] = {
        "value": item["value"],
        "evidence_text": item["evidence_text"],
        "event_type": item["event_type"],
        "confidence": item["confidence"],
        "message_id": None,
        "updated_at": now.isoformat(),
        "source": "questionnaire",
    }
    state.collected_fields = collected
    state.missing_fields = [goal.key for goal in spec.goals if goal.key not in collected]
    await session.flush()
    await generate_outcome_snapshot(session, service_case, milestone=spec.milestone)
    return event


async def apply_outcome_analysis(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    message: Message,
    spec: OutcomeMilestoneSpec,
    state: OutcomeStageState,
    raw: dict[str, Any],
    intervention_analysis: dict[str, Any] | None,
    allowed_followup_fields: set[str] | None = None,
) -> dict[str, Any]:
    valid_fields = {goal.key for goal in spec.goals}
    collected = dict(state.collected_fields or {})
    created_events: list[dict[str, Any]] = []

    for item in _normalize_events(raw.get("outcome_events"), valid_fields):
        if await _event_exists(session, service_case.id, message.id, item):
            continue
        event = OutcomeEvent(
            service_case_id=service_case.id,
            message_id=message.id,
            milestone=spec.milestone,
            field_key=item["field_key"],
            event_type=item["event_type"],
            value_text=item["value"],
            evidence_text=item["evidence_text"],
            confidence=item["confidence"],
            source="chat",
            occurred_at=message.created_at,
            metadata_json={"message_channel": message.message_type},
        )
        session.add(event)
        collected[item["field_key"]] = {
            "value": item["value"],
            "evidence_text": item["evidence_text"],
            "event_type": item["event_type"],
            "confidence": item["confidence"],
            "message_id": message.id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        created_events.append(item)

    missing = [goal.key for goal in spec.goals if goal.key not in collected]
    state.collected_fields = collected
    state.missing_fields = missing

    followup_plan = _normalize_followup(raw.get("followup_plan"), missing, allowed_followup_fields)
    if (
        service_case.needs_human_intervention
        or service_case.risk_level == "high"
        or _must_suppress_followup(intervention_analysis)
    ):
        followup_plan = {"should_ask": False, "field_key": "", "question": "", "reason": "风险或人工介入场景，暂停成效追问。"}
    elif followup_plan.get("should_ask") and _is_followup_in_cooldown(state):
        followup_plan = {
            "should_ask": False,
            "field_key": "",
            "question": "",
            "reason": "上一轮成效追问仍在冷却期，避免连续追问造成回复疲劳。",
        }
    elif followup_plan.get("should_ask") and _is_followup_context_unsuitable(message.content, created_events):
        followup_plan = {
            "should_ask": False,
            "field_key": "",
            "question": "",
            "reason": "本轮不是成效采集语境，避免在确认、闲聊或流程问题后硬追问。",
        }
    elif followup_plan.get("should_ask"):
        state.last_followup_field = followup_plan["field_key"]
        state.last_followup_question = followup_plan["question"]
        state.last_followup_at = datetime.now(timezone.utc)
        state.followup_count = int(state.followup_count or 0) + 1

    await apply_outcome_events_to_user_state(
        session,
        service_case=service_case,
        message=message,
        milestone=spec.milestone,
        created_events=created_events,
    )
    action_focus = await score_action_focus(
        session,
        service_case=service_case,
        message=message,
        created_events=created_events,
        intervention_analysis=intervention_analysis,
    )

    await session.flush()
    return {
        "milestone": spec.milestone,
        "milestone_name": spec.name,
        "service_day": service_day(service_case),
        "created_events": created_events,
        "collected_fields": collected,
        "missing_fields": missing,
        "followup_plan": followup_plan,
        "action_focus": action_focus,
        "notes": str(raw.get("notes") or ""),
    }


async def generate_outcome_snapshot(
    session: AsyncSession,
    service_case: ServiceCase,
    *,
    milestone: str | None = None,
) -> OutcomeSnapshot:
    spec = MILESTONE_BY_KEY.get(milestone or current_milestone_spec(service_case).milestone)
    if not spec:
        raise ValueError(f"unknown outcome milestone: {milestone}")

    events = list(
        await session.scalars(
            select(OutcomeEvent)
            .where(
                OutcomeEvent.service_case_id == service_case.id,
                OutcomeEvent.milestone == spec.milestone,
            )
            .order_by(OutcomeEvent.created_at.asc())
        )
    )
    checkins = list(
        await session.scalars(
            select(Checkin)
            .where(Checkin.service_case_id == service_case.id)
            .order_by(Checkin.checkin_date.asc(), Checkin.created_at.asc())
        )
    )
    state = await get_or_create_stage_state(session, service_case.id, spec)
    metrics = _snapshot_metrics(service_case, spec, checkins, state)
    evidence = {
        "outcome_events": [
            {
                "field_key": item.field_key,
                "value": item.value_text,
                "evidence_text": item.evidence_text,
                "confidence": item.confidence,
                "message_id": item.message_id,
            }
            for item in events
        ],
        "collected_fields": state.collected_fields or {},
        "missing_fields": state.missing_fields or [],
    }
    summary = _snapshot_summary(spec, metrics, evidence)

    snapshot = await session.scalar(
        select(OutcomeSnapshot).where(
            OutcomeSnapshot.service_case_id == service_case.id,
            OutcomeSnapshot.milestone == spec.milestone,
        )
    )
    if not snapshot:
        snapshot = OutcomeSnapshot(
            service_case_id=service_case.id,
            milestone=spec.milestone,
        )
        session.add(snapshot)
    snapshot.status = "ready"
    snapshot.metrics_json = metrics
    snapshot.evidence_json = evidence
    snapshot.ai_summary = summary
    snapshot.action_items = _snapshot_action_items(spec, evidence)
    snapshot.generated_at = datetime.now(timezone.utc)
    await session.flush()
    return snapshot


def current_milestone_spec(service_case: ServiceCase) -> OutcomeMilestoneSpec:
    day = service_day(service_case)
    for spec in MILESTONE_SPECS:
        if day <= spec.day:
            return spec
    return MILESTONE_SPECS[-1]


def service_day(service_case: ServiceCase) -> int:
    anchor = service_case.started_at or service_case.created_at
    if not anchor:
        return 1
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - anchor.astimezone(timezone.utc)
    return max(delta.days + 1, 1)


def outcome_followup_prompt(outcome_result: dict[str, Any] | None) -> str:
    if not outcome_result:
        return "无"
    action_focus = outcome_result.get("action_focus")
    action_text = ""
    if isinstance(action_focus, dict) and action_focus.get("changed"):
        current = action_focus.get("current_focus")
        if isinstance(current, dict):
            evidence = action_focus.get("change_evidence")
            if isinstance(evidence, list):
                evidence_text = "；".join(str(item) for item in evidence[:2] if str(item).strip())
            else:
                evidence_text = ""
            action_text = (
                f"本轮当前行动重点已调整为：{current.get('label')}。\n"
                f"建议动作：{current.get('action')}\n"
                f"触发依据：{evidence_text or current.get('reason') or '用户刚刚提供了新的执行反馈'}\n"
                "请像真人班主任一样自然说明：先承接用户当前内容，再用一句话说明接下来先抓这个重点；不要说系统分数或阈值。"
            )
    plan = outcome_result.get("followup_plan")
    followup_text = ""
    if isinstance(plan, dict) and plan.get("should_ask"):
        followup_text = (
            f"本轮可以自然顺带追问 1 个成效问题：{plan.get('question')}\n"
            f"追问目标字段：{plan.get('field_key')}\n"
            f"原因：{plan.get('reason') or '当前阶段成效证据缺失'}\n"
            "请先回应用户当前内容，再自然补这一句；如果用户只是确认、感谢、闲聊或语境突然不合适，可以不问。"
        )
    return "\n\n".join(item for item in (action_text, followup_text) if item) or "无"


async def score_action_focus(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    message: Message,
    created_events: list[dict[str, Any]],
    intervention_analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score finite action-focus candidates from validated facts.

    The LLM extracts open-ended facts. This deterministic layer only maps
    high-confidence structured facts to a small set of service actions, so
    unrecognized observations remain evidence without shaking the plan.
    """

    state_profile = await get_or_create_user_state_profile(session, service_case.id)
    state = state_profile.state_json
    domain = state["domains"]["action_focus"]
    current_value = domain.get("current", {}).get("value")
    focus_state = current_value if isinstance(current_value, dict) else {}
    candidates = _decayed_candidates(focus_state.get("candidates"), now=datetime.now(timezone.utc))
    current_focus = focus_state.get("current_focus") if isinstance(focus_state.get("current_focus"), dict) else {}
    increments: list[dict[str, Any]] = []

    forced_key = _forced_action_key(intervention_analysis)
    if forced_key:
        increments.append(_action_increment(forced_key, 1.0, "风控或人工介入信号", message.content, forced=True))

    for event in created_events:
        increments.extend(_increments_from_event(event))

    for increment in increments:
        key = increment["key"]
        candidate = candidates.get(key) if isinstance(candidates.get(key), dict) else {}
        definition = ACTION_FOCUS_DEFINITIONS[key]
        score = min(1.0, float(candidate.get("score") or 0.0) + float(increment["delta"]))
        evidence = list(candidate.get("evidence") or [])
        evidence.append(
                {
                    "text": increment["evidence"],
                    "reason": increment["reason"],
                    "message_id": message.id,
                    "occurred_at": _event_time_iso(message.created_at),
                }
            )
        candidates[key] = {
            "key": key,
            "label": definition["label"],
            "action": definition["action"],
            "score": round(score, 3),
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "evidence": evidence[-5:],
        }

    top_candidate = _top_candidate(candidates)
    changed = False
    change_reason = ""
    if top_candidate and _should_switch_action_focus(current_focus, top_candidate, forced=bool(forced_key)):
        changed = True
        change_reason = "高优先级信号触发行动重点切换" if forced_key else "候选行动累计分达到阈值"
        now = datetime.now(timezone.utc)
        current_focus = {
            "key": top_candidate["key"],
            "label": top_candidate["label"],
            "action": top_candidate["action"],
            "score": top_candidate["score"],
            "started_at": now.isoformat(),
            "soft_lock_until": (now + ACTION_FOCUS_SOFT_LOCK).isoformat(),
            "reason": change_reason,
            "evidence": list(top_candidate.get("evidence") or [])[-3:],
        }

    focus_state = {
        "current_focus": current_focus,
        "candidates": candidates,
        "last_scored_at": datetime.now(timezone.utc).isoformat(),
        "last_change": {
            "changed": changed,
            "reason": change_reason,
            "message_id": message.id,
        },
    }
    domain["current"] = {
        "value": focus_state,
        "source": "outcome_rule_score",
        "evidence": _action_focus_evidence_text(current_focus, changed),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "confidence": float(current_focus.get("score") or 0.0) if current_focus else 0.0,
    }
    if changed:
        history = list(domain.get("history") or [])
        history.append(
            {
                "value": current_focus,
                "source": "outcome_rule_score",
                "evidence": _action_focus_evidence_text(current_focus, changed),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "confidence": float(current_focus.get("score") or 0.0),
                "message_id": message.id,
            }
        )
        domain["history"] = history[-30:]
    state_profile.state_json = state
    state_profile.source = "dynamic"
    flag_modified(state_profile, "state_json")
    await session.flush()

    return {
        "changed": changed,
        "current_focus": current_focus,
        "top_candidate": top_candidate,
        "increments": increments,
        "change_evidence": [
            item.get("text")
            for item in list(current_focus.get("evidence") or [])[-2:]
            if isinstance(item, dict) and item.get("text")
        ],
    }


def _analysis_context(
    *,
    service_case: ServiceCase,
    spec: OutcomeMilestoneSpec,
    state: OutcomeStageState,
    intervention_analysis: dict[str, Any] | None,
    recent_messages: list[str],
    followup_time_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collected = state.collected_fields or {}
    missing = state.missing_fields or [goal.key for goal in spec.goals if goal.key not in collected]
    return {
        "service_case_id": service_case.id,
        "case_no": service_case.case_no,
        "service_day": service_day(service_case),
        "milestone": spec.milestone,
        "milestone_name": spec.name,
        "collect_goals": [
            {
                "key": goal.key,
                "label": goal.label,
                "evidence_examples": list(goal.evidence_examples),
            }
            for goal in spec.goals
        ],
        "already_collected": collected,
        "missing_fields": missing,
        "last_followup": {
            "field_key": state.last_followup_field,
            "question": state.last_followup_question,
            "asked_at": state.last_followup_at.isoformat() if state.last_followup_at else None,
            "count": state.followup_count or 0,
        },
        "safety_context": {
            "requires_human_intervention": bool(
                service_case.needs_human_intervention
                or (intervention_analysis or {}).get("requires_human_intervention")
            ),
            "persistent_risk_level": service_case.risk_level,
            "risk_flags": (intervention_analysis or {}).get("risk_flags") or [],
            "refund_intent": (intervention_analysis or {}).get("refund_intent") or {},
            "emotion_signal": (intervention_analysis or {}).get("emotion_signal") or {},
        },
        "recent_messages": recent_messages[-6:],
        "followup_time_context": followup_time_context or {},
    }


async def _build_followup_time_context(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    spec: OutcomeMilestoneSpec,
    state: OutcomeStageState,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize what is sensible to ask right now.

    The LLM should not infer meal timing from scratch. This deterministic
    context tells it which milestone fields are both still missing and
    reasonable to ask at the current local time, after today's check-ins.
    """

    local_now = _local_now(now)
    today = local_now.date()
    checkins = list(
        await session.scalars(
            select(Checkin)
            .where(
                Checkin.service_case_id == service_case.id,
                Checkin.checkin_date == today,
            )
            .order_by(Checkin.created_at.asc())
        )
    )
    answered_today = _answered_today_from_checkins(checkins)
    collected = state.collected_fields or {}
    missing = state.missing_fields or [goal.key for goal in spec.goals if goal.key not in collected]
    allowed_by_time = set(_time_allowed_field_keys(local_now.hour))
    answered_field_keys = set(_answered_field_keys(answered_today))
    allowed = [
        field_key
        for field_key in missing
        if field_key in allowed_by_time and field_key not in answered_field_keys
    ]
    return {
        "timezone": "Asia/Shanghai",
        "local_time": local_now.isoformat(),
        "local_hour": local_now.hour,
        "answered_today": answered_today,
        "allowed_field_keys": allowed,
        "blocked_field_keys": [
            field_key
            for field_key in missing
            if field_key not in allowed
        ],
        "rule": "只能围绕 allowed_field_keys 追问；如果为空，本轮不要追问。",
    }


def _local_now(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(CHINA_TZ)


def _answered_today_from_checkins(checkins: list[Checkin]) -> dict[str, Any]:
    answered = {
        "morning_c": False,
        "noon_b": False,
        "night_a": False,
        "product_schedule": False,
        "sleep": False,
        "weight": False,
        "waist": False,
        "water": False,
        "diet": False,
        "bowel": False,
        "exercise": False,
        "body_feedback": False,
        "discomfort": False,
    }
    for checkin in checkins:
        parsed = checkin.parsed_json if isinstance(checkin.parsed_json, dict) else {}
        schedule = parsed.get("product_schedule")
        if isinstance(schedule, dict):
            for key in ("morning_c", "noon_b", "night_a"):
                if _has_value(schedule.get(key)):
                    answered[key] = True
            answered["product_schedule"] = any(answered[key] for key in ("morning_c", "noon_b", "night_a"))
        for source_key, target_key in (
            ("sleep", "sleep"),
            ("weight", "weight"),
            ("waist", "waist"),
            ("water", "water"),
            ("diet", "diet"),
            ("bowel", "bowel"),
            ("exercise", "exercise"),
            ("sensation", "body_feedback"),
            ("mood", "body_feedback"),
            ("discomfort", "discomfort"),
        ):
            if _has_value(parsed.get(source_key)):
                answered[target_key] = True
    return answered


def _answered_field_keys(answered_today: dict[str, Any]) -> tuple[str, ...]:
    fields: list[str] = []
    if answered_today.get("product_schedule"):
        fields.extend(["product_schedule", "rhythm_understood", "started_execution", "execution_rate_signal"])
    if answered_today.get("sleep"):
        fields.append("sleep_change")
    if answered_today.get("weight"):
        fields.extend(["weight_change", "weight_result", "challenge_goal_result"])
    if answered_today.get("waist"):
        fields.extend(["waist_or_body_change", "waist_result"])
    if answered_today.get("bowel"):
        fields.append("bowel_change")
    if answered_today.get("body_feedback"):
        fields.extend(["energy_change", "positive_body_signal"])
    if answered_today.get("discomfort"):
        fields.append("discomfort_feedback")
    return tuple(dict.fromkeys(fields))


def _time_allowed_field_keys(hour: int) -> tuple[str, ...]:
    if 6 <= hour < 10:
        return (
            "sleep_change",
            "weight_change",
            "bowel_change",
            "energy_change",
            "profile_completed",
            "documents_received",
            "plan_understood",
            "started_execution",
            "product_schedule",
        )
    if 10 <= hour < 14:
        return (
            "plan_understood",
            "started_execution",
            "product_schedule",
            "rhythm_understood",
            "documents_received",
            "profile_completed",
            "bowel_change",
            "energy_change",
        )
    if 14 <= hour < 18:
        return (
            "product_schedule",
            "rhythm_understood",
            "energy_change",
            "positive_body_signal",
            "execution_rate_signal",
            "adjustment_need",
            "bowel_change",
        )
    if 18 <= hour < 22:
        return (
            "product_schedule",
            "adjustment_need",
            "execution_rate_signal",
            "positive_body_signal",
            "habit_stability",
            "next_stage_intent",
            "energy_change",
        )
    return (
        "energy_change",
        "sleep_change",
        "bowel_change",
        "habit_stability",
        "next_stage_intent",
        "self_management",
        "lifestyle_habit",
        "long_term_weight_stability",
    )


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        return bool(text) and text not in {"未记录", "暂无", "无", "None"}
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


async def _extract_outcome_safely(content: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        return await AIService().extract_outcome_insight(content=content, context=context)
    except Exception:
        logger.exception("Failed to extract outcome insight")
        return {
            "outcome_events": [],
            "followup_plan": {"should_ask": False, "field_key": "", "question": "", "reason": "成效提取失败"},
            "notes": "成效提取失败，已跳过本轮状态更新。",
        }


def _normalize_events(value: Any, valid_fields: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    events: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        field_key = _string(item.get("field_key"))
        evidence_text = _string(item.get("evidence_text"))
        fact_value = _string(item.get("value"))
        confidence = _confidence(item.get("confidence"))
        if field_key not in valid_fields or not evidence_text or not fact_value or confidence < 0.55:
            continue
        raw_event_type = _string(item.get("event_type"))
        event_type = raw_event_type if raw_event_type in VALID_EVENT_TYPES else "observation"
        events.append(
            {
                "field_key": field_key,
                "event_type": event_type,
                "value": fact_value,
                "evidence_text": evidence_text,
                "confidence": confidence,
            }
        )
    return events


def _normalize_followup(
    value: Any,
    missing_fields: list[str],
    allowed_fields: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or not value.get("should_ask"):
        return {"should_ask": False, "field_key": "", "question": "", "reason": ""}
    field_key = _string(value.get("field_key"))
    question = _string(value.get("question"))
    if field_key not in missing_fields or not question:
        return {"should_ask": False, "field_key": "", "question": "", "reason": ""}
    if allowed_fields is not None and field_key not in allowed_fields:
        return {
            "should_ask": False,
            "field_key": "",
            "question": "",
            "reason": "本轮追问字段不在当前时段或当日未答范围内，已由后端拦截。",
        }
    return {
        "should_ask": True,
        "field_key": field_key,
        "question": question,
        "reason": _string(value.get("reason")),
    }


def _allowed_followup_fields_from_context(context: Any) -> set[str] | None:
    if not isinstance(context, dict):
        return None
    followup_context = context.get("followup_time_context")
    if not isinstance(followup_context, dict):
        return None
    allowed = followup_context.get("allowed_field_keys")
    if not isinstance(allowed, list):
        return None
    return {_string(item) for item in allowed if _string(item)}


async def _event_exists(
    session: AsyncSession,
    service_case_id: int,
    message_id: int | None,
    item: dict[str, Any],
) -> bool:
    statement = select(OutcomeEvent.id).where(
        OutcomeEvent.service_case_id == service_case_id,
        OutcomeEvent.field_key == item["field_key"],
    )
    if message_id is not None:
        statement = statement.where(OutcomeEvent.message_id == message_id)
    else:
        statement = statement.where(OutcomeEvent.evidence_text == item["evidence_text"])
    existing = await session.scalar(statement.limit(1))
    return existing is not None


def _must_suppress_followup(intervention_analysis: dict[str, Any] | None) -> bool:
    if not intervention_analysis:
        return False
    if intervention_analysis.get("requires_human_intervention"):
        return True
    refund = intervention_analysis.get("refund_intent")
    if isinstance(refund, dict) and refund.get("detected"):
        return True
    emotion = intervention_analysis.get("emotion_signal")
    if isinstance(emotion, dict) and emotion.get("detected") and emotion.get("level") in {"medium", "high"}:
        return True
    return False


def _is_followup_context_unsuitable(content: str | None, created_events: list[dict[str, Any]]) -> bool:
    """Avoid metric follow-ups after acknowledgements, tests, and service-flow questions.

    The outcome extractor is intentionally broad so it does not miss useful
    evidence. This guard only controls whether to ask an extra follow-up in the
    chat reply. It does not block evidence already extracted from the message.
    """
    text = (content or "").strip()
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) <= 6 and not created_events:
        return True
    service_flow_markers = (
        "测试",
        "你干啥",
        "在吗",
        "收到",
        "好的",
        "谢谢",
        "文件",
        "问卷",
        "链接",
        "发了吗",
        "提醒",
        "下一步",
        "怎么做",
    )
    if not created_events and any(marker in compact for marker in service_flow_markers):
        return True
    return False


def _is_followup_in_cooldown(state: OutcomeStageState) -> bool:
    if not state.last_followup_at:
        return False
    last = state.last_followup_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last.astimezone(timezone.utc) < FOLLOWUP_COOLDOWN


def _forced_action_key(intervention_analysis: dict[str, Any] | None) -> str:
    if not intervention_analysis:
        return ""
    if intervention_analysis.get("requires_human_intervention"):
        return "safety_review"
    refund = intervention_analysis.get("refund_intent")
    if isinstance(refund, dict) and refund.get("detected"):
        return "safety_review"
    return ""


def _increments_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    field_key = str(event.get("field_key") or "")
    event_type = str(event.get("event_type") or "").lower()
    confidence = _confidence(event.get("confidence"))
    evidence = str(event.get("evidence_text") or event.get("value") or "").strip()
    if not field_key or confidence < 0.65 or event_type in {"none", "resolved"}:
        return []
    if event_type in {"improved", "positive"}:
        if field_key in {"energy_change", "positive_body_signal", "habit_stability"}:
            return [_action_increment("motivation_support", 0.1 * confidence, "正向反馈可用于鼓励陪跑", evidence)]
        return []
    rules = FIELD_ACTION_RULES.get(field_key, ())
    return [
        _action_increment(action_key, delta * confidence, f"{field_key} 触发 {ACTION_FOCUS_DEFINITIONS[action_key]['label']}", evidence)
        for action_key, delta in rules
        if action_key in ACTION_FOCUS_DEFINITIONS
    ]


def _action_increment(
    key: str,
    delta: float,
    reason: str,
    evidence: str,
    *,
    forced: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "delta": round(max(delta, 0.0), 3),
        "reason": reason,
        "evidence": evidence[:160],
        "forced": forced,
    }


def _decayed_candidates(value: Any, *, now: datetime) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, candidate in value.items():
        if key not in ACTION_FOCUS_DEFINITIONS or not isinstance(candidate, dict):
            continue
        score = float(candidate.get("score") or 0.0)
        last_seen = _parse_datetime(candidate.get("last_seen_at"))
        if last_seen:
            days = max((now - last_seen).total_seconds() / 86400, 0.0)
            score = max(0.0, score - days * ACTION_FOCUS_DECAY_PER_DAY)
        if score <= 0.05:
            continue
        definition = ACTION_FOCUS_DEFINITIONS[key]
        result[key] = {
            **candidate,
            "key": key,
            "label": definition["label"],
            "action": definition["action"],
            "score": round(score, 3),
        }
    return result


def _top_candidate(candidates: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(candidates.values(), key=lambda item: float(item.get("score") or 0.0))


def _should_switch_action_focus(
    current_focus: dict[str, Any],
    candidate: dict[str, Any],
    *,
    forced: bool,
) -> bool:
    candidate_score = float(candidate.get("score") or 0.0)
    if forced:
        return True
    if candidate_score < ACTION_FOCUS_THRESHOLD:
        return False
    if not current_focus:
        return True
    if candidate.get("key") == current_focus.get("key"):
        return False
    current_score = float(current_focus.get("score") or 0.0)
    if _is_action_focus_locked(current_focus):
        return candidate_score >= max(0.98, current_score + ACTION_FOCUS_LEAD_MARGIN)
    return candidate_score >= current_score + ACTION_FOCUS_LEAD_MARGIN


def _is_action_focus_locked(current_focus: dict[str, Any]) -> bool:
    locked_until = _parse_datetime(current_focus.get("soft_lock_until"))
    return bool(locked_until and datetime.now(timezone.utc) < locked_until)


def _action_focus_evidence_text(current_focus: dict[str, Any], changed: bool) -> str:
    if not current_focus:
        return "暂无当前行动重点"
    prefix = "行动重点已调整" if changed else "行动重点评分已更新"
    evidence = current_focus.get("evidence")
    if isinstance(evidence, list) and evidence:
        latest = evidence[-1]
        if isinstance(latest, dict) and latest.get("text"):
            return f"{prefix}：{latest['text']}"
    return f"{prefix}：{current_focus.get('label') or '待观察'}"


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _event_time_iso(value: datetime | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _snapshot_metrics(
    service_case: ServiceCase,
    spec: OutcomeMilestoneSpec,
    checkins: list[Checkin],
    state: OutcomeStageState,
) -> dict[str, Any]:
    relevant_checkins = [item for item in checkins if _checkin_day(service_case, item) <= spec.day]
    weights = [_number_from_value((item.parsed_json or {}).get("weight")) for item in relevant_checkins]
    weights = [item for item in weights if item is not None]
    waists = [_number_from_value((item.parsed_json or {}).get("waist")) for item in relevant_checkins]
    waists = [item for item in waists if item is not None]
    return {
        "milestone": spec.milestone,
        "service_day": service_day(service_case),
        "checkin_days": len({item.checkin_date.isoformat() for item in relevant_checkins}),
        "collected_count": len(state.collected_fields or {}),
        "missing_count": len(state.missing_fields or []),
        "weight_change": round(weights[-1] - weights[0], 2) if len(weights) >= 2 else None,
        "waist_change": round(waists[-1] - waists[0], 2) if len(waists) >= 2 else None,
    }


def _snapshot_summary(spec: OutcomeMilestoneSpec, metrics: dict[str, Any], evidence: dict[str, Any]) -> str:
    collected_count = metrics.get("collected_count", 0)
    missing_count = metrics.get("missing_count", 0)
    return (
        f"{spec.name}已收集 {collected_count} 项成效证据，仍缺 {missing_count} 项。"
        f"当前有 {metrics.get('checkin_days', 0)} 天打卡可参考。"
    )


def _snapshot_action_items(spec: OutcomeMilestoneSpec, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    missing = evidence.get("missing_fields") or []
    if not missing:
        return [{"type": "continue", "text": "成效证据已较完整，继续保持当前服务节奏。"}]
    labels = {goal.key: goal.label for goal in spec.goals}
    return [
        {
            "type": "collect_missing",
            "field_key": field,
            "text": f"后续聊天中自然补齐：{labels.get(field, field)}",
        }
        for field in missing[:3]
    ]


def _checkin_day(service_case: ServiceCase, checkin: Checkin) -> int:
    anchor = service_case.started_at or service_case.created_at
    if not anchor:
        return 1
    anchor_date = anchor.astimezone(timezone.utc).date() if anchor.tzinfo else anchor.date()
    return max((checkin.checkin_date - anchor_date).days + 1, 1)


def _number_from_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _confidence(value: Any) -> float:
    try:
        return max(min(float(value), 1.0), 0.0)
    except (TypeError, ValueError):
        return 0.0
