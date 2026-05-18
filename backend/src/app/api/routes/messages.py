from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase
from app.schemas.message import MessageRead, MessageSend, MessageUpdate
from app.services.channels import send_channel_text

router = APIRouter()


@router.get("/service-cases/{service_case_id}/messages", response_model=list[MessageRead])
async def list_case_messages(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    service_case = await session.get(ServiceCase, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    result = await session.scalars(
        select(Message)
        .where(
            Message.group_id == service_case.group_id,
            Message.created_at >= service_case.created_at,
        )
        .order_by(Message.created_at.asc())
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


from sqlalchemy.orm import selectinload

@router.get("/messages/pending-ai", response_model=list[MessageRead])
async def list_pending_ai_messages(
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    result = await session.scalars(
        select(Message)
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            ServiceCase.deleted_at.is_(None),
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == MessageSenderType.BOT,
            Message.purpose == MessagePurpose.CHAT,
            Message.status == MessageStatus.PENDING,
        )
        .options(selectinload(Message.trigger))
        .order_by(Message.created_at.desc())
    )
    return list(result)


@router.post("/messages/{message_id}/send", response_model=MessageRead)
async def send_pending_message(
    message_id: int,
    session: AsyncSession = Depends(get_session),
) -> Message:
    message = await session.scalar(
        select(Message).where(Message.id == message_id).options(selectinload(Message.trigger))
    )
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    if message.direction != MessageDirection.OUTBOUND:
        raise HTTPException(status_code=400, detail="only outbound messages can be sent")
    if message.status != MessageStatus.PENDING:
        raise HTTPException(status_code=400, detail="message is not pending")
    result = await send_channel_text(message.group_id, message.content)
    if result.success:
        message.status = MessageStatus.SENT
        message.wecom_message_id = result.message_id
        message.error = None
    else:
        message.status = MessageStatus.FAILED
        message.error = result.error
    await session.commit()
    await session.refresh(message)
    return message


@router.patch("/messages/{message_id}", response_model=MessageRead)
async def update_message(
    message_id: int,
    payload: MessageUpdate,
    session: AsyncSession = Depends(get_session),
) -> Message:
    message = await session.scalar(
        select(Message).where(Message.id == message_id).options(selectinload(Message.trigger))
    )
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    if message.status != MessageStatus.PENDING:
        raise HTTPException(status_code=400, detail="only pending messages can be edited")
    data = payload.model_dump(exclude_unset=True)
    if "content" in data and data["content"] is not None:
        message.content = data["content"]
    await session.commit()
    await session.refresh(message)
    return message
