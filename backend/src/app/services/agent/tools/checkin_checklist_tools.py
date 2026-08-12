from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
)
from app.services.agent.state import AgentRunContext
from app.services.checkin_checklists import (
    MANUAL_FOLLOWUP_SIZE,
    get_effective_daily_checklist,
    restore_system_daily_checklist,
    set_manual_daily_checklist,
)


class SetCheckinChecklistArgs(BaseModel):
    questions: list[str] = Field(
        min_length=MANUAL_FOLLOWUP_SIZE,
        max_length=MANUAL_FOLLOWUP_SIZE,
        description=(
            "工作人员在本条消息中逐项写出的第 2—5 项追问原文，必须按原意原文拆成正好 4 项，"
            "第 1 项固定为早C/午B/晚A，"
            "不要补写、改写、合并或推测。"
        ),
    )


async def _explicit_staff_source(
    session,
    context: AgentRunContext,
) -> tuple[Message | None, str]:
    if context.trigger_id is None:
        return None, ""
    source = await session.scalar(
        select(Message).where(
            Message.id == context.trigger_id,
            Message.service_case_id == context.service_case_id,
            Message.direction == MessageDirection.INBOUND,
            Message.purpose == MessagePurpose.CHAT,
            Message.sender_type == MessageSenderType.STAFF,
        )
    )
    if source is None:
        return None, ""
    analysis = source.analysis_json if isinstance(source.analysis_json, dict) else {}
    meta = analysis.get("_qiwe_meta") if isinstance(analysis.get("_qiwe_meta"), dict) else {}
    query = " ".join(str(meta.get("explicit_bot_query_text") or "").split()).strip()
    if not query:
        return None, ""
    return source, query


def _questions_are_grounded(query: str, questions: list[str]) -> bool:
    normalized_query = " ".join(query.split()).casefold()
    return all(
        " ".join(question.split()).strip().casefold() in normalized_query
        for question in questions
    )


def _checklist_payload(checklist) -> dict[str, Any]:
    return {
        "source_type": checklist.source_type,
        "version_no": checklist.version_no,
        "questions": [item["label"] for item in checklist.resolved_items()],
    }


def build_checkin_checklist_tools(*, context: AgentRunContext) -> list[BaseTool]:
    async def get_checkin_checklist() -> dict[str, Any]:
        """Read the current five questions for the bound service case."""
        async with SessionLocal() as session:
            checklist = await get_effective_daily_checklist(
                session, service_case_id=context.service_case_id
            )
        return {"ok": True, **_checklist_payload(checklist)}

    async def set_checkin_checklist(questions: list[str]) -> dict[str, Any]:
        """Save items 2-5 from an explicitly mentioned staff command; item 1 is fixed."""
        async with SessionLocal() as session:
            source, query = await _explicit_staff_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "只有当前群内已识别的班主任或运营明确 @ 机器人，并在同一条消息写全第 2—5 项后才能修改。",
                }
            if not _questions_are_grounded(query, questions):
                return {
                    "ok": False,
                    "error": "第 2—5 项内容必须全部来自本条 @ 消息，不能补写或改写；请在同一条消息中完整列出 4 项。",
                }
            try:
                checklist = await set_manual_daily_checklist(
                    session,
                    service_case_id=context.service_case_id,
                    questions=questions,
                    source_message=source,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            await session.commit()
        return {"ok": True, "updated": True, **_checklist_payload(checklist)}

    async def restore_default_checkin_checklist() -> dict[str, Any]:
        """Restore a versioned snapshot of the current system-derived five questions."""
        async with SessionLocal() as session:
            source, _ = await _explicit_staff_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "只有当前群内已识别的班主任或运营明确 @ 机器人后才能恢复默认五项。",
                }
            try:
                checklist = await restore_system_daily_checklist(
                    session,
                    service_case_id=context.service_case_id,
                    source_message=source,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            await session.commit()
        return {"ok": True, "restored": True, **_checklist_payload(checklist)}

    return [
        StructuredTool.from_function(
            coroutine=get_checkin_checklist,
            name="get_checkin_checklist",
            description=(
                "Read the exact five daily follow-up questions currently effective for THIS service case. "
                "Use when someone asks what the current five questions are."
            ),
        ),
        StructuredTool.from_function(
            coroutine=set_checkin_checklist,
            name="set_checkin_checklist",
            description=(
                "Set daily follow-up items 2-5 for THIS group; item 1 is always the fixed morning-C/noon-B/night-A "
                "product check-in. Call only when an identified staff member explicitly @mentions the bot and writes "
                "all four editable questions in the SAME message. Pass exactly those four verbatim questions; never "
                "invent, paraphrase, merge, or copy them from a file."
            ),
            args_schema=SetCheckinChecklistArgs,
        ),
        StructuredTool.from_function(
            coroutine=restore_default_checkin_checklist,
            name="restore_default_checkin_checklist",
            description=(
                "Restore THIS group's five questions to a versioned snapshot of the current system default. "
                "Call only when an identified staff member explicitly @mentions the bot and clearly requests reset."
            ),
        ),
    ]
