from __future__ import annotations

"""交付文档的发送、阅读提醒、临时 docx 落盘、发送记录与自动发送判定。"""

import asyncio
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.channels import send_channel_document, send_channel_text
from app.services.content_policy import get_content_delivery_mode
from app.services.risk_service import is_high_risk
from app.services.service_case_queries import get_active_service_case
from app.services.docx_service import (
    safe_filename,
    write_markdown_docx,
    write_template_docx,
)
from app.services.document_common import (
    DOCUMENT_DEFINITIONS,
    _recipient_name_and_gender,
    _user_name,
)


async def _send_document_reading_reminder(session: AsyncSession, service_case: ServiceCase) -> None:
    """两份文件发送后，提醒用户先阅读、随时答疑。"""
    content = (
        "两份专属文件都发给您啦 📄\n\n"
        "请先花几分钟仔细阅读《个人能量重启处方单》和《28天执行标准与打卡要求》，把今天的方案看明白；"
        "任何有疑问的地方都可以随时在群里提出来，我和班主任都会帮您解答。\n\n"
        "今天先安心熟悉文件、不用急着执行，我们一步步来，陪您把这 28 天走扎实 🌟"
    )
    result = await send_channel_text(service_case.group_id, content)
    session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            wecom_message_id=result.message_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="markdown",
            content=content,
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT if result.success else MessageStatus.FAILED,
            error=result.error,
        )
    )
    await session.commit()


async def send_case_documents(session: AsyncSession, service_case_id: int) -> list[UserDocument]:
    service_case = await get_active_service_case(session, service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == service_case_id)
            .order_by(UserDocument.created_at.desc())
        )
    )
    latest_by_type: dict[DocumentType, UserDocument] = {}
    for document in documents:
        latest_by_type.setdefault(document.document_type, document)
    documents = [
        latest_by_type[document_type]
        for document_type in DOCUMENT_DEFINITIONS
        if document_type in latest_by_type
    ]
    if not documents:
        raise RuntimeError("documents are required before sending")
    documents_to_send = [document for document in documents if document.status != DocumentStatus.SENT]
    if not documents_to_send:
        return documents

    should_send_intro = not any(
        document.status == DocumentStatus.SENT for document in documents
    ) and not await _document_intro_already_sent(session, service_case)
    if should_send_intro:
        intro = "两份专属文件已生成，请查收：\n1.《个人能量重启处方单》\n2.《28 天执行标准与打卡要求》"
        intro_result = await send_channel_text(service_case.group_id, intro)
        session.add(
            Message(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                wecom_message_id=intro_result.message_id,
                direction=MessageDirection.OUTBOUND,
                sender_type=MessageSenderType.BOT,
                message_type="markdown",
                content=intro,
                purpose=MessagePurpose.DOCUMENT_DELIVERY,
                status=MessageStatus.SENT if intro_result.success else MessageStatus.FAILED,
                error=intro_result.error,
            )
        )
        await session.commit()
        if not intro_result.success:
            raise RuntimeError(intro_result.error or "failed to send document intro")

    for document in documents_to_send:
        file_path, cleanup = await create_temporary_document_file(session, document)
        try:
            result = await send_channel_document(
                service_case.group_id,
                str(file_path),
                caption=f"{document.title}.docx",
            )
        finally:
            cleanup()
        if not result.success:
            _record_document_delivery(
                session,
                service_case,
                document,
                result.message_id,
                status=MessageStatus.FAILED,
                error=result.error,
            )
            await session.commit()
            raise RuntimeError(result.error or f"failed to send {document.title}")
        document.status = DocumentStatus.SENT
        document.sent_at = _utc_now()
        _record_document_delivery(session, service_case, document, result.message_id)
        await session.commit()
    if should_send_intro:
        await _send_document_reading_reminder(session, service_case)
    for document in documents:
        await session.refresh(document)
    return documents


async def send_one_document(session: AsyncSession, document: UserDocument) -> UserDocument:
    service_case = await get_active_service_case(session, document.service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    file_path, cleanup = await create_temporary_document_file(session, document)
    try:
        result = await send_channel_document(
            service_case.group_id,
            str(file_path),
            caption=f"{document.title}.docx",
        )
    finally:
        cleanup()
    if not result.success:
        _record_document_delivery(
            session,
            service_case,
            document,
            result.message_id,
            status=MessageStatus.FAILED,
            error=result.error,
        )
        await session.commit()
        raise RuntimeError(result.error or f"failed to send {document.title}")
    document.status = DocumentStatus.SENT
    document.sent_at = _utc_now()
    _record_document_delivery(session, service_case, document, result.message_id)
    await session.commit()
    await session.refresh(document)
    return document


async def create_temporary_document_file(
    session: AsyncSession,
    document: UserDocument,
) -> tuple[Path, Callable[[], None]]:
    service_case = await session.get(ServiceCase, document.service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    user_name = await _user_name(session, service_case)
    _, gender = await _recipient_name_and_gender(session, service_case)
    temp_dir = Path(tempfile.mkdtemp(prefix="ran-ai-docx-"))
    output_path = temp_dir / _document_filename(
        user_name=user_name,
        document_type=document.document_type,
        title=document.title,
        gender=gender,
    )
    await _render_document_file(
        output_path=output_path,
        content=document.content_md,
        content_json=document.content_json,
    )

    def cleanup() -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_path, cleanup


def _record_document_delivery(
    session: AsyncSession,
    service_case: ServiceCase,
    document: UserDocument,
    message_id: str | None,
    *,
    status: MessageStatus = MessageStatus.SENT,
    error: str | None = None,
) -> None:
    verb = "已发送文件" if status == MessageStatus.SENT else "文件发送失败"
    session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            wecom_message_id=message_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="document",
            content=f"{verb}：{document.title}.docx",
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=status,
            error=error,
        )
    )


async def _should_auto_send(session: AsyncSession, service_case: ServiceCase) -> bool:
    return await get_content_delivery_mode(session) == "auto_send" and not is_high_risk(service_case)


async def _document_intro_already_sent(session: AsyncSession, service_case: ServiceCase) -> bool:
    message = await session.scalar(
        select(Message.id)
        .where(
            Message.service_case_id == service_case.id,
            Message.purpose == MessagePurpose.DOCUMENT_DELIVERY,
            Message.message_type == "markdown",
            Message.status == MessageStatus.SENT,
        )
        .limit(1)
    )
    return message is not None


async def _render_document_file(
    *,
    output_path: Path,
    content: str,
    content_json: dict | None = None,
) -> None:
    return await asyncio.to_thread(
        _render_document_file_sync,
        output_path=output_path,
        content=content,
        content_json=content_json,
    )


def _render_document_file_sync(
    *,
    output_path: Path,
    content: str,
    content_json: dict | None = None,
) -> None:
    if content_json:
        write_template_docx(content_json, output_path)
    else:
        write_markdown_docx(content, output_path)


def _document_filename(
    *,
    user_name: str,
    document_type: DocumentType,
    title: str,
    gender: str,
) -> str:
    # 性别以入营问卷为准（男→先生 / 女→女士 / 未提供→不带后缀），
    # 不再靠扫描正文关键词猜，避免同一用户各文件称呼不一致。
    suffix = {"男": "先生", "女": "女士"}.get((gender or "").strip(), "")
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return safe_filename(f"{user_name} {title} {user_name}{suffix} {document_type.value} {version}.docx")


def _utc_now():
    return datetime.now(timezone.utc)
