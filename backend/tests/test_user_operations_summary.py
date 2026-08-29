from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.user_operations_summary import (
    attention_summary,
    build_user_operations_snapshot,
    latest_dynamic_fact,
    latest_staff_guidance,
)


def test_latest_dynamic_fact_uses_event_time_across_domains() -> None:
    state = {
        "domains": {
            "goals": {
                "label": "改善目标",
                "history": [
                    {
                        "field_key": "started_execution",
                        "value": "已开始执行",
                        "source": "archivist",
                        "evidence": "7 月 10 日开始执行",
                        "occurred_at": "2026-07-10T08:00:00+00:00",
                    }
                ],
            },
            "body_feedback": {
                "label": "体感反馈",
                "history": [
                    {
                        "field_key": "sleep_change",
                        "value": "近两天睡得更稳",
                        "source": "archivist",
                        "evidence": "用户反馈近两天睡眠改善",
                        "occurred_at": "2026-07-12T08:00:00+00:00",
                    }
                ],
            },
            "personal_preferences": {
                "label": "个人偏好",
                "history": [
                    {
                        "field_key": "food_preference",
                        "value": "偏好清淡",
                        "source": "intake",
                        "evidence": "入营问卷",
                        "occurred_at": "2026-07-13T08:00:00+00:00",
                    }
                ],
            },
        }
    }

    fact = latest_dynamic_fact(state)

    assert fact is not None
    assert fact.domain == "body_feedback"
    assert fact.value == "近两天睡得更稳"
    assert fact.evidence == "用户反馈近两天睡眠改善"


def test_latest_dynamic_fact_does_not_treat_intake_as_a_recent_change() -> None:
    state = {
        "domains": {
            "goals": {
                "label": "改善目标",
                "history": [
                    {
                        "value": {"day28_goal": "改善睡眠"},
                        "source": "intake",
                        "evidence": "入营问卷",
                        "occurred_at": "2026-07-16T08:00:00+00:00",
                    }
                ],
            }
        }
    }

    assert latest_dynamic_fact(state) is None


def test_latest_staff_guidance_returns_the_newest_confirmed_arrangement() -> None:
    state = {
        "domains": {
            "staff_guidance": {
                "label": "班主任确认的个性化安排",
                "history": [
                    {
                        "field_key": "staff_guidance_1",
                        "value": "晚餐主食减半",
                        "source": "archivist_staff_guidance",
                        "evidence": "班主任原话 1",
                        "occurred_at": "2026-07-10T08:00:00+00:00",
                    },
                    {
                        "field_key": "staff_guidance_2",
                        "value": "明天早上反馈饥饿感",
                        "source": "archivist_staff_guidance",
                        "evidence": "班主任原话 2",
                        "occurred_at": "2026-07-11T08:00:00+00:00",
                    },
                ],
            }
        }
    }

    fact = latest_staff_guidance(state)

    assert fact is not None
    assert fact.value == "明天早上反馈饥饿感"
    assert fact.evidence == "班主任原话 2"


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"status": ServiceCaseStatus.COMPLETED}, "closed"),
        ({"refund_pending_confirmation": True}, "action_required"),
        ({"ai_auto_reply_enabled": False}, "manual"),
        ({"risk_level": "high"}, "guarded"),
        ({"risk_level": "medium"}, "watch"),
        ({}, "automatic"),
    ],
)
def test_attention_summary_priority(changes: dict[str, object], expected_code: str) -> None:
    values: dict[str, object] = {
        "status": ServiceCaseStatus.IN_SERVICE,
        "refund_pending_confirmation": False,
        "ai_auto_reply_enabled": True,
        "risk_level": "normal",
        "risk_flags": [],
        "health_profile": None,
        "intervention_reasons": [],
    }
    values.update(changes)

    code, _label, _reason = attention_summary(SimpleNamespace(**values))  # type: ignore[arg-type]

    assert code == expected_code


