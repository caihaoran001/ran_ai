from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db import session as db_session_module
from app.models.service_case import ServiceCase
from app.services.agent.checkpoint import (
    ainvoke_resumable,
    checkpoint_config,
    get_agent_checkpointer,
)
from app.services.agent_observability import record_model_usage, record_tool_calls
from app.services.agent.context_builder import build_agent_memory_context
from app.services.agent.model import create_agent_chat_model
from app.services.agent.prompts import build_agent_user_prompt, get_agent_system_prompt
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.state import AgentRunContext, AgentState
from app.services.agent.tools import build_agent_tools
from app.services.channels.registry import get_current_channel
from app.services.content_policy import get_qiwe_group_nickname, get_qiwe_nickname
from app.services.knowledge_index import retrieve_indexed_knowledge_result
from app.services.execution_commitment import agent_execution_context

logger = logging.getLogger("uvicorn.error")

DATA_UNAVAILABLE_REPLY = "我这边暂时没能查到准确记录，先不乱报数据。稍后再帮你确认一下。"
WEB_SEARCH_CALL_LIMIT = 2
TOOL_FAILURE_CIRCUIT_LIMIT = 2
# 纯文本工具（knowledge/skill）的失败以显式错误前缀判定，避免正文含“失败/错误”被误判为工具失败而误触熔断。
_STR_TOOL_ERROR_PREFIXES = (
    "SKILL_LOAD_ERROR",
    "SKILL_FILE_ERROR",
    "TOOL_ERROR",
    "TOOL_NOT_FOUND",
)


class AgentDataUnavailableError(RuntimeError):
    pass


_SHARED_SKILL_REGISTRY: SkillRegistry | None = None


def _shared_skill_registry() -> SkillRegistry:
    global _SHARED_SKILL_REGISTRY
    if _SHARED_SKILL_REGISTRY is None:
        hidden: set[str] = set()
        if not get_settings().final_review_enabled:
            hidden.add("final_review")
        _SHARED_SKILL_REGISTRY = SkillRegistry(hidden_skills=frozenset(hidden))
    return _SHARED_SKILL_REGISTRY


@dataclass
class AgentReplyResult:
    reply: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    data_tool_failed: bool = False
    # 本轮参考问答召回条数。None = 跳过检索（意图识别判定不需要）或检索异常，0 = 查了但库里没有匹配。
    # 纯观测字段，不参与任何回复判断。
    knowledge_hit_count: int | None = None
    knowledge_hits: list[dict[str, Any]] = field(default_factory=list)
    # 改写查询零命中时可回退原问题；保留实际生效查询，避免观测把回退命中错记到改写词上。
    knowledge_query_used: str | None = None
    knowledge_fallback_used: bool = False
    knowledge_primary_hit_count: int | None = None


@dataclass(frozen=True)
class _AgentGraphExecutionContext:
    """Per-invocation bindings consumed by the process-shared compiled graph."""

    handler: "RanrenAgentGraph"


_AGENT_GRAPH_CACHE: list[tuple[BaseCheckpointSaver | None, Any]] = []

# 模型用量账本里的场景标签：区分同一个 ReAct Agent 被哪种任务驱动，便于按用途看成本。
# 未登记的运行类型一律记为普通回复。
_USAGE_SCENARIOS = {
    "scheduled_reminder": "agent_reminder",
    "churn_recall": "churn_recall",
}


