"""员工纠正的确定性校验、分流和 Chat Agent 反哺读视图。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_draft import KnowledgeDraft
from app.models.message import Message, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.knowledge_evolution import create_draft_candidate
from app.services.user_state_profile import apply_staff_correction_to_user_state


@dataclass
class CorrectionApplyResult:
    personal_written: int = 0
    qa_drafts_created: int = 0
    agent_rule_drafts_created: int = 0
    skipped: int = 0


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
        corrected_answer = _text(item.get("corrected_answer"), 2000) or summary
        if not summary or not applicability or not corrected_answer:
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
            result.personal_written += int(profile is not None)
            continue

        if scope == "global_knowledge":
            question = _text(item.get("question"), 1000) or applicability
            draft_type = "qa"
            category = "员工纠正沉淀"
        elif scope == "agent_behavior":
            question = applicability
            draft_type = "agent_rule"
            category = "Chat Agent 回答规则"
        else:
            result.skipped += 1
            continue

        draft = await create_draft_candidate(
            session,
            draft_type=draft_type,
            title=summary[:80],
            question=question,
            answer=corrected_answer,
            category=category,
            tags=["员工纠正", scope],
            reason="班主任在群内对助教回答进行了明确纠正或补充。",
            evidence=evidence,
            sanitized_text=evidence,
            source_message_ids=[bot.id, staff.id],
            content_json={
                "source": "archivist_staff_correction",
                "service_case_id": service_case.id,
                "scope": scope,
                "summary": summary,
                "applicability": applicability,
                "bot_message_id": bot.id,
                "staff_message_id": staff.id,
                "confidence": confidence,
            },
        )
        if draft is None:
            result.skipped += 1
        elif draft_type == "qa":
            result.qa_drafts_created += 1
        else:
            result.agent_rule_drafts_created += 1
    return result


async def load_approved_agent_rule_context(
    session: AsyncSession,
    *,
    limit: int = 6,
) -> str:
    """返回经人工审核的回答规则，作为受控上下文注入 Chat Agent。"""

    drafts = list(
        await session.scalars(
            select(KnowledgeDraft)
            .where(
                KnowledgeDraft.draft_type == "agent_rule",
                KnowledgeDraft.status == "approved",
            )
            .order_by(KnowledgeDraft.approved_at.desc(), KnowledgeDraft.id.desc())
            .limit(max(1, min(limit, 12)))
        )
    )
    if not drafts:
        return ""
    return "\n".join(
        f"- 适用场景：{_text(draft.question, 240)}；回答规则：{_text(draft.answer, 500)}"
        for draft in drafts
    )


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
