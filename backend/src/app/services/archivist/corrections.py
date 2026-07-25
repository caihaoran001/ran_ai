"""员工纠正的确定性校验与 Chat Agent 反哺读视图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.user_state_profile import apply_staff_correction_to_user_state


@dataclass
class CorrectionApplyResult:
    personal_written: int = 0
    recorded: int = 0
    skipped: int = 0
    personal_items: list[dict[str, Any]] = field(default_factory=list)
    general_items: list[dict[str, Any]] = field(default_factory=list)


async def apply_reported_corrections(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    corrections: list[dict[str, Any]],
) -> CorrectionApplyResult:
    result = CorrectionApplyResult()
    for item in corrections:
        confidence = _confidence(item.get("confidence"))
        if confidence < 0.7:
            result.skipped += 1
            continue
        bot = await session.get(Message, _positive_int(item.get("bot_message_id")))
        staff = await session.get(Message, _positive_int(item.get("staff_message_id")))
        if not _valid_pair(service_case.id, bot, staff):
            result.skipped += 1
            continue

        summary = _text(item.get("summary"), 500)
        applicability = _text(item.get("applicability"), 500)
        if not summary or not applicability:
            result.skipped += 1
            continue
        evidence = f"助教[{bot.id}]：{_text(bot.content, 800)}\n班主任[{staff.id}]：{_text(staff.content, 800)}"
        scope = str(item.get("scope") or "")

        if scope == "personal":
            profile = await apply_staff_correction_to_user_state(
                session,
                service_case=service_case,
                staff_message=staff,
                summary=summary,
                evidence=evidence,
                confidence=confidence,
            )
            if profile is None:
                result.skipped += 1
            else:
                result.personal_written += 1
                result.recorded += 1
                result.personal_items.append(
                    {
                        "summary": summary,
                        "applicability": applicability,
                        "staff_message_id": staff.id,
                    }
                )
            continue

        if scope != "general":
            result.skipped += 1
            continue
        # 通用纠错已在 archivist_extract_traces.emitted_json 中保留原始上报，
        # 此处只做消息配对校验和计数。需要进知识库时，模型必须另外调用
        # report_faq_candidate，由 knowledge_evolution 统一校验班主任确认并建稿。
        result.recorded += 1
        result.general_items.append(
            {
                "summary": summary,
                "applicability": applicability,
                "bot_message_id": bot.id,
                "staff_message_id": staff.id,
            }
        )
    return result


def _valid_pair(service_case_id: int, bot: Message | None, staff: Message | None) -> bool:
    return bool(
        bot
        and staff
        and bot.service_case_id == service_case_id
        and staff.service_case_id == service_case_id
        and bot.sender_type == MessageSenderType.BOT
        and staff.sender_type == MessageSenderType.STAFF
        and bot.id < staff.id
    )


def _positive_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _text(value: object, limit: int) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())[:limit]
