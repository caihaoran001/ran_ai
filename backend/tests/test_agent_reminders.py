from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.agent_reminder_task import AgentReminderTask, AgentReminderTaskStatus
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.factory import build_agent_tools
from app.services.agent.tools.reminder_tools import build_reminder_tools
from app.services.agent.graph import AgentReplyResult, RanrenAgentGraph
from app.services.reminder_service import ReminderService


class DummyModel:
    def bind_tools(self, tools):
        self.tools = list(tools)
        return self


async def _case(db_session, *, status=ServiceCaseStatus.IN_SERVICE, started_at=None) -> ServiceCase:
    user = User(name="提醒测试用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"REMINDER-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=f"reminder-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(16),
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


async def _inbound(db_session, service_case: ServiceCase) -> Message:
    inbound = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="明天9点提醒我称体重",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(inbound)
    await db_session.flush()
    return inbound


@pytest.mark.asyncio
async def test_agent_tool_factory_includes_create_group_reminder():
    tool_names = {
        tool.name
        for tool in build_agent_tools(
            context=AgentRunContext(service_case_id=123),
            settings=get_settings(),
            skill_registry=SkillRegistry(),
        )
    }

    assert "create_group_reminder" in tool_names


@pytest.mark.asyncio
async def test_scheduled_agent_mode_excludes_create_group_reminder():
    graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=123),
        settings=get_settings(),
        skill_registry=SkillRegistry(),
        chat_model=DummyModel(),
        include_reminder_tools=False,
    )

    assert "create_group_reminder" not in graph.tools_by_name


@pytest.mark.asyncio
async def test_create_group_reminder_tool_creates_pending_task(db_session):
    service_case = await _case(db_session)
    inbound = await _inbound(db_session, service_case)
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id, trigger_id=inbound.id),
            settings=get_settings(),
        )
    }
    remind_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    result = await tools["create_group_reminder"].ainvoke({"remind_at": remind_at, "content": "提醒我称体重"})

    assert result["ok"] is True
    task = await db_session.scalar(select(AgentReminderTask).where(AgentReminderTask.id == result["task_id"]))
    assert task is not None
    assert task.service_case_id == service_case.id
    assert task.group_id == service_case.group_id
    assert task.trigger_message_id == inbound.id
    assert task.status == AgentReminderTaskStatus.PENDING
    assert task.content == "提醒我称体重"


@pytest.mark.asyncio
async def test_create_group_reminder_tool_rejects_past_time(db_session):
    service_case = await _case(db_session)
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }
    remind_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    result = await tools["create_group_reminder"].ainvoke({"remind_at": remind_at, "content": "提醒我称体重"})

    assert result["ok"] is False
    count = len(list(await db_session.scalars(select(AgentReminderTask))))
    assert count == 0


@pytest.mark.asyncio
async def test_reminder_service_sends_due_agent_generated_reminder_task(db_session, monkeypatch):
    service_case = await _case(db_session)
    task = AgentReminderTask(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        remind_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        content="该称体重了",
        status=AgentReminderTaskStatus.PENDING,
    )
    db_session.add(task)
    await db_session.flush()
    calls = []

    async def fake_generate_scheduled_reminder_reply(**kwargs):
        calls.append(kwargs)
        return AgentReplyResult(
            reply="今天先称一下体重，再看下昨晚睡眠状态。",
            tool_trace=[{"name": "get_recent_checkins", "ok": True}],
        )

    monkeypatch.setattr(
        "app.services.reminder_service.generate_scheduled_reminder_reply",
        fake_generate_scheduled_reminder_reply,
    )

    await ReminderService(get_settings()).check_agent_reminder_tasks_once(now=datetime.now(timezone.utc))

    assert calls
    assert calls[0]["service_case_id"] == service_case.id
    assert calls[0]["reminder_instruction"] == "该称体重了"
    assert task.status == AgentReminderTaskStatus.SENT
    assert task.sent_message_id is not None
    message = await db_session.get(Message, task.sent_message_id)
    assert message is not None
    assert message.purpose == MessagePurpose.REMINDER
    assert message.status == MessageStatus.SENT
    assert message.content == "今天先称一下体重，再看下昨晚睡眠状态。"
    assert message.analysis_json["agent_reminder_task_id"] == task.id
    assert message.analysis_json["reminder_instruction"] == "该称体重了"
    assert message.analysis_json["agent"]["scheduled_reminder"] is True
    assert message.analysis_json["agent"]["tool_trace"][0]["name"] == "get_recent_checkins"


@pytest.mark.asyncio
async def test_reminder_service_marks_completed_case_task_failed(db_session):
    service_case = await _case(db_session, status=ServiceCaseStatus.COMPLETED)
    task = AgentReminderTask(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        remind_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        content="该称体重了",
        status=AgentReminderTaskStatus.PENDING,
    )
    db_session.add(task)
    await db_session.flush()

    await ReminderService(get_settings()).check_agent_reminder_tasks_once(now=datetime.now(timezone.utc))

    assert task.status == AgentReminderTaskStatus.FAILED
    assert "completed" in (task.error or "")
    messages = list(
        await db_session.scalars(
            select(Message).where(
                Message.service_case_id == service_case.id,
                Message.purpose == MessagePurpose.REMINDER,
            )
        )
    )
    assert messages == []
