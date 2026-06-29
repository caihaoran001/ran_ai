from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import AIMessage

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.agent.graph import AgentReplyResult, AgentRunContext, RanrenAgentGraph
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.tools.service_case_tools import build_service_case_tools
from app.services.agent.tools.web_search_tools import build_web_search_tools
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_ai_reply_delivery_mode
from app.services.ranren_ai.engine import RanrenAIEngine


class FakeToolCallingModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if not self.responses:
            return AIMessage(content="默认回复")
        return self.responses.pop(0)


class FakeAsyncTool:
    def __init__(self, result):
        self.calls = []
        self.result = result

    async def ainvoke(self, args):
        self.calls.append(args)
        return self.result


async def _case(db_session, *, started_at=None):
    user = User(name="Agent测试用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"AGENT-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=f"agent-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(16),
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=started_at or datetime.now(timezone.utc),
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


async def _inbound(db_session, service_case: ServiceCase, content: str = "我现在第几天？") -> Message:
    inbound = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(inbound)
    await db_session.flush()
    return inbound


@pytest.mark.asyncio
async def test_skill_registry_loads_skill_and_rejects_path_escape():
    registry = SkillRegistry()
    catalog = registry.catalog_text()
    assert "product_usage" in catalog
    assert "checkin_analysis" in catalog

    skill = registry.read_skill("checkin_analysis")
    assert "get_weight_trend" in skill

    with pytest.raises(ValueError):
        registry.read_skill_file("checkin_analysis", "../product_usage/SKILL.md")


@pytest.mark.asyncio
async def test_service_progress_tool_only_uses_bound_context(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["get_service_progress"].ainvoke({})

    assert result["ok"] is True
    assert result["status"] == "in_service"
    assert "service_day" in result


@pytest.mark.asyncio
async def test_weight_trend_tool_returns_data_insufficient(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    db_session.add(
        Checkin(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            checkin_date=date.today(),
            content="今日体重 65.2",
            parsed_json={"weight": "65.2"},
        )
    )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["get_weight_trend"].ainvoke({"days": 7})

    assert result["ok"] is True
    assert result["trend"] is None
    assert "数据不足" in result["note"]


@pytest.mark.asyncio
async def test_graph_runs_agent_tools_loop(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_service_progress",
                        "args": {},
                        "id": "call-progress",
                    }
                ],
            ),
            AIMessage(content="你现在是服务进行中，今天先稳住一个动作。"),
        ]
    )

    result = await RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=123),
        settings=settings,
        chat_model=model,
    ).run(user_query="我现在第几天了？", recent_messages=[])

    assert "服务" in result.reply
    assert result.tool_trace[0]["name"] == "get_service_progress"
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_web_search_rejects_health_queries_without_network():
    settings = get_settings()
    original_enabled = settings.web_search_enabled
    original_key = settings.tavily_api_key
    try:
        settings.web_search_enabled = True
        settings.tavily_api_key = "test-key"
        tools = {tool.name: tool for tool in build_web_search_tools(settings=settings)}

        result = await tools["web_search_lifestyle"].ainvoke({"query": "高血压减肥药怎么吃"})
    finally:
        settings.web_search_enabled = original_enabled
        settings.tavily_api_key = original_key

    assert result["ok"] is False
    assert "不能联网搜索" in result["error"]


@pytest.mark.asyncio
async def test_graph_allows_two_web_searches_and_rejects_extra(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search_lifestyle",
                        "args": {"query": "近期电影推荐"},
                        "id": "call-web-1",
                    },
                    {
                        "name": "web_search_lifestyle",
                        "args": {"query": "近期电视剧推荐"},
                        "id": "call-web-2",
                    },
                    {
                        "name": "web_search_lifestyle",
                        "args": {"query": "近期综艺推荐"},
                        "id": "call-web-3",
                    },
                ],
            ),
            AIMessage(content="最近可以先看一部电影和一部剧，挑你喜欢的类型就好。"),
        ]
    )
    graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=123),
        settings=settings,
        chat_model=model,
    )
    fake_search = FakeAsyncTool({"ok": True, "results": [{"title": "电影推荐"}]})
    graph.tools_by_name["web_search_lifestyle"] = fake_search

    result = await graph.run(user_query="最近有什么好看的电影电视剧？", recent_messages=[])

    assert len(fake_search.calls) == 2
    assert [trace["name"] for trace in result.tool_trace] == [
        "web_search_lifestyle",
        "web_search_lifestyle",
        "web_search_lifestyle",
    ]
    assert result.tool_trace[0]["ok"] is True
    assert result.tool_trace[1]["ok"] is True
    assert result.tool_trace[2]["ok"] is False
    assert "最多联网搜索 2 次" in result.tool_trace[2]["error"]


@pytest.mark.asyncio
async def test_engine_uses_agent_only_when_global_and_case_toggles_enabled(
    db_session,
    mock_message_channel,
    monkeypatch,
):
    settings = get_settings()
    original_agent_enabled = settings.ai_agent_enabled
    settings.ai_agent_enabled = True
    await set_ai_reply_delivery_mode("auto_send", db_session)
    calls: list[int] = []

    async def fake_generate_agent_reply(*, service_case_id, trigger_id, user_query, recent_messages, settings):
        calls.append(service_case_id)
        return AgentReplyResult(
            reply="agent reply",
            tool_trace=[{"name": "get_service_progress", "ok": True}],
        )

    async def fake_legacy_reply(self, *, message, service_case_id, analysis, trigger_id):
        return "legacy reply"

    monkeypatch.setattr("app.services.ranren_ai.engine.generate_agent_reply", fake_generate_agent_reply)
    monkeypatch.setattr(RanrenAIEngine, "_generate_legacy_ai_reply", fake_legacy_reply)

    try:
        service_case = await _case(db_session)
        inbound = await _inbound(db_session, service_case)
        engine = RanrenAIEngine(mock_message_channel)
        message = ChannelMessage(
            channel="qiwe",
            chat_id=service_case.group_id,
            chat_type="group",
            text="我现在第几天？",
            message_id="agent-toggle-1",
            user_id="user-1",
        )

        await engine._reply_with_ai(
            message,
            service_case_id=service_case.id,
            analysis={"requires_human_intervention": False},
            trigger_id=inbound.id,
        )
        assert calls == []

        service_case.ai_agent_enabled = True
        inbound2 = await _inbound(db_session, service_case, "我现在第几天？")
        await engine._reply_with_ai(
            message,
            service_case_id=service_case.id,
            analysis={"requires_human_intervention": False},
            trigger_id=inbound2.id,
        )
        assert calls == [service_case.id]
    finally:
        settings.ai_agent_enabled = original_agent_enabled
        await set_ai_reply_delivery_mode("manual_review", db_session)
