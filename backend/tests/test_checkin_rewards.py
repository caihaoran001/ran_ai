from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.models.checkin import Checkin
from app.models.checkin_reward import (
    CheckinRewardProgram,
    CheckinRewardProgressNotice,
    CheckinRewardQualification,
    CheckinRewardRound,
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
from app.services.checkin_reward_settings import (
    DEFAULT_REWARD_TIERS,
    set_reward_tier_config,
)
from app.services.checkin_rewards import (
    case_participates_in_current_round,
    is_effective_reward_checkin,
    reconcile_case_rewards,
    render_qualification_notice,
    reward_checkin_evidence_message_ids,
)

PROGRAM_START = "2026-08-01"


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _reward_program_enabled(db_session):
    """默认开启奖励并让本文件的服务单参与；需要停用/不参与的用例在正文覆盖。"""
    await set_reward_tier_config(
        db_session,
        {
            "enabled": True,
            "program_start_date": PROGRAM_START,
            "tiers": [
                {"days": tier.days, "reward_text": tier.reward_text}
                for tier in DEFAULT_REWARD_TIERS
            ],
        },
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
async def test_progress_notice_is_dropped_when_tier_removed_and_returns_when_added_back(
    db_session,
) -> None:
    """档位删掉后排队中的差一天提醒不再发；档位加回来要能重新提醒。"""
    from app.services import checkin_rewards as rewards_module
    from app.services.channels.base import ChannelSendResult

    def _config(days: tuple[int, ...]) -> dict:
        names = {3: "1 管咖啡", 7: "1 盒钥匙"}
        return {
            "enabled": True,
            "program_start_date": PROGRAM_START,
            "tiers": [{"days": day, "reward_text": names[day]} for day in days],
        }

    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    for offset in range(2):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content="完整打卡",
                parsed_json=_parsed(),
            )
        )
    await db_session.flush()

    first = await reconcile_case_rewards(
        db_session, service_case_id=service_case.id, through_date=date(2026, 8, 2)
    )
    assert first.new_progress_threshold == 2

    sent: list[str] = []

    async def fake_send(group_id: str, content: str) -> ChannelSendResult:
        sent.append(content)
        return ChannelSendResult(success=True, message_id="fake-progress")

    # 运营把 3 天那档删掉：排队中的提醒必须作废，不能再把取消掉的奖励发出去。
    await set_reward_tier_config(db_session, _config((7,)))
    delivered = await rewards_module._deliver_pending_progress_notices(
        service_case_id=service_case.id, send_text=fake_send
    )
    assert delivered == 0
    assert sent == []
    notice = await db_session.scalar(
        select(CheckinRewardProgressNotice).where(
            CheckinRewardProgressNotice.service_case_id == service_case.id
        )
    )
    assert notice is not None
    assert notice.notification_status == "skipped"

    # 档位加回来：作废过的那条不能挡住新提醒，奖品名跟当前配置走。
    await set_reward_tier_config(db_session, _config((3, 7)))
    again = await reconcile_case_rewards(
        db_session, service_case_id=service_case.id, through_date=date(2026, 8, 2)
    )
    assert again.new_progress_threshold == 2
    delivered = await rewards_module._deliver_pending_progress_notices(
        service_case_id=service_case.id, send_text=fake_send
    )
    assert delivered == 1
    assert sent == ["你已经累计完成 2 天完整打卡，再完成 1 天，就能获得 1 管咖啡～"]


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


def test_reward_shipping_tool_description_stops_when_recipient_fields_are_missing() -> None:
    tool = build_reward_shipping_tools(
        context=AgentRunContext(service_case_id=999)
    )[0]
    action_description = tool.args_schema.model_json_schema()["properties"]["action"][
        "description"
    ]

    assert "otherwise call no reward tool and ask only for the missing recipient fields" in (
        tool.description
    )
    assert "Never call get_status merely because recipient fields are missing" in (
        tool.description
    )
    assert "never use it for an immediate-shipment request" in action_description


