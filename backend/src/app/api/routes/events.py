from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text

from app.db.session import SessionLocal
from app.models.knowledge_draft import KnowledgeDraft
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, UserDocument
from app.services.service_case_queries import active_service_case_condition

router = APIRouter()


@router.get("/events/stream")
async def event_stream(request: Request) -> StreamingResponse:
    async def generate():
        while True:
            if await request.is_disconnected():
                break
            payload = await _review_counts()
            yield f"event: pending_review\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield f"event: intervention\ndata: {json.dumps({'count': payload['intervention_count']}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _review_counts() -> dict[str, int]:
    async with SessionLocal() as session:
        pending_ai_messages = await session.scalar(
            select(func.count(Message.id))
            .join(ServiceCase, Message.service_case_id == ServiceCase.id)
            .where(
                active_service_case_condition(),
                Message.direction == MessageDirection.OUTBOUND,
                Message.sender_type == MessageSenderType.BOT,
                Message.purpose == MessagePurpose.CHAT,
                Message.status == MessageStatus.PENDING,
            )
        ) or 0
        pending_knowledge_drafts = await session.scalar(
            select(func.count(KnowledgeDraft.id)).where(KnowledgeDraft.status == "pending")
        ) or 0
        pending_documents = await _pending_latest_document_count(session)
        intervention_count = await session.scalar(
            select(func.count(ServiceCase.id)).where(
                active_service_case_condition(),
                ServiceCase.needs_human_intervention.is_(True),
            )
        ) or 0

    return {
        "pending_ai_messages": pending_ai_messages,
        "pending_knowledge_drafts": pending_knowledge_drafts,
        "pending_documents": pending_documents,
        "pending_total": pending_ai_messages + pending_knowledge_drafts + pending_documents,
        "intervention_count": intervention_count,
    }


async def _pending_latest_document_count(session) -> int:
    latest_docs = (
        select(UserDocument.status)
        .distinct(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(active_service_case_condition())
        .order_by(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
            UserDocument.created_at.desc(),
        )
        .subquery()
    )
    return await session.scalar(
        select(func.count())
        .select_from(latest_docs)
        .where(latest_docs.c.status.in_([DocumentStatus.DRAFT, DocumentStatus.REVIEWING]))
    ) or 0
