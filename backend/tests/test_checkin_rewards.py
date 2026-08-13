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
    reward_checkin_evidence_message_ids,
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


def test_reward_evidence_prefers_exact_ids_over_legacy_batch_anchor() -> None:
    parsed = _parsed()
    parsed["_meta"] = {"evidence_message_ids": [31, "32"]}
    parsed["custom_checkin"]["checklist:7:2"]["evidence_message_id"] = 33
    row = Checkin(
        service_case_id=1,
        group_id="g",
        checkin_date=date(2026, 8, 1),
        content="五项都回了",
        raw_message_id="99",
        parsed_json=parsed,
    )

    assert reward_checkin_evidence_message_ids(row) == [31, 32, 33]


def test_reward_evidence_falls_back_to_legacy_batch_anchor() -> None:
    row = Checkin(
        service_case_id=1,
        group_id="g",
        checkin_date=date(2026, 8, 1),
        content="五项都回了",
        raw_message_id="99",
        parsed_json=_parsed(),
    )

    assert reward_checkin_evidence_message_ids(row) == [99]


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
    assert first.complete_checkin_days == 7
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
async def test_completed_case_can_settle_day28_during_grace_without_counting_day29(
    db_session,
) -> None:
    service_case = _case()
    service_case.status = ServiceCaseStatus.COMPLETED
    service_case.ended_at = datetime(2026, 8, 28, 15, 59, tzinfo=timezone.utc)
    db_session.add(service_case)
    await db_session.flush()
    for offset in range(29):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content=f"打卡 {offset + 1}",
                parsed_json=_parsed(),
            )
        )
    await db_session.flush()

    result = await reconcile_case_rewards(
        db_session,
        service_case_id=service_case.id,
        through_date=date(2026, 8, 29),
        allow_completed_settlement=True,
    )

    assert result.complete_checkin_days == 28
    assert result.valid_dates[-1] == date(2026, 8, 28)
    assert result.new_qualification_days == (3, 7, 14, 28)


@pytest.mark.asyncio
async def test_refunded_case_stops_new_rewards_but_keeps_existing_qualification(
    db_session,
) -> None:
    service_case = _case()
    service_case.status = ServiceCaseStatus.REFUNDED
    db_session.add(service_case)
    await db_session.flush()
    existing = CheckinRewardQualification(
        service_case_id=service_case.id,
        tier_days=3,
        reward_code="coffee_tube",
        reward_name="咖啡",
        quantity=1,
        qualified_on=date(2026, 8, 3),
    )
    db_session.add(existing)
    for offset in range(7):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content=f"退款前完整打卡 {offset + 1}",
                parsed_json=_parsed(),
            )
        )
    await db_session.flush()

    result = await reconcile_case_rewards(
        db_session,
        service_case_id=service_case.id,
        through_date=date(2026, 8, 28),
    )
    qualifications = list(
        await db_session.scalars(
            select(CheckinRewardQualification).where(
                CheckinRewardQualification.service_case_id == service_case.id
            )
        )
    )

    assert result.new_qualification_days == ()
    assert [row.tier_days for row in qualifications] == [3]


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
async def test_reward_admin_lists_each_counted_day_with_five_items_and_original_messages(
    db_session, admin_client
) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    db_session.add(
        CheckinRewardQualification(
            service_case_id=service_case.id,
            tier_days=3,
            reward_code="coffee_tube",
            reward_name="咖啡",
            quantity=1,
            qualified_on=date(2026, 8, 3),
        )
    )
    await db_session.flush()

    for offset in range(3):
        message = Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content=f"第{offset + 1}天原话：早C喝了，午B没喝，晚A暂停；蔬菜2拳；快走25分钟；23:20睡；情绪4分",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
        )
        db_session.add(message)
        await db_session.flush()
        parsed = _parsed(prefix="checklist:7")
        parsed["custom_checkin"] = {
            f"checklist:7:{index}": {
                "question": f"个性题目{index}",
                "answer": f"第{index}项回答",
                "evidence_message_id": message.id,
            }
            for index in range(2, 6)
        }
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content="结构化打卡",
                raw_message_id=str(message.id),
                parsed_json=parsed,
            )
        )
    await db_session.flush()

    response = await admin_client.get("/checkin-rewards")

    assert response.status_code == 200, response.text
    row = next(
        item
        for item in response.json()
        if item["service_case_id"] == service_case.id
    )
    assert row["complete_checkin_days"] == 3
    assert [item["checkin_date"] for item in row["checkin_days"]] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]
    first_day = row["checkin_days"][0]
    assert first_day["cumulative_day"] == 1
    assert len(first_day["items"]) == 5
    assert first_day["items"][0] == {
        "order": 1,
        "item_id": "checklist:7:1",
        "question": "早C、午B、晚A",
        "answer": "早C：已喝；午B：未喝；晚A：暂停",
    }
    assert first_day["items"][1]["question"] == "个性题目2"
    assert first_day["source_messages"][0]["content"].startswith("第1天原话")
    assert first_day["original_submission"] is None


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
