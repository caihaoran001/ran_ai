from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.message import (
    Message,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase
from app.schemas.message import MessageRead, MessageSend
from app.services.channels import send_channel_text

router = APIRouter()


@router.get("/service-cases/{service_case_id}/messages", response_model=list[MessageRead])
async def list_case_messages(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    result = await session.scalars(
        select(Message)
        .where(Message.service_case_id == service_case_id)
        .order_by(Message.created_at.desc())
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
    service_case = await session.get(ServiceCase, service_case_id)
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
