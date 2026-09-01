"""交付文档的发送、阅读提醒、临时 docx 落盘、发送记录与自动发送判定。"""

from __future__ import annotations

import asyncio
import logging
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
from app.models.checkin_reward import CheckinRewardProgram
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
from app.services.execution_commitment import render_commitment_invitation


logger = logging.getLogger(__name__)


async def _both_latest_intake_documents_sent(
    session: AsyncSession, *, service_case_id: int
) -> bool:
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(
                UserDocument.service_case_id == service_case_id,
                UserDocument.document_type.in_(tuple(DOCUMENT_DEFINITIONS)),
            )
            .order_by(UserDocument.created_at.desc(), UserDocument.id.desc())
        )
    )
    latest_by_type: dict[DocumentType, UserDocument] = {}
    for document in documents:
        latest_by_type.setdefault(document.document_type, document)
    return all(
        latest_by_type.get(document_type) is not None
        and latest_by_type[document_type].status == DocumentStatus.SENT
        for document_type in DOCUMENT_DEFINITIONS
    )


async def _render_post_document_checkin_example(
    session: AsyncSession, *, service_case_id: int
) -> str | None:
    """从最新已发送执行建议书读取生成时保存的原题和答案。

    这里不走日常追问的有效清单：那条链路会优先人工版本并固定改写产品项，和用户
    实际收到的执行建议书不是同一份内容。历史文档缺少答案时宁可跳过，也不在发送时猜。
    """

    document = await session.scalar(
        select(UserDocument)
        .where(
            UserDocument.service_case_id == service_case_id,
            UserDocument.document_type == DocumentType.DAY28_EXECUTION_STANDARD,
            UserDocument.status == DocumentStatus.SENT,
        )
        .order_by(UserDocument.sent_at.desc(), UserDocument.id.desc())
        .limit(1)
    )
    if document is None or not isinstance(document.content_json, dict):
        logger.warning(
            "checkin example skipped: sent execution document missing service_case_id=%s",
            service_case_id,
        )
        return None
    items = _raw_checkin_example_items(document.content_json)
    if len(items) != 5:
        logger.warning(
            "checkin example skipped: five generated items with answers required "
            "service_case_id=%s document_id=%s item_count=%s",
            service_case_id,
            document.id,
            len(items),
        )
        return None
    item_lines = [
        f"{index}. {item['title']}｜{item['example_answer']}"
        for index, item in enumerate(items, start=1)
    ]
    return "\n".join(
        [
            "两份专属文件都发给你啦。每天直接复制下面这段，只把“｜”后面的内容改成当天实际情况：",
            "",
            "【每日打卡】",
            "",
            *item_lines,
            "",
            "没完成也照实改，五项都有回答就算完整打卡。产品和餐食建议随手拍照发群里，有疑问随时问我们。",
        ]
    )


def _raw_checkin_example_items(content_json: dict) -> list[dict[str, str]]:
    """读取文档自身保存的结构化五项，不套清单兜底、不改写第一项。"""

    raw_items: object = None
    blocks = content_json.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("key") or "") == "daily_checkin":
                raw_items = block.get("checkin_items")
                break
    # 兼容早期直接保存生成 payload 的内部格式；没有答案的历史数据仍会在下方返回空。
    if raw_items is None:
        raw_items = content_json.get("daily_checkin")
    if not isinstance(raw_items, list) or len(raw_items) != 5:
        return []

    ordered = [item for item in raw_items if isinstance(item, dict)]
    if len(ordered) != 5:
        return []
    try:
        ordered.sort(key=lambda item: int(item.get("order")))
    except (TypeError, ValueError):
        return []
    if [int(item.get("order")) for item in ordered] != [1, 2, 3, 4, 5]:
        return []
    result: list[dict[str, str]] = []
    for item in ordered:
        title = str(item.get("title") or "").strip()
        answer = str(item.get("example_answer") or "").strip()
        if not title or not answer:
            return []
        result.append({"title": title, "example_answer": answer})
    return result