class RanrenAgentGraph:
    """One run's dependencies backed by a process-shared compiled topology."""

    def __init__(
        self,
        *,
        context: AgentRunContext,
        settings: Settings | None = None,
        skill_registry: SkillRegistry | None = None,
        chat_model: Any | None = None,
        include_reminder_tools: bool = True,
        checkpointer: BaseCheckpointSaver | None = None,
    ):
        self.context = context
        self.settings = settings or get_settings()
        self.checkpointer = checkpointer
        # 复用进程级共享 registry，使 skill 目录扫描的缓存能跨请求命中（skill 是静态文件，改动靠 docker restart 失效）。
        self.skill_registry = skill_registry or _shared_skill_registry()
        self.tools = build_agent_tools(
            context=context,
            settings=self.settings,
            skill_registry=self.skill_registry,
            include_reminder_tools=include_reminder_tools,
            include_followup_preference_tools=include_reminder_tools,
            include_service_lifecycle_tools=include_reminder_tools,
        )
        self.tools_by_name = {tool.name: tool for tool in self.tools}
        base_model = chat_model or create_agent_chat_model(self.settings)
        self.model = base_model.bind_tools(self.tools) if hasattr(base_model, "bind_tools") else base_model
        # 工具预算与 Agent 整体预算独立：调大整轮等待时间不能把一个慢工具也放大到几十秒。
        self._tool_timeout_seconds = max(
            1.0,
            float(self.settings.agent_tool_timeout_seconds or 15.0),
        )
        self.graph = _get_agent_compiled_graph(checkpointer)

    async def _resolve_bot_nickname(self) -> str:
        """取机器人在本群的昵称，注入提示词身份用。

        群昵称优先、微信昵称兜底：
        1) 机器人在本群的群昵称（memberList.name，DB 缓存）——正是 @ 显示、正文里出现的那个名；
           注意企微群成员表通常不含机器人账号自身，故此项对机器人常为空。
        2) 微信昵称（DB 缓存 qiwe_nickname_map）——机器人自身昵称一般已缓存在此，零网络、可靠。
        3) 账号级微信昵称（get_self_nickname，网络）——DB 都没有时的最后兜底。
        渠道不支持或全部取不到时返回空串（不注入身份块）。
        """
        ctx = self.context
        if ctx.bot_user_id:
            try:
                async with db_session_module.SessionLocal() as session:
                    if ctx.group_id:
                        group_nickname = await get_qiwe_group_nickname(session, ctx.group_id, ctx.bot_user_id)
                        if group_nickname:
                            return group_nickname
                    contact_nickname = await get_qiwe_nickname(session, ctx.bot_user_id)
                    if contact_nickname:
                        return contact_nickname
            except Exception:
                logger.exception(
                    "Failed to resolve bot nickname from DB cache service_case_id=%s", ctx.service_case_id
                )
        channel = get_current_channel()
        if channel is not None and hasattr(channel, "get_self_nickname"):
            try:
                return await channel.get_self_nickname() or ""
            except Exception:
                logger.exception("Failed to resolve bot self nickname service_case_id=%s", ctx.service_case_id)
        return ""

    async def run(
        self,
        *,
        user_query: str,
        knowledge_query: str | None = None,
        recent_messages: list[str],
        query_speaker: str = "用户",
        run_config: dict[str, Any] | None = None,
    ) -> AgentReplyResult:
        language = await self._resolve_language()
        memory_context = await self._build_memory_context(language=language)
        effective_knowledge_query = (
            user_query if knowledge_query is None else knowledge_query.strip()
        )
        (
            knowledge_context,
            knowledge_hit_count,
            knowledge_hits,
            knowledge_query_used,
            knowledge_fallback_used,
            knowledge_primary_hit_count,
        ) = await self._build_knowledge_context(
            effective_knowledge_query,
            fallback_query=(
                user_query
                if knowledge_query is not None
                and knowledge_query.strip()
                and knowledge_query.strip() != user_query.strip()
                else None
            ),
            language=language,
        )
        bot_nickname = await self._resolve_bot_nickname()
        service_day_context, final_review_followup = await self._case_prompt_extras(
            language=language
        )
        initial_messages: list[BaseMessage] = [
            SystemMessage(content=get_agent_system_prompt(language)),
            HumanMessage(
                content=build_agent_user_prompt(
                    user_query=user_query,
                    query_speaker=query_speaker,
                    recent_messages=recent_messages,
                    skill_catalog=self.skill_registry.catalog_text(),
                    current_time_context=self._current_time_context()
                    + service_day_context,
                    memory_context=memory_context,
                    knowledge_context=knowledge_context,
                    bot_nickname=bot_nickname,
                    language=language,
                    final_review_followup=final_review_followup,
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
            invocation = (
                ainvoke_resumable(
                    self.graph,
                    state,
                    config=run_config,
                    checkpointer=self.checkpointer,
                    delete_thread_on_success=False,
                    context=_AgentGraphExecutionContext(handler=self),
                )
                if run_config is not None
                else self.graph.ainvoke(
                    state,
                    config={"recursion_limit": max(4, self.settings.agent_max_llm_calls * 2 + 2)},
                    context=_AgentGraphExecutionContext(handler=self),
                )
            )
            result = await asyncio.wait_for(
                invocation,
                timeout=max(1.0, float(self.settings.agent_timeout_seconds or 90.0)),
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
            knowledge_hit_count=knowledge_hit_count,
            knowledge_hits=knowledge_hits,
            knowledge_query_used=knowledge_query_used,
            knowledge_fallback_used=knowledge_fallback_used,
            knowledge_primary_hit_count=knowledge_primary_hit_count,
        )

    def _current_time_context(self) -> str:
        """Runtime clock hook; isolated evaluation overrides it with a fixed clock."""
        return _current_time_context()

    async def _case_prompt_extras(self, *, language: str) -> tuple[str, str]:
        """返回（服务第几天那一句，结营复盘待问段）。一次查库出两段。

        服务天数常驻而不留给工具去查：客户说"我才刚开始""都快结束了"，或者第 3 天
        报了掉 5 斤（该警惕），几乎每轮都用得上。一行字约 25 个 token，换成调工具
        是模型多一轮完整往返、上下文全量重发，差两个数量级，而且它可能压根不调。

        复盘待问段只在普通回复轮次注入：复盘节点自己那轮走的是另一条生成入口。
        任何一步取不到就当没有，绝不因为这两段拼不出来而让整轮回复失败。
        """
        if not self.context.service_case_id:
            return "", ""
        is_en = (language or "").strip().lower() == "en"
        try:
            from app.services.final_review_runner import followup_prompt_block
            from app.services.service_case_activation import (
                effective_service_day,
                service_continuation_active,
            )

            async with db_session_module.SessionLocal() as session:
                case_row = await session.get(ServiceCase, self.context.service_case_id)
                if case_row is None:
                    return "", ""
                tz = ZoneInfo(self.settings.reminder_timezone)
                day = effective_service_day(case_row, datetime.now(tz), tz)
                day_text = ""
                if day >= 1:
                    continued = service_continuation_active(case_row)
                    if is_en:
                        day_text = (
                            f" This customer is on day {day} of the service"
                            + (" (continued service)." if continued else " (28 days in total).")
                        )
                    else:
                        day_text = (
                            f"今天是这位客户服务的第 {day} 天"
                            + ("（续服中）。" if continued else "（共 28 天）。")
                        )
                followup = await followup_prompt_block(
                    session,
                    settings=self.settings,
                    service_case=case_row,
                    language=language,
                )
                return day_text, followup
        except Exception:
            logger.exception(
                "Failed to build case prompt extras service_case_id=%s",
                self.context.service_case_id,
            )
            return "", ""

    async def _resolve_language(self) -> str:
        language = self.context.language or "zh"
        if language == "zh" and self.context.service_case_id:
            try:
                async with db_session_module.SessionLocal() as session:
                    case_row = await session.get(ServiceCase, self.context.service_case_id)
                    if case_row and getattr(case_row, "language", None):
                        language = case_row.language
            except Exception:
                pass
        return language

    async def _build_memory_context(self, *, language: str = "zh") -> str:
        is_en = (language or "").strip().lower() == "en"
        try:
            async with db_session_module.SessionLocal() as session:
                memory_scopes = _agent_memory_scopes(self.context.agent_context_plan)
                memory = await build_agent_memory_context(
                    session,
                    service_case_id=self.context.service_case_id,
                    memory_scopes=memory_scopes,
                )
                commitment = await session.scalar(
                    select(ServiceCase.execution_commitment).where(
                        ServiceCase.id == self.context.service_case_id
                    )
                )
                label = "[Execution Commitment]" if is_en else "【执行约定】"
                return f"{memory}\n\n{label}\n{agent_execution_context(commitment)}"
        except Exception:
            logger.exception("Failed to build agent memory context service_case_id=%s", self.context.service_case_id)
            return "No user background available." if is_en else "暂无可用用户背景。"

    async def _build_knowledge_context(
        self,
        query: str,
        *,
        fallback_query: str | None = None,
        language: str = "zh",
    ) -> tuple[str, int | None, list[dict[str, Any]], str | None, bool, int | None]:
        """每轮按意图识别改写后的问题预检索知识库(FAQ/markdown)注入提示词。

        排除 source_type='case'——优秀案例只走 search_success_case 情绪触发工具,
        不在普通问答里冒出来。改写缺失时调用方已回退原问题；检索失败/无命中一律给占位串,
        绝不因知识检索拖垮回复。

        改写查询零命中且原问题不同才回退原问题，防止改写删掉产品、症状或限制条件后
        把原本能找到的答案变成零命中；有命中时不合并两侧结果，避免扩大噪声。

        返回 (注入文本, 最终召回条数, 命中证据, 实际查询, 是否回退, 首查条数)。条数和证据只供观测:
        None 表示没查(空查询=意图识别判定不需要检索)
        或检索抛异常, 0 表示查了但库里没有匹配——这两种是不同的病, 不能混成一个数。
        """
        # 命中的 FAQ 正文本身是中文语料，无法按语言切换；占位串是我们自己写的，
        # 英文服务单必须给英文，否则英文提示词里会凭空冒出一行中文。
        empty_hint = (
            "No relevant reference Q&A."
            if (language or "").strip().lower() == "en"
            else "暂无相关参考问答。"
        )
        clean = (query or "").strip()
        if not clean:
            return empty_hint, None, [], None, False, None
        try:
            async with db_session_module.SessionLocal() as session:
                result = await retrieve_indexed_knowledge_result(
                    session,
                    clean,
                    settings=self.settings,
                    exclude_source_types={"case"},
                )
                primary_hit_count = len(result.hits)
                fallback_clean = (fallback_query or "").strip()
                fallback_used = False
                if (
                    primary_hit_count == 0
                    and fallback_clean
                    and fallback_clean != clean
                ):
                    fallback_result = await retrieve_indexed_knowledge_result(
                        session,
                        fallback_clean,
                        settings=self.settings,
                        exclude_source_types={"case"},
                    )
                    if fallback_result.hits:
                        result = fallback_result
                        fallback_used = True
                query_used = fallback_clean if fallback_used else clean
            return result.context or empty_hint, len(result.hits), [
                hit.observation() for hit in result.hits
            ], query_used, fallback_used, primary_hit_count
        except Exception:
            logger.exception(
                "Failed to build agent knowledge context service_case_id=%s", self.context.service_case_id
            )
            return empty_hint, None, [], clean, False, None

    async def _agent_node(self, state: AgentState) -> dict[str, Any]:
        llm_count = int(state.get("llm_call_count") or 0)
        max_llm_calls = max(1, self.settings.agent_max_llm_calls)
        if llm_count >= max_llm_calls:
            return {
                "messages": [AIMessage(content="我这边暂时没能完整处理完这条消息，先不乱判断。")],
                "llm_call_count": llm_count,
            }
        scenario = _USAGE_SCENARIOS.get(self.context.run_kind, "reply_agent")
        request_messages = state.get("messages") or []
        response: Any = None
        calls_used = 0
        while calls_used < 2 and llm_count + calls_used < max_llm_calls:
            started_at = time.monotonic()
            try:
                response = await self.model.ainvoke(request_messages)
            except Exception as exc:
                await record_model_usage(
                    agent_run_id=self.context.agent_run_id,
                    scenario=scenario,
                    service_case_id=self.context.service_case_id,
                    message_id=self.context.trigger_id,
                    model=self.settings.ai_model,
                    elapsed_ms=int((time.monotonic() - started_at) * 1000),
                    success=False,
                    error_type=type(exc).__name__,
                    request_messages=request_messages,
                    settings=self.settings,
                )
                raise
            calls_used += 1
            empty_final = not getattr(response, "tool_calls", None) and not _last_ai_text(
                [response]
            )
            await record_model_usage(
                agent_run_id=self.context.agent_run_id,
                scenario=scenario,
                service_case_id=self.context.service_case_id,
                message_id=self.context.trigger_id,
                model=self.settings.ai_model,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
                response=response,
                success=not empty_final,
                error_type="EmptyAgentReply" if empty_final else None,
                request_messages=request_messages,
                settings=self.settings,
            )
            if not empty_final:
                break
            if calls_used < 2 and llm_count + calls_used < max_llm_calls:
                logger.warning(
                    "Agent model returned empty final content; retrying once service_case_id=%s",
                    self.context.service_case_id,
                )
        return {"messages": [response], "llm_call_count": llm_count + calls_used}

    async def _tools_node(self, state: AgentState) -> dict[str, Any]:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        tool_calls = list(getattr(last, "tool_calls", None) or [])
        max_tools = max(1, int(self.settings.agent_max_tool_calls or 8))
        current_count = int(state.get("tool_call_count") or 0)
        remaining = max(0, max_tools - current_count)

        # 预算内的调用参与熔断/限流/执行；预算外(含 remaining==0)的调用一律拒绝，
        # 但每个 tool_call 都必须补一条 ToolMessage —— 否则 AIMessage 里有 tool_call 缺响应，下一轮 LLM 会 400。
        within_budget = tool_calls[:remaining]
        over_budget = tool_calls[remaining:]
        allowed_calls: list[tuple[int, dict[str, Any]]] = []
        prepared: dict[int, tuple[ToolMessage, dict[str, Any]]] = {}
        web_search_seen = int(state.get("web_search_count") or 0)
        failure_counts = dict(state.get("consecutive_tool_failures") or {})
        for idx, call in enumerate(within_budget):
            name = call.get("name") or ""
            if name and failure_counts.get(name, 0) >= TOOL_FAILURE_CIRCUIT_LIMIT:
                prepared[idx] = self._reject_tool_call(
                    call,
                    f"TOOL_CIRCUIT_OPEN: {name} 连续失败，已停止本轮继续调用。",
                    data_tool_failed=_is_data_tool(name),
                    circuit_rejected=True,
                )
                continue
            if name == "web_search_lifestyle":
                if web_search_seen >= WEB_SEARCH_CALL_LIMIT:
                    prepared[idx] = self._reject_tool_call(
                        call,
                        f"TOOL_REJECTED: 每次 Agent 运行最多联网搜索 {WEB_SEARCH_CALL_LIMIT} 次。",
                    )
                    continue
                web_search_seen += 1
            allowed_calls.append((idx, call))
        for offset, call in enumerate(over_budget):
            prepared[remaining + offset] = self._reject_tool_call(
                call, "TOOL_LIMIT_REACHED: 已达到本轮工具调用上限。"
            )

        results = await asyncio.gather(
            *(self._run_one_tool(call) for _, call in allowed_calls),
            return_exceptions=True,
        )
        for (idx, call), result in zip(allowed_calls, results, strict=True):
            if isinstance(result, BaseException):
                prepared[idx] = self._reject_tool_call(
                    call,
                    f"TOOL_ERROR: {type(result).__name__}: {result}",
                    data_tool_failed=_is_data_tool(call.get("name") or ""),
                )
            else:
                prepared[idx] = result
        ordered = [prepared[idx] for idx in range(len(tool_calls))]
        tool_messages = [item[0] for item in ordered]
        traces = [item[1] for item in ordered]
        await record_tool_calls(
            agent_run_id=self.context.agent_run_id,
            traces=traces,
        )
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
            "tool_call_count": current_count + len(within_budget),
            "web_search_count": web_search_count,
            "data_tool_failed": data_tool_failed,
            "data_tool_status": data_tool_status,
            "consecutive_tool_failures": consecutive_tool_failures,
            "loaded_skills": sorted(loaded),
        }

    async def _run_one_tool(self, call: dict[str, Any]) -> tuple[ToolMessage, dict[str, Any]]:
        name = call.get("name") or ""
        args = call.get("args") or {}
        call_id = _tool_call_id(call)
        trace: dict[str, Any] = {
            "name": name,
            "args": args,
            "ok": False,
            "tool_call_id": call_id,
        }
        tool = self.tools_by_name.get(name)
        if tool is None:
            content = f"TOOL_NOT_FOUND: {name}"
            trace["error"] = content
            trace["elapsed_ms"] = 0
            self._log_tool_result(name, args, trace, elapsed_ms=0.0)
            return ToolMessage(content=content, tool_call_id=call_id), trace
        start = time.monotonic()
        try:
            # 单个工具套超时，别让某个慢查询/慢外部请求独占整轮的全局预算（graph 层 wait_for）。
            result = await asyncio.wait_for(tool.ainvoke(args), timeout=self._tool_timeout_seconds)
            trace["ok"] = not _tool_result_failed(result)
            trace["data_tool_failed"] = _is_data_tool(name) and not trace["ok"]
            if not trace["ok"]:
                trace["error"] = "TOOL_RETURNED_FAILURE"
            content = _stringify_tool_result(result)
        except asyncio.TimeoutError:
            trace["error"] = f"timeout>{self._tool_timeout_seconds}s"
            trace["data_tool_failed"] = _is_data_tool(name)
            content = f"TOOL_ERROR: TimeoutError: 工具 {name} 执行超过 {self._tool_timeout_seconds}s。"
        except Exception as exc:
            trace["error"] = f"{type(exc).__name__}: {exc}"
            trace["data_tool_failed"] = _is_data_tool(name)
            content = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
        elapsed_ms = (time.monotonic() - start) * 1000
        trace["elapsed_ms"] = int(elapsed_ms)
        self._log_tool_result(name, args, trace, elapsed_ms=elapsed_ms)
        return ToolMessage(content=content, tool_call_id=call_id), trace

    def _log_tool_result(
        self, name: str, args: dict[str, Any], trace: dict[str, Any], *, elapsed_ms: float
    ) -> None:
        """集中记录每次工具调用（case_id/渠道/工具名/参数键/耗时/状态），线上可追溯“为什么没给数据”。"""
        status = "ok" if trace.get("ok") else "fail"
        log = logger.info if trace.get("ok") else logger.warning
        log(
            "AgentTool case_id=%s channel=%s tool=%s arg_keys=%s status=%s elapsed_ms=%.1f error=%s",
            self.context.service_case_id,
            self.context.channel,
            name,
            sorted((args or {}).keys()),
            status,
            elapsed_ms,
            trace.get("error"),
        )

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
            "tool_call_id": _tool_call_id(call),
            "elapsed_ms": 0,
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


def _agent_node_dispatch(
    method_name: str,
):
    async def dispatch(
        state: AgentState,
        runtime: Runtime[_AgentGraphExecutionContext],
    ) -> dict[str, Any]:
        method = getattr(runtime.context.handler, method_name)
        return await method(state)

    dispatch.__name__ = f"{method_name}_dispatch"
    return dispatch


def _agent_route_dispatch(
    method_name: str,
):
    def dispatch(
        state: AgentState,
        runtime: Runtime[_AgentGraphExecutionContext],
    ) -> str:
        method = getattr(runtime.context.handler, method_name)
        return method(state)

    dispatch.__name__ = f"{method_name}_dispatch"
    return dispatch


def _compile_agent_graph(checkpointer: BaseCheckpointSaver | None):
    builder = StateGraph(
        AgentState,
        context_schema=_AgentGraphExecutionContext,
    )
    builder.add_node("agent", _agent_node_dispatch("_agent_node"))
    builder.add_node("tools", _agent_node_dispatch("_tools_node"))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        _agent_route_dispatch("_route_after_agent"),
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)


def _get_agent_compiled_graph(checkpointer: BaseCheckpointSaver | None):
    for cached_checkpointer, graph in _AGENT_GRAPH_CACHE:
        if cached_checkpointer is checkpointer:
            return graph
    graph = _compile_agent_graph(checkpointer)
    _AGENT_GRAPH_CACHE.append((checkpointer, graph))
    return graph


def start_agent_graph_runtime(
    checkpointer: BaseCheckpointSaver | None = None,
) -> None:
    """Compile one shared ReAct topology variant during process startup."""
    _get_agent_compiled_graph(checkpointer)
    logger.info(
        "Shared LangGraph ReAct topology compiled checkpointed=%s",
        checkpointer is not None,
    )


def stop_agent_graph_runtime() -> None:
    _AGENT_GRAPH_CACHE.clear()


async def generate_agent_reply(
    *,
    service_case_id: int,
    trigger_id: int | None,
    user_query: str,
    knowledge_query: str | None = None,
    query_speaker: str = "用户",
    recent_messages: list[str],
    analysis: dict[str, Any] | None = None,
    channel: str | None = None,
    group_id: str | None = None,
    bot_user_id: str | None = None,
    settings: Settings | None = None,
    checkpoint_thread_id: str | None = None,
    agent_run_id: int | None = None,
    language: str = "zh",
) -> AgentReplyResult:
    settings = settings or get_settings()
    checkpointer = get_agent_checkpointer() if checkpoint_thread_id else None
    run_config = (
        checkpoint_config(
            checkpoint_thread_id,
            checkpoint_ns="agent",
            recursion_limit=max(4, settings.agent_max_llm_calls * 2 + 2),
        )
        if checkpointer is not None and checkpoint_thread_id
        else None
    )
    result = await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
            agent_run_id=agent_run_id,
            run_kind="interactive",
            agent_context_plan=_agent_context_plan(analysis),
            channel=channel,
            group_id=group_id,
            bot_user_id=bot_user_id,
            language=language,
        ),
        settings=settings,
        checkpointer=checkpointer,
    ).run(
        user_query=user_query,
        knowledge_query=knowledge_query,
        recent_messages=recent_messages,
        query_speaker=query_speaker,
        run_config=run_config,
    )
    # Agent 主动调了数据工具且失败，说明它自己判断需要真实数据；直接走安全兜底，不再用关键词二次猜意图。
    if result.data_tool_failed:
        raise AgentDataUnavailableError(DATA_UNAVAILABLE_REPLY)
    return result


