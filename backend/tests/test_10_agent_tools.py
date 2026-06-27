from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import AIMessage

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.agent.graph import AgentRunContext, RanrenAgentGraph
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.tools.service_case_tools import build_service_case_tools
from app.services.agent.tools.web_search_tools import build_web_search_tools


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
