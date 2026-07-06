from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.core.config import Settings, get_settings
from app.db import session as db_session_module
from app.services.agent.context_builder import build_agent_memory_context
from app.services.agent.model import create_agent_chat_model
from app.services.agent.prompts import AGENT_SYSTEM_PROMPT, build_agent_user_prompt
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.state import AgentRunContext, AgentState
from app.services.agent.tools import build_agent_tools

logger = logging.getLogger("uvicorn.error")

DATA_QUERY_KEYWORDS = ("第几天", "服务进度", "打卡", "体重", "腰围", "文档", "问卷", "最近变化")
DATA_UNAVAILABLE_REPLY = "我这边暂时没能查到准确记录，先不乱报数据。稍后再帮你确认一下。"
WEB_SEARCH_CALL_LIMIT = 2
TOOL_FAILURE_CIRCUIT_LIMIT = 2


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
        include_reminder_tools: bool = True,
    ):
        self.context = context
        self.settings = settings or get_settings()
        self.skill_registry = skill_registry or SkillRegistry()
        self.tools = build_agent_tools(
            context=context,
            settings=self.settings,
            skill_registry=self.skill_registry,
            include_reminder_tools=include_reminder_tools,
        )
        self.tools_by_name = {tool.name: tool for tool in self.tools}
        base_model = chat_model or create_agent_chat_model(self.settings)
        self.model = base_model.bind_tools(self.tools) if hasattr(base_model, "bind_tools") else base_model
        self.graph = self._compile()

    async def run(self, *, user_query: str, recent_messages: list[str], query_speaker: str = "用户") -> AgentReplyResult:
        memory_context = await self._build_memory_context()
        initial_messages: list[BaseMessage] = [
            SystemMessage(content=AGENT_SYSTEM_PROMPT),
            HumanMessage(
                content=build_agent_user_prompt(
                    user_query=user_query,
                    query_speaker=query_speaker,
                    recent_messages=recent_messages,
                    skill_catalog=self.skill_registry.catalog_text(),
                    current_time_context=_current_time_context(),
                    memory_context=memory_context,
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
            "data_tool_status": {},
            "consecutive_tool_failures": {},
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

    async def _build_memory_context(self) -> str:
        try:
            async with db_session_module.SessionLocal() as session:
                memory_scopes = _agent_memory_scopes(self.context.agent_context_plan)
                return await build_agent_memory_context(
                    session,
                    service_case_id=self.context.service_case_id,
                    memory_scopes=memory_scopes,
                )
        except Exception:
            logger.exception("Failed to build agent memory context service_case_id=%s", self.context.service_case_id)
            return "暂无可用用户背景。"

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
        failure_counts = dict(state.get("consecutive_tool_failures") or {})
        for idx, call in enumerate(runnable_calls):
            name = call.get("name") or ""
            if name and failure_counts.get(name, 0) >= TOOL_FAILURE_CIRCUIT_LIMIT:
                rejected_results[idx] = self._reject_tool_call(
                    call,
                    f"TOOL_CIRCUIT_OPEN: {name} 连续失败，已停止本轮继续调用。",
                    data_tool_failed=_is_data_tool(name),
                    circuit_rejected=True,
                )
                continue
            if name == "web_search_lifestyle":
                if web_search_seen >= WEB_SEARCH_CALL_LIMIT:
                    rejected_results[idx] = self._reject_tool_call(
                        call,
                        f"TOOL_REJECTED: 每次 Agent 运行最多联网搜索 {WEB_SEARCH_CALL_LIMIT} 次。",
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
        web_search_count = int(state.get("web_search_count") or 0) + sum(
            1 for trace in traces if trace["name"] == "web_search_lifestyle" and trace.get("ok")
        )
        data_tool_status = dict(state.get("data_tool_status") or {})
        consecutive_tool_failures = dict(state.get("consecutive_tool_failures") or {})
        for trace in traces:
            name = str(trace.get("name") or "")
            if not name:
                continue
            if _is_data_tool(name):
                data_tool_status[name] = bool(trace.get("data_tool_failed"))
            if trace.get("circuit_rejected"):
                continue
            if trace.get("ok"):
                consecutive_tool_failures[name] = 0
            else:
                consecutive_tool_failures[name] = consecutive_tool_failures.get(name, 0) + 1
        data_tool_failed = any(data_tool_status.values())
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
            "data_tool_status": data_tool_status,
            "consecutive_tool_failures": consecutive_tool_failures,
            "loaded_skills": sorted(loaded),
        }

    async def _run_one_tool(self, call: dict[str, Any], state: AgentState) -> tuple[ToolMessage, dict[str, Any]]:
        name = call.get("name") or ""
        args = call.get("args") or {}
        call_id = _tool_call_id(call)
        trace: dict[str, Any] = {"name": name, "args": args, "ok": False}
        if name == "web_search_lifestyle" and int(state.get("web_search_count") or 0) >= WEB_SEARCH_CALL_LIMIT:
            content = f"TOOL_REJECTED: 每次 Agent 运行最多联网搜索 {WEB_SEARCH_CALL_LIMIT} 次。"
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

    def _reject_tool_call(
        self,
        call: dict[str, Any],
        content: str,
        *,
        data_tool_failed: bool = False,
        circuit_rejected: bool = False,
    ) -> tuple[ToolMessage, dict[str, Any]]:
        trace: dict[str, Any] = {
            "name": call.get("name") or "",
            "args": call.get("args") or {},
            "ok": False,
            "error": content,
        }
        if data_tool_failed:
            trace["data_tool_failed"] = True
        if circuit_rejected:
            trace["circuit_rejected"] = True
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
    query_speaker: str = "用户",
    recent_messages: list[str],
    analysis: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> AgentReplyResult:
    settings = settings or get_settings()
    result = await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
            agent_context_plan=_agent_context_plan(analysis),
        ),
        settings=settings,
    ).run(user_query=user_query, recent_messages=recent_messages, query_speaker=query_speaker)
    if result.data_tool_failed and _looks_like_data_query(user_query):
        raise AgentDataUnavailableError(DATA_UNAVAILABLE_REPLY)
    return result


async def generate_scheduled_reminder_reply(
    *,
    service_case_id: int,
    trigger_id: int | None,
    reminder_instruction: str,
    recent_messages: list[str],
    settings: Settings | None = None,
) -> AgentReplyResult:
    settings = settings or get_settings()
    user_query = (
        "这是一条到点执行的群内定时提醒任务。请不要追问确认，也不要创建新的提醒；"
        "直接生成现在要发到群里的最终提醒文案。文案要短、自然，优先结合当前服务进度、"
        "近期打卡、体重趋势、用户状态画像和业务知识；如果提醒目标只是普通事项，也按原意简单提醒。"
        "现在已经到了执行时间，不要复述或强调原定时间；如果提醒目标里仍残留日期/几点等时间表达，忽略时间壳，只处理要提醒的事情。\n"
        "不要在文案里自己写“@某人/@昵称”这类文字——系统会自动用企微原生 @ 提醒到对应用户，你只写提醒正文即可。\n"
        f"提醒目标：{reminder_instruction}"
    )
    return await RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case_id, trigger_id=trigger_id),
        settings=settings,
        include_reminder_tools=False,
    ).run(user_query=user_query, recent_messages=recent_messages, query_speaker="系统定时任务")


def _agent_context_plan(analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(analysis, dict):
        return None
    value = analysis.get("agent_context_plan")
    return value if isinstance(value, dict) else None


def _agent_memory_scopes(agent_context_plan: dict[str, Any] | None) -> list[str] | None:
    if not isinstance(agent_context_plan, dict) or "memory_scopes" not in agent_context_plan:
        return None
    scopes = agent_context_plan.get("memory_scopes")
    if not isinstance(scopes, list):
        return None
    return [str(item) for item in scopes if isinstance(item, str)]


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
    return name in {
        "get_service_progress",
        "get_recent_checkins",
        "get_weight_trend",
        "get_user_state_profile",
        "get_sent_checkin_links",
        "list_delivery_documents",
        "read_delivery_document",
        "list_group_files",
        "read_group_file",
    }


def _looks_like_data_query(text: str) -> bool:
    return any(keyword in (text or "") for keyword in DATA_QUERY_KEYWORDS)


def _current_time_context() -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"现在是北京时间 {now:%Y-%m-%d %H:%M}（{weekdays[now.weekday()]}）。用户说“现在、最近、最新、今年”时，按这个日期理解。"
