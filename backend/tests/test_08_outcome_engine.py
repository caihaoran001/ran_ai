from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.outcome import OutcomeEvent, OutcomeSnapshot
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.outcome_engine import (
    apply_archived_outcome,
    generate_outcome_snapshot,
)
from app.services.user_state_profile import initialize_user_state_from_intake


async def _setup_outcome_case(db_session, *, days: int = 7):
    user = User(name="成效测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-OUTCOME-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"outcome-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=days - 1),
    )
    db_session.add(case)
    await db_session.flush()
    return case


async def _add_message(db_session, case, content: str = "占位消息") -> Message:
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    return message


def _event(field_key: str, event_type: str, *, value: str = "占位事实", evidence: str = "用户原话", confidence: float = 0.9):
    return {
        "field_key": field_key,
        "event_type": event_type,
        "value": value,
        "evidence_text": evidence,
        "confidence": confidence,
    }


def _profile_fact_by_value(value: object, expected: str) -> dict:
    """在事实列表、当前状态或历史记录中递归找指定 value。"""
    if isinstance(value, dict):
        if value.get("value") == expected:
            return value
        for item in value.values():
            found = _profile_fact_by_value(item, expected)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _profile_fact_by_value(item, expected)
            if found:
                return found
    return {}


@pytest.mark.asyncio
async def test_archived_outcome_records_positive_event(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "今天下午没那么困了，整体精神稳一点")

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[_event("energy_change", "improved", value="白天精神有改善", evidence=message.content)],
        profile_signals=[],
    )

    assert result["milestone"] == "day7"
    assert result["created_events"]
    events = list(
        await db_session.scalars(select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id))
    )
    assert len(events) == 1
    assert events[0].field_key == "energy_change"
    assert events[0].event_type == "improved"
    assert events[0].source == "archivist"


@pytest.mark.asyncio
async def test_negative_events_are_not_recorded(db_session):
    """成效只记正向：worsened/unchanged/not_completed/discomfort 一律不入表。"""
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "睡得更差了，午B喝完还有点心慌")

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[
            _event("sleep_change", "worsened", value="睡眠变差"),
            _event("energy_change", "unchanged", value="精神没变化"),
            _event("started_execution", "not_completed", value="还没开始"),
            _event("energy_change", "discomfort", value="心慌"),
        ],
        profile_signals=[],
    )

    assert result["created_events"] == []
    events = list(
        await db_session.scalars(select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id))
    )
    assert events == []


@pytest.mark.asyncio
async def test_cross_milestone_positive_field_is_not_dropped(db_session):
    """回归：第3天用户提前反馈睡眠变好（sleep_change 属 day7）以前会被里程碑过滤静默丢弃，现应落库。"""
    case = await _setup_outcome_case(db_session, days=3)
    message = await _add_message(db_session, case, "这两天睡得比以前好多了")

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[_event("sleep_change", "improved", value="睡眠改善", evidence=message.content)],
        profile_signals=[],
    )

    assert result["milestone"] == "day3"
    field_keys = {item["field_key"] for item in result["created_events"]}
    assert "sleep_change" in field_keys
    events = list(
        await db_session.scalars(select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id))
    )
    assert {e.field_key for e in events} == {"sleep_change"}


@pytest.mark.asyncio
async def test_unknown_field_is_dropped(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "随便说说")

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[_event("mood_change", "improved", value="心情好")],
        profile_signals=[],
    )

    assert result["created_events"] == []


@pytest.mark.asyncio
async def test_positive_event_projects_user_state_but_negative_does_not(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "今天下午没那么困了，整体精神稳一点")

    await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[
            _event("energy_change", "improved", value="白天精神有改善", evidence=message.content),
            _event("sleep_change", "worsened", value="睡眠变差"),
        ],
        profile_signals=[],
    )

    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    assert state_profile is not None
    body_feedback = state_profile.state_json["domains"]["body_feedback"]
    value = body_feedback["current"]["value"]
    # 正向 energy_change 投影进 body_feedback，负向 sleep_change 不投影。
    assert value["energy_change"]["value"] == "白天精神有改善"
    assert "sleep_change" not in value


