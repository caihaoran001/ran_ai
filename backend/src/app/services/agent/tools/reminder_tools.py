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
_ALLOWED_RECURRENCE = {"none", "daily", "weekly"}


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
    recurrence: str = Field(
        default="none",
        description=(
            "Recurrence rule: 'none' (one-time, default), 'daily' (every day at the same time), "
            "or 'weekly' (every week on the same weekday/time). For a recurring reminder, remind_at "
            "is the first occurrence; the series auto-stops when the 28-day service window ends and "
            "can be cancelled anytime via cancel_group_reminder."
        ),
    )


class CancelReminderArgs(BaseModel):
    task_id: int | None = Field(
        default=None,
        description="Specific pending reminder id to cancel (from list_group_reminders). Omit to auto-pick when only one is pending.",
    )
    cancel_all: bool = Field(
        default=False,
        description="Cancel all pending reminders for the current group when true.",
    )


def build_reminder_tools(*, context: AgentRunContext, settings: Settings) -> list[BaseTool]:
    async def create_group_reminder(remind_at: str, content: str, recurrence: str = "none") -> dict[str, Any]:
        tz = ZoneInfo(settings.reminder_timezone)
        recurrence = (recurrence or "none").strip().lower()
        if recurrence not in _ALLOWED_RECURRENCE:
            return {"ok": False, "error": "循环规则只支持单次(none)、每天(daily)、每周(weekly)。"}
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

            # 幂等去重：同一服务单在目标时间 ±120s 内已有待发提醒时，不重复创建。
            # 防 Agent 对同一意图连发两次（如用户说一次、随后确认一次各建一条，导致到点重复提醒）。
            remind_utc = remind_local.astimezone(timezone.utc)
            dedup_window = timedelta(seconds=120)
            existing = await session.scalar(
                select(AgentReminderTask)
                .where(
                    AgentReminderTask.service_case_id == service_case.id,
                    AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
                    AgentReminderTask.remind_at >= remind_utc - dedup_window,
                    AgentReminderTask.remind_at <= remind_utc + dedup_window,
                )
                .order_by(AgentReminderTask.remind_at)
            )
            if existing is not None:
                return {
                    "ok": True,
                    "task_id": existing.id,
                    "remind_at": existing.remind_at.astimezone(tz).isoformat(),
                    "content": existing.content,
                    "deduped": True,
                    "note": "该时间点附近已有一条待发提醒，未重复创建（沿用已存在的提醒）。",
                }

            task = AgentReminderTask(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                trigger_message_id=context.trigger_id,
                remind_at=remind_local.astimezone(timezone.utc),
                content=clean_content,
                recurrence=recurrence,
                status=AgentReminderTaskStatus.PENDING,
            )
            session.add(task)
            await session.flush()
            task_id = task.id
            await session.commit()

        recur_note = {
            "none": "已创建一次性群提醒；到点后会先由 Agent 结合当时上下文生成最终提醒文案。",
            "daily": "已创建每天循环群提醒；服务期结束会自动停止，用户可随时取消。",
            "weekly": "已创建每周循环群提醒；服务期结束会自动停止，用户可随时取消。",
        }
        return {
            "ok": True,
            "task_id": task_id,
            "remind_at": remind_local.isoformat(),
            "content": clean_content,
            "recurrence": recurrence,
            "note": recur_note[recurrence],
        }

    async def list_group_reminders() -> dict[str, Any]:
        tz = ZoneInfo(settings.reminder_timezone)
        async with SessionLocal() as session:
            tasks = list(
                await session.scalars(
                    select(AgentReminderTask)
                    .where(
                        AgentReminderTask.service_case_id == context.service_case_id,
                        AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
                    )
                    .order_by(AgentReminderTask.remind_at)
                )
            )
        pending = [
            {
                "task_id": task.id,
                "remind_at": task.remind_at.astimezone(tz).isoformat(),
                "content": task.content,
                "recurrence": task.recurrence,
            }
            for task in tasks
        ]
        return {"ok": True, "pending": pending, "count": len(pending)}

    async def cancel_group_reminder(task_id: int | None = None, cancel_all: bool = False) -> dict[str, Any]:
        tz = ZoneInfo(settings.reminder_timezone)
        async with SessionLocal() as session:
            tasks = list(
                await session.scalars(
                    select(AgentReminderTask)
                    .where(
                        AgentReminderTask.service_case_id == context.service_case_id,
                        AgentReminderTask.status == AgentReminderTaskStatus.PENDING,
                    )
                    .order_by(AgentReminderTask.remind_at)
                )
            )
            if not tasks:
                return {"ok": True, "cancelled": [], "note": "当前没有待发提醒。"}

            if task_id is not None:
                targets = [t for t in tasks if t.id == task_id]
                if not targets:
                    return {
                        "ok": False,
                        "error": f"没有找到编号 {task_id} 的待发提醒（可能已发送或已取消）。",
                        "pending": _pending_view(tasks, tz),
                    }
            elif cancel_all:
                targets = tasks
            elif len(tasks) == 1:
                targets = tasks
            else:
                # 多条待发且未指明取消哪个：交回列表让 Agent 反问用户。
                return {
                    "ok": True,
                    "need_choice": True,
                    "pending": _pending_view(tasks, tz),
                    "note": "有多条待发提醒，请先确认用户要取消哪一个（或全部）。",
                }

            now_utc = datetime.now(timezone.utc)
            cancelled = []
            for task in targets:
                task.status = AgentReminderTaskStatus.CANCELLED
                task.cancelled_at = now_utc
                cancelled.append(
                    {
                        "task_id": task.id,
                        "remind_at": task.remind_at.astimezone(tz).isoformat(),
                        "content": task.content,
                        "recurrence": task.recurrence,
                    }
                )
            await session.commit()
        return {"ok": True, "cancelled": cancelled, "count": len(cancelled)}

    return [
        StructuredTool.from_function(
            coroutine=create_group_reminder,
            name="create_group_reminder",
            description=(
                "Create a scheduled agent reminder for the current group only. At the due time, the scheduler "
                "runs the agent again to generate the final message from the stored instruction. Use when the "
                "user explicitly asks the bot to remind, advise, suggest, or proactively say something at a "
                "specific future date/time. The remind_at argument must be a concrete ISO-8601 datetime; if the "
                "time is vague, ask one clarification before calling this tool. Set recurrence to 'daily' or "
                "'weekly' for repeating reminders (remind_at is the first occurrence); recurring series auto-stop "
                "when the 28-day service window ends and can be cancelled anytime."
            ),
            args_schema=CreateReminderArgs,
        ),
        StructuredTool.from_function(
            coroutine=list_group_reminders,
            name="list_group_reminders",
            description=(
                "List all pending (not-yet-sent) reminders for the current group, with their id, local time, "
                "content and recurrence. Call this before cancel_group_reminder when the user's reference is "
                "ambiguous, so you can map it to a task_id."
            ),
        ),
        StructuredTool.from_function(
            coroutine=cancel_group_reminder,
            name="cancel_group_reminder",
            description=(
                "Cancel pending reminder(s) for the current group when the user says things like '别提醒了' / "
                "'取消提醒' / '晚A那个先别提醒'. Pass task_id to cancel a specific one, cancel_all=true to cancel "
                "every pending reminder. With no args it auto-cancels when exactly one is pending, otherwise it "
                "returns the pending list (need_choice) so you can ask the user which to cancel."
            ),
            args_schema=CancelReminderArgs,
        ),
    ]


def _pending_view(tasks: list[AgentReminderTask], tz: ZoneInfo) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task.id,
            "remind_at": task.remind_at.astimezone(tz).isoformat(),
            "content": task.content,
            "recurrence": task.recurrence,
        }
        for task in tasks
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
