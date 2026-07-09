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


class QiweAccountView(BaseModel):
    guid: str = ""
    token_masked: str = ""
    token_set: bool = False


class QiweAccountUpdate(BaseModel):
    guid: str
    token: str


def _mask_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    if len(token) <= 4:
        return "****"
    return f"****{token[-4:]}"


@router.get("/settings/qiwe-account", response_model=QiweAccountView)
async def get_qiwe_account_api(session: AsyncSession = Depends(get_session)) -> QiweAccountView:
    from app.services.qiwe_account_settings import get_qiwe_account

    account = await get_qiwe_account(session)
    token = account.get("token", "")
    return QiweAccountView(
        guid=account.get("guid", ""),
        token_masked=_mask_token(token),
        token_set=bool(token),
    )


@router.post("/settings/qiwe-account", response_model=QiweAccountView)
async def update_qiwe_account_api(
    payload: QiweAccountUpdate,
    session: AsyncSession = Depends(get_session),
) -> QiweAccountView:
    from fastapi import HTTPException

    from app.services.qiwe_account_settings import set_qiwe_account

    try:
        account = await set_qiwe_account(payload.guid, payload.token, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return QiweAccountView(
        guid=account["guid"],
        token_masked=_mask_token(account["token"]),
        token_set=True,
    )


class StaffSenderRule(BaseModel):
    type: str  # "equals" or "contains"
    pattern: str


class StaffSenderSettings(BaseModel):
    rules: list[StaffSenderRule]


@router.get("/settings/qiwei-staff-senders", response_model=StaffSenderSettings)
async def get_qiwei_staff_senders_api(session: AsyncSession = Depends(get_session)) -> StaffSenderSettings:
    from app.services.content_policy import get_qiwei_staff_senders
    rules = await get_qiwei_staff_senders(session)
    return StaffSenderSettings(rules=rules)


@router.post("/settings/qiwei-staff-senders", response_model=StaffSenderSettings)
async def update_qiwei_staff_senders_api(
    payload: StaffSenderSettings,
    session: AsyncSession = Depends(get_session),
) -> StaffSenderSettings:
    from app.services.content_policy import set_qiwei_staff_senders
    rules = [rule.model_dump() for rule in payload.rules]
    saved_rules = await set_qiwei_staff_senders(rules, session)
    return StaffSenderSettings(rules=saved_rules)