@pytest.mark.parametrize(
    ("status", "expected_label"),
    [
        (ServiceCaseStatus.CREATED, "等待首次 @ 机器人"),
        (ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED, "画像生成中"),
        (ServiceCaseStatus.GENERATING, "画像生成中"),
        (ServiceCaseStatus.DOCUMENTS_GENERATED, "文件待审核"),
        (ServiceCaseStatus.REVIEWING, "文件审核中"),
        (ServiceCaseStatus.APPROVED, "等待开始服务"),
    ],
)
def test_attention_summary_uses_pre_service_status(status: ServiceCaseStatus, expected_label: str) -> None:
    case = SimpleNamespace(
        status=status,
        refund_pending_confirmation=False,
        ai_auto_reply_enabled=True,
        risk_level="normal",
        risk_flags=[],
        health_profile=None,
        intervention_reasons=[],
    )

    code, label, _reason = attention_summary(case)  # type: ignore[arg-type]

    assert code == "watch"
    assert label == expected_label


def test_created_case_attention_does_not_require_questionnaire() -> None:
    case = SimpleNamespace(
        status=ServiceCaseStatus.CREATED,
        refund_pending_confirmation=False,
        ai_auto_reply_enabled=True,
        ai_introduced_at=datetime.now(timezone.utc),
        service_plan_start_date=None,
        risk_level="normal",
        risk_flags=[],
        health_profile=None,
        intervention_reasons=[],
    )

    code, label, reason = attention_summary(case)  # type: ignore[arg-type]

    assert code == "watch"
    assert label == "等待设置开始日期"
    assert "问卷可选" in reason


@pytest.mark.asyncio
async def test_operations_snapshot_counts_only_unique_non_deleted_checkin_days(
    db_session,
    create_case,
    admin_client,
) -> None:
    suffix = secrets.token_hex(6)
    response = await create_case(
        {
            "group_id": f"operations-summary-{suffix}",
            "user": {"name": "画像汇总测试"},
        }
    )
    case_data = response.json()
    service_case = await db_session.get(ServiceCase, case_data["id"])
    assert service_case is not None
    base = datetime(2026, 7, 10, 8, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                user_id=service_case.user_id,
                checkin_date=date(2026, 7, 10),
                content="打卡 1",
                created_at=base,
                updated_at=base,
            ),
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                user_id=service_case.user_id,
                checkin_date=date(2026, 7, 11),
                content="打卡 2",
                created_at=base + timedelta(days=1),
                updated_at=base + timedelta(days=1),
            ),
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                user_id=service_case.user_id,
                checkin_date=date(2026, 7, 10),
                content="已删除的旧打卡",
                deleted_at=base + timedelta(days=2),
                created_at=base + timedelta(days=2),
                updated_at=base + timedelta(days=2),
            ),
        ]
    )
    await db_session.flush()

    snapshot = await build_user_operations_snapshot(
        db_session,
        service_case=service_case,
        state_profile=None,
    )

    assert snapshot.checkin_days == 2
    assert snapshot.last_checkin_at == base + timedelta(days=1)

    response = await admin_client.get(f"/users/{service_case.user_id}")
    assert response.status_code == 200
    assert response.json()["operations_snapshot"]["checkin_days"] == 2


@pytest.mark.asyncio
async def test_message_limit_returns_latest_five_in_chronological_order(
    admin_client,
    create_case,
    db_session,
) -> None:
    suffix = secrets.token_hex(6)
    response = await create_case(
        {
            "group_id": f"operations-messages-{suffix}",
            "user": {"name": "最近聊天测试"},
        }
    )
    case_data = response.json()
    service_case = await db_session.get(ServiceCase, case_data["id"])
    assert service_case is not None
    base = datetime.now(timezone.utc) + timedelta(seconds=1)
    db_session.add_all(
        [
            Message(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="text",
                content=f"message-{index}",
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                created_at=base + timedelta(minutes=index),
                updated_at=base + timedelta(minutes=index),
            )
            for index in range(7)
        ]
    )
    await db_session.flush()

    result = await admin_client.get(
        f"/service-cases/{service_case.id}/messages",
        params={"limit": 5},
    )

    assert result.status_code == 200
    assert [item["content"] for item in result.json()] == [
        "message-2",
        "message-3",
        "message-4",
        "message-5",
        "message-6",
    ]