@pytest.mark.asyncio
async def test_reward_shipping_tool_rejects_partial_fields_before_writing(
    db_session,
) -> None:
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
        content="现在发货，收件人张三",
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
        }
    )
    assert result["ok"] is False
    assert result["missing_shipping_fields"] == ["手机号", "详细地址"]
    assert "必须一次提供完整" in result["error"]
    save_result = await tool.ainvoke(
        {"action": "save_shipping_info", "recipient_name": "张三"}
    )
    assert save_result["ok"] is False
    assert save_result["missing_shipping_fields"] == ["手机号", "详细地址"]
    assert await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    ) is None
    await db_session.refresh(program)
    await db_session.refresh(qualification)
    assert program.current_choice == "pending_choice"
    assert program.recipient_name is None
    assert qualification.shipment_id is None


@pytest.mark.asyncio
async def test_reward_shipping_tool_writes_once_when_all_fields_are_complete(
    db_session,
) -> None:
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
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="现在发货，收件人张三，手机号13800138000，详细地址杭州市西湖区某街道88号",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=message.id,
        )
    )[0]
    result = await tool.ainvoke(
        {
            "action": "ship_now",
            "recipient_name": "张三",
            "recipient_phone": "13800138000",
            "shipping_address": "杭州市西湖区某街道88号",
        }
    )
    shipment = await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    )
    await db_session.refresh(qualification)
    assert result["shipment_status"] == "pending_shipment"
    assert result["missing_shipping_fields"] == []
    assert shipment.recipient_name == "张三"
    assert shipment.recipient_phone == "13800138000"
    assert shipment.shipping_address == "杭州市西湖区某街道88号"
    assert qualification.shipment_id == shipment.id


@pytest.mark.asyncio
async def test_reward_shipping_tool_saves_complete_address_then_reuses_it(
    db_session,
) -> None:
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
    save_message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="收件人张三，手机号13800138000，详细地址杭州市西湖区某街道88号，帮我保存",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(save_message)
    await db_session.flush()
    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=save_message.id,
        )
    )[0]
    saved = await tool.ainvoke(
        {
            "action": "save_shipping_info",
            "recipient_name": "张三",
            "recipient_phone": "13800138000",
            "shipping_address": "杭州市西湖区某街道88号",
        }
    )
    assert saved["recorded"] == "save_shipping_info"
    assert saved["active_shipment"] is False
    assert await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    ) is None

    reuse_message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="沿用之前保存的地址，现在发货",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(reuse_message)
    await db_session.flush()
    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=reuse_message.id,
        )
    )[0]
    shipped = await tool.ainvoke(
        {"action": "ship_now", "reuse_saved_address": True}
    )
    shipment = await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    )
    assert shipped["shipment_status"] == "pending_shipment"
    assert shipment.recipient_name == "张三"
    assert shipment.recipient_phone == "13800138000"
    assert shipment.shipping_address == "杭州市西湖区某街道88号"


@pytest.mark.asyncio
async def test_reward_shipping_tool_rejects_reuse_without_complete_saved_address(
    db_session,
) -> None:
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
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="沿用之前保存的地址，现在发货",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=message.id,
        )
    )[0]
    result = await tool.ainvoke(
        {"action": "ship_now", "reuse_saved_address": True}
    )
    assert result["ok"] is False
    assert result["missing_shipping_fields"] == ["收件人", "手机号", "详细地址"]
    assert "没有可沿用的完整收件信息" in result["error"]
    assert await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    ) is None
    await db_session.refresh(program)
    await db_session.refresh(qualification)
    assert program.current_choice == "pending_choice"
    assert qualification.shipment_id is None


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
async def test_reward_tools_return_pause_copy_and_reject_all_writes(
    db_session,
) -> None:
    await set_reward_tier_config(db_session, {"enabled": False})
    tools = {
        tool.name: tool
        for tool in build_reward_shipping_tools(
            context=AgentRunContext(service_case_id=999),
        )
    }

    status_result = await tools["get_checkin_reward_status"].ainvoke({})
    legacy_status_result = await tools["manage_reward_shipping"].ainvoke(
        {"action": "get_status"}
    )
    write_result = await tools["manage_reward_shipping"].ainvoke(
        {
            "action": "ship_now",
            "recipient_name": "张三",
            "recipient_phone": "13800138000",
            "shipping_address": "杭州市西湖区某街道 88 号",
        }
    )

    assert status_result == {
        "ok": True,
        "rewards_enabled": False,
        "program_paused": True,
        "reply_instruction": "奖励规则正在讨论中，确认后会另行通知。",
    }
    assert legacy_status_result == status_result
    assert write_result["ok"] is False
    assert write_result["program_paused"] is True
    assert "不记录发货、累积或收件信息" in write_result["error"]


