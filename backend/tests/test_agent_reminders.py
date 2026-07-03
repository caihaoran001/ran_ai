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
async def test_create_group_reminder_tool_strips_schedule_time_from_instruction(db_session):
    service_case = await _case(db_session)
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }
    remind_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    result = await tools["create_group_reminder"].ainvoke(
        {"remind_at": remind_at, "content": "明天上午9点提醒我称体重"}
    )

    assert result["ok"] is True
    task = await db_session.scalar(select(AgentReminderTask).where(AgentReminderTask.id == result["task_id"]))
    assert task is not None
    assert task.content == "称体重"
    assert result["content"] == "称体重"


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
async def test_create_group_reminder_tool_dedupes_near_duplicate(db_session):
    service_case = await _case(db_session)
    inbound = await _inbound(db_session, service_case)
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id, trigger_id=inbound.id),
            settings=get_settings(),
        )
    }
    base = datetime.now(timezone.utc) + timedelta(minutes=10)
    first = await tools["create_group_reminder"].ainvoke(
        {"remind_at": base.isoformat(), "content": "提醒做饭，并给一份推荐食谱"}
    )
    # 30s 内的第二次同意图请求应被去重，返回同一 task，不新建
    second = await tools["create_group_reminder"].ainvoke(
        {"remind_at": (base + timedelta(seconds=30)).isoformat(), "content": "做饭，并给一份推荐食谱"}
    )

    assert first["ok"] is True and second["ok"] is True
    assert second.get("deduped") is True
    assert second["task_id"] == first["task_id"]
    tasks = list(
        await db_session.scalars(
            select(AgentReminderTask).where(AgentReminderTask.service_case_id == service_case.id)
        )
    )
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_reminder_service_sends_native_at_mention_to_requester(db_session, monkeypatch):
    from app.services.channels.registry import set_current_channel
    from app.services.channels.base import ChannelSendResult

    hypertext_calls = []

    class HyperChannel:
        name = "qiwe"

        async def send_text(self, chat_id, content, **kwargs):
            return ChannelSendResult(success=True, message_id="plain-id")

        async def send_hypertext(self, chat_id, content, **kwargs):
            hypertext_calls.append({"chat_id": chat_id, "content": content})
            return ChannelSendResult(success=True, message_id="hyper-id")

    set_current_channel(HyperChannel())
    try:
        service_case = await _case(db_session)
        # trigger 是带企微 meta 的用户消息（请求者 userId + 昵称）
        trigger = Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content="三分钟后提醒我做饭",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            analysis_json={"_qiwe_meta": {"sender_id": "78813006", "sender_name": "阿杰"}},
        )
        db_session.add(trigger)
        await db_session.flush()
        task = AgentReminderTask(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            trigger_message_id=trigger.id,
            remind_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            content="做饭，并给一份推荐食谱",
            status=AgentReminderTaskStatus.PENDING,
        )
        db_session.add(task)
        await db_session.flush()

        async def fake_generate_scheduled_reminder_reply(**kwargs):
            return AgentReplyResult(reply="到点做饭啦，今晚来份鸡胸炒时蔬。", tool_trace=[])

        monkeypatch.setattr(
            "app.services.reminder_service.generate_scheduled_reminder_reply",
            fake_generate_scheduled_reminder_reply,
        )

        await ReminderService(get_settings()).check_agent_reminder_tasks_once(now=datetime.now(timezone.utc))

        # 走原生 @：hypertext 首段 subtype=1 且 text 是请求者 userId
        assert len(hypertext_calls) == 1
        segments = hypertext_calls[0]["content"]
        assert segments[0] == {"subtype": 1, "text": "78813006"}
        assert "到点做饭" in segments[1]["text"]
        assert task.status == AgentReminderTaskStatus.SENT
        message = await db_session.get(Message, task.sent_message_id)
        # 落库内容带可读 @昵称 前缀，与实际发送一致
        assert message.content.startswith("@阿杰 ")
    finally:
        set_current_channel(None)


@pytest.mark.asyncio
async def test_create_group_reminder_tool_creates_recurring_task(db_session):
    service_case = await _case(db_session)
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }
    remind_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    result = await tools["create_group_reminder"].ainvoke(
        {"remind_at": remind_at, "content": "吃午饭", "recurrence": "daily"}
    )

    assert result["ok"] is True
    assert result["recurrence"] == "daily"
    task = await db_session.scalar(select(AgentReminderTask).where(AgentReminderTask.id == result["task_id"]))
    assert task is not None
    assert task.recurrence == "daily"


