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
    generate_stage_report,
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


@router.post("/service-cases/{service_case_id}/stage-reports/generate", response_model=UserDocumentRead)
async def generate_case_stage_report(
    service_case_id: int,
    stage: str,
    session: AsyncSession = Depends(get_session),
) -> UserDocument:
    service_case = await _case_or_404(session, service_case_id)
    try:
        return await generate_stage_report(session, service_case, stage=stage)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/service-cases/{service_case_id}/documents/send", response_model=list[UserDocumentRead])
async def send_all_case_documents(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[UserDocument]:
    await _case_or_404(session, service_case_id)
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
    await _case_or_404(session, service_case_id)
    documents = list(await session.scalars(
        select(UserDocument)
        .where(UserDocument.service_case_id == service_case_id)
        .order_by(UserDocument.created_at.desc())
    ))
    if include_history:
        return documents
    latest_by_type = {}
    for document in documents:
        latest_by_type.setdefault(_document_identity(document), document)
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
    service_case = await session.scalar(
        select(ServiceCase).where(ServiceCase.id == service_case_id, ServiceCase.deleted_at.is_(None))
    )
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    return service_case


def _document_identity(document: UserDocument) -> tuple[str, str]:
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    stage = str(content_json.get("stage") or "")
    return (str(document.document_type), stage)