@pytest.mark.asyncio
async def test_reward_status_tool_returns_total_tiers_choice_and_shipment_history(
    db_session,
) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    shipment = CheckinRewardShipment(
        service_case_id=service_case.id,
        status="shipped",
        requested_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        shipped_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        marked_by="ops-a",
    )
    program = CheckinRewardProgram(
        service_case_id=service_case.id,
        current_choice="accumulate",
        choice_tier_days=7,
    )
    db_session.add_all([shipment, program])
    await db_session.flush()
    qualifications = [
        CheckinRewardQualification(
            service_case_id=service_case.id,
            tier_days=3,
            reward_code="coffee_tube",
            reward_name="咖啡",
            quantity=1,
            qualified_on=date(2026, 8, 3),
            shipment_id=shipment.id,
        ),
        CheckinRewardQualification(
            service_case_id=service_case.id,
            tier_days=7,
            reward_code="key_box",
            reward_name="钥匙",
            quantity=1,
            qualified_on=date(2026, 8, 7),
        ),
    ]
    db_session.add_all(qualifications)
    for offset in range(7):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content=f"完整打卡 {offset + 1}",
                parsed_json=_parsed(),
            )
        )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_reward_shipping_tools(
            context=AgentRunContext(service_case_id=service_case.id)
        )
    }

    result = await tools["get_checkin_reward_status"].ainvoke({})

    assert result["complete_checkin_days"] == 7
    assert result["current_choice"] == "accumulate"
    assert result["current_choice_text"] == "选择继续累积"
    assert result["operational_status_text"] == "已选择继续累积"
    assert result["earned_rewards"] == "1 管咖啡、1 盒钥匙"
    assert result["shipped_rewards"] == "1 管咖啡"
    assert result["unshipped_rewards"] == "1 盒钥匙"
    assert result["reward_tiers"] == [
        {
            "required_complete_days": 3,
            "reward": "1 管咖啡",
            "earned": True,
            "qualified_on": "2026-08-03",
            "shipment_status": "shipped",
        },
        {
            "required_complete_days": 7,
            "reward": "1 盒钥匙",
            "earned": True,
            "qualified_on": "2026-08-07",
            "shipment_status": None,
        },
        {
            "required_complete_days": 14,
            "reward": "1 瓶C8",
            "earned": False,
            "qualified_on": None,
            "shipment_status": None,
        },
        {
            "required_complete_days": 28,
            "reward": "3 管咖啡",
            "earned": False,
            "qualified_on": None,
            "shipment_status": None,
        },
    ]
    assert result["shipment_history"] == [
        {
            "shipment_id": shipment.id,
            "status": "shipped",
            "status_text": "已发货",
            "rewards": "1 管咖啡",
            "requested_at": "2026-08-04T00:00:00+00:00",
            "shipped_at": "2026-08-05T00:00:00+00:00",
        }
    ]


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
        for item in response.json()["cases"]
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
async def test_reward_admin_freezes_evidence_when_case_not_in_current_round(
    db_session, admin_client
) -> None:
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    db_session.add(
        CheckinRewardQualification(
            service_case_id=service_case.id,
            tier_days=3,
            reward_code="day_3",
            reward_name="1 管咖啡",
            quantity=1,
            qualified_on=date(2026, 8, 3),
        )
    )
    for offset in range(5):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content="结构化打卡",
                parsed_json=_parsed(),
            )
        )
    await db_session.flush()

    await set_reward_tier_config(db_session, {"enabled": False})
    response = await admin_client.get("/checkin-rewards")
    assert response.status_code == 200, response.text
    row = next(
        item
        for item in response.json()["cases"]
        if item["service_case_id"] == service_case.id
    )
    assert row["participating"] is False
    assert row["complete_checkin_days"] == 3
    assert [item["checkin_date"] for item in row["checkin_days"]] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]

    await set_reward_tier_config(
        db_session, {"enabled": True, "program_start_date": "2026-08-01"}
    )
    response = await admin_client.get("/checkin-rewards")
    assert response.status_code == 200, response.text
    row = next(
        item
        for item in response.json()["cases"]
        if item["service_case_id"] == service_case.id
    )
    assert row["participating"] is True
    assert row["complete_checkin_days"] == 5


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


