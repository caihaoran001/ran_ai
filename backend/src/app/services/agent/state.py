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
