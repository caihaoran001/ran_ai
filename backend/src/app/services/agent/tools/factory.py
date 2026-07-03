from __future__ import annotations

from langchain_core.tools import BaseTool

from app.core.config import Settings
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.knowledge_tools import build_knowledge_tools
from app.services.agent.tools.reminder_tools import build_reminder_tools
from app.services.agent.tools.service_case_tools import build_service_case_tools
from app.services.agent.tools.skill_tools import build_skill_tools
from app.services.agent.tools.web_search_tools import build_web_search_tools


def build_agent_tools(
    *,
    context: AgentRunContext,
    settings: Settings,
    skill_registry: SkillRegistry,
) -> list[BaseTool]:
    tools: list[BaseTool] = []
    tools.extend(build_skill_tools(skill_registry))
    tools.extend(build_service_case_tools(context=context, settings=settings))
    tools.extend(build_reminder_tools(context=context, settings=settings))
    tools.extend(build_knowledge_tools(settings=settings))
    tools.extend(build_web_search_tools(settings=settings))
    return tools