@pytest.mark.asyncio
async def test_admin_can_mark_reward_shipped_while_program_is_off(
    db_session, admin_client
) -> None:
    """停用只停新奖励，已获得的奖励照常可兑现。"""
    await set_reward_tier_config(db_session, {"enabled": False})
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

    response = await admin_client.post(
        f"/checkin-rewards/shipments/{shipment.id}/mark-shipped",
        headers={"x-admin-operator": "ops-a"},
    )

    assert response.status_code == 200, response.text
    assert shipment.status == "shipped"


@pytest.mark.asyncio
async def test_case_created_before_round_moment_does_not_accrue(db_session) -> None:
    moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    db_session.add(CheckinRewardRound(started_on=date(2026, 8, 1), created_at=moment))
    service_case = _case()
    service_case.created_at = moment - timedelta(seconds=1)
    db_session.add(service_case)
    await db_session.flush()
    for offset in range(3):
        db_session.add(
            Checkin(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                content="完整打卡",
                parsed_json=_parsed(),
            )
        )
    await db_session.flush()

    result = await reconcile_case_rewards(db_session, service_case_id=service_case.id)

    assert result.new_qualification_days == ()
    assert result.new_progress_threshold is None
    assert (
        await db_session.scalar(
            select(CheckinRewardProgressNotice).where(
                CheckinRewardProgressNotice.service_case_id == service_case.id
            )
        )
        is None
    )

    tools = {
        tool.name: tool
        for tool in build_reward_shipping_tools(
            context=AgentRunContext(service_case_id=service_case.id),
        )
    }
    payload = await tools["get_checkin_reward_status"].ainvoke({})
    assert payload["participating"] is False
    assert payload["complete_checkin_days"] == 0
    assert "不参与" in payload["reply_instruction"]


@pytest.mark.asyncio
async def test_participation_boundary_is_the_round_enable_moment(db_session) -> None:
    moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    db_session.add(CheckinRewardRound(started_on=date(2026, 8, 1), created_at=moment))

    before = _case()
    before.created_at = moment - timedelta(seconds=1)
    after = _case()
    after.created_at = moment + timedelta(seconds=1)
    db_session.add_all([before, after])
    await db_session.flush()
    for service_case in (before, after):
        for offset in range(3):
            db_session.add(
                Checkin(
                    service_case_id=service_case.id,
                    group_id=service_case.group_id,
                    checkin_date=date(2026, 8, 1) + timedelta(days=offset),
                    content="完整打卡",
                    parsed_json=_parsed(),
                )
            )
    await db_session.flush()

    before_result = await reconcile_case_rewards(
        db_session, service_case_id=before.id
    )
    after_result = await reconcile_case_rewards(db_session, service_case_id=after.id)

    assert before_result.complete_checkin_days == 0
    assert before_result.new_qualification_days == ()
    assert before_result.new_progress_threshold is None
    assert after_result.new_qualification_days == (3,)


