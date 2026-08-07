from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.message import Message
from app.models.outcome import OutcomeEvent, OutcomeSnapshot, OutcomeStageState
from app.models.service_case import ServiceCase
from app.core.config import get_settings
from app.services.body_change_stats import load_intake_baselines
from app.services.body_metrics import parse_waist_cm, parse_weight_kg
from app.services.service_case_queries import effective_service_day, effective_start_at
from app.services.user_state_profile import (
    OUTCOME_FIELD_DOMAIN_MAP,
    apply_outcome_events_to_user_state,
    apply_profile_signals_to_user_state,
    apply_state_signals_to_user_state,
)

logger = logging.getLogger(__name__)

# 账本记全方向，下游只吃正向：两件事必须分开。2026-07-14 的 00c30c8 为了不让负面结论
# 污染 AI 用户小抄和优秀案例库，把成效表一起砍成只收正向，副作用是负向只剩"当前服务状态"
# 里的自由文本（无维度标签、每类只留最新一条），看板再也数不出"多少人反馈睡眠变差"。
# 现在恢复成：成效表收全方向，投影用户状态背景/优秀案例判断处各自过滤 POSITIVE_EVENT_TYPES。
POSITIVE_EVENT_TYPES = frozenset({"improved", "completed"})
NEGATIVE_EVENT_TYPES = frozenset({"worsened", "unchanged", "not_completed"})
VALID_EVENT_TYPES = POSITIVE_EVENT_TYPES | NEGATIVE_EVENT_TYPES

# 已知成效维度：不再按当前里程碑限制，修复"跨里程碑正向被静默丢弃"的问题
# （如第3天用户提前反馈睡眠变好=day7 的 sleep_change 曾被丢）。与 OUTCOME_FIELD_DOMAIN_MAP
# 同源（该 map 已排除偏负向的 discomfort_feedback/plateau_signal/adjustment_need），避免漂移。
KNOWN_OUTCOME_FIELDS = frozenset(OUTCOME_FIELD_DOMAIN_MAP)

# 主观体感维度（睡眠/精神/排便等）：无数值序列，只能靠用户口述，单次提及有偶然性。
# 用"同一维度不同日≥2 次正向"作为"已确立改善"的结构化线索，交给同轮档案官终判。
SUBJECTIVE_FIELDS = frozenset(
    key for key, domain in OUTCOME_FIELD_DOMAIN_MAP.items() if domain == "body_feedback"
)


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
            OutcomeGoal("execution_rate_signal", "执行率是否够", ("这周漏了几次", "基本都完成")),
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


