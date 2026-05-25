from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.db.session import SessionLocal
from app.models.service_case import ServiceCase, ServiceCaseStatus
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
from app.services.service_case_queries import get_active_service_case, get_service_case

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


@router.post(
    "/service-cases/{service_case_id}/generate-documents/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_generate_case_documents(
    service_case_id: int,
    background_tasks: BackgroundTasks,
    auto_send: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    service_case = await _case_or_404(session, service_case_id)
    service_case.status = ServiceCaseStatus.GENERATING
    await session.commit()
    background_tasks.add_task(_generate_documents_in_background, service_case_id, auto_send)
    return {"detail": "document generation queued"}


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
    await _case_or_404(session, service_case_id, include_deleted=True)
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


@router.get("/documents", response_model=list[UserDocumentRead])
async def list_all_documents(
    include_history: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[UserDocument]:
    documents = list(await session.scalars(
        select(UserDocument)
        .order_by(UserDocument.created_at.desc())
    ))
    if include_history:
        return documents
    latest_by_key = {}
    for document in documents:
        key = (document.service_case_id, *_document_identity(document))
        latest_by_key.setdefault(key, document)
    return list(latest_by_key.values())


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


async def _case_or_404(
    session: AsyncSession,
    service_case_id: int,
    *,
    include_deleted: bool = False,
) -> ServiceCase:
    service_case = (
        await get_service_case(session, service_case_id)
        if include_deleted
        else await get_active_service_case(session, service_case_id)
    )
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    return service_case


def _document_identity(document: UserDocument) -> tuple[str, str]:
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    stage = str(content_json.get("stage") or "")
    return (str(document.document_type), stage)


async def _generate_documents_in_background(service_case_id: int, auto_send: bool) -> None:
    async with SessionLocal() as session:
        service_case = await get_active_service_case(session, service_case_id)
        if not service_case:
            return
        try:
            await generate_documents(session, service_case, auto_send=auto_send)
        except Exception as exc:
            await session.rollback()
            failed_case = await get_active_service_case(session, service_case_id)
            if failed_case:
                _append_generation_error_note(failed_case, exc)
                await session.commit()


def _append_generation_error_note(service_case: ServiceCase, exc: Exception) -> None:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    settings = get_settings()
    service_case.status = ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
    message = (
        f"[{timestamp}] 自动生成画像/交付文件失败：{type(exc).__name__}: {exc}"
        f"（AI_MAX_TOKENS={settings.ai_max_tokens}）"
    )
    existing = service_case.notes or ""
    service_case.notes = (f"{existing}\n{message}" if existing else message)[-2000:]