@pytest.mark.asyncio
async def test_profile_signals_projected_without_outcome_events(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "我周末一般去漂流，挺喜欢户外的，平时基本不吃晚饭")

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[],
        profile_signals=[
            {
                "domain": "personal_preferences",
                "signal_type": "activity_preference",
                "value": "喜欢户外和漂流",
                "evidence_text": message.content,
                "confidence": 0.9,
            },
            {
                "domain": "lifestyle_habits",
                "signal_type": "meal_pattern",
                "value": "长期不吃晚饭",
                "evidence_text": message.content,
                "confidence": 0.9,
            },
        ],
    )

    assert result["created_events"] == []
    assert {item["domain"] for item in result["profile_signals"]} == {
        "personal_preferences",
        "lifestyle_habits",
    }
    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    domains = state_profile.state_json["domains"]
    assert domains["personal_preferences"]["current"]["value"]["activity_preference"][0]["value"] == "喜欢户外和漂流"
    assert domains["lifestyle_habits"]["current"]["value"]["meal_pattern"][0]["value"] == "长期不吃晚饭"


@pytest.mark.asyncio
async def test_profile_signals_keep_multiple_same_type_facts_with_own_evidence(db_session):
    """同一 signal_type 可以有多条并存事实，且每条都必须保留自己的原话与溯源元数据。"""
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "我不吃香菜，也不喜欢芹菜")

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[],
        profile_signals=[
            {
                "domain": "personal_preferences",
                "signal_type": "food_preference",
                "value": "不吃香菜",
                "evidence_text": "我不吃香菜",
                "confidence": 0.96,
            },
            {
                "domain": "personal_preferences",
                "signal_type": "food_preference",
                "value": "不喜欢芹菜",
                "evidence_text": "也不喜欢芹菜",
                "confidence": 0.91,
            },
        ],
    )

    assert len(result["profile_signals"]) == 2
    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    current_value = state_profile.state_json["domains"]["personal_preferences"]["current"]["value"]
    facts = current_value["food_preference"]
    assert isinstance(facts, list)
    coriander = _profile_fact_by_value(facts, "不吃香菜")
    celery = _profile_fact_by_value(facts, "不喜欢芹菜")

    assert coriander and celery
    assert coriander["evidence"] == "我不吃香菜"
    assert coriander["source"] == "archivist_profile_signal"
    assert coriander["confidence"] == pytest.approx(0.96)
    assert coriander["message_id"] == message.id
    assert coriander["occurred_at"] == message.created_at.isoformat()
    assert coriander["status"] == "confirmed"
    assert coriander["first_seen_at"] == coriander["occurred_at"]
    assert coriander["last_confirmed_at"] == coriander["occurred_at"]
    assert celery["evidence"] == "也不喜欢芹菜"
    assert celery["source"] == "archivist_profile_signal"
    assert celery["confidence"] == pytest.approx(0.91)
    assert celery["message_id"] == message.id
    assert celery["occurred_at"] == message.created_at.isoformat()
    assert celery["status"] == "confirmed"
    assert celery["first_seen_at"] == celery["occurred_at"]
    assert celery["last_confirmed_at"] == celery["occurred_at"]


@pytest.mark.asyncio
async def test_supplement_habit_keeps_structured_status_and_updates_current_fact(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    using_message = await _add_message(db_session, case, "我一直在吃鱼油")

    first = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=using_message,
        outcome_events=[],
        profile_signals=[
            {
                "domain": "lifestyle_habits",
                "signal_type": "supplement_habit",
                "value": "长期吃鱼油",
                "supplement_name": "鱼油",
                "habit_status": "using",
                "evidence_text": using_message.content,
                "confidence": 0.94,
            }
        ],
    )
    assert first["profile_signals"][0]["supplement_name"] == "鱼油"

    stopped_message = await _add_message(db_session, case, "鱼油我现在已经停了")
    await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=stopped_message,
        outcome_events=[],
        profile_signals=[
            {
                "domain": "lifestyle_habits",
                "signal_type": "supplement_habit",
                "value": "已停用鱼油",
                "supplement_name": "鱼油",
                "habit_status": "stopped",
                "evidence_text": stopped_message.content,
                "confidence": 0.97,
            }
        ],
    )

    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    supplement_facts = state_profile.state_json["domains"]["lifestyle_habits"]["current"]["value"][
        "supplement_habit"
    ]
    assert len(supplement_facts) == 1
    assert supplement_facts[0]["value"] == "已停用鱼油"
    assert supplement_facts[0]["supplement_name"] == "鱼油"
    assert supplement_facts[0]["habit_status"] == "stopped"
    assert supplement_facts[0]["first_seen_at"] == using_message.created_at.isoformat()
    assert supplement_facts[0]["last_confirmed_at"] == stopped_message.created_at.isoformat()
    history = state_profile.state_json["domains"]["lifestyle_habits"]["history"]
    assert [item["habit_status"] for item in history if item["field_key"] == "supplement_habit"] == [
        "using",
        "stopped",
    ]