@pytest.mark.asyncio
async def test_create_group_reminder_tool_rejects_bad_recurrence(db_session):
    service_case = await _case(db_session)
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }
    remind_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    result = await tools["create_group_reminder"].ainvoke(
        {"remind_at": remind_at, "content": "吃午饭", "recurrence": "monthly"}
    )

    assert result["ok"] is False


@pytest.mark.asyncio
async def test_reminder_service_schedules_next_recurrence(db_session, monkeypatch):
    service_case = await _case(db_session)
    task = AgentReminderTask(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        remind_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        content="吃午饭",
        recurrence="daily",
        status=AgentReminderTaskStatus.PENDING,
    )
    db_session.add(task)
    await db_session.flush()

    async def fake_generate_scheduled_reminder_reply(**kwargs):
        return AgentReplyResult(reply="到点吃午饭啦，蛋白配蔬菜。", tool_trace=[])

    monkeypatch.setattr(
        "app.services.reminder_service.generate_scheduled_reminder_reply",
        fake_generate_scheduled_reminder_reply,
    )

    await ReminderService(get_settings()).check_agent_reminder_tasks_once(now=datetime.now(timezone.utc))

    assert task.status == AgentReminderTaskStatus.SENT
    # 发完后应新排一条 +1 天的 PENDING，content/recurrence 一致
    pending = list(
        await db_session.scalars(
            select(AgentReminderTask).where(
                AgentReminderTask.service_case_id == service_case.id,
                AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
            )
        )
    )
    assert len(pending) == 1
    nxt = pending[0]
    assert nxt.id != task.id
    assert nxt.recurrence == "daily"
    assert nxt.content == "吃午饭"
    assert nxt.remind_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_list_group_reminders_tool(db_session):
    service_case = await _case(db_session)
    for offset, content in ((10, "做饭"), (20, "喝水")):
        db_session.add(
            AgentReminderTask(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                remind_at=datetime.now(timezone.utc) + timedelta(minutes=offset),
                content=content,
                status=AgentReminderTaskStatus.PENDING,
            )
        )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }

    result = await tools["list_group_reminders"].ainvoke({})

    assert result["ok"] is True
    assert result["count"] == 2
    assert {item["content"] for item in result["pending"]} == {"做饭", "喝水"}


@pytest.mark.asyncio
async def test_cancel_group_reminder_tool_cancels_by_id(db_session):
    service_case = await _case(db_session)
    keep = AgentReminderTask(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        remind_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        content="做饭",
        status=AgentReminderTaskStatus.PENDING,
    )
    drop = AgentReminderTask(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        remind_at=datetime.now(timezone.utc) + timedelta(minutes=20),
        content="喝晚A",
        status=AgentReminderTaskStatus.PENDING,
    )
    db_session.add_all([keep, drop])
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }

    result = await tools["cancel_group_reminder"].ainvoke({"task_id": drop.id})

    assert result["ok"] is True
    assert result["count"] == 1
    await db_session.refresh(drop)
    await db_session.refresh(keep)
    assert drop.status == AgentReminderTaskStatus.CANCELLED
    assert drop.cancelled_at is not None
    assert keep.status == AgentReminderTaskStatus.PENDING


@pytest.mark.asyncio
async def test_cancel_group_reminder_tool_need_choice_when_multiple(db_session):
    service_case = await _case(db_session)
    for offset, content in ((10, "做饭"), (20, "喝水")):
        db_session.add(
            AgentReminderTask(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                remind_at=datetime.now(timezone.utc) + timedelta(minutes=offset),
                content=content,
                status=AgentReminderTaskStatus.PENDING,
            )
        )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }

    result = await tools["cancel_group_reminder"].ainvoke({})

    assert result["ok"] is True
    assert result.get("need_choice") is True
    assert len(result["pending"]) == 2
    # 未指明时不应取消任何一条
    remaining = list(
        await db_session.scalars(
            select(AgentReminderTask).where(
                AgentReminderTask.service_case_id == service_case.id,
                AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
            )
        )
    )
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_cancel_group_reminder_tool_cancel_all(db_session):
    service_case = await _case(db_session)
    for offset, content in ((10, "做饭"), (20, "喝水")):
        db_session.add(
            AgentReminderTask(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                remind_at=datetime.now(timezone.utc) + timedelta(minutes=offset),
                content=content,
                status=AgentReminderTaskStatus.PENDING,
            )
        )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_reminder_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }

    result = await tools["cancel_group_reminder"].ainvoke({"cancel_all": True})

    assert result["ok"] is True
    assert result["count"] == 2
    remaining = list(
        await db_session.scalars(
            select(AgentReminderTask).where(
                AgentReminderTask.service_case_id == service_case.id,
                AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
            )
        )
    )
    assert remaining == []


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
