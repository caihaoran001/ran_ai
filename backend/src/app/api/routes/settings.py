from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db.session import get_session
from app.services.content_policy import (
    get_ai_reply_delivery_mode,
    get_content_delivery_mode,
    set_ai_reply_delivery_mode,
    set_content_delivery_mode,
)
from app.services.knowledge_evolution_settings import (
    get_knowledge_evolution_config,
    set_knowledge_evolution_config,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class AutoSendSetting(BaseModel):
    auto_send_documents: bool


class ContentDeliveryPolicy(BaseModel):
    mode: str = "manual_review"


@router.get("/settings/auto-send")
async def get_auto_send_setting(session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    return {"auto_send_documents": await get_content_delivery_mode(session) == "auto_send"}


@router.post("/settings/auto-send")
async def update_auto_send_setting(
    payload: AutoSendSetting,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    mode = "auto_send" if payload.auto_send_documents else "manual_review"
    saved = await set_content_delivery_mode(mode, session)
    return {"auto_send_documents": saved == "auto_send"}


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


class KnowledgeEvolutionSetting(BaseModel):
    enabled: bool = False
    interval_seconds: int = 3600
    qa_at: str = "00:00"
    daily_qa_limit: int = 500
    case_days: str = "3,7,14,21,28"
    case_limit_per_case: int = 200
    timezone: str = "Asia/Shanghai"


@router.get("/settings/knowledge-evolution", response_model=KnowledgeEvolutionSetting)
async def get_knowledge_evolution_setting_api(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeEvolutionSetting:
    config = await get_knowledge_evolution_config(session)
    return KnowledgeEvolutionSetting(**config.__dict__)


@router.post("/settings/knowledge-evolution", response_model=KnowledgeEvolutionSetting)
async def update_knowledge_evolution_setting_api(
    payload: KnowledgeEvolutionSetting,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeEvolutionSetting:
    config = await set_knowledge_evolution_config(session, payload.model_dump())
    return KnowledgeEvolutionSetting(**config.__dict__)