@pytest.mark.asyncio
async def test_profile_signal_guard_routes_health_and_causal_misclassification(db_session):
    """术后/生理状态不是生活习惯，因果判断也不是偏好；误投后应转入当前状态。"""
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(
        db_session,
        case,
        "我还在宫腔镜术后恢复中，月经周期还没稳定；我觉得空腹吃黄瓜掉秤快；另外我不喜欢吃香菜。",
    )

    result = await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[],
        profile_signals=[
            {
                "domain": "lifestyle_habits",
                "signal_type": "other",
                "value": "宫腔镜术后恢复中",
                "evidence_text": "我还在宫腔镜术后恢复中",
                "confidence": 0.95,
            },
            {
                "domain": "lifestyle_habits",
                "signal_type": "other",
                "value": "月经周期还没稳定",
                "evidence_text": "月经周期还没稳定",
                "confidence": 0.92,
            },
            {
                "domain": "personal_preferences",
                "signal_type": "food_preference",
                "value": "空腹吃黄瓜掉秤快",
                "evidence_text": "我觉得空腹吃黄瓜掉秤快",
                "confidence": 0.9,
            },
            {
                "domain": "personal_preferences",
                "signal_type": "food_preference",
                "value": "不喜欢吃香菜",
                "evidence_text": "我不喜欢吃香菜",
                "confidence": 0.94,
            },
        ],
    )

    assert result["profile_signals"]
    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    domains = state_profile.state_json["domains"]
    habits = domains["lifestyle_habits"]["current"].get("value", {})
    assert not _profile_fact_by_value(habits, "宫腔镜术后恢复中")
    assert not _profile_fact_by_value(habits, "月经周期还没稳定")
    preferences = domains["personal_preferences"]["current"]["value"]
    assert _profile_fact_by_value(preferences, "不喜欢吃香菜")
    assert not _profile_fact_by_value(preferences, "空腹吃黄瓜掉秤快")

    service_domain = domains["service_state"]
    health_fact = service_domain["current"]["value"]["health_concern"]
    belief_fact = service_domain["current"]["value"]["user_belief"]
    assert health_fact["value"] == "月经周期还没稳定"
    assert health_fact["evidence"] == "月经周期还没稳定"
    assert health_fact["source"] == "archivist_profile_guardrail"
    assert health_fact["message_id"] == message.id
    assert health_fact["status"] == "pending_confirmation"
    assert health_fact["first_seen_at"] == health_fact["occurred_at"]
    assert health_fact["last_confirmed_at"] == health_fact["occurred_at"]
    assert _profile_fact_by_value(service_domain["history"], "宫腔镜术后恢复中")
    assert belief_fact["value"] == "空腹吃黄瓜掉秤快"
    assert belief_fact["evidence"] == "我觉得空腹吃黄瓜掉秤快"
    assert belief_fact["source"] == "archivist_profile_guardrail"
    assert belief_fact["message_id"] == message.id
    assert belief_fact["status"] == "pending_confirmation"
    assert belief_fact["first_seen_at"] == belief_fact["occurred_at"]
    assert belief_fact["last_confirmed_at"] == belief_fact["occurred_at"]


@pytest.mark.asyncio
async def test_state_signal_can_resolve_previous_current_obstacle(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    first = await _add_message(db_session, case, "晚上总忘记执行")
    await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=first,
        outcome_events=[],
        profile_signals=[],
        state_signals=[
            {
                "state_type": "current_obstacle",
                "status": "active",
                "value": "晚上容易忘记执行",
                "evidence_text": "晚上总忘记执行",
                "confidence": 0.9,
            }
        ],
    )
    second = await _add_message(db_session, case, "这个问题已经解决了")
    await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=second,
        outcome_events=[],
        profile_signals=[],
        state_signals=[
            {
                "state_type": "current_obstacle",
                "status": "resolved",
                "value": "晚上忘记执行的问题已解决",
                "evidence_text": "这个问题已经解决了",
                "confidence": 0.88,
            }
        ],
    )

    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    service_state = state_profile.state_json["domains"]["service_state"]
    assert service_state["current"]["value"]["current_obstacle"]["status"] == "resolved"
    assert [item["status"] for item in service_state["history"]] == ["active", "resolved"]