async def apply_archived_outcome(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    anchor_message: Message,
    outcome_events: list[dict[str, Any]],
    profile_signals: list[dict[str, Any]],
    state_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """档案官(Archivist)成效/画像落库入口（复用 apply_outcome_analysis 的确定性写入/投影）。

    档案官按天批量抽取、不精确对应单条消息，故用该批**最后一条用户消息**作 anchor：
    事件 message_id/occurred_at、画像投影 message_id 均归到它，既有真实 message_id 供去重，
    又符合既有"按当前里程碑归属事件"的语义。source="archivist" 标溯源。
    """
    spec = current_milestone_spec(service_case)
    state = await get_or_create_stage_state(session, service_case.id, spec)
    raw = {
        "outcome_events": outcome_events or [],
        "profile_signals": profile_signals or [],
        "state_signals": state_signals or [],
    }
    return await apply_outcome_analysis(
        session,
        service_case=service_case,
        message=anchor_message,
        spec=spec,
        state=state,
        raw=raw,
        source="archivist",
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
    source: str = "chat",
) -> dict[str, Any]:
    collected = dict(state.collected_fields or {})
    created_events: list[dict[str, Any]] = []
    profile_signals = _normalize_profile_signals(raw.get("profile_signals"))
    state_signals = _normalize_state_signals(raw.get("state_signals"))

    for item in _normalize_events(raw.get("outcome_events"), KNOWN_OUTCOME_FIELDS):
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
            source=source,
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

    await apply_outcome_events_to_user_state(
        session,
        service_case=service_case,
        message=message,
        milestone=spec.milestone,
        created_events=created_events,
        source=source,
    )
    await apply_profile_signals_to_user_state(
        session,
        service_case=service_case,
        message=message,
        profile_signals=profile_signals,
        source=source,
    )
    await apply_state_signals_to_user_state(
        session,
        service_case=service_case,
        message=message,
        state_signals=state_signals,
        source=source,
    )

    await session.flush()
    return {
        "milestone": spec.milestone,
        "milestone_name": spec.name,
        "service_day": service_day(service_case),
        "created_events": created_events,
        "profile_signals": profile_signals,
        "state_signals": state_signals,
        "collected_fields": collected,
        "missing_fields": missing,
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
    # 主观印证看全量（跨里程碑）事件：同一体感维度需在不同日多次出现才算"已确立"。
    all_events = list(
        await session.scalars(
            select(OutcomeEvent).where(OutcomeEvent.service_case_id == service_case.id)
        )
    )
    state = await get_or_create_stage_state(session, service_case.id, spec)
    # 打卡里的体重/腰围是自由文本，判断裸数字是公斤还是斤要拿本人入营问卷值当锚。
    baselines = await load_intake_baselines(session, [service_case.id])
    baseline_weight, baseline_waist = baselines.get(service_case.id, (None, None))
    metrics = _snapshot_metrics(
        service_case,
        spec,
        checkins,
        state,
        baseline_weight=baseline_weight,
        baseline_waist=baseline_waist,
    )
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
        "subjective_signals": _subjective_signals(all_events),
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
    start = effective_start_at(service_case)
    if start is not None:
        tz = ZoneInfo(get_settings().reminder_timezone)
        return max(effective_service_day(service_case, datetime.now(tz), tz), 1)
    anchor = service_case.created_at
    if not anchor:
        return 1
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - anchor.astimezone(timezone.utc)
    return max(delta.days + 1, 1)


def _normalize_events(value: Any, known_fields: frozenset[str]) -> list[dict[str, Any]]:
    """校验并过滤成效事件。兜底闸门：只保留已知维度 + 已知方向（正向或负向）；
    维度不认识、方向说不清、缺原话证据或置信度不够的一律丢弃。方向本身不构成拦截理由——
    "睡得更差了"和"睡得更好了"一样要进账本，下游按 POSITIVE_EVENT_TYPES 各自过滤。"""
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
        event_type = _string(item.get("event_type"))
        if (
            field_key not in known_fields
            or event_type not in VALID_EVENT_TYPES
            or not evidence_text
            or not fact_value
            or confidence < 0.55
        ):
            continue
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


def _normalize_profile_signals(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed_domains = {"personal_preferences", "lifestyle_habits"}
    signals: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        domain = _string(item.get("domain"))
        signal_type = _string(item.get("signal_type")) or "other"
        fact_value = _string(item.get("value"))
        evidence_text = _string(item.get("evidence_text"))
        confidence = _confidence(item.get("confidence"))
        if domain not in allowed_domains or not fact_value or not evidence_text or confidence < 0.65:
            continue
        signal = {
            "domain": domain,
            "signal_type": signal_type,
            "value": fact_value,
            "evidence_text": evidence_text,
            "confidence": confidence,
        }
        if signal_type == "supplement_habit":
            supplement_name = _string(item.get("supplement_name"))
            habit_status = _string(item.get("habit_status"))
            if not supplement_name or habit_status not in {"using", "considering", "stopped"}:
                continue
            signal["supplement_name"] = supplement_name
            signal["habit_status"] = habit_status
            supplement_category = _string(item.get("supplement_category"))
            signal["supplement_category"] = (
                supplement_category
                if supplement_category
                in {"micronutrient", "macronutrient", "health_supplement", "herbal", "other"}
                else "other"
            )
        signals.append(signal)
    return signals[:5]


def _normalize_state_signals(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed_types = {
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
    signals: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        state_type = _string(item.get("state_type"))
        fact_value = _string(item.get("value"))
        evidence_text = _string(item.get("evidence_text"))
        confidence = _confidence(item.get("confidence"))
        status = _string(item.get("status")) or "active"
        if state_type not in allowed_types or not fact_value or not evidence_text or confidence < 0.6:
            continue
        if status not in {"active", "resolved"}:
            status = "active"
        signals.append(
            {
                "state_type": state_type,
                "value": fact_value,
                "evidence_text": evidence_text,
                "confidence": confidence,
                "status": status,
            }
        )
    return signals[:8]


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


def _snapshot_metrics(
    service_case: ServiceCase,
    spec: OutcomeMilestoneSpec,
    checkins: list[Checkin],
    state: OutcomeStageState,
    *,
    baseline_weight: float | None = None,
    baseline_waist: float | None = None,
) -> dict[str, Any]:
    """阶段快照的客观指标。

    体重/腰围一律走 ``body_metrics`` 解析：只取第一个数字会把「140.2斤 → 70.7kg」
    算成掉了 70 公斤，把腰围填的「0」当成真实围度。解析不出可信值的记录直接不参与，
    快照宁可少一个数，也不能给出离谱的变化。
    """

    relevant_checkins = [item for item in checkins if _checkin_day(service_case, item) <= spec.day]
    weights = [
        parse_weight_kg((item.parsed_json or {}).get("weight"), baseline_kg=baseline_weight).value
        for item in relevant_checkins
    ]
    weights = [item for item in weights if item is not None]
    waists = [
        parse_waist_cm((item.parsed_json or {}).get("waist"), baseline_cm=baseline_waist).value
        for item in relevant_checkins
    ]
    waists = [item for item in waists if item is not None]
    return {
        "milestone": spec.milestone,
        "service_day": service_day(service_case),
        "checkin_days": len({item.checkin_date.isoformat() for item in relevant_checkins}),
        "collected_count": len(state.collected_fields or {}),
        "missing_count": len(state.missing_fields or []),
        "weight_change": round(weights[-1] - weights[0], 2) if len(weights) >= 2 else None,
        "waist_change": round(waists[-1] - waists[0], 2) if len(waists) >= 2 else None,
        "weight_trend": _series_trend(weights),
        "waist_trend": _series_trend(waists),
    }


def _series_trend(values: list[float]) -> dict[str, Any] | None:
    """客观指标趋势去噪：先 3 日滑动吸收单天反弹，再看净变化方向与一致性。

    不设"显著"阈值——net/consistent 交给档案官模型解读。单天体重波动
    （如某天掉一斤第二天弹回）被平滑吸收，不会被误判为向下趋势。
    """
    if len(values) < 2:
        return None
    smoothed = _moving_average(values, window=3)
    net = round(smoothed[-1] - smoothed[0], 2)
    eps = 0.05
    if net <= -eps:
        direction = "down"
    elif net >= eps:
        direction = "up"
    else:
        direction = "flat"
    diffs = [round(b - a, 4) for a, b in zip(smoothed, smoothed[1:])]
    if direction == "flat":
        consistent = all(abs(d) < eps for d in diffs)
    else:
        # 平滑后仍与净方向相反、且幅度超过 eps 的步数即"反弹"；容忍 ≤1/4 的反弹，
        # 单天波动被吸收判为一致，纯噪声（反弹占比高）判为不一致。
        opposing = sum(
            1 for d in diffs
            if (direction == "down" and d > eps) or (direction == "up" and d < -eps)
        )
        consistent = opposing <= max(1, len(diffs) // 4)
    return {
        "direction": direction,
        "net": net,
        "consistent": bool(consistent),
        "points": len(values),
    }


def _moving_average(values: list[float], *, window: int) -> list[float]:
    out: list[float] = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1): i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def _subjective_signals(all_events: list[OutcomeEvent]) -> dict[str, dict[str, Any]]:
    """按体感维度统计"不同日的正向提及次数"：≥2 天=已确立改善(established)，单次=候选线索(candidate)。

    这是给档案官优秀案例判断的结构化线索，本身不做硬阈值拦截。
    """
    days_by_field: dict[str, set[str]] = {}
    for event in all_events:
        if event.field_key not in SUBJECTIVE_FIELDS or event.event_type not in POSITIVE_EVENT_TYPES:
            continue
        occurred = event.occurred_at or event.created_at
        if occurred is None:
            continue
        days_by_field.setdefault(event.field_key, set()).add(occurred.date().isoformat())
    return {
        field: {
            "distinct_days": len(days),
            "status": "established" if len(days) >= 2 else "candidate",
        }
        for field, days in days_by_field.items()
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
    anchor = effective_start_at(service_case) or service_case.created_at
    if not anchor:
        return 1
    tz = ZoneInfo(get_settings().reminder_timezone)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    anchor_date = anchor.astimezone(tz).date()
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
