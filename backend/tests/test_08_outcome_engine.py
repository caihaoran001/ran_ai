from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.outcome import OutcomeEvent, OutcomeSnapshot, OutcomeStageState
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.outcome_engine import (
    _build_followup_time_context,
    analyze_and_update_outcomes,
    apply_outcome_analysis,
    current_milestone_spec,
    generate_outcome_snapshot,
    get_or_create_stage_state,
    prepare_outcome_extraction,
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


@pytest.mark.asyncio
async def test_chat_outcome_extraction_updates_day7_state(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天下午没那么困了，整体精神稳一点",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    result = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    assert result["milestone"] == "day7"
    assert result["created_events"]
    assert "energy_change" in result["collected_fields"]

    events = list(
        await db_session.scalars(
            select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
        )
    )
    assert len(events) == 1
    assert events[0].field_key == "energy_change"

    state = await db_session.scalar(
        select(OutcomeStageState).where(
            OutcomeStageState.service_case_id == case.id,
            OutcomeStageState.milestone == "day7",
        )
    )
    assert state is not None
    assert "energy_change" in (state.collected_fields or {})
    assert "energy_change" not in (state.missing_fields or [])


@pytest.mark.asyncio
async def test_outcome_followup_suppressed_when_human_intervention_needed(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天下午没那么困了，但我想退款",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    result = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={
            "requires_human_intervention": True,
            "refund_intent": {"detected": True},
        },
        recent_messages=[],
    )

    assert result["followup_plan"]["should_ask"] is False
    assert "暂停成效追问" in result["followup_plan"]["reason"]


@pytest.mark.asyncio
async def test_outcome_followup_suppressed_while_case_high_risk(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    case.risk_level = "high"
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天已经好一些了",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    result = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    assert result["followup_plan"]["should_ask"] is False
    assert "暂停成效追问" in result["followup_plan"]["reason"]


@pytest.mark.asyncio
async def test_outcome_followup_suppressed_when_user_is_dissatisfied(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="这个跟我想象的不太一样，我不满意",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    result = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={
            "requires_human_intervention": True,
            "intervention_reasons": [
                {
                    "code": "complaint",
                    "level": "medium",
                    "reason": "用户表达服务体验不满。",
                    "evidence": "这个跟我想象的不太一样，我不满意",
                    "source": "chat_message",
                }
            ],
        },
        recent_messages=[],
    )

    assert result["followup_plan"]["should_ask"] is False
    assert "暂停成效追问" in result["followup_plan"]["reason"]


@pytest.mark.asyncio
async def test_outcome_followup_has_cooldown_to_avoid_reply_fatigue(db_session, mock_ai, monkeypatch):
    monkeypatch.setattr(
        "app.services.outcome_engine._local_now",
        lambda _: datetime(2026, 6, 4, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    case = await _setup_outcome_case(db_session, days=7)
    first_message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天下午没那么困了",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(first_message)
    await db_session.flush()

    first = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=first_message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )
    assert first["followup_plan"]["should_ask"] is True

    second_message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="收到，我今天先照着做",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(second_message)
    await db_session.flush()

    second = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=second_message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    assert second["followup_plan"]["should_ask"] is False
    assert "冷却期" in second["followup_plan"]["reason"]


@pytest.mark.asyncio
async def test_followup_time_context_blocks_answered_morning_fields(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    today = datetime(2026, 6, 3, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")).date()
    db_session.add(
        Checkin(
            service_case_id=case.id,
            group_id=case.group_id,
            user_id=case.user_id,
            checkin_date=today,
            content="晨间打卡",
            parsed_json={
                "weight": "70.1",
                "sleep": "23点睡，7点起，质量还行",
                "product_schedule": {"morning_c": "已喝"},
            },
        )
    )
    await db_session.flush()

    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context["local_hour"] == 8
    assert context["answered_today"]["weight"] is True
    assert context["answered_today"]["sleep"] is True
    assert "weight_change" not in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]
    assert "energy_change" in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_afternoon_allows_execution_feedback_fields(db_session):
    case = await _setup_outcome_case(db_session, days=14)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context["local_hour"] == 14
    assert "execution_rate_signal" in context["allowed_field_keys"]
    assert "adjustment_need" in context["allowed_field_keys"]
    assert "positive_body_signal" in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_evening_allows_evening_execution_fields(db_session):
    case = await _setup_outcome_case(db_session, days=28)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context["local_hour"] == 20
    assert "habit_stability" in context["allowed_field_keys"]
    assert "next_stage_intent" in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_afternoon_allows_day3_product_schedule(db_session):
    case = await _setup_outcome_case(db_session, days=3)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context["local_hour"] == 14
    assert "product_schedule" in context["allowed_field_keys"]
    assert "rhythm_understood" in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_late_night_allows_sleep_not_product_schedule(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context["local_hour"] == 23
    # 深夜时段 allowed_by_time 全量 8 字段
    expected_late_night = {
        "energy_change", "sleep_change", "bowel_change",
        "habit_stability", "next_stage_intent",
        "self_management", "lifestyle_habit", "long_term_weight_stability",
    }
    # day7 missing 只包含 energy_change, sleep_change, bowel_change 落在深夜窗口
    assert set(context["allowed_field_keys"]).issubset(expected_late_night)
    assert {"energy_change", "sleep_change", "bowel_change"}.issubset(
        set(context["allowed_field_keys"])
    )
    assert "product_schedule" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_outcome_plan_includes_followup_time_context(db_session):
    case = await _setup_outcome_case(db_session, days=3)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天刚开始喝，整体还行",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    plan = await prepare_outcome_extraction(
        db_session,
        service_case_id=case.id,
        message_id=message.id,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    assert plan is not None
    assert "followup_time_context" in plan.context
    assert "allowed_field_keys" in plan.context["followup_time_context"]


@pytest.mark.asyncio
async def test_followup_is_blocked_when_not_in_allowed_time_fields(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天精神还可以",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    result = await apply_outcome_analysis(
        db_session,
        service_case=case,
        message=message,
        spec=spec,
        state=state,
        raw={
            "outcome_events": [],
            "followup_plan": {
                "should_ask": True,
                "field_key": "weight_change",
                "question": "今天体重是多少？",
                "reason": "模型试图追问体重",
            },
        },
        intervention_analysis={"requires_human_intervention": False},
        allowed_followup_fields={"energy_change"},
    )

    assert result["followup_plan"]["should_ask"] is False
    assert "后端拦截" in result["followup_plan"]["reason"]


@pytest.mark.asyncio
async def test_generate_outcome_snapshot_is_idempotent(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天下午没那么困了",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    first = await generate_outcome_snapshot(db_session, case, milestone="day7")
    second = await generate_outcome_snapshot(db_session, case, milestone="day7")

    assert first.id == second.id
    assert second.metrics_json["milestone"] == "day7"
    assert second.evidence_json["outcome_events"]


@pytest.mark.asyncio
async def test_action_focus_switches_from_high_priority_structured_fact(db_session):
    case = await _setup_outcome_case(db_session, days=3)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="午B喝完有点心慌，我有点不敢喝了",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    result = await apply_outcome_analysis(
        db_session,
        service_case=case,
        message=message,
        spec=spec,
        state=state,
        raw={
            "outcome_events": [
                {
                    "field_key": "discomfort_feedback",
                    "event_type": "feedback",
                    "value": "用户反馈午B后心慌",
                    "evidence_text": message.content,
                    "confidence": 0.95,
                }
            ],
            "followup_plan": {"should_ask": False},
        },
        intervention_analysis={"requires_human_intervention": False},
    )

    assert result["action_focus"]["changed"] is True
    assert result["action_focus"]["current_focus"]["key"] == "safety_review"
    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    assert state_profile is not None
    action_focus = state_profile.state_json["domains"]["action_focus"]["current"]["value"]
    assert action_focus["current_focus"]["label"] == "安全与人工复核"


@pytest.mark.asyncio
async def test_unmapped_structured_fact_does_not_change_action_focus(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天体重轻了0.3斤",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)

    result = await apply_outcome_analysis(
        db_session,
        service_case=case,
        message=message,
        spec=spec,
        state=state,
        raw={
            "outcome_events": [
                {
                    "field_key": "weight_change",
                    "event_type": "feedback",
                    "value": "用户反馈体重轻了0.3斤",
                    "evidence_text": message.content,
                    "confidence": 0.9,
                }
            ],
            "followup_plan": {"should_ask": False},
        },
        intervention_analysis={"requires_human_intervention": False},
    )

    assert result["created_events"]
    assert result["action_focus"]["changed"] is False
    assert result["action_focus"]["current_focus"] == {}


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
    assert domains["basic"]["current"]["value"]["weight_loss_band"] == "维持优化带（A带）"
    assert domains["goals"]["current"]["value"]["main_needs"] == "感觉总是累、没精神"
    assert domains["need_analysis"]["current"]["value"]["priority_need"] == "先恢复白天精力"
    assert domains["goals"]["history"]


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
async def test_outcome_events_update_user_state_profile_evidence_chain(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天下午没那么困了，整体精神稳一点",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    assert state_profile is not None
    body_feedback = state_profile.state_json["domains"]["body_feedback"]
    assert body_feedback["current"]["value"]["energy_change"]["value"] == "用户反馈白天精神状态有改善"
    assert body_feedback["history"][0]["evidence"] == "今天下午没那么困了，整体精神稳一点"


@pytest.mark.asyncio
async def test_profile_signals_update_user_state_without_outcome_events(db_session, mock_ai):
    case = await _setup_outcome_case(db_session, days=7)
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="我周末一般去漂流，挺喜欢户外的，平时基本不吃晚饭",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    result = await analyze_and_update_outcomes(
        db_session,
        service_case=case,
        message=message,
        intervention_analysis={"requires_human_intervention": False},
        recent_messages=[],
    )

    assert result["created_events"] == []
    assert {item["domain"] for item in result["profile_signals"]} == {
        "personal_preferences",
        "lifestyle_habits",
    }
    state_profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    assert state_profile is not None
    domains = state_profile.state_json["domains"]
    assert domains["personal_preferences"]["current"]["value"]["activity_preference"]["value"] == "喜欢户外和漂流"
    assert domains["lifestyle_habits"]["current"]["value"]["meal_pattern"]["value"] == "长期不吃晚饭"


@pytest.mark.asyncio
async def test_followup_time_context_noon_allows_midday_fields(db_session):
    case = await _setup_outcome_case(db_session, days=3)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 12
    expected_noon = {
        "plan_understood", "started_execution", "product_schedule",
        "rhythm_understood", "documents_received", "profile_completed",
        "bowel_change", "energy_change",
    }
    assert set(context["allowed_field_keys"]).issubset(expected_noon)
    assert "started_execution" in context["allowed_field_keys"]
    assert "product_schedule" in context["allowed_field_keys"]
    assert "rhythm_understood" in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_boundary_hour_10_is_noon(db_session):
    case = await _setup_outcome_case(db_session, days=3)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 10
    assert "rhythm_understood" in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_boundary_hour_14_is_afternoon(db_session):
    case = await _setup_outcome_case(db_session, days=14)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 14
    assert "execution_rate_signal" in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_boundary_hour_18_is_evening(db_session):
    case = await _setup_outcome_case(db_session, days=28)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 18
    assert "habit_stability" in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_boundary_hour_22_is_late_night(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session,
        service_case=case,
        spec=spec,
        state=state,
        now=datetime(2026, 6, 3, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 22
    assert "sleep_change" in context["allowed_field_keys"]
    assert "product_schedule" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_answered_field_keys_maps_weight_to_three_fields(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    today = datetime(2026, 6, 3, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")).date()
    db_session.add(Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=today, content="晨间打卡", parsed_json={"weight": "70.1"},
    ))
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["answered_today"]["weight"] is True
    assert "weight_change" not in context["allowed_field_keys"]
    assert "weight_result" not in context["allowed_field_keys"]
    assert "challenge_goal_result" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_answered_field_keys_maps_body_feedback_to_energy_and_positive(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    today = datetime(2026, 6, 3, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")).date()
    db_session.add(Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=today, content="午间打卡", parsed_json={"sensation": "有点累", "mood": "还行"},
    ))
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["answered_today"]["body_feedback"] is True
    assert "energy_change" not in context["allowed_field_keys"]
    assert "positive_body_signal" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_answered_field_keys_maps_waist_to_waist_fields(db_session):
    case = await _setup_outcome_case(db_session, days=14)
    today = datetime(2026, 6, 3, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")).date()
    db_session.add(Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=today, content="晚间打卡", parsed_json={"waist": "80"},
    ))
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["answered_today"]["waist"] is True
    assert "waist_or_body_change" not in context["allowed_field_keys"]
    assert "waist_result" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_afternoon_checkin_blocks_evening_fields(db_session):
    case = await _setup_outcome_case(db_session, days=14)
    today = datetime(2026, 6, 3, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")).date()
    db_session.add(Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=today, content="下午打卡",
        parsed_json={"weight": "69.5", "product_schedule": {"morning_c": "已喝", "noon_b": "已喝"}},
    ))
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["answered_today"]["weight"] is True
    assert context["answered_today"]["product_schedule"] is True
    assert "weight_change" not in context["allowed_field_keys"]
    assert "product_schedule" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_deep_night_checkin_blocks_morning_fields(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    today = datetime(2026, 6, 3, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai")).date()
    db_session.add(Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=today, content="深夜打卡",
        parsed_json={"weight": "70.2", "sleep": "凌晨1点才睡，质量差"},
    ))
    await db_session.flush()
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["answered_today"]["weight"] is True
    assert context["answered_today"]["sleep"] is True
    assert "weight_change" not in context["allowed_field_keys"]
    assert "sleep_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_hour_5_is_late_night(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 5, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 5
    assert "sleep_change" in context["allowed_field_keys"]
    assert "energy_change" in context["allowed_field_keys"]
    assert "weight_change" not in context["allowed_field_keys"]


@pytest.mark.asyncio
async def test_followup_time_context_hour_6_is_morning(db_session):
    case = await _setup_outcome_case(db_session, days=7)
    spec = current_milestone_spec(case)
    state = await get_or_create_stage_state(db_session, case.id, spec)
    context = await _build_followup_time_context(
        db_session, service_case=case, spec=spec, state=state,
        now=datetime(2026, 6, 3, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert context["local_hour"] == 6
    assert "weight_change" in context["allowed_field_keys"]
    assert "sleep_change" in context["allowed_field_keys"]
    assert "lifestyle_habit" not in context["allowed_field_keys"]