@pytest.mark.asyncio
async def test_snapshot_weight_trend_absorbs_single_day_bounce(db_session):
    """客观趋势去噪：整体下降但中间单天反弹，3 日滑动后仍判为 down 且 consistent。"""
    case = await _setup_outcome_case(db_session, days=7)
    weights = [70.0, 69.6, 70.1, 69.3, 69.0, 68.7]
    for offset, weight in enumerate(weights):
        db_session.add(
            Checkin(
                service_case_id=case.id,
                group_id=case.group_id,
                user_id=case.user_id,
                checkin_date=(datetime.now(timezone.utc) - timedelta(days=len(weights) - 1 - offset)).date(),
                content=f"打卡{offset}",
                parsed_json={"weight": str(weight)},
            )
        )
    await db_session.flush()

    snapshot = await generate_outcome_snapshot(db_session, case, milestone="day7")

    trend = snapshot.metrics_json["weight_trend"]
    assert trend is not None
    assert trend["direction"] == "down"
    assert trend["consistent"] is True


@pytest.mark.asyncio
async def test_snapshot_subjective_signal_needs_two_distinct_days(db_session):
    """主观印证：同一体感维度不同日≥2 次正向=established，单次=candidate。"""
    case = await _setup_outcome_case(db_session, days=7)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            OutcomeEvent(
                service_case_id=case.id, milestone="day7", field_key="sleep_change",
                event_type="improved", value_text="睡得好", evidence_text="e1",
                confidence=0.9, source="archivist", occurred_at=now - timedelta(days=2),
            ),
            OutcomeEvent(
                service_case_id=case.id, milestone="day7", field_key="sleep_change",
                event_type="improved", value_text="又睡得好", evidence_text="e2",
                confidence=0.9, source="archivist", occurred_at=now - timedelta(days=1),
            ),
            OutcomeEvent(
                service_case_id=case.id, milestone="day7", field_key="energy_change",
                event_type="improved", value_text="精神好", evidence_text="e3",
                confidence=0.9, source="archivist", occurred_at=now,
            ),
        ]
    )
    await db_session.flush()

    snapshot = await generate_outcome_snapshot(db_session, case, milestone="day7")

    signals = snapshot.evidence_json["subjective_signals"]
    assert signals["sleep_change"]["distinct_days"] == 2
    assert signals["sleep_change"]["status"] == "established"
    assert signals["energy_change"]["status"] == "candidate"


@pytest.mark.asyncio
async def test_generate_outcome_snapshot_is_idempotent(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "今天下午没那么困了")
    await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[_event("energy_change", "improved", value="精神稳一点", evidence=message.content)],
        profile_signals=[],
    )

    first = await generate_outcome_snapshot(db_session, case, milestone="day7")
    second = await generate_outcome_snapshot(db_session, case, milestone="day7")

    assert first.id == second.id
    assert second.metrics_json["milestone"] == "day7"
    assert second.evidence_json["outcome_events"]