async def _maybe_send_post_document_checkin_example(
    session: AsyncSession, service_case: ServiceCase
) -> bool:
    """两份当前入营文件都成功后的唯一触发点。

    批量发送和运营逐份发送都必须调到这里，去重不依赖调用入口。
    """
    if not await _both_latest_intake_documents_sent(
        session, service_case_id=service_case.id
    ):
        return False
    content = await _render_post_document_checkin_example(
        session, service_case_id=service_case.id
    )
    if content is None:
        return False
    locked_case = await session.scalar(
        select(ServiceCase).where(ServiceCase.id == service_case.id).with_for_update()
    )
    if locked_case is None:
        return False
    program = await session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case.id
        )
    )
    if program is None:
        program = CheckinRewardProgram(service_case_id=service_case.id)
        session.add(program)
        await session.flush()
    if program.intro_status == "sending" and program.intro_message_id is not None:
        previous = await session.get(Message, program.intro_message_id)
        if previous is not None and previous.status in {
            MessageStatus.SENT,
            MessageStatus.FAILED,
            MessageStatus.UNKNOWN,
        }:
            program.intro_status = previous.status.value
    if program.intro_status in {"sending", "sent", "unknown"}:
        return False

    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="markdown",
        content=content,
        purpose=MessagePurpose.DOCUMENT_DELIVERY,
        status=MessageStatus.SENDING,
        analysis_json={"checkin_reward": {"kind": "intro"}},
    )
    session.add(message)
    await session.flush()
    program.intro_status = "sending"
    program.intro_message_id = message.id
    # 先固化 sending：如果请求已发出却丢了回包，不能自动再发。
    await session.commit()
    try:
        result = await send_channel_text(service_case.group_id, content)
    except Exception as exc:
        message.status = MessageStatus.UNKNOWN
        message.error = f"send raised: {type(exc).__name__}: {exc}"
        program.intro_status = "unknown"
        await session.commit()
        return False
    if result.success:
        message.status = MessageStatus.SENT
        message.wecom_message_id = result.message_id
        message.sent_at = _utc_now()
        message.error = None
        program.intro_status = "sent"
    elif result.delivery_uncertain:
        message.status = MessageStatus.UNKNOWN
        message.error = result.error
        program.intro_status = "unknown"
    else:
        message.status = MessageStatus.FAILED
        message.error = result.error
        program.intro_status = "failed"
    await session.commit()
    return bool(result.success)


async def _maybe_send_execution_commitment_invitation(
    session: AsyncSession, service_case: ServiceCase
) -> bool:
    """两份最新交付文件均发送成功后，只发送一次执行承诺邀请。"""
    if not await _both_latest_intake_documents_sent(
        session, service_case_id=service_case.id
    ):
        return False
    locked_case = await session.scalar(
        select(ServiceCase).where(ServiceCase.id == service_case.id).with_for_update()
    )
    if locked_case is None or not isinstance(locked_case.execution_commitment, dict):
        return False
    state = dict(locked_case.execution_commitment)
    invitation = dict(state.get("invitation") or {})
    if invitation.get("status") in {"sending", "sent", "unknown"}:
        return False
    content = render_commitment_invitation(state)
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="markdown",
        content=content,
        purpose=MessagePurpose.DOCUMENT_DELIVERY,
        status=MessageStatus.SENDING,
        analysis_json={"execution_commitment": {"kind": "invitation"}},
    )
    session.add(message)
    await session.flush()
    invitation.update({"status": "sending", "message_id": message.id})
    state["invitation"] = invitation
    locked_case.execution_commitment = state
    await session.commit()
    try:
        result = await send_channel_text(service_case.group_id, content)
    except Exception as exc:
        message.status = MessageStatus.UNKNOWN
        message.error = f"send raised: {type(exc).__name__}: {exc}"
        invitation["status"] = "unknown"
        locked_case.execution_commitment = {**state, "invitation": invitation}
        await session.commit()
        return False
    message.status = (
        MessageStatus.SENT
        if result.success
        else MessageStatus.UNKNOWN
        if result.delivery_uncertain
        else MessageStatus.FAILED
    )
    message.wecom_message_id = result.message_id
    message.error = result.error
    if result.success:
        message.sent_at = _utc_now()
        invitation.update(
            {"status": "sent", "sent_at": message.sent_at.isoformat()}
        )
    else:
        invitation["status"] = "unknown" if result.delivery_uncertain else "failed"
    locked_case.execution_commitment = {**state, "invitation": invitation}
    await session.commit()
    return bool(result.success)


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
        await _maybe_send_post_document_checkin_example(session, service_case)
        await _maybe_send_execution_commitment_invitation(session, service_case)
        return documents

    should_send_intro = not any(
        document.status == DocumentStatus.SENT for document in documents
    ) and not await _document_intro_already_sent(session, service_case)
    if should_send_intro:
        intro = "两份专属文件已生成，请查收：\n1.《个人能量重启诊断报告》\n2.《28天高燃人生执行建议书》"
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
    await _maybe_send_post_document_checkin_example(session, service_case)
    await _maybe_send_execution_commitment_invitation(session, service_case)
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
    await _maybe_send_post_document_checkin_example(session, service_case)
    await _maybe_send_execution_commitment_invitation(session, service_case)
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


def document_manual_review_hold(document: UserDocument) -> bool:
    """这份文档是否被内部人工复核标记扣住、禁止自动发送。

    标记来源是 content_json 顶层的非渲染元数据 internal_review，和后台界面读的是同一份
    持久化状态；用户可见的 blocks / content_md 里不含它。
    """

    content_json = document.content_json
    if not isinstance(content_json, dict):
        return False
    internal_review = content_json.get("internal_review")
    if not isinstance(internal_review, dict):
        return False
    return bool(internal_review.get("manual_review_required"))


def documents_manual_review_hold(documents: list[UserDocument]) -> bool:
    return any(document_manual_review_hold(document) for document in documents)


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
    display_name = f"{user_name}{suffix}" if suffix else user_name
    return safe_filename(f"{display_name}_{title}.docx")


def _utc_now():
    return datetime.now(timezone.utc)
