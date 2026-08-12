from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.checkin import Checkin
from app.models.checkin_reward import (
    CheckinRewardProgram,
    CheckinRewardProgressNotice,
    CheckinRewardQualification,
    CheckinRewardShipment,
)
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.reward_shipping_tools import build_reward_shipping_tools
from app.services.checkin_rewards import (
    is_effective_reward_checkin,
    reconcile_case_rewards,
    render_qualification_notice,
)


def _case() -> ServiceCase:
    token = uuid4().hex
    return ServiceCase(
        case_no=f"REWARD-{token[:10]}",
        group_id=f"reward-{token[:10]}",
        questionnaire_token=token,
        status=ServiceCaseStatus.IN_SERVICE,
        service_plan_start_date=date(2026, 8, 1),
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _parsed(*, prefix: str = "checklist:7", complete: bool = True) -> dict:
    custom = {
        f"{prefix}:{index}": {"answer": "未完成" if index == 3 else "已回答"}
        for index in range(2, 6)
    }
    if not complete:
        custom.pop(f"{prefix}:5")
    return {
        "product_schedule": {
            "morning_c": "已喝",
            "noon_b": "未喝",
            "night_a": "暂停",
        },
        "custom_checkin": custom,
    }


def test_effective_reward_checkin_requires_one_complete_checklist_version() -> None:
    row = Checkin(
        service_case_id=1,
        group_id="g",
        checkin_date=date(2026, 8, 1),
        content="五项都回了",
        parsed_json=_parsed(),
    )
    assert is_effective_reward_checkin(row) is True

    row.parsed_json = _parsed(complete=False)
    row.parsed_json["custom_checkin"]["checklist:other:5"] = {"answer": "已回答"}
    assert is_effective_reward_checkin(row) is False


@pytest.mark.asyncio
async def test_reconcile_creates_each_tier_once_and_skips_stale_progress(db_session) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    for offset in range(7):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content=f"打卡 {offset + 1}",
                parsed_json=_parsed(),
            )
        )
    db_session.add(
        CheckinRewardProgressNotice(
            service_case_id=service_case.id,
            threshold_days=2,
            next_tier_days=3,
        )
    )
    await db_session.flush()

    first = await reconcile_case_rewards(
        db_session,
        service_case_id=service_case.id,
        through_date=date(2026, 8, 12),
    )
    second = await reconcile_case_rewards(
        db_session,
        service_case_id=service_case.id,
        through_date=date(2026, 8, 12),
    )

    qualifications = list(
        await db_session.scalars(
            select(CheckinRewardQualification)
            .where(CheckinRewardQualification.service_case_id == service_case.id)
            .order_by(CheckinRewardQualification.tier_days)
        )
    )
    progress = await db_session.scalar(
        select(CheckinRewardProgressNotice).where(
            CheckinRewardProgressNotice.service_case_id == service_case.id
        )
    )
    assert first.effective_days == 7
    assert first.new_qualification_days == (3, 7)
    assert second.new_qualification_days == ()
    assert [row.tier_days for row in qualifications] == [3, 7]
    assert progress.notification_status == "skipped"

    text = await render_qualification_notice(
        db_session,
        service_case_id=service_case.id,
        newly_qualified=qualifications,
    )
    assert "本次新获得：1 管咖啡、1 盒钥匙" in text
    assert "目前尚未寄出：1 管咖啡、1 盒钥匙" in text
    assert "1 现在寄出" in text
    assert "2 继续累积" in text


@pytest.mark.asyncio
async def test_reward_shipping_tool_collects_fields_across_messages(db_session) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    program = CheckinRewardProgram(
        service_case_id=service_case.id,
        current_choice="pending_choice",
        choice_tier_days=3,
    )
    qualification = CheckinRewardQualification(
        service_case_id=service_case.id,
        tier_days=3,
        reward_code="coffee_tube",
        reward_name="咖啡",
        quantity=1,
        qualified_on=date(2026, 8, 3),
    )
    db_session.add_all([program, qualification])
    await db_session.flush()
    first_message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="1，张三，13800138000",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(first_message)
    await db_session.flush()

    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=first_message.id,
        )
    )[0]
    result = await tool.ainvoke(
        {
            "action": "ship_now",
            "recipient_name": "张三",
            "recipient_phone": "13800138000",
        }
    )
    assert result["shipment_status"] == "missing_address"
    assert result["missing_shipping_fields"] == ["详细地址"]

    second_message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="杭州市西湖区某街道 88 号",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(second_message)
    await db_session.flush()
    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=second_message.id,
        )
    )[0]
    result = await tool.ainvoke(
        {
            "action": "save_shipping_info",
            "shipping_address": "杭州市西湖区某街道 88 号",
        }
    )
    shipment = await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    )
    assert result["shipment_status"] == "pending_shipment"
    assert result["missing_shipping_fields"] == []
    assert shipment.recipient_name == "张三"
    assert shipment.shipping_address == "杭州市西湖区某街道 88 号"
    assert qualification.shipment_id == shipment.id


@pytest.mark.asyncio
async def test_reward_shipping_tool_does_not_treat_unrelated_one_as_choice(db_session) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="1",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    tool = build_reward_shipping_tools(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=message.id)
    )[0]

    status_payload = await tool.ainvoke({"action": "get_status"})
    rejected = await tool.ainvoke({"action": "ship_now"})

    assert status_payload["has_earned_rewards"] is False
    assert rejected["ok"] is False
    assert "没有已获得" in rejected["error"]


@pytest.mark.asyncio
async def test_admin_marks_complete_shipment_as_shipped_and_notifies(
    db_session, admin_client
) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    shipment = CheckinRewardShipment(
        service_case_id=service_case.id,
        status="pending_shipment",
        recipient_name="张三",
        recipient_phone="13800138000",
        shipping_address="杭州市西湖区某街道 88 号",
        requested_at=datetime.now(timezone.utc),
    )
    db_session.add(shipment)
    await db_session.flush()
    db_session.add(
        CheckinRewardQualification(
            service_case_id=service_case.id,
            tier_days=3,
            reward_code="coffee_tube",
            reward_name="咖啡",
            quantity=1,
            qualified_on=date(2026, 8, 3),
            shipment_id=shipment.id,
        )
    )
    await db_session.flush()

    response = await admin_client.post(
        f"/checkin-rewards/shipments/{shipment.id}/mark-shipped",
        headers={"x-admin-operator": "ops-a"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["marked_by"] == "ops-a"
    assert response.json()["user_notification_sent"] is True
    assert shipment.status == "shipped"
    assert shipment.notification_status == "sent"
    message = await db_session.get(Message, shipment.notification_message_id)
    assert message.content == "你的打卡奖励已寄出，请留意查收～"
