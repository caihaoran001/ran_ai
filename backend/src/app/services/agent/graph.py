from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.core.config import Settings, get_settings
from app.services.agent.model import create_agent_chat_model
from app.services.agent.prompts import AGENT_SYSTEM_PROMPT, build_agent_user_prompt
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.state import AgentRunContext, AgentState
from app.services.agent.tools import build_agent_tools

logger = logging.getLogger("uvicorn.error")

DATA_QUERY_KEYWORDS = ("第几天", "服务进度", "打卡", "体重", "腰围", "文档", "问卷", "最近变化")
DATA_UNAVAILABLE_REPLY = "我这边暂时没能查到准确记录，先不乱报数据。稍后再帮你确认一下。"


class AgentDataUnavailableError(RuntimeError):
    pass


@dataclass
class AgentReplyResult:
    reply: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    data_tool_failed: bool = False


class RanrenAgentGraph:
    def __init__(
        self,
        *,
        context: AgentRunContext,
        settings: Settings | None = None,
        skill_registry: SkillRegistry | None = None,
        chat_model: Any | None = None,
    ):
        self.context = context
        self.settings = settings or get_settings()
        self.skill_registry = skill_registry or SkillRegistry()
        self.tools = build_agent_tools(
            context=context,
            settings=self.settings,
            skill_registry=self.skill_registry,
        )
        self.tools_by_name = {tool.name: tool for tool in self.tools}
        base_model = chat_model or create_agent_chat_model(self.settings)
        self.model = base_model.bind_tools(self.tools) if hasattr(base_model, "bind_tools") else base_model
        self.graph = self._compile()

    async def run(self, *, user_query: str, recent_messages: list[str]) -> AgentReplyResult:
        initial_messages: list[BaseMessage] = [
            SystemMessage(content=AGENT_SYSTEM_PROMPT),
            HumanMessage(
                content=build_agent_user_prompt(
                    user_query=user_query,
                    recent_messages=recent_messages,
                    skill_catalog=self.skill_registry.catalog_text(),
                )
            ),
        ]
        state: AgentState = {
            "messages": initial_messages,
            "loaded_skills": [],
            "tool_trace": [],
            "llm_call_count": 0,
            "tool_call_count": 0,
            "web_search_count": 0,
            "data_tool_failed": False,
        }
        try:
            result = await asyncio.wait_for(
                self.graph.ainvoke(
                    state,
                    config={"recursion_limit": max(4, self.settings.agent_max_llm_calls * 2 + 2)},
                ),
                timeout=max(1.0, float(self.settings.agent_timeout_seconds or 40.0)),
            )
        except Exception:
            logger.exception("LangGraph agent run failed service_case_id=%s", self.context.service_case_id)
            raise
        reply = _last_ai_text(result.get("messages") or [])
        if not reply:
            raise RuntimeError("Agent returned empty reply")
        return AgentReplyResult(
            reply=reply,
            tool_trace=list(result.get("tool_trace") or []),
            data_tool_failed=bool(result.get("data_tool_failed")),
        )

    def _compile(self):
        builder = StateGraph(AgentState)
        builder.add_node("agent", self._agent_node)
        builder.add_node("tools", self._tools_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", self._route_after_agent, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")
        return builder.compile()

    async def _agent_node(self, state: AgentState) -> dict[str, Any]:
        llm_count = int(state.get("llm_call_count") or 0)
        if llm_count >= max(1, self.settings.agent_max_llm_calls):
            return {
                "messages": [AIMessage(content="我这边暂时没能完整处理完这条消息，先不乱判断。")],
                "llm_call_count": llm_count,
            }
        response = await self.model.ainvoke(state.get("messages") or [])
        return {"messages": [response], "llm_call_count": llm_count + 1}

    async def _tools_node(self, state: AgentState) -> dict[str, Any]:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        tool_calls = list(getattr(last, "tool_calls", None) or [])
        max_tools = max(1, int(self.settings.agent_max_tool_calls or 8))
        current_count = int(state.get("tool_call_count") or 0)
        remaining = max_tools - current_count
        if remaining <= 0:
            return {
                "messages": [
                    ToolMessage(
                        content="TOOL_LIMIT_REACHED: 已达到本轮工具调用上限。",
                        tool_call_id=_tool_call_id(tool_calls[0]) if tool_calls else "tool-limit",
                    )
                ],
                "tool_call_count": current_count,
            }

        runnable_calls = tool_calls[:remaining]
        allowed_calls: list[tuple[int, dict[str, Any]]] = []
        rejected_results: dict[int, tuple[ToolMessage, dict[str, Any]]] = {}
        web_search_seen = int(state.get("web_search_count") or 0)
        for idx, call in enumerate(runnable_calls):
            name = call.get("name") or ""
            if name == "web_search_lifestyle":
                if web_search_seen >= 1:
                    rejected_results[idx] = self._reject_tool_call(
                        call,
                        "TOOL_REJECTED: 每次 Agent 运行最多联网搜索一次。",
                    )
                    continue
                web_search_seen += 1
            allowed_calls.append((idx, call))

        results = await asyncio.gather(
            *(self._run_one_tool(call, state) for _, call in allowed_calls),
            return_exceptions=False,
        )
        ordered_results = dict(rejected_results)
        for (idx, _), result in zip(allowed_calls, results, strict=True):
            ordered_results[idx] = result
        ordered = [ordered_results[idx] for idx in range(len(runnable_calls))]
        tool_messages = [item[0] for item in ordered]
        traces = [item[1] for item in ordered]
        web_search_count = int(state.get("web_search_count") or 0) + sum(1 for trace in traces if trace["name"] == "web_search_lifestyle")
        data_tool_failed = bool(state.get("data_tool_failed")) or any(trace.get("data_tool_failed") for trace in traces)
        loaded = set(state.get("loaded_skills") or [])
        for trace in traces:
            if trace["name"] == "skill" and trace.get("ok"):
                name = (trace.get("args") or {}).get("name")
                if name:
                    loaded.add(str(name))
        return {
            "messages": tool_messages,
            "tool_trace": list(state.get("tool_trace") or []) + traces,
            "tool_call_count": current_count + len(runnable_calls),
            "web_search_count": web_search_count,
            "data_tool_failed": data_tool_failed,
            "loaded_skills": sorted(loaded),
        }

    async def _run_one_tool(self, call: dict[str, Any], state: AgentState) -> tuple[ToolMessage, dict[str, Any]]:
        name = call.get("name") or ""
        args = call.get("args") or {}
        call_id = _tool_call_id(call)
        trace: dict[str, Any] = {"name": name, "args": args, "ok": False}
        if name == "web_search_lifestyle" and int(state.get("web_search_count") or 0) >= 1:
            content = "TOOL_REJECTED: 每次 Agent 运行最多联网搜索一次。"
            trace["error"] = content
            return ToolMessage(content=content, tool_call_id=call_id), trace
        tool = self.tools_by_name.get(name)
        if tool is None:
            content = f"TOOL_NOT_FOUND: {name}"
            trace["error"] = content
            return ToolMessage(content=content, tool_call_id=call_id), trace
        try:
            result = await tool.ainvoke(args)
            trace["ok"] = not _tool_result_failed(result)
            trace["data_tool_failed"] = _is_data_tool(name) and not trace["ok"]
            content = _stringify_tool_result(result)
        except Exception as exc:
            trace["error"] = f"{type(exc).__name__}: {exc}"
            trace["data_tool_failed"] = _is_data_tool(name)
            content = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
        return ToolMessage(content=content, tool_call_id=call_id), trace

    def _reject_tool_call(self, call: dict[str, Any], content: str) -> tuple[ToolMessage, dict[str, Any]]:
        trace: dict[str, Any] = {
            "name": call.get("name") or "",
            "args": call.get("args") or {},
            "ok": False,
            "error": content,
        }
        return ToolMessage(content=content, tool_call_id=_tool_call_id(call)), trace

    def _route_after_agent(self, state: AgentState) -> str:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if getattr(last, "tool_calls", None):
            return "tools"
        return END