@pytest.mark.asyncio
async def test_intake_initializes_user_state_profile(db_session):
    case = await _setup_outcome_case(db_session, days=1)

    state_profile = await initialize_user_state_from_intake(
        db_session,
        service_case=case,
        profile_json={
            "summary": "用户希望先改善精神和睡眠。",
            "day28_goal": "0-2斤；重点是状态更稳。",
            "main_problem": "节律不稳定",
            "risks": "暂无明确风险",
            "need_analysis": {
                "surface_needs": ["没精神", "睡好"],
                "deep_needs": ["睡眠债与节律不稳"],
                "priority_need": "先恢复白天精力",
                "system_outputs": ["早C/午B/晚A节律方案", "睡前流程"],
                "next_actions": ["先守住前三天产品节律"],
                "evidence_notes": ["用户期待白天更有精神"],
            },
        },
        intake_facts={
            "legal_name": "蔡浩然",
            "preferred_name": "NAR",
            "gender": "男",
            "age": "30",
            "height_cm": "175",
            "weight_kg": "65",
            "bmi": "21.2",
            "waist": "未提供",
            "weight_loss_band": "维持优化带（A带）",
            "main_needs": "感觉总是累、没精神",
            "day28_expectation": "白天更有精神",
            "health_note": "无",
            "diet_pattern": "主食偏多",
        },
    )

    assert state_profile.service_case_id == case.id
    domains = state_profile.state_json["domains"]
    assert "action_focus" not in domains
    assert domains["basic"]["current"]["value"]["weight_loss_band"] == "维持优化带（A带）"
    assert domains["goals"]["current"]["value"]["main_needs"] == "感觉总是累、没精神"
    assert domains["need_analysis"]["current"]["value"]["priority_need"] == "先恢复白天精力"
    assert domains["goals"]["history"]


@pytest.mark.asyncio
async def test_intake_rebuild_preserves_dynamic_current_and_history(db_session):
    """重新生成入营画像只刷新基线，不得清空已有聊天成效与长期画像。"""
    case = await _setup_outcome_case(db_session, days=7)
    message = await _add_message(db_session, case, "我喜欢户外徒步，这周白天精神也好多了")
    await apply_archived_outcome(
        db_session,
        service_case=case,
        anchor_message=message,
        outcome_events=[
            _event(
                "energy_change",
                "improved",
                value="白天精神改善",
                evidence="这周白天精神也好多了",
            )
        ],
        profile_signals=[
            {
                "domain": "personal_preferences",
                "signal_type": "activity_preference",
                "value": "喜欢户外徒步",
                "evidence_text": "我喜欢户外徒步",
                "confidence": 0.93,
            }
        ],
    )

    before = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    dynamic_before = {
        key: before.state_json["domains"][key]
        for key in ("personal_preferences", "body_feedback")
    }

    rebuilt = await initialize_user_state_from_intake(
        db_session,
        service_case=case,
        profile_json={
            "summary": "新的入营基线",
            "day28_goal": "精神更稳",
            "main_problem": "作息不稳定",
            "need_analysis": {"priority_need": "先调整作息"},
        },
        intake_facts={
            "legal_name": "重建测试用户",
            "weight_kg": "64",
            "main_needs": "改善精力",
            "day28_expectation": "白天不困",
            "health_note": "无",
            "diet_pattern": "三餐规律",
        },
    )

    domains = rebuilt.state_json["domains"]
    assert domains["basic"]["current"]["value"]["weight_kg"] == "64"
    assert domains["goals"]["current"]["value"]["day28_goal"] == "精神更稳"
    assert domains["personal_preferences"] == dynamic_before["personal_preferences"]
    assert domains["body_feedback"] == dynamic_before["body_feedback"]
    assert domains["personal_preferences"]["history"]
    assert domains["body_feedback"]["history"]


