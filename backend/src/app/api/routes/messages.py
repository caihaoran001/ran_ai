from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.schemas.message import MessageRead, MessageSend
from app.services.channels import send_channel_text
from app.services.service_case_queries import get_active_service_case, get_service_case

router = APIRouter()


@router.get("/service-cases/{service_case_id}/messages", response_model=list[MessageRead])
async def list_case_messages(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    service_case = await get_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    result = await session.scalars(
        select(Message)
        .where(
            Message.group_id == service_case.group_id,
            Message.created_at >= service_case.created_at,
            not_(
                (Message.direction == MessageDirection.OUTBOUND)
                & (Message.purpose == MessagePurpose.REMINDER)
                & (Message.status == MessageStatus.FAILED)
            ),
        )
        .options(selectinload(Message.trigger))
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result)


@router.post(
    "/service-cases/{service_case_id}/messages/send",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def send_case_message(
    service_case_id: int,
    payload: MessageSend,
    session: AsyncSession = Depends(get_session),
) -> Message:
    service_case = await get_active_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")

    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="markdown",
        content=payload.content,
        purpose=payload.purpose,
        status=MessageStatus.PENDING,
    )
    session.add(message)
    await session.flush()

    result = await send_channel_text(service_case.group_id, payload.content)
    if result.success:
        message.status = MessageStatus.SENT
        message.wecom_message_id = result.message_id
    else:
        message.status = MessageStatus.FAILED
        message.error = result.error
    await session.commit()
    await session.refresh(message)
    return message


@router.get("/messages/{message_id}/image")
async def get_message_image(
    message_id: int,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    message = await session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    analysis = message.analysis_json or {}
    image_info = analysis.get("image_info")
    if not image_info or "local_path" not in image_info:
        raise HTTPException(status_code=400, detail="message is not an image message")
    local_path = Path(image_info["local_path"]).resolve()
    # 路径前缀校验：只允许返回图片缓存目录内的文件，防止 analysis_json 被污染后读取任意文件
    cache_root = Path(".wecom_cache/images").resolve()
    if not local_path.is_relative_to(cache_root):
        raise HTTPException(status_code=400, detail="invalid image path")
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="image file not found on disk")
    media_type = image_info.get("content_type") or "image/jpeg"
    return FileResponse(local_path, media_type=media_type)

