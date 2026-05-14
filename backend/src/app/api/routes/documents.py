from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, UserDocument
from app.schemas.document import DocumentUpdate, UserDocumentRead, UserProfileRead
from app.services.document_service import (
    generate_documents,
    generate_profile,
    rewrite_document_file,
    send_case_documents,
    send_one_document,
)

router = APIRouter()


@router.post("/service-cases/{service_case_id}/generate-profile", response_model=UserProfileRead)
async def generate_case_profile(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
):
    service_case = await _case_or_404(session, service_case_id)
    try:
        return await generate_profile(session, service_case)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/service-cases/{service_case_id}/generate-documents", response_model=list[UserDocumentRead])
async def generate_case_documents(
    service_case_id: int,
    auto_send: bool = False,
    session: AsyncSession = Depends(get_session),
):
    service_case = await _case_or_404(session, service_case_id)
    try:
        return await generate_documents(session, service_case, auto_send=auto_send)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/service-cases/{service_case_id}/documents/send", response_model=list[UserDocumentRead])
async def send_all_case_documents(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[UserDocument]:
    try:
        return await send_case_documents(session, service_case_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/service-cases/{service_case_id}/documents", response_model=list[UserDocumentRead])
async def list_case_documents(
    service_case_id: int,
    include_history: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[UserDocument]:
    documents = list(await session.scalars(
        select(UserDocument)
        .where(UserDocument.service_case_id == service_case_id)
        .order_by(UserDocument.created_at.desc())
    ))
    if include_history:
        return documents
    latest_by_type = {}
    for document in documents:
        latest_by_type.setdefault(document.document_type, document)
    return list(latest_by_type.values())


@router.get("/documents/{document_id}", response_model=UserDocumentRead)
async def get_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
) -> UserDocument:
    document = await session.get(UserDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.patch("/documents/{document_id}", response_model=UserDocumentRead)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    session: AsyncSession = Depends(get_session),
) -> UserDocument:
    document = await session.get(UserDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="document not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(document, key, value)
    return await rewrite_document_file(session, document)


@router.post("/documents/{document_id}/approve", response_model=UserDocumentRead)
async def approve_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
) -> UserDocument:
    document = await session.get(UserDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="document not found")
    document.status = DocumentStatus.APPROVED
    document.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(document)
    return document


@router.post("/documents/{document_id}/send", response_model=UserDocumentRead)
async def mark_document_sent(
    document_id: int,
    session: AsyncSession = Depends(get_session),
) -> UserDocument:
    document = await session.get(UserDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        return await send_one_document(session, document)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    document = await session.get(UserDocument, document_id)
    if not document or not document.file_path:
        raise HTTPException(status_code=404, detail="document file not found")
    path = Path(document.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="document file not found")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


async def _case_or_404(session: AsyncSession, service_case_id: int) -> ServiceCase:
    service_case = await session.get(ServiceCase, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    if service_case.deleted_at:
        raise HTTPException(status_code=400, detail="deleted service case is read-only")
    return service_case
