from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text

from app.db.session import SessionLocal
from app.models.knowledge_draft import KnowledgeDraft
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import ACTIVE_DOCUMENT_TYPES, DocumentStatus, UserDocument
from app.services.service_case_queries import active_service_case_condition

router = APIRouter()


@router.get("/events/stream")
async def event_stream(request: Request) -> StreamingResponse:
    async def generate():
        while True:
            if await request.is_disconnected():
                break
            # asyncio.shield 保证「查询 + 连接归还(__aexit__)」整体不被客户端断开
            # (CancelledError) 中途打断；否则 asyncpg 连接来不及 checkin 回池子，
            # 会泄漏成 non-checked-in 连接并最终耗尽连接池(本次整站/机器人瘫痪的根因)。
            payload = await asyncio.shield(_review_counts())
            yield f"event: pending_review\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            # 心跳注释行(SSE 规范，前端 EventSource 自动忽略)：保活长连接、便于中间层及时感知存活
            yield ": ping\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _review_counts() -> dict[str, int]:
    async with SessionLocal() as session:
        pending_knowledge_drafts = await session.scalar(
            select(func.count(KnowledgeDraft.id)).where(KnowledgeDraft.status == "pending")
        ) or 0
        pending_documents = await _pending_latest_document_count(session)
        # 已退款待确认：AI 识别到客户已发起/申请退款，等运营在审核控制台核实。
        # 与待审文件同理，待办计数不按新老群过滤（见 _pending_latest_document_count）。
        refund_pending = await session.scalar(
            select(func.count(ServiceCase.id)).where(
                active_service_case_condition(),
                ServiceCase.refund_pending_confirmation.is_(True),
                ServiceCase.status != ServiceCaseStatus.REFUNDED,
            )
        ) or 0
    # pending_total 驱动审核控制台侧边栏红点：只统计控制台里真正待处理的项——
    # 待审核文件、待审核知识草稿、已退款待确认。
    return {
        "pending_knowledge_drafts": pending_knowledge_drafts,
        "pending_documents": pending_documents,
        "refund_pending": refund_pending,
        "pending_total": pending_knowledge_drafts + pending_documents + refund_pending,
    }


async def _pending_latest_document_count(session) -> int:
    """侧边栏红点的待审文件数：**全量**，必须与 /dashboard/pending-documents 列表同口径。

    这里若按新老群过滤、列表却不过滤（或反之），就会出现"列表有 6 项、红点不计这 6 项"
    的错乱。待办计数与待办列表永远同口径，理由见该列表接口的说明。
    """
    latest_docs = (
        select(UserDocument.status)
        .distinct(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            UserDocument.document_type.in_(ACTIVE_DOCUMENT_TYPES),
        )
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
