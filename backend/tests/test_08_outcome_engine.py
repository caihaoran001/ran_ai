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
    assert domains["personal_preferences"]["current"]["value"]["activity_preference"]["value"] == "喜欢户外和漂流"
    assert domains["lifestyle_habits"]["current"]["value"]["meal_pattern"]["value"] == "长期不吃晚饭"


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