async def generate_scheduled_reminder_reply(
    *,
    service_case_id: int,
    trigger_id: int | None,
    reminder_instruction: str,
    recent_messages: list[str],
    channel: str | None = None,
    settings: Settings | None = None,
    agent_run_id: int | None = None,
    language: str = "zh",
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
        context=AgentRunContext(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
            agent_run_id=agent_run_id,
            run_kind="scheduled_reminder",
            channel=channel,
            language=language,
        ),
        settings=settings,
        include_reminder_tools=False,
    ).run(user_query=user_query, recent_messages=recent_messages, query_speaker="系统定时任务")


async def generate_final_review_reply(
    *,
    service_case_id: int,
    node_instruction: str,
    recent_messages: list[str],
    channel: str | None = None,
    settings: Settings | None = None,
    agent_run_id: int | None = None,
    language: str = "zh",
) -> AgentReplyResult:
    """结营复盘节点到点：让助教按指令开口（题干已在指令里，逐字念）。

    与定时提醒同样不带 checkpoint、不给提醒工具（防提醒套提醒），但**保留服务生命
    周期与偏好工具**：客户很可能就在这几天说"别再推荐了"，那句话必须当场生效。
    """
    settings = settings or get_settings()
    return await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case_id,
            trigger_id=None,
            agent_run_id=agent_run_id,
            run_kind="final_review",
            channel=channel,
            language=language,
        ),
        settings=settings,
        include_reminder_tools=False,
    ).run(
        user_query=node_instruction,
        recent_messages=recent_messages,
        query_speaker="系统定时任务",
    )


