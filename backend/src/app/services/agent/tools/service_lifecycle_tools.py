from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.agent.state import AgentRunContext
from app.services.service_case_queries import (
    effective_service_day,
    select_active_service_cases,
    service_continuation_active,
    service_phase_end_at,
    service_window_ended,
)


async def _explicit_group_source(session, context: AgentRunContext) -> tuple[Message | None, dict[str, Any]]:
    """只接受当前群聊中明确 @ 机器人的用户/工作人员消息。

    消息入库时，engine 只在真实 @ 机器人时写入
    `_qiwe_meta.explicit_bot_query_text`，工具在写状态前再校验一次。
    """
    if context.trigger_id is None:
        return None, {}
    source = await session.scalar(
        select(Message).where(
            Message.id == context.trigger_id,
            Message.service_case_id == context.service_case_id,
            Message.direction == MessageDirection.INBOUND,
            Message.purpose == MessagePurpose.CHAT,
            Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
        )
    )
    if source is None:
        return None, {}
    analysis = source.analysis_json if isinstance(source.analysis_json, dict) else {}
    meta = analysis.get("_qiwe_meta") if isinstance(analysis.get("_qiwe_meta"), dict) else {}
    if not str(meta.get("explicit_bot_query_text") or "").strip():
        return None, meta
    return source, meta


def _append_event(
    service_case: ServiceCase,
    *,
    action: str,
    occurred_at: datetime,
    source: Message,
    meta: dict[str, Any],
) -> None:
    events = list(service_case.service_lifecycle_events or [])
    events.append(
        {
            "action": action,
            "occurred_at": occurred_at.isoformat(),
            "source_message_id": source.id,
            "sender_type": source.sender_type.value,
            "sender_id": meta.get("sender_id"),
            "sender_name": meta.get("sender_name"),
        }
    )
    service_case.service_lifecycle_events = events


def build_service_lifecycle_tools(*, context: AgentRunContext, settings: Settings) -> list[BaseTool]:
    async def continue_service() -> dict[str, Any]:
        """在原服务单上恢复无固定截止日的连续服务，不重置服务天数。"""
        tz = ZoneInfo(settings.reminder_timezone)
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            source, meta = await _explicit_group_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "续服必须由当前群成员明确 @ 机器人提出，本次未验证到有效指令。",
                }

            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == context.service_case_id).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除，不能续服。"}
            if service_case.status == ServiceCaseStatus.REFUNDED:
                return {"ok": False, "error": "已退款服务不能在群内直接续服，请先由运营确认恢复。"}
            window_ended = service_window_ended(service_case, now.astimezone(tz), tz)
            if service_case.status == ServiceCaseStatus.IN_SERVICE and not window_ended:
                day = effective_service_day(service_case, now.astimezone(tz), tz)
                return {
                    "ok": True,
                    "continued": False,
                    "already_in_service": True,
                    "service_day": day,
                    "service_day_text": f"第 {day} 天" if day >= 1 else "服务尚未正式开始",
                    "continuation_active": service_continuation_active(service_case),
                    "note": "当前已在服务中，未重复修改服务起点或天数。",
                }
            if service_case.status not in {
                ServiceCaseStatus.IN_SERVICE,
                ServiceCaseStatus.COMPLETED,
            }:
                return {
                    "ok": False,
                    "error": "只能对28天阶段已完结的服务恢复续服；当前还处于入营或交付流程。",
                }

            service_case.status = ServiceCaseStatus.IN_SERVICE
            # 指令可能刚好落在第 29 天调度器尚未把状态切成 COMPLETED 的短窗口；
            # 此时先固化原阶段结束时间，再开启续服，避免立即被再次结束。
            if service_case.ended_at is None and window_ended:
                service_case.ended_at = service_phase_end_at(service_case, tz)
            service_case.service_continued_at = now
            _append_event(
                service_case,
                action="continue_service",
                occurred_at=now,
                source=source,
                meta=meta,
            )
            day = effective_service_day(service_case, now.astimezone(tz), tz)
            await session.commit()
            return {
                "ok": True,
                "continued": True,
                "service_day": day,
                "service_day_text": f"第 {day} 天" if day >= 1 else "服务尚未正式开始",
                "continued_at": now.astimezone(tz).isoformat(),
                "note": "已在原服务单上恢复连续服务；原服务起点和天数未重置。",
            }

    async def end_continued_service() -> dict[str, Any]:
        """由群内工作人员明确结束当前续服，保留所有历史与审计记录。"""
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            source, meta = await _explicit_group_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "结束续服必须在当前群里明确 @ 机器人提出。",
                }
            if source.sender_type != MessageSenderType.STAFF:
                return {
                    "ok": False,
                    "error": "只有已识别的班主任或运营人员可以结束续服。",
                }

            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == context.service_case_id).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            if service_case.status == ServiceCaseStatus.COMPLETED:
                return {
                    "ok": True,
                    "ended": False,
                    "already_completed": True,
                    "note": "当前服务已结束，未重复写入。",
                }
            if not service_continuation_active(service_case):
                return {
                    "ok": False,
                    "error": "当前不是续服中状态，不能通过该指令结束普通服务。",
                }

            service_case.status = ServiceCaseStatus.COMPLETED
            service_case.ended_at = now
            _append_event(
                service_case,
                action="end_continued_service",
                occurred_at=now,
                source=source,
                meta=meta,
            )
            await session.commit()
            return {
                "ok": True,
                "ended": True,
                "ended_at": now.isoformat(),
                "note": "续服已结束，原服务单和所有历史记录保留。",
            }

    return [
        StructuredTool.from_function(
            coroutine=continue_service,
            name="continue_service",
            description=(
                "Continue service on THIS existing service case without resetting its original start date or service day. "
                "Use only when the current user or staff member explicitly @mentions the bot and clearly asks to continue/"
                "resume service for a graduated case. Never use for motivational phrases such as 'continue working hard', "
                "ordinary renewal discussion, or when no explicit continuation request was made."
            ),
        ),
        StructuredTool.from_function(
            coroutine=end_continued_service,
            name="end_continued_service",
            description=(
                "End an open-ended continued service on THIS service case. Use only for an explicit @bot request from "
                "identified staff. It must not end an ordinary first 28-day service or act on a customer's request."
            ),
        ),
    ]
