"""档案官优秀案例合成：由成效稳态变化驱动,不再按 day3/7/14/21/28 里程碑硬扫。

优秀案例的用途是——当用户怀疑产品没用时,检索出真实学员的完整成功故事当证据。因此:
- 触发靠"成效稳态变化"(本轮档案官写入了新的成效事件,且该单出现过正向进展),
  而非固定里程碑——不说话/没进展的单不会触发,烧钱少、也不漏安静但有完整弧线的单;
- "显著且平稳(非一次性小波动)"由 LLM 依据完整成效时间线判断,不硬编码阈值;
- 合成时喂**全历史 + 成效时间线**,产出完整弧线的案例草稿,复用 knowledge_evolution 的
  抽取/去重/人工审核/入库管线(不重复造表、不绕过审核)。

由持久化 post-task 在归档提交成功后用独立 session 调用；普通调用异常只记日志，
post-task 会要求异常向上抛并退避重试，不影响已提交的归档水位线。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.message import Message, MessagePurpose
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase
from app.services.knowledge_evolution import (
    KnowledgeEvolutionService,
    _existing_case_summaries,
    _render_sanitized_conversation,
)

logger = logging.getLogger(__name__)

_POSITIVE_EVENT_TYPES = frozenset({"improved", "completed"})
_HISTORY_MSG_LIMIT = 200
_TIMELINE_LIMIT = 40

_STABILITY_NOTE = (
    "本案例由成效稳态变化触发。请依据 outcome_timeline(该用户的完整成效事件时间线)判断:"
    "只有当出现**显著且已稳定**的正向变化——即同类进展在多次记录中持续、没有随即回弹的"
    "一次性小波动——才产出优秀案例;临时的、单点的、未被后续印证的波动不算,一律返回空。"
    "合成时综合全历史,产出完整弧线的成功故事;若 existing_case_summaries 已覆盖同一转折,"
    "只补充新的突破或证据,不重复生成。"
)


async def maybe_synthesize_case(
    *,
    service_case_id: int,
    run_result: Any,
    settings: Settings | None = None,
    raise_on_error: bool = False,
) -> None:
    """成效有新沉淀时,评估并(在够格时)合成一条优秀案例草稿。尽力而为,失败不外抛。"""
    try:
        if not getattr(run_result, "outcome_written", 0):
            return  # 本轮没写入新成效事件 → 无新信号,不评估
        settings = settings or get_settings()
        async with SessionLocal() as session:
            service_case = await session.get(ServiceCase, service_case_id)
            if service_case is None or service_case.deleted_at is not None:
                return

            events = await _load_outcome_events(session, service_case_id)
            if not any(e.event_type in _POSITIVE_EVENT_TYPES for e in events):
                return  # 没有任何正向进展 → 没有可展示的成功故事

            messages = await _load_history(session, service_case_id)
            if len(messages) < 4:
                return
            conversation = _render_sanitized_conversation(messages)
            if not conversation.strip():
                return

            existing = await _existing_case_summaries(
                session, service_case_id=service_case_id
            )
            case_memory = await session.scalar(
                select(ArchivistCaseMemory).where(
                    ArchivistCaseMemory.service_case_id == service_case_id
                )
            )
            scan_context = {
                "scan_type": "archivist_outcome_stable",
                "service_case_id": service_case_id,
                "case_no": service_case.case_no,
                "existing_case_summaries": existing,
                "outcome_timeline": _timeline(events),
                "case_memory_summary": (case_memory.summary if case_memory else None),
                "note": _STABILITY_NOTE,
            }

            service = KnowledgeEvolutionService()
            created, _skipped = await service._create_drafts(
                session,
                conversation=conversation,
                messages=messages,
                candidate_kind="case",
                scan_context=scan_context,
            )
            if created:
                logger.info(
                    "Archivist synthesized case drafts=%s service_case_id=%s",
                    created,
                    service_case_id,
                )
    except Exception:
        logger.exception(
            "Archivist case synthesis failed service_case_id=%s", service_case_id
        )
        if raise_on_error:
            raise


async def _load_outcome_events(session, service_case_id: int) -> list[OutcomeEvent]:
    rows = (
        await session.execute(
            select(OutcomeEvent)
            .where(OutcomeEvent.service_case_id == service_case_id)
            .order_by(OutcomeEvent.occurred_at.asc().nullslast(), OutcomeEvent.id.asc())
            .limit(_TIMELINE_LIMIT)
        )
    ).scalars().all()
    return list(rows)


def _timeline(events: list[OutcomeEvent]) -> list[dict[str, Any]]:
    return [
        {
            "field_key": e.field_key,
            "event_type": e.event_type,
            "value": e.value_text,
            "milestone": e.milestone,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
        }
        for e in events
    ]


async def _load_history(session, service_case_id: int) -> list[Message]:
    rows = (
        await session.execute(
            select(Message)
            .where(
                Message.service_case_id == service_case_id,
                Message.purpose == MessagePurpose.CHAT,
                Message.content != "",
            )
            .options(
                selectinload(Message.service_case).selectinload(ServiceCase.user)
            )
            .order_by(Message.id.asc())
            .limit(_HISTORY_MSG_LIMIT)
        )
    ).scalars().all()
    return list(rows)
