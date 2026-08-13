from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from app.models.outcome import OutcomeEvent, OutcomeSnapshot
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.outcome_trends import build_outcome_trend_summary


async def _trend_case(db_session, start: date) -> ServiceCase:
    suffix = uuid4().hex
    service_case = ServiceCase(
        case_no=f"TREND-{suffix}",
        group_id=f"trend-group-{suffix}",
        questionnaire_token=f"trend-token-{suffix}",
        status=ServiceCaseStatus.IN_SERVICE,
        service_plan_start_date=start,
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


def _at(day: date, hour: int = 4) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def _metric(payload: dict, key: str) -> dict:
    return next(
        metric
        for group in payload["groups"]
        for metric in group["metrics"]
        if metric["key"] == key
    )


def _point(payload: dict, metric_key: str, month: str) -> dict:
    return next(
        point for point in _metric(payload, metric_key)["points"] if point["month"] == month
    )


@pytest.mark.asyncio
async def test_outcome_trends_compare_only_same_maturity_stage(db_session):
    """新批次没走到阶段时不能进入分母，晚于前28天的证据也不能倒灌。"""

    fixed_now = datetime(2026, 8, 8, 4, tzinfo=timezone.utc)
    baseline = await build_outcome_trend_summary(
        db_session,
        months=3,
        now=fixed_now,
    )
    june = await _trend_case(db_session, date(2026, 6, 1))
    july = await _trend_case(db_session, date(2026, 7, 20))
    await _trend_case(db_session, date(2026, 8, 5))

    db_session.add(
        UserProfile(
            service_case_id=june.id,
            profile_json={"summary": "已建档"},
            summary="已建档",
            generated_by_model="test",
            created_at=_at(date(2026, 6, 2)),
        )
    )
    db_session.add(
        UserProfile(
            service_case_id=july.id,
            profile_json={"summary": "已建档"},
            summary="已建档",
            generated_by_model="test",
            created_at=_at(date(2026, 7, 21)),
        )
    )
    for document_type in (
        DocumentType.ENERGY_RESTART_PRESCRIPTION,
        DocumentType.DAY28_EXECUTION_STANDARD,
    ):
        db_session.add(
            UserDocument(
                service_case_id=june.id,
                document_type=document_type,
                title="核心交付",
                content_md="test",
                content_json={},
                status=DocumentStatus.REVIEWING,
                created_at=_at(date(2026, 6, 3)),
            )
        )
    db_session.add(
        OutcomeSnapshot(
            service_case_id=june.id,
            milestone="day7",
            status="ready",
            metrics_json={},
            evidence_json={},
            generated_at=_at(date(2026, 6, 7)),
            created_at=_at(date(2026, 6, 7)),
        )
    )

    for offset in range(20):
        checkin_day = date(2026, 6, 1) + timedelta(days=offset)
        db_session.add(
            Checkin(
                service_case_id=june.id,
                group_id=june.group_id,
                checkin_date=checkin_day,
                content="打卡",
                parsed_json={},
                created_at=_at(checkin_day),
            )
        )
    for offset in (0, 2, 5):
        message_day = date(2026, 6, 1) + timedelta(days=offset)
        db_session.add(
            Message(
                service_case_id=june.id,
                group_id=june.group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                content="用户反馈",
                status=MessageStatus.RECEIVED,
                analysis_json={
                    "renewal_intent": {"detected": offset == 5},
                    "refund_intent": {"detected": False},
                },
                created_at=_at(message_day),
            )
        )
    db_session.add_all(
        [
            OutcomeEvent(
                service_case_id=june.id,
                milestone="day7",
                field_key="sleep_change",
                event_type="improved",
                value_text="睡得更稳",
                evidence_text="原话",
                confidence=0.9,
                source="chat",
                occurred_at=_at(date(2026, 6, 4)),
            ),
            OutcomeEvent(
                service_case_id=june.id,
                milestone="day28",
                field_key="weight_change",
                event_type="improved",
                value_text="体重下降",
                evidence_text="原话",
                confidence=0.9,
                source="chat",
                occurred_at=_at(date(2026, 6, 20)),
            ),
            OutcomeEvent(
                service_case_id=june.id,
                milestone="day28",
                field_key="referral_signal",
                event_type="completed",
                value_text="推荐朋友",
                evidence_text="原话",
                confidence=0.9,
                source="chat",
                occurred_at=_at(date(2026, 6, 25)),
            ),
        ]
    )
    # 第 30 天才表达退款，不属于首个 28 天服务窗口。
    db_session.add(
        Message(
            service_case_id=june.id,
            group_id=june.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            content="晚于服务窗口的退款意向",
            status=MessageStatus.RECEIVED,
            analysis_json={
                "renewal_intent": {"detected": False},
                "refund_intent": {"detected": True},
            },
            created_at=_at(date(2026, 6, 30)),
        )
    )
    await db_session.flush()

    payload = await build_outcome_trend_summary(
        db_session,
        months=3,
        now=fixed_now,
    )

    assert [item["month"] for item in payload["cohorts"]] == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    cohort_deltas = [
        {
            key: current[key] - before[key]
            for key in ("started_cases", "eligible_day7_cases", "eligible_day28_cases")
        }
        for current, before in zip(payload["cohorts"], baseline["cohorts"], strict=True)
    ]
    assert cohort_deltas == [
        {"started_cases": 1, "eligible_day7_cases": 1, "eligible_day28_cases": 1},
        {"started_cases": 1, "eligible_day7_cases": 1, "eligible_day28_cases": 0},
        {"started_cases": 1, "eligible_day7_cases": 0, "eligible_day28_cases": 0},
    ]

    def assert_point_delta(
        metric_key: str,
        month: str,
        *,
        numerator: int,
        denominator: int,
    ) -> None:
        current = _point(payload, metric_key, month)
        before = _point(baseline, metric_key, month)
        assert current["numerator"] - before["numerator"] == numerator
        assert current["denominator"] - before["denominator"] == denominator

    assert_point_delta("profile_completion_rate", "2026-06", numerator=1, denominator=1)
    assert_point_delta("profile_completion_rate", "2026-07", numerator=1, denominator=1)
    assert_point_delta("core_document_generation_rate", "2026-07", numerator=0, denominator=1)
    assert_point_delta("day7_checkin_active_rate", "2026-06", numerator=1, denominator=1)
    assert_point_delta("day28_checkin_completion_rate", "2026-06", numerator=1, denominator=1)
    assert_point_delta("body_feedback_rate", "2026-06", numerator=1, denominator=1)
    assert_point_delta("sleep_improvement_rate", "2026-06", numerator=1, denominator=1)
    assert_point_delta("renewal_intent_rate", "2026-06", numerator=1, denominator=1)
    assert_point_delta("refund_intent_rate", "2026-06", numerator=0, denominator=1)
    assert_point_delta("referral_signal_rate", "2026-06", numerator=1, denominator=1)
    # 7 月这批人只走到了第 7 天，因此任何第 28 天指标都不新增分母。
    assert_point_delta("result_signal_rate", "2026-07", numerator=0, denominator=0)


@pytest.mark.asyncio
async def test_outcome_trends_endpoint_returns_auditable_shape(admin_client):
    response = await admin_client.get("/dashboard/outcome-trends?months=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["months"] == 2
    assert len(payload["cohorts"]) == 2
    assert [item["key"] for item in payload["groups"]] == [
        "efficiency",
        "participation",
        "outcome",
        "commercial",
    ]
    assert all(
        {"value", "numerator", "denominator", "status"} <= set(point)
        for group in payload["groups"]
        for metric in group["metrics"]
        for point in metric["points"]
    )
    assert payload["history_notes"]
