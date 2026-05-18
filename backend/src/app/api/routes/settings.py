from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import get_session
from app.services.content_policy import (
    get_ai_reply_delivery_mode,
    get_content_delivery_mode,
    set_ai_reply_delivery_mode,
    set_content_delivery_mode,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class AutoSendSetting(BaseModel):
    auto_send_documents: bool


class ContentDeliveryPolicy(BaseModel):
    mode: str = "manual_review"


@router.get("/settings/auto-send")
async def get_auto_send_setting() -> dict[str, bool]:
    return {"auto_send_documents": get_settings().auto_send_documents}


@router.post("/settings/auto-send")
async def update_auto_send_setting(payload: AutoSendSetting) -> dict[str, bool]:
    return {"auto_send_documents": payload.auto_send_documents}


@router.get("/settings/content-delivery-policy", response_model=ContentDeliveryPolicy)
async def get_content_delivery_policy(session: AsyncSession = Depends(get_session)) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await get_content_delivery_mode(session))


@router.post("/settings/content-delivery-policy", response_model=ContentDeliveryPolicy)
async def update_content_delivery_policy(
    payload: ContentDeliveryPolicy,
    session: AsyncSession = Depends(get_session),
) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await set_content_delivery_mode(payload.mode, session))


@router.get("/settings/ai-reply-delivery-policy", response_model=ContentDeliveryPolicy)
async def get_ai_reply_delivery_policy(session: AsyncSession = Depends(get_session)) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await get_ai_reply_delivery_mode(session))


@router.post("/settings/ai-reply-delivery-policy", response_model=ContentDeliveryPolicy)
async def update_ai_reply_delivery_policy(
    payload: ContentDeliveryPolicy,
    session: AsyncSession = Depends(get_session),
) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await set_ai_reply_delivery_mode(payload.mode, session))


class RiskFixedReply(BaseModel):
    content: str


@router.get("/settings/risk-fixed-reply", response_model=RiskFixedReply)
async def get_risk_fixed_reply_api(session: AsyncSession = Depends(get_session)) -> RiskFixedReply:
    from app.services.content_policy import get_risk_fixed_reply
    return RiskFixedReply(content=await get_risk_fixed_reply(session))


@router.post("/settings/risk-fixed-reply", response_model=RiskFixedReply)
async def update_risk_fixed_reply_api(
    payload: RiskFixedReply,
    session: AsyncSession = Depends(get_session),
) -> RiskFixedReply:
    from app.services.content_policy import set_risk_fixed_reply
    return RiskFixedReply(content=await set_risk_fixed_reply(payload.content, session))
