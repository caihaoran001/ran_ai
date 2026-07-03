from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.agent_reminder_task import AgentReminderTask, AgentReminderTaskStatus
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.agent.state import AgentRunContext
from app.services.service_case_queries import select_active_service_cases, service_window_ended

_MAX_REMINDER_INSTRUCTION_CHARS = 240
_MAX_REMINDER_DAYS_AHEAD = 30


class CreateReminderArgs(BaseModel):
    remind_at: str = Field(
        min_length=1,
        max_length=64,
        description="Reminder time as ISO-8601 datetime. Include timezone when possible.",
    )
    content: str = Field(
        min_length=1,
        max_length=_MAX_REMINDER_INSTRUCTION_CHARS,
        description=(
            "Reminder goal/instruction without the scheduling time, e.g. '称体重' or "
            "'生成一句早间饮食注意事项'. The scheduled agent will generate the final message at send time."
        ),
    )


def build_reminder_tools(*, context: AgentRunContext, settings: Settings) -> list[BaseTool]:
    async def create_group_reminder(remind_at: str, content: str) -> dict[str, Any]:
        tz = ZoneInfo(settings.reminder_timezone)
        parsed = _parse_remind_at(remind_at, tz)
        if parsed is None:
            return {"ok": False, "error": "提醒时间不明确，请先追问具体日期和时间。"}

        now_local = datetime.now(tz)
        remind_local = parsed.astimezone(tz)
        if remind_local <= now_local:
            return {"ok": False, "error": "提醒时间已经过去，请让用户重新确认一个未来时间。"}
        if remind_local > now_local + timedelta(days=_MAX_REMINDER_DAYS_AHEAD):
            return {"ok": False, "error": f"暂时只能设置未来 {_MAX_REMINDER_DAYS_AHEAD} 天内的一次性提醒。"}

        clean_content = _clean_content(content)
        if not clean_content:
            return {"ok": False, "error": "提醒内容为空，请先确认要提醒什么。"}

        async with SessionLocal() as session:
            service_case = await session.scalar(select_active_service_cases(ServiceCase.id == context.service_case_id))
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除，不能创建提醒。"}
            if service_case.status in {ServiceCaseStatus.COMPLETED, ServiceCaseStatus.REFUNDED}:
                return {"ok": False, "error": "当前服务单已结束，不能创建提醒。"}
            if service_window_ended(service_case, now_local, tz):
                return {"ok": False, "error": "当前服务窗口已结束，不能创建提醒。"}

            task = AgentReminderTask(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                trigger_message_id=context.trigger_id,
                remind_at=remind_local.astimezone(timezone.utc),
                content=clean_content,
                status=AgentReminderTaskStatus.PENDING,
            )
            session.add(task)
            await session.flush()
            task_id = task.id
            await session.commit()

        return {
            "ok": True,
            "task_id": task_id,
            "remind_at": remind_local.isoformat(),
            "content": clean_content,
            "note": "已创建一次性群提醒；到点后会先由 Agent 结合当时上下文生成最终提醒文案。",
        }

    return [
        StructuredTool.from_function(
            coroutine=create_group_reminder,
            name="create_group_reminder",
            description=(
                "Create a one-time scheduled agent reminder for the current group only. At the due time, the "
                "scheduler will run the agent again to generate the final message from the stored instruction. "
                "Use when the user explicitly asks the bot to remind, advise, suggest, or proactively say "
                "something at a specific future date/time. The remind_at argument must be a concrete ISO-8601 "
                "datetime; if the time is vague, ask one clarification before calling this tool. Does not support "
                "recurring daily/weekly reminders."
            ),
            args_schema=CreateReminderArgs,
        )
    ]


def _parse_remind_at(value: str, tz: ZoneInfo) -> datetime | None:
    clean = (value or "").strip()
    if not clean:
        return None
    clean = clean.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _clean_content(value: str) -> str:
    clean = " ".join((value or "").split())
    clean = _strip_leading_schedule_phrase(clean).strip(" ，,。")
    return clean[:_MAX_REMINDER_INSTRUCTION_CHARS]


def _strip_leading_schedule_phrase(value: str) -> str:
    return re.sub(
        (
            r"^请?(?:在)?"
            r"(?:(?:今天|明天|后天|今晚|明早|明晚|周[一二三四五六日天]|星期[一二三四五六日天])\s*)?"
            r"(?:(?:早上|上午|中午|下午|晚上|夜里)\s*)?"
            r"(?:[0-2]?\d|[一二两三四五六七八九十]{1,3})"
            r"(?:[:：点时](?:[0-5]?\d|[零一二三四五六七八九十]{1,3})?(?:分)?)?"
            r"(?:左右|前后|的时候)?\s*"
            r"(?:提醒(?:一下)?(?:我|我们|群里)?|发(?:一条|个)?|说(?:一声|一句)?)\s*"
        ),
        "",
        value,
        count=1,
    )
