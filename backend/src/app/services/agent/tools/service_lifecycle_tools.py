from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.agent.state import AgentRunContext
from app.services.service_case_activation import (
    activate_service_case_if_due,
    align_active_service_start_with_plan,
)
from app.services.service_case_queries import (
    effective_service_day,
    select_active_service_cases,
    service_continuation_active,
    service_phase_end_at,
    service_window_ended,
)


async def _explicit_group_source(
    session, context: AgentRunContext
) -> tuple[Message | None, dict[str, Any]]:
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
    meta = (
        analysis.get("_qiwe_meta")
        if isinstance(analysis.get("_qiwe_meta"), dict)
        else {}
    )
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
    details: dict[str, Any] | None = None,
) -> None:
    events = list(service_case.service_lifecycle_events or [])
    event = {
        "action": action,
        "occurred_at": occurred_at.isoformat(),
        "source_message_id": source.id,
        "sender_type": source.sender_type.value,
        "sender_id": meta.get("sender_id"),
        "sender_name": meta.get("sender_name"),
    }
    if details:
        event.update(details)
    events.append(event)
    service_case.service_lifecycle_events = events


def build_service_lifecycle_tools(
    *, context: AgentRunContext, settings: Settings
) -> list[BaseTool]:
    async def set_service_start_date(start_date: str) -> dict[str, Any]:
        """由当前群内工作人员设置本服务单的 28 天计划开始日期。"""
        raw_date = start_date.strip()
        try:
            parsed_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "开始日期格式无效，请换算成 YYYY-MM-DD 后重试。",
            }
        if parsed_date.isoformat() != raw_date:
            return {
                "ok": False,
                "error": "开始日期必须使用 YYYY-MM-DD 格式。",
            }

        tz = ZoneInfo(settings.reminder_timezone)
        now = datetime.now(timezone.utc)
        local_today = now.astimezone(tz).date()
        async with SessionLocal() as session:
            source, meta = await _explicit_group_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "设置服务开始日期必须由工作人员在当前群里明确 @ 机器人提出。",
                }
            if source.sender_type != MessageSenderType.STAFF:
                return {
                    "ok": False,
                    "error": "只有已识别的班主任或运营人员可以设置服务开始日期。",
                }

            service_case = await session.scalar(
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            if service_case.status in {
                ServiceCaseStatus.COMPLETED,
                ServiceCaseStatus.REFUNDED,
            }:
                return {
                    "ok": False,
                    "error": "已结束或已退款的服务不能在群内改开始日期，请由运营核对后处理。",
                }
            if service_continuation_active(service_case):
                return {
                    "ok": False,
                    "error": "当前处于续服中，不能直接改原 28 天服务起点，请由运营核对后处理。",
                }
            if (
                service_case.status == ServiceCaseStatus.IN_SERVICE
                and parsed_date > local_today
            ):
                return {
                    "ok": False,
                    "error": "已开始的服务不能改成未来日期，请先由运营核对当前服务状态。",
                }

            old_date = service_case.service_plan_start_date
            service_case.service_plan_start_date = parsed_date
            activated = activate_service_case_if_due(service_case, now=now, tz=tz)
            aligned = align_active_service_start_with_plan(service_case, now=now, tz=tz)
            changed = old_date != parsed_date
            if changed or activated or aligned:
                _append_event(
                    service_case,
                    action="set_service_start_date",
                    occurred_at=now,
                    source=source,
                    meta=meta,
                    details={
                        "old_start_date": old_date.isoformat() if old_date else None,
                        "new_start_date": parsed_date.isoformat(),
                    },
                )
                await session.commit()

            day = effective_service_day(service_case, now.astimezone(tz), tz)
            return {
                "ok": True,
                "changed": changed,
                "activated": activated,
                "start_date": parsed_date.isoformat(),
                "service_status": service_case.status.value,
                "service_day": day if parsed_date <= local_today else None,
                "service_day_text": (
                    f"第 {day} 天"
                    if parsed_date <= local_today and day >= 1
                    else "服务尚未正式开始"
                ),
                "note": (
                    "开始日期已生效，28 天任务从该日期起算。"
                    if parsed_date <= local_today
                    else "开始日期已保存，到该日期再启动 28 天任务；群聊服务不受影响。"
                ),
            }

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
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除，不能续服。"}
            if service_case.status == ServiceCaseStatus.REFUNDED:
                return {
                    "ok": False,
                    "error": "已退款服务不能在群内直接续服，请先由运营确认恢复。",
                }
            window_ended = service_window_ended(service_case, now.astimezone(tz), tz)
            if service_case.status == ServiceCaseStatus.IN_SERVICE and not window_ended:
                day = effective_service_day(service_case, now.astimezone(tz), tz)
                return {
                    "ok": True,
                    "continued": False,
                    "already_in_service": True,
                    "service_day": day,
                    "service_day_text": f"第 {day} 天"
                    if day >= 1
                    else "服务尚未正式开始",
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
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
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

    async def set_service_case_language(language: str) -> dict[str, Any]:
        """由当前群内工作人员切换本服务单的主服务语言（zh 或 en）。

        限定工作人员的理由：语言切一次，问卷、交付文档、阶段报告、定时提醒和
        群内回复全部跟着换。客户随口一句 "can you speak English?" 不该有这个权限，
        那属于转达给班主任的诉求，和本文件里其他生命周期工具是同一条边界。
        """
        target_lang = (language or "").strip().lower()
        if target_lang in ("chinese", "zh-cn", "zh_cn", "cn", "中文", "汉语", "普通话"):
            target_lang = "zh"
        elif target_lang in ("english", "en-us", "en_us", "en-gb", "英文", "英语"):
            target_lang = "en"

        if target_lang not in ("zh", "en"):
            return {
                "ok": False,
                "error": "语言代码必须为 'zh'（中文）或 'en'（英文）。",
            }

        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            source, meta = await _explicit_group_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "切换服务语言必须由工作人员在当前群里明确 @ 机器人提出。",
                }
            if source.sender_type != MessageSenderType.STAFF:
                return {
                    "ok": False,
                    "error": "只有已识别的班主任或运营人员可以切换服务语言；客户的语言诉求请转达班主任。",
                }

            service_case = await session.scalar(
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            if service_case.language == target_lang:
                return {
                    "ok": True,
                    "language": target_lang,
                    "already_set": True,
                    "note": f"当前群服务语言已经是 {target_lang}，无需重复设置。",
                }

            service_case.language = target_lang
            _append_event(
                service_case,
                action="set_service_case_language",
                occurred_at=now,
                source=source,
                meta=meta,
                details={"language": target_lang},
            )
            await session.commit()
            return {
                "ok": True,
                "language": target_lang,
                "updated": True,
                "note": f"本群服务语言已成功切换为 {target_lang}。",
            }

    return [
        StructuredTool.from_function(
            coroutine=set_service_start_date,
            name="set_service_start_date",
            description=(
                "Set or correct the 28-day service start date for THIS group. Use only when identified staff explicitly "
                "@mentions the bot and asks to set/change the service start date. Convert an unambiguous natural-language "
                "date to YYYY-MM-DD before calling. The questionnaire is optional and this date controls scheduled day-based "
                "work, not whether the bot may chat. Never use for customer messages, messages without an explicit @mention, "
                "or general discussion about dates."
            ),
        ),
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
        StructuredTool.from_function(
            coroutine=set_service_case_language,
            name="set_service_case_language",
            description=(
                "Set or switch the primary service language for THIS group (strictly 'zh' for Chinese or 'en' for English). "
                "Switching also changes the questionnaire, delivery documents, stage reports and reminders, so it is "
                "restricted to identified staff: use it only when a staff member explicitly @mentions the bot in this group "
                "and asks to switch the group's language (e.g. '请使用英文对话', '此专属群全程使用英文对话', 'switch to English', '换回中文'). "
                "It must not act on a customer's request — reply to the customer and let staff decide instead. "
                "Never use for general quotes or incidental English/Chinese words in normal conversation."
            ),
        ),
    ]