async def generate_agent_reply(
    *,
    service_case_id: int,
    trigger_id: int | None,
    user_query: str,
    recent_messages: list[str],
    settings: Settings | None = None,
) -> AgentReplyResult:
    settings = settings or get_settings()
    result = await RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case_id, trigger_id=trigger_id),
        settings=settings,
    ).run(user_query=user_query, recent_messages=recent_messages)
    if result.data_tool_failed and _looks_like_data_query(user_query):
        raise AgentDataUnavailableError(DATA_UNAVAILABLE_REPLY)
    return result


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            content = message.content
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                return "\n".join(str(item) for item in content).strip()
    return ""


def _tool_call_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or call.get("tool_call_id") or f"tool-{call.get('name') or 'unknown'}")


def _stringify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    return repr(result)


def _tool_result_failed(result: Any) -> bool:
    if isinstance(result, dict):
        return result.get("ok") is False
    if isinstance(result, str):
        return "ERROR" in result or "未配置" in result or "失败" in result
    return False


def _is_data_tool(name: str) -> bool:
    return name in {"get_service_progress", "get_recent_checkins", "get_weight_trend", "get_user_state_profile"}


def _looks_like_data_query(text: str) -> bool:
    return any(keyword in (text or "") for keyword in DATA_QUERY_KEYWORDS)