@pytest.mark.asyncio
async def test_participation_falls_back_to_program_start_date_without_round(
    db_session,
) -> None:
    config = await set_reward_tier_config(
        db_session, {"enabled": True, "program_start_date": "2026-08-10"}
    )
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    # 2026-08-10 00:00 北京 == 2026-08-09 16:00 UTC
    cutoff = datetime(2026, 8, 9, 16, 0, tzinfo=timezone.utc)
    before = _case()
    before.created_at = cutoff - timedelta(seconds=1)
    at_cutoff = _case()
    at_cutoff.created_at = cutoff
    after = _case()
    after.created_at = cutoff + timedelta(seconds=1)
    db_session.add_all([before, at_cutoff, after])
    await db_session.flush()

    assert (
        await case_participates_in_current_round(
            db_session, service_case=before, config=config
        )
        is False
    )
    assert (
        await case_participates_in_current_round(
            db_session, service_case=at_cutoff, config=config
        )
        is True
    )
    assert (
        await case_participates_in_current_round(
            db_session, service_case=after, config=config
        )
        is True
    )


@pytest.mark.asyncio
async def test_reward_tool_reports_non_participation_before_round_moment(
    db_session,
) -> None:
    moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    db_session.add(CheckinRewardRound(started_on=date(2026, 8, 1), created_at=moment))
    service_case = _case()
    service_case.created_at = moment - timedelta(seconds=1)
    db_session.add(service_case)
    await db_session.flush()
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="现在发货，收件人张三，手机号13800138000，详细地址杭州市西湖区某街道88号",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_reward_shipping_tools(
            context=AgentRunContext(
                service_case_id=service_case.id,
                trigger_id=message.id,
            )
        )
    }

    payload = await tools["get_checkin_reward_status"].ainvoke({})
    refused = await tools["manage_reward_shipping"].ainvoke(
        {
            "action": "ship_now",
            "recipient_name": "张三",
            "recipient_phone": "13800138000",
            "shipping_address": "杭州市西湖区某街道88号",
        }
    )

    assert payload["participating"] is False
    assert payload["complete_checkin_days"] == 0
    assert "本轮开启后新建的服务单才参与" in payload["reply_instruction"]
    assert refused["ok"] is False
    assert refused["batch_closed"] is True
    assert refused["error"] == "本批已结束，不能自助登记发货或继续累积。"


@pytest.mark.asyncio
async def test_reopen_moves_program_start_and_keeps_it_until_next_off(
    db_session,
) -> None:
    first = await set_reward_tier_config(
        db_session, {"enabled": True, "program_start_date": "2026-08-01"}
    )
    assert first.program_start_date == date(2026, 8, 1)

    edited = await set_reward_tier_config(
        db_session,
        {
            "enabled": True,
            "tiers": [{"days": 5, "reward_text": "改后的奖励"}],
        },
    )
    assert edited.program_start_date == date(2026, 8, 1)

    await set_reward_tier_config(db_session, {"enabled": False})
    reopened = await set_reward_tier_config(db_session, {"enabled": True})

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    assert reopened.program_start_date == today


async def _rounds(db_session) -> list[CheckinRewardRound]:
    return list(
        await db_session.scalars(
            select(CheckinRewardRound).order_by(CheckinRewardRound.id)
        )
    )


@pytest.mark.asyncio
async def test_switch_off_to_on_records_one_round_and_stays_single_while_enabled(
    db_session,
) -> None:
    # autouse fixture 已经开着；清空轮次记录并先关一次，制造干净的关→开。
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    await set_reward_tier_config(db_session, {"enabled": False})

    await set_reward_tier_config(
        db_session, {"enabled": True, "program_start_date": "2026-08-01"}
    )
    rounds = await _rounds(db_session)
    assert len(rounds) == 1
    assert rounds[0].started_on == date(2026, 8, 1)
    assert rounds[0].closed_at is None

    # 保持开启再保存一次（改奖品、改档位）不应再开第二轮。
    await set_reward_tier_config(
        db_session,
        {
            "enabled": True,
            "tiers": [{"days": 5, "reward_text": "改后的奖励"}],
        },
    )
    rounds = await _rounds(db_session)
    assert len(rounds) == 1
    assert rounds[0].closed_at is None


@pytest.mark.asyncio
async def test_switch_on_to_off_closes_open_round_without_creating_new_round(
    db_session,
) -> None:
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    await set_reward_tier_config(db_session, {"enabled": False})
    await set_reward_tier_config(
        db_session, {"enabled": True, "program_start_date": "2026-08-01"}
    )
    assert [row.closed_at for row in await _rounds(db_session)] == [None]

    await set_reward_tier_config(db_session, {"enabled": False})
    rounds = await _rounds(db_session)
    assert len(rounds) == 1
    assert rounds[0].closed_at is not None


