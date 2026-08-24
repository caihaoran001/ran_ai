from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.knowledge_faq import KnowledgeFAQ, KnowledgeFAQVersion
from app.schemas.knowledge_faq import (
    KnowledgeFAQCreate,
    KnowledgeFAQRead,
    KnowledgeFAQUpdate,
    KnowledgeFAQVersionRead,
)
from app.services.knowledge_faq_service import (
    confirm_faq,
    create_faq,
    set_faq_status,
    unconfirm_faq,
    update_faq,
)

router = APIRouter()


@router.get("/knowledge-faqs", response_model=list[KnowledgeFAQRead])
async def list_knowledge_faqs(
    status: str = "active",
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeFAQ]:
    query = select(KnowledgeFAQ).order_by(KnowledgeFAQ.updated_at.desc())
    if status != "all":
        query = query.where(KnowledgeFAQ.status == status)
    return list(await session.scalars(query))


@router.post("/knowledge-faqs", response_model=KnowledgeFAQRead)
async def create_knowledge_faq(
    payload: KnowledgeFAQCreate,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    return await create_faq(session, **payload.model_dump(), source_type="manual")


@router.patch("/knowledge-faqs/{faq_id}", response_model=KnowledgeFAQRead)
async def update_knowledge_faq(
    faq_id: int,
    payload: KnowledgeFAQUpdate,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    faq = await _get_faq(session, faq_id)
    return await update_faq(session, faq, **payload.model_dump(exclude_unset=True))


@router.post("/knowledge-faqs/{faq_id}/enable", response_model=KnowledgeFAQRead)
async def enable_knowledge_faq(
    faq_id: int,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    return await set_faq_status(session, await _get_faq(session, faq_id), "active")


@router.post("/knowledge-faqs/{faq_id}/disable", response_model=KnowledgeFAQRead)
async def disable_knowledge_faq(
    faq_id: int,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    return await set_faq_status(session, await _get_faq(session, faq_id), "disabled")


@router.post("/knowledge-faqs/{faq_id}/confirm", response_model=KnowledgeFAQRead)
async def confirm_knowledge_faq(
    faq_id: int,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    return await confirm_faq(session, await _get_faq(session, faq_id))


@router.post("/knowledge-faqs/{faq_id}/unconfirm", response_model=KnowledgeFAQRead)
async def unconfirm_knowledge_faq(
    faq_id: int,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    return await unconfirm_faq(session, await _get_faq(session, faq_id))


@router.delete("/knowledge-faqs/{faq_id}", response_model=KnowledgeFAQRead)
async def archive_knowledge_faq(
    faq_id: int,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeFAQ:
    return await set_faq_status(session, await _get_faq(session, faq_id), "archived")


@router.get("/knowledge-faqs/{faq_id}/versions", response_model=list[KnowledgeFAQVersionRead])
async def list_knowledge_faq_versions(
    faq_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeFAQVersion]:
    await _get_faq(session, faq_id)
    result = await session.scalars(
        select(KnowledgeFAQVersion)
        .where(KnowledgeFAQVersion.faq_id == faq_id)
        .order_by(KnowledgeFAQVersion.version.desc())
    )
    return list(result)


async def _get_faq(session: AsyncSession, faq_id: int) -> KnowledgeFAQ:
    faq = await session.scalar(
        select(KnowledgeFAQ)
        .where(KnowledgeFAQ.id == faq_id)
        .options(selectinload(KnowledgeFAQ.versions))
        .limit(1)
    )
    if not faq:
        raise HTTPException(status_code=404, detail="knowledge faq not found")
    return faq