@pytest.mark.asyncio
async def test_dashboard_summary_includes_global_outcome_metrics(db_session, admin_client, mock_ai):
    baseline_response = await admin_client.get("/dashboard/summary")
    assert baseline_response.status_code == 200
    baseline_metrics = {
        item["key"]: item
        for item in baseline_response.json()["outcome_summary"]["metrics"]
    }
    baseline_weight = baseline_metrics["avg_day28_weight_change"]
    baseline_weight_count = baseline_weight["denominator"]
    baseline_weight_value = baseline_weight["value"] or 0

    case = await _setup_outcome_case(db_session, days=28)
    db_session.add(
        UserProfile(
            service_case_id=case.id,
            profile_json={"summary": "已建档"},
            summary="已建档",
            generated_by_model="test",
        )
    )
    db_session.add_all(
        [
            UserDocument(
                service_case_id=case.id,
                document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
                title="个人能量重启处方单",
                content_md="test",
                content_json={"blocks": []},
                status=DocumentStatus.REVIEWING,
            ),
            UserDocument(
                service_case_id=case.id,
                document_type=DocumentType.DAY28_EXECUTION_STANDARD,
                title="28天执行标准与打卡要求",
                content_md="test",
                content_json={"blocks": []},
                status=DocumentStatus.REVIEWING,
            ),
        ]
    )
    for offset in range(20):
        db_session.add(
            Checkin(
                service_case_id=case.id,
                group_id=case.group_id,
                checkin_date=(datetime.now(timezone.utc) - timedelta(days=27 - offset)).date(),
                content=f"第{offset + 1}天打卡",
                parsed_json={"weight": str(65 - offset * 0.05), "waist": str(80 - offset * 0.1)},
            )
        )
    db_session.add_all(
        [
            OutcomeEvent(
                service_case_id=case.id,
                milestone="day7",
                field_key="energy_change",
                event_type="improved",
                value_text="精神更稳",
                evidence_text="今天下午没那么困了",
                confidence=0.9,
                source="chat",
                occurred_at=datetime.now(timezone.utc),
            ),
            OutcomeSnapshot(
                service_case_id=case.id,
                milestone="day7",
                status="ready",
                metrics_json={"checkin_days": 7},
                evidence_json={},
                ai_summary="7日快照",
                generated_at=datetime.now(timezone.utc),
            ),
            OutcomeSnapshot(
                service_case_id=case.id,
                milestone="day28",
                status="ready",
                metrics_json={"weight_change": -1.2, "waist_change": -2.0},
                evidence_json={},
                ai_summary="28日快照",
                generated_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await db_session.flush()

    response = await admin_client.get("/dashboard/summary")

    assert response.status_code == 200
    outcome_summary = response.json()["outcome_summary"]
    metrics = {item["key"]: item for item in outcome_summary["metrics"]}
    assert metrics["profile_completion_rate"]["numerator"] >= 1
    assert metrics["core_document_generation_rate"]["numerator"] >= 1
    assert metrics["day7_snapshot_rate"]["numerator"] >= 1
    assert metrics["day28_checkin_completion_rate"]["numerator"] >= 1
    assert metrics["body_feedback_rate"]["numerator"] >= 1
    assert metrics["core_document_generation_rate"]["label"] == "核心交付生成率"
    expected_weight_avg = round(
        ((baseline_weight_value * baseline_weight_count) - 1.2)
        / (baseline_weight_count + 1),
        2,
    )
    assert metrics["avg_day28_weight_change"]["value"] == expected_weight_avg
    assert metrics["avg_day28_weight_change"]["denominator"] == baseline_weight_count + 1


@pytest.mark.asyncio
async def test_dashboard_sleep_improvement_rate_counts_positive_only(
    db_session, admin_client, mock_ai
):
    baseline = await admin_client.get("/dashboard/summary")
    baseline_metric = next(
        item
        for item in baseline.json()["outcome_summary"]["metrics"]
        if item["key"] == "sleep_improvement_rate"
    )
    baseline_numerator = baseline_metric["numerator"]

    improved_case = await _setup_outcome_case(db_session, days=14)
    worse_case = await _setup_outcome_case(db_session, days=14)
    for case, event_type in ((improved_case, "improved"), (worse_case, "worsened")):
        db_session.add(
            OutcomeEvent(
                service_case_id=case.id,
                milestone="day7",
                field_key="sleep_change",
                event_type=event_type,
                value_text="睡眠变化",
                evidence_text="用户原话",
                confidence=0.9,
                source="chat",
                occurred_at=datetime.now(timezone.utc),
            )
        )
        db_session.add(
            Checkin(
                service_case_id=case.id,
                group_id=case.group_id,
                checkin_date=datetime.now(timezone.utc).date(),
                content="打卡",
                parsed_json={"sleep": "睡了7小时", "weight": "65"},
            )
        )
    await db_session.flush()

    response = await admin_client.get("/dashboard/summary")
    assert response.status_code == 200
    metrics = {
        item["key"]: item for item in response.json()["outcome_summary"]["metrics"]
    }
    metric = metrics["sleep_improvement_rate"]

    # 只有 improved 计入分子，worsened 不算；分母是全部有效服务单
    assert metric["numerator"] == baseline_numerator + 1
    assert metric["label"] == "睡眠质量提升率"
    assert metric["unit"] == "%"
    assert metric["denominator"] == metrics["profile_completion_rate"]["denominator"]
    # 副口径落在说明文字里：打卡记录过睡眠的人中有多少变好
    assert "打卡里记录过睡眠的" in metric["description"]
