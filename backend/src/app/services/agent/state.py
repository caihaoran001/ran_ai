from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    loaded_skills: list[str]
    tool_trace: list[dict]
    llm_call_count: int
    tool_call_count: int
    web_search_count: int
    data_tool_failed: bool
    data_tool_status: dict[str, bool]
    consecutive_tool_failures: dict[str, int]


@dataclass(frozen=True)
class AgentRunContext:
    service_case_id: int
    trigger_id: int | None = None
    agent_run_id: int | None = None
    run_kind: str = "interactive"
    agent_context_plan: dict | None = None
    # 触发本次 Agent 运行的消息渠道名（如 "qiwe"）。用于日志可观测性与多渠道排查；
    # 按“渠道隔离”铁律，工具/日志需要知道自己服务的是哪个渠道，缺省 None 兼容定时任务等无渠道场景。
    channel: str | None = None
    # 当前群 id 与机器人自身 userId：用于解析"机器人在本群的群昵称"注入身份提示词。
    # 缺省 None 兼容非群聊/定时任务等无群上下文场景。
    group_id: str | None = None
    bot_user_id: str | None = None
    language: str = "zh"