@pytest.mark.asyncio
async def test_reward_admin_groups_case_under_current_then_closed_round(
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

    payload = (await admin_client.get("/checkin-rewards")).json()
    assert payload["rewards_enabled"] is True
    assert payload["batches"][0] == {
        "key": "current",
        "kind": "current",
        "started_on": PROGRAM_START,
        "closed_at": None,
        "reward_created_on": None,
        "case_count": sum(
            1 for item in payload["cases"] if item["batch_key"] == "current"
        ),
    }
    row = next(
        item for item in payload["cases"] if item["service_case_id"] == service_case.id
    )
    assert row["batch_key"] == "current"

    await set_reward_tier_config(db_session, {"enabled": False})
    payload = (await admin_client.get("/checkin-rewards")).json()
    assert payload["rewards_enabled"] is False
    assert payload["batches"][0]["key"] == "current"
    assert payload["batches"][0]["started_on"] is None
    assert payload["batches"][0]["case_count"] == 0

    row = next(
        item for item in payload["cases"] if item["service_case_id"] == service_case.id
    )
    assert row["batch_key"].startswith("round:")
    batch = next(b for b in payload["batches"] if b["key"] == row["batch_key"])
    assert batch["kind"] == "round"
    assert batch["closed_at"] is not None
    assert batch["case_count"] >= 1
    assert payload["batches"][0]["key"] == "current"
    assert payload["batches"][1]["key"] == row["batch_key"]


@pytest.mark.asyncio
async def test_reward_admin_puts_reward_without_matching_round_into_legacy_batch(
    db_session, admin_client
) -> None:
    round_moment = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    db_session.add(
        CheckinRewardRound(started_on=date(2026, 8, 13), created_at=round_moment)
    )
    service_case = _case()
    service_case.service_plan_start_date = date(2026, 7, 1)
    # 该单建单早于所有已记录轮次，只能落到 legacy。
    service_case.created_at = round_moment - timedelta(days=30)
    db_session.add(service_case)
    await db_session.flush()
    db_session.add(
        CheckinRewardQualification(
            service_case_id=service_case.id,
            tier_days=3,
            reward_code="coffee_tube",
            reward_name="咖啡",
            quantity=1,
            qualified_on=date(2026, 7, 3),
            # 北京 2026-08-13 12:00，legacy 批次日期取自这条资格。
            created_at=datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.flush()

    payload = (await admin_client.get("/checkin-rewards")).json()
    row = next(
        item for item in payload["cases"] if item["service_case_id"] == service_case.id
    )
    assert row["batch_key"] == "legacy:2026-08-13"
    batch = next(b for b in payload["batches"] if b["key"] == row["batch_key"])
    assert batch["kind"] == "legacy"
    assert batch["started_on"] is None
    assert batch["closed_at"] is None
    assert batch["reward_created_on"] == "2026-08-13"
    assert batch["case_count"] >= 1

    assert payload["batches"][0]["key"] == "current"
    legacy_dates = [
        b["reward_created_on"] for b in payload["batches"] if b["kind"] == "legacy"
    ]
    assert legacy_dates == sorted(legacy_dates, reverse=True)

    # cases 必须按 batches 顺序成段排列，批次内不打散。
    batch_order = {b["key"]: index for index, b in enumerate(payload["batches"])}
    order_seq = [batch_order[item["batch_key"]] for item in payload["cases"]]
    assert order_seq == sorted(order_seq)


@pytest.mark.asyncio
async def test_reward_admin_attributes_by_case_creation_moment(
    db_session, admin_client
) -> None:
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    window_start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    open_moment = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    closed_round = CheckinRewardRound(
        started_on=date(2026, 6, 1),
        closed_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        created_at=window_start,
    )
    db_session.add(closed_round)
    db_session.add(
        CheckinRewardRound(started_on=date(2026, 7, 1), created_at=open_moment)
    )
    await db_session.flush()

    inside = _case()
    inside.created_at = window_start + timedelta(hours=1)
    legacy = _case()
    legacy.created_at = window_start - timedelta(hours=1)
    db_session.add_all([inside, legacy])
    await db_session.flush()
    db_session.add_all(
        [
            CheckinRewardQualification(
                service_case_id=inside.id,
                tier_days=3,
                reward_code="coffee_tube",
                reward_name="咖啡",
                quantity=1,
                qualified_on=date(2026, 6, 3),
            ),
            CheckinRewardQualification(
                service_case_id=legacy.id,
                tier_days=3,
                reward_code="coffee_tube",
                reward_name="咖啡",
                quantity=1,
                qualified_on=date(2026, 5, 31),
            ),
        ]
    )
    await db_session.flush()

    payload = (await admin_client.get("/checkin-rewards")).json()
    inside_row = next(
        item for item in payload["cases"] if item["service_case_id"] == inside.id
    )
    legacy_row = next(
        item for item in payload["cases"] if item["service_case_id"] == legacy.id
    )

    assert inside_row["batch_key"] == f"round:{closed_round.id}"
    assert inside_row["participating"] is False
    assert legacy_row["batch_key"].startswith("legacy:")
    assert legacy_row["participating"] is False
    assert payload["batches"][0]["key"] == "current"
    batch_keys = [batch["key"] for batch in payload["batches"]]
    assert batch_keys.index(f"round:{closed_round.id}") < batch_keys.index(
        legacy_row["batch_key"]
    )


async def _closed_batch_case(db_session):
    """建单早于本轮开启时刻的服务单，参与标记为 False。"""
    moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    db_session.add(CheckinRewardRound(started_on=date(2026, 8, 1), created_at=moment))
    service_case = _case()
    service_case.created_at = moment - timedelta(seconds=1)
    db_session.add(service_case)
    await db_session.flush()
    return service_case


@pytest.mark.asyncio
async def test_closed_batch_refuses_accumulate_and_keeps_active_draft(db_session) -> None:
    service_case = await _closed_batch_case(db_session)
    choice_at = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    program = CheckinRewardProgram(
        service_case_id=service_case.id,
        current_choice="pending_choice",
        choice_tier_days=3,
        choice_updated_at=choice_at,
    )
    shipment = CheckinRewardShipment(
        service_case_id=service_case.id,
        status="missing_address",
        requested_at=datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([program, shipment])
    await db_session.flush()
    qualification = CheckinRewardQualification(
        service_case_id=service_case.id,
        tier_days=3,
        reward_code="coffee_tube",
        reward_name="咖啡",
        quantity=1,
        qualified_on=date(2026, 8, 3),
        shipment_id=shipment.id,
    )
    db_session.add(qualification)
    await db_session.flush()

    tool = build_reward_shipping_tools(
        context=AgentRunContext(service_case_id=service_case.id)
    )[0]
    result = await tool.ainvoke({"action": "accumulate"})

    assert result["ok"] is False
    assert result["batch_closed"] is True
    assert result["participating"] is False
    assert "不要说继续累积或“攒着”仍可办理" in result["reply_instruction"]
    assert "也不要让用户找班主任办理更多累积" in result["reply_instruction"]
    assert "联系班主任处理这些已得奖励的寄送" in result["reply_instruction"]
    await db_session.refresh(program)
    await db_session.refresh(shipment)
    await db_session.refresh(qualification)
    assert program.current_choice == "pending_choice"
    assert program.choice_updated_at == choice_at
    assert shipment.status == "missing_address"
    assert qualification.shipment_id == shipment.id


@pytest.mark.asyncio
async def test_closed_batch_refuses_ship_now_and_creates_no_shipment(db_session) -> None:
    service_case = await _closed_batch_case(db_session)
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
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="现在发货，收件人张三，手机号13800138000，详细地址杭州市西湖区某街道88号",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()

    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=message.id,
        )
    )[0]
    result = await tool.ainvoke(
        {
            "action": "ship_now",
            "recipient_name": "张三",
            "recipient_phone": "13800138000",
            "shipping_address": "杭州市西湖区某街道88号",
        }
    )

    assert result["ok"] is False
    assert result["batch_closed"] is True
    assert result["error"] == "本批已结束，不能自助登记发货或继续累积。"
    assert "不要说继续累积或“攒着”仍可办理" in result["reply_instruction"]
    assert await db_session.scalar(
        select(CheckinRewardShipment).where(
            CheckinRewardShipment.service_case_id == service_case.id
        )
    ) is None
    await db_session.refresh(program)
    await db_session.refresh(qualification)
    assert program.current_choice == "pending_choice"
    assert program.recipient_name is None
    assert qualification.shipment_id is None


@pytest.mark.asyncio
async def test_closed_batch_get_status_still_returns_earned_rewards_with_guidance(
    db_session,
) -> None:
    service_case = await _closed_batch_case(db_session)
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
    tools = {
        tool.name: tool
        for tool in build_reward_shipping_tools(
            context=AgentRunContext(service_case_id=service_case.id)
        )
    }

    payload = await tools["manage_reward_shipping"].ainvoke({"action": "get_status"})
    read_payload = await tools["get_checkin_reward_status"].ainvoke({})

    assert payload["ok"] is True
    assert payload["participating"] is False
    assert payload["has_earned_rewards"] is True
    assert payload["earned_rewards"] == "1 管咖啡"
    guidance = payload["reply_instruction"]
    assert "不参与" in guidance
    assert "本轮开启后新建的服务单才参与" in guidance
    assert "不要记录收件人、手机号、地址" in guidance
    assert "联系班主任" in guidance
    assert "不要说继续累积仍可办理、也不要说以后还能再攒" in guidance
    assert read_payload["reply_instruction"] == guidance

    closed_batch_text = "本批已结束，自助登记已关闭，已得奖励需联系班主任"
    assert payload["current_choice_text"] == closed_batch_text
    assert payload["operational_status_text"] == closed_batch_text
    assert "待选择" not in payload["current_choice_text"]
    assert "继续累积" not in payload["current_choice_text"]
    assert "待选择" not in payload["operational_status_text"]
    assert "继续累积" not in payload["operational_status_text"]
    assert read_payload["current_choice_text"] == closed_batch_text
    assert read_payload["operational_status_text"] == closed_batch_text


@pytest.mark.asyncio
async def test_participating_case_accumulate_still_records(db_session) -> None:
    moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    await db_session.execute(delete(CheckinRewardRound))
    await db_session.flush()
    db_session.add(CheckinRewardRound(started_on=date(2026, 8, 1), created_at=moment))
    service_case = _case()
    service_case.created_at = moment + timedelta(seconds=1)
    db_session.add(service_case)
    await db_session.flush()
    program = CheckinRewardProgram(
        service_case_id=service_case.id,
        current_choice="pending_choice",
        choice_tier_days=3,
    )
    shipment = CheckinRewardShipment(
        service_case_id=service_case.id,
        status="missing_address",
        requested_at=datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([program, shipment])
    await db_session.flush()
    qualification = CheckinRewardQualification(
        service_case_id=service_case.id,
        tier_days=3,
        reward_code="coffee_tube",
        reward_name="咖啡",
        quantity=1,
        qualified_on=date(2026, 8, 3),
        shipment_id=shipment.id,
    )
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="2",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add_all([qualification, message])
    await db_session.flush()

    tool = build_reward_shipping_tools(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=message.id,
        )
    )[0]
    status_before = await tool.ainvoke({"action": "get_status"})
    assert status_before["participating"] is True
    assert status_before["current_choice_text"] == "待选择现在发货或继续累积"
    result = await tool.ainvoke({"action": "accumulate"})

    assert result["recorded"] == "accumulate"
    assert result["participating"] is True
    assert result["current_choice_text"] == "选择继续累积"
    assert result["operational_status_text"] == "已选择继续累积"
    await db_session.refresh(program)
    await db_session.refresh(shipment)
    await db_session.refresh(qualification)
    assert program.current_choice == "accumulate"
    assert program.choice_updated_at is not None
    assert shipment.status == "cancelled"
    assert qualification.shipment_id is None