# 召回消息的「重新启动」动作候选。三件都是刻意压到最低门槛的单个动作，
# 由模型挑一件，不是清单也不是三选一让用户选。措辞是业务侧定的原话，不要改写。
#
# 三件的区别是**要求的门槛**，不是身体维度——这一点写错，模型就会在语义重叠的两件
# 之间摇摆（曾把「收口」窄化成只管晚上吃，与「报睡眠」的作息场景撞车）。
# 「收口」沿用本项目既有口径：晚间收尾节律（晚A 的作用即「帮助晚间收口」），
# 打卡问卷原话是「昨晚睡眠是否按计划收口」，含晚餐后不再进食与按点睡下两层，
# 是要他真做一次；而「报睡眠」只要一个数字，不要求任何行为改变，门槛最低。
_CHURN_RECALL_RESTART_ACTIONS = {
    "只拍一张餐照": (
        "吃上没有抓手：很久没拍过餐、不确定怎么搭配、也没在记录。"
        "只需要他拍一张、不需要他改吃法"
    ),
    "只做一次收口": (
        "晚上收不住：晚餐后还在吃，或者拖着不睡。"
        "请他今晚只做一次晚间收尾——吃完就停、到点放下手机睡下，只做这一晚"
    ),
    "只报一下昨晚几点睡觉": (
        "整个人都停摆了、什么都不想做：连拍照和收口都推不动，"
        "那就只要一个数字，报个昨晚入睡时间就算重新接上线，不要求他改任何行为"
    ),
}


