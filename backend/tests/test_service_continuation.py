from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.config import get_settings
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.service_lifecycle_tools import (
    build_service_lifecycle_tools,
)
from app.services.service_case_queries import (
    effective_service_day,
    service_window_ended,
)


async def _graduated_case(
    db_session, *, status: ServiceCaseStatus = ServiceCaseStatus.COMPLETED
):
    user = User(name="续服测试用户")
    db_session.add(user)
    await db_session.flush()
    ended_at = datetime.now(timezone.utc) - timedelta(days=10)
    service_case = ServiceCase(
        case_no=f"CONTINUE-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=f"continue-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(16),
        status=status,
        service_plan_start_date=date.today() - timedelta(days=40),
        started_at=datetime.now(timezone.utc) - timedelta(days=40),
        ended_at=ended_at,
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case, ended_at


async def _created_case(db_session):
    user = User(name="开始日期测试用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"START-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=f"start-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(16),
        status=ServiceCaseStatus.CREATED,
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


async def _explicit_command(
    db_session,
    service_case: ServiceCase,
    *,
    sender_type: MessageSenderType = MessageSenderType.USER,
    content: str = "@燃人AI 继续服务",
    mentioned: bool = True,
) -> Message:
    meta = {
        "sender_id": "sender-1",
        "sender_name": "测试发送者",
        "sender_type": sender_type.value,
    }
    if mentioned:
        meta["explicit_bot_query_text"] = content.replace("@燃人AI", "").strip()
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=sender_type,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": meta},
    )
    db_session.add(message)
    await db_session.flush()
    return message


def _tools(service_case: ServiceCase, source: Message):
    return {
        tool.name: tool
        for tool in build_service_lifecycle_tools(
            context=AgentRunContext(
                service_case_id=service_case.id, trigger_id=source.id
            ),
            settings=get_settings(),
        )
    }


@pytest.mark.asyncio
async def test_staff_can_start_service_without_questionnaire(db_session):
    service_case = await _created_case(db_session)
    source = await _explicit_command(
        db_session,
        service_case,
        sender_type=MessageSenderType.STAFF,
        content="@燃人AI 服务从今天开始",
    )
    start_date = datetime.now(ZoneInfo(get_settings().reminder_timezone)).date()

    result = await _tools(service_case, source)["set_service_start_date"].ainvoke(
        {"start_date": start_date.isoformat()}
    )

    await db_session.refresh(service_case)
    assert result["ok"] is True
    assert result["activated"] is True
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.service_plan_start_date == start_date
    assert (
        service_case.started_at.astimezone(
            ZoneInfo(get_settings().reminder_timezone)
        ).date()
        == start_date
    )
    assert (
        service_case.service_lifecycle_events[0]["action"] == "set_service_start_date"
    )
    assert service_case.service_lifecycle_events[0]["old_start_date"] is None
    assert (
        service_case.service_lifecycle_events[0]["new_start_date"]
        == start_date.isoformat()
    )


@pytest.mark.asyncio
async def test_staff_can_schedule_future_start_without_questionnaire(db_session):
    service_case = await _created_case(db_session)
    source = await _explicit_command(
        db_session,
        service_case,
        sender_type=MessageSenderType.STAFF,
        content="@燃人AI 服务从下周一开始",
    )
    start_date = datetime.now(
        ZoneInfo(get_settings().reminder_timezone)
    ).date() + timedelta(days=7)

    result = await _tools(service_case, source)["set_service_start_date"].ainvoke(
        {"start_date": start_date.isoformat()}
    )

    await db_session.refresh(service_case)
    assert result["ok"] is True
    assert result["activated"] is False
    assert result["service_day"] is None
    assert service_case.status == ServiceCaseStatus.CREATED
    assert service_case.started_at is None
    assert service_case.service_plan_start_date == start_date


@pytest.mark.asyncio
async def test_staff_start_date_overrides_questionnaire_date(db_session):
    service_case = await _created_case(db_session)
    old_date = datetime.now(
        ZoneInfo(get_settings().reminder_timezone)
    ).date() + timedelta(days=5)
    new_date = old_date + timedelta(days=2)
    service_case.status = ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
    service_case.service_plan_start_date = old_date
    await db_session.flush()
    source = await _explicit_command(
        db_session,
        service_case,
        sender_type=MessageSenderType.STAFF,
        content="@燃人AI 服务开始日期改成下周日",
    )

    result = await _tools(service_case, source)["set_service_start_date"].ainvoke(
        {"start_date": new_date.isoformat()}
    )

    await db_session.refresh(service_case)
    assert result["ok"] is True
    assert result["changed"] is True
    assert service_case.status == ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
    assert service_case.service_plan_start_date == new_date
    assert (
        service_case.service_lifecycle_events[-1]["old_start_date"]
        == old_date.isoformat()
    )
    assert (
        service_case.service_lifecycle_events[-1]["new_start_date"]
        == new_date.isoformat()
    )


@pytest.mark.asyncio
async def test_only_explicit_staff_mention_can_set_service_start_date(db_session):
    service_case = await _created_case(db_session)
    user_source = await _explicit_command(
        db_session,
        service_case,
        sender_type=MessageSenderType.USER,
        content="@燃人AI 服务从今天开始",
    )
    today = datetime.now(ZoneInfo(get_settings().reminder_timezone)).date().isoformat()

    denied_user = await _tools(service_case, user_source)[
        "set_service_start_date"
    ].ainvoke({"start_date": today})
    staff_without_mention = await _explicit_command(
        db_session,
        service_case,
        sender_type=MessageSenderType.STAFF,
        content="服务从今天开始",
        mentioned=False,
    )
    denied_no_mention = await _tools(service_case, staff_without_mention)[
        "set_service_start_date"
    ].ainvoke({"start_date": today})

    await db_session.refresh(service_case)
    assert denied_user["ok"] is False
    assert "班主任或运营人员" in denied_user["error"]
    assert denied_no_mention["ok"] is False
    assert "@" in denied_no_mention["error"]
    assert service_case.service_plan_start_date is None


@pytest.mark.asyncio
async def test_continue_service_keeps_original_day_and_prevents_phase_reend(db_session):
    service_case, original_ended_at = await _graduated_case(db_session)
    source = await _explicit_command(db_session, service_case)
    tools = _tools(service_case, source)

    result = await tools["continue_service"].ainvoke({})

    await db_session.refresh(service_case)
    tz = ZoneInfo(get_settings().reminder_timezone)
    now = datetime.now(tz)
    assert result["ok"] is True
    assert result["continued"] is True
    assert result["service_day"] == effective_service_day(service_case, now, tz)
    assert result["service_day"] >= 41
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.service_continued_at is not None
    assert service_case.ended_at == original_ended_at
    assert service_window_ended(service_case, now, tz) is False
    assert service_case.service_lifecycle_events == [
        {
            "action": "continue_service",
            "occurred_at": service_case.service_lifecycle_events[0]["occurred_at"],
            "source_message_id": source.id,
            "sender_type": "user",
            "sender_id": "sender-1",
            "sender_name": "测试发送者",
        }
    ]

    duplicate = await tools["continue_service"].ainvoke({})
    await db_session.refresh(service_case)
    assert duplicate["ok"] is True
    assert duplicate["already_in_service"] is True
    assert len(service_case.service_lifecycle_events) == 1


@pytest.mark.asyncio
async def test_continue_service_requires_explicit_bot_mention(db_session):
    service_case, _ = await _graduated_case(db_session)
    source = await _explicit_command(db_session, service_case, mentioned=False)

    result = await _tools(service_case, source)["continue_service"].ainvoke({})

    await db_session.refresh(service_case)
    assert result["ok"] is False
    assert "@" in result["error"]
    assert service_case.status == ServiceCaseStatus.COMPLETED
    assert service_case.service_continued_at is None


@pytest.mark.asyncio
async def test_continue_service_rejects_refunded_case(db_session):
    service_case, _ = await _graduated_case(
        db_session, status=ServiceCaseStatus.REFUNDED
    )
    source = await _explicit_command(db_session, service_case)

    result = await _tools(service_case, source)["continue_service"].ainvoke({})

    assert result["ok"] is False
    assert "已退款" in result["error"]


@pytest.mark.asyncio
async def test_continue_service_handles_day29_before_phase_end_scheduler_runs(
    db_session,
):
    service_case, _ = await _graduated_case(
        db_session, status=ServiceCaseStatus.IN_SERVICE
    )
    service_case.ended_at = None
    await db_session.flush()
    source = await _explicit_command(db_session, service_case)

    result = await _tools(service_case, source)["continue_service"].ainvoke({})

    await db_session.refresh(service_case)
    assert result["ok"] is True
    assert result["continued"] is True
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.service_continued_at is not None
    assert service_case.ended_at is not None


@pytest.mark.asyncio
async def test_only_staff_can_end_continued_service(db_session):
    service_case, _ = await _graduated_case(db_session)
    user_source = await _explicit_command(db_session, service_case)
    continued = await _tools(service_case, user_source)["continue_service"].ainvoke({})
    assert continued["ok"] is True

    user_end = await _explicit_command(
        db_session, service_case, content="@燃人AI 结束服务"
    )
    denied = await _tools(service_case, user_end)["end_continued_service"].ainvoke({})
    assert denied["ok"] is False
    assert "班主任或运营人员" in denied["error"]

    staff_end = await _explicit_command(
        db_session,
        service_case,
        sender_type=MessageSenderType.STAFF,
        content="@燃人AI 结束服务",
    )
    ended = await _tools(service_case, staff_end)["end_continued_service"].ainvoke({})

    await db_session.refresh(service_case)
    assert ended["ok"] is True
    assert ended["ended"] is True
    assert service_case.status == ServiceCaseStatus.COMPLETED
    assert service_case.service_continued_at is not None
    assert [event["action"] for event in service_case.service_lifecycle_events] == [
        "continue_service",
        "end_continued_service",
    ]
