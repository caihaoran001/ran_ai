from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.message import Message, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.agent.state import AgentRunContext
from app.services.checkin_followup_preferences import (
    FOLLOWUP_TOPIC_LABELS,
    FOLLOWUP_TOPICS,
    get_followup_preference_states,
    set_followup_preference_states,
)
from app.services.service_case_queries import select_active_service_cases

FollowupTopic = Literal[
    "daily_checklist", "weight", "waist", "milestone_review", "contradiction"
]


class UpdateFollowupPreferencesArgs(BaseModel):
    topics: list[FollowupTopic] = Field(
        min_length=1,
        max_length=len(FOLLOWUP_TOPICS),
        description=(
            "Topics to change. daily_checklist=昨日五项回顾, weight=体重, waist=腰围, "
            "milestone_review=次晨空腹打卡提醒, contradiction=打卡数据核对。可一次传多个。"
        ),
    )
    enabled: bool = Field(
        description="true=恢复该主动追问，false=停止该主动追问。"
    )


def _state_payload(states: dict[str, bool]) -> list[dict[str, Any]]:
    return [
        {"topic": topic, "label": FOLLOWUP_TOPIC_LABELS[topic], "enabled": bool(states[topic])}
        for topic in FOLLOWUP_TOPICS
    ]


def build_followup_preference_tools(*, context: AgentRunContext) -> list[BaseTool]:
    async def get_checkin_followup_preferences() -> dict[str, Any]:
        """Read every proactive check-in switch for the bound service case."""
        async with SessionLocal() as session:
            states = await get_followup_preference_states(
                session, service_case_id=context.service_case_id
            )
        return {"ok": True, "preferences": _state_payload(states)}

    async def update_checkin_followup_preferences(
        topics: list[FollowupTopic], enabled: bool
    ) -> dict[str, Any]:
        """Persist an explicit user request; never accepts another case/user as input."""
        if context.trigger_id is None:
            return {"ok": False, "error": "当前不是用户发起的群聊，不能修改追问偏好。"}
        async with SessionLocal() as session:
            source = await session.scalar(
                select(Message).where(
                    Message.id == context.trigger_id,
                    Message.service_case_id == context.service_case_id,
                    Message.sender_type == MessageSenderType.USER,
                )
            )
            if source is None:
                return {"ok": False, "error": "只能根据当前服务用户的明确请求修改追问偏好。"}
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == context.service_case_id)
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已结束，不能修改追问偏好。"}
            states = await set_followup_preference_states(
                session,
                service_case_id=context.service_case_id,
                topics=topics,
                enabled=enabled,
                source_message_id=source.id,
            )
            await session.commit()
        return {
            "ok": True,
            "updated_topics": list(topics),
            "enabled": enabled,
            "preferences": _state_payload(states),
        }

    return [
        StructuredTool.from_function(
            coroutine=get_checkin_followup_preferences,
            name="get_checkin_followup_preferences",
            description=(
                "Read all current proactive check-in follow-up switches for THIS service case. "
                "Use when the user asks which check-in topics will still be proactively asked."
            ),
        ),
        StructuredTool.from_function(
            coroutine=update_checkin_followup_preferences,
            name="update_checkin_followup_preferences",
            description=(
                "Change proactive check-in follow-up switches for THIS service user only. Use only when the user "
                "explicitly asks to stop or resume one or more topics; no @ mention or reply-to-bot requirement. "
                "Do NOT call merely because the user reports a weight, waist, or check-in value."
            ),
            args_schema=UpdateFollowupPreferencesArgs,
        ),
    ]