async def generate_churn_recall_reply(
    *,
    service_case_id: int,
    trigger_id: int | None,
    silent_days: int,
    recent_messages: list[str],
    channel: str | None = None,
    settings: Settings | None = None,
    agent_run_id: int | None = None,
    language: str = "zh",
) -> AgentReplyResult:
    """长期沉默用户的召回文案：一条消息里「一个极小的重启动作 + 约燃总聊一次」。

    silent_days 只用来让模型判断疏远程度，**不能写进文案**——把天数摆到用户面前
    等于当众点名他掉队了，只会让本就不想开口的人更不想开口。
    """
    settings = settings or get_settings()
    action_lines = "\n".join(
        f"- {action} —— {why}"
        for action, why in _CHURN_RECALL_RESTART_ACTIONS.items()
    )
    user_query = (
        "这是一条主动召回消息：这位用户已经很久没有在群里说话了，现在要发一条消息把他拉回来。"
        "直接生成最终要发到群里的文案，不要追问确认，不要创建提醒。\n"
        "文案固定包含两部分，合成一条消息、自然衔接，总共三四句话：\n"
        "（1）重新启动：从下面三件里挑【一件】最贴合他情况的，只提一件，保留“只……”的说法，"
        "让他觉得几乎不花力气就能重新开始；不要三件都列，也不要写成任务清单。\n"
        f"{action_lines}\n"
        "挑选顺序是硬性的：先看他自己的对话里露出过哪类卡点——提过吃/拍照就挑餐照，"
        "提过晚上吃东西或拖着不睡就挑收口，只有对话里确实看不出任何卡点线索时，"
        "才退到门槛最低的报睡眠时间。他沉默多久不影响这个判断，"
        "针对他真实提过的事反而更容易把人叫回来。\n"
        "（2）个案交流：问他要不要安排一次和燃总的电话或视频，做一次个案交流。"
        "只问意愿，不要提任何具体时间或时间选项——具体怎么约由班主任后续安排。\n"
        "硬性要求：\n"
        "- 结合他自己的情况写，不要写谁都适用的通用问候\n"
        "- 不要提他多久没说话、没打卡、掉队了这类计数或评价，不要质问原因，不要施压\n"
        "- 不要给任何健康建议、饮食方案、用量或症状判断，这条消息只负责把人叫回来\n"
        "- 不要在文案里写“@某人/@昵称”，系统会自动用企微原生 @ 提醒到他本人\n"
        "- 语气像相熟的人隔了阵子来问一句，不要像客服话术或群发广告\n"
        f"（供你判断疏远程度的内部信息，禁止写进文案：他已 {silent_days} 天没有发言。）"
    )
    return await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
            agent_run_id=agent_run_id,
            run_kind="churn_recall",
            channel=channel,
            language=language,
        ),
        settings=settings,
        include_reminder_tools=False,
    ).run(user_query=user_query, recent_messages=recent_messages, query_speaker="系统召回任务")


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
        # 只认显式错误前缀，别用“失败/错误”模糊子串——否则知识/skill 正文里出现这些字会被误判为工具失败。
        return result.lstrip().startswith(_STR_TOOL_ERROR_PREFIXES)
    return False


def _is_data_tool(name: str) -> bool:
    return name in {
        "get_service_progress",
        "get_recent_checkins",
        "get_weight_trend",
        "list_delivery_documents",
        "read_delivery_document",
        "list_group_files",
        "read_group_file",
        "get_checkin_reward_status",
        "get_checkin_followup_preferences",
    }


def _current_time_context() -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"现在是北京时间 {now:%Y-%m-%d %H:%M}（{weekdays[now.weekday()]}）。用户说“现在、最近、最新、今年”时，按这个日期理解。"
