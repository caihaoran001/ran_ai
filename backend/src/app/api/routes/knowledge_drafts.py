from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.knowledge_draft import KnowledgeDraft
from app.schemas.knowledge_draft import (
    KnowledgeIndexStats,
    KnowledgeIndexSyncRequest,
    KnowledgeIndexSyncResult,
    KnowledgeDraftRead,
    KnowledgeDraftApprovalRequest,
    KnowledgeDraftReject,
    KnowledgeDraftUpdate,
)
from app.services.knowledge_dedupe import (
    KnowledgeBusinessConflict,
    KnowledgeDedupeUnavailable,
)
from app.services.knowledge_evolution import approve_draft, reject_draft
from app.services.knowledge_index import knowledge_index_stats, sync_knowledge_chunks

router = APIRouter()


@router.get("/knowledge-base/index/stats", response_model=KnowledgeIndexStats)
async def get_knowledge_index_stats(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeIndexStats:
    return KnowledgeIndexStats(**await knowledge_index_stats(session))


@router.post("/knowledge-base/index/sync", response_model=KnowledgeIndexSyncResult)
async def sync_knowledge_index(
    payload: KnowledgeIndexSyncRequest,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeIndexSyncResult:
    result = await sync_knowledge_chunks(
        session,
        embed=payload.embed,
        force_embedding=payload.force_embedding,
    )
    return KnowledgeIndexSyncResult(**result.__dict__)


@router.get("/knowledge-drafts", response_model=list[KnowledgeDraftRead])
async def list_knowledge_drafts(
    status: str = "pending",
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeDraft]:
    query = select(KnowledgeDraft).order_by(KnowledgeDraft.created_at.desc())
    if status != "all":
        query = query.where(KnowledgeDraft.status == status)
    result = await session.scalars(query)
    return list(result)


@router.patch("/knowledge-drafts/{draft_id}", response_model=KnowledgeDraftRead)
async def update_knowledge_draft(
    draft_id: int,
    payload: KnowledgeDraftUpdate,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDraft:
    draft = await _get_draft(session, draft_id)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="only pending drafts can be edited")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(draft, key, value)
    await session.commit()
    await session.refresh(draft)
    return draft


@router.post("/knowledge-drafts/{draft_id}/approve", response_model=KnowledgeDraftRead)
async def approve_knowledge_draft(
    draft_id: int,
    payload: KnowledgeDraftApprovalRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDraft:
    draft = await _get_draft(session, draft_id)
    try:
        return await approve_draft(
            session,
            draft,
            resolution=payload.resolution if payload else None,
        )
    except KnowledgeBusinessConflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except KnowledgeDedupeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/knowledge-drafts/{draft_id}/reject", response_model=KnowledgeDraftRead)
async def reject_knowledge_draft(
    draft_id: int,
    payload: KnowledgeDraftReject | None = None,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDraft:
    draft = await _get_draft(session, draft_id)
    return await reject_draft(session, draft, payload.reason if payload else None)


async def _get_draft(session: AsyncSession, draft_id: int) -> KnowledgeDraft:
    draft = await session.get(KnowledgeDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="knowledge draft not found")
    return draft
